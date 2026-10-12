"""Blog notification events (docs/blog-closed-loop-plan-2026-10-12.md §6).

Every blog event goes through ``notification_delivery.deliver`` with an event
key from ``BLOG_EVENTS``, so mute switches, aggregation, read-by-post and
stale-target marking work the same for all of them.

Rows store a structured ``metadata`` (event, post, comment, actor reference
and a title template) rather than relying on the name written at the time.
``render_notifications`` re-renders the actor at read time through the
persona service: if an author later makes a post anonymous, older cards show
"楼主" too. Writers still store the public face as ``actor_display_name`` for
keyword search and older clients, never a hidden real name.

Notifications are a side effect: callers run them through ``best_effort`` so a
failure here never undoes the like, comment, follow or post that caused it.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Iterable, Optional

from . import blog_persona_service as persona_service
from . import notification_delivery as delivery
from .message_center_service import _build_notification_payload

logger = logging.getLogger(__name__)

MESSAGE_CATEGORY_BLOG_COMMENT = "blog_comment"
MESSAGE_CATEGORY_BLOG_HOT = "blog_hot"
MESSAGE_CATEGORY_BLOG_CAREER = "blog_career"
MESSAGE_CATEGORY_BLOG_MODERATION = "blog_moderation"
MESSAGE_CATEGORY_BLOG_LIKE = "blog_like"
MESSAGE_CATEGORY_BLOG_FOLLOW = "blog_follow"
BLOG_CATEGORIES = (
    MESSAGE_CATEGORY_BLOG_COMMENT, MESSAGE_CATEGORY_BLOG_LIKE, MESSAGE_CATEGORY_BLOG_FOLLOW,
    MESSAGE_CATEGORY_BLOG_HOT, MESSAGE_CATEGORY_BLOG_MODERATION, MESSAGE_CATEGORY_BLOG_CAREER,
)
# Opening a post settles these; moderation outcomes must be read deliberately.
READ_BY_POST_CATEGORIES = tuple(item for item in BLOG_CATEGORIES if item != MESSAGE_CATEGORY_BLOG_MODERATION)
SYSTEM_ACTOR = "博客中心"
THREAD_REPLY_WINDOW_SECONDS = 3600
MAX_MENTIONS = 10

EVENT_COMMENT = "blog.comment"
EVENT_REPLY = "blog.reply"
EVENT_THREAD_REPLY = "blog.thread_reply"
EVENT_MENTION = "blog.mention"
EVENT_LIKE_POST = "blog.like_post"
EVENT_LIKE_COMMENT = "blog.like_comment"
EVENT_FOLLOW = "blog.follow"
EVENT_FEATURED = "blog.featured"
EVENT_HOT = "blog.hot"
EVENT_MODERATION = "blog.moderation"
EVENT_APPEAL_RESULT = "blog.appeal_result"
EVENT_REPORT_RESULT = "blog.report_result"
EVENT_REVIEW_PASSED = "blog.review_passed"
EVENT_HOUSEKEEPER_FAILED = "blog.housekeeper_failed"
EVENT_CAREER_DEADLINE = "blog.career_deadline"

# The plan §6.1 catalog. Every event defaults on; forced ones cannot be muted.
BLOG_EVENTS: tuple[dict[str, Any], ...] = (
    {"key": EVENT_COMMENT, "label": "评论了我的帖子", "help": ""},
    {"key": EVENT_REPLY, "label": "回复了我的评论", "help": ""},
    {"key": EVENT_THREAD_REPLY, "label": "我的帖子下有新的楼中楼回复", "help": "接连到来的回复合并为一条，间隔超过 1 小时另起一条"},
    {"key": EVENT_MENTION, "label": "@了我", "help": "同一个人在同一帖子里多次 @ 你，合并为一条"},
    {"key": EVENT_LIKE_POST, "label": "赞了我的帖子", "help": "只显示人数，同一帖子按天合并"},
    {"key": EVENT_LIKE_COMMENT, "label": "赞了我的评论", "help": "只显示人数，同一评论按天合并"},
    {"key": EVENT_FOLLOW, "label": "关注了我", "help": "按天合并"},
    {"key": EVENT_FEATURED, "label": "帖子被设为精华", "help": ""},
    {"key": EVENT_HOT, "label": "帖子进入热门", "help": ""},
    {"key": EVENT_REVIEW_PASSED, "label": "帖子审核通过", "help": "等待超过 30 秒才会提醒"},
    {"key": EVENT_REPORT_RESULT, "label": "我的举报处理结果", "help": ""},
    {"key": EVENT_HOUSEKEEPER_FAILED, "label": "管家没能回复", "help": ""},
    {"key": EVENT_CAREER_DEADLINE, "label": "收藏的就业机会即将截止", "help": ""},
    {"key": EVENT_MODERATION, "label": "内容被限制、移除或恢复", "help": "涉及你的内容，始终通知", "forced": True},
    {"key": EVENT_APPEAL_RESULT, "label": "申诉结果", "help": "始终通知", "forced": True},
)
FORCED_EVENTS = frozenset(item["key"] for item in BLOG_EVENTS if item.get("forced"))
EVENT_KEYS = frozenset(item["key"] for item in BLOG_EVENTS)
# Events whose card can carry an inline reply (plan §7.5).
REPLYABLE_EVENTS = frozenset({EVENT_COMMENT, EVENT_REPLY, EVENT_MENTION})


def best_effort(conn, action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a notification side effect without risking the caller's write.

    A savepoint isolates it: on PostgreSQL a failed statement would otherwise
    abort the whole transaction (and with it the like or comment).
    """
    try:
        conn.execute("SAVEPOINT blog_notify")
    except Exception:  # noqa: BLE001 - adapters without savepoints just run the action
        return _run_unguarded(action, *args, **kwargs)
    try:
        result = action(*args, **kwargs)
    except Exception:  # noqa: BLE001 - logged; the user's action stands
        logger.exception("blog notification failed: %s", getattr(action, "__name__", action))
        conn.execute("ROLLBACK TO SAVEPOINT blog_notify")
        conn.execute("RELEASE SAVEPOINT blog_notify")
        return None
    conn.execute("RELEASE SAVEPOINT blog_notify")
    return result


