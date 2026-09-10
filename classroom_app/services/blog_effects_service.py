"""Commit blog mention delivery with the post/comment, then use the scheduler.

The existing blog_ai_reply_jobs row remains the publication receipt and guards
against duplicate replies. Scheduler retries recover interrupted generation.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

from .blog_ai_service import contains_blog_housekeeper_mention
from .scheduled_task_service import schedule_task


BLOG_MENTION_TASK_KIND = "blog_mention_reply"


def _trigger_snapshot(conn, trigger_type: str, trigger_id: int) -> dict[str, Any] | None:
    if trigger_type == "post":
        row = conn.execute("SELECT id, id AS post_id, author_identity, author_role, author_user_pk, title, content_md FROM blog_posts WHERE id=? AND status='published'", (int(trigger_id),)).fetchone()
    elif trigger_type == "comment":
        row = conn.execute("SELECT c.id,c.post_id,c.author_identity,c.author_role,c.author_user_pk,'' AS title,c.content_md FROM blog_comments c JOIN blog_posts p ON p.id=c.post_id WHERE c.id=? AND c.status='active' AND p.status='published'", (int(trigger_id),)).fetchone()
    else:
        raise ValueError("Invalid blog mention trigger")
    return dict(row) if row else None


def enqueue_blog_mention(conn, user: dict, *, trigger_type: str, trigger_id: int) -> int | None:
    """No commit or model work: caller owns the blog mutation transaction."""
    source = _trigger_snapshot(conn, trigger_type, trigger_id)
    identity = f"{user.get('role')}:{user.get('id')}"
    if not source or source["author_identity"] != identity:
        return None
    if not contains_blog_housekeeper_mention(str(source.get("title") or "") + "\n" + str(source.get("content_md") or "")):
        return None
    row = conn.execute("SELECT status,assistant_comment_id FROM blog_ai_reply_jobs WHERE trigger_type=? AND trigger_id=?", (trigger_type, int(trigger_id))).fetchone()
    if row and (row["status"] == "done" or row["assistant_comment_id"]):
        return None
    scheduled_id = schedule_task(conn, task_kind=BLOG_MENTION_TASK_KIND, run_at=datetime.now(),
                         payload={"trigger_type": trigger_type, "trigger_id": int(trigger_id), "actor_role": str(user["role"]), "actor_id": int(user["id"])},
                         dedupe_key=f"blog-mention:{trigger_type}:{int(trigger_id)}", owner_role=str(user["role"]), owner_user_pk=int(user["id"]),
                         title="博客 @管家 回复", priority=90, max_attempts=8, replace=False)
    # A new, authenticated edit may explicitly retry an exhausted effect, but
    # must never reset a live worker's claim or an existing published receipt.
    conn.execute("UPDATE scheduled_tasks SET status='pending',attempt_count=0,last_error='',next_attempt_at=NULL,locked_at=NULL,locked_by='',finished_at=NULL,run_at=?,updated_at=? WHERE id=? AND status IN ('failed','cancelled')", (datetime.now().isoformat(), datetime.now().isoformat(), scheduled_id))
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
    raise RuntimeError("Blog mention reply has no completed publication receipt")
