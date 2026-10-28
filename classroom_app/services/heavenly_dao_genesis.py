"""天道造人 (genesis): new 散修 when the platform is quiet (docs/wanderer-ai-free-agents-2026-10-25.md §10.2).

Runs at the end of the daily review (``heavenly_dao_service.run_dao_review_once``,
full-scope reviews only) and from the admin "立即造人" button.

* ``measure_platform_activity`` counts real users' posts / comments / likes /
  classroom messages / logins over 24h and 7d ("real" excludes every wanderer
  identity and the assistant), plus wanderer actions. It is stored in the
  ``daily_audit`` evidence under ``activity``.
* ``plan_genesis`` decides how many to create:
  room = min(max_total - all wanderer rows (any status),
             max_per_day - created today, max_per_week - created in 7 days);
  nothing when room <= 0, when ``genesis_enabled`` is off, or when
  ``real_actions_24h >= genesis_min_real_actions_24h`` (the manual trigger
  ignores the switch and the activity threshold, never the caps);
  otherwise ``planned = 1`` if there was no genesis in the last 7 days
  (so the very first creation is exactly 1), else ``min(2, room)``.
* one ``dao:genesis`` call (deep text, operation ``generation`` -> effort max)
  returns ``{"rationale", "seeds": [...]}``; each seed must pass
  ``wanderer_persona_seeds.validate_seed`` and must not clash with an existing
  slug, wanderer name or real student name. Invalid seeds are skipped and logged.
* each valid seed is persisted through the same path as the seeded wanderers
  (``ensure_wanderer_students_detailed`` + ``seed_initial_knowledge``) with
  tier 0, cultivation 0, ``origin='dao'``, plus a
  ``wanderer_dao_reviews(review_kind='genesis', verdict='created')`` row.
  The new wanderer gets ``avatar_silhouette = pick_silhouette(seed)`` (recorded
  in the review evidence), see design §11.

Real student names are never sent to the model; the clash check is local.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from ..time_utils import local_iso, local_now
from .heavenly_dao_rules import HEAVENLY_DAO_SYSTEM_PROMPT
from .wanderer_ai_service import DAO_KIND_GENESIS, AiCaller, dao_judge
from .wanderer_avatar_service import pick_silhouette
from .wanderer_identity_service import (
    ORIGIN_DAO,
    WANDERER_ENROLLMENT_STATUS,
    ensure_wanderer_students_detailed,
    insert_dao_review,
    invalidate_wanderer_cache,
    load_wanderer_by_slug,
    wanderer_student_number,
)
from .wanderer_persona_seeds import SeedValidationError, render_persona_prompt, seed_initial_knowledge, validate_seed
from .wanderer_settings_service import (
    SETTING_GENESIS_MAX_PER_DAY,
    SETTING_GENESIS_MAX_PER_WEEK,
    SETTING_GENESIS_MAX_TOTAL,
    SETTING_GENESIS_MIN_REAL_ACTIONS_24H,
    genesis_enabled,
    genesis_setting,
)

GENESIS_REVIEW_KIND = "genesis"
GENESIS_VERDICT = "created"
LOW_ACTIVITY_STREAK_DAYS = 7
MAX_PLANNED = 2
RATIONALE_MAX_CHARS = 200
_REAL_KEYS = ("posts", "comments", "likes", "chat_messages", "logins")

ConnFactory = Optional[Callable[[], Any]]


def _connection(conn_factory: ConnFactory) -> Any:
    if conn_factory is not None:
        return conn_factory()
    from ..database import get_db_connection

    return get_db_connection()


def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    try:
        return int(row["n"] or 0)
    except (TypeError, KeyError, IndexError):
        return int(row[0] or 0)


def _iso(moment: datetime) -> str:
    """Naive local ISO to the second: compares correctly with ``local_iso`` and ``datetime.now().isoformat`` text."""
    return moment.replace(tzinfo=None).isoformat(timespec="seconds")


def _marks(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

def _wanderer_student_ids(conn: Any) -> list[int]:
    return [int(row[0]) for row in conn.execute("SELECT student_id FROM wanderer_agents").fetchall()]


def _login_count(conn: Any, since: str, student_ids: list[int]) -> int:
    """Real student logins; 0 when the audit table is absent (savepoint keeps the transaction usable)."""
    from ..db.savepoint import best_effort

    excluded = f" AND student_id NOT IN ({_marks(student_ids)})" if student_ids else ""
    result = {"n": 0}
    with best_effort(conn, "genesis_login_count", log_prefix="[DAO]"):
        result["n"] = _count(conn, f"SELECT COUNT(*) AS n FROM student_login_audit_logs WHERE logged_at >= ?{excluded}",
                             (since, *student_ids))
    return result["n"]


def _real_counts(conn: Any, since: str, student_ids: list[int]) -> dict[str, int]:
    identities = [f"student:{sid}" for sid in student_ids]
    not_author = f" AND author_identity NOT IN ({_marks(identities)})" if identities else ""
    not_liker = f" AND user_identity NOT IN ({_marks(identities)})" if identities else ""
    # chat_logs.user_id is the user pk as text; wanderers write as role 'student'.
    not_chatter = (f" AND NOT (user_role = 'student' AND user_id IN ({_marks(student_ids)}))"
                   if student_ids else "")
    return {
        "posts": _count(conn, "SELECT COUNT(*) AS n FROM blog_posts WHERE author_role <> 'assistant' "
                              f"AND created_at >= ?{not_author}", (since, *identities)),
        "comments": _count(conn, "SELECT COUNT(*) AS n FROM blog_comments WHERE author_role <> 'assistant' "
                                 f"AND created_at >= ?{not_author}", (since, *identities)),
        "likes": _count(conn, "SELECT COUNT(*) AS n FROM blog_likes WHERE user_role <> 'assistant' "
                              f"AND created_at >= ?{not_liker}", (since, *identities)),
        "chat_messages": _count(conn, "SELECT COUNT(*) AS n FROM chat_logs WHERE user_role IN ('student', 'teacher') "
                                      f"AND logged_at >= ?{not_chatter}",
                                (since, *[str(sid) for sid in student_ids])),
        "logins": _login_count(conn, since, student_ids),
    }


def measure_platform_activity(conn: Any, now: Optional[datetime] = None) -> dict[str, Any]:
    """Real-user and wanderer activity over the last 24h / 7d (counts only, no identities)."""
    moment = now or local_now()
    windows = {"24h": _iso(moment - timedelta(hours=24)), "7d": _iso(moment - timedelta(days=7))}
    student_ids = _wanderer_student_ids(conn)
    activity: dict[str, Any] = {}
    for label, since in windows.items():
        counts = _real_counts(conn, since, student_ids)
        activity.update({f"real_{key}_{label}": counts[key] for key in _REAL_KEYS})
        activity[f"real_actions_{label}"] = sum(counts.values())
        activity[f"wanderer_actions_{label}"] = _count(
            conn, "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE created_at >= ? AND outcome = 'ok' "
                  "AND action_kind NOT IN ('skip', 'experience_learn')", (since,))
    activity["wanderers_total"] = len(student_ids)
    activity["measured_at"] = local_iso()
    return activity


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def _genesis_count_since(conn: Any, since: str) -> int:
    return _count(conn, "SELECT COUNT(*) AS n FROM wanderer_dao_reviews WHERE review_kind = ? AND verdict = ? "
                        "AND created_at >= ?", (GENESIS_REVIEW_KIND, GENESIS_VERDICT, since))


def plan_genesis(conn: Any, activity: dict[str, Any], now: Optional[datetime] = None, *,
                 manual: bool = False) -> dict[str, Any]:
    """``{"planned", "reason", "room", ...}``; see the module doc for the rule."""
    moment = now or local_now()
    threshold = genesis_setting(conn, SETTING_GENESIS_MIN_REAL_ACTIONS_24H)
    total = _count(conn, "SELECT COUNT(*) AS n FROM wanderer_agents", ())
    today = _genesis_count_since(conn, _iso(moment.replace(hour=0, minute=0, second=0, microsecond=0)))
    week = _genesis_count_since(conn, _iso(moment - timedelta(days=LOW_ACTIVITY_STREAK_DAYS)))
    room = min(genesis_setting(conn, SETTING_GENESIS_MAX_TOTAL) - total,
               genesis_setting(conn, SETTING_GENESIS_MAX_PER_DAY) - today,
               genesis_setting(conn, SETTING_GENESIS_MAX_PER_WEEK) - week)
    plan = {"planned": 0, "room": max(0, room), "total": total, "created_today": today, "created_7d": week,
            "threshold": threshold, "real_actions_24h": int(activity.get("real_actions_24h") or 0), "manual": manual}
    if not manual and not genesis_enabled(conn):
        return {**plan, "reason": "disabled"}
    if room <= 0:
        return {**plan, "reason": "caps"}
    if not manual and plan["real_actions_24h"] >= threshold:
        return {**plan, "reason": "activity_ok"}
    planned = 1 if week == 0 else min(MAX_PLANNED, room)
    return {**plan, "planned": min(planned, room), "reason": "low_activity" if not manual else "manual"}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_SEED_EXAMPLE = {
    "slug": "xiaoyu", "display_name": "许小雨", "archetype": "雨天写诗的理科生", "gender": "女",
    "hometown": "四川成都", "childhood": "2–3 句童年经历", "schooling": "2–3 句求学经历",
    "interests": ["3–6 个兴趣标签"], "public_intro": "一句公开自述（≤ 40 字）",
    "speech_style": {"voice_profile": "reflective", "catchphrases": [], "punctuation": "标点习惯", "emoji": "表情偏好",
                     "length": "长短习惯"},
    "temperament": ["3–5 条性格特征"], "interaction_habit": "一句互动习惯",
    "interaction_frequency": {"activity_level": 3, "active_hours": [[12, 14], [21, 23]],
                              "weekday_weights": {"0": 1.0, "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0, "5": 0.6,
                                                  "6": 0.6}},
    "reply_policy": {"ignore_prob": 0.3, "emoji_only_prob": 0.1, "multi_part_prob": 0.2, "max_parts": 2,
                     "post_prob": 0.08},
    "taboos": ["1–5 条禁忌"],
    "initial_knowledge": [{"title": "短标题", "digest": "一两句知识要点", "tags": ["标签"]}] * 3,
    "daily_action_budget": 10,
}


def _existing_wanderers(conn: Any) -> list[dict[str, str]]:
    rows = conn.execute("SELECT slug, display_name, archetype FROM wanderer_agents ORDER BY id").fetchall()
    return [{"slug": str(row[0]), "name": str(row[1]), "archetype": str(row[2])} for row in rows]


def genesis_message(existing: list[dict[str, str]], planned: int, activity: dict[str, Any]) -> str:
    from .wanderer_voice_service import genesis_voice_catalog
    return (
        f"本次不是裁决，而是「天道造人」：平台近来冷清，请创造 {planned} 位新的散修（AI 电子学生）。\n"
        "要求：\n"
        "1. 每位的性格、原型、兴趣、说话方式都要与下列已有散修明显不同，也彼此不同；不要再造「礼貌 AI 助手」。\n"
        "2. slug 用 3–24 位小写拼音字母或数字，不得与已有 slug 重复；display_name 用 2–4 个汉字的中文名或 2–12 位英文昵称，"
        "不得与已有散修重名，也不要用常见的真实人名组合。\n"
        "3. 新散修从零开始修行（第 0 境），initial_knowledge 正好 3 条，只是背景常识。\n"
        "4. 字段与示例完全一致，数值在示例给出的范围内；文字自然、口语，不用 Markdown。\n"
        "5. speech_style.voice_profile 选择最符合个人气质的一项，出生即绑定对应讲话指南和情境案例。"
        "catchphrases 可以为空，最多四条低频偏好，不用固定口头禅或表情替代完整性格。"
        "同一种声线也要有不同关注点、背景和关系距离。\n"
        f"可用声线：{genesis_voice_catalog()}\n"
        f"已有散修（slug / 名字 / 原型）：{json.dumps(existing, ensure_ascii=False)}\n"
        f"平台活跃度：{json.dumps(activity, ensure_ascii=False, sort_keys=True)}\n"
        f"单个种子的格式示例：{json.dumps(_SEED_EXAMPLE, ensure_ascii=False)}\n"
        '只输出一个 JSON 对象：{"rationale": "一句话说明为何造这几位", "seeds": [种子, ...]}'
    )


def _clash_sets(conn: Any) -> tuple[set[str], set[str]]:
    slugs = {str(row[0]) for row in conn.execute("SELECT slug FROM wanderer_agents").fetchall()}
    numbers = conn.execute("SELECT student_id_number FROM students WHERE student_id_number LIKE ?",
                           (wanderer_student_number("") + "%",)).fetchall()
    slugs |= {str(row[0])[len(wanderer_student_number("")):] for row in numbers}
    names = {str(row[0]).strip() for row in conn.execute("SELECT display_name FROM wanderer_agents").fetchall()}
    names |= {str(row[0]).strip() for row in conn.execute(
        "SELECT DISTINCT name FROM students WHERE COALESCE(enrollment_status, '') <> ?",
        (WANDERER_ENROLLMENT_STATUS,)).fetchall()}
    names |= {str(row[0]).strip() for row in conn.execute("SELECT DISTINCT name FROM teachers").fetchall()}
    return slugs, names


def select_valid_seeds(conn: Any, raw_seeds: Any, planned: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """(valid normalized seeds, at most ``planned``; rejections with reasons)."""
    slugs, names = _clash_sets(conn)
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for index, raw in enumerate(raw_seeds if isinstance(raw_seeds, list) else []):
        label = str(raw.get("slug") if isinstance(raw, dict) else index)[:40]
        try:
            seed = validate_seed(raw)
        except SeedValidationError as exc:
            rejected.append({"seed": label, "reason": str(exc)})
            continue
        if seed["slug"] in slugs or seed["display_name"] in names:
            rejected.append({"seed": label, "reason": "slug 或名字与已有散修/真实用户重复"})
            continue
        if len(valid) >= planned:
            rejected.append({"seed": label, "reason": "超出本次计划数量"})
            continue
        slugs.add(seed["slug"])
        names.add(seed["display_name"])
        valid.append(seed)
    for item in rejected:
        print(f"[DAO] genesis seed skipped: {item['seed']}: {item['reason']}")
    return valid, rejected


def _seed_summary(seed: dict[str, Any]) -> dict[str, Any]:
    return {"slug": seed["slug"], "display_name": seed["display_name"], "archetype": seed["archetype"],
            "interests": seed["interests"], "temperament": seed["temperament"]}


def persist_genesis_seed(conn: Any, seed: dict[str, Any], *, rationale: str, activity: dict[str, Any],
                         created_by: str = "dao") -> Optional[int]:
    """Create the wanderer (tier 0, cultivation 0, origin 'dao'). Returns its id, or None if it already existed."""
    payload = {**seed, "initial_tier": 0, "origin": ORIGIN_DAO, "persona_prompt": render_persona_prompt(seed)}
    detail = ensure_wanderer_students_detailed(conn, [payload])
    wanderer = load_wanderer_by_slug(conn, seed["slug"])
    if seed["slug"] not in detail["created"] or wanderer is None:
        return None
    seed_initial_knowledge(conn, wanderer, seed)
    silhouette = pick_silhouette(seed)
    review_id = insert_dao_review(
        conn, wanderer_id=int(wanderer["id"]), review_kind=GENESIS_REVIEW_KIND, verdict=GENESIS_VERDICT,
        from_tier=0, to_tier=0, reason=rationale or "平台冷清，天道造人。",
        evidence={"activity": activity, "seed_summary": _seed_summary(seed), "avatar_silhouette": silhouette},
        created_by=created_by,
    )
    now = local_iso()
    # The birth record is history, not a verdict to reflect on.
    conn.execute("UPDATE wanderer_dao_reviews SET acknowledged_at = ? WHERE id = ?", (now, review_id))
    conn.execute(
        "UPDATE wanderer_agents SET cultivation_score = 0, created_by_review_id = ?, last_review_id = ?, "
        "last_review_at = ?, avatar_silhouette = ?, updated_at = ? WHERE id = ?",
        (review_id, review_id, now, silhouette, now, int(wanderer["id"])),
    )
    invalidate_wanderer_cache([wanderer["student_id"]])
    return int(wanderer["id"])


async def run_genesis(conn_factory: ConnFactory = None, *, activity: Optional[dict[str, Any]] = None,
                      ai_caller: Optional[AiCaller] = None, manual: bool = False,
                      now: Optional[datetime] = None) -> dict[str, Any]:
    """Measure (unless given), plan, generate, validate and persist. Returns a summary dict."""
    with _connection(conn_factory) as conn:
        measured = activity if activity is not None else measure_platform_activity(conn, now)
        plan = plan_genesis(conn, measured, now, manual=manual)
        existing = _existing_wanderers(conn)
    summary: dict[str, Any] = {"status": "skipped", "activity": measured, "plan": plan, "created": 0,
                               "created_ids": [], "rejected": []}
    if plan["planned"] <= 0:
        return summary
    result = await dao_judge(conn_factory, system_prompt=HEAVENLY_DAO_SYSTEM_PROMPT,
                             user_message=genesis_message(existing, plan["planned"], measured),
                             kind=DAO_KIND_GENESIS, ai_caller=ai_caller)
    payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    rationale = str(payload.get("rationale") or "").strip()[:RATIONALE_MAX_CHARS]
    with _connection(conn_factory) as conn:
        seeds, rejected = select_valid_seeds(conn, payload.get("seeds"), plan["planned"])
        created_ids = [wanderer_id for wanderer_id in (
            persist_genesis_seed(conn, seed, rationale=rationale, activity=measured,
                                 created_by="admin" if manual else "dao") for seed in seeds
        ) if wanderer_id is not None]
        conn.commit()
    return {**summary, "status": "ok" if result.get("status") == "ok" else "failed", "created": len(created_ids),
            "created_ids": created_ids, "rejected": rejected, "rationale": rationale,
            "error": str(result.get("error") or "")[:200]}
