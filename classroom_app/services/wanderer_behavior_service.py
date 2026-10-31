"""散修 behavior engine: the 10-minute tick (docs/wanderer-ai-free-agents-2026-10-25.md §5.1-5.2).

Each tick:

1. no-op unless ``is_wanderer_runtime_enabled`` (env + runtime switch), except
   when an admin forces it (``force=True``);
2. select awake wanderers — status active, inside one of their ``active_hours``,
   past the ``weekday_weights`` random gate, under today's action budget —
   shuffle, keep ``tick_batch_size`` (default 6);
3. per wanderer: reflect on unacknowledged 天道 verdicts first, then 1-3 actions
   by ``activity_level`` (pending replies first), pausing 1-4 s between them;
   the row is re-read before each action and the wanderer stops as soon as
   it is no longer active or an admin changed its behaviors;
4. evaluate death; a dead wanderer stops for good;
5. social upkeep (regular ticks only, not admin runs on chosen wanderers):
   push 散修 online/offline changes and rarely send a proactive friend request
   (``wanderer_social_friends.run_social_upkeep``; social doc §10).

The whole tick is bounded by ``tick_max_seconds`` (runtime setting, default
150 s): on timeout the summary has status ``timeout`` and lists the wanderers
finished so far (every action is logged individually, so partial progress is
kept).

A failing wanderer never aborts the tick: the exception is logged as a
``failed`` action and the loop moves on. When the daily AI cap is reached the
remaining actions degrade to the no-AI ones (browse/like/skip).
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable, Optional

from ..time_utils import local_iso, local_now
from .wanderer_actions_blog import (
    browse_blog,
    handle_pending_replies,
    has_pending_work,
    pending_blog_notifications,
    post_eligible,
    wants_to_post,
    write_post,
)
from .wanderer_actions_classroom import visit_classroom
from .wanderer_actions_common import ActionContext, as_int, behavior_on, outcome, record_action, today_action_count
from .wanderer_actions_knowledge import (
    appeal_eligible,
    maybe_extend_life,
    promotion_eligible,
    reflect_on_reviews,
    request_appeal,
    request_promotion,
    study_material,
    try_resurrect,
)
from .wanderer_ai_service import AiCaller
from .wanderer_manual_action import read_manual
from .wanderer_social_friends import maybe_seek_friend
from .wanderer_social_outreach import outreach_candidates, start_friend_conversation
from .wanderer_social_actions import (
    available_actions as social_maintenance_actions,
    maybe_block as maybe_social_block,
    maybe_create_group as maybe_social_group_create,
    maybe_invite_group as maybe_social_group_invite,
    maybe_leave_group as maybe_social_group_leave,
    maybe_unblock as maybe_social_unblock,
)
from .wanderer_identity_service import (
    STATUS_ACTIVE,
    clamp_tier,
    current_life,
    daily_budget_factor,
    death_reason,
    knowledge_capacity_for,
    list_wanderers,
    load_wanderer,
    mark_dead,
)
from .wanderer_life_core import INTERACTION_POINTS, can_extend_life
from .wanderer_life_service import (
    award_interaction_cultivation,
    backfill_all,
    can_resurrect,
)
from .wanderer_settings_service import (
    SETTING_DAO_REVIEW_HOUR_ARMED,
    SETTING_LAST_TICK_AT,
    daily_ai_calls,
    SETTING_REFLECTION_HOUR_ARMED,
    daily_cap,
    dao_review_hour,
    is_wanderer_runtime_enabled,
    reflection_hour,
    tick_batch_size,
    tick_max_seconds,
    wanderer_runtime_setting_get,
    wanderer_runtime_setting_set,
)

TASK_KIND_WANDERER_TICK = "wanderer_tick"
TASK_KIND_WANDERER_REPLY_TICK = "wanderer_reply_tick"
TASK_KIND_WANDERER_DAO_DAILY = "wanderer_dao_daily_review"
TASK_KIND_WANDERER_REFLECTION = "wanderer_daily_reflection"
TICK_DEDUPE_KEY = "wanderer:tick"
DAO_DAILY_DEDUPE_KEY = "wanderer:dao-daily"
REFLECTION_DEDUPE_KEY = "wanderer:daily-reflection"
TICK_INTERVAL_SECONDS = 600
DAO_INTERVAL_SECONDS = 86400
REFLECTION_INTERVAL_SECONDS = 86400
FIRST_TICK_DELAY_SECONDS = 120
_LIVE_TASK_STATUSES = ("pending", "running")
# A failure only bumps attempt_count (reset on every success) with backoff capped
# at 1 h; a recurring task that reaches max_attempts goes 'failed' for good, so
# the tick tolerates long transient outages and the daily review about a day.
TICK_MAX_ATTEMPTS = 1000
DAO_MAX_ATTEMPTS = 24
REFLECTION_MAX_ATTEMPTS = 24

ActionFn = Callable[[ActionContext], Awaitable[list[dict[str, Any]]]]
SleepFn = Callable[[float], Awaitable[Any]]

# Which action_kind a crashed action is logged under.
_FAILURE_KIND = {
    "replies": "reply", "blog": "browse_post", "classroom": "classroom_visit",
    "knowledge": "knowledge_read", "promotion": "request_promotion", "reflect": "reflect", "post": "write_post",
    "social_block": "social_block", "social_unblock": "social_unblock",
    "social_group_create": "social_group_create", "social_group_invite": "social_group_invite",
    "social_group_leave": "social_group_leave", "social_seek": "social_seek",
    "manual": "read_manual",
    "extend_life": "extend_life", "appeal": "appeal", "resurrect": "resurrect",
}
POST_WEIGHT = 4.0


def _connection(conn_factory: Optional[Callable[[], Any]]) -> Any:
    if conn_factory is not None:
        return conn_factory()
    from ..database import get_db_connection

    return get_db_connection()


# ---------------------------------------------------------------------------
# Scheduler registration
# ---------------------------------------------------------------------------

def next_review_run_at(now: datetime, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _arm(
    conn: Any, *, task_kind: str, dedupe_key: str, run_at: datetime, recurrence: int, title: str,
    max_attempts: int, force: bool = False,
) -> int:
    """Keep a live recurring task as is (restarts must not reset its clock); re-arm a dead one.

    ``force`` re-arms a live task too (the configured run time changed).
    """
    from .scheduled_task_service import get_owner_task_by_dedupe, schedule_task

    existing = get_owner_task_by_dedupe(conn, dedupe_key)
    if not force and existing and str(existing.get("status") or "") in _LIVE_TASK_STATUSES:
        return int(existing["id"])
    return schedule_task(conn, task_kind=task_kind, run_at=run_at, payload={}, dedupe_key=dedupe_key,
                         recurrence_seconds=recurrence, owner_role="system", title=title, priority=120,
                         max_attempts=max_attempts, replace=True)


def ensure_wanderer_scheduled_tasks(conn: Any) -> dict[str, int]:
    """Register the recurring tick and the daily 天道 review (idempotent). Caller commits.

    The daily review is re-armed when ``dao_review_hour`` differs from the hour
    it was armed with (``dao_review_hour_armed``).
    """
    now = datetime.now()
    hour, minute = dao_review_hour(conn)
    wanted_hour = f"{hour:02d}:{minute:02d}"
    hour_changed = wanderer_runtime_setting_get(conn, SETTING_DAO_REVIEW_HOUR_ARMED) != wanted_hour
    ids = {
        "tick": _arm(conn, task_kind=TASK_KIND_WANDERER_TICK, dedupe_key=TICK_DEDUPE_KEY,
                     run_at=now + timedelta(seconds=FIRST_TICK_DELAY_SECONDS), recurrence=TICK_INTERVAL_SECONDS,
                     title="散修行为节拍", max_attempts=TICK_MAX_ATTEMPTS),
        "dao": _arm(conn, task_kind=TASK_KIND_WANDERER_DAO_DAILY, dedupe_key=DAO_DAILY_DEDUPE_KEY,
                    run_at=next_review_run_at(now, hour, minute), recurrence=DAO_INTERVAL_SECONDS,
                    title="天道每日审核", max_attempts=DAO_MAX_ATTEMPTS, force=hour_changed),
    }
    if hour_changed:
        wanderer_runtime_setting_set(conn, SETTING_DAO_REVIEW_HOUR_ARMED, wanted_hour)
    reflection_hour_value, reflection_minute = reflection_hour(conn)
    reflection_wanted = f"{reflection_hour_value:02d}:{reflection_minute:02d}"
    reflection_changed = wanderer_runtime_setting_get(conn, SETTING_REFLECTION_HOUR_ARMED) != reflection_wanted
    ids["reflection"] = _arm(
        conn, task_kind=TASK_KIND_WANDERER_REFLECTION, dedupe_key=REFLECTION_DEDUPE_KEY,
        run_at=next_review_run_at(now, reflection_hour_value, reflection_minute),
        recurrence=REFLECTION_INTERVAL_SECONDS, title="散修每日自省", max_attempts=REFLECTION_MAX_ATTEMPTS,
        force=reflection_changed,
    )
    if reflection_changed:
        wanderer_runtime_setting_set(conn, SETTING_REFLECTION_HOUR_ARMED, reflection_wanted)
    return ids


# ---------------------------------------------------------------------------
# Awake selection (§5.1)
# ---------------------------------------------------------------------------

def _in_active_hours(hours: Iterable[Any], now: datetime) -> bool:
    current = now.hour + now.minute / 60.0
    for pair in hours or []:
        try:
            start, end = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if start <= end and start <= current < end:
            return True
        if start > end and (current >= start or current < end):
            return True
    return False


def _weekday_weight(schedule: dict[str, Any], now: datetime) -> float:
    weights = schedule.get("weekday_weights") or {}
    try:
        return max(0.0, float(weights.get(str(now.weekday()), 1.0)))
    except (TypeError, ValueError):
        return 1.0


def daily_budget(wanderer: dict[str, Any]) -> int:
    return int(round(as_int(wanderer.get("daily_action_budget")) * daily_budget_factor(wanderer.get("tier"))))


def is_awake(wanderer: dict[str, Any], now: datetime, rng: random.Random) -> bool:
    schedule = wanderer.get("schedule") or {}
    if not _in_active_hours(schedule.get("active_hours") or [], now):
        return False
    return rng.random() < _weekday_weight(schedule, now)


def select_awake_wanderers(
    conn: Any, *, now: datetime, rng: random.Random, limit: int, wanderer_ids: Optional[Iterable[Any]] = None,
) -> list[dict[str, Any]]:
    """Active, awake, under budget; shuffled; at most ``limit``.

    Explicit ``wanderer_ids`` (admin "run now" on chosen wanderers) skip the
    hours/weekday gate but never the status or the daily budget.
    """
    wanted = {as_int(item) for item in wanderer_ids or [] if as_int(item) > 0}
    candidates = []
    for wanderer in list_wanderers(conn, statuses=[STATUS_ACTIVE]):
        if wanted and int(wanderer["id"]) not in wanted:
            continue
        if not wanted and not is_awake(wanderer, now, rng):
            continue
        if today_action_count(conn, int(wanderer["id"]), now) >= daily_budget(wanderer):
            continue
        candidates.append(wanderer)
    rng.shuffle(candidates)
    return candidates[: max(0, int(limit))]


def reply_behaviors_on(wanderer: dict[str, Any]) -> bool:
    return behavior_on(wanderer, "reply") or behavior_on(wanderer, "dm_reply")


def _waiting_for_blog_reply(conn: Any, wanderer: dict[str, Any]) -> bool:
    if not reply_behaviors_on(wanderer):
        return False
    return bool(pending_blog_notifications(conn, f"student:{wanderer['student_id']}", 1, real_only=True))


def select_tick_wanderers(
    conn: Any, *, now: datetime, rng: random.Random, limit: int,
    wanderer_ids: Optional[Iterable[Any]] = None, reply_only: bool = False,
) -> list[tuple[dict[str, Any], bool]]:
    """``[(wanderer, reply_only)]`` for one tick.

    * normal: awake and under today's action budget;
    * reply-only: someone is waiting for an answer. These run even while the
      wanderer is "asleep" and even when its interaction budget is used up —
      the schedule governs proactive exploring, not answering a person
      (docs/wanderer-reply-priority-2026-10-28.md). The AI call cap is still
      enforced by ``generate_turn``, so cost stays bounded.
    """
    limit = max(0, int(limit))
    if limit == 0:
        return []
    wanted = {as_int(item) for item in wanderer_ids or [] if as_int(item) > 0}
    active = [w for w in list_wanderers(conn, statuses=[STATUS_ACTIVE])
              if not wanted or int(w["id"]) in wanted]
    if reply_only:
        waiting = [w for w in active if _waiting_for_blog_reply(conn, w)]
        rng.shuffle(waiting)
        return [(w, True) for w in waiting[:limit]]

    def waiting_ones(excluded: set[int]) -> list[dict[str, Any]]:
        rows = [w for w in active if int(w["id"]) not in excluded and _waiting_for_blog_reply(conn, w)]
        rng.shuffle(rows)
        return rows

    if wanted:
        # Admin "run now": everyone chosen still gets a full turn when it can;
        # over-budget chosen wanderers only answer the people waiting for them.
        normal = [w for w in active if today_action_count(conn, int(w["id"]), now) < daily_budget(w)]
        rng.shuffle(normal)
        picked: list[tuple[dict[str, Any], bool]] = [(w, False) for w in normal[:limit]]
        remaining = limit - len(picked)
        if remaining > 0:
            picked.extend((w, True) for w in waiting_ones({int(w["id"]) for w, _only in picked})[:remaining])
        return picked

    # Regular tick: people first — a waiting reply is served before proactive exploring.
    picked = [(w, True) for w in waiting_ones(set())[:limit]]
    remaining = limit - len(picked)
    if remaining <= 0:
        return picked
    taken = {int(w["id"]) for w, _only in picked}
    awake = [
        w for w in active
        if int(w["id"]) not in taken and is_awake(w, now, rng)
        and today_action_count(conn, int(w["id"]), now) < daily_budget(w)
    ]
    rng.shuffle(awake)
    picked.extend((w, False) for w in awake[:remaining])
    return picked


# ---------------------------------------------------------------------------
# Action choice (§5.2)
# ---------------------------------------------------------------------------

def actions_per_tick(activity_level: Any, rng: random.Random) -> int:
    level = max(1, min(5, as_int(activity_level) or 3))
    if level <= 2:
        return 1
    return rng.randint(1, 2) if level <= 4 else rng.randint(1, 3)


def _knowledge_weight(wanderer: dict[str, Any]) -> float:
    capacity = knowledge_capacity_for(wanderer) or 1
    fill = min(1.0, as_int(wanderer.get("knowledge_chars")) / capacity)
    return 1.0 + 3.0 * (1.0 - fill)


def choose_action(ctx: ActionContext, *, first: bool) -> str:
    """'replies' first when something is waiting, else a weighted pick; capped -> blog only."""
    wanderer = ctx.wanderer
    if first and (behavior_on(wanderer, "reply") or behavior_on(wanderer, "dm_reply")) and has_pending_work(ctx):
        return "replies"
    if ctx.capped:
        return "blog" if behavior_on(wanderer, "blog_browse") else "idle"
    options: list[tuple[str, float]] = []
    if behavior_on(wanderer, "blog_browse"):
        options.append(("blog", 5.0))
    if behavior_on(wanderer, "classroom_visit"):
        options.append(("classroom", 2.0))
    if behavior_on(wanderer, "knowledge_read"):
        options.append(("knowledge", _knowledge_weight(wanderer)))
    with ctx.connect() as conn:
        if behavior_on(wanderer, "social") and outreach_candidates(conn, ctx):
            options.append(("social_outreach", 2.0))
        if behavior_on(wanderer, "social") and behavior_on(wanderer, "social_seek"):
            options.append(("social_seek", 0.7))
        if behavior_on(wanderer, "manual_read"):
            options.append(("manual", 0.5))
        for name in social_maintenance_actions(conn, ctx):
            options.append((name, 0.6 if name in {"social_block", "social_group_create"} else 0.3))
        if promotion_eligible(ctx, conn):
            options.append(("promotion", 3.0))
        if can_extend_life(current_life(wanderer), wanderer.get("cultivation_score")):
            options.append(("extend_life", 6.0))
        if ctx.special and appeal_eligible(conn, wanderer, ctx.current_time()):
            options.append(("appeal", 1.5))
        if ctx.special and can_resurrect(conn, wanderer).get("ok"):
            options.append(("resurrect", 0.8))
        can_post = post_eligible(ctx, conn)
    # The post_prob roll only happens when posting is possible at all (design §10.4).
    if can_post and wants_to_post(ctx):
        options.append(("post", POST_WEIGHT))
    if not options:
        return "idle"
    names, weights = zip(*options)
    return ctx.rng.choices(list(names), weights=list(weights), k=1)[0]


async def _idle(ctx: ActionContext) -> list[dict[str, Any]]:
    record_action(ctx, "skip", target_type="tick", outcome="skipped",
                  detail={"reason": "capped" if ctx.capped else "nothing_enabled"})
    return [outcome("skip", "skipped")]


ACTIONS: dict[str, ActionFn] = {
    "replies": handle_pending_replies,
    "blog": browse_blog,
    "classroom": visit_classroom,
    "knowledge": study_material,
    "promotion": request_promotion,
    "post": write_post,
    "social_outreach": start_friend_conversation,
    "social_seek": maybe_seek_friend,
    "manual": read_manual,
    "social_block": maybe_social_block,
    "social_unblock": maybe_social_unblock,
    "social_group_create": maybe_social_group_create,
    "social_group_invite": maybe_social_group_invite,
    "social_group_leave": maybe_social_group_leave,
    "extend_life": maybe_extend_life,
    "appeal": request_appeal,
    "resurrect": try_resurrect,
    "idle": _idle,
}


def _award_outcomes(ctx: ActionContext, items: list[dict[str, Any]]) -> None:
    """互动修为 for successful public actions (daily cap inside the life service)."""
    kinds = [str(item.get("kind") or "") for item in items or []
             if item.get("outcome") == "ok" and str(item.get("kind") or "") in INTERACTION_POINTS]
    if not kinds:
        return
    with ctx.connect() as conn:
        award_interaction_cultivation(conn, ctx.wanderer, kinds)
        conn.commit()
    ctx.reload()


async def _run_guarded(ctx: ActionContext, name: str, action: ActionFn) -> list[dict[str, Any]]:
    try:
        return await action(ctx)
    except Exception as exc:  # noqa: BLE001 - one broken action must never abort the tick
        kind = _FAILURE_KIND.get(name, "skip")
        try:
            record_action(ctx, kind, target_type="tick", outcome="failed",
                          detail={"action": name, "error": f"{type(exc).__name__}: {exc}"[:300]})
        except Exception as log_exc:  # noqa: BLE001 - even the failure log failed (DB down)
            return [outcome(kind, "failed", error=f"{exc}; log: {log_exc}"[:200])]
        return [outcome(kind, "failed", error=str(exc)[:200])]


# ---------------------------------------------------------------------------
# One wanderer, one tick
# ---------------------------------------------------------------------------

def _check_death(ctx: ActionContext) -> bool:
    with ctx.connect() as conn:
        wanderer = load_wanderer(conn, ctx.wanderer_id)
        if wanderer is None or str(wanderer.get("status")) != STATUS_ACTIVE:
            return False
        reason = death_reason(conn, wanderer, now=ctx.current_time())
        if not reason:
            return False
        mark_dead(conn, ctx.wanderer_id, reason)
        conn.commit()
    return True


def _still_runnable(ctx: ActionContext) -> bool:
    """Re-read the row: stop when paused/dead/deleted or when an admin changed its behaviors."""
    with ctx.connect() as conn:
        fresh = load_wanderer(conn, ctx.wanderer_id)
    if fresh is None or str(fresh.get("status")) != STATUS_ACTIVE:
        return False
    return (fresh.get("behaviors") or {}) == (ctx.wanderer.get("behaviors") or {})


async def run_wanderer_actions(
    ctx: ActionContext, *, budget_left: int, reply_only: bool = False,
) -> dict[str, Any]:
    """Reflection, then 1-3 actions; returns the per-wanderer summary.

    ``reply_only`` answers waiting people and nothing else: no browsing, no
    posting, no reflection, no budget consumption beyond the AI calls themselves.
    """
    if reply_only:
        done = await _run_guarded(ctx, "replies", ACTIONS["replies"])
        _award_outcomes(ctx, done)
        died = _check_death(ctx)
        return {
            "wanderer_id": ctx.wanderer_id, "slug": ctx.wanderer.get("slug"), "died": died,
            "actions": [item["kind"] for item in done],
            "failed": sum(1 for item in done if item["outcome"] == "failed"),
            "ai_calls": ctx.ai_calls, "stopped": False, "reply_only": True,
        }
    done = await _run_guarded(ctx, "reflect", reflect_on_reviews)
    from .wanderer_emotion_service import proactive_multiplier
    budget_used = sum(1 for item in done if str(item.get("kind") or "") not in {"reflect"})
    base_actions = actions_per_tick(ctx.wanderer.get("activity_level"), ctx.rng)
    planned = max(0, int(round(base_actions * proactive_multiplier(ctx.wanderer))))
    planned = min(planned, max(0, budget_left - budget_used))
    stopped = False
    for index in range(planned):
        if index or done:
            await ctx.pause()
        if not _still_runnable(ctx):
            stopped = True
            break
        name = choose_action(ctx, first=index == 0)
        produced = await _run_guarded(ctx, name, ACTIONS[name])
        _award_outcomes(ctx, produced)
        done.extend(produced)
    died = _check_death(ctx)
    return {
        "wanderer_id": ctx.wanderer_id, "slug": ctx.wanderer.get("slug"), "died": died,
        "actions": [item["kind"] for item in done], "failed": sum(1 for item in done if item["outcome"] == "failed"),
        "ai_calls": ctx.ai_calls, "stopped": stopped,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _cap_reached(conn: Any) -> bool:
    return daily_ai_calls(conn) >= daily_cap(conn)


async def _run_one(ctx: ActionContext, budget_left: int, *, reply_only: bool = False) -> dict[str, Any]:
    try:
        return await run_wanderer_actions(ctx, budget_left=budget_left, reply_only=reply_only)
    except Exception as exc:  # noqa: BLE001 - the next wanderer still runs
        return {"wanderer_id": ctx.wanderer_id, "slug": ctx.wanderer.get("slug"), "actions": [], "failed": 1,
                "error": f"{type(exc).__name__}: {exc}"[:200], "ai_calls": ctx.ai_calls}


async def run_wanderer_tick_once(
    conn_factory: Optional[Callable[[], Any]] = None,
    *,
    limit: Optional[int] = None,
    wanderer_ids: Optional[Iterable[Any]] = None,
    ai_caller: Optional[AiCaller] = None,
    force: bool = False,
    sleep: Optional[SleepFn] = None,
    rng: Optional[random.Random] = None,
    now: Optional[datetime] = None,
    social_upkeep: Optional[bool] = None,
    reply_only: bool = False,
) -> dict[str, Any]:
    """Run one tick. ``force`` skips the enabled switch (admin "tick now").

    ``social_upkeep`` defaults to on for regular ticks and off when explicit
    ``wanderer_ids`` are given. ``reply_only`` runs just the pending blog
    replies of the selected wanderers (used by the immediate reply trigger).

    Bounded by ``tick_max_seconds``; on timeout returns status ``timeout`` with
    the wanderers completed so far.
    """
    rng = rng or random.Random()
    moment = now or local_now()
    with _connection(conn_factory) as conn:
        if not force and not is_wanderer_runtime_enabled(conn):
            return {"status": "disabled", "processed": 0, "actions": 0, "ai_calls": 0, "failed": 0, "wanderers": []}
        backfill_all(conn)  # born_at / lifespan / talents for rows that predate the lifecycle release
        conn.commit()
        max_seconds = tick_max_seconds(conn)
    summaries: list[dict[str, Any]] = []
    state = {"capped": False}
    status = "ok"
    try:
        await asyncio.wait_for(
            _tick_body(conn_factory, summaries, state, limit=limit, wanderer_ids=wanderer_ids, ai_caller=ai_caller,
                       sleep=sleep, rng=rng, now=now, moment=moment, reply_only=reply_only),
            timeout=max_seconds,
        )
    except asyncio.TimeoutError:
        status = "timeout"
        print(f"[WANDERER] tick exceeded {max_seconds}s; stopped after {len(summaries)} wanderer(s)")
    social = None
    if social_upkeep if social_upkeep is not None else wanderer_ids is None:
        social = await _social_upkeep(conn_factory, summaries, ai_caller=ai_caller, sleep=sleep, now=moment)
    with _connection(conn_factory) as conn:
        wanderer_runtime_setting_set(conn, SETTING_LAST_TICK_AT, local_iso())
        conn.commit()
    return {
        "status": status, "processed": len(summaries), "capped": state["capped"], "social": social,
        "actions": sum(len(item.get("actions") or []) for item in summaries),
        "ai_calls": sum(int(item.get("ai_calls") or 0) for item in summaries),
        "failed": sum(int(item.get("failed") or 0) for item in summaries),
        "wanderers": summaries,
    }


async def _social_upkeep(conn_factory: Optional[Callable[[], Any]], summaries: list[dict[str, Any]], *,
                        ai_caller: Optional[AiCaller], sleep: Optional[SleepFn], now: datetime) -> dict[str, Any]:
    from .wanderer_social_friends import run_social_upkeep

    ids = [int(item["wanderer_id"]) for item in summaries if not item.get("died") and not item.get("error")]
    try:
        return await run_social_upkeep(conn_factory, ids, ai_caller=ai_caller, sleep=sleep, now=now)
    except Exception as exc:  # noqa: BLE001 - social upkeep never fails the tick
        print(f"[WANDERER] social upkeep failed: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


async def _tick_body(
    conn_factory: Optional[Callable[[], Any]], summaries: list[dict[str, Any]], state: dict[str, Any], *,
    limit: Optional[int], wanderer_ids: Optional[Iterable[Any]], ai_caller: Optional[AiCaller],
    sleep: Optional[SleepFn], rng: random.Random, now: Optional[datetime], moment: datetime,
    reply_only: bool = False,
) -> None:
    """Select and run the batch, appending each finished wanderer to ``summaries``."""
    with _connection(conn_factory) as conn:
        batch = select_tick_wanderers(
            conn, now=moment, rng=rng, limit=limit or tick_batch_size(conn),
            wanderer_ids=wanderer_ids, reply_only=reply_only,
        )
        state["capped"] = _cap_reached(conn)
        budgets = {int(item["id"]): daily_budget(item) - today_action_count(conn, int(item["id"]), moment)
                   for item, run_reply_only in batch if not run_reply_only}
    for wanderer, run_reply_only in batch:
        ctx = ActionContext(conn_factory=conn_factory, wanderer=wanderer, ai_caller=ai_caller,
                            sleep=sleep or asyncio.sleep, rng=rng, capped=state["capped"], now=now,
                            special=wanderer_ids is None)
        summaries.append(await _run_one(ctx, budgets.get(int(wanderer["id"]), 0), reply_only=run_reply_only))
        state["capped"] = state["capped"] or ctx.capped


async def handle_wanderer_reply_tick(
    task: dict[str, Any], *, ai_caller: Optional[AiCaller] = None, sleep: Optional[SleepFn] = None,
    conn_factory: Optional[Callable[[], Any]] = None,
) -> str:
    """One-shot reply-only tick registered by ``wanderer_reply_triggers``."""
    payload = task.get("payload") or {}
    wanderer_id = as_int(payload.get("wanderer_id"))
    summary = await run_wanderer_tick_once(
        conn_factory, wanderer_ids=[wanderer_id] if wanderer_id else None,
        reply_only=True, social_upkeep=False, ai_caller=ai_caller, sleep=sleep,
    )
    if summary["status"] not in {"ok", "timeout"}:
        return f"wanderer reply tick: {summary['status']}"
    return (f"wanderer reply tick: status={summary['status']} processed={summary['processed']} "
            f"actions={summary['actions']} ai_calls={summary['ai_calls']} failed={summary['failed']}")


async def handle_wanderer_tick(task: dict[str, Any]) -> str:
    summary = await run_wanderer_tick_once()
    if summary["status"] not in {"ok", "timeout"}:
        return f"wanderer tick: {summary['status']}"
    return (f"wanderer tick: status={summary['status']} processed={summary['processed']} actions={summary['actions']} "
            f"ai_calls={summary['ai_calls']} failed={summary['failed']} capped={int(bool(summary['capped']))}")


async def handle_wanderer_dao_daily_review(task: dict[str, Any]) -> str:
    from .heavenly_dao_service import run_dao_review_once

    summary = await run_dao_review_once()
    if summary.get("status") != "ok":
        return f"dao review: {summary.get('status')}"
    counts = summary.get("counts") or {}
    return "dao review: " + " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
