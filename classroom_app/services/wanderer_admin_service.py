"""管理端「散修管理」的读写服务（docs/wanderer-ai-free-agents-2026-10-25.md §6）。

只做聚合与受控写入：身份、知识、裁决的真源仍在 Phase A 的
``wanderer_identity_service`` / ``wanderer_knowledge_service`` /
``wanderer_settings_service``。SQL 只用 ``?`` 占位与通用函数，SQLite 与
PostgreSQL 同跑；调用方负责 commit。

错误约定：散修不存在 → ``LookupError``；参数非法 → ``ValueError``。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta
from typing import Any, Optional

from ..db.schema_wanderers import ensure_wanderer_schema
from ..time_utils import local_iso, local_now
from .cultivation_display_service import emblem_for_level, invalidate_emblem_cache
from .wanderer_avatar_service import (
    SILHOUETTES,
    SOURCE_LABELS as AVATAR_SOURCE_LABELS,
    avatar_source,
    default_avatar_file,
    normalize_silhouette,
    pick_silhouette,
)
from .wanderer_identity_service import (
    MAX_TIER,
    ORIGIN_DAO,
    STATUS_ACTIVE,
    STATUS_DEAD,
    STATUS_PAUSED,
    clamp_tier,
    insert_dao_review,
    invalidate_wanderer_cache,
    list_wanderers,
    load_wanderer,
    load_wanderer_by_student_id,
    mark_dead,
    tier_level,
)
from .wanderer_knowledge_service import forget_all, knowledge_summary
from .wanderer_settings_service import (
    SETTING_DAILY_AI_CALL_CAP,
    SETTING_ENABLED,
    GENESIS_INT_SETTINGS,
    SETTING_GENESIS_ENABLED,
    SETTING_LAST_MANUAL_GENESIS_SUMMARY,
    SETTING_LAST_MANUAL_REVIEW_SUMMARY,
    SETTING_LAST_MANUAL_TICK_SUMMARY,
    SETTING_LIFECYCLE_ENABLED,
    daily_ai_calls,
    daily_cap,
    genesis_settings,
    is_wanderer_runtime_enabled,
    lifecycle_enabled,
    wanderer_runtime_setting_get,
    wanderer_runtime_setting_set,
)

STATUS_LABELS = {STATUS_ACTIVE: "活跃", STATUS_PAUSED: "暂停", STATUS_DEAD: "陨落"}
ACTION_LABELS = {
    "browse_post": "浏览帖子", "like_post": "点赞帖子", "like_comment": "点赞评论", "comment": "评论",
    "reply": "回复", "dm_reply": "回复私信", "classroom_visit": "旁听课堂", "classroom_message": "课堂留言",
    "knowledge_read": "研读固化", "knowledge_forget": "遗忘知识", "request_promotion": "申请升阶",
    "reflect": "反思", "skip": "跳过", "write_post": "发帖",
    "social_friend": "好友往来", "social_message": "聊天发言",
    "experience_learn": "交流学习",
    "extend_life": "延寿", "appeal": "天道抗辩", "resurrect": "复活同修",
}
REVIEW_KIND_LABELS = {
    "promotion": "升阶", "discipline": "纪律", "daily_audit": "日审", "mention_reply": "@天道",
    "admin_override": "管理员调整", "revive": "复活", "death": "陨落", "genesis": "天道造人",
    "penalty": "折寿", "talent": "天赋调整", "appeal": "抗辩", "life": "寿元",
}
VERDICT_LABELS = {
    "approved": "批准", "rejected": "驳回", "warning": "警告", "demoted": "降阶",
    "noted": "记录", "died": "陨落", "revived": "复活", "created": "造人",
    "adjusted": "调整", "deducted": "扣寿", "upheld": "成立", "rejected_appeal": "驳回",
}
ORIGIN_LABELS = {"seed": "种子", "dao": "天道所造", "reincarnated": "转世"}

SETTING_TICK_BATCH_SIZE = "tick_batch_size"
SETTING_CLASSROOM_VISITS = "classroom_visits_enabled"
SETTING_DAO_REVIEW_HOUR = "dao_review_hour"
SETTING_LAST_TICK_AT = "last_tick_at"
SETTING_LAST_DAO_REVIEW_AT = "last_dao_review_at"
DEFAULT_TICK_BATCH_SIZE = 6
DEFAULT_DAO_REVIEW_HOUR = "03:30"
DAILY_AI_CALL_CAP_RANGE = (0, 5000)
TICK_BATCH_SIZE_RANGE = (1, 20)
ACTIVITY_LEVEL_RANGE = (1, 5)
DAILY_ACTION_BUDGET_RANGE = (0, 100)
PERSONA_PROMPT_MAX_CHARS = 8000
JSON_FIELD_MAX_CHARS = 8000
TIER_REASON_MAX_CHARS = 300
STATS_SERIES_DAYS = 7
DEFAULT_PAGE_LIMIT = 30
DEFAULT_KNOWLEDGE_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
DETAIL_RECENT_REVIEWS = 5
DETAIL_RECENT_ACTIONS = 10
_HOUR_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TRUTHY = {"1", "true", "yes", "on"}
_JSON_PATCH_FIELDS = {
    "schedule_json": dict,
    "reply_policy_json": dict,
    "behaviors_json": dict,
    "interests_json": list,
}
_AVATAR_PATCH_FIELD = "avatar_silhouette"
_PATCH_FIELDS = frozenset({"persona_prompt", "activity_level", "daily_action_budget", _AVATAR_PATCH_FIELD,
                           *_JSON_PATCH_FIELDS})
# ``avatar_file`` marker meaning "admin chose a silhouette": not a file name, so
# the illustration is skipped and seeding never refills it (design §11).
AVATAR_FILE_OPT_OUT = "-"
_SETTING_FIELDS = frozenset({
    SETTING_ENABLED, SETTING_DAILY_AI_CALL_CAP, SETTING_TICK_BATCH_SIZE, SETTING_CLASSROOM_VISITS,
    SETTING_DAO_REVIEW_HOUR, SETTING_GENESIS_ENABLED, SETTING_LIFECYCLE_ENABLED, *GENESIS_INT_SETTINGS,
})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except AttributeError:
        return dict(row)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _loads(raw: Any, kind: type) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except ValueError:
        value = None
    return value if isinstance(value, kind) else kind()


def _today_start() -> str:
    return f"{local_now().date().isoformat()}T00:00:00"


def _actor_label(actor: Optional[dict[str, Any]]) -> str:
    return f"teacher:{_as_int((actor or {}).get('id'))}"


def require_wanderer(conn: Any, wanderer_id: Any) -> dict[str, Any]:
    number = _as_int(wanderer_id, -1)
    wanderer = load_wanderer(conn, number) if number > 0 else None
    if wanderer is None:
        raise LookupError("散修不存在")
    return wanderer


def _invalidate(wanderer: dict[str, Any]) -> None:
    invalidate_wanderer_cache([wanderer["student_id"]])
    invalidate_emblem_cache([wanderer["student_id"]])


def _count(conn: Any, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return _as_int(_row_dict(row).get("n")) if row is not None else 0


def avatar_url(student_id: Any) -> str:
    return f"/api/profile/avatar?role=student&user_id={_as_int(student_id)}"


def _versioned_avatar_url(wanderer: dict[str, Any]) -> str:
    """Avatar URL with a token of the avatar fields, so an admin change bypasses the 1 h HTTP cache."""
    token = "|".join(str(wanderer.get(key) or "") for key in ("avatar_file", "avatar_silhouette", "avatar_file_hash"))
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:8]
    return f"{avatar_url(wanderer['student_id'])}&v={digest}"


def homepage_url(slug: Any) -> str:
    from .blog_persona_service import wanderer_homepage_url

    return wanderer_homepage_url(slug)


def tier_payload(tier: Any, score: Any = None) -> dict[str, Any]:
    level = tier_level(tier)
    try:
        shown = float(score) if score is not None else None
    except (TypeError, ValueError):
        shown = None
    return {
        "tier": clamp_tier(tier),
        "tier_name": str(level.get("name") or ""),
        "tier_key": str(level.get("key") or ""),
        "emblem": emblem_for_level(level, shown),
    }


def _env_enabled() -> bool:
    return str(os.getenv("WANDERER_ENABLED", "false")).strip().lower() in _TRUTHY


def _runtime_enabled_flag(conn: Any) -> bool:
    return str(wanderer_runtime_setting_get(conn, SETTING_ENABLED, "1")).strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# action / review / knowledge serialization
# ---------------------------------------------------------------------------

def _action_href(item: dict[str, Any], detail: dict[str, Any]) -> str:
    target_type = str(item.get("target_type") or "")
    target_id = str(item.get("target_id") or "")
    post_id = str(detail.get("post_id") or (target_id if target_type in {"blog_post", "post"} else ""))
    if post_id.isdigit():
        return f"/blog?post={post_id}"
    offering_id = _as_int(item.get("class_offering_id"))
    return f"/classroom/{offering_id}" if offering_id > 0 else ""


def _serialize_action(row: Any) -> dict[str, Any]:
    item = _row_dict(row)
    kind = str(item.get("action_kind") or "")
    detail = _loads(item.get("detail_json"), dict)
    return {
        "id": _as_int(item.get("id")),
        "wanderer_id": _as_int(item.get("wanderer_id")),
        "action_kind": kind,
        "action_label": ACTION_LABELS.get(kind, kind),
        "target_type": str(item.get("target_type") or ""),
        "target_id": str(item.get("target_id") or ""),
        "class_offering_id": _as_int(item.get("class_offering_id")) or None,
        "content_preview": str(item.get("content_preview") or ""),
        "ai_calls": _as_int(item.get("ai_calls")),
        "mood": str(item.get("mood") or ""),
        "outcome": str(item.get("outcome") or "ok"),
        "created_at": str(item.get("created_at") or ""),
        "detail": detail,
        "href": _action_href(item, detail),
    }


def _optional_int(value: Any) -> Optional[int]:
    return None if value is None else _as_int(value)


def _serialize_review(row: Any) -> dict[str, Any]:
    item = _row_dict(row)
    kind = str(item.get("review_kind") or "")
    verdict = str(item.get("verdict") or "")
    return {
        "id": _as_int(item.get("id")),
        "wanderer_id": _optional_int(item.get("wanderer_id")),
        "review_kind": kind,
        "review_kind_label": REVIEW_KIND_LABELS.get(kind, kind),
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict),
        "from_tier": _optional_int(item.get("from_tier")),
        "to_tier": _optional_int(item.get("to_tier")),
        "reason": str(item.get("reason") or ""),
        "advice": str(item.get("advice") or ""),
        "created_by": str(item.get("created_by") or ""),
        "acknowledged_at": item.get("acknowledged_at"),
        "created_at": str(item.get("created_at") or ""),
        **_review_extras(kind, item),
    }


def _review_extras(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    """Genesis rows expose the activity snapshot and the created persona's summary (admin only)."""
    if kind != "genesis":
        return {}
    evidence = _loads(item.get("evidence_json"), dict)
    return {"activity": evidence.get("activity") if isinstance(evidence.get("activity"), dict) else {},
            "seed_summary": evidence.get("seed_summary") if isinstance(evidence.get("seed_summary"), dict) else {}}


