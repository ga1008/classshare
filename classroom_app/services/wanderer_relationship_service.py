"""Directed affinity values, system-computed from versioned daily self-assessments."""
from __future__ import annotations

import json
import math
from typing import Any, Iterable, Optional

from ..time_utils import local_iso

AFFINITY_MIN = 0.0
AFFINITY_MAX = 100.0
FEELING_EVENT_LIMIT = 8
FEELING_MAX_MOOD = 20
FEELING_MAX_REASON = 200
FEELING_MAX_SCENE = 40
PROMPT_RELATIONSHIPS = 6
PROMPT_REASON_MAX = 120
GRIEF_AFFINITY_THRESHOLD = 70.0
GRIEF_DELTA = 0.0
REUNION_DELTA = 0.0
_FEELING_LABELS = ((100.0, "倾心"), (80.0, "挚友"), (60.0, "亲近"), (40.0, "平常"), (20.0, "疏远"))


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _loads(raw: Any, kind: type) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except (TypeError, ValueError):
        value = None
    return value if isinstance(value, kind) else kind()


def affinity_label(affinity: Any) -> str:
    value = _as_float(affinity)
    for threshold, label in _FEELING_LABELS:
        if value >= threshold:
            return label
    return "厌恶"


def _valid_peer(wanderer: dict[str, Any], peer_identity: Any) -> str:
    peer = str(peer_identity or "").strip()
    if ":" not in peer or peer == f"student:{wanderer.get('student_id')}":
        return ""
    if not peer.startswith(("student:", "teacher:")):
        return ""
    return peer[:60]


