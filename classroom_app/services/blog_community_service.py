from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from . import blog_service
from . import notification_delivery as delivery
from .blog_section_service import resolve_blog_section_key


FOLLOW_TARGET_TYPES = {"section", "author", "tag"}
# "关注更新 N" counts followed posts since the feed was last opened (plan §6.4).
FOLLOWING_SEEN_KEY = "blog.following"
FOLLOWING_FIRST_LOOK_DAYS = 7
MAX_FOLLOWS = 500
# The topbar polls the summary for every signed-in user; the following count
# is cached per person for a short while (and dropped when they open the feed).
FOLLOWING_COUNT_TTL_SECONDS = 45
_FOLLOWING_COUNT_CACHE: dict[str, tuple[float, int]] = {}
_FOLLOWING_COUNT_CACHE_LIMIT = 2000
REPORT_TARGET_TYPES = {"post", "comment", "opportunity"}
# "privacy_identity" = "暴露了我的身份或个人信息": pinned first on the review desk.
REPORT_REASONS = {"false_information", "spam", "abuse", "job_scam", "privacy", "privacy_identity", "other"}


def _normalize_follow_target(conn, target_type: Any, target_key: Any) -> tuple[str, str]:
    normalized_type = str(target_type or "").strip().lower()
    normalized_key = str(target_key or "").strip()
    if normalized_type not in FOLLOW_TARGET_TYPES or not normalized_key:
        raise ValueError("关注对象不正确")
    if normalized_type == "section":
        normalized_key = resolve_blog_section_key(conn, normalized_key, fallback=None) or ""
    elif normalized_type == "author":
        if not blog_service.IDENTITY_PATTERN.fullmatch(normalized_key):
            raise ValueError("作者标识不正确")
        role, _, raw_pk = normalized_key.partition(":")
        table = {"student": "students", "teacher": "teachers"}.get(role)
        # Following someone notifies them: only real accounts can be followed.
        if table is None or conn.execute(f"SELECT 1 FROM {table} WHERE id = ? LIMIT 1", (int(raw_pk),)).fetchone() is None:
            raise ValueError("作者不存在")
    else:
        normalized_key = normalized_key[:40]
    return normalized_type, normalized_key


