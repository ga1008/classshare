from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote

from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from ..db.schema_blog_closed_loop import comment_media_rows, insert_comment_media
from . import blog_moderation_states as mod_states
from . import blog_persona_service as persona_service
from . import blog_safety_service as safety
from .academic_service import china_today
from .blog_image_policy import is_suitable_news_cover_dimensions
from .blog_section_service import (
    DEFAULT_BLOG_SECTION_KEY,
    list_blog_sections as list_blog_section_catalog,
    resolve_blog_section_key,
)
from .learning_progress_service import build_student_public_cultivation_badge


POST_STATUS_DRAFT = "draft"
POST_STATUS_PUBLISHED = "published"
POST_STATUS_HIDDEN = "hidden"
POST_STATUS_MODERATED = "moderated"
POST_STATUS_DELETED = "deleted"

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


class BlogRevisionConflict(ValueError):
    """The caller must reload/reconcile its edit; never silently overwrite."""


class BlogModerationLocked(PermissionError):
    """A moderator-owned state that the author cannot change by editing."""


# Legacy hide statuses: P3 converges them to "published" + moderation_state
# (startup backfill); still recognised so an unconverged row stays locked.
MODERATED_POST_STATUSES = frozenset({POST_STATUS_HIDDEN, POST_STATUS_MODERATED})

# moderation_state (plan §4) decides visibility since P3.
MODERATION_STATE_NONE = mod_states.STATE_NONE
MODERATION_STATE_PASSED = mod_states.STATE_PASSED
MODERATION_STATE_LIMITED = mod_states.STATE_LIMITED
# The author sees their own post in feeds while it waits for review.
_OWN_FEED_STATES = (*mod_states.PUBLIC_STATES, mod_states.STATE_PENDING)


def _post_for_mutation(
    conn,
    user: dict,
    post_id: int,
    expected_updated_at: Optional[str],
    expected_revision: Optional[int] = None,
) -> dict:
    # All editing routes share this lock, including old Web callers without a
    # revision. It also orders attachment metadata and post deletion on Pg.
    suffix = " FOR UPDATE" if get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM blog_posts WHERE id = ?" + suffix, (int(post_id),)).fetchone()
    if row is None:
        raise ValueError("帖子不存在")
    post = dict(row)
    if post["author_identity"] != _ensure_identity(user)[2]:
        raise PermissionError("没有权限修改此帖子")
    conflict = BlogRevisionConflict("帖子已被更新，请重新读取并核对后再操作；当前输入不会覆盖新内容。")
    if expected_revision is not None:
        # The content revision only moves on content edits, so a comment or a
        # pin while the author is editing no longer turns into a conflict.
        if _safe_int(expected_revision) != _safe_int(post.get("content_revision") or 1):
            raise conflict
    elif expected_updated_at is not None and (not isinstance(expected_updated_at, str) or expected_updated_at != str(post.get("updated_at") or "")):
        raise conflict
    return post


DUPLICATE_SUBMIT_WINDOW_SECONDS = 10


def _recent_duplicate_cutoff() -> str:
    return (datetime.now() - timedelta(seconds=DUPLICATE_SUBMIT_WINDOW_SECONDS)).isoformat()


def _is_integrity_error(exc: BaseException) -> bool:
    # sqlite3.IntegrityError and psycopg's IntegrityError/UniqueViolation are
    # not wrapped by the DB layer; match them by class without importing drivers.
    return any(cls.__name__ in {"IntegrityError", "UniqueViolation"} for cls in type(exc).__mro__)


def _insert_once(conn, sql: str, params) -> bool:
    """Run an ``INSERT … WHERE NOT EXISTS``; return whether a row was added.

    Only for toggles whose transaction has written nothing yet: when a
    concurrent duplicate wins the race on a UNIQUE constraint, the whole
    transaction is rolled back (PostgreSQL would otherwise stay aborted) and
    the caller reports the settled state instead of a 500.
    """
    try:
        return bool(conn.execute(sql, params).rowcount)
    except Exception as exc:
        if not _is_integrity_error(exc):
            raise
        conn.rollback()
        return False


