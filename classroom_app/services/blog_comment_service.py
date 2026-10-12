"""Blog comments as floors (plan §7.1–§7.4, P4).

A top-level comment is a floor; every reply in it is flat (depth 2) and says
"回复 @X". Floors are paged in SQL; each floor preloads its first replies with
a window function, the rest load on demand. A deleted or withheld floor that
still has visible replies stays as an empty placeholder so the thread reads.

Editing (author, 15 minutes) re-runs L1 and the AI review of the new revision.
Deleting is soft: the row keeps who deleted it, and someone deleting another
person's comment must give a reason that is sent to the commenter.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from . import blog_moderation_states as states
from . import blog_safety_service as safety

FLOORS_PER_PAGE = 20
MAX_FLOORS_PER_PAGE = 50
REPLY_PREVIEW = 3
REPLIES_PER_PAGE = 20
EDIT_WINDOW_MINUTES = 15
MAX_DELETE_REASON = 200
DELETED_BY_AUTHOR = "author"
DELETED_BY_POST_AUTHOR = "post_author"
DELETED_BY_MODERATOR = "moderator"
PLACEHOLDERS = {
    DELETED_BY_AUTHOR: "该评论已被作者删除",
    DELETED_BY_POST_AUTHOR: "该评论已被帖子作者删除",
    DELETED_BY_MODERATOR: "该评论已被管理员移除",
}
WITHHELD_PLACEHOLDER = "该评论已被处理"
_COLUMNS = (
    "c.id, c.post_id, c.parent_comment_id, c.root_comment_id, c.reply_to_comment_id, "
    "c.author_identity, c.author_role, c.author_user_pk, c.author_display_name, "
    "c.author_display_mode, c.author_display_snapshot, c.content_md, c.emoji_payload_json, "
    "c.attachments_json, c.status, c.like_count, c.created_at, c.updated_at, c.edited_at, "
    "c.content_revision, c.deleted_by_role, c.moderation_state, c.moderation_reason_code, "
    "c.moderation_note, c.moderated_by"
)


def _now() -> datetime:
    return datetime.now()


class _Viewer:
    """Who is looking, and the SQL that says which comments they may see."""

    def __init__(self, conn, user: dict):
        from .blog_moderation_service import is_blog_moderator
        from .blog_service import _ensure_identity

        self.user = user
        self.user_pk, self.role, self.identity = _ensure_identity(user)
        self.moderator = is_blog_moderator(conn, user)

    def visible_sql(self, alias: str) -> tuple[str, list[Any]]:
        if self.moderator:
            return f"{alias}.status = 'active'", []
        return (f"{alias}.status = 'active' AND ({states.public_state_sql(alias)} OR {alias}.author_identity = ?)",
                [self.identity])


def _load_post(conn, user: dict, post_id: int) -> dict:
    from .blog_service import _can_view_post, _get_post_raw

    post = _get_post_raw(conn, int(post_id))
    if post is None:
        raise ValueError("帖子不存在")
    if not _can_view_post(conn, user, post):
        raise PermissionError("没有权限查看此帖子")
    return post


def _floor_where(viewer: _Viewer) -> tuple[str, list[Any]]:
    floor_visible, floor_params = viewer.visible_sql("c")
    reply_visible, reply_params = viewer.visible_sql("r")
    sql = (
        f"c.post_id = ? AND c.parent_comment_id IS NULL AND (({floor_visible}) OR EXISTS ("
        f"SELECT 1 FROM blog_comments r WHERE r.root_comment_id = c.id AND r.parent_comment_id IS NOT NULL "
        f"AND {reply_visible}))"
    )
    return sql, [*floor_params, *reply_params]


def _visible(row: dict, viewer: _Viewer) -> bool:
    if str(row.get("status") or "") != "active":
        return False
    return viewer.moderator or str(row.get("author_identity") or "") == viewer.identity or states.is_public_state(
        row.get("moderation_state"))


def _liked_ids(conn, viewer: _Viewer, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT target_id FROM blog_likes WHERE target_type = 'comment' AND user_identity = ? AND target_id IN ({placeholders})",
        (viewer.identity, *ids),
    ).fetchall()
    return {int(row["target_id"]) for row in rows}


def _can_edit(row: dict, viewer: _Viewer) -> bool:
    if str(row.get("author_identity") or "") != viewer.identity or str(row.get("status") or "") != "active":
        return False
    if str(row.get("moderation_state") or "") in states.SANCTIONED_STATES:
        return False
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or ""))
    except ValueError:
        return False
    return _now() - created <= timedelta(minutes=EDIT_WINDOW_MINUTES)


def _serialize(conn, viewer: _Viewer, post: dict, rows: list[dict]) -> dict[int, dict]:
    """Every row through the one comment serializer, plus P4 fields."""
    from .blog_service import AUTHOR_DISPLAY_REAL, _load_comment_avatar_map, _serialize_comment

    if not rows:
        return {}
    for row in rows:
        row["_viewer_moderator"] = viewer.moderator
        if not _visible(row, viewer):
            row["_withheld"] = True
    post_author = str(post.get("author_identity") or "")
    post_mode = str(post.get("author_display_mode") or AUTHOR_DISPLAY_REAL)
    avatar_map = _load_comment_avatar_map(conn, [row for row in rows if not row.get("_withheld")])
    liked = _liked_ids(conn, viewer, [int(row["id"]) for row in rows])
    comments_open = bool(post.get("allow_comments"))
    result = {}
    for row in rows:
        mine = str(row.get("author_identity") or "") == viewer.identity
        withheld = bool(row.get("_withheld"))
        item = _serialize_comment(
            row, viewer_identity=viewer.identity, is_liked=int(row["id"]) in liked, avatar_map=avatar_map,
            can_delete=not withheld and (mine or post_author == viewer.identity or viewer.moderator),
            post_author_identity=post_author, post_display_mode=post_mode,
        )
        deleted = str(row.get("status") or "") == "deleted"
        item.update({
            "root_comment_id": int(row.get("root_comment_id") or row["id"]),
            "reply_to_comment_id": row.get("reply_to_comment_id") or row.get("parent_comment_id"),
            "is_floor": row.get("parent_comment_id") is None,
            "edited": bool(row.get("edited_at")) and not withheld,
            "content_revision": int(row.get("content_revision") or 1),
            "can_edit": not withheld and _can_edit(row, viewer),
            "can_reply": not withheld and comments_open and not deleted,
            "can_moderate": viewer.role == "teacher" and not withheld,
            "can_report": viewer.role != "assistant" and not mine and not withheld,
            "placeholder_text": (
                PLACEHOLDERS.get(str(row.get("deleted_by_role") or ""), PLACEHOLDERS[DELETED_BY_AUTHOR])
                if deleted else WITHHELD_PLACEHOLDER
            ) if withheld else "",
            "replies": [],
        })
        result[int(row["id"])] = item
    return result


def _attach_reply_targets(conn, viewer: _Viewer, post: dict, replies: list[dict]) -> None:
    """ "回复 @X": X is the replied-to comment's public face, never a real name."""
    from .blog_service import _load_comment_avatar_map, _serialize_comment

    wanted: dict[int, Optional[dict]] = {}
    for item in replies:
        target = item.get("reply_to_comment_id")
        if target and int(target) != int(item["root_comment_id"]):
            wanted[int(target)] = None
    if wanted:
        placeholders = ", ".join("?" for _ in wanted)
        rows = [dict(row) for row in conn.execute(
            f"SELECT {_COLUMNS} FROM blog_comments c WHERE c.id IN ({placeholders})", list(wanted),
        ).fetchall()]
        context = _load_comment_avatar_map(conn, [row for row in rows if _visible(row, viewer)])
        for row in rows:
            if not _visible(row, viewer):
                wanted[int(row["id"])] = {"id": int(row["id"]), "display_name": "", "hidden": True}
                continue
            face = _serialize_comment(
                row, viewer_identity=viewer.identity, is_liked=False, avatar_map=context, can_delete=False,
                post_author_identity=str(post.get("author_identity") or ""),
                post_display_mode=str(post.get("author_display_mode") or "real_name"),
            )["author"]
            wanted[int(row["id"])] = {"id": int(row["id"]), "display_name": face["display_name"], "hidden": False}
    for item in replies:
        target = item.get("reply_to_comment_id")
        item["reply_to"] = wanted.get(int(target)) if target else None


