"""Thin legacy delivery guard: native idempotency and caller-owned atomicity."""
from __future__ import annotations
from functools import wraps
import hashlib
import json


def atomic_private_delivery(fn):
    @wraps(fn)
    def wrapped(conn,user,*args,**kwargs):
        from .wanderer_social_budget_service import _agent
        from .social_common import serialize_on
        sender=f"{user.get('role')}:{user.get('id')}"
        if _agent(conn,sender) is None:
            return fn(conn,user,*args,**kwargs)
        serialize_on(conn,'wanderer-private-send',[sender])
        conn.execute('SAVEPOINT wanderer_private_delivery')
        try:
            result=fn(conn,user,*args,**kwargs)
        except Exception:
            conn.execute('ROLLBACK TO SAVEPOINT wanderer_private_delivery')
            conn.execute('RELEASE SAVEPOINT wanderer_private_delivery')
            raise
        conn.execute('RELEASE SAVEPOINT wanderer_private_delivery')
        return result
    return wrapped


def operation_for(conn,user,*,contact_identity,class_offering_id,content,attachments):
    from .agent_platform_request_context import _request_identity,is_verified_wanderer_request_user
    from ..db.schema_wanderer_social import ensure_wanderer_social_schema
    if not is_verified_wanderer_request_user(user):
        return None
    binding=_request_identity.get()
    source=binding.service_authority
    ensure_wanderer_social_schema(conn)
    # Content addresses survive an upload retry; transient storage paths do not.
    payload={'peer':contact_identity,'scope':class_offering_id,'content':content,
             'attachments':[{key:item.get(key) for key in ('file_hash','original_filename','mime_type','file_size')}
                            for item in attachments]}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    prior=conn.execute('SELECT * FROM wanderer_private_message_operations WHERE operation_id=?',(binding.operation_id,)).fetchone()
    if prior and (int(prior['wanderer_id'])!=int(source.wanderer_id) or prior['request_hash']!=digest):
        raise ValueError('同一私信操作编号不能用于不同内容或身份。')
    message=None
    if prior:
        row=conn.execute('SELECT * FROM private_messages WHERE id=? AND sender_identity=?',
                         (prior['message_id'],f"student:{binding.actor_id}")).fetchone()
        if row is None:
            raise ValueError('原私信已不存在，不能重放该操作。')
        message=dict(row)
    from .message_center_service import build_conversation_key
    from .wanderer_message_delivery_service import prepare_private
    delivery = prepare_private(conn, user,
        conversation_key=build_conversation_key(f'student:{binding.actor_id}', contact_identity, class_offering_id),
        content=content, attachments=attachments)
    if delivery and delivery['message'] is not None:
        message = delivery['message']
    return {'operation_id':binding.operation_id,'wanderer_id':int(source.wanderer_id),
            'request_hash':digest,'message':message, 'delivery_guard':delivery}


def record_operation(conn,operation,message):
    if operation is None:
        return
    from ..time_utils import local_iso
    conn.execute('INSERT INTO wanderer_private_message_operations(operation_id,wanderer_id,request_hash,message_id,created_at) VALUES(?,?,?,?,?)',
                 (operation['operation_id'],operation['wanderer_id'],operation['request_hash'],int(message['id']),local_iso()))
    from .wanderer_message_delivery_service import record_delivery
    record_delivery(conn, operation.get('delivery_guard'), int(message['id']))