def _run_unguarded(action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return action(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("blog notification failed: %s", getattr(action, "__name__", action))
        return None


def _send(conn, payload: dict[str, Any], *, event: str, post_id: Optional[int] = None, **options: Any) -> int:
    return delivery.deliver(
        conn, payload, event_key=event, forced=event in FORCED_EVENTS,
        ref_post_id=int(post_id) if post_id is not None else None, **options,
    )


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


def _identity_of(role: str, user_pk: int) -> str:
    return f"{role}:{int(user_pk)}"


def _can_see_post(conn, role: str, user_pk: int, post: dict) -> bool:
    """Plan §6.1: never notify someone about content they cannot open."""
    from .blog_service import _can_view_post

    try:
        return bool(_can_view_post(conn, {"id": int(user_pk), "role": role}, post))
    except (ValueError, PermissionError):
        return False


def _metadata(event: str, post_id: int, *, comment_id: Optional[int] = None, actor: Optional[dict] = None,
              template: str = "", **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"event": event, "post_id": int(post_id)}
    if comment_id:
        data["comment_id"] = int(comment_id)
    if actor:
        data["actor"] = actor
    if template:
        data["title_template"] = template
    data.update(extra)
    return data


def _post_link(post_id: int, comment_id: Optional[int] = None) -> str:
    anchor = f"&comment={int(comment_id)}" if comment_id else ""
    return f"/blog?post={int(post_id)}{anchor}"


def _load_comment(conn, comment_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, post_id, parent_comment_id, root_comment_id, author_identity, author_role, author_user_pk, "
        "author_display_name, author_display_mode, author_display_snapshot, content_md, status, moderation_state "
        "FROM blog_comments WHERE id = ?",
        (int(comment_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_post(conn, post_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM blog_posts WHERE id = ? LIMIT 1", (int(post_id),)).fetchone()
    return dict(row) if row else None


# --- comments, replies and mentions -----------------------------------------

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
    """``commenter_role``/``commenter_pk`` are empty unless the comment is
    real-named, so a pen-name or anonymous commenter is never contactable."""
    preview = (comment_preview or "")[:120]
    post_id = int(post["id"])
    actor = {"type": "comment", "id": int(comment_id)}
    common = dict(
        body_preview=preview, actor_role=commenter_role, actor_user_pk=commenter_pk,
        actor_display_name=commenter_name, link_url=_post_link(post_id, comment_id),
        ref_type="blog_comment", ref_id=str(comment_id),
    )
    post_author_identity = str(post.get("author_identity") or "")

    if parent_comment_id is None:
        if post_author_identity == commenter_identity:
            return
        # Blog crawler/editorial posts are authored by the platform assistant,
        # which has no user inbox.
        recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
        if recipient is None or not _can_see_post(conn, *recipient, post):
            return
        template = "{actor} 评论了你的帖子"
        _send(conn, _build_notification_payload(
            recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_COMMENT,
            title=template.replace("{actor}", commenter_name),
            metadata=_metadata(EVENT_COMMENT, post_id, comment_id=comment_id, actor=actor, template=template),
            **common,
        ), event=EVENT_COMMENT, post_id=post_id)
        return

    parent_row = conn.execute(
        "SELECT author_identity, author_role, author_user_pk, author_display_name FROM blog_comments WHERE id = ?",
        (parent_comment_id,),
    ).fetchone()
    if parent_row is None:
        return
    parent_identity = str(parent_row["author_identity"] or "")
    if parent_identity != commenter_identity:
        recipient = _resolve_notifiable_user(parent_row["author_role"], parent_row["author_user_pk"])
        if recipient is not None and _can_see_post(conn, *recipient, post):
            template = "{actor} 回复了你的评论"
            _send(conn, _build_notification_payload(
                recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_COMMENT,
                title=template.replace("{actor}", commenter_name),
                metadata=_metadata(EVENT_REPLY, post_id, comment_id=comment_id, actor=actor, template=template,
                                   parent_comment_id=int(parent_comment_id)),
                **common,
            ), event=EVENT_REPLY, post_id=post_id)

    # The post author hears about replies inside floors as one rolling card,
    # unless the reply was to them (above) or by them.
    if post_author_identity in {commenter_identity, parent_identity}:
        return
    recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
    if recipient is None or not _can_see_post(conn, *recipient, post):
        return
    template = "你的帖子下有 {count} 条新回复"
    _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_COMMENT,
        title=template, body_preview=preview, actor_display_name="",
        link_url=_post_link(post_id, comment_id), ref_type="blog_post_replies", ref_id=str(post_id),
        metadata=_metadata(EVENT_THREAD_REPLY, post_id, comment_id=comment_id, template=template),
    ), event=EVENT_THREAD_REPLY, post_id=post_id, aggregate_key=f"{EVENT_THREAD_REPLY}:{post_id}",
        aggregate_window_seconds=THREAD_REPLY_WINDOW_SECONDS, aggregate_recent=int(comment_id))


def _face_name(mode: str, profile: dict, snapshot: str) -> str:
    if mode == persona_service.MODE_PEN:
        return str(profile.get("nickname") or snapshot or "").strip()
    return str(profile.get("name") or snapshot or "").strip()


def _mention_candidates(conn, post: dict, actor_identity: str) -> dict[str, str]:
    """Who can be @-mentioned here: name -> identity.

    Only faces already public in this thread (real names and pen names of the
    post author and commenters) and the actor's followed authors. Anonymous
    voices are never candidates, so a mention cannot unmask anyone. A name
    shared by two people is ambiguous and pings nobody.
    """
    from .blog_moderation_states import public_state_sql

    faces: list[tuple[str, str, str]] = []  # (identity, mode, snapshot)
    post_mode = str(post.get("author_display_mode") or persona_service.MODE_REAL)
    if post_mode in {persona_service.MODE_REAL, persona_service.MODE_PEN}:
        faces.append((str(post.get("author_identity") or ""), post_mode, str(post.get("author_display_name") or "")))
    rows = conn.execute(
        f"""
        SELECT DISTINCT c.author_identity, c.author_display_mode, c.author_display_snapshot
        FROM blog_comments c
        WHERE c.post_id = ? AND c.status = 'active' AND {public_state_sql('c')}
          AND c.author_display_mode IN ('real_name', 'nickname')
        """,
        (int(post["id"]),),
    ).fetchall()
    faces.extend((str(r["author_identity"] or ""), str(r["author_display_mode"] or ""),
                  str(r["author_display_snapshot"] or "")) for r in rows)
    followed = conn.execute(
        "SELECT target_key FROM blog_follows WHERE user_identity = ? AND target_type = 'author'",
        (str(actor_identity or ""),),
    ).fetchall()
    faces.extend((str(r["target_key"] or ""), persona_service.MODE_REAL, "") for r in followed)
    people = [face for face in faces if face[0].split(":", 1)[0] in {"student", "teacher"}]
    profiles = persona_service.load_profiles(conn, {face[0] for face in people})
    owners: dict[str, set[str]] = {}
    for identity, mode, snapshot in people:
        name = _face_name(mode, profiles.get(identity) or {}, snapshot)
        if name and name != "管家":
            owners.setdefault(name, set()).add(identity)
    return {name: next(iter(ids)) for name, ids in owners.items() if len(ids) == 1}


def find_mentions(text: str, candidates: dict[str, str]) -> list[str]:
    """Identities @-mentioned in ``text`` (longest name wins, no separators needed)."""
    names = sorted(candidates, key=len, reverse=True)
    found: list[str] = []
    source = str(text or "")
    position = source.find("@")
    while position != -1 and len(found) < MAX_MENTIONS:
        for name in names:
            if source.startswith(name, position + 1):
                identity = candidates[name]
                if identity not in found:
                    found.append(identity)
                break
        position = source.find("@", position + 1)
    return found


def _already_told_about_comment(conn, recipient: tuple[str, int], comment_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM message_center_notifications WHERE recipient_role = ? AND recipient_user_pk = ? "
        "AND category = ? AND ref_type = 'blog_comment' AND ref_id = ? LIMIT 1",
        (recipient[0], recipient[1], MESSAGE_CATEGORY_BLOG_COMMENT, str(comment_id)),
    ).fetchone()
    return row is not None


def notify_mentions(conn, post: dict, *, actor_identity: str, text: str, comment_id: Optional[int] = None,
                    actor_role: str = "", actor_pk: Any = None, actor_name: str = "") -> int:
    """``@某人`` in a public comment or post (plan §6.1 ``blog.mention``)."""
    if "@" not in str(text or ""):
        return 0
    post_id = int(post["id"])
    where = "评论" if comment_id else "帖子"
    template = f"{{actor}} 在{where}中提到了你"
    actor = {"type": "comment", "id": int(comment_id)} if comment_id else {"type": "post", "id": post_id}
    sent = 0
    for identity in find_mentions(text, _mention_candidates(conn, post, actor_identity)):
        if identity == actor_identity:
            continue
        role, _, raw_pk = identity.partition(":")
        recipient = _resolve_notifiable_user(role, raw_pk)
        if recipient is None or not _can_see_post(conn, *recipient, post):
            continue
        if comment_id and _already_told_about_comment(conn, recipient, int(comment_id)):
            continue  # The reply/comment card for this very comment already reached them.
        payload = _build_notification_payload(
            recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_COMMENT,
            title=template.replace("{actor}", actor_name or "有人"), body_preview=str(text or "")[:120],
            actor_role=actor_role, actor_user_pk=actor_pk, actor_display_name=actor_name,
            link_url=_post_link(post_id, comment_id),
            ref_type="blog_comment" if comment_id else "blog_post_mention",
            ref_id=str(comment_id or post_id),
            metadata=_metadata(EVENT_MENTION, post_id, comment_id=comment_id, actor=actor, template=template),
        )
        # Repeated @ from one person in one thread fold into one unread card
        # ("…（3 次）"), so mentions cannot flood anyone; a post mentions each
        # person once (dedupe by post).
        options = ({"aggregate_key": f"{EVENT_MENTION}:{post_id}:{delivery.member_token(actor_identity)}"}
                   if comment_id else {})
        sent += 1 if _send(conn, payload, event=EVENT_MENTION, post_id=post_id, **options) else 0
    return sent


def notify_comment_mentions(conn, comment_id: int) -> int:
    comment = _load_comment(conn, comment_id)
    post = _load_post(conn, int(comment["post_id"])) if comment else None
    if comment is None or post is None:
        return 0
    real = str(comment.get("author_display_mode") or "") == persona_service.MODE_REAL
    return notify_mentions(
        conn, post, actor_identity=str(comment.get("author_identity") or ""), text=str(comment.get("content_md") or ""),
        comment_id=int(comment["id"]), actor_role=str(comment.get("author_role") or "") if real else "",
        actor_pk=comment.get("author_user_pk") if real else None,
        actor_name=str(comment.get("author_display_name") or ""),
    )


def notify_post_mentions(conn, post_id: int) -> int:
    """Called whenever a post is (again) public; each person is told once per post."""
    from . import blog_moderation_states as states

    post = _load_post(conn, post_id)
    if post is None or str(post.get("status") or "") != "published" or not states.is_public_state(
            post.get("moderation_state")):
        return 0
    mode = str(post.get("author_display_mode") or persona_service.MODE_REAL)
    real = mode == persona_service.MODE_REAL
    return notify_mentions(
        conn, post, actor_identity=str(post.get("author_identity") or ""),
        text=f"{post.get('title') or ''}\n{post.get('content_md') or ''}",
        actor_role=str(post.get("author_role") or "") if real else "",
        actor_pk=post.get("author_user_pk") if real else None,
        actor_name="匿名作者" if mode == persona_service.MODE_ANONYMOUS else str(post.get("author_display_name") or ""),
    )


# --- likes and follows (count only, D10/D11) ---------------------------------

def notify_like(conn, *, target_type: str, target_id: int, liker_identity: str) -> int:
    if target_type == "post":
        post = _load_post(conn, target_id)
        if post is None:
            return 0
        owner = (post.get("author_identity"), post.get("author_role"), post.get("author_user_pk"))
        post_id, comment_id = int(post["id"]), None
        event, noun, body = EVENT_LIKE_POST, "帖子", f"「{post.get('title') or '无标题'}」"
    else:
        comment = _load_comment(conn, target_id)
        post = _load_post(conn, int(comment["post_id"])) if comment else None
        if comment is None or post is None:
            return 0
        owner = (comment.get("author_identity"), comment.get("author_role"), comment.get("author_user_pk"))
        post_id, comment_id = int(post["id"]), int(comment["id"])
        event, noun, body = EVENT_LIKE_COMMENT, "评论", str(comment.get("content_md") or "")[:120]
    if str(owner[0] or "") == str(liker_identity or ""):
        return 0
    recipient = _resolve_notifiable_user(owner[1], owner[2])
    if recipient is None or not _can_see_post(conn, *recipient, post):
        return 0
    template = f"{{count}} 人赞了你的{noun}"
    return _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_LIKE,
        title=template, body_preview=body, actor_display_name=SYSTEM_ACTOR,
        link_url=_post_link(post_id, comment_id),
        ref_type="blog_comment_like" if comment_id else "blog_post_like", ref_id=str(comment_id or post_id),
        metadata=_metadata(event, post_id, comment_id=comment_id, template=template),
    ), event=event, post_id=post_id, aggregate_key=f"{event}:{comment_id or post_id}:{date.today().isoformat()}",
        aggregate_member=str(liker_identity))


def notify_follow(conn, *, follower: dict, target_identity: str, pen: bool = False) -> int:
    """Following is real-named (D10): the card names the latest follower.

    Pen-name follows get their own card ("关注了你的笔名"), never merged with
    real-name ones, so the counts stay per face.
    """
    role, _, raw_pk = str(target_identity or "").partition(":")
    recipient = _resolve_notifiable_user(role, raw_pk)
    follower_role = str(follower.get("role") or "").strip().lower()
    follower_pk = _safe_int_pk(follower.get("id"))
    if recipient is None or follower_pk is None or follower_role not in {"student", "teacher"}:
        return 0
    follower_identity = _identity_of(follower_role, follower_pk)
    if follower_identity == _identity_of(*recipient):
        return 0
    profile = persona_service.load_profiles(conn, [follower_identity]).get(follower_identity) or {}
    name = str(profile.get("name") or follower.get("name") or "有人")
    template = "{count} 人关注了你的笔名" if pen else "{count} 人关注了你"
    face = "pen" if pen else "real"
    return _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_FOLLOW,
        title=template, body_preview=f"最近关注的是 {name}", actor_role=follower_role, actor_user_pk=follower_pk,
        actor_display_name=name, link_url="/blog?view=studio&tab=fans", ref_type="blog_follow",
        ref_id=f"{face}:{follower_identity}", metadata={"event": EVENT_FOLLOW, "title_template": template},
    ), event=EVENT_FOLLOW, aggregate_key=f"{EVENT_FOLLOW}:{face}:{date.today().isoformat()}",
        aggregate_member=follower_identity, aggregate_recent=follower_identity)