def _reply_rows(conn, viewer: _Viewer, root_ids: list[int], *, per_floor: int) -> list[dict]:
    if not root_ids:
        return []
    visible, params = viewer.visible_sql("r")
    placeholders = ", ".join("?" for _ in root_ids)
    rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT {_COLUMNS.replace('c.', 'r.')},
                   ROW_NUMBER() OVER (PARTITION BY r.root_comment_id ORDER BY r.created_at, r.id) AS reply_rank
            FROM blog_comments r
            WHERE r.root_comment_id IN ({placeholders}) AND r.parent_comment_id IS NOT NULL AND {visible}
        ) ranked
        WHERE reply_rank <= ?
        ORDER BY root_comment_id, reply_rank
        """,
        (*root_ids, *params, int(per_floor)),
    ).fetchall()
    return [dict(row) for row in rows]


def _reply_counts(conn, viewer: _Viewer, root_ids: list[int]) -> dict[int, int]:
    if not root_ids:
        return {}
    visible, params = viewer.visible_sql("r")
    placeholders = ", ".join("?" for _ in root_ids)
    rows = conn.execute(
        f"SELECT r.root_comment_id AS root_id, COUNT(*) AS total FROM blog_comments r "
        f"WHERE r.root_comment_id IN ({placeholders}) AND r.parent_comment_id IS NOT NULL AND {visible} "
        f"GROUP BY r.root_comment_id",
        (*root_ids, *params),
    ).fetchall()
    return {int(row["root_id"]): int(row["total"]) for row in rows}


def _floor_page_for(conn, viewer: _Viewer, post_id: int, comment_id: int, limit: int) -> tuple[Optional[int], int]:
    """(floor id, page) that contains the comment, when the viewer may see it."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM blog_comments c WHERE c.id = ? AND c.post_id = ?",
                       (int(comment_id), int(post_id))).fetchone()
    if row is None or not _visible(dict(row), viewer):
        return None, 1
    floor_id = int(row["root_comment_id"] or row["id"]) if row["parent_comment_id"] is not None else int(row["id"])
    floor = conn.execute("SELECT created_at, id FROM blog_comments WHERE id = ?", (floor_id,)).fetchone()
    if floor is None:
        return None, 1
    where, params = _floor_where(viewer)
    before = conn.execute(
        f"SELECT COUNT(*) AS total FROM blog_comments c WHERE {where} "
        f"AND (c.created_at < ? OR (c.created_at = ? AND c.id < ?))",
        (int(post_id), *params, floor["created_at"], floor["created_at"], floor_id),
    ).fetchone()
    return floor_id, int(before["total"] or 0) // limit + 1