def _escape_like_pattern(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _next_post_revision(previous: str) -> str:
    now = _now_iso()
    if now <= str(previous or ""):
        try:
            return (datetime.fromisoformat(previous) + timedelta(microseconds=1)).isoformat()
        except (TypeError, ValueError):
            pass
    return now


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


def _hydrate_cover_media_metadata(conn, rows: list[dict]) -> None:
    """Attach cover dimensions in one query so list serialization stays N+1 free."""
    hashes = sorted({str(row.get("cover_image_hash") or "").strip().lower() for row in rows} - {""})
    if not hashes:
        return
    placeholders = ", ".join("?" for _ in hashes)
    asset_rows = conn.execute(
        f"""
        SELECT file_hash, uploader_identity, image_width, image_height
        FROM blog_media_assets
        WHERE file_hash IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        """,
        hashes,
    ).fetchall()
    by_owner: dict[tuple[str, str], dict] = {}
    by_hash: dict[str, dict] = {}
    for asset_row in asset_rows:
        asset = dict(asset_row)
        file_hash = str(asset.get("file_hash") or "").strip().lower()
        owner = str(asset.get("uploader_identity") or "").strip()
        by_owner.setdefault((file_hash, owner), asset)
        by_hash.setdefault(file_hash, asset)
    for row in rows:
        file_hash = str(row.get("cover_image_hash") or "").strip().lower()
        identity = str(row.get("author_identity") or "").strip()
        asset = by_owner.get((file_hash, identity)) or by_hash.get(file_hash)
        if not asset:
            continue
        row["cover_image_width"] = _safe_int(asset.get("image_width"))
        row["cover_image_height"] = _safe_int(asset.get("image_height"))


def _assistant_cover_is_presentable(row: dict) -> bool:
    if str(row.get("author_role") or "") != "assistant":
        return True
    if not str(row.get("cover_image_hash") or "").strip():
        return False
    width = _safe_int(row.get("cover_image_width"))
    height = _safe_int(row.get("cover_image_height"))
    # Legacy assets without metadata remain visible; newly crawled assets always
    # carry dimensions and are evaluated by the shared crawler/display policy.
    if width is None or height is None:
        return True
    return is_suitable_news_cover_dimensions(width, height)


def _presentation_cover(row: dict) -> dict[str, Any]:
    stored_hash = str(row.get("cover_image_hash") or "").strip().lower()
    presentable_hash = stored_hash if _assistant_cover_is_presentable(row) else ""
    author_role = str(row.get("author_role") or "")
    return {
        "hash": presentable_hash,
        "kind": "source" if presentable_hash else ("editorial" if author_role == "assistant" else "none"),
        "width": _safe_int(row.get("cover_image_width")),
        "height": _safe_int(row.get("cover_image_height")),
    }


def _strip_rejected_assistant_cover_markdown(content_md: str, row: dict) -> str:
    stored_hash = str(row.get("cover_image_hash") or "").strip().lower()
    if not stored_hash or _assistant_cover_is_presentable(row):
        return str(content_md or "")
    image_pattern = re.compile(
        rf"!\[[^\]\r\n]*\]\(/api/blog/image/{re.escape(stored_hash)}\)[ \t]*"
        rf"(?:\r?\n){{1,3}}(?:>[^\r\n]*(?:\r?\n|$))?",
        re.IGNORECASE,
    )
    cleaned = image_pattern.sub("\n", str(content_md or ""), count=1)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


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


AVATAR_KIND_PERSON = "person"
AVATAR_KIND_ANON = "anon"
AVATAR_KIND_ASSISTANT = "assistant"


def _build_avatar_url(role: str, user_pk: Any, avatar_hash: str = "") -> str:
    normalized_role = str(role or "").strip().lower()
    normalized_user_pk = _safe_int(user_pk)
    if normalized_role not in {"teacher", "student"} or normalized_user_pk is None:
        # Never fall back to the parameterless endpoint: it serves the *viewer's*
        # own avatar. The client renders anon/assistant/initials from avatar_kind.
        return ""
    revision = quote(str(avatar_hash or "default"), safe="")
    return (
        f"/api/profile/avatar?role={quote(normalized_role, safe='')}"
        f"&user_id={normalized_user_pk}&v={revision}"
    )


def _generate_summary(content_md: str, limit: int = MAX_SUMMARY_LENGTH) -> str:
    # Markdown structures must be removed BEFORE punctuation stripping, or the
    # image/link syntax breaks apart and its URL leaks into the summary text.
    text = str(content_md or "")
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"^\s*>\s*配图来源[:：].*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"/api/blog/image/[0-9a-fA-F]{16,}", "", text)
    text = re.sub(r"[#*`>\-\[\]()!|~]", " ", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:max(limit - 3, 0)].rstrip() + "..."


def _estimate_reading_minutes(row: dict) -> int:
    content = str(row.get("content_md") or "")
    content_length = len(content) if content else int(row.get("content_length") or 0)
    if content_length <= 0:
        content_length = len(str(row.get("summary") or "")) + len(str(row.get("title") or ""))
    return max(1, (content_length + 499) // 500)


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
        from .teaching_lifecycle_service import lock_teaching_parent
        lock_teaching_parent(conn, "class", int(class_id))
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
    role = str(user.get("role") or "").strip().lower()
    if role == "assistant":
        # Internal pipelines (housekeeper, crawler) read every published post.
        return f"{prefix}status = '{POST_STATUS_PUBLISHED}'", []
    public_state = mod_states.public_state_sql(table_alias)
    own_states = ", ".join(f"'{state}'" for state in _OWN_FEED_STATES)
    own = f"({prefix}author_identity = ? AND COALESCE({prefix}moderation_state, 'none') IN ({own_states}))"
    if role == "teacher":
        # Teachers see every audience, but sanctioned or unreviewed posts of
        # others only on the review desk (plan §4.1).
        return f"{prefix}status = '{POST_STATUS_PUBLISHED}' AND ({public_state} OR {own})", [viewer_identity]

    return (
        f"""
        {prefix}status = '{POST_STATUS_PUBLISHED}'
        AND (
            {own}
            OR ({public_state} AND (
            {prefix}visibility = '{VISIBILITY_PUBLIC}'
            OR (
                {prefix}visibility = '{VISIBILITY_CLASS}'
                AND (
                    {prefix}visible_class_id IN (
                        SELECT class_id FROM students WHERE id = ?
                    )
                    OR (
                        {prefix}visible_class_offering_id IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM class_offering_class_links cocl_m
                            JOIN students st_m ON st_m.id = ?
                            WHERE cocl_m.offering_id = {prefix}visible_class_offering_id
                              AND cocl_m.class_id = st_m.class_id
                        )
                    )
                )
            )
            OR (
                {prefix}visibility = '{VISIBILITY_SELECTED}'
                AND {prefix}visible_user_identities_json LIKE ?
            )
            ))
        )
        """.strip(),
        [viewer_identity, viewer_user_pk, viewer_user_pk, f'%"{viewer_identity}"%'],
    )


def get_blog_sections(conn, user: dict) -> list[dict[str, Any]]:
    """Return the data-driven section catalog with visibility-safe post counts."""
    user_pk, _role, identity = _ensure_identity(user)
    sections = list_blog_section_catalog(conn)
    visibility_sql, params = _build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
    )
    rows = conn.execute(
        f"""
        SELECT section_key, COUNT(*) AS post_count
        FROM blog_posts
        WHERE ({visibility_sql})
          AND status = ?
        GROUP BY section_key
        """,
        [*params, POST_STATUS_PUBLISHED],
    ).fetchall()
    count_map = {
        str(row["section_key"] or DEFAULT_BLOG_SECTION_KEY): int(row["post_count"] or 0)
        for row in rows
    }
    return [
        {
            **section,
            "post_count": count_map.get(str(section["section_key"]), 0),
        }
        for section in sections
    ]


def _resolve_optional_blog_section(conn, value: Any) -> str | None:
    if not str(value or "").strip():
        return None
    return resolve_blog_section_key(conn, value, fallback=None)


def _trending_order_expression(table_alias: str = "") -> str:
    """Return a backend-safe, time-decayed ranking expression.

    Views are capped so a repeatedly opened old post cannot permanently crowd
    newer discussions out of the discovery surfaces.
    """
    prefix = f"{table_alias}." if table_alias else ""
    base = (
        f"({prefix}like_count * 3 + {prefix}comment_count * 2 + {prefix}bookmark_count * 2 + "
        f"CASE WHEN {prefix}view_count > 50 THEN 50 ELSE {prefix}view_count END)"
    )
    if get_configured_db_engine() == "postgres":
        age_weeks = (
            "GREATEST(COALESCE(EXTRACT(EPOCH FROM "
            f"(CURRENT_TIMESTAMP - NULLIF({prefix}created_at, '')::timestamp)) / 604800.0, 0), 0)"
        )
    else:
        age_weeks = (
            f"CASE WHEN julianday('now') > julianday({prefix}created_at) "
            f"THEN (julianday('now') - julianday({prefix}created_at)) / 7.0 ELSE 0 END"
        )
    return f"({base} / (1.0 + {age_weeks}))"


def _record_post_view(conn, post_id: int, viewer_identity: str, *, viewed_at: str | None = None) -> bool:
    """Record one unique view per authenticated identity and clock hour."""
    now = str(viewed_at or _now_iso())
    view_bucket = now[:13]
    insert_sql = """
        INSERT INTO blog_post_views (
            post_id, viewer_identity, view_bucket, first_viewed_at, last_viewed_at
        ) VALUES (?, ?, ?, ?, ?)
    """
    if get_configured_db_engine() == "postgres":
        insert_sql += " ON CONFLICT (post_id, viewer_identity, view_bucket) DO NOTHING"
    else:
        insert_sql = insert_sql.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
    cursor = conn.execute(insert_sql, (post_id, viewer_identity, view_bucket, now, now))
    inserted = bool(cursor.rowcount)
    if inserted:
        conn.execute("UPDATE blog_posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
    else:
        conn.execute(
            """
            UPDATE blog_post_views
            SET last_viewed_at = ?, view_events = view_events + 1
            WHERE post_id = ? AND viewer_identity = ? AND view_bucket = ?
            """,
            (now, post_id, viewer_identity, view_bucket),
        )
    return inserted


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


def _load_public_cultivation_badge(conn, student_id: Any) -> dict[str, Any] | None:
    normalized_id = _safe_int(student_id)
    if normalized_id is None:
        return None
    try:
        return build_student_public_cultivation_badge(conn, normalized_id)
    except Exception as exc:
        print(f"[BLOG] 公开修为称号加载失败 student_id={normalized_id}: {exc}")
        return None


class AuthorContext(dict):
    """identity -> cultivation badge, plus the batch-loaded live author profiles.

    Every list path already builds one of these per page and hands it to the
    serializer, so personas resolve with one profile query per role instead of
    one per post.
    """

    def __init__(self, badges: dict[str, dict], profiles: dict[str, dict[str, str]]):
        super().__init__(badges)
        self.profiles = profiles


def _build_author_cultivation_badge_map(conn, rows: list[dict]) -> AuthorContext:
    student_ids: set[int] = set()
    face_identities: set[str] = set()
    for row in rows:
        role = str(row.get("author_role") or "").strip().lower()
        display_mode = str(row.get("author_display_mode") or "").strip().lower()
        user_pk = _safe_int(row.get("author_user_pk"))
        if display_mode != AUTHOR_DISPLAY_ANONYMOUS:
            face_identities.add(str(row.get("author_identity") or ""))
        # Badges belong to the real-name face only; a pen name must not carry
        # the student's cultivation rank (it would help unmask them).
        if role == "student" and persona_service.is_public_identity_mode(role, display_mode) and user_pk is not None:
            student_ids.add(user_pk)

    badge_map: dict[str, dict] = {}
    for student_id in student_ids:
        badge = _load_public_cultivation_badge(conn, student_id)
        if badge:
            badge_map[_build_identity("student", student_id)] = badge
    return AuthorContext(badge_map, persona_service.load_profiles(conn, face_identities))


def _build_post_author_snapshot(conn, user: dict, *, author_display_mode: Any = None) -> dict[str, Any]:
    user_pk, role, identity = _ensure_identity(user)
    profile = _load_user_profile_snapshot(conn, role, user_pk)
    real_name = str(profile.get("name") or user.get("name") or "").strip()
    nickname = str(profile.get("nickname") or user.get("nickname") or "").strip()
    class_name = str(profile.get("class_name") or "").strip()
    display_mode = _normalize_author_display_mode(user, author_display_mode)

    if role == "student" and display_mode == AUTHOR_DISPLAY_NICKNAME:
        if not nickname:
            raise ValueError("请先在个人中心设置笔名（昵称）后再用笔名发帖")
        problem = persona_service.pen_name_problem(conn, nickname, self_role=role, self_pk=user_pk)
        if problem:
            raise ValueError(f"{problem}，请到个人中心修改后再用笔名发帖")
        display_name = nickname
    elif role == "student" and display_mode == AUTHOR_DISPLAY_ANONYMOUS:
        display_name = "匿名同学"
    elif role == "assistant":
        display_name = str(user.get("name") or "管家").strip() or "管家"
    else:
        display_name = real_name or nickname or str(user.get("name") or "").strip()

    is_anonymous = role == "student" and display_mode == AUTHOR_DISPLAY_ANONYMOUS
    # The class tag identifies a student; only the real-name face carries it.
    system_tags = [class_name] if role == "student" and display_mode == AUTHOR_DISPLAY_REAL and class_name else []
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
        return ""
    return _build_avatar_url(role, user_pk, avatar_hash)


def _avatar_kind(role: Any, display_mode: Any = "") -> str:
    if str(display_mode or "").strip().lower() == AUTHOR_DISPLAY_ANONYMOUS:
        return AVATAR_KIND_ANON
    if str(role or "").strip().lower() == "assistant":
        return AVATAR_KIND_ASSISTANT
    return AVATAR_KIND_PERSON


# Transitional "楼主" mask (P0). The P2 persona service replaces this with a
# single resolver, but until then every comment of an anonymous post's author
# must be rendered without identity, avatar, badge or real name.
OP_ANON_DISPLAY_NAME = "楼主"


def is_anonymous_post_author(post: dict | None, actor_identity: Any) -> bool:
    if not post:
        return False
    return (
        str(post.get("author_display_mode") or "") == AUTHOR_DISPLAY_ANONYMOUS
        and str(post.get("author_identity") or "") == str(actor_identity or "")
        and bool(str(actor_identity or ""))
    )


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
    section_key: str = DEFAULT_BLOG_SECTION_KEY,
    author_display_mode: str = AUTHOR_DISPLAY_REAL,
    visibility: str = VISIBILITY_PUBLIC,
    visible_class_id: Optional[int] = None,
    visible_class_offering_id: Optional[int] = None,
    visible_user_identities: Optional[list[str]] = None,
    allow_comments: bool = True,
    tags: Optional[list[str]] = None,
    status: str = POST_STATUS_PUBLISHED,
    allow_anonymous_comments: bool = True,
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
    normalized_offering_id = (
        _safe_int(visible_class_offering_id)
        if normalized_visibility == VISIBILITY_CLASS
        else None
    )
    normalized_tags = _normalize_tags(tags or [])
    normalized_section_key = resolve_blog_section_key(
        conn,
        section_key,
        require_user_posts=role != "assistant",
    )
    normalized_status = status if status in {POST_STATUS_DRAFT, POST_STATUS_PUBLISHED} else POST_STATUS_PUBLISHED
    media_assets = _resolve_post_media_assets(
        conn,
        user,
        _extract_blog_image_hashes(normalized_content),
    )
    author_snapshot = _build_post_author_snapshot(conn, user, author_display_mode=author_display_mode)

    # A double-clicked "发布" (or a retried request) must not create two posts.
    duplicate = conn.execute(
        """
        SELECT id, status, created_at FROM blog_posts
        WHERE author_identity = ? AND title = ? AND content_md = ? AND created_at >= ?
        ORDER BY id DESC LIMIT 1
        """,
        (identity, normalized_title, normalized_content, _recent_duplicate_cutoff()),
    ).fetchone()
    if duplicate is not None:
        return {
            "id": int(duplicate["id"]),
            "status": str(duplicate["status"] or normalized_status),
            "created_at": str(duplicate["created_at"] or ""),
            "deduplicated": True,
        }

    if role != "assistant":
        safety.enforce_post_rate_limit(conn, identity)
    review_plan = _plan_post_review(
        conn, role, normalized_status, normalized_title, normalized_content, " ".join(normalized_tags),
    )

    now = _now_iso()
    is_published = normalized_status == POST_STATUS_PUBLISHED
    post_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO blog_posts (
            author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
            author_avatar_hash, author_avatar_mime,
            section_key, title, content_md, summary, cover_image_hash,
            status, visibility, visible_class_id, visible_class_offering_id, visible_user_identities_json,
            allow_comments, system_tags_json, tags_json, created_at, updated_at,
            moderation_state, moderated_by, published_at, content_revision, allow_anonymous_comments
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            author_snapshot["identity"],
            author_snapshot["role"],
            author_snapshot["user_pk"],
            author_snapshot["display_name"],
            author_snapshot["display_mode"],
            author_snapshot["avatar_hash"],
            author_snapshot["avatar_mime"],
            normalized_section_key,
            normalized_title,
            normalized_content,
            _generate_summary(normalized_content),
            _extract_first_image_hash(normalized_content),
            normalized_status,
            normalized_visibility,
            normalized_class_id,
            normalized_offering_id,
            json.dumps(normalized_identities, ensure_ascii=False),
            1 if allow_comments else 0,
            json.dumps(author_snapshot["system_tags"], ensure_ascii=False),
            json.dumps(normalized_tags, ensure_ascii=False),
            now,
            now,
            review_plan.state if review_plan else MODERATION_STATE_NONE,
            review_plan.moderated_by if review_plan else "",
            now if is_published else None,
            1 if allow_anonymous_comments else 0,
        ),
    )
    _sync_post_attachments(conn, post_id, media_assets)
    moderation_state = _queue_post_review(conn, post_id, 1, review_plan)
    if is_published and mod_states.is_public_state(moderation_state):
        from .blog_notifications import best_effort

        best_effort(conn, _after_post_public, conn, post_id)
    return {
        "id": post_id, "status": normalized_status, "created_at": now, "content_revision": 1,
        "moderation_state": moderation_state,
    }


def _plan_post_review(conn, role: str, status: str, *texts: Any, already_public: bool = False):
    """L1 + initial moderation state for a post that is (or becomes) published."""
    if status != POST_STATUS_PUBLISHED:
        return None
    verdict = safety.check_text(conn, *texts)
    if verdict.level == safety.LEVEL_BLOCK:
        if role != "assistant":
            raise safety.BlogSafetyBlocked(verdict.categories)
        # Platform pipelines are never refused mid-run; a person reviews instead.
        verdict = safety.LocalVerdict(safety.LEVEL_FLAG, verdict.categories)
    return safety.plan_review(target_type="post", role=role, verdict=verdict, already_public=already_public)


def _queue_post_review(conn, post_id: int, revision: int, plan) -> str:
    if plan is None:
        return MODERATION_STATE_NONE
    if safety.enqueue_review(conn, target_type="post", target_id=post_id, revision=revision, plan=plan) == "quota":
        return mod_states.STATE_FLAGGED if plan.state == mod_states.STATE_PENDING else plan.state
    return plan.state


def update_post(
    conn,
    user: dict,
    post_id: int,
    *,
    title: Optional[str] = None,
    content_md: Optional[str] = None,
    section_key: Optional[str] = None,
    author_display_mode: Optional[str] = None,
    visibility: Optional[str] = None,
    visible_class_id: Optional[int] = None,
    visible_user_identities: Optional[list[str]] = None,
    allow_comments: Optional[bool] = None,
    tags: Optional[list[str]] = None,
    status: Optional[str] = None,
    expected_updated_at: Optional[str] = None,
    allow_anonymous_comments: Optional[bool] = None,
    expected_revision: Optional[int] = None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    post = _post_for_mutation(conn, user, post_id, expected_updated_at, expected_revision)

    updates: list[str] = []
    params: list[Any] = []
    next_content = str(post.get("content_md") or "")
    attachments_need_sync = False
    now = _next_post_revision(str(post.get("updated_at") or ""))

    if title is not None:
        normalized_title = str(title or "").strip()[:MAX_TITLE_LENGTH]
        if not normalized_title:
            raise ValueError("标题不能为空")
        updates.append("title = ?")
        params.append(normalized_title)

    if section_key is not None:
        updates.append("section_key = ?")
        params.append(
            resolve_blog_section_key(
                conn,
                section_key,
                require_user_posts=role != "assistant",
            )
        )

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

    if allow_anonymous_comments is not None:
        updates.append("allow_anonymous_comments = ?")
        params.append(1 if allow_anonymous_comments else 0)

    if tags is not None:
        updates.append("tags_json = ?")
        params.append(json.dumps(_normalize_tags(tags), ensure_ascii=False))

    current_status = str(post.get("status") or "")
    current_state = str(post.get("moderation_state") or MODERATION_STATE_NONE)
    sanctioned = current_status in MODERATED_POST_STATUSES or current_state in mod_states.SANCTIONED_STATES
    next_status = current_status
    if status is not None and status in {POST_STATUS_DRAFT, POST_STATUS_PUBLISHED} and status != current_status:
        # Only a moderator lifts a sanction; publishing or withdrawing never does.
        if sanctioned:
            raise BlogModerationLocked("该帖子正在处理中，暂不能自行发布或撤回")
        next_status = status
        updates.append("status = ?")
        params.append(status)
        if status == POST_STATUS_PUBLISHED:
            # First publication stamps the feed time; republishing keeps it.
            updates.append("published_at = COALESCE(published_at, ?)")
            params.append(now)

    review_plan = None
    text_changed = any(value is not None for value in (title, content_md, tags))
    if next_status == POST_STATUS_PUBLISHED and (text_changed or next_status != current_status):
        review_plan = _plan_post_review(
            conn, role, next_status, *_merged_review_text(post, title, content_md, tags),
            already_public=current_status == POST_STATUS_PUBLISHED and mod_states.is_public_state(current_state),
        )
        if sanctioned:
            # Only a person lifts a sanction (plan §4.1): the corrected text is
            # re-checked by AI for the moderator's information, but nothing
            # automatic (verdict, timeout, quota, failure) may publish it.
            review_plan = safety.ReviewPlan(mod_states.STATE_PENDING, mod_states.BY_RESUBMIT, review_plan.needs_ai)
    if review_plan is not None:
        updates.extend(["moderation_state = ?", "moderated_by = ?", "moderation_reason_code = ?", "moderation_note = ?"])
        params.extend([review_plan.state, review_plan.moderated_by, "", ""])

    if not updates:
        return {"id": post_id, "updated": False}

    next_revision = (_safe_int(post.get("content_revision")) or 1) + 1
    updates.extend(["edited_at = ?", "updated_at = ?", "content_revision = ?"])
    params.extend([now, now, next_revision, post_id])
    conn.execute(f"UPDATE blog_posts SET {', '.join(updates)} WHERE id = ?", params)

    moderation_state = current_state
    if review_plan is not None:
        if sanctioned:
            from .blog_moderation_service import withdraw_open_appeal_on_edit

            withdraw_open_appeal_on_edit(conn, post_id, identity)
        moderation_state = _queue_post_review(conn, post_id, next_revision, review_plan)

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

    from .blog_notifications import best_effort, set_post_target_state

    if next_status == POST_STATUS_PUBLISHED and mod_states.is_public_state(moderation_state):
        best_effort(conn, _after_post_public, conn, post_id)
    elif next_status != current_status:
        # Withdrawn to a draft: other people's cards about it lead nowhere now.
        best_effort(conn, set_post_target_state, conn, post, gone=True)
    return {
        "id": post_id, "updated": True, "edited_at": now, "updated_at": now, "content_revision": next_revision,
        "moderation_state": moderation_state,
    }


def _after_post_public(conn, post_id: int) -> None:
    """A post (again) visible to others: revive its cards, tell people it @-mentions."""
    from .blog_notifications import notify_post_mentions, set_post_target_state

    post = _get_post_raw(conn, post_id)
    if post is None:
        return
    set_post_target_state(conn, post, gone=False)
    notify_post_mentions(conn, post_id)


def _merged_review_text(post: dict, title: Optional[str], content_md: Optional[str], tags) -> tuple[str, str, str]:
    """The post's text after this edit: what L1 and the AI must look at."""
    merged_tags = tags if tags is not None else _safe_json_loads(post.get("tags_json"), [])
    return (
        str(title if title is not None else post.get("title") or ""),
        str(content_md if content_md is not None else post.get("content_md") or ""),
        " ".join(str(tag) for tag in (merged_tags or [])),
    )


def delete_post(conn, user: dict, post_id: int, *, expected_updated_at: Optional[str] = None) -> dict:
    from .blog_notifications import best_effort, set_post_target_state

    user_pk, role, identity = _ensure_identity(user)
    post = _post_for_mutation(conn, user, post_id, expected_updated_at)
    best_effort(conn, set_post_target_state, conn, post, gone=True, deleted=True)
    audited = conn.execute("SELECT 1 FROM blog_moderation_logs WHERE post_id = ? LIMIT 1", (post_id,)).fetchone()
    if audited is not None:
        # Moderation logs (incl. identity reveals) cascade with the post row,
        # so a moderated post is only hidden from everyone (plan §4.1 soft delete).
        from .blog_moderation_service import withdraw_open_appeal_on_edit

        now = _now_iso()
        withdraw_open_appeal_on_edit(conn, post_id, identity)
        conn.execute(
            "UPDATE blog_posts SET status = ?, deleted_at = ?, updated_at = ?, is_pinned = 0, is_featured = 0 WHERE id = ?",
            (POST_STATUS_DELETED, now, now, post_id),
        )
        return {"id": post_id, "deleted": True}

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
    query: Optional[str] = None,
    visibility_filter: Optional[str] = None,
    section_key: Optional[str] = None,
) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    offset = max(page - 1, 0) * limit
    visibility_sql, params = _build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
    )

    conditions = [visibility_sql]
    normalized_section_key = _resolve_optional_blog_section(conn, section_key)
    if normalized_section_key:
        conditions.append("section_key = ?")
        params.append(normalized_section_key)
    if author_identity:
        normalized_author_identity = str(author_identity).strip().lower()
        conditions.append("author_identity = ?")
        params.append(normalized_author_identity)
        if normalized_author_identity != identity:
            # "ta的帖子" lists the real-name face only: pen-name and anonymous
            # posts must never be linkable to the person behind them.
            conditions.append("author_display_mode = ?")
            params.append(AUTHOR_DISPLAY_REAL)
    if tag:
        normalized_tag = _escape_like_pattern(str(tag or "").strip())
        conditions.append(
            "(tags_json LIKE ? ESCAPE '\\'"
            " OR (author_display_mode = 'real_name' AND system_tags_json LIKE ? ESCAPE '\\'))"
        )
        params.extend([f'%"{normalized_tag}"%', f'%"{normalized_tag}"%'])
    if query:
        normalized_query = str(query or "").strip()
        if normalized_query:
            # LOWER on both sides keeps SQLite and PostgreSQL case-insensitive alike.
            like_query = f"%{_escape_like_pattern(normalized_query)}%"
            conditions.append(
                """
                (
                    LOWER(title) LIKE LOWER(?) ESCAPE '\\'
                    OR LOWER(summary) LIKE LOWER(?) ESCAPE '\\'
                    OR LOWER(content_md) LIKE LOWER(?) ESCAPE '\\'
                    OR LOWER(tags_json) LIKE LOWER(?) ESCAPE '\\'
                    OR (author_display_mode = 'real_name' AND LOWER(system_tags_json) LIKE LOWER(?) ESCAPE '\\')
                )
                """.strip()
            )
            params.extend([like_query, like_query, like_query, like_query, like_query])
    if visibility_filter in {VISIBILITY_PUBLIC, VISIBILITY_CLASS, VISIBILITY_SELECTED}:
        conditions.append("visibility = ?")
        params.append(visibility_filter)
    if sort == "featured":
        conditions.append("is_featured = 1")

    where_clause = " AND ".join(f"({item})" for item in conditions if item)
    if sort == "hot":
        order_clause = (
            "is_pinned DESC, is_featured DESC, "
            f"{_trending_order_expression()} DESC, created_at DESC, id DESC"
        )
    elif sort == "featured":
        order_clause = "is_featured DESC, featured_at DESC, created_at DESC, id DESC"
    else:
        # A draft published later is "new" when it is published, not when it
        # was first saved; legacy rows were backfilled with published_at = created_at.
        order_clause = "is_pinned DESC, is_featured DESC, COALESCE(published_at, created_at) DESC, id DESC"

    total = int(
        conn.execute(f"SELECT COUNT(*) AS total FROM blog_posts WHERE {where_clause}", params).fetchone()["total"]
    )
    rows = conn.execute(
        f"""
        SELECT id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
               author_avatar_hash, author_avatar_mime,
               section_key, title, summary, cover_image_hash,
               status, visibility, allow_comments, allow_anonymous_comments, is_pinned, is_featured,
               moderation_state, moderation_reason_code, moderation_note, content_revision, published_at,
               view_count, like_count, comment_count, bookmark_count,
               system_tags_json, tags_json, created_at, edited_at, updated_at,
               LENGTH(content_md) AS content_length,
               EXISTS(
                   SELECT 1 FROM blog_likes bl
                   WHERE bl.target_type = '{TARGET_TYPE_POST}'
                     AND bl.target_id = blog_posts.id
                     AND bl.user_identity = ?
               ) AS is_liked,
               EXISTS(
                   SELECT 1 FROM blog_bookmarks bb
                   WHERE bb.post_id = blog_posts.id
                     AND bb.user_identity = ?
               ) AS is_bookmarked
        FROM blog_posts
        WHERE {where_clause}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        [identity, identity, *params, limit, offset],
    ).fetchall()

    row_items = [dict(row) for row in rows]
    _hydrate_cover_media_metadata(conn, row_items)
    cultivation_badge_map = _build_author_cultivation_badge_map(conn, row_items)
    posts = [
        _serialize_post_summary(
            row,
            viewer_identity=identity,
            cultivation_badge_map=cultivation_badge_map,
        )
        for row in row_items
    ]
    return {
        "posts": posts,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def get_blog_topbar_summary(conn, user: dict, *, section_key: Optional[str] = None) -> dict[str, Any]:
    user_pk, _role, identity = _ensure_identity(user)
    today_iso = china_today().isoformat()
    normalized_section_key = _resolve_optional_blog_section(conn, section_key)

    visibility_sql, params = _build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
    )

    section_sql = " AND section_key = ?" if normalized_section_key else ""
    query_params = [*params]
    if normalized_section_key:
        query_params.append(normalized_section_key)
    query_params.append(today_iso)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS today_new_count
        FROM blog_posts
        WHERE ({visibility_sql})
          {section_sql}
          AND substr(created_at, 1, 10) = ?
        """,
        query_params,
    ).fetchone()
    today_new_count = int(row["today_new_count"] or 0) if row else 0
    return {
        "today": today_iso,
        "today_new_count": today_new_count,
    }