def list_follows(conn, user: dict) -> list[dict[str, Any]]:
    _user_pk, _role, identity = blog_service._ensure_identity(user)
    rows = conn.execute(
        """
        SELECT target_type, target_key, created_at
        FROM blog_follows
        WHERE user_identity = ?
        ORDER BY created_at DESC, id DESC
        """,
        (identity,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_follow(conn, user: dict, *, target_type: Any, target_key: Any, following: bool | None = None) -> dict[str, Any]:
    user_pk, role, identity = blog_service._ensure_identity(user)
    normalized_type, normalized_key = _normalize_follow_target(conn, target_type, target_key)
    existing = conn.execute(
        "SELECT id FROM blog_follows WHERE user_identity = ? AND target_type = ? AND target_key = ? LIMIT 1",
        (identity, normalized_type, normalized_key),
    ).fetchone()
    next_following = not bool(existing) if following is None else bool(following)
    if next_following and existing is None:
        total = conn.execute("SELECT COUNT(*) AS total FROM blog_follows WHERE user_identity = ?", (identity,)).fetchone()
        if int(total["total"] or 0) >= MAX_FOLLOWS:
            raise ValueError(f"最多关注 {MAX_FOLLOWS} 个作者、板块或标签")
        conn.execute(
            """
            INSERT INTO blog_follows (user_identity, user_role, user_pk, target_type, target_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_identity, target_type, target_key) DO NOTHING
            """,
            (identity, role, user_pk, normalized_type, normalized_key),
        )
        _forget_following_count(identity)
    elif not next_following and existing is not None:
        conn.execute("DELETE FROM blog_follows WHERE id = ?", (int(existing["id"]),))
    if next_following and existing is None and normalized_type == "author":
        from .blog_notifications import best_effort, notify_follow

        best_effort(conn, notify_follow, conn, follower=user, target_identity=normalized_key)
    return {"target_type": normalized_type, "target_key": normalized_key, "following": next_following}


def list_following_posts(
    conn,
    user: dict,
    *,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    _user_pk, _role, identity = blog_service._ensure_identity(user)
    where = _following_where(conn, user)
    if where is None:
        return {"posts": [], "total": 0, "page": page, "limit": limit, "has_more": False}
    where_sql, params = where
    total = int(conn.execute(f"SELECT COUNT(*) AS total FROM blog_posts p WHERE {where_sql}", params).fetchone()["total"])
    offset = max(page - 1, 0) * limit
    rows = conn.execute(
        f"""
        SELECT p.*,
               LENGTH(p.content_md) AS content_length,
               EXISTS(SELECT 1 FROM blog_likes bl WHERE bl.target_type = 'post' AND bl.target_id = p.id AND bl.user_identity = ?) AS is_liked,
               EXISTS(SELECT 1 FROM blog_bookmarks bb WHERE bb.post_id = p.id AND bb.user_identity = ?) AS is_bookmarked
        FROM blog_posts p
        WHERE {where_sql}
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        [identity, identity, *params, limit, offset],
    ).fetchall()
    row_items = [dict(row) for row in rows]
    badge_map = blog_service._build_author_cultivation_badge_map(conn, row_items)
    posts = [
        blog_service._serialize_post_summary(row, viewer_identity=identity, cultivation_badge_map=badge_map)
        for row in row_items
    ]
    return {"posts": posts, "total": total, "page": page, "limit": limit, "has_more": offset + limit < total}


def _following_where(conn, user: dict) -> tuple[str, list[Any]] | None:
    """SQL for "posts from what I follow" (alias ``p``); None when following nothing."""
    user_pk, _role, identity = blog_service._ensure_identity(user)
    follows = list_follows(conn, user)
    if not follows:
        return None
    visibility_sql, visibility_params = blog_service._build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
        table_alias="p",
    )
    follow_clauses: list[str] = []
    follow_params: list[Any] = []
    for follow in follows:
        target_type = str(follow["target_type"])
        target_key = str(follow["target_key"])
        if target_type == "section":
            follow_clauses.append("p.section_key = ?")
            follow_params.append(target_key)
        elif target_type == "author":
            # Following a person follows their real-name face only; pen-name
            # posts would otherwise reveal who the pen name is.
            follow_clauses.append("(p.author_identity = ? AND p.author_display_mode = 'real_name')")
            follow_params.append(target_key)
        elif target_type == "tag":
            follow_clauses.append(
                "(p.tags_json LIKE ? OR (p.author_display_mode = 'real_name' AND p.system_tags_json LIKE ?))"
            )
            follow_params.extend((f'%"{target_key}"%', f'%"{target_key}"%'))
    if not follow_clauses:
        return None
    where_sql = f"({visibility_sql}) AND p.status = ? AND ({' OR '.join(follow_clauses)})"
    return where_sql, [*visibility_params, blog_service.POST_STATUS_PUBLISHED, *follow_params]


def _forget_following_count(identity: str) -> None:
    _FOLLOWING_COUNT_CACHE.pop(identity, None)


def count_following_unread(conn, user: dict) -> int:
    """Followed posts published since the following feed was last opened (not pushed)."""
    _user_pk, _role, identity = blog_service._ensure_identity(user)
    cached = _FOLLOWING_COUNT_CACHE.get(identity)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    count = _count_following_unread(conn, user, identity)
    if len(_FOLLOWING_COUNT_CACHE) >= _FOLLOWING_COUNT_CACHE_LIMIT:
        _FOLLOWING_COUNT_CACHE.clear()
    _FOLLOWING_COUNT_CACHE[identity] = (time.monotonic() + FOLLOWING_COUNT_TTL_SECONDS, count)
    return count


def _count_following_unread(conn, user: dict, identity: str) -> int:
    where = _following_where(conn, user)
    if where is None:
        return 0
    where_sql, params = where
    seen_at = delivery.get_seen_at(conn, identity, FOLLOWING_SEEN_KEY) or (
        datetime.now() - timedelta(days=FOLLOWING_FIRST_LOOK_DAYS)).isoformat()
    row = conn.execute(
        f"SELECT COUNT(*) AS total FROM blog_posts p WHERE {where_sql} "
        "AND p.author_identity <> ? AND COALESCE(p.published_at, p.created_at) > ?",
        [*params, identity, seen_at],
    ).fetchone()
    return int(row["total"] or 0) if row else 0


def mark_following_seen(conn, user: dict) -> str:
    _user_pk, _role, identity = blog_service._ensure_identity(user)
    _forget_following_count(identity)
    return delivery.mark_seen(conn, identity, FOLLOWING_SEEN_KEY)


def create_report(
    conn,
    user: dict,
    *,
    target_type: Any,
    target_id: Any,
    reason_code: Any,
    details: Any = "",
) -> dict[str, Any]:
    user_pk, role, identity = blog_service._ensure_identity(user)
    normalized_type = str(target_type or "").strip().lower()
    normalized_reason = str(reason_code or "").strip().lower()
    try:
        normalized_id = int(target_id)
    except (TypeError, ValueError):
        raise ValueError("举报对象不正确")
    if normalized_type not in REPORT_TARGET_TYPES or normalized_reason not in REPORT_REASONS:
        raise ValueError("请选择有效的举报原因")
    if normalized_type == "post":
        post = blog_service._get_post_raw(conn, normalized_id)
    else:
        table = "blog_comments" if normalized_type == "comment" else "blog_opportunities"
        visible = (
            " AND status = 'active' AND (COALESCE(moderation_state, 'none') IN ('none', 'passed', 'flagged') "
            "OR author_identity = ?)" if normalized_type == "comment" else ""
        )
        target = conn.execute(
            f"SELECT post_id FROM {table} WHERE id = ?{visible} LIMIT 1",
            (normalized_id, identity) if normalized_type == "comment" else (normalized_id,),
        ).fetchone()
        post = blog_service._get_post_raw(conn, int(target["post_id"])) if target else None
    if not post or not blog_service._can_view_post(conn, user, post):
        raise ValueError("举报对象不存在或不可访问")
    normalized_details = str(details or "").strip()[:2000]
    conn.execute(
        """
        INSERT INTO blog_reports (
            target_type, target_id, reporter_identity, reporter_role, reporter_user_pk,
            reason_code, details, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(target_type, target_id, reporter_identity, status) DO UPDATE SET
            reason_code = excluded.reason_code,
            details = excluded.details,
            updated_at = CURRENT_TIMESTAMP
        """,
        (normalized_type, normalized_id, identity, role, user_pk, normalized_reason, normalized_details),
    )
    row = conn.execute(
        """
        SELECT id, target_type, target_id, reason_code, details, status, created_at, updated_at
        FROM blog_reports
        WHERE target_type = ? AND target_id = ? AND reporter_identity = ? AND status = 'pending'
        LIMIT 1
        """,
        (normalized_type, normalized_id, identity),
    ).fetchone()
    result = dict(row)
    if normalized_type in {"post", "comment"}:
        from .blog_moderation_service import apply_report_threshold

        # N different reporters limit the content until a moderator decides.
        result["auto_limited"] = apply_report_threshold(conn, normalized_type, normalized_id)
    return result


def list_pending_reports(conn, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM blog_reports
        WHERE status = 'pending'
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (max(1, min(int(limit or 100), 200)),),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        title = ""
        target_url = ""
        if item["target_type"] == "post":
            target = conn.execute("SELECT title FROM blog_posts WHERE id = ?", (item["target_id"],)).fetchone()
            title = str(target["title"] or "") if target else ""
            target_url = f"/blog?post={int(item['target_id'])}" if target else ""
        elif item["target_type"] == "comment":
            target = conn.execute(
                "SELECT post_id, content_md FROM blog_comments WHERE id = ?",
                (item["target_id"],),
            ).fetchone()
            title = str(target["content_md"] or "")[:120] if target else ""
            target_url = f"/blog?post={int(target['post_id'])}" if target else ""
        else:
            target = conn.execute(
                "SELECT p.id AS post_id, p.title FROM blog_opportunities o "
                "JOIN blog_posts p ON p.id = o.post_id WHERE o.id = ?",
                (item["target_id"],),
            ).fetchone()
            title = str(target["title"] or "") if target else ""
            target_url = f"/blog?section=career&post={int(target['post_id'])}" if target else ""
        item["target_title"] = title
        item["target_url"] = target_url
        result.append(item)
    return result


def resolve_report(conn, user: dict, report_id: int, *, status: str, notes: Any = "") -> dict[str, Any]:
    _user_pk, _role, identity = blog_service._ensure_identity(user)
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"resolved", "dismissed"}:
        raise ValueError("处理状态不正确")
    cursor = conn.execute(
        """
        UPDATE blog_reports
        SET status = ?, resolved_by_identity = ?, resolution_notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (normalized_status, identity, str(notes or "").strip()[:2000], int(report_id)),
    )
    if not cursor.rowcount:
        raise ValueError("举报不存在或已经处理")
    return {"id": int(report_id), "status": normalized_status}
