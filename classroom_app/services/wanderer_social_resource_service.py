"""Small account dispatch adapter; normal social services retain all ACL/state rules."""
from __future__ import annotations
from .social_common import SocialError


def actor_id(conn,identity):
    role,_,number=str(identity).partition(':')
    if role!='student' or not number.isdigit():
        return None
    from .account_control_service import table_exists
    if not table_exists(conn,'wanderer_agents'):
        return None
    row=conn.execute('SELECT id FROM wanderer_agents WHERE student_id=?',(int(number),)).fetchone()
    return int(row['id']) if row else None


def lock_people(conn,identities):
    from .wanderer_resource_service import actor_resource_owner_ids
    from .wanderer_bond_service import lock_agents
    ids=set()
    for identity in set(identities):
        owner=actor_id(conn,identity)
        if owner is not None:
            ids.update(actor_resource_owner_ids(conn,owner))
    if ids:
        lock_agents(conn,ids)


def room(conn,identity,resource,count,base):
    owner=actor_id(conn,identity)
    if owner is None:
        return int(count)<int(base)
    from .wanderer_resource_service import capacity
    return capacity(conn,owner,resource)['remaining']>=1


def occupy(conn,identity,resource,object_key,amount=1):
    owner=actor_id(conn,identity)
    if owner is None:
        return
    from .wanderer_resource_service import resize_capacity
    result=resize_capacity(conn,owner,resource,object_key,amount)
    if not result['ok']:
        raise SocialError('本人及直接道侣的可用容量不足。',409)


def release(conn,identity,resource,object_key):
    occupy(conn,identity,resource,object_key,0)


def charge_request(conn,identity,log_id):
    owner=actor_id(conn,identity)
    if owner is None:
        return
    from .wanderer_resource_service import consume
    if not consume(conn,owner,'friend_requests',1,f'friend-request:{log_id}')['ok']:
        raise SocialError('今日可用好友申请额度不足。',429)


def request_room(conn,identity,count,base):
    owner=actor_id(conn,identity)
    if owner is None:
        return int(count)<int(base)
    from .wanderer_resource_service import snapshot
    return snapshot(conn,owner,'friend_requests')['remaining']>=1


def text_limit(conn,identity,resource,base):
    owner=actor_id(conn,identity)
    if owner is None:
        return int(base)
    from .wanderer_resource_service import ability_limit
    return ability_limit(conn,owner,resource)
