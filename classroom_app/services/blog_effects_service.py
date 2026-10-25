"""Commit blog mention delivery with the post/comment, then use the scheduler.

The existing blog_ai_reply_jobs row remains the publication receipt and guards
against duplicate replies. Scheduler retries recover interrupted generation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

from .blog_ai_service import BLOG_AI_JOB_ABANDONED, contains_blog_housekeeper_mention
from .scheduled_task_service import schedule_task


BLOG_MENTION_TASK_KIND = "blog_mention_reply"
_SANCTIONED_STATES = ("limited", "removed", "appeal_pending")


def housekeeper_daily_limit() -> int:
    try:
        return max(int(os.getenv("BLOG_HOUSEKEEPER_DAILY_LIMIT", "20")), 0)
    except ValueError:
        return 20


def housekeeper_quota_exhausted(conn, identity: str) -> bool:
    """Plan §5.3: each person may call @管家 a limited number of times a day."""
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_ai_reply_jobs WHERE trigger_author_identity = ? AND created_at >= ?",
        (identity, start),
    ).fetchone()
    return int((row["total"] if row else 0) or 0) >= housekeeper_daily_limit()


def _trigger_snapshot(conn, trigger_type: str, trigger_id: int) -> dict[str, Any] | None:
    if trigger_type == "post":
        row = conn.execute("SELECT id, id AS post_id, author_identity, author_role, author_user_pk, title, content_md, moderation_state FROM blog_posts WHERE id=? AND status='published'", (int(trigger_id),)).fetchone()
    elif trigger_type == "comment":
        row = conn.execute("SELECT c.id,c.post_id,c.author_identity,c.author_role,c.author_user_pk,'' AS title,c.content_md,CASE WHEN COALESCE(p.moderation_state,'none') IN ('limited','removed','appeal_pending') THEN p.moderation_state ELSE c.moderation_state END AS moderation_state FROM blog_comments c JOIN blog_posts p ON p.id=c.post_id WHERE c.id=? AND c.status='active' AND p.status='published'", (int(trigger_id),)).fetchone()
    else:
        raise ValueError("Invalid blog mention trigger")
    return dict(row) if row else None


def _enqueue_dao_mention_best_effort(conn, user: dict, *, trigger_type: str, trigger_id: int) -> None:
    """@天道 rides the same commit as the post/comment; a failure never blocks the author."""
    from ..db.savepoint import best_effort
    from .heavenly_dao_mention import enqueue_dao_mention

    with best_effort(conn, "dao_mention", log_prefix="[DAO]"):
        enqueue_dao_mention(conn, user, trigger_type=trigger_type, trigger_id=trigger_id)


def enqueue_blog_mention(conn, user: dict, *, trigger_type: str, trigger_id: int) -> int | None:
    """No commit or model work: caller owns the blog mutation transaction.

    Also schedules the @天道 reply (separate channel) when the trigger mentions it.
    """
    _enqueue_dao_mention_best_effort(conn, user, trigger_type=trigger_type, trigger_id=trigger_id)
    source = _trigger_snapshot(conn, trigger_type, trigger_id)
    identity = f"{user.get('role')}:{user.get('id')}"
    if not source or source["author_identity"] != identity:
        return None
    if not contains_blog_housekeeper_mention(str(source.get("title") or "") + "\n" + str(source.get("content_md") or "")):
        return None
    row = conn.execute("SELECT status,assistant_comment_id,updated_at FROM blog_ai_reply_jobs WHERE trigger_type=? AND trigger_id=?", (trigger_type, int(trigger_id))).fetchone()
    if row and (row["status"] == "done" or row["assistant_comment_id"]):
        return None
    if row is None and housekeeper_quota_exhausted(conn, identity):
        return None
    rearmable_statuses = ("failed", "cancelled")
    if row and row["status"] == BLOG_AI_JOB_ABANDONED:
        # A reported failure is only retried after the author re-saves the post;
        # comments are immutable, so a new comment is their retry path.
        edited = conn.execute("SELECT edited_at FROM blog_posts WHERE id=?", (int(trigger_id),)).fetchone() if trigger_type == "post" else None
        if not edited or str(edited["edited_at"] or "") <= str(row["updated_at"] or ""):
            return None
        conn.execute("UPDATE blog_ai_reply_jobs SET status='failed',updated_at=? WHERE trigger_type=? AND trigger_id=? AND status=?", (datetime.now().isoformat(), trigger_type, int(trigger_id), BLOG_AI_JOB_ABANDONED))
        # The abandoned attempt finished its scheduler task as "done"; that
        # task must run again, or the re-armed job would wait forever.
        rearmable_statuses = ("failed", "cancelled", "done")
    scheduled_id = schedule_task(conn, task_kind=BLOG_MENTION_TASK_KIND, run_at=datetime.now(),
                         payload={"trigger_type": trigger_type, "trigger_id": int(trigger_id), "actor_role": str(user["role"]), "actor_id": int(user["id"])},
                         dedupe_key=f"blog-mention:{trigger_type}:{int(trigger_id)}", owner_role=str(user["role"]), owner_user_pk=int(user["id"]),
                         title="博客 @管家 回复", priority=90, max_attempts=8, replace=False)
    # A new, authenticated edit may explicitly retry an exhausted effect, but
    # must never reset a live worker's claim or an existing published receipt.
    placeholders = ", ".join("?" for _ in rearmable_statuses)
    conn.execute(f"UPDATE scheduled_tasks SET status='pending',attempt_count=0,last_error='',next_attempt_at=NULL,locked_at=NULL,locked_by='',finished_at=NULL,run_at=?,updated_at=? WHERE id=? AND status IN ({placeholders})", (datetime.now().isoformat(), datetime.now().isoformat(), scheduled_id, *rearmable_statuses))
    return scheduled_id


async def handle_blog_mention_reply(task: dict[str, Any]) -> str:
    from ..database import get_db_connection
    from .agent_actor_service import resolve_agent_actor
    from .blog_ai_service import maybe_reply_to_comment_mention, maybe_reply_to_post_mention
    from .scheduled_task_service import SCHEDULER_STALE_MINUTES

    payload = task.get("payload") or {}
    trigger_type, trigger_id = str(payload.get("trigger_type") or ""), int(payload.get("trigger_id") or 0)
    with get_db_connection() as conn:
        source = _trigger_snapshot(conn, trigger_type, trigger_id)
        state = str((source or {}).get("moderation_state") or "none")
        if state == "pending":
            # Reply only once the trigger is public; the scheduler retries with backoff.
            raise RuntimeError("Blog mention waits for the content review")
        if state in _SANCTIONED_STATES:
            source = None
        if not source or not contains_blog_housekeeper_mention(str(source.get("title") or "") + "\n" + str(source.get("content_md") or "")):
            conn.execute("UPDATE blog_ai_reply_jobs SET status='failed',error_message='原发言已删除、隐藏或取消提及',updated_at=? WHERE trigger_type=? AND trigger_id=? AND status='pending' AND assistant_comment_id IS NULL", (datetime.now().isoformat(), trigger_type, trigger_id))
            conn.commit()
            return "skipped: blog mention is no longer active"
        try:
            actor = resolve_agent_actor(conn, str(payload.get("actor_role") or ""), payload.get("actor_id"))
        except HTTPException:
            conn.execute("UPDATE blog_ai_reply_jobs SET status='failed',error_message='原作者账号已不可用',updated_at=? WHERE trigger_type=? AND trigger_id=? AND status='pending' AND assistant_comment_id IS NULL", (datetime.now().isoformat(), trigger_type, trigger_id))
            conn.commit()
            raise
        if source["author_identity"] != actor.key:
            raise PermissionError("Blog mention author changed")
        job = conn.execute("SELECT * FROM blog_ai_reply_jobs WHERE trigger_type=? AND trigger_id=?", (trigger_type, trigger_id)).fetchone()
        if job and (job["status"] == "done" or job["assistant_comment_id"]):
            return "done: existing blog reply receipt"
        if job and job["status"] == BLOG_AI_JOB_ABANDONED:
            return "abandoned: caller was notified privately"
        if job and job["status"] == "pending":
            cutoff = (datetime.now() - timedelta(minutes=SCHEDULER_STALE_MINUTES)).isoformat()
            cursor = conn.execute("UPDATE blog_ai_reply_jobs SET status='failed',error_message='回复任务中断，正在重试',updated_at=? WHERE id=? AND status='pending' AND assistant_comment_id IS NULL AND updated_at<=?", (datetime.now().isoformat(), job["id"], cutoff))
            if cursor.rowcount != 1:
                raise RuntimeError("Blog mention reply is still running; retry later")
            conn.commit()
    if trigger_type == "post":
        await maybe_reply_to_post_mention(trigger_id, actor.as_user())
    else:
        await maybe_reply_to_comment_mention(trigger_id, actor.as_user())
    with get_db_connection() as conn:
        job = conn.execute("SELECT status,assistant_comment_id FROM blog_ai_reply_jobs WHERE trigger_type=? AND trigger_id=?", (trigger_type, trigger_id)).fetchone()
    if job and (job["status"] == "done" or job["assistant_comment_id"]):
        return "done: blog reply published"
    if job and job["status"] == BLOG_AI_JOB_ABANDONED:
        return "abandoned: caller was notified privately"
    raise RuntimeError("Blog mention reply has no completed publication receipt")