# --- system events ------------------------------------------------------------

def notify_post_featured(
    conn,
    post: dict,
    moderator_identity: str,
    moderator_role: str,
    moderator_pk: int,
) -> None:
    """Shown as "博客中心" (N7), once per post."""
    recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
    if recipient is None:
        return
    post_id = int(post["id"])
    _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_HOT,
        title="你的帖子被设为精华", body_preview=f"「{post.get('title', '')}」已被设为精华帖",
        actor_display_name=SYSTEM_ACTOR, link_url=_post_link(post_id),
        ref_type="blog_featured", ref_id=str(post_id), metadata=_metadata(EVENT_FEATURED, post_id),
    ), event=EVENT_FEATURED, post_id=post_id)


def notify_housekeeper_failed(
    conn,
    *,
    recipient_role: Any,
    recipient_pk: Any,
    post_id: int,
    reason: str,
) -> None:
    """Tell only the caller that @管家 could not reply (never a public comment)."""
    recipient = _resolve_notifiable_user(recipient_role, recipient_pk)
    if recipient is None:
        return
    detail = str(reason or "").strip().rstrip("。") or "管家暂时无法回复"
    _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_COMMENT,
        title="管家这次没能回复", body_preview=f"{detail}。可以稍后重新 @管家。"[:120],
        actor_display_name="管家", link_url=_post_link(int(post_id)),
        ref_type="blog_post", ref_id=str(int(post_id)),
        metadata=_metadata(EVENT_HOUSEKEEPER_FAILED, int(post_id)),
    ), event=EVENT_HOUSEKEEPER_FAILED, post_id=int(post_id), dedupe=False)


