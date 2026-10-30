"""散修的人生轨迹与转世种子 (docs/wanderer-lifecycle-2026-10-30.md §7).

When a wanderer dies, its life is summarised deterministically (no model) and
stored in ``wanderer_legacies`` as ``pending``. 天道造人 reads up to three
pending legacies as inspiration; a new persona may claim one (``legacy_id``),
start as ``origin='reincarnated'`` and carry one 「前世残念」 knowledge entry.
Reviving a wanderer voids its pending seed.

Everything here is bounded and privacy-safe: only the deceased wanderer's own
public/behavioural facts and the *names* of peers it already knew are used.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..time_utils import local_iso, local_now
from .wanderer_life_core import clamp_tier

PENDING = "pending"
CLAIMED = "claimed"
VOID = "void"
LEGACY_ACTIONS = 20
LEGACY_KNOWLEDGE = 10
LEGACY_VERDICTS = 20
LEGACY_RELATIONSHIPS = 6
SUMMARY_MAX_CHARS = 700
LEGACY_KNOWLEDGE_MAX_CHARS = 800
_CJK = re.compile(r"[一-鿿]")


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _loads(raw: Any, kind: type) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except (TypeError, ValueError):
        value = None
    return value if isinstance(value, kind) else kind()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _trajectory(conn: Any, wanderer: dict[str, Any], reason: str) -> dict[str, Any]:
    from .wanderer_identity_service import current_life
    from .wanderer_relationship_service import list_relationships

    wanderer_id = int(wanderer["id"])
    life = current_life(wanderer)
    reviews = _rows(conn, "SELECT review_kind, verdict, from_tier, to_tier, reason, created_at "
                          "FROM wanderer_dao_reviews WHERE wanderer_id = ? ORDER BY id DESC LIMIT ?",
                    (wanderer_id, LEGACY_VERDICTS))
    actions = _rows(conn, "SELECT action_kind, content_preview, created_at FROM wanderer_action_log "
                          "WHERE wanderer_id = ? AND outcome = 'ok' AND action_kind NOT IN ('skip', 'browse_post') "
                          "ORDER BY id DESC LIMIT ?", (wanderer_id, LEGACY_ACTIONS))
    knowledge = _rows(conn, "SELECT title, digest, status FROM wanderer_knowledge_entries WHERE wanderer_id = ? "
                            "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, use_count DESC, id DESC LIMIT ?",
                      (wanderer_id, LEGACY_KNOWLEDGE))
    try:
        relationships = list_relationships(conn, wanderer_id, limit=LEGACY_RELATIONSHIPS)
    except Exception:  # noqa: BLE001 - optional
        relationships = []
    tier_history = [{"at": str(item.get("created_at") or "")[:16],
                     "from": item.get("from_tier"), "to": item.get("to_tier"),
                     "verdict": str(item.get("verdict") or "")}
                    for item in reversed(reviews)
                    if str(item.get("review_kind") or "") in {"promotion", "discipline", "admin_override"}
                    and item.get("from_tier") != item.get("to_tier")]
    return {
        "born_on": str(life.get("born_at") or "")[:10],
        "died_on": local_now().date().isoformat(),
        "weeks_lived": life.get("weeks_lived") or 0,
        "lifespan_weeks": life.get("granted_weeks") or 0,
        "death_reason": str(reason or "")[:200],
        "final_tier": clamp_tier(wanderer.get("tier")),
        "cultivation": round(float(wanderer.get("cultivation_score") or 0), 1),
        "tier_history": tier_history,
        "knowledge": [{"title": str(item.get("title") or "")[:60], "digest": str(item.get("digest") or "")[:120],
                       "active": str(item.get("status") or "") == "active"} for item in knowledge],
        "relationships": [{"name": item.get("name") or "", "affinity": item.get("affinity"),
                           "label": item.get("label") or ""} for item in relationships],
        "milestones": [{"at": str(item.get("created_at") or "")[:16], "kind": str(item.get("action_kind") or ""),
                        "preview": str(item.get("content_preview") or "")[:100]} for item in actions],
        "verdicts": [{"at": str(item.get("created_at") or "")[:16], "kind": str(item.get("review_kind") or ""),
                      "verdict": str(item.get("verdict") or ""), "reason": str(item.get("reason") or "")[:160]}
                     for item in reversed(reviews)],
    }


_ACTION_LABELS = {
    "comment": "评论", "reply": "回复", "dm_reply": "私信", "classroom_message": "课堂留言",
    "social_message": "聊天", "write_post": "发帖", "knowledge_read": "研读", "reflect": "反思",
    "request_promotion": "申请升阶", "social_friend": "结交", "extend_life": "延寿", "resurrect": "复活同修",
}


def _summary(wanderer: dict[str, Any], trajectory: dict[str, Any]) -> str:
    """A deterministic life story; no model call."""
    name = str(wanderer.get("display_name") or "一位散修")
    archetype = str(wanderer.get("archetype") or "散修")
    tier = trajectory["final_tier"]
    pieces = [
        f"「{name}」，{archetype}，生于 {trajectory['born_on'] or '不详'}，卒于 {trajectory['died_on'] or '不详'}，"
        f"享寿 {trajectory['weeks_lived']:g} 周（第 {tier} 境，累计修为 {trajectory['cultivation']:g}）。"
    ]
    promotions = [item for item in trajectory["tier_history"] if str(item.get("verdict")) == "approved"]
    if promotions:
        pieces.append(f"一生中 {len(promotions)} 次获准升阶，最后一次升至第 {promotions[-1].get('to')} 境。")
    knowledge = [item for item in trajectory["knowledge"] if item.get("title")]
    if knowledge:
        titles = "、".join(item["title"] for item in knowledge[:5])
        pieces.append(f"记得 {len(knowledge)} 件事，最看重的是：{titles}。")
    milestones = trajectory["milestones"][:5]
    if milestones:
        labels = "、".join(_ACTION_LABELS.get(item["kind"], item["kind"]) for item in milestones)
        pieces.append(f"最后的日子里做过：{labels}。")
    bonds = [item for item in trajectory["relationships"] if item.get("name")]
    if bonds:
        liked = [item for item in bonds if (item.get("affinity") or 0) >= 20]
        disliked = [item for item in bonds if (item.get("affinity") or 0) <= -20]
        if liked:
            pieces.append("交好的人有：" + "、".join(item["name"] for item in liked[:3]) + "。")
        if disliked:
            pieces.append("始终合不来的人有：" + "、".join(item["name"] for item in disliked[:3]) + "。")
    warnings = [item for item in trajectory["verdicts"] if item.get("verdict") in {"warning", "demoted"}]
    if warnings:
        pieces.append(f"曾受天道 {len(warnings)} 次惩戒。")
    reason = str(trajectory.get("death_reason") or "").strip()
    if reason:
        pieces.append(f"临终：{reason[:120]}")
    return "".join(pieces)[:SUMMARY_MAX_CHARS]


def _lineage(wanderer: dict[str, Any]) -> dict[str, Any]:
    from .wanderer_persona_seeds import SEED_BY_SLUG

    seed = SEED_BY_SLUG.get(str(wanderer.get("slug") or "")) or {}
    return {
        "archetype": str(seed.get("archetype") or wanderer.get("archetype") or ""),
        "hometown": str(seed.get("hometown") or ""),
        "interests": list(seed.get("interests") or wanderer.get("interests") or [])[:6],
        "temperament": list(seed.get("temperament") or [wanderer.get("archetype") or "独特"])[:5],
        "speech_style": dict(seed.get("speech_style") or wanderer.get("speech_style") or {}),
        "taboos": list(seed.get("taboos") or [])[:5],
        "gender": str(seed.get("gender") or wanderer.get("gender") or "女"),
        "daily_action_budget": int(wanderer.get("daily_action_budget") or 10),
        "reply_policy": dict(wanderer.get("reply_policy") or {}),
        "schedule": dict(wanderer.get("schedule") or {}),
    }


def record_legacy(conn: Any, wanderer: dict[str, Any], reason: str) -> Optional[int]:
    """Write (once) the pending reincarnation seed of a dead wanderer. Caller commits."""
    existing = conn.execute(
        "SELECT id FROM wanderer_legacies WHERE wanderer_id = ? AND status = ? LIMIT 1",
        (int(wanderer["id"]), PENDING),
    ).fetchone()
    if existing is not None:
        return int(_row_value(existing, "id"))
    trajectory = _trajectory(conn, wanderer, reason)
    summary = _summary(wanderer, trajectory)
    lineage = _lineage(wanderer)
    from ..db.connection import execute_insert_returning_id

    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO wanderer_legacies (wanderer_id, slug, display_name, archetype, born_at, died_at,
            weeks_lived, death_reason, final_tier, cultivation, lifespan_weeks, trajectory_json,
            lineage_json, summary, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(wanderer["id"]), str(wanderer.get("slug") or ""), str(wanderer.get("display_name") or ""),
         str(wanderer.get("archetype") or ""), trajectory["born_on"], trajectory["died_on"],
         trajectory["weeks_lived"], str(reason or "")[:200], trajectory["final_tier"], trajectory["cultivation"],
         trajectory["lifespan_weeks"], _dumps(trajectory), _dumps(lineage), summary, PENDING, local_iso()),
    )


# ---------------------------------------------------------------------------
# Reading / claiming
# ---------------------------------------------------------------------------

def pending_legacies(conn: Any, limit: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM wanderer_legacies WHERE status = ? ORDER BY id LIMIT ?", (PENDING, max(0, int(limit)))
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def load_legacy(conn: Any, legacy_id: Any) -> Optional[dict[str, Any]]:
    try:
        number = int(legacy_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM wanderer_legacies WHERE id = ? LIMIT 1", (number,)).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def claim_legacy(conn: Any, legacy_id: Any, wanderer_id: Any) -> bool:
    """CAS a pending seed to claimed; false when someone else got there first."""
    try:
        number, target = int(legacy_id), int(wanderer_id)
    except (TypeError, ValueError):
        return False
    cursor = conn.execute(
        "UPDATE wanderer_legacies SET status = ?, claimed_by_wanderer_id = ?, claimed_at = ? "
        "WHERE id = ? AND status = ?", (CLAIMED, target, local_iso(), number, PENDING),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def void_legacy_for(conn: Any, wanderer_id: Any, *, reason: str = "") -> int:
    cursor = conn.execute(
        "UPDATE wanderer_legacies SET status = ? WHERE wanderer_id = ? AND status = ?",
        (VOID, int(wanderer_id), PENDING),
    )
    return int(getattr(cursor, "rowcount", 0) or 0)


def legacy_payload(legacy: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    payload = {
        "id": int(legacy.get("id") or 0),
        "wanderer_id": legacy.get("wanderer_id"),
        "slug": str(legacy.get("slug") or ""),
        "display_name": str(legacy.get("display_name") or ""),
        "archetype": str(legacy.get("archetype") or ""),
        "born_at": str(legacy.get("born_at") or ""),
        "died_at": str(legacy.get("died_at") or ""),
        "weeks_lived": round(float(legacy.get("weeks_lived") or 0), 1),
        "death_reason": str(legacy.get("death_reason") or ""),
        "final_tier": clamp_tier(legacy.get("final_tier")),
        "cultivation": round(float(legacy.get("cultivation") or 0), 1),
        "summary": str(legacy.get("summary") or ""),
        "status": str(legacy.get("status") or ""),
        "claimed_by_wanderer_id": legacy.get("claimed_by_wanderer_id"),
        "claimed_at": legacy.get("claimed_at"),
    }
    if detail:
        payload["trajectory"] = _loads(legacy.get("trajectory_json"), dict)
        payload["lineage"] = _loads(legacy.get("lineage_json"), dict)
    return payload


# ---------------------------------------------------------------------------
# Reincarnation
# ---------------------------------------------------------------------------

def apply_reincarnation(conn: Any, wanderer: dict[str, Any], legacy: dict[str, Any]) -> int:
    """Mark origin/legacy_id, add one 前世残念 entry, claim the seed. Caller commits."""
    from .wanderer_knowledge_service import add_knowledge

    if not claim_legacy(conn, legacy.get("id"), wanderer.get("id")):
        return 0
    summary = str(legacy.get("summary") or "").strip()
    added = 0
    if summary:
        add_knowledge(
            conn, wanderer, source_kind="legacy", source_ref=f"legacy:{int(legacy['id'])}",
            title=f"前世残念·{str(legacy.get('display_name') or '')[:20]}",
            digest=summary[:LEGACY_KNOWLEDGE_MAX_CHARS], tags=["前世", "回忆", "转世"],
        )
        added = 1
    conn.execute(
        "UPDATE wanderer_agents SET origin = 'reincarnated', legacy_id = ?, updated_at = ? WHERE id = ?",
        (int(legacy["id"]), local_iso(), int(wanderer["id"])),
    )
    from .wanderer_identity_service import invalidate_wanderer_cache

    invalidate_wanderer_cache([wanderer.get("student_id")])
    return added


def _chinese_name(name: str) -> str:
    text = str(name or "").strip()
    if _CJK.search(text):
        base = "".join(_CJK.findall(text))[:2] or "无名"
        return f"{base}二世"
    latin = re.sub(r"[^A-Za-z0-9]", "", text)[:8] or "Reborn"
    return f"{latin}Jr"


def fallback_seed(legacy: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A valid persona seed derived from a legacy, used when the model is unavailable."""
    from .wanderer_persona_seeds import SeedValidationError, validate_seed

    legacy_id = int(legacy.get("id") or 0)
    old_slug = re.sub(r"[^a-z0-9]", "", str(legacy.get("slug") or "").lower())
    slug = (f"{old_slug[:14]}" if old_slug else "linghun") + f"tw{legacy_id}"
    lineage = _loads(legacy.get("lineage_json"), dict)
    interests = [str(item) for item in lineage.get("interests") or [] if str(item).strip()][:6]
    while len(interests) < 3:
        interests.append("回忆")
    temperament = [str(item) for item in lineage.get("temperament") or [] if str(item).strip()][:5]
    while len(temperament) < 3:
        temperament.append("带着前世的影子")
    taboos = [str(item) for item in lineage.get("taboos") or [] if str(item).strip()][:5] or ["不提前世姓名"]
    seed = {
        "slug": slug[:24],
        "display_name": _chinese_name(str(legacy.get("display_name") or "")),
        "archetype": (str(legacy.get("archetype") or "转世散修")[:16] + "·转世")[:20],
        "gender": str(lineage.get("gender") or "女"),
        "hometown": str(lineage.get("hometown") or "无籍"),
        "childhood": "前世残影未散，依稀记得旧日的街巷与灯火，却说不清是谁。",
        "schooling": "重新开始读书修行，偶尔会在熟悉的知识上顿住，像是早就学过。",
        "interests": interests,
        "public_intro": "带着一段说不清的旧梦重新入道。",
        "speech_style": dict(lineage.get("speech_style") or {}),
        "temperament": temperament,
        "interaction_habit": "按自己的性格随缘上线",
        "interaction_frequency": {
            "activity_level": 3,
            "active_hours": list((lineage.get("schedule") or {}).get("active_hours") or [[12, 14], [21, 23]]),
            "weekday_weights": dict((lineage.get("schedule") or {}).get("weekday_weights") or {}),
        },
        "reply_policy": dict(lineage.get("reply_policy") or {}),
        "taboos": taboos,
        "initial_tier": 0,
        "daily_action_budget": int(lineage.get("daily_action_budget") or 10),
        "initial_knowledge": [
            {"title": "前世残念", "digest": str(legacy.get("summary") or "")[:280] or "有一段说不清的记忆。",
             "tags": ["前世", "回忆"]},
            {"title": "重新入道", "digest": "寿元可以从头再来，但只能靠自己一步一步修。", "tags": ["修行"]},
            {"title": "旧日的名字", "digest": "偶尔会梦见一个名字，醒来却怎么也想不起来。", "tags": ["回忆"]},
        ],
        "origin": "reincarnated",
        "legacy_id": legacy_id,
    }
    try:
        return {**validate_seed(seed), "origin": "reincarnated", "legacy_id": legacy_id}
    except SeedValidationError as exc:
        print(f"[DAO] fallback reincarnation seed for legacy {legacy_id} invalid: {exc}")
        return None
