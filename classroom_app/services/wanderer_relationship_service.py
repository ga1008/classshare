"""散修自己的关系与情绪 (docs/wanderer-lifecycle-2026-10-30.md §5).

One row per (wanderer, peer): affinity −100..100, interaction count, the last
mood/reason and the last eight feeling events. Feelings are *chosen by the
wanderer*: a successful conversation turn may return
``"feeling": {"affinity": -3..3, "reason": "..."}``; 亲和 scales gains and
softens losses. With no feeling output only the interaction count moves — the
service never invents an emotion from another person's words.

Also provides the grief/reunion atmosphere around death and resurrection, the
prompt block, admin lists and the public aggregate counts.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from ..time_utils import local_iso
from .wanderer_life_core import talent_value

AFFINITY_MIN = -100.0
AFFINITY_MAX = 100.0
FEELING_EVENT_LIMIT = 8
FEELING_MAX_MOOD = 20
FEELING_MAX_REASON = 200
FEELING_MAX_SCENE = 40
PROMPT_RELATIONSHIPS = 6
PROMPT_REASON_MAX = 120
GRIEF_AFFINITY_THRESHOLD = 40.0
GRIEF_DELTA = -6.0
REUNION_DELTA = 5.0
_FEELING_LABELS = ((60.0, "挚友"), (20.0, "亲近"), (-19.999, "平常"), (-59.999, "疏远"))


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


def _charm(wanderer: dict[str, Any]) -> float:
    return max(0.2, talent_value(wanderer.get("talents"), "charm"))


def scale_delta(wanderer: dict[str, Any], delta: Any) -> float:
    """Clamp one event to ±5; 亲和 multiplies gains and divides losses."""
    try:
        raw = max(-5.0, min(5.0, float(delta)))
    except (TypeError, ValueError):
        return 0.0
    charm = _charm(wanderer)
    scaled = raw * charm if raw >= 0 else raw / charm
    return round(max(-5.0, min(5.0, scaled)), 1)


def _existing(conn: Any, wanderer_id: int, peer_identity: str) -> Any:
    return conn.execute(
        "SELECT * FROM wanderer_relationships WHERE wanderer_id = ? AND peer_identity = ? LIMIT 1",
        (int(wanderer_id), peer_identity),
    ).fetchone()


def _append_event(feelings: list[Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in feelings if isinstance(item, dict)][-(FEELING_EVENT_LIMIT - 1):]
    return [*items, event]


def record_feeling(conn: Any, wanderer: dict[str, Any], peer_identity: Any, *,
                   delta: Any = 0, mood: Any = "", reason: Any = "", scene: Any = "") -> Optional[dict[str, Any]]:
    """Apply one feeling event and count the interaction. Caller commits."""
    peer = _valid_peer(wanderer, peer_identity)
    if not peer:
        return None
    wanderer_id = int(wanderer["id"])
    existing = _existing(conn, wanderer_id, peer)
    scaled = scale_delta(wanderer, delta)
    now = local_iso()
    event = {"at": now, "delta": scaled, "mood": str(mood or "")[:FEELING_MAX_MOOD],
             "reason": str(reason or "")[:FEELING_MAX_REASON], "scene": str(scene or "")[:FEELING_MAX_SCENE]}
    if existing is None:
        affinity = max(AFFINITY_MIN, min(AFFINITY_MAX, scaled))
        conn.execute(
            "INSERT INTO wanderer_relationships (wanderer_id, peer_identity, peer_kind, affinity, interactions, "
            "last_delta, last_mood, last_reason, feelings_json, first_met_at, last_interaction_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
            (wanderer_id, peer, peer_kind(conn, peer), affinity, scaled, event["mood"], event["reason"],
             json.dumps([event], ensure_ascii=False), now, now, now),
        )
        return {"peer_identity": peer, "affinity": affinity, "delta": scaled}

    feelings = _loads(_row_value(existing, "feelings_json"), list)
    affinity = max(AFFINITY_MIN, min(AFFINITY_MAX, round(_as_float(_row_value(existing, "affinity")) + scaled, 1)))
    interactions = int(_row_value(existing, "interactions") or 0) + 1
    kind = str(_row_value(existing, "peer_kind") or "") or peer_kind(conn, peer)
    conn.execute(
        "UPDATE wanderer_relationships SET affinity = ?, interactions = ?, last_delta = ?, last_mood = ?, "
        "last_reason = ?, feelings_json = ?, peer_kind = ?, last_interaction_at = ?, updated_at = ? "
        "WHERE wanderer_id = ? AND peer_identity = ?",
        (affinity, interactions, scaled, event["mood"], event["reason"],
         json.dumps(_append_event(feelings, event), ensure_ascii=False), kind, now, now, wanderer_id, peer),
    )
    return {"peer_identity": peer, "affinity": affinity, "delta": scaled}


def note_interaction(conn: Any, wanderer: dict[str, Any], peer_identity: Any, *, scene: Any = "") -> None:
    """Count an interaction without inventing a feeling. Caller commits."""
    record_feeling(conn, wanderer, peer_identity, delta=0, reason="", scene=scene)


def adjust_relationship(conn: Any, wanderer: dict[str, Any], peer_identity: Any, *,
                        affinity_delta: Any = 0, trust_delta: Any = 0, note: Any = "",
                        scene: Any = "reflection") -> Optional[dict[str, Any]]:
    """A reflection's small, bounded adjustment to an existing relationship."""
    peer = _valid_peer(wanderer, peer_identity)
    if not peer:
        return None
    existing = _existing(conn, int(wanderer["id"]), peer)
    if existing is None:
        return None
    try:
        delta = max(-2.0, min(2.0, float(affinity_delta)))
    except (TypeError, ValueError):
        delta = 0.0
    try:
        trust_delta = max(-0.2, min(0.2, float(trust_delta)))
    except (TypeError, ValueError):
        trust_delta = 0.0
    affinity = max(AFFINITY_MIN, min(AFFINITY_MAX,
                                     round(_as_float(_row_value(existing, "affinity")) + scale_delta(wanderer, delta), 1)))
    trust = max(-1.0, min(1.0, round(_as_float(_row_value(existing, "trust")) + trust_delta, 3)))
    familiarity = max(0.0, min(1.0, round(_as_float(_row_value(existing, "familiarity")) + 0.02, 3)))
    feelings = _loads(_row_value(existing, "feelings_json"), list)
    now = local_iso()
    event = {"at": now, "delta": delta, "mood": "", "reason": str(note or "")[:FEELING_MAX_REASON],
             "scene": str(scene or "")[:FEELING_MAX_SCENE]}
    conn.execute(
        "UPDATE wanderer_relationships SET affinity = ?, trust = ?, familiarity = ?, last_delta = ?, "
        "last_reason = ?, feelings_json = ?, last_interaction_at = ?, updated_at = ? "
        "WHERE wanderer_id = ? AND peer_identity = ?",
        (affinity, trust, familiarity, delta, event["reason"],
         json.dumps(_append_event(feelings, event), ensure_ascii=False), now, now,
         int(wanderer["id"]), peer),
    )
    return {"peer_identity": peer, "affinity": affinity, "trust": trust, "delta": delta}