def notify_post_hot(
    conn,
    post: dict,
    *,
    score: int,
) -> None:
    recipient = _resolve_notifiable_user(post.get("author_role"), post.get("author_user_pk"))
    if recipient is None:
        return
    post_id = int(post["id"])
    _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_HOT,
        title="你的帖子进入热门",
        body_preview=f"「{post.get('title', '')}」正在被更多人看到，当前热度分 {int(score)}",
        actor_display_name=SYSTEM_ACTOR, link_url=_post_link(post_id),
        ref_type="blog_post", ref_id=str(post_id), metadata=_metadata(EVENT_HOT, post_id),
    ), event=EVENT_HOT, post_id=post_id, dedupe=False)


def notify_opportunity_deadline(conn, opportunity: dict[str, Any], user_state: dict[str, Any]) -> bool:
    recipient = _resolve_notifiable_user(user_state.get("user_role"), user_state.get("user_pk"))
    if recipient is None:
        return False
    post_id = _safe_int_pk(opportunity.get("post_id"))
    opportunity_id = _safe_int_pk(opportunity.get("id"))
    if post_id is None or opportunity_id is None:
        return False
    employer = str(opportunity.get("employer_name") or opportunity.get("post_title") or "就业机会")
    deadline_text = str(opportunity.get("deadline_at") or "")[:10]
    _send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_CAREER,
        title="收藏的就业机会即将截止",
        body_preview=f"{employer} 的报名截止时间为 {deadline_text or '近期'}，请及时核验官方公告并准备材料。",
        actor_display_name="毕业新征程", link_url=f"/blog?section=career&post={post_id}",
        ref_type="blog_opportunity_deadline", ref_id=str(opportunity_id),
        metadata={"event": EVENT_CAREER_DEADLINE, "post_id": post_id, "opportunity_id": opportunity_id,
                  "deadline_at": opportunity.get("deadline_at")},
    ), event=EVENT_CAREER_DEADLINE, post_id=post_id, dedupe=False)
    return True