def list_floors(conn, user: dict, post_id: int, *, page: int = 1, limit: int = FLOORS_PER_PAGE,
                around: Optional[int] = None, reply_preview: int = REPLY_PREVIEW,
                max_limit: int = MAX_FLOORS_PER_PAGE) -> dict[str, Any]:
    post = _load_post(conn, user, post_id)
    viewer = _Viewer(conn, user)
    limit = max(1, min(int(limit or FLOORS_PER_PAGE), int(max_limit)))
    focus_floor = None
    if around:
        focus_floor, page = _floor_page_for(conn, viewer, post_id, int(around), limit)
    page = max(1, int(page or 1))
    where, params = _floor_where(viewer)
    total = int(conn.execute(f"SELECT COUNT(*) AS total FROM blog_comments c WHERE {where}",
                             (int(post_id), *params)).fetchone()["total"] or 0)
    floor_rows = [dict(row) for row in conn.execute(
        f"SELECT {_COLUMNS} FROM blog_comments c WHERE {where} ORDER BY c.created_at, c.id LIMIT ? OFFSET ?",
        (int(post_id), *params, limit, (page - 1) * limit),
    ).fetchall()]
    root_ids = [int(row["id"]) for row in floor_rows]
    reply_rows = _reply_rows(conn, viewer, root_ids, per_floor=reply_preview)
    if around and focus_floor in root_ids and int(around) not in {int(row["id"]) for row in reply_rows}:
        # Deep link into a long floor: load its replies up to the target.
        deep = _replies_page_rows(conn, viewer, focus_floor, offset=0, limit=200)
        reply_rows = [row for row in reply_rows if int(row["root_comment_id"]) != focus_floor] + deep
    serialized = _serialize(conn, viewer, post, floor_rows + reply_rows)
    counts = _reply_counts(conn, viewer, root_ids)
    replies = [serialized[int(row["id"])] for row in reply_rows]
    _attach_reply_targets(conn, viewer, post, replies)
    floors = []
    for row in floor_rows:
        floor = serialized[int(row["id"])]
        floor["replies"] = [item for item in replies if item["root_comment_id"] == floor["id"]]
        floor["reply_count"] = counts.get(floor["id"], 0)
        floor["has_more_replies"] = floor["reply_count"] > len(floor["replies"])
        floors.append(floor)
    return {
        "post_id": int(post_id), "comments": floors, "total": total, "page": page, "limit": limit,
        "has_more": page * limit < total, "comment_count": int(post.get("comment_count") or 0),
        "focus_comment_id": int(around) if around and focus_floor else None,
    }