def get_blog_discovery(conn, user: dict, *, section_key: Optional[str] = None) -> dict[str, Any]:
    user_pk, _role, identity = _ensure_identity(user)
    normalized_section_key = _resolve_optional_blog_section(conn, section_key)
    visibility_sql, visibility_params = _build_post_visibility_sql(
        user,
        viewer_identity=identity,
        viewer_user_pk=user_pk,
    )
    published_where = f"({visibility_sql}) AND status = ?"
    published_params = [*visibility_params, POST_STATUS_PUBLISHED]
    if normalized_section_key:
        published_where += " AND section_key = ?"
        published_params.append(normalized_section_key)

    summary = get_blog_topbar_summary(conn, user, section_key=normalized_section_key)
    stats_row = conn.execute(
        f"""
        SELECT COUNT(*) AS visible_count,
               COALESCE(SUM(like_count), 0) AS like_count,
               COALESCE(SUM(comment_count), 0) AS comment_count,
               COALESCE(SUM(bookmark_count), 0) AS bookmark_count
        FROM blog_posts
        WHERE {published_where}
        """,
        published_params,
    ).fetchone()

    post_fields = f"""
        id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
        author_avatar_hash, author_avatar_mime,
        section_key, title, summary, cover_image_hash,
        status, visibility, allow_comments, allow_anonymous_comments, is_pinned, is_featured,
        moderation_state, moderation_reason_code, moderation_note, content_revision, published_at,
        view_count, like_count, comment_count, bookmark_count,
        system_tags_json, tags_json, created_at, edited_at, updated_at,
        LENGTH(content_md) AS content_length,
        EXISTS(
            SELECT 1 FROM blog_likes bl
            WHERE bl.target_type = '{TARGET_TYPE_POST}'
              AND bl.target_id = blog_posts.id
              AND bl.user_identity = ?
        ) AS is_liked,
        EXISTS(
            SELECT 1 FROM blog_bookmarks bb
            WHERE bb.post_id = blog_posts.id
              AND bb.user_identity = ?
        ) AS is_bookmarked
    """

    def serialize_rows(rows) -> list[dict]:
        row_items = [dict(row) for row in rows]
        _hydrate_cover_media_metadata(conn, row_items)
        badge_map = _build_author_cultivation_badge_map(conn, row_items)
        return [
            _serialize_post_summary(
                row,
                viewer_identity=identity,
                cultivation_badge_map=badge_map,
            )
            for row in row_items
        ]

    spotlight_rows = conn.execute(
        f"""
        SELECT {post_fields}
        FROM blog_posts
        WHERE {published_where}
          AND (is_pinned = 1 OR is_featured = 1)
        ORDER BY is_pinned DESC, is_featured DESC, featured_at DESC, created_at DESC, id DESC
        LIMIT 3
        """,
        [identity, identity, *published_params],
    ).fetchall()

    if len(spotlight_rows) < 3:
        fallback_rows = conn.execute(
            f"""
            SELECT {post_fields}
            FROM blog_posts
            WHERE {published_where}
            ORDER BY is_pinned DESC, is_featured DESC,
                     {_trending_order_expression()} DESC,
                     created_at DESC, id DESC
            LIMIT ?
            """,
            [identity, identity, *published_params, 3],
        ).fetchall()
        seen_ids = {int(row["id"]) for row in spotlight_rows}
        merged_rows = [*spotlight_rows]
        for row in fallback_rows:
            if int(row["id"]) in seen_ids:
                continue
            merged_rows.append(row)
            seen_ids.add(int(row["id"]))
            if len(merged_rows) >= 3:
                break
        spotlight_rows = merged_rows

    trending_rows = conn.execute(
        f"""
        SELECT {post_fields}
        FROM blog_posts
        WHERE {published_where}
        ORDER BY {_trending_order_expression()} DESC,
                 comment_count DESC, like_count DESC, created_at DESC, id DESC
        LIMIT 6
        """,
        [identity, identity, *published_params],
    ).fetchall()

    tag_rows = conn.execute(
        f"""
        SELECT system_tags_json, tags_json, author_display_mode, like_count, comment_count, view_count
        FROM blog_posts
        WHERE {published_where}
        ORDER BY created_at DESC, id DESC
        LIMIT 160
        """,
        published_params,
    ).fetchall()
    tag_map: dict[str, dict[str, Any]] = {}
    for row in tag_rows:
        row_dict = dict(row)
        heat = 1 + _calculate_hot_score(row_dict)
        is_real = str(row_dict.get("author_display_mode") or AUTHOR_DISPLAY_REAL) == AUTHOR_DISPLAY_REAL
        system_tags = row_dict.get("system_tags_json") if is_real else "[]"
        for tag in _merge_post_tags(system_tags, row_dict.get("tags_json")):
            bucket = tag_map.setdefault(tag, {"name": tag, "count": 0, "heat": 0})
            bucket["count"] += 1
            bucket["heat"] += heat
    hot_tags = sorted(
        tag_map.values(),
        key=lambda item: (int(item["heat"]), int(item["count"]), str(item["name"])),
        reverse=True,
    )[:12]

    author_rows = conn.execute(
        f"""
        SELECT author_identity, author_role, author_user_pk,
               MAX(author_display_name) AS author_display_name,
               MAX(author_display_mode) AS author_display_mode,
               COUNT(*) AS post_count,
               COALESCE(SUM(like_count * 3 + comment_count * 2 + view_count), 0) AS hot_score,
               MAX(created_at) AS latest_at
        FROM blog_posts
        WHERE {published_where}
          AND author_display_mode = ?
        GROUP BY author_identity, author_role, author_user_pk
        ORDER BY hot_score DESC, post_count DESC, latest_at DESC
        LIMIT 6
        """,
        [*published_params, AUTHOR_DISPLAY_REAL],
    ).fetchall()
    # Real-name faces only, one entry per person (pen names / anonymity would
    # otherwise be linked to the person through this rail).
    author_context = _build_author_cultivation_badge_map(conn, [dict(row) for row in author_rows])
    active_authors = []
    for row in author_rows:
        row_dict = dict(row)
        author_identity = str(row_dict.get("author_identity") or "")
        persona = persona_service.resolve(
            actor_identity=author_identity,
            actor_role=str(row_dict.get("author_role") or ""),
            actor_user_pk=row_dict.get("author_user_pk"),
            display_mode=AUTHOR_DISPLAY_REAL,
            snapshot_name=str(row_dict.get("author_display_name") or ""),
            profiles=author_context.profiles,
            badge=author_context.get(author_identity),
        )
        active_authors.append(
            {
                **persona,
                "post_count": int(row_dict.get("post_count") or 0),
                "hot_score": int(row_dict.get("hot_score") or 0),
            }
        )

    return {
        "sections": get_blog_sections(conn, user),
        "active_section": normalized_section_key or "",
        "summary": {
            **summary,
            "visible_count": int(stats_row["visible_count"] or 0) if stats_row else 0,
            "like_count": int(stats_row["like_count"] or 0) if stats_row else 0,
            "comment_count": int(stats_row["comment_count"] or 0) if stats_row else 0,
            "bookmark_count": int(stats_row["bookmark_count"] or 0) if stats_row else 0,
        },
        "spotlight_posts": serialize_rows(spotlight_rows),
        "trending_posts": serialize_rows(trending_rows),
        "hot_tags": hot_tags,
        "active_authors": active_authors,
    }