def notify_blog_moderation(
    conn,
    *,
    recipient_role: Any,
    recipient_pk: Any,
    post_id: int,
    title: str,
    body: str,
    comment_id: Optional[int] = None,
    event: str = EVENT_MODERATION,
    link_to_content: bool = True,
) -> bool:
    """A review/moderation outcome for one person (author, reporter).

    The actor is always "博客中心": moderators act for the platform, and a
    reporter must never learn who handled their report (plan §5.4).
    """
    recipient = _resolve_notifiable_user(recipient_role, recipient_pk)
    if recipient is None:
        return False
    event_key = event if event in EVENT_KEYS else EVENT_MODERATION
    return bool(_send(conn, _build_notification_payload(
        recipient_role=recipient[0], recipient_user_pk=recipient[1], category=MESSAGE_CATEGORY_BLOG_MODERATION,
        title=str(title or "")[:80], body_preview=str(body or "")[:160], actor_display_name=SYSTEM_ACTOR,
        link_url=_post_link(int(post_id), comment_id) if link_to_content else "",
        ref_type="blog_comment" if comment_id else "blog_post", ref_id=str(int(comment_id or post_id)),
        metadata=_metadata(event_key, int(post_id), comment_id=comment_id),
    ), event=event_key, post_id=int(post_id), dedupe=False))


