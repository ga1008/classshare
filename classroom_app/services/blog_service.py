from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote


POST_STATUS_DRAFT = "draft"
POST_STATUS_PUBLISHED = "published"
POST_STATUS_HIDDEN = "hidden"
POST_STATUS_MODERATED = "moderated"

AUTHOR_DISPLAY_REAL = "real_name"
AUTHOR_DISPLAY_NICKNAME = "nickname"
AUTHOR_DISPLAY_ANONYMOUS = "anonymous"
AUTHOR_DISPLAY_MODES = {
    AUTHOR_DISPLAY_REAL,
    AUTHOR_DISPLAY_NICKNAME,
    AUTHOR_DISPLAY_ANONYMOUS,
}

VISIBILITY_PUBLIC = "public"
VISIBILITY_CLASS = "class_visible"
VISIBILITY_SELECTED = "selected_users"

COMMENT_STATUS_ACTIVE = "active"
COMMENT_STATUS_HIDDEN = "hidden"

TARGET_TYPE_POST = "post"
TARGET_TYPE_COMMENT = "comment"

POSTS_PER_PAGE = 20
COMMENTS_PER_PAGE = 50
MAX_TAGS = 5
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 50000
MAX_COMMENT_LENGTH = 5000
MAX_SUMMARY_LENGTH = 200
MAX_SELECTED_USERS = 40
MAX_COMMENT_ATTACHMENTS = 4
MAX_COMMENT_CUSTOM_EMOJIS = 16
MAX_CUSTOM_EMOJI_RESULTS = 80
HOT_POST_SCORE_THRESHOLD = 18
HOT_POST_MIN_LIKES = 3
HOT_POST_MIN_COMMENTS = 2

IMAGE_HASH_PATTERN = re.compile(r"/api/blog/image/([a-f0-9]{64})")
IDENTITY_PATTERN = re.compile(r"^(student|teacher|assistant):\d+$")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_json_loads(raw_value: Any, fallback: Any):
    if isinstance(raw_value, type(fallback)):
        return raw_value
    if raw_value in (None, ""):
        return fallback
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _build_identity(role: str, user_pk: int) -> str:
    return f"{role}:{user_pk}"


def _ensure_identity(user: dict) -> tuple[int, str, str]:
    user_pk = _safe_int(user.get("id"))
    role = str(user.get("role") or "").strip().lower()
    if user_pk is None or role not in {"student", "teacher", "assistant"}:
        raise ValueError("invalid user")
    return user_pk, role, _build_identity(role, user_pk)


def _is_teacher(user: dict) -> bool:
    return str(user.get("role") or "").strip().lower() == "teacher"


def _can_override_post_visibility(user: dict) -> bool:
    return str(user.get("role") or "").strip().lower() in {"teacher", "assistant"}


def _can_editorialize_posts(user: dict) -> bool:
    return _is_teacher(user)


def _build_avatar_url(role: str, user_pk: Any, avatar_hash: str = "") -> str:
    normalized_role = str(role or "").strip().lower()
    normalized_user_pk = _safe_int(user_pk)
    if normalized_role not in {"teacher", "student"} or normalized_user_pk is None:
        return "/api/profile/avatar"
    revision = quote(str(avatar_hash or "default"), safe="")
    return (
        f"/api/profile/avatar?role={quote(normalized_role, safe='')}"
        f"&user_id={normalized_user_pk}&v={revision}"
    )


def _generate_summary(content_md: str, limit: int = MAX_SUMMARY_LENGTH) -> str:
    text = re.sub(r"[#*`>\-\[\]()!|~]", " ", str(content_md or ""))
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:max(limit - 3, 0)].rstrip() + "..."


def _extract_blog_image_hashes(content_md: str) -> list[str]:
    seen: set[str] = set()
    hashes: list[str] = []
    for match in IMAGE_HASH_PATTERN.finditer(str(content_md or "")):
        file_hash = match.group(1)
        if file_hash in seen:
            continue
        seen.add(file_hash)
        hashes.append(file_hash)
    return hashes


def _extract_first_image_hash(content_md: str) -> str:
    hashes = _extract_blog_image_hashes(content_md)
    return hashes[0] if hashes else ""


