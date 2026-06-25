"""Core service for the teacher lesson-plan (教案) content asset.

Owns CRUD, payload normalization, org-scoped sharing/visibility, tags, cover
auto-fill, and the clone-to-inherit flow. A *lesson plan* is one whole-semester
document = a cover (``cover``) + an ordered list of per-session tables
(``sessions``). It mirrors the exam-paper feature: TEXT uuid id, JSON content,
tags, a ``scope_level`` sharing ladder, and AI-generation status fields that the
list page polls to render placeholder cards.

Visibility reuses :mod:`material_scope_service` (the shared 系部→院级→校级 ladder)
with a column mapping (``scope_level`` plays the role of *openness*) plus an
extra ``private`` level that means owner-only. The owner's org unit is
snapshotted onto the row so the predicate needs no join.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..db.schema_lesson_plans import ensure_lesson_plan_schema
from . import material_scope_service as scope_core
from .organization_scope_service import load_teacher_org_scope
from .resource_access_service import is_super_admin_teacher

# ---------------------------------------------------------------------------
# Sharing scope
# ---------------------------------------------------------------------------
SCOPE_PRIVATE = "private"
SCOPE_DEPARTMENT = "department"
SCOPE_COLLEGE = "college"
SCOPE_SCHOOL = "school"

SCOPE_ORDER = (SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_COLLEGE, SCOPE_SCHOOL)
SCOPE_LABELS = {
    SCOPE_PRIVATE: "私有",
    SCOPE_DEPARTMENT: "本系部公开",
    SCOPE_COLLEGE: "本院级公开",
    SCOPE_SCHOOL: "全校公开",
}
# Column mapping so material_scope_service can read our lesson_plans row, where
# ``scope_level`` plays the part of *openness*.
_SCOPE_COLS = {
    "school": "school_code",
    "college": "college",
    "department": "department",
    "level": "scope_level",  # unused by the predicate but kept for completeness
    "openness": "scope_level",
}


def normalize_scope_level(value: Any, *, default: str = SCOPE_PRIVATE) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in SCOPE_ORDER else default


def scope_label(value: Any) -> str:
    return SCOPE_LABELS.get(normalize_scope_level(value), SCOPE_LABELS[SCOPE_PRIVATE])


def scope_options() -> list[dict[str, str]]:
    return [{"value": level, "label": SCOPE_LABELS[level]} for level in SCOPE_ORDER]


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def teacher_scope(conn: sqlite3.Connection, teacher_id: int | str) -> dict[str, str]:
    """The owner/viewer org scope used by the visibility predicate."""
    return load_teacher_org_scope(conn, int(teacher_id))


def can_view_plan(row: Any, viewer_scope: dict[str, str], *, is_super_admin: bool = False) -> bool:
    data = dict(row) if not isinstance(row, dict) else row
    level = normalize_scope_level(data.get("scope_level"))
    if level == SCOPE_PRIVATE:
        return bool(is_super_admin)
    # Re-key the row into the attr_* shape material_scope_service expects.
    proxy = {
        "attr_school_code": data.get("school_code"),
        "attr_college": data.get("college"),
        "attr_department": data.get("department"),
        "openness": level,
    }
    return scope_core.can_view(proxy, viewer_scope, is_super_admin=is_super_admin)


def visibility_where(
    viewer_scope: dict[str, str],
    teacher_id: int,
    *,
    is_super_admin: bool = False,
    alias: str = "",
) -> tuple[str, list[Any]]:
    """WHERE fragment: own rows (any scope incl. private) OR shared+visible.

    Returns ``(sql, params)`` — a self-contained boolean group.
    """
    prefix = f"{alias}." if alias else ""
    if is_super_admin:
        return "1=1", []
    shared_sql, shared_params = scope_core.build_visibility_filter(
        viewer_scope, is_super_admin=False, cols=_SCOPE_COLS, table_alias=alias.rstrip(".")
    )
    sql = (
        f"({prefix}teacher_id = ? "
        f"OR (lower(trim({prefix}scope_level)) != 'private' AND {shared_sql}))"
    )
    return sql, [int(teacher_id), *shared_params]


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------
COVER_FIELDS = (
    "course_name",
    "course_category",
    "credits",
    "total_hours",
    "teacher_name",
    "teaching_unit",
    "class_name",
    "textbook",
    "publisher",
    "semester_label",
    "school_name",
)

_PAYLOAD_CONTAINER_KEYS = (
    "lesson_plan",
    "lessonPlan",
    "payload",
    "result",
    "data",
    "content",
    "parsed",
)

_COVER_CONTAINER_KEYS = (
    "cover",
    "metadata",
    "basic_info",
    "basicInfo",
    "course_info",
    "courseInfo",
    "fields",
)

_SESSION_CONTAINER_KEYS = (
    "sessions",
    "lesson_sessions",
    "lessonSessions",
    "session_plans",
    "sessionPlans",
    "lessons",
    "tables",
    "items",
)

_COVER_ALIASES = {
    "course_name": ("course", "course_title", "courseTitle", "title", "课程名称", "课程名"),
    "course_category": ("category", "course_type", "courseType", "course_nature", "课程类别", "课程性质"),
    "credits": ("credit", "学分"),
    "total_hours": ("hours", "total_periods", "totalHours", "学时", "总学时"),
    "teacher_name": ("teacher", "teacher", "teacherName", "instructor", "授课教师", "教师"),
    "teaching_unit": ("unit", "department", "college", "teachingUnit", "教学单位", "学院", "系部"),
    "class_name": ("class", "className", "teaching_class", "teachingClass", "授课班级", "班级"),
    "textbook": ("book", "textbook_name", "textbookName", "教材", "使用教材"),
    "publisher": ("press", "publisher_name", "publisherName", "出版社", "出版单位"),
    "semester_label": ("semester", "term", "semesterLabel", "学期", "开课学期"),
    "school_name": ("school", "schoolName", "university", "学校", "学校名称"),
}

_SESSION_ALIASES = {
    "chapter": ("title", "topic", "lesson_title", "lessonTitle", "content_title", "授课章节", "章节", "主题"),
    "objectives": ("objective", "teaching_objectives", "teachingObjectives", "教学目标", "教学目的和要求"),
    "key_points": ("keypoints", "key_points_and_difficulties", "重点", "教学重点"),
    "difficulties": ("difficulty", "difficult_points", "难点", "教学难点"),
    "methods": ("method", "teaching_methods", "教学方法"),
    "means": ("teaching_means", "tools", "教学手段"),
    "process": ("content", "teaching_process", "teachingProcess", "lesson_process", "教学内容及过程", "教学过程"),
    "side_notes": ("notes", "sideNotes", "annotation", "旁批", "批注"),
    "post_notes": ("reflection", "postNotes", "after_class_notes", "教学后记"),
}

_SCHEDULE_ALIASES = {
    "date": ("session_date", "date_text", "授课日期", "日期"),
    "week_index": ("week", "weekIndex", "周次", "第几周"),
    "weekday": ("day", "weekdayIndex", "星期", "星期几"),
    "sections": ("period", "periods", "section", "sections_text", "节次", "课节"),
    "text": ("schedule_text", "scheduleText", "time", "class_time", "授课时间"),
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_value(data: dict[str, Any], primary: str, aliases: dict[str, tuple[str, ...]]) -> Any:
    for key in (primary, *aliases.get(primary, ())):
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return data.get(primary)


def _first_mapping(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_sequence(data: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    structured = data.get("structured")
    if isinstance(structured, dict):
        return _first_sequence(structured, keys)
    return []


def _unwrap_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    data = raw
    for _ in range(3):
        nested = None
        for key in _PAYLOAD_CONTAINER_KEYS:
            value = data.get(key)
            if isinstance(value, dict):
                nested = value
                break
        if not nested:
            break
        if _first_mapping(nested, _COVER_CONTAINER_KEYS) or _first_sequence(nested, _SESSION_CONTAINER_KEYS):
            data = nested
        else:
            break
    return data


def normalize_cover(raw: Any) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    return {field: _text(_first_value(data, field, _COVER_ALIASES)) for field in COVER_FIELDS}


def _normalize_schedule(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    week = _first_value(data, "week_index", _SCHEDULE_ALIASES)
    weekday = _first_value(data, "weekday", _SCHEDULE_ALIASES)
    try:
        week = int(week) if week not in (None, "") else None
    except (TypeError, ValueError):
        week = None
    try:
        weekday = int(weekday) if weekday not in (None, "") else None
    except (TypeError, ValueError):
        weekday = None
    return {
        "date": _text(_first_value(data, "date", _SCHEDULE_ALIASES)),
        "week_index": week,
        "weekday": weekday,
        "sections": _text(_first_value(data, "sections", _SCHEDULE_ALIASES)),
        "text": _text(_first_value(data, "text", _SCHEDULE_ALIASES)),
    }


def normalize_session(raw: Any, index: int) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    material_ids = data.get("source_material_ids")
    if not isinstance(material_ids, list):
        material_ids = []
    return {
        "index": index,
        "schedule": _normalize_schedule(data.get("schedule") if isinstance(data.get("schedule"), dict) else data),
        "chapter": _text(_first_value(data, "chapter", _SESSION_ALIASES)),
        "objectives": _text(_first_value(data, "objectives", _SESSION_ALIASES)),
        "key_points": _text(_first_value(data, "key_points", _SESSION_ALIASES)),
        "difficulties": _text(_first_value(data, "difficulties", _SESSION_ALIASES)),
        "methods": _text(_first_value(data, "methods", _SESSION_ALIASES)),
        "means": _text(_first_value(data, "means", _SESSION_ALIASES)),
        "process": _text(_first_value(data, "process", _SESSION_ALIASES)),
        "side_notes": _text(_first_value(data, "side_notes", _SESSION_ALIASES)),
        "post_notes": _text(_first_value(data, "post_notes", _SESSION_ALIASES)),
        "source_material_ids": [str(m) for m in material_ids if str(m).strip()],
        "ai_filled": bool(data.get("ai_filled")),
    }


def normalize_sessions(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    return [normalize_session(item, idx) for idx, item in enumerate(items, start=1)]


def normalize_lesson_plan_payload(raw: Any) -> dict[str, Any]:
    data = _unwrap_payload(raw)
    cover_raw = _first_mapping(data, _COVER_CONTAINER_KEYS) or data.get("cover") or {}
    sessions_raw = _first_sequence(data, _SESSION_CONTAINER_KEYS)
    return {
        "cover": normalize_cover(cover_raw),
        "sessions": normalize_sessions(sessions_raw),
    }


# ---------------------------------------------------------------------------
# Cover auto-fill
# ---------------------------------------------------------------------------
def build_cover_from_offering(
    conn: sqlite3.Connection, class_offering_id: int, *, teacher: dict[str, Any]
) -> dict[str, str]:
    """Auto-fill cover fields from the offering → course → class → teacher."""
    cover = normalize_cover({})
    cover["teacher_name"] = _text(teacher.get("name") or teacher.get("username"))
    org = teacher_scope(conn, int(teacher["id"]))
    cover["school_name"] = _text(org.get("school_name"))
    cover["teaching_unit"] = _text(org.get("college") or org.get("department"))

    row = conn.execute(
        """
        SELECT o.id AS offering_id, o.semester AS semester,
               co.name AS course_name, co.credits AS credits,
               co.total_hours AS total_hours, co.college AS course_college,
               co.department AS course_department, co.school_name AS course_school_name,
               cl.name AS class_name, cl.academic_class_name AS academic_class_name,
               tb.title AS textbook_title, tb.publisher AS textbook_publisher
        FROM class_offerings o
        LEFT JOIN courses co ON co.id = o.course_id
        LEFT JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
        WHERE o.id = ?
        """,
        (int(class_offering_id),),
    ).fetchone()
    if row:
        row = dict(row)
        cover["course_name"] = _text(row.get("course_name"))
        credits = row.get("credits")
        cover["credits"] = "" if credits in (None, "") else _text(credits)
        hours = row.get("total_hours")
        cover["total_hours"] = "" if hours in (None, "", 0) else _text(hours)
        cover["class_name"] = _text(row.get("academic_class_name") or row.get("class_name"))
        cover["semester_label"] = _text(row.get("semester"))
        cover["textbook"] = _text(row.get("textbook_title")) or cover["textbook"]
        cover["publisher"] = _text(row.get("textbook_publisher")) or cover["publisher"]
        if row.get("course_school_name"):
            cover["school_name"] = _text(row.get("course_school_name"))
        unit = _text(row.get("course_college") or row.get("course_department"))
        if unit:
            cover["teaching_unit"] = unit

        # Best-effort: pull 课程性质/总学时/学期 from the academic sync item.
        # Wrapped defensively — the table/column set varies across deployments.
        sync = None
        try:
            sync = conn.execute(
                """
                SELECT course_nature, course_total_hours_text, total_hours_text,
                       academic_year_name, academic_term_name
                FROM teacher_academic_course_sync_items
                WHERE class_offering_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(class_offering_id),),
            ).fetchone()
        except Exception:
            sync = None
        if sync:
                sync = dict(sync)
                cover["course_category"] = _text(sync.get("course_nature")) or cover["course_category"]
                if not cover["total_hours"]:
                    cover["total_hours"] = _text(
                        sync.get("course_total_hours_text") or sync.get("total_hours_text")
                    )
                term = " ".join(
                    p
                    for p in (
                        _text(sync.get("academic_year_name")),
                        _text(sync.get("academic_term_name")),
                    )
                    if p
                )
                if term:
                    cover["semester_label"] = term
    return cover


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _normalize_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = _load(tags, [])
    if not isinstance(tags, list):
        return []
    seen: list[str] = []
    for tag in tags:
        text = _text(tag)
        if text and text not in seen:
            seen.append(text)
    return seen[:24]