def get_post_detail(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    row = conn.execute("SELECT * FROM blog_posts WHERE id = ? LIMIT 1", (post_id,)).fetchone()
    if row is None:
        raise ValueError("帖子不存在")

    post_row = dict(row)
    if not _can_view_post(conn, user, post_row):
        raise PermissionError("没有权限查看此帖子")

    _hydrate_cover_media_metadata(conn, [post_row])

    if _record_post_view(conn, post_id, identity):
        post_row["view_count"] = int(post_row.get("view_count") or 0) + 1

    cultivation_badge_map = _build_author_cultivation_badge_map(conn, [post_row])
    result = _serialize_post_detail(
        post_row,
        user=user,
        viewer_identity=identity,
        is_liked=_is_liked(conn, identity, TARGET_TYPE_POST, post_id),
        is_bookmarked=_is_bookmarked(conn, identity, post_id),
        cultivation_badge_map=cultivation_badge_map,
    )
    _attach_moderation_view(conn, user, post_row, result)
    return result


def get_post_moderation_status(conn, user: dict, post_id: int) -> dict:
    """Light poll for the author's "审核中" banner (plan §5.2): state only."""
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if not _can_view_post(conn, user, post):
        raise PermissionError("没有权限查看此帖子")
    view: dict[str, Any] = {"permissions": {}, "is_author": str(post.get("author_identity") or "") == _ensure_identity(user)[2]}
    _attach_moderation_view(conn, user, post, view)
    return {"post_id": int(post_id), "content_revision": _safe_int(post.get("content_revision")) or 1,
            "moderation": view.get("moderation")}


def _moderation_view(conn, row: dict, *, moderator: bool, with_appeal: bool) -> dict[str, Any]:
    from . import blog_moderation_service as moderation

    state = str(row.get("moderation_state") or MODERATION_STATE_NONE)
    if state == mod_states.STATE_FLAGGED and not moderator:
        # "flagged" is a queue marker; the author is not told (plan §5.1).
        state = MODERATION_STATE_PASSED
    code = str(row.get("moderation_reason_code") or "")
    view = {
        "state": state,
        "label": mod_states.STATE_LABELS.get(state, ""),
        "is_public": mod_states.is_public_state(state),
        "reason_code": code if state in mod_states.SANCTIONED_STATES or moderator else "",
        "reason_label": mod_states.reason_label(code) if state in mod_states.SANCTIONED_STATES or moderator else "",
        "note": str(row.get("moderation_note") or "") if state in mod_states.SANCTIONED_STATES or moderator else "",
    }
    view["resubmitted"] = str(row.get("moderated_by") or "") == mod_states.BY_RESUBMIT
    if moderator:
        view["moderated_by"] = str(row.get("moderated_by") or "")
    if with_appeal:
        view["appeal"] = moderation.appeal_status(conn, row)
    return view


def _attach_moderation_view(conn, user: dict, post_row: dict, result: dict) -> None:
    from . import blog_moderation_service as moderation

    role = str(user.get("role") or "").strip().lower()
    moderator = moderation.is_blog_moderator(conn, user)
    is_author = bool(result.get("is_author"))
    state = str(post_row.get("moderation_state") or MODERATION_STATE_NONE)
    live = str(post_row.get("status") or "") == POST_STATUS_PUBLISHED
    permissions = result.setdefault("permissions", {})
    permissions.update({
        "can_moderate": moderator and live,
        # Any teacher may limit published content at once; a moderator reviews it.
        "can_emergency_limit": role == "teacher" and not moderator and live and mod_states.is_public_state(state),
        "can_reveal": moderator and str(post_row.get("author_display_mode") or "") in {AUTHOR_DISPLAY_NICKNAME, AUTHOR_DISPLAY_ANONYMOUS},
        "can_report": not is_author and role != "assistant",
        # The legacy private/visible toggle is replaced by the moderation menu.
        "can_hide": False,
    })
    if (is_author or moderator) and live:
        result["moderation"] = _moderation_view(conn, post_row, moderator=moderator, with_appeal=is_author)
    else:
        result["moderation"] = None
    if not moderator and result.get("moderation_state") == mod_states.STATE_FLAGGED:
        result["moderation_state"] = MODERATION_STATE_PASSED


def update_post_reading_progress(
    conn,
    user: dict,
    post_id: int,
    *,
    dwell_seconds: Any = 0,
    max_scroll_ratio: Any = 0,
) -> dict[str, Any]:
    _user_pk, _role, identity = _ensure_identity(user)
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    if not _can_view_post(conn, user, post):
        raise PermissionError("没有权限查看此帖子")
    try:
        normalized_dwell = max(0, min(int(float(dwell_seconds or 0)), 24 * 60 * 60))
    except (TypeError, ValueError):
        normalized_dwell = 0
    try:
        normalized_ratio = max(0.0, min(float(max_scroll_ratio or 0), 1.0))
    except (TypeError, ValueError):
        normalized_ratio = 0.0
    now = _now_iso()
    _record_post_view(conn, post_id, identity, viewed_at=now)
    bucket = now[:13]
    completed = 1 if normalized_ratio >= 0.9 else 0
    conn.execute(
        """
        UPDATE blog_post_views
        SET last_viewed_at = ?,
            dwell_seconds = CASE WHEN dwell_seconds < ? THEN ? ELSE dwell_seconds END,
            max_scroll_ratio = CASE WHEN max_scroll_ratio < ? THEN ? ELSE max_scroll_ratio END,
            completed = CASE WHEN completed = 1 OR ? = 1 THEN 1 ELSE 0 END
        WHERE post_id = ? AND viewer_identity = ? AND view_bucket = ?
        """,
        (
            now,
            normalized_dwell,
            normalized_dwell,
            normalized_ratio,
            normalized_ratio,
            completed,
            post_id,
            identity,
            bucket,
        ),
    )
    return {
        "post_id": post_id,
        "dwell_seconds": normalized_dwell,
        "max_scroll_ratio": normalized_ratio,
        "completed": bool(completed),
    }


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
    conditions = ["author_identity = ?", f"status <> '{POST_STATUS_DELETED}'"]
    params: list[Any] = [identity]

    if status_filter == POST_STATUS_DRAFT:
        conditions.append("status = ?")
        params.append(POST_STATUS_DRAFT)
    elif status_filter == POST_STATUS_PUBLISHED:
        conditions.append(mod_states.public_post_sql())
    elif status_filter == "reviewing":
        conditions.append(
            f"status = '{POST_STATUS_PUBLISHED}' AND moderation_state IN "
            f"('{mod_states.STATE_PENDING}', '{mod_states.STATE_APPEAL_PENDING}')"
        )
    elif status_filter in {"limited", POST_STATUS_HIDDEN, POST_STATUS_MODERATED}:
        conditions.append(f"moderation_state IN ('{mod_states.STATE_LIMITED}', '{mod_states.STATE_REMOVED}')")

    where_clause = " AND ".join(conditions)
    total = int(
        conn.execute(f"SELECT COUNT(*) AS total FROM blog_posts WHERE {where_clause}", params).fetchone()["total"]
    )
    rows = conn.execute(
        f"""
        SELECT id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode,
               author_avatar_hash, author_avatar_mime,
               section_key, title, summary, cover_image_hash,
               status, visibility, allow_comments, allow_anonymous_comments, is_pinned, is_featured,
               moderation_state, moderation_reason_code, moderation_note, content_revision, published_at,
               view_count, like_count, comment_count, bookmark_count,
               system_tags_json, tags_json, created_at, edited_at, updated_at,
               LENGTH(content_md) AS content_length,
               EXISTS(
                   SELECT 1 FROM blog_likes bl
                   WHERE bl.target_type = '{TARGET_TYPE_POST}'
                     AND bl.target_id = blog_posts.id
                     AND bl.user_identity = ?
               ) AS is_liked,
               EXISTS(
                   SELECT 1 FROM blog_bookmarks bb
                   WHERE bb.post_id = blog_posts.id
                     AND bb.user_identity = ?
               ) AS is_bookmarked
        FROM blog_posts
        WHERE {where_clause}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [identity, identity, *params, limit, offset],
    ).fetchall()
    row_items = [dict(row) for row in rows]
    _hydrate_cover_media_metadata(conn, row_items)
    cultivation_badge_map = _build_author_cultivation_badge_map(conn, row_items)
    posts = [
        _serialize_post_summary(
            row,
            viewer_identity=identity,
            cultivation_badge_map=cultivation_badge_map,
        )
        for row in row_items
    ]
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

    row_items = [dict(row) for row in rows]
    _hydrate_cover_media_metadata(conn, row_items)
    cultivation_badge_map = _build_author_cultivation_badge_map(conn, row_items)
    posts = []
    for row in row_items:
        post = _serialize_post_summary(
            row,
            viewer_identity=identity,
            cultivation_badge_map=cultivation_badge_map,
        )
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
    """Floors with their first replies (plan §7.1); see blog_comment_service."""
    from .blog_comment_service import list_floors

    # The legacy read (agent capability, api_get_post) keeps whole floors: up
    # to 100 floors and their replies, as the old tree did.
    return list_floors(conn, user, post_id, page=page, limit=limit, reply_preview=200, max_limit=100)


def notify_released_comment(conn, comment_id: int) -> None:
    """A comment held for review (L1 flag) notifies nobody when written; once
    it becomes visible, the post author / replied-to person get the usual notice."""
    from .blog_notifications import notify_new_comment

    row = conn.execute(
        "SELECT id, post_id, parent_comment_id, author_identity, author_role, author_user_pk, author_display_name, "
        "author_display_mode, content_md, attachments_json, emoji_payload_json FROM blog_comments WHERE id = ?",
        (int(comment_id),),
    ).fetchone()
    post = _get_post_raw(conn, int(row["post_id"])) if row else None
    if row is None or post is None:
        return
    item = dict(row)
    public_face = str(item.get("author_display_mode") or AUTHOR_DISPLAY_REAL) == AUTHOR_DISPLAY_REAL
    notify_new_comment(
        conn, post, int(item["id"]), _safe_int(item.get("parent_comment_id")),
        str(item.get("author_identity") or ""),
        str(item.get("author_role") or "") if public_face else "",
        item.get("author_user_pk") if public_face else None,
        str(item.get("author_display_name") or ""),
        _build_comment_preview(
            str(item.get("content_md") or ""),
            _safe_json_loads(item.get("attachments_json"), []),
            _safe_json_loads(item.get("emoji_payload_json"), []),
        ),
    )
    from .blog_notifications import best_effort, notify_comment_mentions

    best_effort(conn, notify_comment_mentions, conn, int(item["id"]))


def _resolve_comment_face(
    conn,
    user: dict,
    post: dict,
    *,
    role: str,
    identity: str,
    user_pk: int,
    requested_mode: Any,
    fallback_name: str,
) -> tuple[str, str, str]:
    """(stored mode, stored display name, snapshot) for a new comment.

    The stored display name is already the public face, so every legacy reader
    of ``author_display_name`` (AI context, agent reads, search) is safe too.
    """
    if role == "assistant":
        name = str(fallback_name or user.get("name") or "").strip() or "管家"
        return AUTHOR_DISPLAY_REAL, name, name
    mode = persona_service.normalize_mode(role, requested_mode)
    if is_anonymous_post_author(post, identity):
        # The anonymous post's author always speaks as "楼主" in their thread.
        return AUTHOR_DISPLAY_ANONYMOUS, OP_ANON_DISPLAY_NAME, OP_ANON_DISPLAY_NAME
    if mode == AUTHOR_DISPLAY_ANONYMOUS:
        if not bool(post.get("allow_anonymous_comments", 1)):
            raise ValueError("这篇帖子的作者没有开放匿名评论")
        code = persona_service.assign_anonymous_code(conn, int(post["id"]), identity)
        return AUTHOR_DISPLAY_ANONYMOUS, f"{persona_service.ANON_PREFIX}{code}", code
    if mode == AUTHOR_DISPLAY_NICKNAME:
        nickname = str(_load_user_profile_snapshot(conn, role, user_pk).get("nickname") or "").strip()
        if not nickname:
            raise ValueError("请先在个人中心设置笔名（昵称）后再用笔名评论")
        problem = persona_service.pen_name_problem(conn, nickname, self_role=role, self_pk=user_pk)
        if problem:
            raise ValueError(f"{problem}，请到个人中心修改后再用笔名评论")
        return AUTHOR_DISPLAY_NICKNAME, nickname, nickname
    name = str(fallback_name or user.get("name") or "").strip()
    return AUTHOR_DISPLAY_REAL, name, name


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
    author_display_mode: str = AUTHOR_DISPLAY_REAL,
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

    root_comment_id: Optional[int] = None
    if parent_comment_id is not None:
        parent = conn.execute(
            """
            SELECT id, post_id, author_identity, root_comment_id
            FROM blog_comments
            WHERE id = ? AND post_id = ? AND status = 'active'
              AND (COALESCE(moderation_state, 'none') IN ('none', 'passed', 'flagged') OR author_identity = ?)
            LIMIT 1
            """,
            (parent_comment_id, post_id, identity),
        ).fetchone()
        if parent is None:
            raise ValueError("回复的评论不存在")
        # Every reply belongs to the floor (top-level comment) of its parent.
        root_comment_id = _safe_int(parent["root_comment_id"]) or int(parent["id"])

    serialized_emojis = json.dumps(normalized_emojis, ensure_ascii=False) if normalized_emojis else ""
    serialized_attachments = json.dumps(normalized_attachments, ensure_ascii=False) if normalized_attachments else "[]"
    # Double-clicked "发送评论" must not post (or notify) twice.
    duplicate = conn.execute(
        """
        SELECT id, created_at FROM blog_comments
        WHERE post_id = ? AND author_identity = ? AND content_md = ?
          AND COALESCE(emoji_payload_json, '') = ? AND COALESCE(attachments_json, '[]') = ?
          AND COALESCE(parent_comment_id, 0) = ?
          AND status = 'active' AND created_at >= ?
        ORDER BY id DESC LIMIT 1
        """,
        (
            post_id,
            identity,
            normalized_content,
            serialized_emojis,
            serialized_attachments,
            int(parent_comment_id or 0),
            _recent_duplicate_cutoff(),
        ),
    ).fetchone()
    if duplicate is not None:
        return {
            "id": int(duplicate["id"]),
            "post_id": post_id,
            "created_at": str(duplicate["created_at"] or ""),
            "deduplicated": True,
        }

    if role == "assistant":
        # Housekeeper output is checked by blog_ai_service before it gets here.
        comment_plan = safety.ReviewPlan(MODERATION_STATE_NONE, "", False)
    else:
        safety.enforce_comment_rate_limit(conn, identity)
        verdict = safety.enforce_local_rules(conn, normalized_content)
        comment_plan = safety.plan_review(target_type="comment", role=role, verdict=verdict)

    now = _now_iso()
    comment_mode, display_name, snapshot = _resolve_comment_face(
        conn, user, post, role=role, identity=identity, user_pk=user_pk,
        requested_mode=author_display_mode, fallback_name=author_display_name,
    )
    comment_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO blog_comments (
            post_id, parent_comment_id,
            author_identity, author_role, author_user_pk, author_display_name,
            content_md, emoji_payload_json, attachments_json,
            status, created_at, updated_at,
            root_comment_id, reply_to_comment_id, author_display_mode, author_display_snapshot,
            moderation_state, moderated_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id,
            parent_comment_id,
            identity,
            role,
            user_pk,
            display_name,
            normalized_content,
            serialized_emojis,
            serialized_attachments,
            now,
            now,
            root_comment_id,
            parent_comment_id,
            comment_mode,
            snapshot,
            comment_plan.state,
            comment_plan.moderated_by,
        ),
    )
    if root_comment_id is None:
        # A top-level comment is its own floor.
        conn.execute("UPDATE blog_comments SET root_comment_id = id WHERE id = ?", (comment_id,))
    for file_hash, media_kind in sorted(
        comment_media_rows(
            {"content_md": normalized_content, "attachments_json": serialized_attachments, "emoji_payload_json": serialized_emojis}
        )
    ):
        insert_comment_media(conn, comment_id, post_id, file_hash, media_kind, now)
    conn.execute(
        "UPDATE blog_posts SET comment_count = comment_count + 1, updated_at = ? WHERE id = ?",
        (now, post_id),
    )
    if safety.enqueue_review(conn, target_type="comment", target_id=comment_id, revision=1, plan=comment_plan) == "quota":
        comment_plan = safety.ReviewPlan(
            mod_states.STATE_FLAGGED if comment_plan.state == mod_states.STATE_PENDING else comment_plan.state,
            mod_states.BY_QUOTA, False,
        )

    # A comment held for review (L1 flag) notifies nobody until it is visible.
    if notify_callback and mod_states.is_public_state(comment_plan.state):
        # The identity is still passed for self-notification suppression, but a
        # pen-name/anonymous commenter must not become a contactable actor.
        public_face = comment_mode == AUTHOR_DISPLAY_REAL
        notify_callback(
            conn,
            post,
            comment_id,
            parent_comment_id,
            identity,
            role if public_face else "",
            user_pk if public_face else None,
            display_name,
            _build_comment_preview(normalized_content, normalized_attachments, normalized_emojis),
        )
        from .blog_notifications import best_effort, notify_comment_mentions

        best_effort(conn, notify_comment_mentions, conn, comment_id)

    _maybe_notify_post_hot(conn, post_id, notify_callback=hot_notify_callback)
    return {"id": comment_id, "post_id": post_id, "created_at": now, "moderation_state": comment_plan.state}


def delete_comment(conn, user: dict, comment_id: int, *, reason: str = "") -> dict:
    """Soft delete (plan §4.2, §7.3); replies stay under a placeholder."""
    from .blog_comment_service import delete_comment as soft_delete

    return soft_delete(conn, user, comment_id, reason=reason)


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
            f"""
            SELECT c.id, c.post_id, p.*
            FROM blog_comments c
            JOIN blog_posts p ON p.id = c.post_id
            WHERE c.id = ? AND c.status = 'active'
              AND ({mod_states.public_state_sql('c')} OR c.author_identity = ?)
            LIMIT 1
            """,
            (target_id, identity),
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
        conn.execute(
            f"""
            UPDATE {count_table}
            SET like_count = CASE
                    WHEN COALESCE(like_count, 0) <= 0 THEN 0
                    ELSE like_count - 1
                END
            WHERE id = ?
            """,
            (target_id,),
        )
        row = conn.execute(f"SELECT like_count FROM {count_table} WHERE id = ?", (target_id,)).fetchone()
        return {
            "liked": False,
            "target_type": target_type,
            "target_id": target_id,
            "like_count": int(row["like_count"] or 0) if row else 0,
        }

    # A concurrent duplicate request (double click) must settle as "liked",
    # not as a unique-constraint 500 and not as a second count increment.
    inserted = _insert_once(
        conn,
        """
        INSERT INTO blog_likes (target_type, target_id, user_identity, user_role, user_pk, created_at)
        SELECT ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM blog_likes WHERE target_type = ? AND target_id = ? AND user_identity = ?
        )
        """,
        (target_type, target_id, identity, role, user_pk, _now_iso(), target_type, target_id, identity),
    )
    if inserted:
        from .blog_notifications import best_effort, notify_like

        conn.execute(f"UPDATE {count_table} SET like_count = like_count + 1 WHERE id = ?", (target_id,))
        best_effort(conn, notify_like, conn, target_type=target_type, target_id=target_id, liker_identity=identity)
    row = conn.execute(f"SELECT like_count FROM {count_table} WHERE id = ?", (target_id,)).fetchone()
    if inserted and target_type == TARGET_TYPE_POST:
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
        conn.execute(
            """
            UPDATE blog_posts
            SET bookmark_count = CASE
                    WHEN COALESCE(bookmark_count, 0) <= 0 THEN 0
                    ELSE bookmark_count - 1
                END
            WHERE id = ?
            """,
            (post_id,),
        )
        row = conn.execute("SELECT bookmark_count FROM blog_posts WHERE id = ?", (post_id,)).fetchone()
        return {"bookmarked": False, "post_id": post_id, "bookmark_count": int(row["bookmark_count"] or 0) if row else 0}

    inserted = _insert_once(
        conn,
        """
        INSERT INTO blog_bookmarks (post_id, user_identity, user_role, user_pk, created_at)
        SELECT ?, ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM blog_bookmarks WHERE post_id = ? AND user_identity = ?)
        """,
        (post_id, identity, role, user_pk, _now_iso(), post_id, identity),
    )
    if inserted:
        conn.execute("UPDATE blog_posts SET bookmark_count = bookmark_count + 1 WHERE id = ?", (post_id,))
    row = conn.execute("SELECT bookmark_count FROM blog_posts WHERE id = ?", (post_id,)).fetchone()
    return {"bookmarked": True, "post_id": post_id, "bookmark_count": int(row["bookmark_count"] or 1) if row else 1}


def _get_published_post_for_editorial(conn, post_id: int) -> dict:
    post = _get_post_raw(conn, post_id)
    if post is None:
        raise ValueError("帖子不存在")
    # Drafts belong to their author; editorial actions only apply to posts
    # that have been published (and, for hide/restore, moderated ones).
    if str(post.get("status") or "") == POST_STATUS_DRAFT:
        raise BlogModerationLocked("草稿尚未发布，不能执行此操作")
    return post


def _was_featured_before(conn, post_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM blog_moderation_logs WHERE post_id = ? AND action = 'feature' LIMIT 1",
        (post_id,),
    ).fetchone()
    return row is not None


def pin_post(conn, user: dict, post_id: int) -> dict:
    user_pk, role, identity = _ensure_identity(user)
    if not _can_editorialize_posts(user):
        raise PermissionError("仅教师可置顶帖子")

    post = _get_published_post_for_editorial(conn, post_id)
    if not mod_states.is_public_state(post.get("moderation_state")) and not bool(post.get("is_pinned")):
        raise BlogModerationLocked("审核中或受限的帖子不能置顶")

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

    post = _get_published_post_for_editorial(conn, post_id)
    if not mod_states.is_public_state(post.get("moderation_state")) and not bool(post.get("is_featured")):
        raise BlogModerationLocked("审核中或受限的帖子不能设为精华")

    new_value = 0 if bool(post.get("is_featured")) else 1
    # Toggling feature off and on again must not notify the author twice.
    should_notify = bool(new_value) and not _was_featured_before(conn, post_id)
    now = _now_iso()
    conn.execute(
        "UPDATE blog_posts SET is_featured = ?, featured_at = ?, updated_at = ? WHERE id = ?",
        (new_value, now if new_value else None, now, post_id),
    )
    _log_moderation(conn, post_id, identity, role, user_pk, "feature" if new_value else "unfeature")
    if should_notify and notify_callback and str(post.get("author_identity") or "") != identity:
        notify_callback(conn, post, identity, role, user_pk)
    return {"id": post_id, "is_featured": bool(new_value)}


def hide_post(conn, user: dict, post_id: int, reason: str = "") -> dict:
    """Legacy "转为私密 / 恢复可见" toggle, now a moderation action (plan §4.1).

    Limiting needs any teacher (emergency limit when not a blog moderator);
    restoring needs a blog moderator. Reason code "other" with the given note.
    """
    from . import blog_moderation_service as moderation

    post = _get_published_post_for_editorial(conn, post_id)
    note = str(reason or "").strip()[:300]
    if mod_states.is_public_state(post.get("moderation_state")):
        result = moderation.moderate(conn, user, "post", post_id, action="limit", reason_code="other", note=note)
    else:
        result = moderation.moderate(conn, user, "post", post_id, action="restore", note=note or "恢复可见")
    hidden = result["moderation_state"] in mod_states.SANCTIONED_STATES
    return {"id": post_id, "status": POST_STATUS_PUBLISHED, "is_private": hidden,
            "moderation_state": result["moderation_state"]}


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
    return execute_insert_returning_id(
        conn,
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
        f"""
        SELECT c.attachments_json, c.emoji_payload_json, p.*
        FROM blog_comments c
        JOIN blog_posts p ON p.id = c.post_id
        WHERE c.status = 'active'
          AND ({mod_states.public_state_sql('c')} OR c.author_identity = ?)
          AND (c.attachments_json LIKE ? OR c.emoji_payload_json LIKE ?)
        ORDER BY c.id DESC
        LIMIT 80
        """,
        (identity, f"%{normalized_hash}%", f"%{normalized_hash}%"),
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


def _calculate_trending_score(post: dict) -> int:
    base_score = (
        int(post.get("like_count") or 0) * 3
        + int(post.get("comment_count") or 0) * 2
        + int(post.get("bookmark_count") or 0) * 2
        + min(int(post.get("view_count") or 0), 50)
    )
    created_raw = str(post.get("created_at") or "").strip()
    if not created_raw:
        return base_score
    try:
        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        now = datetime.now(tz=created_at.tzinfo) if created_at.tzinfo else datetime.now()
        age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    except (TypeError, ValueError, OverflowError):
        return base_score
    return max(0, int(round(base_score / (1.0 + age_days / 7.0))))


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
    status = str(post.get("status") or "")
    if status == POST_STATUS_DELETED:
        # Soft-deleted: gone for everyone, kept only for the moderation record.
        from .blog_moderation_service import is_blog_moderator

        return is_blog_moderator(conn, user)
    if str(post.get("author_identity") or "") == identity:
        return True
    if role == "assistant":
        return status != POST_STATUS_DRAFT
    if status not in {POST_STATUS_PUBLISHED, *MODERATED_POST_STATUSES}:
        # Never someone else's draft, not even for moderators (plan §4.1).
        return False
    public = status == POST_STATUS_PUBLISHED and mod_states.is_public_state(post.get("moderation_state"))
    if role == "teacher":
        if public:
            return True
        from .blog_moderation_service import is_blog_moderator

        return is_blog_moderator(conn, user)
    if not public:
        return False

    visibility = str(post.get("visibility") or VISIBILITY_PUBLIC)
    if visibility == VISIBILITY_PUBLIC:
        return True
    if visibility == VISIBILITY_CLASS:
        class_id = _safe_int(post.get("visible_class_id"))
        offering_id = _safe_int(post.get("visible_class_offering_id"))
        if class_id is None and offering_id is None:
            return False
        if role == "student":
            if class_id is not None:
                row = conn.execute(
                    "SELECT 1 FROM students WHERE id = ? AND class_id = ? LIMIT 1",
                    (user_pk, class_id),
                ).fetchone()
                if row is not None:
                    return True
            if offering_id is not None:
                row = conn.execute(
                    """
                    SELECT 1 FROM class_offering_class_links cocl_m
                    JOIN students st_m ON st_m.id = ?
                    WHERE cocl_m.offering_id = ? AND cocl_m.class_id = st_m.class_id
                    LIMIT 1
                    """,
                    (user_pk, offering_id),
                ).fetchone()
                if row is not None:
                    return True
            return False
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


def _load_comment_avatar_map(conn, rows: list[dict]) -> AuthorContext:
    """Persona context for a comment page: same shape as the post-list context."""
    return _build_author_cultivation_badge_map(conn, rows)


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


def _log_moderation(
    conn,
    post_id: int,
    moderator_identity: str,
    moderator_role: str,
    moderator_pk: int,
    action: str,
    reason: str = "",
    *,
    target_type: str = TARGET_TYPE_POST,
    target_id: Optional[int] = None,
    reason_code: str = "",
):
    conn.execute(
        """
        INSERT INTO blog_moderation_logs (
            post_id, moderator_identity, moderator_role, moderator_user_pk, action, reason,
            target_type, target_id, reason_code, note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id,
            moderator_identity,
            moderator_role,
            moderator_pk,
            action,
            str(reason or ""),
            target_type,
            target_id if target_id is not None else post_id,
            str(reason_code or ""),
            str(reason or ""),
        ),
    )