def _serialize_knowledge(row: Any) -> dict[str, Any]:
    item = _row_dict(row)
    return {
        "id": _as_int(item.get("id")),
        "source_kind": str(item.get("source_kind") or ""),
        "source_ref": str(item.get("source_ref") or ""),
        "title": str(item.get("title") or ""),
        "digest": str(item.get("digest") or ""),
        "chars": _as_int(item.get("chars")),
        "tags": _loads(item.get("tags_json"), list),
        "status": str(item.get("status") or ""),
        "use_count": _as_int(item.get("use_count")),
        "created_at": str(item.get("created_at") or ""),
    }


# ---------------------------------------------------------------------------
# overview + cards
# ---------------------------------------------------------------------------

def _status_counts(conn: Any) -> tuple[dict[str, int], int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n, SUM(promotion_requested) AS promo FROM wanderer_agents GROUP BY status"
    ).fetchall()
    counts = {STATUS_ACTIVE: 0, STATUS_PAUSED: 0, STATUS_DEAD: 0}
    promotion = 0
    for row in rows:
        item = _row_dict(row)
        status = str(item.get("status") or "")
        counts = {**counts, status: _as_int(item.get("n"))}
        if status != STATUS_DEAD:
            promotion += _as_int(item.get("promo"))
    return counts, promotion