def apply_turn_feeling(conn: Any, wanderer: dict[str, Any], peer_identity: Any, turn: Any, *,
                       scene: Any = "") -> Optional[dict[str, Any]]:
    """The optional ``feeling`` object of one turn (already parsed) → one event."""
    feeling = (turn or {}).get("feeling") if isinstance(turn, dict) else None
    if not isinstance(feeling, dict):
        return note_interaction(conn, wanderer, peer_identity, scene=scene)
    return record_feeling(
        conn, wanderer, peer_identity, delta=feeling.get("affinity") or 0, mood=turn.get("mood") or "",
        reason=feeling.get("reason") or turn.get("reason") or "", scene=scene,
    )


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
    for row in raw:
        affinity = round(_as_float(_row_value(row, "affinity")), 1)
        items.append({
            "peer_identity": str(_row_value(row, "peer_identity", 1) or ""),
            "peer_kind": str(_row_value(row, "peer_kind", 2) or "student"),
            "affinity": affinity,
            "label": affinity_label(affinity),
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
        lines.append(f"- {item['name']}（{kind}）：{item['label']}（好感 {item['affinity']:+g}，"
                     f"信任 {item['trust']:+.2f}，熟悉 {item['familiarity']:.2f}）{suffix}")
    return ("【你与他人的关系】这只是你自己的感受，会随交流变化，不是对方的标签；回答时可以参考，但不必说出来。\n"
            + "\n".join(lines))


def public_summary(conn: Any, wanderer_id: int) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN affinity >= 20 THEN 1 ELSE 0 END) AS close, "
        "SUM(CASE WHEN affinity <= -20 THEN 1 ELSE 0 END) AS dislike "
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
    affinity = max(AFFINITY_MIN, min(AFFINITY_MAX, round(_as_float(_row_value(row, "affinity")) + delta, 1)))
    now = local_iso()
    event = {"at": now, "delta": round(delta, 1), "mood": mood, "reason": reason[:FEELING_MAX_REASON], "scene": scene}
    conn.execute(
        "UPDATE wanderer_relationships SET affinity = ?, last_delta = ?, last_mood = ?, last_reason = ?, "
        "feelings_json = ?, updated_at = ? WHERE wanderer_id = ? AND peer_identity = ?",
        (affinity, round(delta, 1), mood, event["reason"],
         json.dumps(_append_event(feelings, event), ensure_ascii=False), now, int(wanderer_id), peer_identity),
    )


def grieve_death(conn: Any, deceased: dict[str, Any]) -> int:
    """Everyone who cared (and other wanderers who liked them) feels the loss. Caller commits."""
    identity = f"student:{deceased.get('student_id')}"
    name = str(deceased.get("display_name") or "一位散修")
    touched = 0
    rows = conn.execute(
        "SELECT wanderer_id, affinity FROM wanderer_relationships WHERE peer_identity = ? AND affinity >= ?",
        (identity, GRIEF_AFFINITY_THRESHOLD),
    ).fetchall()
    for row in rows:
        _event_for_peer(conn, int(_row_value(row, "wanderer_id")), identity, delta=GRIEF_DELTA,
                        mood="难过", reason=f"听闻{name}陨落。", scene="death")
        touched += 1
    return touched


def reunite(conn: Any, revived: dict[str, Any]) -> int:
    """A revived wanderer's close peers feel relief. Caller commits."""
    identity = f"student:{revived.get('student_id')}"
    name = str(revived.get("display_name") or "一位散修")
    touched = 0
    rows = conn.execute(
        "SELECT wanderer_id FROM wanderer_relationships WHERE peer_identity = ? AND affinity >= ?",
        (identity, GRIEF_AFFINITY_THRESHOLD),
    ).fetchall()
    for row in rows:
        _event_for_peer(conn, int(_row_value(row, "wanderer_id")), identity, delta=REUNION_DELTA,
                        mood="欣慰", reason=f"{name}回来了。", scene="revive")
        touched += 1
    return touched