# --- read and stale state ---------------------------------------------------------

def mark_post_read(conn, user: dict, post_id: int) -> int:
    from .blog_service import _ensure_identity

    _pk, _role, identity = _ensure_identity(user)
    return delivery.mark_read_by_post(conn, identity, int(post_id), categories=READ_BY_POST_CATEGORIES)


def set_post_target_state(conn, post: dict, *, gone: bool, deleted: bool = False) -> int:
    """A deleted post is gone for everyone; a limited/removed/withdrawn one only
    for people other than its author (who can still open it to appeal).

    Only cards that are live (or gone because of the post) change: a post
    coming back never revives a card about a comment deleted meanwhile.
    """
    return delivery.set_target_state(
        conn, delivery.TARGET_STATE_GONE_POST if gone else "", ref_post_id=int(post["id"]),
        exclude_recipient="" if deleted else str(post.get("author_identity") or ""),
        exclude_categories=(MESSAGE_CATEGORY_BLOG_MODERATION,),
        only_states=("",) if gone else (delivery.TARGET_STATE_GONE_POST,),
    )


def set_comment_target_state(conn, comment_id: int, *, gone: bool) -> int:
    state = delivery.TARGET_STATE_GONE_COMMENT if gone else ""
    return sum(
        delivery.set_target_state(
            conn, state, ref_type=ref_type, ref_ids=[int(comment_id)],
            exclude_categories=(MESSAGE_CATEGORY_BLOG_MODERATION,),
            only_states=None if gone else (delivery.TARGET_STATE_GONE_COMMENT,),
        )
        for ref_type in ("blog_comment", "blog_comment_like")
    )


