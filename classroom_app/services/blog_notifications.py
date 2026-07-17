from __future__ import annotations

from typing import Any, Optional

from .message_center_service import _build_notification_payload, _insert_notification

MESSAGE_CATEGORY_BLOG_COMMENT = "blog_comment"
MESSAGE_CATEGORY_BLOG_HOT = "blog_hot"
MESSAGE_CATEGORY_BLOG_CAREER = "blog_career"


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
        recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
        post_author_identity = str(post.get("author_identity") or "")

        if post_author_identity == commenter_identity:
            return

        # Blog crawler/editorial posts are authored by the platform assistant.
        # That identity has no user inbox and must never be passed to the
        # student/teacher-only message center identity builder.
        if recipient is None:
            return
        post_author_role, post_author_pk = recipient

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

        recipient = _resolve_notifiable_user(parent_row["author_role"], parent_row["author_user_pk"])
        if recipient is None:
            return
        parent_role, parent_pk = recipient

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
    recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
    if recipient is None:
        return
    author_role, author_pk = recipient

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
    recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
    if recipient is None:
        return
    author_role, author_pk = recipient

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


def notify_opportunity_deadline(conn, opportunity: dict[str, Any], user_state: dict[str, Any]) -> bool:
    recipient = _resolve_notifiable_user(user_state.get("user_role"), user_state.get("user_pk"))
    if recipient is None:
        return False
    recipient_role, recipient_pk = recipient
    post_id = _safe_int_pk(opportunity.get("post_id"))
    opportunity_id = _safe_int_pk(opportunity.get("id"))
    if post_id is None or opportunity_id is None:
        return False
    employer = str(opportunity.get("employer_name") or opportunity.get("post_title") or "就业机会")
    deadline_text = str(opportunity.get("deadline_at") or "")[:10]
    payload = _build_notification_payload(
        recipient_role=recipient_role,
        recipient_user_pk=recipient_pk,
        category=MESSAGE_CATEGORY_BLOG_CAREER,
        title="收藏的就业机会即将截止",
        body_preview=f"{employer} 的报名截止时间为 {deadline_text or '近期'}，请及时核验官方公告并准备材料。",
        actor_display_name="毕业新征程",
        link_url=f"/blog?section=career&post={post_id}",
        ref_type="blog_opportunity_deadline",
        ref_id=str(opportunity_id),
        metadata={"opportunity_id": opportunity_id, "deadline_at": opportunity.get("deadline_at")},
    )
    _insert_notification(conn, payload)
    return True


def _safe_int_pk(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_notifiable_user(role: Any, user_pk: Any) -> Optional[tuple[str, int]]:
    normalized_role = str(role or "").strip().lower()
    normalized_pk = _safe_int_pk(user_pk)
    if normalized_role not in {"student", "teacher"} or normalized_pk is None:
        return None
    return normalized_role, normalized_pk