def _serialize_post_summary(
    row: dict,
    *,
    viewer_identity: str,
    cultivation_badge_map: dict[str, dict] | None = None,
) -> dict:
    author_role = str(row.get("author_role") or "")
    author_display_mode = str(row.get("author_display_mode") or AUTHOR_DISPLAY_REAL)
    author_identity = str(row.get("author_identity") or "")
    is_author = author_identity == viewer_identity
    # Older pen-name/anonymous rows stored a class tag; it identifies the
    # student, so only real-name posts expose system tags.
    system_tags = (
        _safe_json_loads(row.get("system_tags_json"), [])
        if persona_service.is_public_identity_mode(author_role, author_display_mode)
        else []
    )
    custom_tags = _safe_json_loads(row.get("tags_json"), [])
    tags = _merge_post_tags(system_tags, custom_tags)
    badges = cultivation_badge_map or {}
    profiles = getattr(badges, "profiles", {}) or {}
    if author_identity not in profiles:
        # Callers without a batch context still get the stored snapshot
        # (a one-entry map, so the shared batch map is never copied or mutated).
        profiles = {
            author_identity: {
                "name": str(row.get("author_display_name") or ""),
                "nickname": "",
                "avatar_hash": str(row.get("author_avatar_hash") or ""),
            }
        }
    author = persona_service.resolve(
        actor_identity=author_identity,
        actor_role=author_role,
        actor_user_pk=row.get("author_user_pk"),
        display_mode=author_display_mode,
        snapshot_name=str(row.get("author_display_name") or ""),
        profiles=profiles,
        badge=badges.get(author_identity),
    )
    presentation_cover = _presentation_cover(row)
    return {
        "id": int(row["id"]),
        "author": author,
        "title": str(row.get("title") or ""),
        "section_key": str(row.get("section_key") or DEFAULT_BLOG_SECTION_KEY),
        "summary": str(row.get("summary") or ""),
        "cover_image_hash": presentation_cover["hash"],
        "cover_image_kind": presentation_cover["kind"],
        "cover_image_width": presentation_cover["width"],
        "cover_image_height": presentation_cover["height"],
        "status": str(row.get("status") or POST_STATUS_PUBLISHED),
        "visibility": str(row.get("visibility") or VISIBILITY_PUBLIC),
        "visibility_label": _visibility_label(str(row.get("visibility") or VISIBILITY_PUBLIC)),
        "allow_comments": bool(row.get("allow_comments")),
        "allow_anonymous_comments": bool(row.get("allow_anonymous_comments", 1)),
        "is_pinned": bool(row.get("is_pinned")),
        "is_featured": bool(row.get("is_featured")),
        "view_count": int(row.get("view_count") or 0),
        "like_count": int(row.get("like_count") or 0),
        "comment_count": int(row.get("comment_count") or 0),
        "bookmark_count": int(row.get("bookmark_count") or 0),
        "hot_score": _calculate_trending_score(row),
        "reading_minutes": _estimate_reading_minutes(row),
        "is_liked": bool(row.get("is_liked")),
        "is_bookmarked": bool(row.get("is_bookmarked")),
        "author_display_mode": author_display_mode,
        "system_tags": _normalize_tags(system_tags),
        "custom_tags": _normalize_tags(custom_tags),
        "tags": tags,
        "created_at": str(row.get("created_at") or ""),
        "published_at": str(row.get("published_at") or "") or None,
        "edited_at": str(row.get("edited_at") or "") or None,
        "updated_at": str(row.get("updated_at") or ""),
        "content_revision": _safe_int(row.get("content_revision")) or 1,
        # "flagged" is a review-desk marker, invisible to authors and readers.
        "moderation_state": (
            MODERATION_STATE_PASSED
            if str(row.get("moderation_state") or "") == mod_states.STATE_FLAGGED
            else str(row.get("moderation_state") or MODERATION_STATE_NONE)
        ),
        "moderation_label": mod_states.STATE_LABELS.get(str(row.get("moderation_state") or ""), "") if is_author else "",
        "is_author": is_author,
    }