def sync_target_state(conn, target_type: str, target_id: int, state: str) -> None:
    """After a review/moderation decision: hide or revive cards about the target.

    A post that became public again also sends the @-mentions it held back.
    """
    from . import blog_moderation_states as states

    if target_type == "post":
        post = _load_post(conn, target_id)
        if post is None:
            return
        if states.is_public_state(state) and str(post.get("status") or "") == "published":
            from .blog_service import _after_post_public

            _after_post_public(conn, int(target_id))
        else:
            set_post_target_state(conn, post, gone=True)
        return
    set_comment_target_state(conn, int(target_id), gone=not states.is_public_state(state))


# --- read-time rendering ----------------------------------------------------------

def _rows_by_id(conn, table: str, ids: Iterable[int], columns: str) -> dict[int, dict]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    placeholders = ", ".join("?" for _ in wanted)
    rows = conn.execute(f"SELECT {columns} FROM {table} WHERE id IN ({placeholders})", wanted).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _actor_personas(conn, items: list[dict]) -> tuple[dict[tuple[str, int], dict], set[int]]:
    """One batch of persona lookups for a whole page of cards (+ ids of live public comments)."""
    from . import blog_moderation_states as states

    comment_ids: set[int] = set()
    post_ids: set[int] = set()
    for item in items:
        meta = item.get("metadata") or {}
        actor = meta.get("actor") or {}
        actor_id = _safe_int_pk(actor.get("id"))
        if actor_id and actor.get("type") == "comment":
            comment_ids.add(actor_id)
        elif actor_id and actor.get("type") == "post":
            post_ids.add(actor_id)
        if meta.get("event") == EVENT_THREAD_REPLY:
            comment_ids.update(int(cid) for cid in meta.get("aggregate_recent") or [] if _safe_int_pk(cid))
    comments = _rows_by_id(conn, "blog_comments", comment_ids,
                           "id, post_id, author_identity, author_role, author_user_pk, author_display_name, "
                           "author_display_mode, author_display_snapshot, status, moderation_state")
    post_ids.update(int(row["post_id"]) for row in comments.values())
    posts = _rows_by_id(conn, "blog_posts", post_ids,
                        "id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode")
    profiles = persona_service.load_profiles(
        conn, [row["author_identity"] for row in [*comments.values(), *posts.values()]])
    personas: dict[tuple[str, int], dict] = {}
    for cid, row in comments.items():
        post = posts.get(int(row["post_id"])) or {}
        personas[("comment", cid)] = persona_service.resolve(
            actor_identity=row["author_identity"], actor_role=row["author_role"], actor_user_pk=row["author_user_pk"],
            display_mode=row["author_display_mode"],
            snapshot_name=row["author_display_snapshot"] or row["author_display_name"], profiles=profiles,
            post_author_identity=post.get("author_identity") or "",
            post_display_mode=post.get("author_display_mode") or "", is_comment=True,
        )
    for pid, row in posts.items():
        personas[("post", pid)] = persona_service.resolve(
            actor_identity=row["author_identity"], actor_role=row["author_role"], actor_user_pk=row["author_user_pk"],
            display_mode=row["author_display_mode"], snapshot_name=row["author_display_name"], profiles=profiles,
        )
    live = {cid for cid, row in comments.items()
            if str(row.get("status") or "") == "active" and states.is_public_state(row.get("moderation_state"))}
    return personas, live