def _replies_page_rows(conn, viewer: _Viewer, root_id: int, *, offset: int, limit: int) -> list[dict]:
    visible, params = viewer.visible_sql("c")
    return [dict(row) for row in conn.execute(
        f"SELECT {_COLUMNS} FROM blog_comments c WHERE c.root_comment_id = ? AND c.parent_comment_id IS NOT NULL "
        f"AND {visible} ORDER BY c.created_at, c.id LIMIT ? OFFSET ?",
        (int(root_id), *params, int(limit), int(offset)),
    ).fetchall()]


def list_replies(conn, user: dict, post_id: int, root_id: int, *, offset: int = 0,
                 limit: int = REPLIES_PER_PAGE) -> dict[str, Any]:
    post = _load_post(conn, user, post_id)
    viewer = _Viewer(conn, user)
    floor = conn.execute("SELECT id FROM blog_comments WHERE id = ? AND post_id = ? AND parent_comment_id IS NULL",
                         (int(root_id), int(post_id))).fetchone()
    if floor is None:
        raise ValueError("楼层不存在")
    limit = max(1, min(int(limit or REPLIES_PER_PAGE), 100))
    offset = max(0, int(offset or 0))
    rows = _replies_page_rows(conn, viewer, int(root_id), offset=offset, limit=limit)
    total = _reply_counts(conn, viewer, [int(root_id)]).get(int(root_id), 0)
    serialized = _serialize(conn, viewer, post, rows)
    replies = [serialized[int(row["id"])] for row in rows]
    _attach_reply_targets(conn, viewer, post, replies)
    return {"post_id": int(post_id), "root_comment_id": int(root_id), "replies": replies, "total": total,
            "offset": offset, "has_more": offset + len(rows) < total}