def _latest_activity(conn: Any) -> dict[str, Any]:
    row = conn.execute(
        "SELECT evidence_json FROM wanderer_dao_reviews WHERE review_kind = ? ORDER BY id DESC LIMIT 1",
        ("daily_audit",),
    ).fetchone()
    activity = _loads(_row_dict(row).get("evidence_json"), dict).get("activity") if row is not None else None
    return activity if isinstance(activity, dict) else {}


def build_wanderer_overview(conn: Any) -> dict[str, Any]:
    ensure_wanderer_schema(conn)
    counts, promotion = _status_counts(conn)
    dao_created = _count(conn, "SELECT COUNT(*) AS n FROM wanderer_agents WHERE origin = ?", ("dao",))
    warnings_today = _count(
        conn,
        "SELECT COUNT(*) AS n FROM wanderer_dao_reviews WHERE verdict = ? AND created_at >= ?",
        ("warning", _today_start()),
    )
    from .wanderer_identity_service import current_life

    life_urgent = sum(1 for item in list_wanderers(conn, statuses=[STATUS_ACTIVE])
                      if current_life(item).get("urgent"))
    return {
        "env_enabled": _env_enabled(),
        "runtime_enabled": _runtime_enabled_flag(conn),
        "running": is_wanderer_runtime_enabled(conn),
        "ai_calls_today": daily_ai_calls(conn),
        "daily_ai_call_cap": daily_cap(conn),
        "counts": {**counts, "total": sum(counts.values()), "dao_created": dao_created},
        "latest_activity": _latest_activity(conn),
        "promotion_requested": promotion,
        "warnings_today": warnings_today,
        "life_urgent": life_urgent,
        "last_tick_at": wanderer_runtime_setting_get(conn, SETTING_LAST_TICK_AT, "") or "",
        "last_dao_review_at": wanderer_runtime_setting_get(conn, SETTING_LAST_DAO_REVIEW_AT, "") or "",
        "last_manual_tick": _loads(wanderer_runtime_setting_get(conn, SETTING_LAST_MANUAL_TICK_SUMMARY), dict),
        "last_manual_review": _loads(wanderer_runtime_setting_get(conn, SETTING_LAST_MANUAL_REVIEW_SUMMARY), dict),
        "last_manual_genesis": _loads(wanderer_runtime_setting_get(conn, SETTING_LAST_MANUAL_GENESIS_SUMMARY), dict),
    }


def _today_action_counts(conn: Any) -> dict[int, int]:
    rows = conn.execute(
        "SELECT wanderer_id, COUNT(*) AS n FROM wanderer_action_log "
        "WHERE created_at >= ? AND action_kind <> ? GROUP BY wanderer_id",
        (_today_start(), "skip"),
    ).fetchall()
    pairs = [_row_dict(row) for row in rows]
    return {_as_int(item.get("wanderer_id")): _as_int(item.get("n")) for item in pairs}


def _latest_actions(conn: Any) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        "SELECT l.* FROM wanderer_action_log l "
        "JOIN (SELECT wanderer_id, MAX(id) AS mid FROM wanderer_action_log GROUP BY wanderer_id) m "
        "ON l.id = m.mid"
    ).fetchall()
    latest = [_serialize_action(row) for row in rows]
    return {item["wanderer_id"]: item for item in latest}


