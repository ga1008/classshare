from __future__ import annotations

from typing import Any, Optional

from .message_center_service import _build_notification_payload, _insert_notification

MESSAGE_CATEGORY_BLOG_COMMENT = "blog_comment"
MESSAGE_CATEGORY_BLOG_HOT = "blog_hot"


def notify_new_comment(
    conn,
    post: dict,
    comment_id: int,
    parent_comment_id: Optional[int],
    commenter_identity: str,
    commenter_role: str,
    commenter_pk: int,
    commenter_name: str,
    comment_preview: str,
) -> None:
    preview = (comment_preview or "")[:120]
    post_id = post["id"]
    post_title = post.get("title", "")
    link_url = f"/blog?post={post_id}"

    if parent_comment_id is None:
        post_author_pk = _safe_int_pk(post.get("author_user_pk"))
        post_author_role = str(post.get("author_role") or "")
        post_author_identity = str(post.get("author_identity") or "")

        if post_author_identity == commenter_identity:
            return

        if post_author_pk is None:
            return

        payload = _build_notification_payload(
            recipient_role=post_author_role,
            recipient_user_pk=post_author_pk,
            category=MESSAGE_CATEGORY_BLOG_COMMENT,
            title=f"{commenter_name} 评论了你的帖子",
            body_preview=preview,
            actor_role=commenter_role,
            actor_user_pk=commenter_pk,
            actor_display_name=commenter_name,
            link_url=link_url,
            ref_type="blog_comment",
            ref_id=str(comment_id),
        )
        _insert_notification(conn, payload)
    else:
        parent_row = conn.execute(
            "SELECT author_identity, author_role, author_user_pk, author_display_name FROM blog_comments WHERE id = ?",
            (parent_comment_id,),
        ).fetchone()
        if parent_row is None:
            return

        parent_identity = str(parent_row["author_identity"] or "")
        if parent_identity == commenter_identity:
            return

        parent_pk = _safe_int_pk(parent_row["author_user_pk"])
        parent_role = str(parent_row["author_role"] or "")
        if parent_pk is None:
            return

        payload = _build_notification_payload(
            recipient_role=parent_role,
            recipient_user_pk=parent_pk,
            category=MESSAGE_CATEGORY_BLOG_COMMENT,
            title=f"{commenter_name} 回复了你的评论",
            body_preview=preview,
            actor_role=commenter_role,
            actor_user_pk=commenter_pk,
            actor_display_name=commenter_name,
            link_url=link_url,
            ref_type="blog_comment",
            ref_id=str(comment_id),
        )
        _insert_notification(conn, payload)


def notify_post_featured(
    conn,
    post: dict,
    moderator_identity: str,
    moderator_role: str,
    moderator_pk: int,
) -> None:
    author_pk = _safe_int_pk(post.get("author_user_pk"))
    author_role = str(post.get("author_role") or "")
    if author_pk is None:
        return

    post_id = post["id"]
    post_title = post.get("title", "")

    payload = _build_notification_payload(
        recipient_role=author_role,
        recipient_user_pk=author_pk,
        category=MESSAGE_CATEGORY_BLOG_HOT,
        title="你的帖子被设为精华",
        body_preview=f"「{post_title}」已被设为精华帖",
        actor_role=moderator_role,
        actor_user_pk=moderator_pk,
        actor_display_name="",
        link_url=f"/blog?post={post_id}",
        ref_type="blog_post",
        ref_id=str(post_id),
    )
    _insert_notification(conn, payload)


def notify_post_hot(
    conn,
    post: dict,
    *,
    score: int,
) -> None:
    author_pk = _safe_int_pk(post.get("author_user_pk"))
    author_role = str(post.get("author_role") or "")
    if author_pk is None:
        return

    post_id = post["id"]
    post_title = post.get("title", "")

    payload = _build_notification_payload(
        recipient_role=author_role,
        recipient_user_pk=author_pk,
        category=MESSAGE_CATEGORY_BLOG_HOT,
        title="你的帖子进入热门",
        body_preview=f"「{post_title}」正在被更多人看到，当前热度分 {int(score)}",
        actor_role="",
        actor_user_pk=None,
        actor_display_name="博客中心",
        link_url=f"/blog?post={post_id}",
        ref_type="blog_post",
        ref_id=str(post_id),
    )
    _insert_notification(conn, payload)


def _safe_int_pk(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
