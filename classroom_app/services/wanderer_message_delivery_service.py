"""Verified AI-only repeat protection; ordinary message services still own writes.

The same causal chain may replay a completed message. Independent events only
reuse identical payloads within ten minutes while no new peer message arrived.
The ledger and the original message/debit commit in the same transaction.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import timedelta, timezone
from typing import Any

from ..db.schema_wanderer_delivery import ensure_wanderer_delivery_schema
from ..time_utils import APP_TIMEZONE, local_iso, local_now, to_local_datetime

REPEAT_WINDOW_SECONDS = 600


def _text(value):
    # Preserve meaningful Markdown/code whitespace and punctuation; only
    # Unicode composition and transport line endings are interchangeable.
    return unicodedata.normalize('NFC', str(value or '')).replace('\r\n', '\n').strip()


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), default=str).encode()).hexdigest()


def social_payload_hash(payload):
    return _hash({'body': _text(payload.get('body')), 'body_format': payload.get('body_format') or 'md',
        'reply_to_id': payload.get('reply_to_id') or None,
        'mentions': sorted(set(str(item) for item in (payload.get('mentions') or []))),
        'media_tokens': payload.get('media_tokens') or [], 'lan_media': payload.get('lan_media') or []})


def private_payload_hash(content, attachments):
    return _hash({'body': _text(content), 'attachments': [
        {key: item.get(key) for key in ('file_hash', 'original_filename', 'mime_type', 'file_size')}
        for item in attachments]})


def _binding(conn, user):
    from .agent_platform_request_context import _request_identity, is_verified_wanderer_request_user
    if not is_verified_wanderer_request_user(user):
        return None
    binding = _request_identity.get()
    source = binding.service_authority
    row = conn.execute('SELECT chain_id FROM wanderer_turns WHERE turn_id=? AND wanderer_id=? AND life_epoch=?',
        (source.turn_id, int(source.wanderer_id), int(source.life_epoch))).fetchone()
    if row is None:
        raise PermissionError('散修发送来源轮次不存在或生命代次已改变。')
    ensure_wanderer_delivery_schema(conn)
    return {'operation_id': binding.operation_id, 'wanderer_id': int(source.wanderer_id),
            'life_epoch': int(source.life_epoch), 'turn_id': source.turn_id,
            'chain_id': row['chain_id'], 'sender': f'student:{binding.actor_id}'}


def _actual_message(conn, operation, message_id):
    social = operation['channel'] == 'social'
    table = 'social_messages' if social else 'private_messages'
    row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND sender_identity=? AND conversation_key=?'
        + (' AND recalled_at IS NULL' if social else ''),
        (int(message_id), operation['sender'], operation['conversation_key'])).fetchone()
    if row is None:
        return None
    if social and operation['conversation_key'].startswith('group:'):
        from .social_conversation_service import require_access
        parsed = require_access(conn, operation['sender'], operation['conversation_key'])
        if row['created_at'] < parsed['membership']['joined_at']:
            return None
    return dict(row)


def _prepare(conn, user, *, channel, conversation_key, payload_hash):
    operation = _binding(conn, user)
    if operation is None:
        return None
    operation.update(channel=channel, conversation_key=conversation_key, payload_hash=payload_hash)
    table = 'social_messages' if channel == 'social' else 'private_messages'
    operation['peer_watermark'] = int(conn.execute(f'SELECT COALESCE(MAX(id),0) FROM {table} '
        'WHERE conversation_key=? AND sender_identity<>?' + (' AND recalled_at IS NULL' if channel == 'social' else ''),
        (conversation_key, operation['sender'])).fetchone()[0])
    prior = conn.execute('SELECT * FROM wanderer_message_deliveries WHERE operation_id=?',
                         (operation['operation_id'],)).fetchone()
    if prior:
        if any(str(prior[name]) != str(operation[name]) for name in
               ('wanderer_id', 'life_epoch', 'channel', 'conversation_key', 'payload_hash')):
            raise ValueError('同一发送操作编号不能用于不同身份、生命或消息内容。')
        message = _actual_message(conn, operation, prior['message_id'])
        if message is None:
            raise ValueError('原消息已删除或撤回，不能重放同一操作。')
        return {**operation, 'message': message, 'origin_operation_id': prior['origin_operation_id'],
                'delivery_kind': 'reused', 'same_operation': True}
    cutoff = (local_now() - timedelta(seconds=REPEAT_WINDOW_SECONDS)).isoformat(timespec='seconds')
    matches = conn.execute('SELECT message_id,origin_operation_id FROM wanderer_message_deliveries '
        'WHERE wanderer_id=? AND life_epoch=? AND channel=? AND conversation_key=? AND payload_hash=? '
        "AND delivery_kind='sent' AND (chain_id=? OR (created_at>=? AND peer_watermark=?)) "
        'ORDER BY created_at DESC,operation_id DESC LIMIT 20',
        (operation['wanderer_id'], operation['life_epoch'], channel, conversation_key, payload_hash,
         operation['chain_id'], cutoff, operation['peer_watermark'])).fetchall()
    for row in matches:
        message = _actual_message(conn, operation, row['message_id'])
        if message is not None:
            operation.update(message=message, origin_operation_id=row['origin_operation_id'], delivery_kind='reused')
            record_delivery(conn, operation, int(message['id']))
            return operation
    return {**operation, 'message': None, 'origin_operation_id': operation['operation_id'], 'delivery_kind': 'sent'}


def prepare_social(conn, user, parsed, payload):
    from .agent_platform_request_context import is_verified_wanderer_request_user
    if not is_verified_wanderer_request_user(user):
        return None
    return _prepare(conn, user, channel='social', conversation_key=parsed['key'],
                    payload_hash=social_payload_hash(payload))


def prepare_private(conn, user, *, conversation_key, content, attachments):
    from .agent_platform_request_context import is_verified_wanderer_request_user
    if not is_verified_wanderer_request_user(user):
        return None
    return _prepare(conn, user, channel='legacy', conversation_key=conversation_key,
                    payload_hash=private_payload_hash(content, attachments))


def record_delivery(conn, operation, message_id):
    if operation is None or operation.get('same_operation'):
        return
    conn.execute('INSERT INTO wanderer_message_deliveries(operation_id,wanderer_id,life_epoch,turn_id,chain_id,'
        'channel,conversation_key,payload_hash,peer_watermark,message_id,origin_operation_id,delivery_kind,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(operation[key] for key in
        ('operation_id','wanderer_id','life_epoch','turn_id','chain_id','channel','conversation_key','payload_hash','peer_watermark'))
        + (int(message_id), operation['origin_operation_id'], operation['delivery_kind'], local_iso()))


def delivery_metadata(operation, message_id):
    if operation is None:
        return {}
    reused = operation['delivery_kind'] == 'reused'
    return {'delivery': {'operation_id': operation['operation_id'], 'message_id': int(message_id),
        'created': not reused, 'reused': reused, 'status': 'already_sent' if reused else 'sent',
        'notice': ('这段内容此前已实际发送，本次复用原消息，没有再次发送、通知或扣交流额度；请勿再发同文。'
                   if reused else '消息已实际保存并发送；后续回调无需再次发送相同内容。')}}


def verified_social_delivery(conn, source, actor, operation_id, key, payload, message_id):
    """Native row + current operation evidence, never trust a claimed HTTP success."""
    from .social_conversation_service import require_access
    from .social_message_service import _require_can_send
    parsed = require_access(conn, actor.key, key)
    _require_can_send(conn, actor.key, parsed)
    ensure_wanderer_delivery_schema(conn)
    row = conn.execute('SELECT * FROM wanderer_message_deliveries WHERE operation_id=? AND wanderer_id=? '
        'AND life_epoch=? AND channel=\'social\' AND conversation_key=? AND payload_hash=?',
        (operation_id, source.wanderer_id, source.life_epoch, parsed['key'], social_payload_hash(payload))).fetchone()
    if row is None or (message_id is not None and int(row['message_id']) != int(message_id)):
        return None
    operation = dict(row) | {'sender': actor.key}
    message = _actual_message(conn, operation, row['message_id'])
    return (message, delivery_metadata(operation, row['message_id'])) if message else None


def verified_private_delivery(conn, source, actor, operation_id, peer, content, message_id):
    from .wanderer_identity_service import load_wanderer, build_wanderer_user_dict
    from .message_center_service import _resolve_contact
    ensure_wanderer_delivery_schema(conn)
    row = conn.execute('SELECT * FROM wanderer_message_deliveries WHERE operation_id=? AND wanderer_id=? '
        "AND life_epoch=? AND channel='legacy'", (operation_id, source.wanderer_id, source.life_epoch)).fetchone()
    if row is None or (message_id is not None and int(row['message_id']) != int(message_id)):
        return None
    operation = dict(row) | {'sender': actor.key}
    message = _actual_message(conn, operation, row['message_id'])
    if message is None or message['recipient_identity'] != peer or _text(message['content']) != _text(content):
        return None
    user = build_wanderer_user_dict(load_wanderer(conn, source.wanderer_id))
    contact = _resolve_contact(conn, user=user, contact_identity=peer, class_offering_id=message['class_offering_id'])
    if not contact or not contact.get('can_send') or contact.get('is_blocked'):
        return None
    from .message_center_service import _is_blocked
    if _is_blocked(conn, peer, actor.key):
        return None
    return message, delivery_metadata(operation, row['message_id'])


def recent_deliveries(conn: Any, wanderer: dict, scope: dict, *, limit: int = 6, budget_chars: int = 6000) -> dict:
    """Refresh only the current readable conversation; never leak a private chat to another scope."""
    from .wanderer_autobiography_service import can_read_scope
    fresh = conn.execute('SELECT life_epoch,authority_version,born_at FROM wanderer_agents WHERE id=?',
                         (int(wanderer['id']),)).fetchone()
    if fresh is None or any(int(wanderer.get(field) or 1) != int(fresh[field]) for field in ('life_epoch', 'authority_version')):
        return {'items': []}
    if scope.get('kind') != 'conversation' or not can_read_scope(conn, wanderer, scope, scope, own=True):
        return {'items': []}
    key = str(scope['key'])
    sender = f"student:{int(wanderer['student_id'])}"
    legacy = '|scope:' in key
    table, field = ('private_messages', 'content') if legacy else ('social_messages', 'body')
    condition, params = '', [key, sender]
    born = to_local_datetime(fresh['born_at'])
    if born:
        condition += ' AND created_at>=?'
        params.append(born.isoformat(timespec='seconds') if legacy else
                      born.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc).isoformat(timespec='microseconds'))
    if not legacy:
        condition += ' AND recalled_at IS NULL'
        if key.startswith('group:'):
            from .social_conversation_service import require_access
            parsed = require_access(conn, sender, key)
            condition += ' AND created_at>=?'
            params.append(parsed['membership']['joined_at'])
    rows = conn.execute(f'SELECT id,created_at,{field} AS content FROM {table} '
        'WHERE conversation_key=? AND sender_identity=?' + condition + ' ORDER BY id DESC LIMIT ?',
        (*params, max(1, min(10, int(limit))))).fetchall()
    items, used = [], 0
    for row in rows:
        content = str(row['content'] or '')
        item = {'id': int(row['id']), 'created_at': row['created_at'], 'channel': 'legacy' if legacy else 'social',
            'status': 'sent', 'content': content[:1200], 'truncated': len(content) > 1200,
            'evidence': {'kind': 'private_message' if legacy else 'social_message', 'id': int(row['id']),
                         'ref': f"{'private_message' if legacy else 'social_message'}:{row['id']}"}}
        size = len(json.dumps(item, ensure_ascii=False, default=str))
        if used + size > max(500, min(10000, int(budget_chars))):
            break
        items.append(item)
        used += size
    return {'conversation_key': key, 'items': items[::-1],
        'notice': '以下是当前权限下可核验的本人已发消息（含此前回调）；已经完成的发送无需重复。这里只显示近期记录，缺少旧项不代表它未发生。'}
