"""被评论 / 被回复 / 被 @ 时的即时回复任务（docs/wanderer-reply-priority-2026-10-28.md）。

作息只限制散修**主动探寻平台**：逛博客、旁听课堂、主动找人聊天。被真实学生问到
（博客评论、回复、@、社交私信、课堂私信、群里被 @）时必须马上回，而且不计入每日
互动额度和会话/群发言上限——但仍然要花一次 AI 调用，所以 ``daily_ai_call_cap``
这个成本上限照旧生效（配额用尽按原有降级处理）。

私信与课堂私信由 ``wanderer_inbox_worker`` 的 2 秒轮询独立处理；这里只补博客
通知这一条仍然挂在 10 分钟节拍上的通道：通知创建时排一次 reply-only 节拍，
只跑回复动作，忽略作息与每日动作预算。同一个散修的连续通知合并成一个待办
（``wanderer:reply-tick:<id>``），pending 的保持原时间，已结束的重新排期。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

TASK_KIND_REPLY_TICK = "wanderer_reply_tick"
REPLY_TICK_DELAY_SECONDS = 3
REPLY_TICK_MAX_ATTEMPTS = 3
TASK_PRIORITY = 115
REPLY_BEHAVIOR = "reply"
DM_BEHAVIOR = "dm_reply"


def reply_tick_dedupe_key(wanderer_id: Any) -> str:
    return f"wanderer:reply-tick:{int(wanderer_id)}"


def _active_wanderer(conn: Any, student_id: Any) -> Optional[dict[str, Any]]:
    from .wanderer_identity_service import STATUS_ACTIVE, load_wanderers_by_student_ids

    try:
        pk = int(student_id or 0)
    except (TypeError, ValueError):
        return None
    if pk <= 0:
        return None
    wanderer = load_wanderers_by_student_ids(conn, [pk]).get(pk)
    if wanderer is None or str(wanderer.get("status") or "") != STATUS_ACTIVE:
        return None
    return wanderer


def _behavior_on(conn: Any, wanderer: dict[str, Any], key: str) -> bool:
    from .wanderer_actions_common import behavior_on

    return behavior_on(wanderer, key)


def _is_wanderer_actor(conn: Any, role: Any, user_pk: Any) -> bool:
    """AI 对 AI 不排即时回复，避免两个散修互相唤醒形成循环。"""
    from .wanderer_identity_service import load_wanderers_by_student_ids

    if str(role or "") != "student":
        return False
    try:
        pk = int(user_pk or 0)
    except (TypeError, ValueError):
        return False
    return bool(pk > 0 and load_wanderers_by_student_ids(conn, [pk]))


def schedule_reply_tick(
    conn: Any,
    wanderer: dict[str, Any],
    *,
    reason: str,
    delay_seconds: float = REPLY_TICK_DELAY_SECONDS,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Register (or coalesce) one reply-only tick. Caller commits; never raises."""
    from .wanderer_settings_service import is_wanderer_runtime_enabled

    try:
        if not is_wanderer_runtime_enabled(conn):
            return None
        key = reply_tick_dedupe_key(wanderer["id"])
        from .scheduled_task_service import get_owner_task_by_dedupe, schedule_task

        moment = now or datetime.now()
        existing = get_owner_task_by_dedupe(conn, key)
        if existing and str(existing.get("status") or "") == "pending":
            return int(existing["id"])
        return schedule_task(
            conn, task_kind=TASK_KIND_REPLY_TICK,
            run_at=moment + timedelta(seconds=max(0.0, float(delay_seconds))),
            payload={"wanderer_id": int(wanderer["id"]), "reason": str(reason or "")[:60]},
            dedupe_key=key, owner_role="system", title=f"散修即时回复 · {wanderer.get('display_name') or ''}",
            priority=TASK_PRIORITY, max_attempts=REPLY_TICK_MAX_ATTEMPTS, replace=True,
        )
    except Exception as exc:  # noqa: BLE001 — 触发失败不能拖垮评论的写入
        print(f"[WANDERER] reply tick schedule failed: {type(exc).__name__}: {exc}")
        return None


def on_blog_notification(
    conn: Any, *, recipient_pk: Any, actor_role: Any = "", actor_pk: Any = None,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """A blog comment/reply/mention reached a 散修; schedule its reply tick."""
    recipient = _active_wanderer(conn, recipient_pk)
    if recipient is None:
        return None
    if _is_wanderer_actor(conn, actor_role, actor_pk):
        return None
    if not _behavior_on(conn, recipient, REPLY_BEHAVIOR) and not _behavior_on(conn, recipient, DM_BEHAVIOR):
        return None
    return schedule_reply_tick(conn, recipient, reason="blog", now=now)