def _card(wanderer: dict[str, Any], today_count: int, last_action: Optional[dict[str, Any]]) -> dict[str, Any]:
    from .wanderer_identity_service import current_life

    status = str(wanderer.get("status") or STATUS_ACTIVE)
    action = last_action or {}
    origin = str(wanderer.get("origin") or "seed")
    life = current_life(wanderer)
    return {
        "id": int(wanderer["id"]),
        "student_id": int(wanderer["student_id"]),
        "slug": str(wanderer.get("slug") or ""),
        "display_name": str(wanderer.get("display_name") or ""),
        "archetype": str(wanderer.get("archetype") or ""),
        **tier_payload(wanderer.get("tier"), wanderer.get("cultivation_score")),
        "cultivation_score": round(float(wanderer.get("cultivation_score") or 0), 1),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "today_actions": today_count,
        "daily_action_budget": int(wanderer.get("daily_action_budget") or 0),
        "warnings_count": int(wanderer.get("warnings_count") or 0),
        "promotion_requested": bool(wanderer.get("promotion_requested")),
        "appeal_requested": bool(wanderer.get("appeal_requested")),
        "life_weeks": life["remaining_weeks"],
        "life_days": life["remaining_days"],
        "life_granted_weeks": life["granted_weeks"],
        "life_urgent": bool(life["urgent"] and status == STATUS_ACTIVE),
        "talents": dict(wanderer.get("talents") or {}),
        "last_active_at": wanderer.get("last_active_at") or "",
        "last_action_preview": action.get("content_preview") or action.get("action_label") or "",
        "last_action_at": action.get("created_at", ""),
        "avatar_url": _versioned_avatar_url(wanderer),
        "origin": origin,
        "origin_label": ORIGIN_LABELS.get(origin, origin),
        "homepage_url": homepage_url(wanderer.get("slug")),
        "created_at": str(wanderer.get("created_at") or ""),
    }


def list_wanderer_cards(conn: Any) -> list[dict[str, Any]]:
    wanderers = list_wanderers(conn)
    today = _today_action_counts(conn)
    latest = _latest_actions(conn)
    return [_card(item, today.get(int(item["id"]), 0), latest.get(int(item["id"]))) for item in wanderers]


def _card_for(conn: Any, wanderer_id: int) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    today = _count(
        conn,
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND created_at >= ? AND action_kind <> ?",
        (int(wanderer_id), _today_start(), "skip"),
    )
    row = conn.execute(
        "SELECT * FROM wanderer_action_log WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (int(wanderer_id),)
    ).fetchone()
    return _card(wanderer, today, _serialize_action(row) if row is not None else None)


# ---------------------------------------------------------------------------
# detail + paged lists
# ---------------------------------------------------------------------------

def _action_totals(conn: Any, wanderer_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT action_kind, COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? GROUP BY action_kind",
        (wanderer_id,),
    ).fetchall()
    pairs = [_row_dict(row) for row in rows]
    return {str(item.get("action_kind")): _as_int(item.get("n")) for item in pairs}


def _daily_series(conn: Any, wanderer_id: int) -> list[dict[str, Any]]:
    today = local_now().date()
    days = [today - timedelta(days=offset) for offset in range(STATS_SERIES_DAYS - 1, -1, -1)]
    rows = conn.execute(
        "SELECT created_at FROM wanderer_action_log WHERE wanderer_id = ? AND created_at >= ? AND action_kind <> ?",
        (wanderer_id, f"{days[0].isoformat()}T00:00:00", "skip"),
    ).fetchall()
    tally: dict[str, int] = {}
    for row in rows:
        key = str(_row_dict(row).get("created_at") or "")[:10]
        tally[key] = tally.get(key, 0) + 1
    return [{"date": day.isoformat(), "label": f"{day.month}/{day.day}", "count": tally.get(day.isoformat(), 0)}
            for day in days]


def _recent(conn: Any, table: str, wanderer_id: int, limit: int) -> list[Any]:
    return conn.execute(
        f"SELECT * FROM {table} WHERE wanderer_id = ? ORDER BY id DESC LIMIT ?", (wanderer_id, int(limit))
    ).fetchall()


def _profile_payload(wanderer: dict[str, Any]) -> dict[str, Any]:
    return {
        "persona_prompt": str(wanderer.get("persona_prompt") or ""),
        "public_intro": str(wanderer.get("public_intro") or ""),
        "activity_level": int(wanderer.get("activity_level") or 0),
        "daily_action_budget": int(wanderer.get("daily_action_budget") or 0),
        "schedule": dict(wanderer.get("schedule") or {}),
        "reply_policy": dict(wanderer.get("reply_policy") or {}),
        "behaviors": dict(wanderer.get("behaviors") or {}),
        "interests": list(wanderer.get("interests") or []),
        "promotion_note": str(wanderer.get("promotion_note") or ""),
        "talents": dict(wanderer.get("talents") or {}),
        "appeal_note": str(wanderer.get("appeal_note") or ""),
    }


def _avatar_payload(wanderer: dict[str, Any]) -> dict[str, Any]:
    source = avatar_source(wanderer)
    return {
        "source": source,
        "source_label": AVATAR_SOURCE_LABELS.get(source, source),
        "silhouette": str(wanderer.get("avatar_silhouette") or ""),
        "has_illustration": bool(default_avatar_file(wanderer.get("slug"))),
        "suggested_silhouette": pick_silhouette(wanderer),
        "silhouettes": [{"key": key, "label": label} for key, label in SILHOUETTES.items()],
    }


def _avatar_columns(wanderer: dict[str, Any], value: Any) -> dict[str, Any]:
    """A silhouette key overrides the illustration; '' restores the default for this wanderer."""
    key = normalize_silhouette(value)
    if key:
        return {"avatar_silhouette": key, "avatar_file": AVATAR_FILE_OPT_OUT}
    default_silhouette = pick_silhouette(wanderer) if str(wanderer.get("origin") or "") == ORIGIN_DAO else ""
    return {"avatar_silhouette": default_silhouette, "avatar_file": default_avatar_file(wanderer.get("slug"))}