def _face(persona: dict) -> dict[str, Any]:
    return {"name": persona.get("display_name") or "", "avatar_url": persona.get("avatar_url") or "",
            "kind": persona.get("kind") or ""}


def _follower_faces(conn, identities: list[str]) -> list[dict[str, Any]]:
    profiles = persona_service.load_profiles(conn, identities)
    return [
        {"name": (profiles.get(identity) or {}).get("name") or "", "kind": persona_service.KIND_REAL,
         "avatar_url": persona_service.avatar_url_for_hash((profiles.get(identity) or {}).get("avatar_hash", ""))}
        for identity in identities
    ]


def _render_title(item: dict, meta: dict, event: str, count: int) -> None:
    template = str(meta.get("title_template") or "")
    if not template:
        return
    title = template.replace("{actor}", item.get("actor_display_name") or "有人").replace("{count}", str(count))
    item["title"] = f"{title}（{count} 次）" if event == EVENT_MENTION and count > 1 else title


def render_notifications(conn, items: list[dict]) -> list[dict]:
    """Re-render blog cards with live personas; other items pass through."""
    blog_items = [item for item in items if str((item.get("metadata") or {}).get("event") or "").startswith("blog.")]
    if not blog_items:
        return items
    personas, live_comments = _actor_personas(conn, blog_items)
    for item in blog_items:
        meta = item.get("metadata") or {}
        event = str(meta.get("event") or "")
        actor_ref = meta.get("actor") or {}
        persona = personas.get((str(actor_ref.get("type") or ""), _safe_int_pk(actor_ref.get("id")) or 0))
        count = int(item.get("aggregate_count") or 1)
        if persona:
            item["actor_display_name"] = persona["display_name"]
            if persona.get("kind") != persona_service.KIND_REAL:
                # A face that is not real-named is never a contactable actor.
                item.update(actor_identity="", actor_role="", actor_user_pk=None)
        _render_title(item, meta, event, count)
        recent = [value for value in meta.get("aggregate_recent") or [] if value]
        if event == EVENT_THREAD_REPLY:
            stack = [_face(personas[("comment", int(cid))]) for cid in recent
                     if _safe_int_pk(cid) and int(cid) in live_comments]
        elif event == EVENT_FOLLOW:
            stack = _follower_faces(conn, [str(value) for value in recent])
        else:
            stack = []
        gone = delivery.is_gone(item.get("target_state"))
        item["blog"] = {
            "event": event,
            "post_id": _safe_int_pk(meta.get("post_id")),
            "comment_id": _safe_int_pk(meta.get("comment_id")),
            "count": count,
            "actor": _face(persona) if persona else {
                "name": item.get("actor_display_name") or SYSTEM_ACTOR, "avatar_url": "", "kind": "system"},
            "avatar_stack": [face for face in stack if face.get("name")],
            "gone": gone,
            "can_reply": event in REPLYABLE_EVENTS and bool(meta.get("comment_id")) and not gone,
        }
        if gone:
            item["link_url"] = ""
        # Member hashes and raw follower/comment ids stay on the server.
        item["metadata"] = {key: value for key, value in meta.items()
                            if key not in {"aggregate_members", "aggregate_recent"}}
    return items


def preferences_view(conn, identity: str) -> list[dict[str, Any]]:
    saved = delivery.load_preferences(conn, identity)
    return [
        {"key": item["key"], "label": item["label"], "help": item.get("help") or "",
         "forced": bool(item.get("forced")), "enabled": True if item.get("forced") else saved.get(item["key"], True)}
        for item in BLOG_EVENTS
    ]


def set_preference(conn, identity: str, event_key: str, enabled: bool) -> None:
    if event_key not in EVENT_KEYS:
        raise ValueError("无效的通知事件")
    if event_key in FORCED_EVENTS:
        raise ValueError("这类通知涉及你的内容，不能关闭")
    delivery.set_event_enabled(conn, identity, event_key, bool(enabled))