def _normalize_tags(tags: Any) -> list[str]:
    raw_items = _safe_json_loads(tags, []) if isinstance(tags, str) else tags
    if not isinstance(raw_items, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        text = text[:24]
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
        if len(normalized) >= MAX_TAGS:
            break
    return normalized


def _normalize_selected_identities(values: Any) -> list[str]:
    raw_items = _safe_json_loads(values, []) if isinstance(values, str) else values
    if not isinstance(raw_items, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        identity = str(item or "").strip().lower()
        if not identity or identity in seen or not IDENTITY_PATTERN.match(identity):
            continue
        normalized.append(identity)
        seen.add(identity)
        if len(normalized) >= MAX_SELECTED_USERS:
            break
    return normalized


def _visibility_label(visibility: str) -> str:
    if visibility == VISIBILITY_CLASS:
        return "同班可见"
    if visibility == VISIBILITY_SELECTED:
        return "指定用户可见"
    return "全部可见"


def _normalize_post_visibility_settings(
    conn,
    user: dict,
    *,
    visibility: Any,
    visible_class_id: Any,
    visible_user_identities: Any,
) -> tuple[str, Optional[int], list[str]]:
    user_pk, role, identity = _ensure_identity(user)
    normalized_visibility = str(visibility or VISIBILITY_PUBLIC).strip().lower()
    if normalized_visibility not in {VISIBILITY_PUBLIC, VISIBILITY_CLASS, VISIBILITY_SELECTED}:
        normalized_visibility = VISIBILITY_PUBLIC

    if normalized_visibility == VISIBILITY_CLASS:
        class_id = _safe_int(visible_class_id)
        if role == "student":
            row = conn.execute(
                "SELECT class_id FROM students WHERE id = ? LIMIT 1",
                (user_pk,),
            ).fetchone()
            student_class_id = _safe_int(row["class_id"]) if row else None
            class_id = class_id or student_class_id
            if class_id is None or class_id != student_class_id:
                raise ValueError("学生仅可将帖子设置为自己所在班级可见")
        elif role == "teacher":
            if class_id is None:
                raise ValueError("请选择可见班级")
            owner_row = conn.execute(
                "SELECT 1 FROM classes WHERE id = ? AND created_by_teacher_id = ? LIMIT 1",
                (class_id, user_pk),
            ).fetchone()
            if owner_row is None:
                raise ValueError("只能选择自己管理的班级")
        else:
            if class_id is None:
                raise ValueError("请选择可见班级")
        return VISIBILITY_CLASS, class_id, []

    if normalized_visibility == VISIBILITY_SELECTED:
        identities = _normalize_selected_identities(visible_user_identities)
        identities = [item for item in identities if item != identity]
        if not identities:
            raise ValueError("请选择至少一位可见用户")
        return VISIBILITY_SELECTED, None, identities

    return VISIBILITY_PUBLIC, None, []


def _build_post_visibility_sql(
    user: dict,
    *,
    viewer_identity: str,
    viewer_user_pk: int,
    table_alias: str = "",
) -> tuple[str, list[Any]]:
    prefix = f"{table_alias}." if table_alias else ""
    if _can_override_post_visibility(user):
        return (
            f"{prefix}status IN ('{POST_STATUS_PUBLISHED}', '{POST_STATUS_HIDDEN}', '{POST_STATUS_MODERATED}')",
            [],
        )

    return (
        f"""
        {prefix}status = '{POST_STATUS_PUBLISHED}'
        AND (
            {prefix}visibility = '{VISIBILITY_PUBLIC}'
            OR {prefix}author_identity = ?
            OR (
                {prefix}visibility = '{VISIBILITY_CLASS}'
                AND {prefix}visible_class_id IN (
                    SELECT class_id FROM students WHERE id = ?
                )
            )
            OR (
                {prefix}visibility = '{VISIBILITY_SELECTED}'
                AND {prefix}visible_user_identities_json LIKE ?
            )
        )
        """.strip(),
        [viewer_identity, viewer_user_pk, f'%"{viewer_identity}"%'],
    )


def _load_user_avatar_hash(conn, role: str, user_pk: int) -> str:
    if role not in {"teacher", "student"}:
        return ""
    table = "teachers" if role == "teacher" else "students"
    row = conn.execute(f"SELECT avatar_file_hash FROM {table} WHERE id = ?", (user_pk,)).fetchone()
    return str(row["avatar_file_hash"] or "") if row else ""


def _load_user_avatar_mime(conn, role: str, user_pk: int) -> str:
    if role not in {"teacher", "student"}:
        return ""
    table = "teachers" if role == "teacher" else "students"
    row = conn.execute(f"SELECT avatar_mime_type FROM {table} WHERE id = ?", (user_pk,)).fetchone()
    return str(row["avatar_mime_type"] or "") if row else ""


def _load_user_profile_snapshot(conn, role: str, user_pk: int) -> dict[str, str]:
    if role == "teacher":
        row = conn.execute(
            """
            SELECT name, nickname
            FROM teachers
            WHERE id = ?
            LIMIT 1
            """,
            (user_pk,),
        ).fetchone()
        if row is None:
            return {"name": "", "nickname": "", "class_name": ""}
        return {
            "name": str(row["name"] or ""),
            "nickname": str(row["nickname"] or ""),
            "class_name": "",
        }

    if role == "student":
        row = conn.execute(
            """
            SELECT s.name, s.nickname, COALESCE(c.name, '') AS class_name
            FROM students s
            LEFT JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
            LIMIT 1
            """,
            (user_pk,),
        ).fetchone()
        if row is None:
            return {"name": "", "nickname": "", "class_name": ""}
        return {
            "name": str(row["name"] or ""),
            "nickname": str(row["nickname"] or ""),
            "class_name": str(row["class_name"] or ""),
        }

    return {"name": "", "nickname": "", "class_name": ""}


def _normalize_author_display_mode(user: dict, author_display_mode: Any) -> str:
    role = str(user.get("role") or "").strip().lower()
    if role != "student":
        return AUTHOR_DISPLAY_REAL

    normalized_mode = str(author_display_mode or AUTHOR_DISPLAY_REAL).strip().lower()
    if normalized_mode not in AUTHOR_DISPLAY_MODES:
        return AUTHOR_DISPLAY_REAL
    return normalized_mode


def _merge_post_tags(system_tags: Any, custom_tags: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*_normalize_tags(system_tags), *_normalize_tags(custom_tags)]:
        lowered = item.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(item)
        if len(merged) >= MAX_TAGS + 2:
            break
    return merged


def _build_post_author_snapshot(conn, user: dict, *, author_display_mode: Any = None) -> dict[str, Any]:
    user_pk, role, identity = _ensure_identity(user)
    profile = _load_user_profile_snapshot(conn, role, user_pk)
    real_name = str(profile.get("name") or user.get("name") or "").strip()
    nickname = str(profile.get("nickname") or user.get("nickname") or "").strip()
    class_name = str(profile.get("class_name") or "").strip()
    display_mode = _normalize_author_display_mode(user, author_display_mode)

    if role == "student" and display_mode == AUTHOR_DISPLAY_NICKNAME:
        if not nickname:
            raise ValueError("请先在个人中心设置昵称后再使用昵称发帖")
        display_name = nickname
    elif role == "student" and display_mode == AUTHOR_DISPLAY_ANONYMOUS:
        display_name = "匿名同学"
    elif role == "assistant":
        display_name = str(user.get("name") or "管家").strip() or "管家"
    else:
        display_name = real_name or nickname or str(user.get("name") or "").strip()

    is_anonymous = role == "student" and display_mode == AUTHOR_DISPLAY_ANONYMOUS
    system_tags = [class_name] if role == "student" and not is_anonymous and class_name else []
    return {
        "identity": identity,
        "role": role,
        "user_pk": user_pk,
        "display_mode": display_mode,
        "display_name": display_name or ("匿名用户" if is_anonymous else "未命名用户"),
        "avatar_hash": "" if is_anonymous else _load_user_avatar_hash(conn, role, user_pk),
        "avatar_mime": "" if is_anonymous else _load_user_avatar_mime(conn, role, user_pk),
        "system_tags": _normalize_tags(system_tags),
    }


def _build_post_author_avatar_url(role: str, user_pk: Any, avatar_hash: str = "", display_mode: str = "") -> str:
    if str(display_mode or "").strip().lower() == AUTHOR_DISPLAY_ANONYMOUS:
        return "/api/profile/avatar"
    return _build_avatar_url(role, user_pk, avatar_hash)


def _load_user_owned_media_assets(
    conn,
    *,
    uploader_identity: str,
    file_hashes: list[str],
) -> dict[str, dict]:
    normalized_hashes = [str(item or "").strip().lower() for item in file_hashes if str(item or "").strip()]
    if not normalized_hashes:
        return {}

    placeholders = ", ".join("?" for _ in normalized_hashes)
    rows = conn.execute(
        f"""
        SELECT *
        FROM blog_media_assets
        WHERE uploader_identity = ?
          AND file_hash IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        """,
        (uploader_identity, *normalized_hashes),
    ).fetchall()

    media_map: dict[str, dict] = {}
    for row in rows:
        file_hash = str(row["file_hash"] or "")
        media_map.setdefault(file_hash, dict(row))
    return media_map


def register_media_asset(
    conn,
    user: dict,
    *,
    file_hash: str,
    filename: str,
    mime_type: str,
    file_size: int,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    normalized_hash = str(file_hash or "").strip().lower()
    if not normalized_hash:
        raise ValueError("invalid media hash")

    now = _now_iso()
    conn.execute(
        """
        INSERT INTO blog_media_assets (
            file_hash,
            uploader_identity,
            uploader_role,
            uploader_user_pk,
            original_filename,
            mime_type,
            file_size,
            image_width,
            image_height,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_hash, uploader_identity) DO UPDATE SET
            original_filename = excluded.original_filename,
            mime_type = excluded.mime_type,
            file_size = excluded.file_size,
            image_width = excluded.image_width,
            image_height = excluded.image_height,
            updated_at = excluded.updated_at
        """,
        (
            normalized_hash,
            identity,
            role,
            user_pk,
            str(filename or normalized_hash),
            str(mime_type or "application/octet-stream"),
            int(file_size or 0),
            _safe_int(image_width),
            _safe_int(image_height),
            now,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT *
        FROM blog_media_assets
        WHERE file_hash = ? AND uploader_identity = ?
        LIMIT 1
        """,
        (normalized_hash, identity),
    ).fetchone()
    return dict(row) if row else {}


def list_available_custom_emojis(conn, user: dict, *, limit: int = MAX_CUSTOM_EMOJI_RESULTS) -> list[dict]:
    user_pk, role, identity = _ensure_identity(user)
    if role not in {"student", "teacher"}:
        return []

    rows = conn.execute(
        """
        SELECT id, class_offering_id, display_name, original_filename, file_hash,
               mime_type, file_size, image_width, image_height, updated_at, created_at
        FROM custom_emojis
        WHERE owner_user_id = ? AND owner_user_role = ?
        ORDER BY updated_at DESC, created_at DESC, id DESC
        LIMIT ?
        """,
        (user_pk, role, max(limit * 3, limit)),
    ).fetchall()

    results: list[dict] = []
    seen_hashes: set[str] = set()
    for row in rows:
        file_hash = str(row["file_hash"] or "")
        if not file_hash or file_hash in seen_hashes:
            continue
        seen_hashes.add(file_hash)
        register_media_asset(
            conn,
            user,
            file_hash=file_hash,
            filename=str(row["original_filename"] or row["display_name"] or file_hash),
            mime_type=str(row["mime_type"] or "application/octet-stream"),
            file_size=int(row["file_size"] or 0),
            image_width=_safe_int(row["image_width"]),
            image_height=_safe_int(row["image_height"]),
        )
        results.append(
            {
                "id": int(row["id"]),
                "name": str(row["display_name"] or row["original_filename"] or "自定义表情"),
                "file_hash": file_hash,
                "mime_type": str(row["mime_type"] or "application/octet-stream"),
                "file_size": int(row["file_size"] or 0),
                "width": int(row["image_width"] or 0),
                "height": int(row["image_height"] or 0),
                "image_url": f"/api/blog/image/{file_hash}",
                "source_class_offering_id": int(row["class_offering_id"] or 0),
            }
        )
        if len(results) >= limit:
            break

    return results


def create_post(
    conn,
    user: dict,
    *,
    title: str,
    content_md: str,
    author_display_mode: str = AUTHOR_DISPLAY_REAL,
    visibility: str = VISIBILITY_PUBLIC,
    visible_class_id: Optional[int] = None,
    visible_user_identities: Optional[list[str]] = None,
    allow_comments: bool = True,
    tags: Optional[list[str]] = None,
    status: str = POST_STATUS_PUBLISHED,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    normalized_title = str(title or "").strip()[:MAX_TITLE_LENGTH]
    normalized_content = str(content_md or "").strip()[:MAX_CONTENT_LENGTH]
    if not normalized_title:
        raise ValueError("标题不能为空")
    if not normalized_content:
        raise ValueError("内容不能为空")

    normalized_visibility, normalized_class_id, normalized_identities = _normalize_post_visibility_settings(
        conn,
        user,
        visibility=visibility,
        visible_class_id=visible_class_id,
        visible_user_identities=visible_user_identities,
    )
    normalized_tags = _normalize_tags(tags or [])
    normalized_status = status if status in {POST_STATUS_DRAFT, POST_STATUS_PUBLISHED} else POST_STATUS_PUBLISHED
    media_assets = _resolve_post_media_assets(
        conn,
        user,
        _extract_blog_image_hashes(normalized_content),
    )
    author_snapshot = _build_post_author_snapshot(conn, user, author_display_mode=author_display_mode)

    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO blog_posts (
            author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
            author_avatar_hash, author_avatar_mime,
            title, content_md, summary, cover_image_hash,
            status, visibility, visible_class_id, visible_user_identities_json,
            allow_comments, system_tags_json, tags_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            author_snapshot["identity"],
            author_snapshot["role"],
            author_snapshot["user_pk"],
            author_snapshot["display_name"],
            author_snapshot["display_mode"],
            author_snapshot["avatar_hash"],
            author_snapshot["avatar_mime"],
            normalized_title,
            normalized_content,
            _generate_summary(normalized_content),
            _extract_first_image_hash(normalized_content),
            normalized_status,
            normalized_visibility,
            normalized_class_id,
            json.dumps(normalized_identities, ensure_ascii=False),
            1 if allow_comments else 0,
            json.dumps(author_snapshot["system_tags"], ensure_ascii=False),
            json.dumps(normalized_tags, ensure_ascii=False),
            now,
            now,
        ),
    )
    post_id = int(cursor.lastrowid)
    _sync_post_attachments(conn, post_id, media_assets)
    return {"id": post_id, "status": normalized_status, "created_at": now}


def update_post(
    conn,
    user: dict,
    post_id: int,
    *,
    title: Optional[str] = None,
    content_md: Optional[str] = None,
    author_display_mode: Optional[str] = None,
    visibility: Optional[str] = None,
    visible_class_id: Optional[int] = None,
    visible_user_identities: Optional[list[str]] = None,
    allow_comments: Optional[bool] = None,
    tags: Optional[list[str]] = None,
    status: Optional[str] = None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if post["author_identity"] != identity:
        raise PermissionError("没有权限编辑此帖子")

    updates: list[str] = []
    params: list[Any] = []
    next_content = str(post.get("content_md") or "")
    attachments_need_sync = False
    now = _now_iso()

    if title is not None:
        normalized_title = str(title or "").strip()[:MAX_TITLE_LENGTH]
        if not normalized_title:
            raise ValueError("标题不能为空")
        updates.append("title = ?")
        params.append(normalized_title)

    if content_md is not None:
        normalized_content = str(content_md or "").strip()[:MAX_CONTENT_LENGTH]
        if not normalized_content:
            raise ValueError("内容不能为空")
        next_content = normalized_content
        media_assets = _resolve_post_media_assets(
            conn,
            user,
            _extract_blog_image_hashes(normalized_content),
            existing_post_id=post_id,
        )
        updates.extend(["content_md = ?", "summary = ?", "cover_image_hash = ?"])
        params.extend(
            [
                normalized_content,
                _generate_summary(normalized_content),
                _extract_first_image_hash(normalized_content),
            ]
        )
        attachments_need_sync = True
    else:
        media_assets = None

    if author_display_mode is not None:
        author_snapshot = _build_post_author_snapshot(conn, user, author_display_mode=author_display_mode)
        updates.extend(
            [
                "author_display_name = ?",
                "author_display_mode = ?",
                "author_avatar_hash = ?",
                "author_avatar_mime = ?",
                "system_tags_json = ?",
            ]
        )
        params.extend(
            [
                author_snapshot["display_name"],
                author_snapshot["display_mode"],
                author_snapshot["avatar_hash"],
                author_snapshot["avatar_mime"],
                json.dumps(author_snapshot["system_tags"], ensure_ascii=False),
            ]
        )

    if visibility is not None:
        normalized_visibility, normalized_class_id, normalized_identities = _normalize_post_visibility_settings(
            conn,
            user,
            visibility=visibility,
            visible_class_id=visible_class_id,
            visible_user_identities=visible_user_identities,
        )
        updates.extend(["visibility = ?", "visible_class_id = ?", "visible_user_identities_json = ?"])
        params.extend(
            [
                normalized_visibility,
                normalized_class_id,
                json.dumps(normalized_identities, ensure_ascii=False),
            ]
        )

    if allow_comments is not None:
        updates.append("allow_comments = ?")
        params.append(1 if allow_comments else 0)

    if tags is not None:
        updates.append("tags_json = ?")
        params.append(json.dumps(_normalize_tags(tags), ensure_ascii=False))

    if status is not None and status in {POST_STATUS_DRAFT, POST_STATUS_PUBLISHED}:
        if post["author_identity"] == identity or _is_teacher(user):
            updates.append("status = ?")
            params.append(status)

    if not updates:
        return {"id": post_id, "updated": False}

    updates.extend(["edited_at = ?", "updated_at = ?"])
    params.extend([now, now, post_id])
    conn.execute(f"UPDATE blog_posts SET {', '.join(updates)} WHERE id = ?", params)

    if attachments_need_sync and media_assets is not None:
        _sync_post_attachments(conn, post_id, media_assets)
    elif content_md is None:
        _sync_post_attachments(
            conn,
            post_id,
            _resolve_post_media_assets(
                conn,
                user,
                _extract_blog_image_hashes(next_content),
                existing_post_id=post_id,
            ),
        )

    return {"id": post_id, "updated": True, "edited_at": now}


def delete_post(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if post["author_identity"] != identity:
        raise PermissionError("没有权限删除此帖子")

    conn.execute("DELETE FROM blog_likes WHERE target_type = ? AND target_id = ?", (TARGET_TYPE_POST, post_id))
    conn.execute(
        """
        DELETE FROM blog_likes
        WHERE target_type = ?
          AND target_id IN (SELECT id FROM blog_comments WHERE post_id = ?)
        """,
        (TARGET_TYPE_COMMENT, post_id),
    )
    conn.execute("DELETE FROM blog_posts WHERE id = ?", (post_id,))
    return {"id": post_id, "deleted": True}


def list_posts(
    conn,
    user: dict,
    *,
    sort: str = "latest",
    page: int = 1,
    limit: int = POSTS_PER_PAGE,
    author_identity: Optional[str] = None,
    tag: Optional[str] = None,
    visibility_filter: Optional[str] = None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    offset = max(page - 1, 0) * limit
    visibility_sql, params = _build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
    )

    conditions = [visibility_sql]
    if author_identity:
        normalized_author_identity = str(author_identity).strip().lower()
        conditions.append("author_identity = ?")
        params.append(normalized_author_identity)
        if normalized_author_identity != identity:
            conditions.append("author_display_mode != ?")
            params.append(AUTHOR_DISPLAY_ANONYMOUS)
    if tag:
        normalized_tag = str(tag or "").strip()
        conditions.append("(tags_json LIKE ? OR system_tags_json LIKE ?)")
        params.extend([f'%"{normalized_tag}"%', f'%"{normalized_tag}"%'])
    if visibility_filter in {VISIBILITY_PUBLIC, VISIBILITY_CLASS, VISIBILITY_SELECTED}:
        conditions.append("visibility = ?")
        params.append(visibility_filter)
    if sort == "featured":
        conditions.append("is_featured = 1")

    where_clause = " AND ".join(f"({item})" for item in conditions if item)
    if sort == "hot":
        order_clause = "is_pinned DESC, is_featured DESC, (like_count * 3 + comment_count * 2 + view_count) DESC, created_at DESC, id DESC"
    elif sort == "featured":
        order_clause = "is_featured DESC, featured_at DESC, created_at DESC, id DESC"
    else:
        order_clause = "is_pinned DESC, is_featured DESC, created_at DESC, id DESC"

    total = int(
        conn.execute(f"SELECT COUNT(*) AS total FROM blog_posts WHERE {where_clause}", params).fetchone()["total"]
    )
    rows = conn.execute(
        f"""
        SELECT id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
               author_avatar_hash, author_avatar_mime,
               title, summary, cover_image_hash,
               status, visibility, allow_comments, is_pinned, is_featured,
               view_count, like_count, comment_count, bookmark_count,
               system_tags_json, tags_json, created_at, edited_at, updated_at
        FROM blog_posts
        WHERE {where_clause}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    posts = [_serialize_post_summary(dict(row), viewer_identity=identity) for row in rows]
    return {
        "posts": posts,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def get_post_detail(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    row = conn.execute("SELECT * FROM blog_posts WHERE id = ? LIMIT 1", (post_id,)).fetchone()
    if row is None:
        raise ValueError("帖子不存在")

    post_row = dict(row)
    if not _can_view_post(conn, user, post_row):
        raise PermissionError("没有权限查看此帖子")

    new_view_count = int(post_row.get("view_count") or 0) + 1
    conn.execute("UPDATE blog_posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
    post_row["view_count"] = new_view_count

    return _serialize_post_detail(
        post_row,
        user=user,
        viewer_identity=identity,
        is_liked=_is_liked(conn, identity, TARGET_TYPE_POST, post_id),
        is_bookmarked=_is_bookmarked(conn, identity, post_id),
    )


def get_my_posts(
    conn,
    user: dict,
    *,
    page: int = 1,
    limit: int = POSTS_PER_PAGE,
    status_filter: Optional[str] = None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    offset = max(page - 1, 0) * limit
    conditions = ["author_identity = ?"]
    params: list[Any] = [identity]

    if status_filter in {POST_STATUS_DRAFT, POST_STATUS_PUBLISHED, POST_STATUS_HIDDEN, POST_STATUS_MODERATED}:
        conditions.append("status = ?")
        params.append(status_filter)

    where_clause = " AND ".join(conditions)
    total = int(
        conn.execute(f"SELECT COUNT(*) AS total FROM blog_posts WHERE {where_clause}", params).fetchone()["total"]
    )
    rows = conn.execute(
        f"""
        SELECT id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
               author_avatar_hash, author_avatar_mime,
               title, summary, cover_image_hash,
               status, visibility, allow_comments, is_pinned, is_featured,
               view_count, like_count, comment_count, bookmark_count,
               system_tags_json, tags_json, created_at, edited_at, updated_at
        FROM blog_posts
        WHERE {where_clause}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    posts = [_serialize_post_summary(dict(row), viewer_identity=identity) for row in rows]
    return {"posts": posts, "total": total, "page": page, "limit": limit, "has_more": offset + limit < total}


def get_bookmarked_posts(conn, user: dict, *, page: int = 1, limit: int = POSTS_PER_PAGE) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    offset = max(page - 1, 0) * limit
    visibility_sql, visibility_params = _build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
        table_alias="bp",
    )

    total = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM blog_bookmarks bb
            JOIN blog_posts bp ON bp.id = bb.post_id
            WHERE bb.user_identity = ?
              AND ({visibility_sql})
            """,
            [identity, *visibility_params],
        ).fetchone()["total"]
    )

    rows = conn.execute(
        f"""
        SELECT bp.*,
               EXISTS(
                   SELECT 1 FROM blog_likes bl
                   WHERE bl.target_type = '{TARGET_TYPE_POST}'
                     AND bl.target_id = bp.id
                     AND bl.user_identity = ?
               ) AS is_liked
        FROM blog_bookmarks bb
        JOIN blog_posts bp ON bp.id = bb.post_id
        WHERE bb.user_identity = ?
          AND ({visibility_sql})
        ORDER BY bb.created_at DESC, bb.id DESC
        LIMIT ? OFFSET ?
        """,
        [identity, identity, *visibility_params, limit, offset],
    ).fetchall()

    posts = []
    for row in rows:
        post = _serialize_post_summary(dict(row), viewer_identity=identity)
        post["is_bookmarked"] = True
        post["is_liked"] = bool(row["is_liked"])
        posts.append(post)

    return {"posts": posts, "total": total, "page": page, "limit": limit, "has_more": offset + limit < total}


def _is_liked(conn, user_identity: str, target_type: str, target_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM blog_likes
        WHERE user_identity = ?
          AND target_type = ?
          AND target_id = ?
        LIMIT 1
        """,
        (user_identity, target_type, target_id),
    ).fetchone()
    return row is not None


def _is_bookmarked(conn, user_identity: str, post_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM blog_bookmarks
        WHERE user_identity = ?
          AND post_id = ?
        LIMIT 1
        """,
        (user_identity, post_id),
    ).fetchone()
    return row is not None


def list_comments(conn, user: dict, post_id: int, *, page: int = 1, limit: int = COMMENTS_PER_PAGE) -> dict:
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if not _can_view_post(conn, user, post):
        raise PermissionError("没有权限查看此帖子")

    user_pk, role, identity = _ensure_identity(user)
    rows = conn.execute(
        """
        SELECT id, post_id, parent_comment_id,
               author_identity, author_role, author_user_pk, author_display_name,
               content_md, emoji_payload_json, attachments_json,
               status, like_count, created_at, updated_at
        FROM blog_comments
        WHERE post_id = ? AND status = 'active'
        ORDER BY created_at ASC, id ASC
        """,
        (post_id,),
    ).fetchall()

    comments = _build_comment_tree(
        conn,
        user,
        viewer_identity=identity,
        post_author_identity=str(post["author_identity"] or ""),
        rows=[dict(row) for row in rows],
        page=page,
        limit=limit,
    )
    return {"comments": comments, "post_id": post_id}


def add_comment(
    conn,
    user: dict,
    post_id: int,
    *,
    content_md: str,
    parent_comment_id: Optional[int] = None,
    author_display_name: str = "",
    emoji_payload_json: str = "",
    attachments_json: str = "[]",
    bypass_comment_lock: bool = False,
    notify_callback=None,
    hot_notify_callback=None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if not _can_view_post(conn, user, post):
        raise PermissionError("没有权限评论此帖子")
    if not bool(post.get("allow_comments")) and not bypass_comment_lock:
        raise PermissionError("帖子已关闭评论")

    normalized_content = str(content_md or "").strip()[:MAX_COMMENT_LENGTH]
    normalized_emojis = _normalize_comment_custom_emojis(conn, user, emoji_payload_json)
    normalized_attachments = _normalize_comment_attachments(conn, user, attachments_json)
    if not normalized_content and not normalized_emojis and not normalized_attachments:
        raise ValueError("评论内容不能为空")

    if parent_comment_id is not None:
        parent = conn.execute(
            """
            SELECT id, post_id, author_identity
            FROM blog_comments
            WHERE id = ? AND post_id = ? AND status = 'active'
            LIMIT 1
            """,
            (parent_comment_id, post_id),
        ).fetchone()
        if parent is None:
            raise ValueError("回复的评论不存在")

    now = _now_iso()
    display_name = str(author_display_name or user.get("name") or "").strip()
    if not display_name and role == "assistant":
        display_name = "管家"
    cursor = conn.execute(
        """
        INSERT INTO blog_comments (
            post_id, parent_comment_id,
            author_identity, author_role, author_user_pk, author_display_name,
            content_md, emoji_payload_json, attachments_json,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            post_id,
            parent_comment_id,
            identity,
            role,
            user_pk,
            display_name,
            normalized_content,
            json.dumps(normalized_emojis, ensure_ascii=False) if normalized_emojis else "",
            json.dumps(normalized_attachments, ensure_ascii=False) if normalized_attachments else "[]",
            now,
            now,
        ),
    )
    comment_id = int(cursor.lastrowid)
    conn.execute(
        "UPDATE blog_posts SET comment_count = comment_count + 1, updated_at = ? WHERE id = ?",
        (now, post_id),
    )

    if notify_callback:
        notify_callback(
            conn,
            post,
            comment_id,
            parent_comment_id,
            identity,
            role,
            user_pk,
            display_name,
            _build_comment_preview(normalized_content, normalized_attachments, normalized_emojis),
        )

    _maybe_notify_post_hot(conn, post_id, notify_callback=hot_notify_callback)
    return {"id": comment_id, "post_id": post_id, "created_at": now}


def delete_comment(conn, user: dict, comment_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    comment = conn.execute(
        """
        SELECT c.*, p.author_identity AS post_author_identity, p.id AS post_id
        FROM blog_comments c
        JOIN blog_posts p ON p.id = c.post_id
        WHERE c.id = ?
        LIMIT 1
        """,
        (comment_id,),
    ).fetchone()
    if comment is None:
        raise ValueError("评论不存在")

    can_delete = (
        comment["author_identity"] == identity
        or comment["post_author_identity"] == identity
        or _can_override_post_visibility(user)
    )
    if not can_delete:
        raise PermissionError("没有权限删除此评论")

    subtree_ids = _collect_comment_subtree_ids(conn, comment_id)
    if not subtree_ids:
        raise ValueError("评论不存在")

    placeholders = ", ".join("?" for _ in subtree_ids)
    conn.execute(
        f"DELETE FROM blog_likes WHERE target_type = ? AND target_id IN ({placeholders})",
        [TARGET_TYPE_COMMENT, *subtree_ids],
    )
    conn.execute(f"DELETE FROM blog_comments WHERE id IN ({placeholders})", subtree_ids)
    conn.execute(
        "UPDATE blog_posts SET comment_count = MAX(comment_count - ?, 0), updated_at = ? WHERE id = ?",
        (len(subtree_ids), _now_iso(), int(comment["post_id"])),
    )
    return {"id": comment_id, "deleted": True, "deleted_count": len(subtree_ids)}


def toggle_like(conn, user: dict, target_type: str, target_id: int, *, hot_notify_callback=None) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    if target_type not in {TARGET_TYPE_POST, TARGET_TYPE_COMMENT}:
        raise ValueError("无效的目标类型")

    if target_type == TARGET_TYPE_POST:
        post = _get_post_raw(conn, target_id)
        if post is None:
            raise ValueError("帖子不存在")
        if not _can_view_post(conn, user, post):
            raise PermissionError("没有权限")
        count_table = "blog_posts"
    else:
        row = conn.execute(
            """
            SELECT c.id, c.post_id, p.*
            FROM blog_comments c
            JOIN blog_posts p ON p.id = c.post_id
            WHERE c.id = ? AND c.status = 'active'
            LIMIT 1
            """,
            (target_id,),
        ).fetchone()
        if row is None:
            raise ValueError("评论不存在")
        if not _can_view_post(conn, user, dict(row)):
            raise PermissionError("没有权限")
        count_table = "blog_comments"

    existing = conn.execute(
        """
        SELECT id
        FROM blog_likes
        WHERE target_type = ? AND target_id = ? AND user_identity = ?
        LIMIT 1
        """,
        (target_type, target_id, identity),
    ).fetchone()

    if existing:
        conn.execute("DELETE FROM blog_likes WHERE id = ?", (int(existing["id"]),))
        conn.execute(f"UPDATE {count_table} SET like_count = MAX(like_count - 1, 0) WHERE id = ?", (target_id,))
        row = conn.execute(f"SELECT like_count FROM {count_table} WHERE id = ?", (target_id,)).fetchone()
        return {
            "liked": False,
            "target_type": target_type,
            "target_id": target_id,
            "like_count": int(row["like_count"] or 0) if row else 0,
        }

    conn.execute(
        """
        INSERT INTO blog_likes (target_type, target_id, user_identity, user_role, user_pk, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (target_type, target_id, identity, role, user_pk, _now_iso()),
    )
    conn.execute(f"UPDATE {count_table} SET like_count = like_count + 1 WHERE id = ?", (target_id,))
    row = conn.execute(f"SELECT like_count FROM {count_table} WHERE id = ?", (target_id,)).fetchone()
    if target_type == TARGET_TYPE_POST:
        _maybe_notify_post_hot(conn, target_id, notify_callback=hot_notify_callback)
    return {
        "liked": True,
        "target_type": target_type,
        "target_id": target_id,
        "like_count": int(row["like_count"] or 1) if row else 1,
    }


def toggle_bookmark(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if not _can_view_post(conn, user, post):
        raise PermissionError("没有权限")

    existing = conn.execute(
        """
        SELECT id
        FROM blog_bookmarks
        WHERE post_id = ? AND user_identity = ?
        LIMIT 1
        """,
        (post_id, identity),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM blog_bookmarks WHERE id = ?", (int(existing["id"]),))
        conn.execute("UPDATE blog_posts SET bookmark_count = MAX(bookmark_count - 1, 0) WHERE id = ?", (post_id,))
        row = conn.execute("SELECT bookmark_count FROM blog_posts WHERE id = ?", (post_id,)).fetchone()
        return {"bookmarked": False, "post_id": post_id, "bookmark_count": int(row["bookmark_count"] or 0) if row else 0}

    conn.execute(
        """
        INSERT INTO blog_bookmarks (post_id, user_identity, user_role, user_pk, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (post_id, identity, role, user_pk, _now_iso()),
    )
    conn.execute("UPDATE blog_posts SET bookmark_count = bookmark_count + 1 WHERE id = ?", (post_id,))
    row = conn.execute("SELECT bookmark_count FROM blog_posts WHERE id = ?", (post_id,)).fetchone()
    return {"bookmarked": True, "post_id": post_id, "bookmark_count": int(row["bookmark_count"] or 1) if row else 1}


def pin_post(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    if not _can_editorialize_posts(user):
        raise PermissionError("仅教师可置顶帖子")

    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")

    new_value = 0 if bool(post.get("is_pinned")) else 1
    now = _now_iso()
    conn.execute(
        "UPDATE blog_posts SET is_pinned = ?, pinned_at = ?, updated_at = ? WHERE id = ?",
        (new_value, now if new_value else None, now, post_id),
    )
    _log_moderation(conn, post_id, identity, role, user_pk, "pin" if new_value else "unpin")
    return {"id": post_id, "is_pinned": bool(new_value)}


def feature_post(conn, user: dict, post_id: int, *, notify_callback=None) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    if not _can_editorialize_posts(user):
        raise PermissionError("仅教师可设置精华")

    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")

    new_value = 0 if bool(post.get("is_featured")) else 1
    now = _now_iso()
    conn.execute(
        "UPDATE blog_posts SET is_featured = ?, featured_at = ?, updated_at = ? WHERE id = ?",
        (new_value, now if new_value else None, now, post_id),
    )
    _log_moderation(conn, post_id, identity, role, user_pk, "feature" if new_value else "unfeature")
    if new_value and notify_callback:
        notify_callback(conn, post, identity, role, user_pk)
    return {"id": post_id, "is_featured": bool(new_value)}


def hide_post(conn, user: dict, post_id: int, reason: str = "") -> dict:
    user_pk, role, identity = _ensure_identity(user)
    if not _can_override_post_visibility(user):
        raise PermissionError("仅教师或 AI 助教可调整帖子可见性")

    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")

    next_status = POST_STATUS_PUBLISHED if post.get("status") == POST_STATUS_MODERATED else POST_STATUS_MODERATED
    now = _now_iso()
    conn.execute(
        "UPDATE blog_posts SET status = ?, updated_at = ? WHERE id = ?",
        (next_status, now, post_id),
    )
    _log_moderation(
        conn,
        post_id,
        identity,
        role,
        user_pk,
        "restore" if next_status == POST_STATUS_PUBLISHED else "hide",
        str(reason or ""),
    )
    return {"id": post_id, "status": next_status, "is_private": next_status == POST_STATUS_MODERATED}


def toggle_comments(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")

    can_toggle = post["author_identity"] == identity or _can_override_post_visibility(user)
    if not can_toggle:
        raise PermissionError("没有权限")

    new_value = 0 if bool(post.get("allow_comments")) else 1
    conn.execute(
        "UPDATE blog_posts SET allow_comments = ?, updated_at = ? WHERE id = ?",
        (new_value, _now_iso(), post_id),
    )
    return {"id": post_id, "allow_comments": bool(new_value)}


def add_attachment(
    conn,
    post_id: int,
    file_hash: str,
    filename: str,
    mime_type: str,
    file_size: int,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> int:
    max_order_row = conn.execute(
        "SELECT MAX(display_order) AS max_order FROM blog_attachments WHERE post_id = ?",
        (post_id,),
    ).fetchone()
    next_order = int(max_order_row["max_order"] or 0) + 1 if max_order_row else 1
    cursor = conn.execute(
        """
        INSERT INTO blog_attachments (post_id, file_hash, original_filename, mime_type, file_size, image_width, image_height, display_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id,
            str(file_hash or "").strip().lower(),
            str(filename or file_hash),
            str(mime_type or "application/octet-stream"),
            int(file_size or 0),
            _safe_int(image_width),
            _safe_int(image_height),
            next_order,
        ),
    )
    return int(cursor.lastrowid)


def list_attachments(conn, post_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM blog_attachments WHERE post_id = ? ORDER BY display_order, id",
        (post_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_media_asset_for_user(conn, user: dict, file_hash: str) -> Optional[dict]:
    normalized_hash = str(file_hash or "").strip().lower()
    if not normalized_hash:
        return None

    user_pk, role, identity = _ensure_identity(user)
    direct_row = conn.execute(
        """
        SELECT *
        FROM blog_media_assets
        WHERE file_hash = ? AND uploader_identity = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (normalized_hash, identity),
    ).fetchone()
    if direct_row is not None:
        return dict(direct_row)

    attachment_rows = conn.execute(
        """
        SELECT a.file_hash, a.original_filename, a.mime_type, a.file_size, a.image_width, a.image_height, p.*
        FROM blog_attachments a
        JOIN blog_posts p ON p.id = a.post_id
        WHERE a.file_hash = ?
        ORDER BY a.id DESC
        """,
        (normalized_hash,),
    ).fetchall()
    for row in attachment_rows:
        payload = dict(row)
        if _can_view_post(conn, user, payload):
            return {
                "file_hash": normalized_hash,
                "original_filename": str(payload.get("original_filename") or normalized_hash),
                "mime_type": str(payload.get("mime_type") or "application/octet-stream"),
                "file_size": int(payload.get("file_size") or 0),
                "image_width": _safe_int(payload.get("image_width")),
                "image_height": _safe_int(payload.get("image_height")),
            }

    comment_rows = conn.execute(
        """
        SELECT c.attachments_json, c.emoji_payload_json, p.*
        FROM blog_comments c
        JOIN blog_posts p ON p.id = c.post_id
        WHERE c.status = 'active'
          AND (c.attachments_json LIKE ? OR c.emoji_payload_json LIKE ?)
        ORDER BY c.id DESC
        LIMIT 80
        """,
        (f"%{normalized_hash}%", f"%{normalized_hash}%"),
    ).fetchall()
    for row in comment_rows:
        payload = dict(row)
        if not _can_view_post(conn, user, payload):
            continue
        attachments = _safe_json_loads(payload.get("attachments_json"), [])
        emojis = _safe_json_loads(payload.get("emoji_payload_json"), [])
        for item in [*attachments, *emojis]:
            if not isinstance(item, dict) or str(item.get("file_hash") or "").strip().lower() != normalized_hash:
                continue
            return {
                "file_hash": normalized_hash,
                "original_filename": str(item.get("name") or item.get("filename") or normalized_hash),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "file_size": int(item.get("file_size") or 0),
                "image_width": _safe_int(item.get("width")),
                "image_height": _safe_int(item.get("height")),
            }

    fallback_row = conn.execute(
        """
        SELECT *
        FROM blog_media_assets
        WHERE file_hash = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (normalized_hash,),
    ).fetchone()
    return dict(fallback_row) if fallback_row is not None and _can_override_post_visibility(user) else None


def _normalize_comment_custom_emojis(conn, user: dict, payload: Any) -> list[dict]:
    user_pk, role, identity = _ensure_identity(user)
    raw_items = _safe_json_loads(payload, []) if isinstance(payload, str) else payload
    if not isinstance(raw_items, list):
        return []

    normalized_hashes: list[str] = []
    raw_by_hash: dict[str, dict] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        file_hash = str(item.get("file_hash") or "").strip().lower()
        if not file_hash or file_hash in raw_by_hash:
            continue
        raw_by_hash[file_hash] = item
        normalized_hashes.append(file_hash)
        if len(normalized_hashes) >= MAX_COMMENT_CUSTOM_EMOJIS:
            break

    owned_media_map = _load_user_owned_media_assets(conn, uploader_identity=identity, file_hashes=normalized_hashes)
    normalized: list[dict] = []
    for file_hash in normalized_hashes:
        media = owned_media_map.get(file_hash)
        if media is None:
            continue
        raw_item = raw_by_hash[file_hash]
        normalized.append(
            {
                "type": "custom",
                "name": str(raw_item.get("name") or media.get("original_filename") or "自定义表情")[:48],
                "file_hash": file_hash,
                "mime_type": str(media.get("mime_type") or "application/octet-stream"),
                "file_size": int(media.get("file_size") or 0),
                "width": int(media.get("image_width") or 0),
                "height": int(media.get("image_height") or 0),
                "image_url": f"/api/blog/image/{file_hash}",
            }
        )
    return normalized


def _normalize_comment_attachments(conn, user: dict, payload: Any) -> list[dict]:
    user_pk, role, identity = _ensure_identity(user)
    raw_items = _safe_json_loads(payload, []) if isinstance(payload, str) else payload
    if not isinstance(raw_items, list):
        return []

    normalized_hashes: list[str] = []
    raw_by_hash: dict[str, dict] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        file_hash = str(item.get("file_hash") or item.get("hash") or "").strip().lower()
        if not file_hash or file_hash in raw_by_hash:
            continue
        raw_by_hash[file_hash] = item
        normalized_hashes.append(file_hash)
        if len(normalized_hashes) >= MAX_COMMENT_ATTACHMENTS:
            break

    owned_media_map = _load_user_owned_media_assets(conn, uploader_identity=identity, file_hashes=normalized_hashes)
    normalized: list[dict] = []
    for file_hash in normalized_hashes:
        media = owned_media_map.get(file_hash)
        if media is None:
            continue
        raw_item = raw_by_hash[file_hash]
        normalized.append(
            {
                "type": "image",
                "name": str(raw_item.get("name") or media.get("original_filename") or "图片")[:128],
                "file_hash": file_hash,
                "mime_type": str(media.get("mime_type") or "application/octet-stream"),
                "file_size": int(media.get("file_size") or 0),
                "width": int(media.get("image_width") or 0),
                "height": int(media.get("image_height") or 0),
                "url": f"/api/blog/image/{file_hash}",
            }
        )
    return normalized


def _build_comment_preview(content: str, attachments: list[dict], emojis: list[dict]) -> str:
    text = str(content or "").strip()
    if text:
        return re.sub(r"\s+", " ", text)[:120]
    if attachments and emojis:
        return "[图片 + 自定义表情]"
    if attachments:
        return "[图片]"
    if emojis:
        return "[自定义表情]"
    return "[新评论]"


def _calculate_hot_score(post: dict) -> int:
    return (
        int(post.get("like_count") or 0) * 3
        + int(post.get("comment_count") or 0) * 2
        + int(post.get("view_count") or 0)
    )


def _maybe_notify_post_hot(conn, post_id: int, *, notify_callback=None) -> None:
    if notify_callback is None:
        return

    post = _get_post_raw(conn, post_id)
    if post is None or str(post.get("status") or "") != POST_STATUS_PUBLISHED:
        return
    if str(post.get("hot_notified_at") or "").strip():
        return

    like_count = int(post.get("like_count") or 0)
    comment_count = int(post.get("comment_count") or 0)
    if like_count < HOT_POST_MIN_LIKES and comment_count < HOT_POST_MIN_COMMENTS:
        return

    hot_score = _calculate_hot_score(post)
    if hot_score < HOT_POST_SCORE_THRESHOLD:
        return

    cursor = conn.execute(
        """
        UPDATE blog_posts
        SET hot_notified_at = ?
        WHERE id = ?
          AND (hot_notified_at IS NULL OR hot_notified_at = '')
        """,
        (_now_iso(), post_id),
    )
    if not cursor.rowcount:
        return

    notify_callback(conn, post, score=hot_score)


def _can_view_post(conn, user: dict, post: dict) -> bool:
    if not post:
        return False
    user_pk, role, identity = _ensure_identity(user)
    if _can_override_post_visibility(user):
        return True
    if str(post.get("author_identity") or "") == identity:
        return True
    if str(post.get("status") or "") != POST_STATUS_PUBLISHED:
        return False

    visibility = str(post.get("visibility") or VISIBILITY_PUBLIC)
    if visibility == VISIBILITY_PUBLIC:
        return True
    if visibility == VISIBILITY_CLASS:
        class_id = _safe_int(post.get("visible_class_id"))
        if class_id is None:
            return False
        if role == "student":
            row = conn.execute(
                "SELECT 1 FROM students WHERE id = ? AND class_id = ? LIMIT 1",
                (user_pk, class_id),
            ).fetchone()
            return row is not None
        return _is_teacher(user)
    if visibility == VISIBILITY_SELECTED:
        return f'"{identity}"' in str(post.get("visible_user_identities_json") or "[]")
    return False


def _get_post_raw(conn, post_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM blog_posts WHERE id = ? LIMIT 1", (post_id,)).fetchone()
    return dict(row) if row else None


def _resolve_post_media_assets(
    conn,
    user: dict,
    file_hashes: list[str],
    *,
    existing_post_id: Optional[int] = None,
) -> list[dict]:
    user_pk, role, identity = _ensure_identity(user)
    normalized_hashes = [str(item or "").strip().lower() for item in file_hashes if str(item or "").strip()]
    if not normalized_hashes:
        return []

    owned_media_map = _load_user_owned_media_assets(conn, uploader_identity=identity, file_hashes=normalized_hashes)
    existing_attachment_map: dict[str, dict] = {}
    if existing_post_id is not None:
        for item in list_attachments(conn, existing_post_id):
            existing_attachment_map[str(item.get("file_hash") or "").strip().lower()] = item

    resolved: list[dict] = []
    for file_hash in normalized_hashes:
        if file_hash in existing_attachment_map:
            item = dict(existing_attachment_map[file_hash])
            item["file_hash"] = file_hash
            resolved.append(item)
            continue

        media = owned_media_map.get(file_hash)
        if media is None:
            if _is_teacher(user):
                fallback = conn.execute(
                    """
                    SELECT *
                    FROM blog_media_assets
                    WHERE file_hash = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (file_hash,),
                ).fetchone()
                if fallback is not None:
                    resolved.append(dict(fallback))
                    continue
            raise ValueError("帖子中包含未上传或无权限使用的图片")
        resolved.append(media)
    return resolved


def _build_comment_tree(
    conn,
    user: dict,
    *,
    viewer_identity: str,
    post_author_identity: str,
    rows: list[dict],
    page: int,
    limit: int,
) -> list[dict]:
    comment_ids = [int(item["id"]) for item in rows]
    liked_ids: set[int] = set()
    if comment_ids:
        placeholders = ", ".join("?" for _ in comment_ids)
        liked_rows = conn.execute(
            f"""
            SELECT target_id
            FROM blog_likes
            WHERE target_type = ?
              AND user_identity = ?
              AND target_id IN ({placeholders})
            """,
            [TARGET_TYPE_COMMENT, viewer_identity, *comment_ids],
        ).fetchall()
        liked_ids = {int(row["target_id"]) for row in liked_rows}

    avatar_map = _load_comment_avatar_map(conn, rows)
    can_moderate = _can_override_post_visibility(user)
    by_parent: dict[Optional[int], list[dict]] = defaultdict(list)

    for row in rows:
        serialized = _serialize_comment(
            row,
            viewer_identity=viewer_identity,
            is_liked=int(row["id"]) in liked_ids,
            avatar_map=avatar_map,
            can_delete=(
                str(row.get("author_identity") or "") == viewer_identity
                or post_author_identity == viewer_identity
                or can_moderate
            ),
        )
        by_parent[_safe_int(row.get("parent_comment_id"))].append(serialized)

    root_comments = by_parent.get(None, [])
    offset = max(page - 1, 0) * limit
    visible_roots = root_comments[offset: offset + limit]

    def attach_replies(items: list[dict]) -> list[dict]:
        result: list[dict] = []
        for item in items:
            item["replies"] = attach_replies(by_parent.get(int(item["id"]), []))
            result.append(item)
        return result

    return attach_replies(visible_roots)


def _load_comment_avatar_map(conn, rows: list[dict]) -> dict[str, dict]:
    teacher_ids: set[int] = set()
    student_ids: set[int] = set()
    for row in rows:
        role = str(row.get("author_role") or "").strip().lower()
        user_pk = _safe_int(row.get("author_user_pk"))
        if user_pk is None:
            continue
        if role == "teacher":
            teacher_ids.add(user_pk)
        elif role == "student":
            student_ids.add(user_pk)

    result: dict[str, dict] = {}
    if teacher_ids:
        placeholders = ", ".join("?" for _ in teacher_ids)
        teacher_rows = conn.execute(
            f"SELECT id, avatar_file_hash FROM teachers WHERE id IN ({placeholders})",
            list(teacher_ids),
        ).fetchall()
        for row in teacher_rows:
            key = _build_identity("teacher", int(row["id"]))
            avatar_hash = str(row["avatar_file_hash"] or "")
            result[key] = {
                "avatar_hash": avatar_hash,
                "avatar_url": _build_avatar_url("teacher", row["id"], avatar_hash),
            }
    if student_ids:
        placeholders = ", ".join("?" for _ in student_ids)
        student_rows = conn.execute(
            f"SELECT id, avatar_file_hash FROM students WHERE id IN ({placeholders})",
            list(student_ids),
        ).fetchall()
        for row in student_rows:
            key = _build_identity("student", int(row["id"]))
            avatar_hash = str(row["avatar_file_hash"] or "")
            result[key] = {
                "avatar_hash": avatar_hash,
                "avatar_url": _build_avatar_url("student", row["id"], avatar_hash),
            }
    return result


def _collect_comment_subtree_ids(conn, comment_id: int) -> list[int]:
    rows = conn.execute(
        """
        WITH RECURSIVE subtree(id) AS (
            SELECT id FROM blog_comments WHERE id = ?
            UNION ALL
            SELECT child.id
            FROM blog_comments child
            JOIN subtree parent ON child.parent_comment_id = parent.id
        )
        SELECT id FROM subtree
        """,
        (comment_id,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _sync_post_attachments(conn, post_id: int, assets: list[dict]) -> None:
    normalized_assets = []
    seen_hashes: set[str] = set()
    for index, asset in enumerate(assets, start=1):
        file_hash = str(asset.get("file_hash") or "").strip().lower()
        if not file_hash or file_hash in seen_hashes:
            continue
        seen_hashes.add(file_hash)
        normalized_assets.append(
            {
                "file_hash": file_hash,
                "original_filename": str(asset.get("original_filename") or asset.get("name") or file_hash),
                "mime_type": str(asset.get("mime_type") or "application/octet-stream"),
                "file_size": int(asset.get("file_size") or 0),
                "image_width": _safe_int(asset.get("image_width") or asset.get("width")),
                "image_height": _safe_int(asset.get("image_height") or asset.get("height")),
                "display_order": index,
            }
        )

    if not normalized_assets:
        conn.execute("DELETE FROM blog_attachments WHERE post_id = ?", (post_id,))
        return

    keep_hashes = [asset["file_hash"] for asset in normalized_assets]
    placeholders = ", ".join("?" for _ in keep_hashes)
    conn.execute(
        f"DELETE FROM blog_attachments WHERE post_id = ? AND file_hash NOT IN ({placeholders})",
        [post_id, *keep_hashes],
    )

    existing_rows = conn.execute(
        "SELECT id, file_hash FROM blog_attachments WHERE post_id = ?",
        (post_id,),
    ).fetchall()
    existing_map = {str(row["file_hash"] or ""): int(row["id"]) for row in existing_rows}

    for asset in normalized_assets:
        existing_id = existing_map.get(asset["file_hash"])
        params = (
            asset["original_filename"],
            asset["mime_type"],
            asset["file_size"],
            asset["image_width"],
            asset["image_height"],
            asset["display_order"],
        )
        if existing_id is None:
            conn.execute(
                """
                INSERT INTO blog_attachments (
                    post_id, file_hash, original_filename, mime_type, file_size, image_width, image_height, display_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    asset["file_hash"],
                    asset["original_filename"],
                    asset["mime_type"],
                    asset["file_size"],
                    asset["image_width"],
                    asset["image_height"],
                    asset["display_order"],
                ),
            )
            continue

        conn.execute(
            """
            UPDATE blog_attachments
            SET original_filename = ?, mime_type = ?, file_size = ?, image_width = ?, image_height = ?, display_order = ?
            WHERE id = ?
            """,
            (*params, existing_id),
        )


def _log_moderation(conn, post_id: int, moderator_identity: str, moderator_role: str, moderator_pk: int, action: str, reason: str = ""):
    conn.execute(
        """
        INSERT INTO blog_moderation_logs (post_id, moderator_identity, moderator_role, moderator_user_pk, action, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (post_id, moderator_identity, moderator_role, moderator_pk, action, str(reason or "")),
    )


def _serialize_post_summary(row: dict, *, viewer_identity: str) -> dict:
    system_tags = _safe_json_loads(row.get("system_tags_json"), [])
    custom_tags = _safe_json_loads(row.get("tags_json"), [])
    tags = _merge_post_tags(system_tags, custom_tags)
    author_role = str(row.get("author_role") or "")
    author_user_pk = _safe_int(row.get("author_user_pk"))
    avatar_hash = str(row.get("author_avatar_hash") or "")
    author_display_mode = str(row.get("author_display_mode") or AUTHOR_DISPLAY_REAL)
    return {
        "id": int(row["id"]),
        "author": {
            "identity": str(row.get("author_identity") or ""),
            "role": author_role,
            "user_pk": author_user_pk,
            "display_name": str(row.get("author_display_name") or ""),
            "display_mode": author_display_mode,
            "is_anonymous": author_display_mode == AUTHOR_DISPLAY_ANONYMOUS,
            "avatar_url": _build_post_author_avatar_url(
                author_role,
                author_user_pk,
                avatar_hash,
                author_display_mode,
            ),
        },
        "title": str(row.get("title") or ""),
        "summary": str(row.get("summary") or ""),
        "cover_image_hash": str(row.get("cover_image_hash") or ""),
        "status": str(row.get("status") or POST_STATUS_PUBLISHED),
        "visibility": str(row.get("visibility") or VISIBILITY_PUBLIC),
        "visibility_label": _visibility_label(str(row.get("visibility") or VISIBILITY_PUBLIC)),
        "allow_comments": bool(row.get("allow_comments")),
        "is_pinned": bool(row.get("is_pinned")),
        "is_featured": bool(row.get("is_featured")),
        "view_count": int(row.get("view_count") or 0),
        "like_count": int(row.get("like_count") or 0),
        "comment_count": int(row.get("comment_count") or 0),
        "bookmark_count": int(row.get("bookmark_count") or 0),
        "author_display_mode": author_display_mode,
        "system_tags": _normalize_tags(system_tags),
        "custom_tags": _normalize_tags(custom_tags),
        "tags": tags,
        "created_at": str(row.get("created_at") or ""),
        "edited_at": str(row.get("edited_at") or "") or None,
        "updated_at": str(row.get("updated_at") or ""),
        "is_author": str(row.get("author_identity") or "") == viewer_identity,
    }


def _serialize_post_detail(
    row: dict,
    *,
    user: dict,
    viewer_identity: str,
    is_liked: bool,
    is_bookmarked: bool,
) -> dict:
    result = _serialize_post_summary(row, viewer_identity=viewer_identity)
    try:
        visible_user_identities = _safe_json_loads(row.get("visible_user_identities_json"), [])
    except Exception:
        visible_user_identities = []

    is_author = result["is_author"]
    result.update(
        {
            "content_md": str(row.get("content_md") or ""),
            "is_liked": bool(is_liked),
            "is_bookmarked": bool(is_bookmarked),
            "visible_class_id": _safe_int(row.get("visible_class_id")),
            "visible_user_identities": visible_user_identities if isinstance(visible_user_identities, list) else [],
            "permissions": {
                "can_edit": is_author,
                "can_delete": is_author,
                "can_toggle_comments": is_author or _can_override_post_visibility(user),
                "can_pin": _can_editorialize_posts(user),
                "can_feature": _can_editorialize_posts(user),
                "can_hide": _can_override_post_visibility(user),
            },
        }
    )
    return result


def _serialize_comment(
    row: dict,
    *,
    viewer_identity: str,
    is_liked: bool,
    avatar_map: dict[str, dict],
    can_delete: bool,
) -> dict:
    author_identity = str(row.get("author_identity") or "")
    avatar_entry = avatar_map.get(author_identity, {})
    attachments = _safe_json_loads(row.get("attachments_json"), [])
    emojis = _safe_json_loads(row.get("emoji_payload_json"), [])
    author_role = str(row.get("author_role") or "")
    author_user_pk = _safe_int(row.get("author_user_pk"))
    return {
        "id": int(row["id"]),
        "post_id": int(row["post_id"]),
        "parent_comment_id": _safe_int(row.get("parent_comment_id")),
        "author": {
            "identity": author_identity,
            "role": author_role,
            "user_pk": author_user_pk,
            "display_name": str(row.get("author_display_name") or ""),
            "avatar_url": avatar_entry.get("avatar_url") or _build_avatar_url(author_role, author_user_pk),
        },
        "content_md": str(row.get("content_md") or ""),
        "custom_emojis": emojis if isinstance(emojis, list) else [],
        "attachments": attachments if isinstance(attachments, list) else [],
        "like_count": int(row.get("like_count") or 0),
        "is_liked": bool(is_liked),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "can_delete": bool(can_delete),
        "can_reply": True,
    }