def _social_counts(conn: Any, wanderer: dict[str, Any]) -> dict[str, int]:
    """好友/群/今日发言 (social doc §10); zeros when the social tables are unavailable."""
    from ..db.savepoint import best_effort
    from .wanderer_social_friends import social_counts

    counts = {"friends": 0, "groups": 0, "messages_today": 0, "pending_requests": 0}
    with best_effort(conn, "wanderer_social_counts", log_prefix="[WANDERER]"):
        counts = social_counts(conn, int(wanderer["student_id"]))
    return counts


def _legacy_rows(conn: Any, wanderer_id: int, limit: int = 5) -> list[dict[str, Any]]:
    from .wanderer_legacy_service import legacy_payload

    rows = conn.execute(
        "SELECT * FROM wanderer_legacies WHERE wanderer_id = ? ORDER BY id DESC LIMIT ?",
        (int(wanderer_id), int(limit)),
    ).fetchall()
    return [legacy_payload({key: row[key] for key in row.keys()}, detail=True) for row in rows]


def build_wanderer_detail(conn: Any, wanderer_id: Any) -> dict[str, Any]:
    from .wanderer_learning_service import admin_learning_summary
    from .wanderer_life_service import life_payload, promotion_requirements
    from .wanderer_public_service import web_search_usage
    from .wanderer_relationship_service import list_relationships
    wanderer = require_wanderer(conn, wanderer_id)
    number = int(wanderer["id"])
    totals = _action_totals(conn, number)
    by_kind = sorted(totals.items(), key=lambda pair: -pair[1])
    return {
        "card": _card_for(conn, number),
        "stats": {
            "total_actions": sum(totals.values()),
            "by_kind": [{"kind": kind, "label": ACTION_LABELS.get(kind, kind), "count": count} for kind, count in by_kind],
            "daily": _daily_series(conn, number),
            "cached": dict(wanderer.get("stats") or {}),
            "social": _social_counts(conn, wanderer),
        },
        "knowledge": knowledge_summary(conn, wanderer),
        "life": life_payload(wanderer),
        "requirements": promotion_requirements(conn, wanderer),
        "relationships": list_relationships(conn, number, limit=12),
        "legacies": _legacy_rows(conn, number),
        "learning": admin_learning_summary(conn, number),
        "web_search_usage": web_search_usage(conn, wanderer),
        "profile": _profile_payload(wanderer),
        "avatar": _avatar_payload(wanderer),
        "reviews": [_serialize_review(row) for row in _recent(conn, "wanderer_dao_reviews", number, DETAIL_RECENT_REVIEWS)],
        "actions": [_serialize_action(row) for row in _recent(conn, "wanderer_action_log", number, DETAIL_RECENT_ACTIONS)],
    }


def _page_bounds(page: Any, limit: Any, default_limit: int) -> tuple[int, int, int]:
    page_number = max(1, _as_int(page, 1))
    size = max(1, min(MAX_PAGE_LIMIT, _as_int(limit, default_limit)))
    return page_number, size, (page_number - 1) * size