def create_lesson_plan(
    conn: sqlite3.Connection,
    *,
    teacher: dict[str, Any],
    title: str,
    cover: Any = None,
    sessions: Any = None,
    course_id: int | None = None,
    class_offering_id: int | None = None,
    source_type: str = "blank",
    status: str = "draft",
    scope_level: str = SCOPE_PRIVATE,
    tags: Any = None,
    ai_gen_task_id: str | None = None,
    ai_gen_status: str | None = None,
    ai_gen_progress: Any = None,
    inherited_from: str | None = None,
) -> str:
    ensure_lesson_plan_schema(conn)
    plan_id = _new_id()
    org = teacher_scope(conn, int(teacher["id"]))
    payload = normalize_lesson_plan_payload({"cover": cover, "sessions": sessions})
    now = _now()
    conn.execute(
        """
        INSERT INTO lesson_plans (
            id, teacher_id, title, course_id, class_offering_id,
            cover_json, sessions_json, tags_json, scope_level, source_type,
            status, ai_gen_task_id, ai_gen_status, ai_gen_error, ai_gen_progress,
            inherited_from, school_code, school_name, college, department,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            int(teacher["id"]),
            _text(title) or "教案",
            int(course_id) if course_id else None,
            int(class_offering_id) if class_offering_id else None,
            _dump(payload["cover"]),
            _dump(payload["sessions"]),
            _dump(_normalize_tags(tags)),
            normalize_scope_level(scope_level),
            source_type,
            status,
            ai_gen_task_id,
            ai_gen_status,
            None,
            _dump(ai_gen_progress or {}),
            inherited_from,
            _text(org.get("school_code")),
            _text(org.get("school_name")),
            _text(org.get("college")),
            _text(org.get("department")),
            now,
            now,
        ),
    )
    return plan_id


def get_lesson_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
    ensure_lesson_plan_schema(conn)
    row = conn.execute("SELECT * FROM lesson_plans WHERE id = ?", (str(plan_id),)).fetchone()
    if not row:
        return None
    return _hydrate(dict(row))


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    row["cover"] = normalize_cover(_load(row.get("cover_json"), {}))
    row["sessions"] = normalize_sessions(_load(row.get("sessions_json"), []))
    row["tags"] = _normalize_tags(_load(row.get("tags_json"), []))
    row["ai_gen_progress_data"] = _load(row.get("ai_gen_progress"), {})
    row["scope_level"] = normalize_scope_level(row.get("scope_level"))
    row["scope_label"] = scope_label(row["scope_level"])
    row["session_count"] = len(row["sessions"])
    return row


def list_lesson_plans(conn: sqlite3.Connection, *, teacher: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_lesson_plan_schema(conn)
    teacher_id = int(teacher["id"])
    is_super = is_super_admin_teacher(conn, teacher_id)
    viewer = teacher_scope(conn, teacher_id)
    where_sql, params = visibility_where(viewer, teacher_id, is_super_admin=is_super, alias="lp")
    rows = conn.execute(
        f"""
        SELECT lp.*, t.name AS owner_teacher_name
        FROM lesson_plans lp
        LEFT JOIN teachers t ON t.id = lp.teacher_id
        WHERE {where_sql}
        ORDER BY lp.updated_at DESC, lp.id DESC
        """,
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = _hydrate(dict(raw))
        row["is_owned"] = int(row.get("teacher_id") or 0) == teacher_id
        row["can_manage"] = row["is_owned"] or is_super
        result.append(_serialize_card(row))
    return result


def _serialize_card(row: dict[str, Any]) -> dict[str, Any]:
    cover = row.get("cover") or {}
    return {
        "id": row["id"],
        "title": row.get("title") or "教案",
        "course_name": cover.get("course_name") or "",
        "class_name": cover.get("class_name") or "",
        "semester_label": cover.get("semester_label") or "",
        "session_count": row.get("session_count") or 0,
        "tags": row.get("tags") or [],
        "scope_level": row.get("scope_level"),
        "scope_label": row.get("scope_label"),
        "source_type": row.get("source_type") or "blank",
        "status": row.get("status") or "draft",
        "ai_gen_status": row.get("ai_gen_status") or "",
        "ai_gen_error": row.get("ai_gen_error") or "",
        "ai_gen_progress": row.get("ai_gen_progress_data") or {},
        "is_owned": bool(row.get("is_owned")),
        "can_manage": bool(row.get("can_manage")),
        "owner_teacher_name": row.get("owner_teacher_name") or "",
        "inherited_from": row.get("inherited_from") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def serialize_card(row: dict[str, Any]) -> dict[str, Any]:
    """Public helper: serialize a hydrated row (from get_lesson_plan) to a card."""
    return _serialize_card(row)


def update_content(
    conn: sqlite3.Connection, plan_id: str, *, cover: Any, sessions: Any, status: str | None = None
) -> None:
    ensure_lesson_plan_schema(conn)
    payload = normalize_lesson_plan_payload({"cover": cover, "sessions": sessions})
    fields = ["cover_json = ?", "sessions_json = ?", "updated_at = ?"]
    params: list[Any] = [_dump(payload["cover"]), _dump(payload["sessions"]), _now()]
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    params.append(str(plan_id))
    conn.execute(f"UPDATE lesson_plans SET {', '.join(fields)} WHERE id = ?", params)


def update_attributes(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    title: str | None = None,
    scope_level: str | None = None,
    course_id: int | None = None,
    class_offering_id: int | None = None,
) -> None:
    ensure_lesson_plan_schema(conn)
    fields: list[str] = []
    params: list[Any] = []
    if title is not None:
        fields.append("title = ?")
        params.append(_text(title) or "教案")
    if scope_level is not None:
        fields.append("scope_level = ?")
        params.append(normalize_scope_level(scope_level))
    if course_id is not None:
        fields.append("course_id = ?")
        params.append(int(course_id) if course_id else None)
    if class_offering_id is not None:
        fields.append("class_offering_id = ?")
        params.append(int(class_offering_id) if class_offering_id else None)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(plan_id))
    conn.execute(f"UPDATE lesson_plans SET {', '.join(fields)} WHERE id = ?", params)


def update_tags(conn: sqlite3.Connection, plan_id: str, tags: Any) -> list[str]:
    ensure_lesson_plan_schema(conn)
    normalized = _normalize_tags(tags)
    conn.execute(
        "UPDATE lesson_plans SET tags_json = ?, updated_at = ? WHERE id = ?",
        (_dump(normalized), _now(), str(plan_id)),
    )
    return normalized


def delete_lesson_plan(conn: sqlite3.Connection, plan_id: str) -> None:
    ensure_lesson_plan_schema(conn)
    conn.execute("DELETE FROM lesson_plans WHERE id = ?", (str(plan_id),))


def set_generation_status(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    status: str | None = None,
    ai_gen_status: str | None = None,
    ai_gen_error: str | None = None,
    progress: Any = None,
    task_id: str | None = None,
) -> None:
    ensure_lesson_plan_schema(conn)
    fields: list[str] = []
    params: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if ai_gen_status is not None:
        fields.append("ai_gen_status = ?")
        params.append(ai_gen_status)
    if ai_gen_error is not None:
        fields.append("ai_gen_error = ?")
        params.append(ai_gen_error)
    if progress is not None:
        fields.append("ai_gen_progress = ?")
        params.append(_dump(progress))
    if task_id is not None:
        fields.append("ai_gen_task_id = ?")
        params.append(task_id)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(plan_id))
    conn.execute(f"UPDATE lesson_plans SET {', '.join(fields)} WHERE id = ?", params)


def clone_for_inherit(
    conn: sqlite3.Connection, source_plan_id: str, *, teacher: dict[str, Any]
) -> str:
    """Copy a shared plan into the calling teacher's own private library.

    The cover's identity fields (授课教师/教学单位/学校/班级) are rewritten to the
    inheriting teacher's context; the teaching content is preserved so the new
    owner can adapt it.
    """
    source = get_lesson_plan(conn, source_plan_id)
    if not source:
        raise ValueError("教案不存在")
    org = teacher_scope(conn, int(teacher["id"]))
    cover = dict(source.get("cover") or {})
    cover["teacher_name"] = _text(teacher.get("name") or teacher.get("username"))
    cover["school_name"] = _text(org.get("school_name")) or cover.get("school_name", "")
    cover["teaching_unit"] = _text(org.get("college") or org.get("department")) or cover.get(
        "teaching_unit", ""
    )
    cover["class_name"] = ""  # the new owner re-binds their own class
    new_title = f"{source.get('title') or '教案'}（继承）"
    return create_lesson_plan(
        conn,
        teacher=teacher,
        title=new_title,
        cover=cover,
        sessions=source.get("sessions"),
        source_type=source.get("source_type") or "blank",
        status="draft",
        scope_level=SCOPE_PRIVATE,
        tags=source.get("tags"),
        inherited_from=str(source_plan_id),
    )
