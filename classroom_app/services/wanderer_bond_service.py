"""Direct, independently consented wanderer bonds. Caller owns every transaction."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from ..db.schema_wanderer_world import ensure_wanderer_world_schema
from ..time_utils import local_iso, local_now

RULE_VERSION = "wanderer-world-v2"
WEEK_SECONDS = 604800


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def lock_agents(conn, ids):
    """Global lock order shared with quota and sanction settlement."""
    for identifier in sorted(set(int(x) for x in ids)):
        cursor = conn.execute("UPDATE wanderer_agents SET authority_version=authority_version WHERE id=?", (identifier,))
        if cursor.rowcount != 1:
            raise ValueError("散修不存在")


def lock_lifecycle(conn, ids):
    """Lock affected lives plus their neighbors before any death can suspend edges."""
    subjects=sorted(set(int(x) for x in ids))
    marks=','.join('?' for _ in subjects)
    def participants():
        rows=conn.execute(f"SELECT first_id,second_id FROM wanderer_bonds WHERE state='active' AND (first_id IN ({marks}) OR second_id IN ({marks}))",(*subjects,*subjects)).fetchall()
        return set(subjects)|{int(row[key]) for row in rows for key in ('first_id','second_id')}
    locked=participants()
    lock_agents(conn,locked)
    if not participants().issubset(locked):
        raise ValueError('道侣关系刚发生变化，请重试本次生命结算')
    return sorted(locked)


def _agent(conn, identifier, *, alive=False):
    row = conn.execute("SELECT * FROM wanderer_agents WHERE id=?", (int(identifier),)).fetchone()
    if row is None:
        raise ValueError("散修不存在")
    result = dict(row)
    if alive and result["status"] not in {"active", "paused"}:
        raise ValueError("散修已陨落")
    if alive:
        from .wanderer_identity_service import current_life
        if current_life(result)["expired"]:
            raise ValueError("散修寿元已耗尽")
    return result


def _pair(first, second):
    first, second = sorted((int(first), int(second)))
    if first <= 0 or first == second:
        raise ValueError("道侣须为两个不同散修")
    return first, second, f"{first}:{second}"


def direct_partners(conn, wanderer_id, *, include_paused=True):
    ensure_wanderer_world_schema(conn)
    states = "('active','paused')" if include_paused else "('active')"
    rows = conn.execute(
        "SELECT CASE WHEN b.first_id=? THEN b.second_id ELSE b.first_id END AS peer "
        "FROM wanderer_bonds b JOIN wanderer_agents a ON a.id=b.first_id JOIN wanderer_agents z ON z.id=b.second_id "
        f"WHERE b.state='active' AND (b.first_id=? OR b.second_id=?) AND a.status IN {states} AND z.status IN {states} ORDER BY peer",
        (int(wanderer_id), int(wanderer_id), int(wanderer_id)),
    ).fetchall()
    from .wanderer_identity_service import current_life
    own = conn.execute("SELECT * FROM wanderer_agents WHERE id=?", (int(wanderer_id),)).fetchone()
    if own is None or current_life(dict(own))["expired"]:
        return []
    result = []
    for row in rows:
        peer = conn.execute("SELECT * FROM wanderer_agents WHERE id=?",(int(row["peer"]),)).fetchone()
        if not current_life(dict(peer))["expired"]:
            result.append(int(row["peer"]))
    return result


list_direct_partners = direct_partners


def bond_count(conn, wanderer_id):
    return len(direct_partners(conn, wanderer_id))


def is_direct_partner(conn, first, second):
    return int(second) in direct_partners(conn, first)


def _mutual_full_affinity(conn, first, second):
    from .wanderer_relationship_service import affinity_state
    for owner, peer in ((first, second), (second, first)):
        state = affinity_state(conn, owner['id'], f"student:{peer['student_id']}")
        # Both the retained v2 choice and a server-calculated daily assessment
        # are deliberate expressions; migration/unrated defaults are not.
        if state['origin'] not in {'self', 'daily_reflection'} or state['revision'] <= 0 or state['affinity'] != 100:
            return False
    return True


def _public_request(row):
    result = dict(row)
    result.pop("review_token", None)
    result["result"] = json.loads(result.pop("result_json") or "{}")
    return result


def get_request(conn, actor_id, request_id):
    ensure_wanderer_world_schema(conn)
    row = conn.execute("SELECT * FROM wanderer_bond_requests WHERE id=?", (str(request_id),)).fetchone()
    if row is None or int(actor_id) not in {int(row["first_id"]), int(row["second_id"])}:
        raise ValueError("申请不存在或没有访问权限")
    return _public_request(row)


def _notify_application(conn, request, actor_id):
    from .message_center_service import _build_notification_payload, _insert_notification
    from .wanderer_runtime_service import enqueue_wake_event
    actor = _agent(conn,actor_id)
    for owner_id in (int(request["first_id"]),int(request["second_id"])):
        if owner_id == int(actor_id):
            continue
        owner = _agent(conn,owner_id)
        kind = "均寿" if request["kind"] == "equalize" else "结契"
        text = f"{actor['display_name']}的{kind}申请：{request['state']}。请自主决定并读取申请详情。"
        metadata = {"request_id":request["id"],"version":request["version"],"state":request["state"],"kind":request["kind"]}
        payload = _build_notification_payload(recipient_role="student",recipient_user_pk=int(owner["student_id"]),category="wanderer_bond",
                                             title=f"{kind}申请动态",body_preview=text,actor_display_name=actor["display_name"],ref_type="wanderer_bond_request",ref_id=request["id"],metadata=metadata)
        _insert_notification(conn,{**payload,"email_notification_allowed":False})
        enqueue_wake_event(conn,owner_id,"bond",f"{request['id']}:{request['version']}",{"kind":"bond_request","request":metadata,"scope":{"kind":"private"}})


def propose(conn, actor_id, peer_id, *, operation_id, reason="", kind="bond"):
    ensure_wanderer_world_schema(conn)
    first_id, second_id, key = _pair(actor_id, peer_id)
    if kind not in {"bond", "equalize"}:
        raise ValueError("未知申请类型")
    lock_agents(conn, (first_id, second_id))
    first, second = _agent(conn, first_id, alive=True), _agent(conn, second_id, alive=True)
    prior = conn.execute("SELECT * FROM wanderer_bond_requests WHERE id=?", (str(operation_id),)).fetchone()
    if prior:
        if prior["pair_key"] != key or prior["kind"] != kind or int(prior["proposer_id"]) != int(actor_id):
            raise ValueError("同一操作编号已绑定其他申请")
        return _public_request(prior)
    bond = conn.execute("SELECT * FROM wanderer_bonds WHERE pair_key=?", (key,)).fetchone()
    if kind == "equalize" and (not bond or bond["state"] != "active"):
        raise ValueError("均寿只能向有效直接道侣申请")
    if kind == "bond" and bond and bond["state"] == "active":
        raise ValueError("双方已经是有效道侣")
    pending = conn.execute("SELECT * FROM wanderer_bond_requests WHERE pair_key=? AND kind=? AND state IN ('awaiting_consent','ready','reviewing')", (key, kind)).fetchone()
    if pending:
        return _public_request(pending)
    now = local_iso()
    conn.execute("INSERT INTO wanderer_bond_requests(id,pair_key,kind,proposer_id,first_id,second_id,state,rule_version,first_epoch,second_epoch,first_consent,second_consent,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,'awaiting_consent',?,?,?,?,?,?,?,?)",
                 (str(operation_id), key, kind, int(actor_id), first_id, second_id, RULE_VERSION,
                  int(first["life_epoch"]), int(second["life_epoch"]), int(int(actor_id) == first_id), int(int(actor_id) == second_id), str(reason)[:1000], now, now))
    result=get_request(conn, actor_id, operation_id)
    _notify_application(conn,result,actor_id)
    return result


def consent(conn, actor_id, request_id, *, accept, expected_revision):
    ensure_wanderer_world_schema(conn)
    request = get_request(conn, actor_id, request_id)
    lock_agents(conn, (request["first_id"], request["second_id"]))
    row = conn.execute("SELECT * FROM wanderer_bond_requests WHERE id=?", (str(request_id),)).fetchone()
    if int(row["version"]) != int(expected_revision):
        raise ValueError("申请版本已变化，请重新读取")
    if row["state"] not in {"awaiting_consent", "ready", "reviewing"}:
        raise ValueError("申请已结束")
    first, second = _agent(conn, row["first_id"], alive=True), _agent(conn, row["second_id"], alive=True)
    if int(first["life_epoch"]) != int(row["first_epoch"]) or int(second["life_epoch"]) != int(row["second_epoch"]):
        raise ValueError("双方生命状态已变化，请提出新申请")
    field = "first_consent" if int(actor_id) == int(row["first_id"]) else "second_consent"
    if not accept:
        conn.execute(f"UPDATE wanderer_bond_requests SET {field}=0,state='withdrawn',review_token='',version=version+1,updated_at=? WHERE id=?", (local_iso(), str(request_id)))
    else:
        first_ok = bool(row["first_consent"]) or field == "first_consent"
        second_ok = bool(row["second_consent"]) or field == "second_consent"
        full = row["kind"] == "equalize" or _mutual_full_affinity(conn, first, second)
        state = "ready" if first_ok and second_ok and full else "awaiting_consent"
        conn.execute(f"UPDATE wanderer_bond_requests SET {field}=1,state=?,review_token='',version=version+1,updated_at=? WHERE id=?", (state, local_iso(), str(request_id)))
    result=get_request(conn, actor_id, request_id)
    _notify_application(conn,result,actor_id)
    return result


def _invalidate(conn, ids):
    from .wanderer_identity_service import invalidate_wanderer_cache
    ids = sorted(set(int(x) for x in ids))
    for identifier in ids:
        conn.execute("UPDATE wanderer_agents SET authority_version=authority_version+1,updated_at=? WHERE id=?", (local_iso(), identifier))
    marks = ",".join("?" for _ in ids)
    invalidate_wanderer_cache([r["student_id"] for r in conn.execute(f"SELECT student_id FROM wanderer_agents WHERE id IN ({marks})", tuple(ids)).fetchall()])


def life_credit(conn, owner_id, seconds, *, source_key, kind, detail=None):
    """Idempotent permanent adjustment; independent of tier recomputation."""
    ensure_wanderer_world_schema(conn)
    lock_agents(conn, [owner_id])
    owner = _agent(conn, owner_id)
    prior = conn.execute("SELECT * FROM wanderer_life_ledger WHERE source_key=? AND owner_id=?", (str(source_key), int(owner_id))).fetchone()
    if prior:
        if int(prior["delta_seconds"]) != int(seconds) or prior["kind"] != kind:
            raise ValueError("寿元账目编号与已有内容冲突")
        return False
    conn.execute("INSERT INTO wanderer_life_ledger(source_key,owner_id,delta_seconds,kind,life_epoch,detail_json,created_at) VALUES(?,?,?,?,?,?,?)",
                 (str(source_key), int(owner_id), int(seconds), str(kind), int(owner["life_epoch"]), _json(detail or {}), local_iso()))
    conn.execute("UPDATE wanderer_agents SET life_bonus_seconds=life_bonus_seconds+?,updated_at=? WHERE id=?", (int(seconds), local_iso(), int(owner_id)))
    from .wanderer_identity_service import invalidate_wanderer_cache
    invalidate_wanderer_cache([owner["student_id"]])
    return True


def _notify(conn, owner_id, verdict, reason, evidence):
    from .wanderer_identity_service import insert_dao_review, load_wanderer
    from .heavenly_dao_service import notify_wanderer
    owner = load_wanderer(conn, owner_id)
    review_id = insert_dao_review(conn, wanderer_id=owner_id, review_kind="bond", verdict=verdict,
                                 from_tier=owner["tier"], to_tier=owner["tier"], reason=str(reason)[:500], evidence=evidence)
    notify_wanderer(conn, owner, review_id, verdict, str(reason))


def apply_review(conn, request_id, *, approved, reason, review_token):
    """Internal adjudicator only. A completed full-history review token is required."""
    ensure_wanderer_world_schema(conn)
    row = conn.execute("SELECT * FROM wanderer_bond_requests WHERE id=?", (str(request_id),)).fetchone()
    if row is None:
        raise ValueError("申请不存在")
    lock_agents(conn, (row["first_id"], row["second_id"]))
    row = dict(conn.execute("SELECT * FROM wanderer_bond_requests WHERE id=?", (str(request_id),)).fetchone())
    if row["state"] in {"approved", "rejected"}:
        return _public_request(row)
    if row["state"] != "reviewing" or not review_token or row["review_token"] != review_token:
        raise ValueError("裁决授权已失效")
    pending = conn.execute("SELECT 1 FROM wanderer_dao_history_blocks WHERE request_id=? AND state<>'reviewed' LIMIT 1", (str(request_id),)).fetchone()
    any_block = conn.execute("SELECT 1 FROM wanderer_dao_history_blocks WHERE request_id=? LIMIT 1", (str(request_id),)).fetchone()
    if pending or not any_block:
        raise ValueError("全部历史尚未完成审阅")
    first, second = _agent(conn, row["first_id"], alive=True), _agent(conn, row["second_id"], alive=True)
    if (not row["first_consent"] or not row["second_consent"] or first["life_epoch"] != row["first_epoch"] or second["life_epoch"] != row["second_epoch"]):
        raise ValueError("申请同意或生命状态已变化")
    if approved and row["kind"] == "bond" and not _mutual_full_affinity(conn, first, second):
        raise ValueError("双方当前好感度尚未同时达到100%")
    now = local_iso()
    result = {"approved": bool(approved), "reason": str(reason or "天道已完成审阅。").splitlines()[0][:200]}
    if approved and row["kind"] == "bond":
        bond = conn.execute("SELECT * FROM wanderer_bonds WHERE pair_key=?", (row["pair_key"],)).fetchone()
        if bond and bond["state"] == "active":
            raise ValueError("双方已有生效契约")
        bond_id = str(bond["id"]) if bond else uuid.uuid4().hex
        if bond:
            conn.execute("UPDATE wanderer_bonds SET state='active',version=version+1,last_request_id=?,reason=?,updated_at=? WHERE id=?", (str(request_id), result["reason"], now, bond_id))
        else:
            conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,reason,created_at,updated_at) VALUES(?,?,?,?,'active',?,?,?,?,?)",
                         (bond_id, row["pair_key"], row["first_id"], row["second_id"], str(request_id), str(request_id), result["reason"], now, now))
        granted = []
        for owner in (row["first_id"], row["second_id"]):
            if life_credit(conn, owner, 4 * WEEK_SECONDS, source_key=f"bond:{row['pair_key']}:first", kind="bond_reward", detail={"bond_id": bond_id}):
                granted.append(int(owner))
        conn.execute("UPDATE wanderer_bonds SET reward_granted=1 WHERE id=?", (bond_id,))
        result.update(bond_id=bond_id, rewarded_ids=granted, reward_weeks=4)
        _invalidate(conn, (row["first_id"], row["second_id"]))
    elif approved:
        if not is_direct_partner(conn, row["first_id"], row["second_id"]):
            raise ValueError("直接道侣关系已失效")
        from .wanderer_identity_service import current_life, load_wanderer
        moment = local_now()
        life_a = current_life(load_wanderer(conn, row["first_id"]), moment)
        life_b = current_life(load_wanderer(conn, row["second_id"]), moment)
        a, b = int(life_a["remaining_seconds"]), int(life_b["remaining_seconds"])
        if a <= 0 or b <= 0:
            raise ValueError("均寿时双方必须仍有余寿")
        target_a = (a + b) // 2
        delta = target_a - a
        life_credit(conn, row["first_id"], delta, source_key=f"equalize:{request_id}", kind="equalize", detail={"peer": row["second_id"]})
        life_credit(conn, row["second_id"], -delta, source_key=f"equalize:{request_id}", kind="equalize", detail={"peer": row["first_id"]})
        result.update(before_seconds=[a, b], after_seconds=[a + delta, b - delta], transferred_seconds=abs(delta))
    conn.execute("UPDATE wanderer_bond_requests SET state=?,decision_reason=?,result_json=?,review_token='',version=version+1,updated_at=? WHERE id=?",
                 ("approved" if approved else "rejected", result["reason"], _json(result), now, str(request_id)))
    for owner in (row["first_id"], row["second_id"]):
        _notify(conn, int(owner), "bond_approved" if approved else "bond_rejected", result["reason"], {"request_id": request_id, **result})
    return get_request(conn, row["proposer_id"], request_id)


def end_bond(conn, actor_id, peer_id, *, expected_revision, reason=""):
    ensure_wanderer_world_schema(conn)
    first, second, key = _pair(actor_id, peer_id)
    lock_agents(conn, (first, second))
    row = conn.execute("SELECT * FROM wanderer_bonds WHERE pair_key=?", (key,)).fetchone()
    if row is None:
        raise ValueError("关系不存在")
    if row["state"] == "ended":
        return dict(row)
    if int(row["version"]) != int(expected_revision):
        raise ValueError("关系版本已变化")
    conn.execute("UPDATE wanderer_bonds SET state='ended',version=version+1,reason=?,updated_at=? WHERE id=?", (str(reason)[:1000], local_iso(), row["id"]))
    _invalidate(conn, (first, second))
    for owner in (first, second):
        _notify(conn, owner, "bond_ended", str(reason or "一方自主结束了道侣关系。"), {"bond_id": row["id"], "actor_id": int(actor_id)})
    return dict(conn.execute("SELECT * FROM wanderer_bonds WHERE id=?", (row["id"],)).fetchone())


def suspend_on_death(conn, wanderer_id):
    ensure_wanderer_world_schema(conn)
    peers = [int(row["peer"]) for row in conn.execute("SELECT CASE WHEN first_id=? THEN second_id ELSE first_id END AS peer FROM wanderer_bonds WHERE state='active' AND (first_id=? OR second_id=?)",(int(wanderer_id),int(wanderer_id),int(wanderer_id))).fetchall()]
    conn.execute("UPDATE wanderer_bonds SET state='dormant',version=version+1,updated_at=? WHERE state='active' AND (first_id=? OR second_id=?)", (local_iso(), int(wanderer_id), int(wanderer_id)))
    conn.execute("UPDATE wanderer_bond_requests SET state='stale',review_token='',version=version+1,updated_at=? WHERE state IN ('awaiting_consent','ready','reviewing') AND (first_id=? OR second_id=?)", (local_iso(), int(wanderer_id), int(wanderer_id)))
    if peers:
        _invalidate(conn, peers)


def list_bonds(conn, wanderer_id):
    ensure_wanderer_world_schema(conn)
    return [dict(row) for row in conn.execute("SELECT * FROM wanderer_bonds WHERE first_id=? OR second_id=? ORDER BY created_at,id", (int(wanderer_id), int(wanderer_id))).fetchall()]


def shared_knowledge_qualification(conn, wanderer_id):
    """Source-deduplicated usable learning; no raw private text or partner XP."""
    from .wanderer_autonomous_learning import read_knowledge
    from .wanderer_identity_service import load_wanderer
    reader = load_wanderer(conn, int(wanderer_id))
    rows, cursor = [], None
    while True:
        page = read_knowledge(conn,reader,scope={'kind':'private'},before=cursor,limit=100)
        rows.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    sources = {}
    for row in rows:
        # Same source learnt by two partners is one qualification, even if reworded.
        key = (str(row["source_kind"]), str(row["source_ref"]))
        existing = sources.get(key)
        if existing is None or int(row["chars"]) > existing["chars"]:
            sources[key] = {"entry_id": int(row["id"]), "origin_author": int(row["origin_author"]),
                            "source_kind": key[0], "source_ref": key[1], "chars": int(row["chars"])}
    return {"knowledge_chars": sum(item["chars"] for item in sources.values()),
            "owners": sorted({item["origin_author"] for item in sources.values()}), "sources": list(sources.values())}


def capabilities():
    rows = (
        ("bond.propose", "peer_id/reason；提出结契或休眠恢复申请，双方当前100%好感及同意后天道全历史审阅", True),
        ("bond.equalize", "peer_id/reason；向直接道侣申请均分当前两人余寿，需另一方同意和天道批准", True),
        ("bond.consent", "request_id/accept(bool)/expected_revision；同意或撤回本人的同意", True),
        ("bond.request", "request_id；读取本人参与的申请及版本", False),
        ("bond.list", "读取本人的直接关系和本人参与的待决申请", False),
        ("bond.end", "peer_id/expected_revision/reason；自主结束直接关系", True),
        ("life.status", "读取寿元、修行条件与本人可用的共享学习来源", False),
        ("life.extend", "reason；按现有修行规则消耗修为延寿", True),
        ("life.gift", "target_identity/weeks/reason；按普通赠寿规则向天道申请", True),
        ("life.promotion", "reason；条件达标后自主向天道申请破境", True),
        ("life.appeal", "reason；满足抗辩条件后向天道申请复核", True),
        ("life.resurrect", "peer_id/reason；满足境界和自身寿元条件后复活同修", True),
    )
    return [{"key": key, "description": description, "mutates": mutates} for key, description, mutates in rows]


def execute_command(conn, wanderer, command, *, operation_id, turn_id):
    """Thin runtime adapter. Runtime owns lease checking and command receipts."""
    key = command.get("capability")
    args = command.get("args") or {}
    actor_id = int(wanderer["id"])
    if key == "bond.propose" or key == "bond.equalize":
        return propose(conn, actor_id, args["peer_id"], operation_id=operation_id,
                       reason=args.get("reason", ""), kind="equalize" if key == "bond.equalize" else "bond")
    if key == "bond.consent":
        if not isinstance(args.get("accept"), bool):
            raise ValueError("accept必须是布尔值")
        return consent(conn, actor_id, args["request_id"], accept=args["accept"], expected_revision=args["expected_revision"])
    if key == "bond.request":
        return get_request(conn, actor_id, args["request_id"])
    if key == "bond.list":
        requests = conn.execute("SELECT * FROM wanderer_bond_requests WHERE first_id=? OR second_id=? ORDER BY created_at,id", (actor_id, actor_id)).fetchall()
        return {"bonds": list_bonds(conn, actor_id), "requests": [_public_request(r) for r in requests]}
    if key == "bond.end":
        return end_bond(conn, actor_id, args["peer_id"], expected_revision=args["expected_revision"], reason=args.get("reason", ""))
    from .wanderer_identity_service import load_wanderer, invalidate_wanderer_cache
    from .wanderer_life_service import life_payload, promotion_requirements, extend_life, resurrect
    fresh = load_wanderer(conn, actor_id)
    if key == "life.status":
        return {"life": life_payload(fresh), "promotion": promotion_requirements(conn, fresh),
                "learning": shared_knowledge_qualification(conn, actor_id)}
    if key == "life.extend":
        return extend_life(conn, fresh)
    if key == "life.gift":
        from .wanderer_actions_knowledge import request_lifespan_gift
        ok, reason = request_lifespan_gift(conn, fresh, args)
        if not ok:
            raise ValueError(f"赠寿申请未提交：{reason}")
        return {"submitted": True}
    if key == "life.resurrect":
        target = load_wanderer(conn, int(args["peer_id"]))
        if target is None:
            raise ValueError("目标散修不存在")
        return resurrect(conn, fresh, target, reason=str(args.get("reason") or ""))
    note = str(args.get("reason") or "").strip()[:500]
    now = local_iso()
    if key == "life.promotion":
        if fresh.get("promotion_requested") or not promotion_requirements(conn, fresh)["ready"]:
            raise ValueError("已在等待裁决或尚未满足破境条件")
        conn.execute("UPDATE wanderer_agents SET promotion_requested=1,promotion_requested_at=?,promotion_note=?,updated_at=? WHERE id=?", (now, note, now, actor_id))
    elif key == "life.appeal":
        from .wanderer_actions_knowledge import appeal_eligible
        if not note or not appeal_eligible(conn, fresh):
            raise ValueError("尚未满足抗辩条件或未提供理由")
        conn.execute("UPDATE wanderer_agents SET appeal_requested=1,appeal_requested_at=?,appeal_note=?,last_appeal_at=?,updated_at=? WHERE id=?", (now, note, now, now, actor_id))
    else:
        raise ValueError("未知能力；天道裁决不能由散修自行执行")
    invalidate_wanderer_cache([fresh["student_id"]])
    return {"submitted": True, "kind": key, "reason": note}
