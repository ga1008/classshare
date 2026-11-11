"""Owner-preserving resource reservations. All mutations use the caller's transaction.

Social credits are integers (three million credits = one ordinary message).
Other resources use fixed-point millionths internally and expose ordinary units.
An ended edge never refunds a debit or changes its original owner.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from ..db.schema_wanderer_world import ensure_wanderer_world_schema
from ..time_utils import local_iso, local_now
from .wanderer_bond_service import bond_count, direct_partners, lock_agents

SCALE = 1_000_000
RESOURCE_CATALOG = {
    "friend_requests": {"unit":"次","period":"day","base":"30","share_mode":"consume"},
    "friend_capacity": {"unit":"位","period":"capacity","base":"1000","share_mode":"occupancy"},
    "group_memberships": {"unit":"群","period":"capacity","base":"50","share_mode":"occupancy"},
    "owned_groups": {"unit":"群","period":"capacity","base":"10","share_mode":"occupancy"},
    "friend_request_chars": {"unit":"字/申请","period":"ability","base":"100","share_mode":"ability"},
    "knowledge_digest_chars": {"unit":"字/笔记","period":"ability","base":"min(3000,1000+200*tier)","share_mode":"ability"},
    "reflection_chars": {"unit":"字/自省","period":"ability","base":"min(3200,1200+200*tier)","share_mode":"ability"},
    "todo_title_chars": {"unit":"字/标题","period":"ability","base":"80","share_mode":"ability"},
    "todo_detail_chars": {"unit":"字/详情","period":"ability","base":"400","share_mode":"ability"},
    "ai_wake": {"unit":"次","period":"day","base":"daily_wake_limit(default 20)*(1+0.9*tier)","share_mode":"consume"},
    "social_message": {"unit":"整数通信积分；3,000,000=普通零好感一条","period":"day","base":"social_message_base(default20)*3,000,000","share_mode":"consume"},
    "web_search": {"unit":"次","period":"day","base":"12*(1+0.2*tier)","share_mode":"consume"},
    "active_interactions": {"unit":"次","period":"day","base":"max(daily_action_budget,20*(1+0.2*tier))","share_mode":"consume"},
    "blog_posts": {"unit":"篇","period":"day","base":"personal_blog_posts_base(default1)","share_mode":"consume"},
    "cultivation_interaction": {"unit":"修为","period":"day","base":"60","share_mode":"consume"},
    "cultivation_todo": {"unit":"修为","period":"day","base":"3","share_mode":"consume"},
    "knowledge_chars": {"unit":"字","period":"capacity","base":"tier knowledge capacity * memory talent","share_mode":"occupancy"},
    "memory_chars": {"unit":"字","period":"capacity","base":"tier knowledge capacity * memory talent","share_mode":"occupancy"},
    "todo_capacity": {"unit":"项","period":"capacity","base":"3*(tier+1)","share_mode":"occupancy"},
    "speech_chars": {"unit":"字/条","period":"ability","base":"100*(tier+1)","share_mode":"ability"},
    "recall_chars": {"unit":"字/次回忆","period":"ability","base":"15000","share_mode":"ability"},
}


def _scale(resource):
    return 1 if resource == "social_message" else SCALE


def _units(value, resource):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("资源数量必须是有限非负数")
    return int((amount * _scale(resource)).to_integral_value(rounding=ROUND_CEILING))


def _number(units, resource):
    return units if resource == "social_message" else units / SCALE


def _base(conn, row, resource, base_limits=None):
    owner = int(row["id"])
    if base_limits is not None and (owner in base_limits or str(owner) in base_limits):
        return base_limits.get(owner, base_limits.get(str(owner)))
    from .wanderer_life_core import (active_interaction_limit, wanderer_search_limit,
                                    knowledge_capacity_for, todo_capacity, active_speech_limit)
    tier = int(row.get("tier") or 0)
    fixed={"friend_requests":30,"friend_capacity":1000,"group_memberships":50,"owned_groups":10,
           "friend_request_chars":100,"todo_title_chars":80,"todo_detail_chars":400}
    if resource in fixed:
        return fixed[resource]
    if resource == "knowledge_digest_chars":
        from .wanderer_life_core import knowledge_digest_limit
        return knowledge_digest_limit(tier)
    if resource == "reflection_chars":
        from .wanderer_life_core import reflection_summary_limit
        return reflection_summary_limit(tier)
    if resource == "ai_wake":
        from .wanderer_life_core import ai_wake_limit
        return ai_wake_limit(tier, row.get("daily_wake_limit"))
    if resource == "blog_posts":
        from .wanderer_settings_service import wanderer_runtime_setting_get
        return max(0,int(wanderer_runtime_setting_get(conn,"personal_blog_posts_base","1") or 1))
    if resource == "recall_chars":
        return 15000
    if resource in {"cultivation_interaction", "cultivation_todo"}:
        return 60 if resource == "cultivation_interaction" else 3
    if resource == "social_message":
        from .wanderer_settings_service import wanderer_runtime_setting_get
        return max(0, int(wanderer_runtime_setting_get(conn, "social_message_base", "20") or 20)) * 3_000_000
    if resource == "web_search":
        return wanderer_search_limit(tier)
    if resource == "active_interactions":
        return active_interaction_limit(tier, row.get("daily_action_budget"))
    if resource in {"memory_chars", "knowledge_chars"}:
        talents = json.loads(row.get("talents_json") or "{}")
        return knowledge_capacity_for(tier, talents.get("memory", 1))
    if resource == "todo_capacity":
        return todo_capacity(tier)
    if resource == "speech_chars":
        return active_speech_limit(tier)
    column = {"blog_posts": "daily_post_limit", "comments": "daily_comment_limit"}.get(resource)
    if column and column in row:
        return int(row[column] or 0)
    raise ValueError(f"未知资源：{resource}")


def _owners(conn, actor_id, allow_share=True):
    actor = conn.execute("SELECT * FROM wanderer_agents WHERE id=?", (int(actor_id),)).fetchone()
    if actor is None:
        raise ValueError("散修不存在")
    ids = [int(actor_id)] + (direct_partners(conn, actor_id, include_paused=False) if allow_share else [])
    return [dict(conn.execute("SELECT * FROM wanderer_agents WHERE id=?", (i,)).fetchone()) for i in ids]


def _restricted(row, resource):
    if resource != "active_interactions":
        return False
    from ..time_utils import parse_datetime
    until = parse_datetime(row.get("interaction_penalty_until"))
    return bool(until and until > local_now() and int(row.get("interaction_limit_penalty") or 0) >= 1_000_000)


def _snapshots(conn,actor_id,resources,dates,*,base_limits=None,allow_share=True):
    """Bounded queries per projection, independent of number of resource kinds."""
    ensure_wanderer_world_schema(conn)
    from .wanderer_identity_service import current_life
    actor_id=int(actor_id)
    edges=conn.execute("SELECT first_id,second_id FROM wanderer_bonds WHERE state='active' AND (first_id=? OR second_id=?)",(actor_id,actor_id)).fetchall()
    candidates={actor_id}
    if allow_share:
        for edge in edges:
            candidates.update((int(edge['first_id']),int(edge['second_id'])))
    marks=','.join('?' for _ in candidates)
    agents={int(r['id']):dict(r) for r in conn.execute(f'SELECT * FROM wanderer_agents WHERE id IN ({marks})',tuple(sorted(candidates))).fetchall()}
    if actor_id not in agents:
        raise ValueError('散修不存在')
    def living(row):
        return row['status'] in {'active','paused'} and not current_life(row)['expired']
    owners=[actor_id]+[i for i in sorted(candidates-{actor_id}) if agents[actor_id]['status']=='active' and agents[i]['status']=='active' and living(agents[actor_id]) and living(agents[i])]
    marks=','.join('?' for _ in owners)
    edges=conn.execute(f"SELECT first_id,second_id FROM wanderer_bonds WHERE state='active' AND (first_id IN ({marks}) OR second_id IN ({marks}))",(*owners,*owners)).fetchall()
    neighbors={int(e[field]) for e in edges for field in ('first_id','second_id')}-set(agents)
    if neighbors:
        more=','.join('?' for _ in neighbors)
        agents.update({int(r['id']):dict(r) for r in conn.execute(f'SELECT * FROM wanderer_agents WHERE id IN ({more})',tuple(sorted(neighbors))).fetchall()})
    counts={i:0 for i in owners}
    for edge in edges:
        first,second=int(edge['first_id']),int(edge['second_id'])
        if first not in agents or second not in agents or not living(agents[first]) or not living(agents[second]):
            continue
        for identifier in (first,second):
            if identifier in counts:
                counts[identifier]+=1
    keymarks=','.join('?' for _ in resources)
    days=sorted(set(dates.values()))
    daymarks=','.join('?' for _ in days)
    usages={(int(r['owner_id']),r['resource'],r['budget_date']):r for r in conn.execute(f'SELECT * FROM wanderer_resource_usage WHERE owner_id IN ({marks}) AND resource IN ({keymarks}) AND budget_date IN ({daymarks})',(*owners,*resources,*days)).fetchall()}
    settings={r['key']:r['value'] for r in conn.execute("SELECT key,value FROM wanderer_runtime_settings WHERE key IN ('social_message_base','personal_blog_posts_base')").fetchall()} if any(k in resources for k in ('social_message','blog_posts')) else {}
    result={}
    for resource in resources:
        projected=[]
        for owner in owners:
            row=agents[owner]
            overrides=base_limits
            if base_limits is None and resource in {'social_message','blog_posts'}:
                name='social_message_base' if resource=='social_message' else 'personal_blog_posts_base'
                default=20 if resource=='social_message' else 1
                overrides={owner:max(0,int(settings.get(name,str(default))))*(3_000_000 if resource=='social_message' else 1)}
            base=_units(_base(conn,row,resource,overrides),resource)
            limit=base*(5+counts[owner])//5
            if resource not in {'social_message','cultivation_interaction','cultivation_todo'}:
                limit=limit//_scale(resource)*_scale(resource)
            if _restricted(row,resource):
                limit=0
            usage=usages.get((owner,resource,dates[resource]))
            used,reserved=(int(usage['used_units']),int(usage['reserved_units'])) if usage else (0,0)
            projected.append({'owner_id':owner,'base':_number(base,resource),'bond_count':counts[owner],
                              'version':int(row.get('authority_version') or 1),'life_epoch':int(row.get('life_epoch') or 1),
                              'limit':_number(limit,resource),'used':_number(used,resource),'reserved':_number(reserved,resource),
                              'remaining':_number(max(0,limit-used-reserved),resource)})
        result[resource]={'resource':resource,'date':dates[resource],'owners':projected,
                          **{key:sum(o[key] for o in projected) for key in ('base','limit','used','reserved','remaining')}}
    return result


def snapshot(conn, actor_id, resource, *, base_limits=None, budget_date=None, allow_share=True):
    return _snapshots(conn,actor_id,[resource],{resource:str(budget_date or local_now().date().isoformat())},base_limits=base_limits,allow_share=allow_share)[resource]


def reserve(conn, actor_id, resource, amount, operation_id, *, base_limits=None, budget_date=None, allow_share=True):
    ensure_wanderer_world_schema(conn)
    day = str(budget_date or local_now().date().isoformat())
    wanted = _units(amount, resource)
    if not str(operation_id).strip():
        raise ValueError("资源操作需要幂等键")
    fingerprint = hashlib.sha256(json.dumps([int(actor_id), resource, day, wanted, bool(allow_share)]).encode()).hexdigest()
    rows = _owners(conn, actor_id, allow_share)
    lock_agents(conn, [r["id"] for r in rows])
    prior = conn.execute("SELECT * FROM wanderer_resource_reservations WHERE operation_id=?", (str(operation_id),)).fetchone()
    if prior:
        if budget_date is None:
            fingerprint = hashlib.sha256(json.dumps([int(actor_id), resource, prior["budget_date"], wanted, bool(allow_share)]).encode()).hexdigest()
        if prior["request_hash"] != fingerprint:
            raise ValueError("资源幂等键不能用于其他请求")
        return {"ok": prior["state"] != "released", "receipt": str(operation_id), "state": prior["state"], "owners": json.loads(prior["lines_json"])}
    fresh = dict(conn.execute("SELECT * FROM wanderer_agents WHERE id=?", (int(actor_id),)).fetchone())
    from .wanderer_identity_service import current_life
    if fresh["status"] != "active" or current_life(fresh)["expired"] or _restricted(fresh,resource):
        return {"ok": False, "reason": "散修当前不能执行操作", "receipt": None, "owners": []}
    state = snapshot(conn, actor_id, resource, base_limits=base_limits, budget_date=day, allow_share=allow_share)
    locked = {int(r["id"]) for r in rows}
    candidates = [o for o in state["owners"] if o["owner_id"] in locked]
    if sum(_units(o["remaining"], resource) for o in candidates) < wanted:
        return {"ok": False, "reason": "资源额度不足", "receipt": None, "owners": [], "snapshot": state}
    left, lines, now = wanted, [], local_iso()
    for owner in candidates:
        taken = min(left, _units(owner["remaining"], resource))
        if not taken:
            continue
        conn.execute("INSERT INTO wanderer_resource_usage(owner_id,resource,budget_date,used_units,reserved_units,updated_at) VALUES(?,?,?,0,?,?) ON CONFLICT(owner_id,resource,budget_date) DO UPDATE SET reserved_units=wanderer_resource_usage.reserved_units+excluded.reserved_units,updated_at=excluded.updated_at", (owner["owner_id"], resource, day, taken, now))
        lines.append({"owner_id": owner["owner_id"], "units": taken, "amount": _number(taken, resource), "life_epoch": owner["life_epoch"]})
        left -= taken
        if left == 0:
            break
    conn.execute("INSERT INTO wanderer_resource_reservations(operation_id,actor_id,resource,budget_date,amount_units,request_hash,state,lines_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'reserved',?,?,?)", (str(operation_id), int(actor_id), resource, day, wanted, fingerprint, json.dumps(lines), now, now))
    return {"ok": True, "receipt": str(operation_id), "state": "reserved", "owners": lines}


def settle(conn, operation_id, *, release=False):
    row = conn.execute("SELECT * FROM wanderer_resource_reservations WHERE operation_id=?", (str(operation_id),)).fetchone()
    if row is None:
        raise ValueError("资源预留不存在")
    lines = json.loads(row["lines_json"])
    if row["state"] != "reserved":
        return {"ok": row["state"] == ("released" if release else "consumed"), "receipt": str(operation_id), "state": row["state"], "owners": lines}
    lock_agents(conn, [int(row["actor_id"])] + [i["owner_id"] for i in lines])
    row = conn.execute("SELECT * FROM wanderer_resource_reservations WHERE operation_id=?", (str(operation_id),)).fetchone()
    if row["state"] != "reserved":
        return {"ok": row["state"] == ("released" if release else "consumed"), "receipt": str(operation_id), "state": row["state"], "owners": lines}
    target, now = ("released" if release else "consumed"), local_iso()
    for line in lines:
        conn.execute("UPDATE wanderer_resource_usage SET reserved_units=reserved_units-?,used_units=used_units+?,updated_at=? WHERE owner_id=? AND resource=? AND budget_date=?", (line["units"], 0 if release else line["units"], now, line["owner_id"], row["resource"], row["budget_date"]))
    conn.execute("UPDATE wanderer_resource_reservations SET state=?,updated_at=? WHERE operation_id=? AND state='reserved'", (target, now, str(operation_id)))
    return {"ok": True, "receipt": str(operation_id), "state": target, "owners": lines}


def consume(conn, actor_id, resource, amount, operation_id, **kwargs):
    result = reserve(conn, actor_id, resource, amount, operation_id, **kwargs)
    return settle(conn, operation_id) if result["ok"] else result


def validate_reservation(conn, operation_id):
    """Pre-dispatch check only; already dispatched work still settles original owners."""
    row = conn.execute("SELECT * FROM wanderer_resource_reservations WHERE operation_id=?",(str(operation_id),)).fetchone()
    if row is None or row["state"] != "reserved":
        return False
    legal = {o["id"]:o for o in _owners(conn,row["actor_id"])}
    from .wanderer_identity_service import current_life
    if any(o["status"] != "active" or current_life(o)["expired"] or _restricted(o,row["resource"]) for o in legal.values()):
        return False
    return all(line["owner_id"] in legal and int(legal[line["owner_id"]]["life_epoch"])==int(line["life_epoch"]) for line in json.loads(row["lines_json"]))


def capacity(conn, actor_id, resource, **kwargs):
    return snapshot(conn, actor_id, resource, budget_date="capacity", **kwargs)


def limits(conn, actor_id, resources=None):
    keys=tuple(RESOURCE_CATALOG) if resources is None else tuple(resources)
    dates={key:local_now().date().isoformat() if RESOURCE_CATALOG[key]['period']=='day' else RESOURCE_CATALOG[key]['period'] for key in keys}
    result=_snapshots(conn,actor_id,keys,dates)
    for key in keys:
        spec=RESOURCE_CATALOG[key]
        result[key]={**result[key],"definition":spec,
                     "bonus":"1 + 0.2 * direct_partner_count; per-owner floor at resource precision",
                     "technical_limit":"平台总预算、正常权限、防滥用和单请求边界保持生效"}
    return result


def ability_limit(conn,actor_id,resource):
    if RESOURCE_CATALOG.get(resource,{}).get("period") != "ability":
        raise ValueError("此资源不是逐次能力视图")
    return int(snapshot(conn,actor_id,resource,budget_date="ability")["limit"])


def lock_actor_resources(conn,actor_id):
    """Acquire current lenders and all historical occupancy owners once, sorted."""
    ids=actor_resource_owner_ids(conn,actor_id)
    lock_agents(conn,ids)
    return sorted(ids)


def lock_command_resources(conn,actor_id,command):
    """Prepare all command participants before locking any actor, including strangers."""
    actor_id=int(actor_id)
    args=command.get('args') or {}
    capability=command.get('capability')
    subjects={actor_id}
    if capability in {'bond.propose','bond.equalize','bond.end','life.resurrect'}:
        try:
            candidate=int(args.get('peer_id'))
            if conn.execute('SELECT id FROM wanderer_agents WHERE id=?',(candidate,)).fetchone():
                subjects.add(candidate)
        except (TypeError,ValueError):
            pass  # Domain validation returns the normal invalid-argument receipt.
    elif capability in {'bond.consent','bond.request'}:
        request=conn.execute('SELECT first_id,second_id FROM wanderer_bond_requests WHERE id=?',(str(args.get('request_id') or ''),)).fetchone()
        if request and actor_id in {int(request['first_id']),int(request['second_id'])}:
            subjects.update((int(request['first_id']),int(request['second_id'])))
    elif capability=='life.gift':
        identity=str(args.get('target_identity') or args.get('target') or '')
        role,_,number=identity.partition(':')
        if role=='student' and number.isdigit():
            peer=conn.execute('SELECT id FROM wanderer_agents WHERE student_id=?',(int(number),)).fetchone()
            if peer:
                subjects.add(int(peer['id']))
    def required():
        ids=set()
        for subject in subjects:
            ids.update(actor_resource_owner_ids(conn,subject))
        # A lifecycle command can settle existing burden and suspend edges.
        if str(capability).startswith(('life.','bond.')):
            affected=set(subjects)
            for subject in subjects:
                affected.update(direct_partners(conn,subject))
            marks=','.join('?' for _ in affected)
            for row in conn.execute(f"SELECT first_id,second_id FROM wanderer_bonds WHERE state='active' AND (first_id IN ({marks}) OR second_id IN ({marks}))",(*affected,*affected)).fetchall():
                ids.update((int(row['first_id']),int(row['second_id'])))
        return ids
    ids=required()
    lock_agents(conn,ids)
    if not required().issubset(ids):
        raise PermissionError('关系或资源占用刚发生变化，请重新唤醒后执行')
    return sorted(ids)


def actor_resource_owner_ids(conn,actor_id):
    ensure_wanderer_world_schema(conn)
    ids={int(actor_id),*direct_partners(conn,actor_id)}
    rows=conn.execute("SELECT lines_json FROM wanderer_capacity_allocations WHERE actor_id=?",(int(actor_id),)).fetchall()
    rows+=conn.execute("SELECT lines_json FROM wanderer_resource_reservations WHERE actor_id=? AND state='reserved'",(int(actor_id),)).fetchall()
    for row in rows:
        ids.update(int(line["owner_id"]) for line in json.loads(row["lines_json"]))
    return sorted(ids)


def resize_capacity(conn, actor_id, resource, object_key, amount, *, base_limits=None):
    """Resize one physical object. Shrinking frees the exact original owners."""
    ensure_wanderer_world_schema(conn)
    wanted = _units(amount, resource)
    old = conn.execute("SELECT * FROM wanderer_capacity_allocations WHERE actor_id=? AND resource=? AND object_key=?", (int(actor_id), resource, str(object_key))).fetchone()
    lines = json.loads(old["lines_json"]) if old else []
    current = sum(i["units"] for i in lines)
    owners = _owners(conn, actor_id)
    lock_agents(conn, [r["id"] for r in owners] + [i["owner_id"] for i in lines])
    # Re-read after the lock; another writer may have resized this same object.
    old = conn.execute("SELECT * FROM wanderer_capacity_allocations WHERE actor_id=? AND resource=? AND object_key=?", (int(actor_id), resource, str(object_key))).fetchone()
    lines = json.loads(old["lines_json"]) if old else []
    current = sum(i["units"] for i in lines)
    now = local_iso()
    if wanted > current:
        state = snapshot(conn, actor_id, resource, budget_date="capacity", base_limits=base_limits)
        locked = {r["id"] for r in owners}
        choices = [o for o in state["owners"] if o["owner_id"] in locked]
        extra = wanted-current
        if sum(_units(o["remaining"], resource) for o in choices) < extra:
            return {"ok": False, "reason": "容量不足", "amount": _number(current, resource), "owners": lines}
        for owner in choices:
            take = min(extra, _units(owner["remaining"], resource))
            if not take:
                continue
            conn.execute("INSERT INTO wanderer_resource_usage(owner_id,resource,budget_date,used_units,reserved_units,updated_at) VALUES(?,?,'capacity',?,0,?) ON CONFLICT(owner_id,resource,budget_date) DO UPDATE SET used_units=wanderer_resource_usage.used_units+excluded.used_units,updated_at=excluded.updated_at", (owner["owner_id"], resource, take, now))
            match = next((line for line in lines if line["owner_id"] == owner["owner_id"]), None)
            if match:
                match["units"] += take
            else:
                lines.append({"owner_id": owner["owner_id"], "units": take})
            extra -= take
            if not extra:
                break
    elif wanted < current:
        remove = current-wanted
        for line in reversed(lines):
            take = min(remove, line["units"])
            conn.execute("UPDATE wanderer_resource_usage SET used_units=used_units-?,updated_at=? WHERE owner_id=? AND resource=? AND budget_date='capacity'", (take, now, line["owner_id"], resource))
            line["units"] -= take
            remove -= take
            if not remove:
                break
    lines = [line for line in lines if line["units"]]
    conn.execute("INSERT INTO wanderer_capacity_allocations(actor_id,resource,object_key,amount_units,lines_json,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(actor_id,resource,object_key) DO UPDATE SET amount_units=excluded.amount_units,lines_json=excluded.lines_json,updated_at=excluded.updated_at", (int(actor_id), resource, str(object_key), wanted, json.dumps(lines), now))
    return {"ok": True, "amount": _number(wanted, resource), "owners": lines}


def release_capacity(conn, actor_id, resource, object_key):
    return resize_capacity(conn, actor_id, resource, object_key, 0)


def grandfather_capacity(conn, actor_id, resource, object_key, amount):
    """Startup migration only: account for an existing object even over limit."""
    ensure_wanderer_world_schema(conn)
    lock_agents(conn, [actor_id])
    prior = conn.execute("SELECT 1 FROM wanderer_capacity_allocations WHERE actor_id=? AND resource=? AND object_key=?", (int(actor_id), resource, str(object_key))).fetchone()
    if prior:
        return False
    units, now = _units(amount, resource), local_iso()
    conn.execute("INSERT INTO wanderer_capacity_allocations(actor_id,resource,object_key,amount_units,lines_json,updated_at) VALUES(?,?,?,?,?,?)", (int(actor_id), resource, str(object_key), units, json.dumps([{"owner_id": int(actor_id), "units": units}]), now))
    conn.execute("INSERT INTO wanderer_resource_usage(owner_id,resource,budget_date,used_units,reserved_units,updated_at) VALUES(?,?,'capacity',?,0,?) ON CONFLICT(owner_id,resource,budget_date) DO UPDATE SET used_units=wanderer_resource_usage.used_units+excluded.used_units,updated_at=excluded.updated_at", (int(actor_id), resource, units, now))
    return True


def migrate_capacity(conn):
    """One-time non-destructive accounting of pre-ledger objects at startup."""
    ensure_wanderer_world_schema(conn)
    migrate_daily_usage(conn)
    if conn.execute("SELECT 1 FROM wanderer_world_schema WHERE key='capacity_migration_v1'").fetchone():
        return 0
    count = 0
    sources = (("wanderer_knowledge_entries", "knowledge_chars", "knowledge", "digest", "active"),
               ("wanderer_episodic_memories", "memory_chars", "memory", "content", "active"),
               ("wanderer_todos", "todo_capacity", "todo", None, "pending"))
    for table, resource, prefix, text_column, status in sources:
        measure = f"LENGTH({text_column})" if text_column else "1"
        rows = conn.execute(f"SELECT id,wanderer_id,{measure} AS amount FROM {table} WHERE status=? ORDER BY wanderer_id,id", (status,)).fetchall()
        for row in rows:
            count += int(grandfather_capacity(conn, row["wanderer_id"], resource, f"{prefix}:{row['id']}", int(row["amount"] or 0)))
    conn.execute("INSERT INTO wanderer_world_schema(key,value) VALUES('capacity_migration_v1','done') ON CONFLICT(key) DO NOTHING")
    return count


def migrate_social_capacity(conn):
    """Account for existing ordinary social objects without deleting excess data."""
    ensure_wanderer_world_schema(conn)
    if conn.execute("SELECT 1 FROM wanderer_world_schema WHERE key='social_capacity_migration_v1'").fetchone():
        return 0
    from ..db.connection import get_configured_db_engine
    if get_configured_db_engine() == 'postgres':
        names = {r['name'] for r in conn.execute("SELECT tablename AS name FROM pg_tables WHERE schemaname=current_schema()").fetchall()}
    else:
        names = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not {'social_friendships','social_groups','social_group_members','social_friend_request_log'}.issubset(names):
        return 0
    owners = {f"student:{r['student_id']}":int(r['id']) for r in conn.execute('SELECT id,student_id FROM wanderer_agents').fetchall()}
    count = 0
    for row in conn.execute("SELECT id,identity_a,identity_b FROM social_friendships WHERE status='accepted' ORDER BY id").fetchall():
        for identity in (row['identity_a'],row['identity_b']):
            if identity in owners:
                count += int(grandfather_capacity(conn,owners[identity],'friend_capacity',f"friend:{row['id']}",1))
    for row in conn.execute("SELECT m.group_id,m.member_identity FROM social_group_members m JOIN social_groups g ON g.id=m.group_id WHERE m.left_at IS NULL AND g.dissolved_at IS NULL ORDER BY m.group_id,m.id").fetchall():
        if row['member_identity'] in owners:
            count += int(grandfather_capacity(conn,owners[row['member_identity']],'group_memberships',f"member:{row['group_id']}",1))
    for row in conn.execute('SELECT id,owner_identity FROM social_groups WHERE dissolved_at IS NULL ORDER BY id').fetchall():
        if row['owner_identity'] in owners:
            count += int(grandfather_capacity(conn,owners[row['owner_identity']],'owned_groups',f"owned_group:{row['id']}",1))
    # The ordinary social timestamp is UTC; v2 budgets use the platform day.
    from datetime import timezone
    now = local_now()
    start = now.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat()
    for row in conn.execute("SELECT requester_identity,COUNT(*) AS n FROM social_friend_request_log WHERE event='request' AND created_at>=? GROUP BY requester_identity",(start,)).fetchall():
        owner = owners.get(row['requester_identity'])
        if owner is not None:
            units = int(row['n'])*SCALE
            conn.execute("INSERT INTO wanderer_resource_usage(owner_id,resource,budget_date,used_units,reserved_units,updated_at) VALUES(?,'friend_requests',?,?,0,?) ON CONFLICT(owner_id,resource,budget_date) DO UPDATE SET used_units=CASE WHEN wanderer_resource_usage.used_units>excluded.used_units THEN wanderer_resource_usage.used_units ELSE excluded.used_units END",(owner,now.date().isoformat(),units,local_iso()))
    conn.execute("INSERT INTO wanderer_world_schema(key,value) VALUES('social_capacity_migration_v1','done') ON CONFLICT(key) DO NOTHING")
    return count


def migrate_daily_usage(conn):
    """Preserve pre-v2 counted consumption; never manufacture a fresh daily budget."""
    if conn.execute("SELECT 1 FROM wanderer_world_schema WHERE key='daily_usage_migration_v1'").fetchone():
        return
    from ..db.connection import get_configured_db_engine
    if get_configured_db_engine()=='postgres':
        names={r['name'] for r in conn.execute("SELECT tablename AS name FROM pg_tables WHERE schemaname=current_schema()").fetchall()}
    else:
        names={r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    day=local_now().date().isoformat()
    def record(owner,resource,date,used,reserved=0):
        conn.execute("INSERT INTO wanderer_resource_usage(owner_id,resource,budget_date,used_units,reserved_units,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id,resource,budget_date) DO UPDATE SET used_units=CASE WHEN wanderer_resource_usage.used_units>excluded.used_units THEN wanderer_resource_usage.used_units ELSE excluded.used_units END,reserved_units=CASE WHEN wanderer_resource_usage.reserved_units>excluded.reserved_units THEN wanderer_resource_usage.reserved_units ELSE excluded.reserved_units END",(int(owner),resource,str(date),_units(used,resource),_units(reserved,resource),local_iso()))
    if 'ai_web_search_daily' in names:
        for row in conn.execute("SELECT * FROM ai_web_search_daily WHERE actor_kind='wanderer' AND budget_date=?",(day,)).fetchall():
            record(row['actor_id'],'web_search',row['budget_date'],int(row['used_count'])+int(row['unknown_count']),int(row['reserved_count']))
        if 'ai_web_search_calls' in names:
            for row in conn.execute("SELECT c.*,w.life_epoch FROM ai_web_search_calls c JOIN wanderer_agents w ON w.id=c.actor_id WHERE c.actor_kind='wanderer' AND c.budget_date=? AND c.state IN ('reserved','sent')",(day,)).fetchall():
                op=f"web-search:{int(row['actor_id'])}:{hashlib.sha256(str(row['logical_call_id']).encode()).hexdigest()}"
                fingerprint=hashlib.sha256(json.dumps([int(row['actor_id']),'web_search',day,SCALE,True]).encode()).hexdigest()
                lines=[{"owner_id":int(row['actor_id']),"units":SCALE,"amount":1,"life_epoch":int(row['life_epoch'])}]
                conn.execute("INSERT INTO wanderer_resource_reservations(operation_id,actor_id,resource,budget_date,amount_units,request_hash,state,lines_json,created_at,updated_at) VALUES(?,?,'web_search',?,?,?,'reserved',?,?,?) ON CONFLICT(operation_id) DO NOTHING",(op,int(row['actor_id']),day,SCALE,fingerprint,json.dumps(lines),local_iso(),local_iso()))
    if 'wanderer_action_log' in names:
        for row in conn.execute("SELECT wanderer_id,SUM(ai_calls) AS calls,SUM(CASE WHEN action_kind='write_post' AND outcome='ok' THEN 1 ELSE 0 END) AS posts,SUM(CASE WHEN action_kind IN ('comment','reply','classroom_message','write_post') AND outcome='ok' THEN 1 ELSE 0 END) AS interactions FROM wanderer_action_log WHERE created_at>=? GROUP BY wanderer_id",(day,)).fetchall():
            record(row['wanderer_id'],'ai_wake',day,int(row['calls'] or 0))
            record(row['wanderer_id'],'blog_posts',day,int(row['posts'] or 0))
            record(row['wanderer_id'],'active_interactions',day,int(row['interactions'] or 0))
    for row in conn.execute("SELECT id,stats_json FROM wanderer_agents").fetchall():
        try:
            stats=json.loads(row['stats_json'] or '{}')
        except (ValueError,TypeError):
            stats={}
        if stats.get('cult_day')==day:
            record(row['id'],'cultivation_interaction',day,max(0,float(stats.get('cult_today') or 0)))
        if stats.get('todo_cult_day')==day:
            record(row['id'],'cultivation_todo',day,max(0,float(stats.get('todo_cult') or 0)))
    conn.execute("INSERT INTO wanderer_world_schema(key,value) VALUES('daily_usage_migration_v1','done') ON CONFLICT(key) DO NOTHING")