def peer_kind(conn: Any, peer_identity: str) -> str:
    if peer_identity.startswith("teacher:"):
        return "teacher"
    try:
        student_id = int(peer_identity.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        return "student"
    from ..db.savepoint import best_effort

    kind = {"value": "student"}
    with best_effort(conn, "wanderer_peer_kind", log_prefix="[WANDERER]"):
        row = conn.execute("SELECT 1 FROM wanderer_agents WHERE student_id = ? LIMIT 1", (student_id,)).fetchone()
        if row is not None:
            kind["value"] = "wanderer"
    return kind["value"]


def _existing(conn: Any, wanderer_id: int, peer_identity: str) -> Any:
    return conn.execute(
        "SELECT * FROM wanderer_relationships WHERE wanderer_id = ? AND peer_identity = ? LIMIT 1",
        (int(wanderer_id), peer_identity),
    ).fetchone()


def _append_event(feelings: list[Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in feelings if isinstance(item, dict)][-(FEELING_EVENT_LIMIT - 1):]
    return [*items, event]


def _prepare(conn: Any) -> None:
    from ..db.schema_wanderer_social import ensure_wanderer_social_schema
    ensure_wanderer_social_schema(conn)


def affinity_states(conn: Any, wanderer_id: int, peer_identities: Iterable[str]) -> dict[str, dict[str, Any]]:
    """One bounded read for a recipient roster, without one query per group member."""
    _prepare(conn)
    peers = list(dict.fromkeys(str(peer) for peer in peer_identities))
    default = {"affinity":0.0,"revision":0,"origin":"unknown","legacy_affinity":None,"source_turn_id":""}
    result = {peer:dict(default) for peer in peers}
    if not peers:
        return result
    marks = ",".join("?" for _ in peers)
    rows = conn.execute(
        "SELECT r.peer_identity,r.affinity,v.revision,v.origin,v.legacy_affinity,v.source_turn_id "
        "FROM wanderer_relationships r LEFT JOIN wanderer_relationship_versions v "
        "ON v.wanderer_id=r.wanderer_id AND v.peer_identity=r.peer_identity "
        f"WHERE r.wanderer_id=? AND r.peer_identity IN ({marks})", (int(wanderer_id),*peers)).fetchall()
    for row in rows:
        legacy = row["revision"] is None
        result[row["peer_identity"]] = {
            "affinity":max(0.0,min(100.0,(float(row["affinity"]) + 100) / 2 if legacy else float(row["affinity"]))),
            "revision":int(row["revision"] or 0),"origin":"legacy" if legacy else row["origin"],
            "legacy_affinity":row["affinity"] if legacy else row["legacy_affinity"],
            "source_turn_id":row["source_turn_id"] or ""}
    return result


def affinity_state(conn: Any, wanderer_id: int, peer_identity: str) -> dict[str, Any]:
    return affinity_states(conn,wanderer_id,[peer_identity])[peer_identity]


def _ensure_relationship(conn: Any, wanderer: dict[str, Any], peer: str) -> None:
    from .social_common import serialize_on
    _prepare(conn)
    serialize_on(conn, "wanderer-relationship", [f"{wanderer['id']}:{peer}"])
    existing = _existing(conn, int(wanderer["id"]), peer)
    state = affinity_state(conn, int(wanderer["id"]), peer)
    now = local_iso()
    if existing is None:
        conn.execute("INSERT INTO wanderer_relationships (wanderer_id,peer_identity,peer_kind,affinity,interactions,"
                     "last_delta,last_mood,last_reason,feelings_json,first_met_at,last_interaction_at,updated_at) "
                     "VALUES (?,?,?,0,0,0,'','','[]',?,?,?)", (int(wanderer["id"]),peer,peer_kind(conn,peer),now,now,now))
        # A removed relationship cannot inherit the old row's revision or origin.
        conn.execute("DELETE FROM wanderer_relationship_versions WHERE wanderer_id=? AND peer_identity=?",
                     (int(wanderer["id"]),peer))
    elif state["origin"] == "legacy":
        conn.execute("UPDATE wanderer_relationships SET affinity=? WHERE wanderer_id=? AND peer_identity=?",
                     (state["affinity"],int(wanderer["id"]),peer))
    conn.execute("INSERT INTO wanderer_relationship_versions "
                 "(wanderer_id,peer_identity,revision,origin,legacy_affinity,updated_at) VALUES (?,?,0,?,?,?) "
                 "ON CONFLICT (wanderer_id,peer_identity) DO NOTHING",
                 (int(wanderer["id"]),peer,"legacy" if existing is not None else "unrated",state["legacy_affinity"],now))


def set_affinity(conn: Any, wanderer: dict[str, Any], peer_identity: Any, value: Any, *,
                 expected_revision: int, source_turn_id: Any, operation_id: str, reason: Any = "") -> dict[str, Any]:
    """Internal CAS write primitive for the rating calculator/migration fixtures.

    This is not an AI capability. Runtime and legacy model-output adapters must
    accept grades through the daily calculator, never forward absolute values.
    """
    from .social_common import SocialError, serialize_on
    peer = _valid_peer(wanderer, peer_identity)
    try:
        number = float(value)
        if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision < 0:
            raise ValueError("invalid revision")
        revision = expected_revision
    except (TypeError, ValueError):
        raise SocialError("好感度和版本格式无效。", 400)
    if not peer or isinstance(value, bool) or not math.isfinite(number) or not 0 <= number <= 100:
        raise SocialError("好感度必须是 0 至 100 的绝对数值。", 400)
    if not operation_id or not str(source_turn_id or "").strip():
        raise SocialError("好感决定需要指令标识和来源回合。", 400)
    role, pk = peer.split(":", 1)
    if not pk.isdigit() or conn.execute(f"SELECT 1 FROM {'students' if role == 'student' else 'teachers'} WHERE id=?", (int(pk),)).fetchone() is None:
        raise SocialError("联系对象不存在。", 404)
    _prepare(conn)
    serialize_on(conn, "wanderer-affinity-operation", [str(operation_id)])
    done = conn.execute("SELECT * FROM wanderer_relationship_changes WHERE operation_id=?", (operation_id,)).fetchone()
    if done is not None:
        if (int(done["wanderer_id"]) != int(wanderer["id"]) or done["peer_identity"] != peer
                or float(done["new_affinity"]) != number or done["source_turn_id"] != str(source_turn_id)
                or done["reason"] != str(reason or "")[:FEELING_MAX_REASON]):
            raise SocialError("指令标识已用于其他好感决定。", 409)
        return {"peer_identity":peer,"affinity":float(done["new_affinity"]),"revision":int(done["revision"]),"replayed":True}
    _ensure_relationship(conn, wanderer, peer)
    state = affinity_state(conn, int(wanderer["id"]), peer)
    if state["revision"] != revision:
        raise SocialError("好感记录已改变，请读取最新版本后再决定。", 409)
    now = local_iso()
    reason = str(reason or "")[:FEELING_MAX_REASON]
    conn.execute("UPDATE wanderer_relationships SET affinity=?,last_delta=?,last_reason=?,updated_at=? "
                 "WHERE wanderer_id=? AND peer_identity=?", (number,number-state["affinity"],reason,now,int(wanderer["id"]),peer))
    conn.execute("UPDATE wanderer_relationship_versions SET revision=revision+1,origin='self',source_turn_id=?,updated_at=? "
                 "WHERE wanderer_id=? AND peer_identity=?", (str(source_turn_id),now,int(wanderer["id"]),peer))
    conn.execute("INSERT INTO wanderer_relationship_changes "
                 "(operation_id,wanderer_id,peer_identity,old_affinity,new_affinity,revision,source_turn_id,reason,created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (operation_id,int(wanderer["id"]),peer,state["affinity"],number,revision+1,str(source_turn_id),reason,now))
    return {"peer_identity":peer,"affinity":number,"revision":revision+1,"delta":number-state["affinity"],"replayed":False}


def record_feeling(conn: Any, wanderer: dict[str, Any], peer_identity: Any, *,
                   delta: Any = 0, mood: Any = "", reason: Any = "", scene: Any = "") -> Optional[dict[str, Any]]:
    """Record an encounter. Conversation outputs never change calculated affinity."""
    peer = _valid_peer(wanderer, peer_identity)
    if not peer:
        return None
    _ensure_relationship(conn,wanderer,peer)
    existing = _existing(conn,int(wanderer["id"]),peer)
    state = affinity_state(conn,int(wanderer["id"]),peer)
    now = local_iso()
    event = {"at":now,"delta":0,"mood":str(mood or "")[:FEELING_MAX_MOOD],
             "reason":str(reason or "")[:FEELING_MAX_REASON],"scene":str(scene or "")[:FEELING_MAX_SCENE]}
    conn.execute("UPDATE wanderer_relationships SET interactions=interactions+1,last_mood=?,last_reason=?,"
                 "feelings_json=?,last_interaction_at=?,updated_at=? WHERE wanderer_id=? AND peer_identity=?",
                 (event["mood"],event["reason"],json.dumps(_append_event(_loads(existing["feelings_json"],list),event),ensure_ascii=False),
                  now,now,int(wanderer["id"]),peer))
    return {"peer_identity":peer,**state,"delta":0}


def note_interaction(conn: Any, wanderer: dict[str, Any], peer_identity: Any, *, scene: Any = "") -> None:
    record_feeling(conn,wanderer,peer_identity,scene=scene)


def adjust_relationship(conn: Any, wanderer: dict[str, Any], peer_identity: Any, *,
                        affinity_delta: Any = 0, trust_delta: Any = 0, note: Any = "", scene: Any = "reflection",
                        value: Any = None, expected_revision: int | None = None,
                        source_turn_id: Any = "", operation_id: str = "") -> Optional[dict[str, Any]]:
    if value is not None:
        from .social_common import SocialError
        raise SocialError('好感度由每日自省评级计算，不能通过旧感受接口指定绝对值。', 400)
    return record_feeling(conn,wanderer,peer_identity,reason=note,scene=scene)


def apply_turn_feeling(conn: Any, wanderer: dict[str, Any], peer_identity: Any, turn: Any, *,
                       scene: Any = "") -> Optional[dict[str, Any]]:
    turn = turn if isinstance(turn,dict) else {}
    feeling = turn.get('feeling') if isinstance(turn.get('feeling'),dict) else {}
    # Old conversational output remains an encounter, never an affinity write.
    return record_feeling(conn,wanderer,peer_identity,mood=turn.get('mood',''),reason=feeling.get('reason',''),scene=scene)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _peer_names(conn: Any, identities: Iterable[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(identities))
    names: dict[str, str] = {}
    students = [item for item in ids if item.startswith("student:") and item.split(":", 1)[1].isdigit()]
    teachers = [item for item in ids if item.startswith("teacher:") and item.split(":", 1)[1].isdigit()]
    if students:
        marks = ",".join("?" for _ in students)
        rows = conn.execute(f"SELECT id, name FROM students WHERE id IN ({marks})",
                            tuple(int(item.split(":", 1)[1]) for item in students)).fetchall()
        names.update({f"student:{int(_row_value(row, 'id'))}": str(_row_value(row, "name", 1) or "")
                      for row in rows})
    if teachers:
        marks = ",".join("?" for _ in teachers)
        rows = conn.execute(f"SELECT id, name FROM teachers WHERE id IN ({marks})",
                            tuple(int(item.split(":", 1)[1]) for item in teachers)).fetchall()
        names.update({f"teacher:{int(_row_value(row, 'id'))}": str(_row_value(row, "name", 1) or "")
                      for row in rows})
    return names


def list_relationships(conn: Any, wanderer_id: int, *, limit: int = 20, by_abs: bool = True) -> list[dict[str, Any]]:
    raw = conn.execute(
        "SELECT * FROM wanderer_relationships WHERE wanderer_id = ? ORDER BY affinity DESC LIMIT ?",
        (int(wanderer_id), max(1, int(limit)) * 4),
    ).fetchall()
    items = []
    states = affinity_states(conn,int(wanderer_id),[str(row["peer_identity"]) for row in raw])
    for row in raw:
        state = states[str(row["peer_identity"])]
        affinity = state["affinity"]
        items.append({
            "peer_identity": str(_row_value(row, "peer_identity", 1) or ""),
            "peer_kind": str(_row_value(row, "peer_kind", 2) or "student"),
            "affinity": affinity, **state,
            "label": "尚未评分" if state["origin"] in {"unknown", "unrated"} else affinity_label(affinity),
            "interactions": int(_row_value(row, "interactions", 5) or 0),
            "last_delta": round(_as_float(_row_value(row, "last_delta", 6)), 1),
            "last_mood": str(_row_value(row, "last_mood", 7) or ""),
            "last_reason": str(_row_value(row, "last_reason", 8) or ""),
            "last_interaction_at": str(_row_value(row, "last_interaction_at", 11) or ""),
            "trust": round(_as_float(_row_value(row, "trust", 13)), 2),
            "familiarity": round(_as_float(_row_value(row, "familiarity", 14)), 2),
            "feelings": _loads(_row_value(row, "feelings_json", 9), list),
        })
    names = _peer_names(conn, [item["peer_identity"] for item in items])
    for item in items:
        item["name"] = names.get(item["peer_identity"]) or item["peer_identity"]
    if by_abs:
        items.sort(key=lambda item: (-abs(item["affinity"]), -item["interactions"]))
    return items[: max(1, int(limit))]


def relationship_block(conn: Any, wanderer: dict[str, Any]) -> str:
    """The prompt block: strongest bonds and strongest dislikes, bounded."""
    try:
        items = list_relationships(conn, int(wanderer["id"]), limit=PROMPT_RELATIONSHIPS)
    except Exception:  # noqa: BLE001 - a missing runtime table must not break a turn
        return ""
    if not items:
        return ""
    lines = []
    for item in items:
        kind = {"wanderer": "散修", "teacher": "老师"}.get(item["peer_kind"], "真实学生")
        reason = item["last_reason"] or item["last_mood"]
        suffix = f"——上次：{str(reason)[:PROMPT_REASON_MAX]}" if reason else ""
        lines.append(f"- {item['name']}（{kind}）：{item['label']}（好感 {item['affinity']:g}%，版本 {item['revision']}，"
                     f"信任 {item['trust']:+.2f}，熟悉 {item['familiarity']:.2f}）{suffix}")
    return ("【你与他人的关系】这只是你自己的感受，会随交流变化，不是对方的标签；回答时可以参考，但不必说出来。\n"
            + "\n".join(lines))


def relationship_rankings(conn, wanderer_id, *, direction='outgoing', offset=0, limit=20, query=''):
    """Admin/self consumers get actual directed values, with server-side ordering.

    Incoming values exist only for AI authors who have a recorded relationship;
    a human's friendship is never invented as a reciprocal numerical feeling.
    """
    from .wanderer_daily_reflection_service import rules, snapshot, GRADES
    from ..db.schema_wanderer_reflection import ensure_wanderer_reflection_schema
    from .wanderer_identity_service import load_wanderer
    from .social_identity_service import friend_people, other_side
    ensure_wanderer_reflection_schema(conn)
    _prepare(conn)
    w = load_wanderer(conn, int(wanderer_id))
    if not w:
        raise LookupError('散修不存在')
    if direction not in {'outgoing','incoming'}:
        raise ValueError('排行方向须为outgoing或incoming')
    offset, limit = max(0,int(offset)), max(1,min(100,int(limit)))
    me = f"student:{w['student_id']}"
    friendships, people = friend_people(conn, me)
    friends = {other_side(item, me) for item in friendships if other_side(item, me) in people}
    where, params = ('r.wanderer_id=?', (int(wanderer_id),)) if direction=='outgoing' else ('r.peer_identity=?', (me,))
    query = str(query or '').strip()[:100].lower()
    if query:
        pattern = f'%{query}%'
        if direction == 'incoming':
            where += " AND (LOWER(author.display_name) LIKE ? OR ('student:' || CAST(author.student_id AS TEXT)) LIKE ? OR LOWER(r.last_reason) LIKE ?)"
            params += (pattern,pattern,pattern)
        else:
            where += (" AND (LOWER(r.peer_identity) LIKE ? OR EXISTS(SELECT 1 FROM students s "
                      "WHERE r.peer_identity=('student:' || CAST(s.id AS TEXT)) AND LOWER(s.name) LIKE ?) "
                      "OR EXISTS(SELECT 1 FROM teachers t WHERE r.peer_identity=('teacher:' || CAST(t.id AS TEXT)) "
                      "AND LOWER(t.name) LIKE ?) OR LOWER(r.last_reason) LIKE ?)")
            params += (pattern,pattern,pattern,pattern)
    join = ' JOIN wanderer_agents author ON author.id=r.wanderer_id '
    total = int(conn.execute('SELECT COUNT(*) AS n FROM wanderer_relationships r'+join+'WHERE '+where,params).fetchone()['n'])
    effective = 'CASE WHEN v.revision IS NULL THEN (r.affinity+100)/2.0 ELSE r.affinity END'
    rows = conn.execute('SELECT r.*,v.revision,v.origin,v.source_turn_id,author.student_id AS author_student_id,'
        'author.display_name AS author_name,'+effective+' AS effective_affinity,'
        'rating.grade AS latest_grade,rating.updated_at AS rated_at,rating.reason AS rating_reason '
        'FROM wanderer_relationships r'+join+
        'LEFT JOIN wanderer_relationship_versions v ON v.wanderer_id=r.wanderer_id AND v.peer_identity=r.peer_identity '
        'LEFT JOIN wanderer_affinity_ratings rating ON rating.wanderer_id=r.wanderer_id AND rating.peer_identity=r.peer_identity '
        'AND rating.reflection_date=(SELECT MAX(r2.reflection_date) FROM wanderer_affinity_ratings r2 '
        'WHERE r2.wanderer_id=r.wanderer_id AND r2.peer_identity=r.peer_identity) '
        'WHERE '+where+' ORDER BY effective_affinity DESC,r.updated_at DESC,r.wanderer_id,r.peer_identity LIMIT ? OFFSET ?',
        (*params,limit,offset)).fetchall()
    names = _peer_names(conn,[row['peer_identity'] for row in rows]) if direction=='outgoing' else {}
    items=[]
    for index,row in enumerate(rows):
        peer=row['peer_identity'] if direction=='outgoing' else f"student:{row['author_student_id']}"
        value=max(0.0,min(100.0,float(row['effective_affinity'])))
        origin=row['origin'] or 'legacy'
        grade=row['latest_grade']
        items.append({'peer_identity':peer,'name':names.get(peer,peer) if direction=='outgoing' else row['author_name'],
            'affinity':value,'rank':offset+index+1,'revision':int(row['revision'] or 0),'origin':origin,
            'label':'尚未评分' if origin in {'unrated','unknown'} else affinity_label(value),
            'last_rating':grade,'last_rating_label':GRADES[grade][0] if grade in GRADES else '',
            'rated_at':row['rated_at'] or '', 'reason':row['rating_reason'] or row['last_reason'] or '',
            'is_friend':peer in friends})
    return {'items':items,'total':total,'next_offset':offset+limit if offset+limit<total else None,
            'direction':direction,'rules':rules(),'reflection':snapshot(conn,w)}


def public_summary(conn: Any, wanderer_id: int) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN affinity >= 60 THEN 1 ELSE 0 END) AS close, "
        "SUM(CASE WHEN affinity < 20 THEN 1 ELSE 0 END) AS dislike "
        "FROM wanderer_relationships WHERE wanderer_id = ?", (int(wanderer_id),),
    ).fetchone()
    return {"total": int(_row_value(row, "n") or 0), "close": int(_row_value(row, "close", 1) or 0),
            "dislike": int(_row_value(row, "dislike", 2) or 0)}


def mutual_affinity(conn: Any, wanderer_a: dict[str, Any], peer_identity: Any, *, minimum: float) -> bool:
    peer = _valid_peer(wanderer_a, peer_identity)
    if not peer or not peer.startswith("student:"):
        return False
    row = conn.execute(
        "SELECT affinity FROM wanderer_relationships WHERE wanderer_id = ? AND peer_identity = ? LIMIT 1",
        (int(wanderer_a["id"]), peer),
    ).fetchone()
    if row is None or _as_float(_row_value(row, "affinity")) < minimum:
        return False
    try:
        peer_student_id = int(peer.split(":", 1)[1])
    except (TypeError, ValueError):
        return False
    peer_wanderer = conn.execute(
        "SELECT id FROM wanderer_agents WHERE student_id = ? LIMIT 1", (peer_student_id,),
    ).fetchone()
    if peer_wanderer is None:
        return False
    back = conn.execute(
        "SELECT affinity FROM wanderer_relationships WHERE wanderer_id = ? AND peer_identity = ? LIMIT 1",
        (int(_row_value(peer_wanderer, "id")), f"student:{wanderer_a.get('student_id')}"),
    ).fetchone()
    return back is not None and _as_float(_row_value(back, "affinity")) >= minimum


# ---------------------------------------------------------------------------
# Death / resurrection atmosphere
# ---------------------------------------------------------------------------

def _event_for_peer(conn: Any, wanderer_id: int, peer_identity: str, *, delta: float, mood: str, reason: str,
                    scene: str) -> None:
    row = _existing(conn, int(wanderer_id), peer_identity)
    if row is None:
        return
    feelings = _loads(_row_value(row, "feelings_json"), list)
    affinity = affinity_state(conn, int(wanderer_id), peer_identity)["affinity"]
    delta = 0.0
    now = local_iso()
    event = {"at": now, "delta": round(delta, 1), "mood": mood, "reason": reason[:FEELING_MAX_REASON], "scene": scene}
    conn.execute(
        "UPDATE wanderer_relationships SET affinity = ?, last_delta = ?, last_mood = ?, last_reason = ?, "
        "feelings_json = ?, updated_at = ? WHERE wanderer_id = ? AND peer_identity = ?",
        (affinity, round(delta, 1), mood, event["reason"],
         json.dumps(_append_event(feelings, event), ensure_ascii=False), now, int(wanderer_id), peer_identity),
    )


def grieve_death(conn: Any, deceased: dict[str, Any]) -> int:
    """Record the death notice for close peers; they choose their own response."""
    identity = f"student:{deceased.get('student_id')}"
    name = str(deceased.get("display_name") or "一位散修")
    touched = 0
    rows = conn.execute(
        "SELECT wanderer_id, affinity FROM wanderer_relationships WHERE peer_identity = ? AND affinity >= ?",
        (identity, GRIEF_AFFINITY_THRESHOLD),
    ).fetchall()
    for row in rows:
        _event_for_peer(conn, int(_row_value(row, "wanderer_id")), identity, delta=GRIEF_DELTA,
                        mood="", reason=f"听闻{name}陨落，由你自行决定感受。", scene="death")
        touched += 1
    return touched


def reunite(conn: Any, revived: dict[str, Any]) -> int:
    """Record the return notice; no automatic emotional or affinity change."""
    identity = f"student:{revived.get('student_id')}"
    name = str(revived.get("display_name") or "一位散修")
    touched = 0
    rows = conn.execute(
        "SELECT wanderer_id FROM wanderer_relationships WHERE peer_identity = ? AND affinity >= ?",
        (identity, GRIEF_AFFINITY_THRESHOLD),
    ).fetchall()
    for row in rows:
        _event_for_peer(conn, int(_row_value(row, "wanderer_id")), identity, delta=REUNION_DELTA,
                        mood="", reason=f"{name}回来了，由你自行决定感受。", scene="revive")
        touched += 1
    return touched