def _paged(conn: Any, table: str, wanderer_id: int, page: Any, limit: Any, default_limit: int) -> tuple[list[Any], dict[str, int]]:
    page_number, size, offset = _page_bounds(page, limit, default_limit)
    total = _count(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE wanderer_id = ?", (wanderer_id,))
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE wanderer_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (wanderer_id, size, offset)
    ).fetchall()
    return rows, {"page": page_number, "limit": size, "total": total, "pages": max(1, -(-total // size))}


def list_actions(conn: Any, wanderer_id: Any, *, page: Any = 1, limit: Any = DEFAULT_PAGE_LIMIT) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    rows, meta = _paged(conn, "wanderer_action_log", int(wanderer["id"]), page, limit, DEFAULT_PAGE_LIMIT)
    return {"items": [_serialize_action(row) for row in rows], **meta}


def list_reviews(conn: Any, wanderer_id: Any, *, page: Any = 1, limit: Any = DEFAULT_PAGE_LIMIT) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    rows, meta = _paged(conn, "wanderer_dao_reviews", int(wanderer["id"]), page, limit, DEFAULT_PAGE_LIMIT)
    return {"items": [_serialize_review(row) for row in rows], **meta}


def list_knowledge(conn: Any, wanderer_id: Any, *, page: Any = 1, limit: Any = DEFAULT_KNOWLEDGE_PAGE_LIMIT) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    rows, meta = _paged(conn, "wanderer_knowledge_entries", int(wanderer["id"]), page, limit, DEFAULT_KNOWLEDGE_PAGE_LIMIT)
    return {"items": [_serialize_knowledge(row) for row in rows], "summary": knowledge_summary(conn, wanderer), **meta}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _bounded_int(value: Any, bounds: tuple[int, int], label: str) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{label}必须是整数")
    try:
        number = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数") from exc
    low, high = bounds
    if not low <= number <= high:
        raise ValueError(f"{label}须在 {low}–{high} 之间")
    return number


def _json_patch_value(key: str, value: Any) -> str:
    kind = _JSON_PATCH_FIELDS[key]
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ValueError(f"{key} 不是合法 JSON") from exc
    if not isinstance(parsed, kind):
        raise ValueError(f"{key} 须为 JSON {'对象' if kind is dict else '数组'}")
    text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    if len(text) > JSON_FIELD_MAX_CHARS:
        raise ValueError(f"{key} 过长")
    return text


def _persona_prompt_value(value: Any) -> str:
    prompt = str(value or "").strip()
    if not prompt:
        raise ValueError("人格提示词不能为空")
    if len(prompt) > PERSONA_PROMPT_MAX_CHARS:
        raise ValueError(f"人格提示词不能超过 {PERSONA_PROMPT_MAX_CHARS} 字")
    return prompt


def _validate_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise ValueError("没有可保存的字段")
    unknown = sorted(set(patch) - _PATCH_FIELDS)
    if unknown:
        raise ValueError(f"不支持修改字段：{', '.join(unknown)}")
    columns: dict[str, Any] = {}
    if "persona_prompt" in patch:
        columns["persona_prompt"] = _persona_prompt_value(patch["persona_prompt"])
    if "activity_level" in patch:
        columns["activity_level"] = _bounded_int(patch["activity_level"], ACTIVITY_LEVEL_RANGE, "活跃度")
    if "daily_action_budget" in patch:
        columns["daily_action_budget"] = _bounded_int(patch["daily_action_budget"], DAILY_ACTION_BUDGET_RANGE, "每日动作预算")
    for key in _JSON_PATCH_FIELDS:
        if key in patch:
            columns[key] = _json_patch_value(key, patch[key])
    return columns


def _tier_reason(reason: Any) -> str:
    note = str(reason or "").strip()
    if not note:
        raise ValueError("调整境界需要填写理由")
    if len(note) > TIER_REASON_MAX_CHARS:
        raise ValueError(f"理由不能超过 {TIER_REASON_MAX_CHARS} 字")
    return note


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------

def _update_columns(conn: Any, wanderer_id: int, columns: dict[str, Any]) -> None:
    # Column names come only from this module's validated whitelists.
    assignments = ", ".join(f"{column} = ?" for column in columns)
    conn.execute(
        f"UPDATE wanderer_agents SET {assignments}, updated_at = ? WHERE id = ?",
        (*columns.values(), local_iso(), int(wanderer_id)),
    )


def update_wanderer(conn: Any, wanderer_id: Any, patch: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    body = dict(patch) if isinstance(patch, dict) else patch
    talent_raw = None
    talent_reason = ""
    if isinstance(body, dict):
        body = dict(body)
        talent_raw = body.pop("talents_json", None)
        talent_reason = str(body.pop("talent_reason", "") or "").strip()
    columns = _validate_patch(body) if body else {}
    if talent_raw is not None:
        update_talents(conn, wanderer, talent_raw, talent_reason, actor=actor)
        wanderer = require_wanderer(conn, wanderer_id)
    has_avatar = isinstance(body, dict) and _AVATAR_PATCH_FIELD in body
    if has_avatar:
        columns.update(_avatar_columns(wanderer, body[_AVATAR_PATCH_FIELD]))
    if not columns and talent_raw is None and not has_avatar:
        raise ValueError("没有可保存的字段")
    if columns:
        _update_columns(conn, int(wanderer["id"]), columns)
    _invalidate(wanderer)
    return build_wanderer_detail(conn, wanderer["id"])


def update_talents(conn: Any, wanderer: dict[str, Any], raw: Any, reason: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    """Admin talent edit: validated, clamped, one review row with the old/new vector."""
    from .wanderer_life_core import normalize_talents
    from .wanderer_life_service import promote_life

    note = str(reason or "").strip()
    if not note:
        raise ValueError("修改天赋需要填写理由")
    if len(note) > TIER_REASON_MAX_CHARS:
        raise ValueError(f"理由不能超过 {TIER_REASON_MAX_CHARS} 字")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("talents_json 不是合法 JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("talents_json 须为对象")
    before = normalize_talents(wanderer.get("talents"), slug=wanderer.get("slug"))
    after = normalize_talents({**before, **{key: raw[key] for key in raw if key in before}}, slug=wanderer.get("slug"))
    changed = {key: [before[key], after[key]] for key in before if abs(before[key] - after[key]) > 1e-9}
    if not changed:
        raise ValueError("天赋没有变化")
    conn.execute(
        "UPDATE wanderer_agents SET talents_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(after, ensure_ascii=False, sort_keys=True), local_iso(), int(wanderer["id"])),
    )
    if "vitality" in changed:
        promote_life(conn, wanderer, wanderer.get("tier"))
        from .wanderer_identity_service import death_reason, mark_dead

        fresh = require_wanderer(conn, wanderer["id"])
        reason = death_reason(conn, fresh)
        if reason:
            mark_dead(conn, int(wanderer["id"]), reason)
    review_id = insert_dao_review(
        conn, wanderer_id=int(wanderer["id"]), review_kind="admin_override", verdict="noted",
        from_tier=clamp_tier(wanderer.get("tier")), to_tier=clamp_tier(wanderer.get("tier")),
        reason=f"调整天赋：{note}", created_by=_actor_label(actor),
        evidence={"talent_from": before, "talent_to": after, "changed": changed},
    )
    _update_columns(conn, int(wanderer["id"]), {"last_review_id": review_id, "last_review_at": local_iso()})
    _invalidate(wanderer)
    return {"changed": changed, "talents": after, "review_id": review_id}


def adjust_life(conn: Any, wanderer_id: Any, weeks: Any, reason: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    """Admin life adjustment: positive grants weeks (≤52), negative deducts; both need a reason."""
    from .wanderer_life_service import set_life_penalty

    wanderer = require_wanderer(conn, wanderer_id)
    if wanderer.get("is_dead"):
        raise ValueError("散修已陨落，请使用「复活」")
    note = _tier_reason(reason)
    result = set_life_penalty(conn, wanderer, weeks, reason=note)
    insert_dao_review(
        conn, wanderer_id=int(wanderer["id"]), review_kind="admin_override", verdict="noted",
        from_tier=clamp_tier(wanderer.get("tier")), to_tier=clamp_tier(wanderer.get("tier")),
        reason=f"调整寿元：{note}", created_by=_actor_label(actor),
        evidence={"weeks_delta": float(weeks), **result},
    )
    _invalidate(wanderer)
    return {"result": result, "card": _card_for(conn, int(wanderer["id"])),
            "detail": build_wanderer_detail(conn, wanderer["id"])}


def set_status(conn: Any, wanderer_id: Any, status: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    wanted = str(status or "").strip()
    if wanted not in {STATUS_ACTIVE, STATUS_PAUSED}:
        raise ValueError("状态只能设为 active 或 paused")
    if wanderer.get("is_dead"):
        raise ValueError("散修已陨落，请使用「复活」")
    _update_columns(conn, int(wanderer["id"]), {"status": wanted})
    _invalidate(wanderer)
    return _card_for(conn, int(wanderer["id"]))


def set_tier(conn: Any, wanderer_id: Any, tier: Any, reason: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    target = _bounded_int(tier, (0, MAX_TIER), "境界")
    note = _tier_reason(reason)
    if wanderer.get("is_dead"):
        raise ValueError("散修已陨落，请先复活")
    review_id = insert_dao_review(
        conn, wanderer_id=int(wanderer["id"]), review_kind="admin_override", verdict="noted",
        from_tier=clamp_tier(wanderer.get("tier")), to_tier=target, reason=note, created_by=_actor_label(actor),
    )
    _update_columns(conn, int(wanderer["id"]), {"tier": target, "last_review_id": review_id, "last_review_at": local_iso()})
    from .wanderer_identity_service import death_reason, mark_dead
    from .wanderer_life_service import promote_life

    promote_life(conn, wanderer, target)
    fresh = require_wanderer(conn, wanderer["id"])
    reason_text = death_reason(conn, fresh)
    if reason_text:
        mark_dead(conn, int(wanderer["id"]), reason_text)
    _invalidate(wanderer)
    return {"review_id": review_id, "card": _card_for(conn, int(wanderer["id"]))}


def reset_warnings(conn: Any, wanderer_id: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    _update_columns(conn, int(wanderer["id"]), {"warnings_count": 0})
    _invalidate(wanderer)
    return _card_for(conn, int(wanderer["id"]))


def wipe(conn: Any, wanderer_id: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    wanderer = require_wanderer(conn, wanderer_id)
    if wanderer.get("is_dead"):
        raise ValueError("散修已陨落")
    forgotten = forget_all(conn, int(wanderer["id"]))
    _update_columns(conn, int(wanderer["id"]), {"tier": 0, "cultivation_score": 0, "promotion_requested": 0})
    mark_dead(conn, int(wanderer["id"]), f"管理员抹除（{_actor_label(actor)}）")
    _invalidate(wanderer)
    return {"forgotten": forgotten, "card": _card_for(conn, int(wanderer["id"]))}


def revive(conn: Any, wanderer_id: Any, *, actor: dict[str, Any]) -> dict[str, Any]:
    from .wanderer_legacy_service import load_legacy, legacy_payload, void_legacy_for
    from .wanderer_life_core import lifespan_weeks_for, normalize_talents
    from .wanderer_life_service import seed_after_revival

    wanderer = require_wanderer(conn, wanderer_id)
    if not wanderer.get("is_dead"):
        raise ValueError("只有已陨落的散修可以复活")
    now = local_iso()
    talents = normalize_talents(wanderer.get("talents"), slug=wanderer.get("slug"))
    conn.execute(
        "UPDATE wanderer_knowledge_entries SET status = 'forgotten', updated_at = ? "
        "WHERE wanderer_id = ? AND status = 'active'", (now, int(wanderer["id"])),
    )
    _update_columns(conn, int(wanderer["id"]), {
        "status": STATUS_ACTIVE, "tier": 0, "warnings_count": 0, "promotion_requested": 0,
        "appeal_requested": 0, "knowledge_chars": 0, "cultivation_score": 0,
        "born_at": now, "lifespan_weeks": lifespan_weeks_for(0, talents["vitality"]),
        "life_penalty_weeks": 0, "life_extensions": 0, "last_extend_at": None, "last_resurrect_at": None,
    })
    legacy_row = conn.execute(
        "SELECT * FROM wanderer_legacies WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (int(wanderer["id"]),)
    ).fetchone()
    legacy = legacy_payload({key: legacy_row[key] for key in legacy_row.keys()}) if legacy_row is not None else None
    void_legacy_for(conn, int(wanderer["id"]), reason="被管理员复活")
    seeded = seed_after_revival(conn, require_wanderer(conn, wanderer["id"]), legacy)
    conn.execute("UPDATE wanderer_agents SET cultivation_score = 0, updated_at = ? WHERE id = ?",
                 (now, int(wanderer["id"])))
    insert_dao_review(
        conn, wanderer_id=int(wanderer["id"]), review_kind="revive", verdict="revived",
        from_tier=clamp_tier(wanderer.get("tier")), to_tier=0,
        reason="管理员复活，寿元重置，恢复初始人格知识。",
        created_by=_actor_label(actor), evidence={"seed_knowledge": seeded, "life_reset": True},
    )
    _invalidate(wanderer)
    return {"seeded_knowledge": seeded, "card": _card_for(conn, int(wanderer["id"]))}


# ---------------------------------------------------------------------------
# runtime settings
# ---------------------------------------------------------------------------

def _flag(value: Optional[str], default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in _TRUTHY


def get_settings(conn: Any) -> dict[str, Any]:
    ensure_wanderer_schema(conn)
    batch = _as_int(wanderer_runtime_setting_get(conn, SETTING_TICK_BATCH_SIZE), DEFAULT_TICK_BATCH_SIZE)
    hour = str(wanderer_runtime_setting_get(conn, SETTING_DAO_REVIEW_HOUR, "") or DEFAULT_DAO_REVIEW_HOUR)
    low, high = TICK_BATCH_SIZE_RANGE
    return {
        "enabled": _runtime_enabled_flag(conn),
        "env_enabled": _env_enabled(),
        "daily_ai_call_cap": daily_cap(conn),
        "tick_batch_size": max(low, min(high, batch)),
        "classroom_visits_enabled": _flag(wanderer_runtime_setting_get(conn, SETTING_CLASSROOM_VISITS), True),
        "dao_review_hour": hour if _HOUR_RE.match(hour) else DEFAULT_DAO_REVIEW_HOUR,
        "lifecycle_enabled": lifecycle_enabled(conn),
        **genesis_settings(conn),
    }


def _bool_setting(value: Any, label: str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip()
    if text in {"0", "1"}:
        return text
    raise ValueError(f"{label}须为 0 或 1")


def _hour_setting(value: Any) -> str:
    hour = str(value or "").strip()
    if not _HOUR_RE.match(hour):
        raise ValueError("天道审核时间须为 HH:MM")
    return hour


_GENESIS_LABELS = {
    "genesis_min_real_actions_24h": "造人活跃度阈值", "genesis_max_total": "散修总数上限",
    "genesis_max_per_day": "每日造人上限", "genesis_max_per_week": "每周造人上限",
}


def _genesis_int_validator(key: str):
    _default, low, high = GENESIS_INT_SETTINGS[key]
    return lambda value: str(_bounded_int(value, (low, high), _GENESIS_LABELS.get(key, key)))


def _validate_settings(patch: Any) -> dict[str, str]:
    if not isinstance(patch, dict) or not patch:
        raise ValueError("没有可保存的设置")
    unknown = sorted(set(patch) - _SETTING_FIELDS)
    if unknown:
        raise ValueError(f"不支持的设置：{', '.join(unknown)}")
    validators = {
        SETTING_ENABLED: lambda v: _bool_setting(v, "启用开关"),
        SETTING_CLASSROOM_VISITS: lambda v: _bool_setting(v, "旁听课堂"),
        SETTING_DAILY_AI_CALL_CAP: lambda v: str(_bounded_int(v, DAILY_AI_CALL_CAP_RANGE, "每日 AI 调用上限")),
        SETTING_TICK_BATCH_SIZE: lambda v: str(_bounded_int(v, TICK_BATCH_SIZE_RANGE, "每轮散修数")),
        SETTING_DAO_REVIEW_HOUR: _hour_setting,
        SETTING_GENESIS_ENABLED: lambda v: _bool_setting(v, "天道造人开关"),
        SETTING_LIFECYCLE_ENABLED: lambda v: _bool_setting(v, "寿元生死开关"),
        **{key: _genesis_int_validator(key) for key in GENESIS_INT_SETTINGS},
    }
    return {key: validators[key](value) for key, value in patch.items()}


def update_settings(conn: Any, patch: Any) -> dict[str, Any]:
    values = _validate_settings(patch)
    previous_hour = get_settings(conn)["dao_review_hour"]
    for key, value in values.items():
        wanderer_runtime_setting_set(conn, key, value)
    settings = get_settings(conn)
    if settings["dao_review_hour"] != previous_hour:
        from .wanderer_behavior_service import ensure_wanderer_scheduled_tasks

        ensure_wanderer_scheduled_tasks(conn)  # re-arms the daily review at the new hour
    return settings


def reseed(conn: Any) -> dict[str, Any]:
    from .wanderer_persona_seeds import seed_wanderers

    report = seed_wanderers(conn)
    invalidate_wanderer_cache()
    invalidate_emblem_cache()
    return {
        "created": list(report.get("created") or []),
        "knowledge_added": int(report.get("knowledge_added") or 0),
        "total": len(report.get("students") or {}),
    }


# ---------------------------------------------------------------------------
# public card
# ---------------------------------------------------------------------------

def public_card(conn: Any, student_id: Any) -> Optional[dict[str, Any]]:
    number = _as_int(student_id, -1)
    if number <= 0:
        return None
    wanderer = load_wanderer_by_student_id(conn, number)
    if wanderer is None:
        return None
    status = str(wanderer.get("status") or STATUS_ACTIVE)
    tier = tier_payload(wanderer.get("tier"))
    return {
        "is_wanderer": True,
        "student_id": number,
        "display_name": str(wanderer.get("display_name") or ""),
        "archetype": str(wanderer.get("archetype") or ""),
        "tier": tier["tier"],
        "tier_name": tier["tier_name"],
        "emblem": tier["emblem"],
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "description": str(wanderer.get("public_intro") or ""),
        "avatar_url": avatar_url(number),
        "homepage_url": homepage_url(wanderer.get("slug")),
    }