def _serialize_post_detail(
    row: dict,
    *,
    user: dict,
    viewer_identity: str,
    is_liked: bool,
    is_bookmarked: bool,
    cultivation_badge_map: dict[str, dict] | None = None,
) -> dict:
    result = _serialize_post_summary(
        row,
        viewer_identity=viewer_identity,
        cultivation_badge_map=cultivation_badge_map,
    )
    try:
        visible_user_identities = _safe_json_loads(row.get("visible_user_identities_json"), [])
    except Exception:
        visible_user_identities = []

    is_author = result["is_author"]
    result.update(
        {
            "content_md": _strip_rejected_assistant_cover_markdown(str(row.get("content_md") or ""), row),
            "is_liked": bool(is_liked),
            "is_bookmarked": bool(is_bookmarked),
            "visible_class_id": _safe_int(row.get("visible_class_id")),
            "visible_class_offering_id": _safe_int(row.get("visible_class_offering_id")),
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
    post_author_identity: str = "",
    post_display_mode: str = AUTHOR_DISPLAY_REAL,
) -> dict:
    author_identity = str(row.get("author_identity") or "")
    attachments = _safe_json_loads(row.get("attachments_json"), [])
    emojis = _safe_json_loads(row.get("emoji_payload_json"), [])
    profiles = getattr(avatar_map, "profiles", {}) or {}
    author_mode = str(row.get("author_display_mode") or AUTHOR_DISPLAY_REAL)
    author = persona_service.resolve(
        actor_identity=author_identity,
        actor_role=str(row.get("author_role") or ""),
        actor_user_pk=row.get("author_user_pk"),
        display_mode=author_mode,
        snapshot_name=str(
            (row.get("author_display_snapshot") if author_mode == AUTHOR_DISPLAY_ANONYMOUS else None)
            or row.get("author_display_name")
            or ""
        ),
        profiles=profiles,
        badge=avatar_map.get(author_identity),
        post_author_identity=post_author_identity,
        post_display_mode=post_display_mode,
        is_comment=True,
    )
    withheld = bool(row.get("_withheld"))
    is_mine = bool(author_identity) and author_identity == viewer_identity
    moderator = bool(row.get("_viewer_moderator"))
    if withheld:
        # A thread anchor only: no author, no content, no actions.
        author = persona_service._persona(kind="withheld", display_name="", avatar_kind="anon")
    state = str(row.get("moderation_state") or MODERATION_STATE_NONE)
    shown_state = MODERATION_STATE_PASSED if state == mod_states.STATE_FLAGGED and not moderator else state
    moderation = None
    if (is_mine or moderator) and not withheld and shown_state not in {MODERATION_STATE_NONE, MODERATION_STATE_PASSED}:
        moderation = {
            "state": shown_state,
            "label": mod_states.STATE_LABELS.get(shown_state, ""),
            "reason_label": mod_states.reason_label(row.get("moderation_reason_code")),
            "note": str(row.get("moderation_note") or ""),
        }
    return {
        "id": int(row["id"]),
        "post_id": int(row["post_id"]),
        "parent_comment_id": _safe_int(row.get("parent_comment_id")),
        "author": author,
        "is_mine": is_mine and not withheld,
        "content_md": "" if withheld else str(row.get("content_md") or ""),
        "custom_emojis": emojis if isinstance(emojis, list) and not withheld else [],
        "attachments": attachments if isinstance(attachments, list) and not withheld else [],
        "like_count": 0 if withheld else int(row.get("like_count") or 0),
        "is_liked": bool(is_liked) and not withheld,
        "created_at": "" if withheld else str(row.get("created_at") or ""),
        "updated_at": "" if withheld else str(row.get("updated_at") or ""),
        "can_delete": bool(can_delete) and not withheld,
        "can_reply": not withheld,
        "withheld": withheld,
        "moderation": moderation,
    }
