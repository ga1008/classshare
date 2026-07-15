from __future__ import annotations

from typing import Any

from . import blog_service
from .blog_section_service import resolve_blog_section_key


FOLLOW_TARGET_TYPES = {"section", "author", "tag"}
REPORT_TARGET_TYPES = {"post", "comment", "opportunity"}
REPORT_REASONS = {"false_information", "spam", "abuse", "job_scam", "privacy", "other"}


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
        conn.execute(
            """
            INSERT INTO blog_follows (user_identity, user_role, user_pk, target_type, target_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_identity, target_type, target_key) DO NOTHING
            """,
            (identity, role, user_pk, normalized_type, normalized_key),
        )
    elif not next_following and existing is not None:
        conn.execute("DELETE FROM blog_follows WHERE id = ?", (int(existing["id"]),))
    return {"target_type": normalized_type, "target_key": normalized_key, "following": next_following}


def list_following_posts(
    conn,
    user: dict,
    *,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    user_pk, _role, identity = blog_service._ensure_identity(user)
    follows = list_follows(conn, user)
    if not follows:
        return {"posts": [], "total": 0, "page": page, "limit": limit, "has_more": False}
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
            follow_clauses.append("p.author_identity = ? AND p.author_display_mode != 'anonymous'")
            follow_params.append(target_key)
        elif target_type == "tag":
            follow_clauses.append("(p.tags_json LIKE ? OR p.system_tags_json LIKE ?)")
            follow_params.extend((f'%"{target_key}"%', f'%"{target_key}"%'))
    if not follow_clauses:
        return {"posts": [], "total": 0, "page": page, "limit": limit, "has_more": False}
    where_sql = f"({visibility_sql}) AND p.status = ? AND ({' OR '.join(follow_clauses)})"
    params = [*visibility_params, blog_service.POST_STATUS_PUBLISHED, *follow_params]
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
    table = {"post": "blog_posts", "comment": "blog_comments", "opportunity": "blog_opportunities"}[normalized_type]
    if conn.execute(f"SELECT id FROM {table} WHERE id = ? LIMIT 1", (normalized_id,)).fetchone() is None:
        raise ValueError("举报对象不存在")
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
    return dict(row)


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
