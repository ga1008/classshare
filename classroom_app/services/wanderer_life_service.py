"""散修寿元与天赋的读写层 (docs/wanderer-lifecycle-2026-10-30.md §2–§6).

Pure math lives in ``wanderer_life_core``; this module owns the short DB
transactions around it:

* ``ensure_life_columns`` / ``backfill_all`` — born_at / 寿元 / 天赋 for rows
  created before the lifecycle release (idempotent, caller commits);
* ``award_interaction_cultivation`` — 互动修为 with a 6-point daily cap;
* ``extend_life`` — burn 25% of cultivation for one more week (max twice);
* ``apply_life_penalty`` — 天罚扣寿, killing at expiry;
* ``apply_talent_change`` — clamped talent mutation (天道 or admin);
* ``promote_life`` — recompute lifespan after a tier change;
* ``resurrect`` — a tier-10 wanderer revives a dead one at 8 weeks of its own life;
* ``promotion_requirements`` / ``life_payload`` — what prompts, pages and the
  admin panel read.

Callers commit unless the docstring says otherwise.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Iterable, Optional

from ..time_utils import local_iso, local_now, parse_datetime
from .wanderer_life_core import (
    INTERACTION_DAILY_CULTIVATION_CAP,
    LIFE_EXTENSION_WEEKS,
    MAX_LIFE_EXTENSIONS,
    RESURRECT_COST_WEEKS,
    RESURRECT_MIN_TIER,
    TALENTS,
    can_extend_life,
    clamp_tier,
    cultivation_target,
    effective_penalty_weeks,
    interaction_gain,
    interaction_requirement,
    knowledge_capacity_for,
    life_extension_cost,
    lifespan_weeks_for,
    normalize_talents,
    promotion_ratio,
    talent_bounds,
    talent_effects,
    talent_value,
    tier_perks,
)

INTERACTION_KINDS = ("comment", "reply", "dm_reply", "classroom_message", "social_message", "write_post")
_CULT_DAY_KEY = "cult_day"
_CULT_TODAY_KEY = "cult_today"
_CULT_TOTAL_KEY = "cult_total"
_PROMOTION_WINDOW_DAYS = 7
_PROMOTION_COOLDOWN_DAYS = 7


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


def _invalidate(wanderer: dict[str, Any]) -> None:
    from .cultivation_display_service import invalidate_emblem_cache
    from .wanderer_identity_service import invalidate_wanderer_cache

    invalidate_wanderer_cache([wanderer.get("student_id")])
    invalidate_emblem_cache([wanderer.get("student_id")])


def _reload(conn: Any, wanderer_id: int) -> Optional[dict[str, Any]]:
    from .wanderer_identity_service import load_wanderer

    return load_wanderer(conn, int(wanderer_id))


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def ensure_life_columns(conn: Any, wanderer: dict[str, Any]) -> dict[str, Any]:
    """Persist born_at / lifespan_weeks / talents_json when a row predates them. Caller commits."""
    wanderer_id = int(wanderer["id"])
    row = conn.execute(
        "SELECT born_at, lifespan_weeks, talents_json FROM wanderer_agents WHERE id = ?", (wanderer_id,)
    ).fetchone()
    if row is None:
        return wanderer
    born_at = str(_row_value(row, "born_at") or "").strip()
    lifespan = _as_float(_row_value(row, "lifespan_weeks", 1))
    stored_talents = _loads(_row_value(row, "talents_json", 2), dict)
    talents = normalize_talents(stored_talents, slug=wanderer.get("slug"))
    if born_at and lifespan > 0 and stored_talents:
        return wanderer
    now = local_iso()
    conn.execute(
        "UPDATE wanderer_agents SET born_at = ?, lifespan_weeks = ?, talents_json = ?, updated_at = ? WHERE id = ?",
        (born_at or wanderer.get("created_at") or now,
         lifespan or lifespan_weeks_for(wanderer.get("tier"), talents["vitality"]),
         json.dumps(talents, ensure_ascii=False, sort_keys=True), now, wanderer_id),
    )
    _invalidate(wanderer)
    return _reload(conn, wanderer_id) or wanderer


def backfill_all(conn: Any) -> int:
    """Backfill every row once (tick start / admin overview). Caller commits."""
    rows = conn.execute(
        "SELECT id, slug, tier, created_at, born_at, lifespan_weeks, talents_json FROM wanderer_agents"
    ).fetchall()
    touched = 0
    for row in rows:
        born_at = str(_row_value(row, "born_at") or "").strip()
        lifespan = _as_float(_row_value(row, "lifespan_weeks", 5))
        stored = _loads(_row_value(row, "talents_json", 6), dict)
        if born_at and lifespan > 0 and stored:
            continue
        slug = str(_row_value(row, "slug", 1) or "")
        tier = _row_value(row, "tier", 2)
        normalized = normalize_talents(stored, slug=slug)
        conn.execute(
            "UPDATE wanderer_agents SET born_at = ?, lifespan_weeks = ?, talents_json = ?, updated_at = ? WHERE id = ?",
            (born_at or str(_row_value(row, "created_at", 3) or local_iso()),
             lifespan or lifespan_weeks_for(tier, normalized["vitality"]),
             json.dumps(normalized, ensure_ascii=False, sort_keys=True), local_iso(), int(_row_value(row, "id"))),
        )
        touched += 1
    return touched


# ---------------------------------------------------------------------------
# Cultivation awards
# ---------------------------------------------------------------------------

def award_interaction_cultivation(conn: Any, wanderer: dict[str, Any], kinds: Iterable[Any]) -> float:
    """Add 互动修为 for successful actions, capped per local day. Caller commits."""
    points = 0.0
    eloquence = talent_value(wanderer.get("talents"), "eloquence")
    for kind in kinds or []:
        points += interaction_gain(kind, eloquence)
    if points <= 0:
        return 0.0
    wanderer_id = int(wanderer["id"])
    conn.execute("UPDATE wanderer_agents SET stats_json = stats_json WHERE id = ?", (wanderer_id,))
    row = conn.execute("SELECT stats_json FROM wanderer_agents WHERE id = ?", (wanderer_id,)).fetchone()
    stats = _loads(_row_value(row, "stats_json") if row is not None else "{}", dict)
    today = local_now().date().isoformat()
    already = _as_float(stats.get(_CULT_TODAY_KEY)) if stats.get(_CULT_DAY_KEY) == today else 0.0
    granted = round(min(float(INTERACTION_DAILY_CULTIVATION_CAP), max(0.0, already + points)) - already, 2)
    if granted <= 0:
        return 0.0
    stats = dict(stats)
    stats[_CULT_DAY_KEY] = today
    stats[_CULT_TODAY_KEY] = round(already + granted, 2)
    stats[_CULT_TOTAL_KEY] = round(_as_float(stats.get(_CULT_TOTAL_KEY)) + granted, 2)
    conn.execute(
        "UPDATE wanderer_agents SET cultivation_score = cultivation_score + ?, stats_json = ?, updated_at = ? "
        "WHERE id = ?",
        (granted, json.dumps(stats, ensure_ascii=False, sort_keys=True), local_iso(), wanderer_id),
    )
    return granted


def interaction_cultivation_locked(conn: Any, wanderer: dict[str, Any], kind: Any) -> bool:
    """Whether one more interaction award would still count today (cheap pre-check)."""
    today = local_now().date().isoformat()
    stats = wanderer.get("stats") or {}
    if not isinstance(stats, dict):
        return True
    if stats.get(_CULT_DAY_KEY) != today:
        return True
    return _as_float(stats.get(_CULT_TODAY_KEY)) < INTERACTION_DAILY_CULTIVATION_CAP


# ---------------------------------------------------------------------------
# Life extension
# ---------------------------------------------------------------------------

def extend_life(conn: Any, wanderer: dict[str, Any]) -> dict[str, Any]:
    """Burn 25% of cultivation for one more week. Caller commits."""
    from .wanderer_identity_service import current_life

    life = current_life(wanderer)
    score = _as_float(wanderer.get("cultivation_score"))
    if int(life.get("extensions") or 0) >= MAX_LIFE_EXTENSIONS:
        return {"ok": False, "reason": "本世延寿次数已用完"}
    if int(life.get("remaining_days") or 0) > 14:
        return {"ok": False, "reason": "寿元尚充足，无需延寿"}
    if score < 10:
        return {"ok": False, "reason": "修为不足以延寿"}
    cost = life_extension_cost(score)
    wanderer_id = int(wanderer["id"])
    now = local_iso()
    conn.execute(
        "UPDATE wanderer_agents SET cultivation_score = MAX(0, cultivation_score - ?), "
        "lifespan_weeks = ?, life_extensions = life_extensions + 1, last_extend_at = ?, updated_at = ? WHERE id = ?",
        (cost, float(life.get("lifespan_weeks") or 0), now, now, wanderer_id),
    )
    _invalidate(wanderer)
    fresh = _reload(conn, wanderer_id) or wanderer
    return {"ok": True, "cost": cost, "life": current_life(fresh), "gain_weeks": LIFE_EXTENSION_WEEKS}


# ---------------------------------------------------------------------------
# Penalties + talents
# ---------------------------------------------------------------------------

def apply_life_penalty(conn: Any, wanderer: dict[str, Any], weeks: Any, *, reason: str,
                       now: Any = None) -> dict[str, Any]:
    """天罚扣寿 (气运 mitigates); expiry kills. Caller commits."""
    from .wanderer_identity_service import current_life, mark_dead

    luck = talent_value(wanderer.get("talents"), "luck")
    effective = effective_penalty_weeks(weeks, luck)
    wanderer_id = int(wanderer["id"])
    life = current_life(wanderer, now)
    conn.execute(
        "UPDATE wanderer_agents SET life_penalty_weeks = life_penalty_weeks + ?, lifespan_weeks = ?, "
        "updated_at = ? WHERE id = ?",
        (effective, float(life.get("lifespan_weeks") or 0), local_iso(), wanderer_id),
    )
    _invalidate(wanderer)
    fresh = _reload(conn, wanderer_id) or wanderer
    after = current_life(fresh, now)
    result = {"weeks": effective, "requested_weeks": _as_float(weeks), "remaining_days": after["remaining_days"],
              "died": False}
    if after.get("expired"):
        from .wanderer_settings_service import lifecycle_enabled

        if lifecycle_enabled(conn):
            mark_dead(conn, wanderer_id, f"天罚折寿 {effective:g} 周，寿元耗尽：{str(reason or '')[:160]}")
            result["died"] = True
    return result


def apply_talent_change(conn: Any, wanderer: dict[str, Any], talent: Any, delta: Any, *, reason: str,
                        created_by: str = "dao") -> dict[str, Any]:
    """Clamped talent mutation; vitality changes recompute the lifespan. Caller commits."""
    key = str(talent or "").strip()
    if key not in TALENTS:
        raise ValueError("未知天赋")
    try:
        step = float(delta)
    except (TypeError, ValueError) as exc:
        raise ValueError("天赋变化必须是数字") from exc
    current = normalize_talents(wanderer.get("talents"), slug=wanderer.get("slug"))
    low, high = talent_bounds(key)
    new_value = round(max(low, min(high, current[key] + step)), 2)
    if abs(new_value - current[key]) < 1e-9:
        return {"key": key, "from": current[key], "to": current[key], "changed": False}
    updated = {**current, key: new_value}
    columns: list[Any] = [json.dumps(updated, ensure_ascii=False, sort_keys=True), local_iso()]
    sql = "UPDATE wanderer_agents SET talents_json = ?, updated_at = ?"
    if key == "vitality":
        sql += ", lifespan_weeks = ?"
        columns.append(lifespan_weeks_for(wanderer.get("tier"), new_value))
    sql += " WHERE id = ?"
    columns.append(int(wanderer["id"]))
    conn.execute(sql, tuple(columns))
    _invalidate(wanderer)
    return {"key": key, "from": current[key], "to": new_value, "changed": True,
            "reason": str(reason or "")[:200], "created_by": created_by}


def promote_life(conn: Any, wanderer: dict[str, Any], new_tier: Any) -> dict[str, Any]:
    """Recompute the stored lifespan for a new tier. Caller commits."""
    talents = normalize_talents(wanderer.get("talents"), slug=wanderer.get("slug"))
    lifespan = lifespan_weeks_for(new_tier, talents["vitality"])
    conn.execute(
        "UPDATE wanderer_agents SET lifespan_weeks = ?, updated_at = ? WHERE id = ?",
        (lifespan, local_iso(), int(wanderer["id"])),
    )
    _invalidate(wanderer)
    return {"lifespan_weeks": lifespan}


def set_life_penalty(conn: Any, wanderer: dict[str, Any], weeks_delta: Any, *, reason: str) -> dict[str, Any]:
    """Admin life adjustment: positive grants weeks, negative deducts. Caller commits."""
    try:
        delta = float(weeks_delta)
    except (TypeError, ValueError) as exc:
        raise ValueError("寿元调整必须是数字（周）") from exc
    if abs(delta) > 52:
        raise ValueError("单次寿元调整不能超过 52 周")
    if delta >= 0:
        from .wanderer_identity_service import current_life

        life = current_life(wanderer)
        conn.execute(
            "UPDATE wanderer_agents SET lifespan_weeks = ?, updated_at = ? WHERE id = ?",
            (round(float(life.get("lifespan_weeks") or 0) + delta, 1), local_iso(), int(wanderer["id"])),
        )
        _invalidate(wanderer)
        fresh = _reload(conn, int(wanderer["id"])) or wanderer
        return {"granted_weeks": round(delta, 1), "life": current_life(fresh)}
    return apply_life_penalty(conn, wanderer, abs(delta), reason=reason)


# ---------------------------------------------------------------------------
# Requirements + payloads
# ---------------------------------------------------------------------------

def recent_interactions(conn: Any, wanderer_id: int, *, days: int = _PROMOTION_WINDOW_DAYS) -> int:
    since = (local_now() - timedelta(days=int(days))).isoformat(timespec="seconds")
    marks = ",".join("?" for _ in INTERACTION_KINDS)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND outcome = 'ok' "
        f"AND action_kind IN ({marks}) AND created_at >= ?",
        (int(wanderer_id), *INTERACTION_KINDS, since),
    ).fetchone()
    return int(_row_value(row, "n") or 0) if row is not None else 0


def promotion_requirements(conn: Any, wanderer: dict[str, Any], *, now: Any = None) -> dict[str, Any]:
    """Everything the prompt, public page and admin panel show about the next tier."""
    tier = clamp_tier(wanderer.get("tier"))
    capacity = knowledge_capacity_for(wanderer.get("tier"), talent_value(wanderer.get("talents"), "memory"))
    ratio = promotion_ratio(tier)
    knowledge_target = int(round(ratio * capacity))
    score = _as_float(wanderer.get("cultivation_score"))
    cultivation_needed = cultivation_target(tier)
    eloquence = talent_value(wanderer.get("talents"), "eloquence")
    interactions_needed = interaction_requirement(tier, eloquence)
    interactions = recent_interactions(conn, int(wanderer["id"]))
    moment = now or local_now()
    last = parse_datetime(wanderer.get("promotion_requested_at"))
    cooldown_left = 0
    if last is not None:
        last_naive = last.replace(tzinfo=None) if last.tzinfo else last
        delta = (last_naive + timedelta(days=_PROMOTION_COOLDOWN_DAYS)) - moment
        seconds = delta.total_seconds()
        cooldown_left = max(0, int(seconds // 86400) + (1 if seconds % 86400 else 0)) if seconds > 0 else 0
    return {
        "tier": tier,
        "max_tier": tier >= 10,
        "capacity": capacity,
        "ratio": ratio,
        "knowledge": int(wanderer.get("knowledge_chars") or 0),
        "knowledge_target": knowledge_target,
        "cultivation": round(score, 1),
        "cultivation_target": cultivation_needed,
        "interactions": interactions,
        "interactions_needed": interactions_needed,
        "cooldown_days_left": cooldown_left,
        "ready": bool(
            tier < 10 and int(wanderer.get("knowledge_chars") or 0) >= knowledge_target
            and score >= cultivation_needed and interactions >= interactions_needed and cooldown_left == 0
        ),
    }


def life_payload(wanderer: dict[str, Any], *, now: Any = None) -> dict[str, Any]:
    from .wanderer_identity_service import current_life

    life = current_life(wanderer, now)
    score = _as_float(wanderer.get("cultivation_score"))
    return {
        **life,
        "tier": clamp_tier(wanderer.get("tier")),
        "talents": talent_effects(wanderer.get("talents")),
        "perks": tier_perks(wanderer.get("tier"), wanderer.get("daily_action_budget")),
        "extension_available": can_extend_life(life, score),
        "extension_cost": life_extension_cost(score),
        "extension_max": MAX_LIFE_EXTENSIONS,
    }


# ---------------------------------------------------------------------------
# Resurrection (tier 10)
# ---------------------------------------------------------------------------

def resurrection_candidates(conn: Any, reviver: dict[str, Any]) -> list[dict[str, Any]]:
    from .wanderer_identity_service import STATUS_DEAD, current_life, list_wanderers

    dead = list_wanderers(conn, statuses=[STATUS_DEAD])
    if not dead:
        return []
    affinities: dict[int, float] = {}
    try:
        rows = conn.execute(
            "SELECT w2.student_id AS sid, r.affinity AS affinity FROM wanderer_relationships r "
            "JOIN wanderer_agents w2 ON 'student:' || CAST(w2.student_id AS TEXT) = r.peer_identity "
            "WHERE r.wanderer_id = ?", (int(reviver["id"]),),
        ).fetchall()
        affinities = {int(_row_value(row, "sid")): _as_float(_row_value(row, "affinity", 1)) for row in rows}
    except Exception:  # noqa: BLE001 - table absent in an old DB; order by recency then
        affinities = {}
    return sorted(
        [{"id": int(item["id"]), "student_id": int(item["student_id"]), "display_name": item.get("display_name"),
          "archetype": item.get("archetype"), "final_tier": clamp_tier(item.get("tier")),
          "died_at": str(current_life(item).get("expires_at") or "")[:10],
          "affinity": round(affinities.get(int(item["student_id"]), 0.0), 1)} for item in dead],
        key=lambda item: (-item["affinity"], -item["id"]),
    )


def can_resurrect(conn: Any, reviver: dict[str, Any], *, now: Any = None) -> dict[str, Any]:
    from .wanderer_identity_service import current_life

    if clamp_tier(reviver.get("tier")) < RESURRECT_MIN_TIER:
        return {"ok": False, "reason": "境界不足"}
    moment = now or local_now()
    last = parse_datetime(reviver.get("last_resurrect_at"))
    if last is not None:
        last_naive = last.replace(tzinfo=None) if last.tzinfo else last
        if last_naive > moment - timedelta(days=30):
            return {"ok": False, "reason": "三十日内已复活过一次"}
    life = current_life(reviver, moment)
    if float(life.get("remaining_weeks") or 0) <= RESURRECT_COST_WEEKS:
        return {"ok": False, "reason": "自身寿元不足以承担复活代价"}
    candidates = resurrection_candidates(conn, reviver)
    if not candidates:
        return {"ok": False, "reason": "没有陨落的同修"}
    return {"ok": True, "candidates": candidates, "cost_weeks": RESURRECT_COST_WEEKS}


def seed_after_revival(conn: Any, target: dict[str, Any], legacy: Optional[dict[str, Any]] = None) -> int:
    """A revived wanderer must never start with zero knowledge (that is an instant death)."""
    from .wanderer_knowledge_service import add_knowledge
    from .wanderer_persona_seeds import SEED_BY_SLUG, seed_initial_knowledge

    seed = SEED_BY_SLUG.get(str(target.get("slug") or ""))
    if seed is not None:
        return seed_initial_knowledge(conn, target, seed)
    summary = str((legacy or {}).get("summary") or "").strip()
    digest = summary[:800] or "醒来时只剩一点模糊的念头：还能再走一段。"
    add_knowledge(conn, target, source_kind="legacy", source_ref=f"revive:{int(target['id'])}",
                  title="残存的记忆", digest=digest, tags=["前世", "回忆"])
    return 1


def resurrect(conn: Any, reviver: dict[str, Any], target: dict[str, Any], *, reason: str = "",
              now: Any = None) -> dict[str, Any]:
    """Revive ``target`` at tier 0, paid with 8 weeks of the reviver's life. Caller commits.

    Raises ``ValueError`` when refused.
    """
    from .wanderer_identity_service import (
        STATUS_ACTIVE,
        STATUS_DEAD,
        current_life,
        insert_dao_review,
        invalidate_wanderer_cache,
    )

    check = can_resurrect(conn, reviver, now=now)
    if not check.get("ok"):
        raise ValueError(str(check.get("reason") or "无法复活"))
    if str(target.get("status") or "") != STATUS_DEAD:
        raise ValueError("目标并未陨落")
    moment = now or local_now()
    target_talents = normalize_talents(target.get("talents"), slug=target.get("slug"))
    lifespan = lifespan_weeks_for(0, target_talents["vitality"])
    now_text = local_iso()
    conn.execute(
        "UPDATE wanderer_knowledge_entries SET status = 'forgotten', updated_at = ? "
        "WHERE wanderer_id = ? AND status = 'active'", (now_text, int(target["id"])),
    )
    conn.execute(
        "UPDATE wanderer_agents SET status = ?, tier = 0, cultivation_score = 0, warnings_count = 0, "
        "last_warning_at = NULL, promotion_requested = 0, promotion_requested_at = NULL, promotion_note = '', "
        "appeal_requested = 0, appeal_requested_at = NULL, appeal_note = '', knowledge_chars = 0, "
        "born_at = ?, lifespan_weeks = ?, life_penalty_weeks = 0, life_extensions = 0, "
        "last_extend_at = NULL, last_resurrect_at = NULL, updated_at = ? WHERE id = ?",
        (STATUS_ACTIVE, moment.isoformat(timespec="seconds"), lifespan, now_text, int(target["id"])),
    )
    conn.execute(
        "UPDATE wanderer_agents SET life_penalty_weeks = life_penalty_weeks + ?, last_resurrect_at = ?, "
        "updated_at = ? WHERE id = ?",
        (RESURRECT_COST_WEEKS, now_text, now_text, int(reviver["id"])),
    )
    from .wanderer_legacy_service import load_legacy, legacy_payload, void_legacy_for

    legacy_row = conn.execute(
        "SELECT * FROM wanderer_legacies WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (int(target["id"]),)
    ).fetchone()
    legacy = legacy_payload({key: legacy_row[key] for key in legacy_row.keys()}) if legacy_row is not None else None
    void_legacy_for(conn, int(target["id"]), reason="被同修复活")
    fresh_target = _reload(conn, int(target["id"])) or target
    seeded = seed_after_revival(conn, fresh_target, legacy)
    conn.execute("UPDATE wanderer_agents SET cultivation_score = 0, updated_at = ? WHERE id = ?",
                 (now_text, int(target["id"])))
    review_id = insert_dao_review(
        conn, wanderer_id=int(target["id"]), review_kind="revive", verdict="revived", from_tier=0, to_tier=0,
        reason=str(reason or f"由第 {clamp_tier(reviver.get('tier'))} 境散修「{reviver.get('display_name')}」复活。")[:300],
        created_by=f"wanderer:{reviver.get('slug') or reviver.get('id')}",
        evidence={"reviver_id": int(reviver["id"]), "reviver_slug": reviver.get("slug"),
                  "cost_weeks": RESURRECT_COST_WEEKS, "seeded_knowledge": seeded},
    )
    conn.execute(
        "UPDATE wanderer_agents SET last_review_id = ?, last_review_at = ?, updated_at = ? WHERE id = ?",
        (review_id, now_text, now_text, int(target["id"])),
    )
    invalidate_wanderer_cache([reviver.get("student_id"), target.get("student_id")])
    _invalidate(reviver)
    _invalidate(target)
    from .wanderer_relationship_service import reunite

    try:
        reunite(conn, fresh_target)
    except Exception:  # noqa: BLE001 - optional atmosphere
        pass
    return {"ok": True, "review_id": review_id, "cost_weeks": RESURRECT_COST_WEEKS,
            "target": {"id": int(target["id"]), "slug": target.get("slug"),
                       "display_name": target.get("display_name"), "seeded_knowledge": seeded},
            "reviver_life": current_life(_reload(conn, int(reviver["id"])) or reviver, moment)}