def get_comment_view(conn, user: dict, comment_id: int) -> dict[str, Any]:
    """One comment as the floors list would show it (for local insertion)."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM blog_comments c WHERE c.id = ?", (int(comment_id),)).fetchone()
    if row is None:
        raise ValueError("评论不存在")
    post = _load_post(conn, user, int(row["post_id"]))
    viewer = _Viewer(conn, user)
    if not _visible(dict(row), viewer):
        raise ValueError("评论不存在")
    item = _serialize(conn, viewer, post, [dict(row)])[int(row["id"])]
    if not item["is_floor"]:
        _attach_reply_targets(conn, viewer, post, [item])
    item["reply_count"] = 0
    item["has_more_replies"] = False
    return item


def _refresh_media(conn, comment_id: int, post_id: int, now: str) -> None:
    from ..db.schema_blog_closed_loop import comment_media_rows, insert_comment_media

    row = conn.execute("SELECT content_md, attachments_json, emoji_payload_json FROM blog_comments WHERE id = ?",
                       (int(comment_id),)).fetchone()
    conn.execute("DELETE FROM blog_comment_media WHERE comment_id = ?", (int(comment_id),))
    for file_hash, media_kind in sorted(comment_media_rows(dict(row)) if row else []):
        insert_comment_media(conn, int(comment_id), int(post_id), file_hash, media_kind, now)


def edit_comment(conn, user: dict, comment_id: int, *, content_md: Any, expected_revision: Any = None) -> dict[str, Any]:
    from .blog_service import MAX_COMMENT_LENGTH, BlogModerationLocked, BlogRevisionConflict, _safe_json_loads

    viewer = _Viewer(conn, user)
    row = conn.execute(f"SELECT {_COLUMNS} FROM blog_comments c WHERE c.id = ?", (int(comment_id),)).fetchone()
    if row is None or str(row["status"] or "") != "active":
        raise ValueError("评论不存在")
    current = dict(row)
    if str(current["author_identity"] or "") != viewer.identity:
        raise PermissionError("只能编辑自己的评论")
    if str(current.get("moderation_state") or "") in states.SANCTIONED_STATES:
        raise BlogModerationLocked("这条评论已被处理，不能再编辑")
    if not _can_edit(current, viewer):
        raise ValueError(f"评论发布超过 {EDIT_WINDOW_MINUTES} 分钟，不能再编辑")
    revision = int(current.get("content_revision") or 1)
    if expected_revision not in (None, "") and int(expected_revision) != revision:
        raise BlogRevisionConflict("评论已在别处修改，请刷新后再编辑")
    text = str(content_md or "").strip()[:MAX_COMMENT_LENGTH]
    has_media = bool(_safe_json_loads(current.get("attachments_json"), [])) or bool(current.get("emoji_payload_json"))
    if not text and not has_media:
        raise ValueError("评论内容不能为空")
    _load_post(conn, user, int(current["post_id"]))
    if viewer.role == "assistant":
        plan = safety.ReviewPlan(states.STATE_NONE, "", False)
    else:
        plan = safety.plan_review(target_type="comment", role=viewer.role, verdict=safety.enforce_local_rules(conn, text))
    now = _now().isoformat()
    next_revision = revision + 1
    cursor = conn.execute(
        "UPDATE blog_comments SET content_md = ?, content_revision = ?, edited_at = ?, updated_at = ?, "
        "moderation_state = ?, moderated_by = ?, moderation_reason_code = '', moderation_note = '' "
        "WHERE id = ? AND content_revision = ?",
        (text, next_revision, now, now, plan.state, plan.moderated_by, int(comment_id), revision),
    )
    if int(cursor.rowcount or 0) != 1:
        raise BlogRevisionConflict("评论已在别处修改，请刷新后再编辑")
    _refresh_media(conn, int(comment_id), int(current["post_id"]), now)
    safety.enqueue_review(conn, target_type="comment", target_id=int(comment_id), revision=next_revision, plan=plan)
    return get_comment_view(conn, user, int(comment_id))


def delete_comment(conn, user: dict, comment_id: int, *, reason: Any = "") -> dict[str, Any]:
    """Soft delete. Replies stay; an emptied floor keeps a placeholder for them."""
    from .blog_moderation_service import log_action
    from .blog_notifications import notify_blog_moderation

    viewer = _Viewer(conn, user)
    row = conn.execute(
        f"SELECT {_COLUMNS}, p.author_identity AS post_author_identity FROM blog_comments c "
        f"JOIN blog_posts p ON p.id = c.post_id WHERE c.id = ?",
        (int(comment_id),),
    ).fetchone()
    if row is None or str(row["status"] or "") != "active":
        raise ValueError("评论不存在")
    comment = dict(row)
    if str(comment["author_identity"] or "") == viewer.identity:
        deleted_by = DELETED_BY_AUTHOR
    elif viewer.moderator:
        deleted_by = DELETED_BY_MODERATOR
    elif str(comment["post_author_identity"] or "") == viewer.identity:
        deleted_by = DELETED_BY_POST_AUTHOR
    else:
        raise PermissionError("没有权限删除此评论")
    note = str(reason or "").strip()[:MAX_DELETE_REASON]
    if deleted_by != DELETED_BY_AUTHOR and not note:
        raise ValueError("删除他人的评论需要写一句原因，评论者会收到通知")
    now = _now().isoformat()
    cursor = conn.execute(
        "UPDATE blog_comments SET status = 'deleted', deleted_at = ?, deleted_by_role = ?, updated_at = ? "
        "WHERE id = ? AND status = 'active'",
        (now, deleted_by, now, int(comment_id)),
    )
    if int(cursor.rowcount or 0) != 1:
        raise ValueError("评论不存在")
    # A deleted comment's images are no longer served to anyone.
    conn.execute("DELETE FROM blog_comment_media WHERE comment_id = ?", (int(comment_id),))
    conn.execute(
        "UPDATE blog_posts SET comment_count = CASE WHEN COALESCE(comment_count, 0) <= 1 THEN 0 "
        "ELSE comment_count - 1 END, updated_at = ? WHERE id = ?",
        (now, int(comment["post_id"])),
    )
    if deleted_by != DELETED_BY_AUTHOR:
        if deleted_by == DELETED_BY_MODERATOR:
            log_action(conn, actor=user, target_type="comment", target=comment, action="delete_comment", note=note)
        notify_blog_moderation(
            conn, recipient_role=comment["author_role"], recipient_pk=comment["author_user_pk"],
            post_id=int(comment["post_id"]),
            title="你的评论已被删除" if deleted_by == DELETED_BY_MODERATOR else "你的评论已被帖子作者删除",
            body=f"原因：{note}",
        )
    return {"id": int(comment_id), "post_id": int(comment["post_id"]), "deleted": True, "deleted_by": deleted_by,
            # Older callers read a count; replies are no longer deleted with it.
            "deleted_count": 1}
