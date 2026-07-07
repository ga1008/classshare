"""Core service for the teacher assessment-plan (考核计划表 / 过程材料) content asset.

Owns CRUD, payload normalization, org-scoped sharing/visibility, tags, field
auto-fill from a class offering, signature binding/resolution, docx export and
HTML preview, and the clone-to-inherit flow. An *assessment plan* is one course's
《课程考核计划表》 = template fields + 考核项目 list (合计 100 分) + 注释, with optional
bound 命题教师 / 系主任 signatures.

It mirrors :mod:`lesson_plan_service` for the table/visibility/CRUD pattern and
delegates all template-shape normalization to
:mod:`material_final_document_service` (the single source of truth for the
《课程考核计划表》 schema, already used by the classroom 期末材料 flow). Export is
delegated to :mod:`material_export_template_service` (which precisely reproduces the
official docx), with bound signature images injected into the fields.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..db.connection import get_configured_db_engine
from ..db.schema_assessment_plans import ensure_assessment_plan_schema
from . import material_scope_service as scope_core
from . import signature_service
from .class_label_service import build_academic_class_label
from .material_export_template_service import build_material_export_artifact
from .material_final_document_service import (
    ASSESSMENT_PLAN_NOTES,
    normalize_final_material_payload,
)
from .organization_scope_service import load_teacher_org_scope
from .resource_access_service import is_super_admin_teacher

# ---------------------------------------------------------------------------
# Sharing scope (identical ladder to lesson plans)
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
_SCOPE_COLS = {
    "school": "school_code",
    "college": "college",
    "department": "department",
    "level": "scope_level",
    "openness": "scope_level",
}

TARGET_TOTAL_SCORE = 100

# The template field set the editor exposes / the importer must surface.
FIELD_KEYS = (
    "school",
    "course_name",
    "class_name",
    "examiner_name",
    "reviewer_name",
    "teacher_name",
    "academic_year",
    "semester",
    "date",
    "assessment_type",
    "assessment_method",
    "assessment_mode",
    "assessment_mode_label",
    "total_score",
)

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
    return load_teacher_org_scope(conn, int(teacher_id))


def can_view_plan(row: Any, viewer_scope: dict[str, str], *, is_super_admin: bool = False) -> bool:
    data = dict(row) if not isinstance(row, dict) else row
    level = normalize_scope_level(data.get("scope_level"))
    if level == SCOPE_PRIVATE:
        return bool(is_super_admin)
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
# Payload normalization (delegates to the 期末材料 template service)
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _uses_postgres_metadata(conn: Any) -> bool:
    if isinstance(conn, sqlite3.Connection):
        return False
    try:
        return get_configured_db_engine() == "postgres"
    except Exception:
        return False


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return None


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if _uses_postgres_metadata(conn):
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            """,
            ("public", table_name),
        ).fetchall()
        return {str(_row_value(row, "column_name", 0) or "") for row in rows if _row_value(row, "column_name", 0)}
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return {str(_row_value(row, "name", 1) or "") for row in rows if _row_value(row, "name", 1)}


def normalize_plan_payload(fields: Any, items: Any) -> dict[str, Any]:
    """Normalize raw teacher/AI/import input into the canonical plan shape.

    Returns ``{"fields", "items", "notes", "score_total", "score_balanced"}``
    using :func:`normalize_final_material_payload` so the result always matches the
    official 《课程考核计划表》 template, then applies a *non-destructive* score check
    (never silently rewrites item scores).
    """
    raw_fields = dict(fields) if isinstance(fields, dict) else {}
    raw_items = items if isinstance(items, list) else []
    payload = normalize_final_material_payload(
        document_type="assessment_plan",
        metadata=raw_fields,
        content_markdown="",
        tables=[],
        export_payload={"fields": raw_fields, "structured": {"assessment_items": raw_items}},
    )
    norm_fields = dict(payload.get("fields") or {})
    structured = payload.get("structured") or {}
    norm_items = [
        {
            "assessment_form": _text(item.get("assessment_form")) or "机试",
            "content": _text(item.get("content")),
            "score": _text(item.get("score")),
        }
        for item in (structured.get("assessment_items") or [])
        if isinstance(item, dict)
    ]
    notes = list(structured.get("notes") or ASSESSMENT_PLAN_NOTES)
    score_total = _sum_scores(norm_items)
    # Keep the human-facing total_score field honest about the real item sum.
    norm_fields["total_score"] = _score_text(score_total)
    return {
        "fields": norm_fields,
        "items": norm_items,
        "notes": notes,
        "score_total": score_total,
        "score_balanced": abs(score_total - TARGET_TOTAL_SCORE) < 1e-6,
    }


def _sum_scores(items: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in items or []:
        try:
            total += float(str(item.get("score") or "").strip() or 0)
        except (TypeError, ValueError):
            continue
    return total


def _score_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _assessment_class_label(row: dict[str, Any]) -> str:
    return build_academic_class_label(row)


def _format_cn_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    match = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", text)
    if not match:
        return text
    return f"{match.group(1)}年{int(match.group(2)):02d}月{int(match.group(3)):02d}日"


def _last_session_date(conn: sqlite3.Connection, class_offering_id: int) -> str:
    try:
        columns = _table_columns(conn, "class_offering_sessions")
    except Exception:
        return ""
    if "session_date" not in columns or "class_offering_id" not in columns:
        return ""
    status_filter = ""
    if "schedule_status" in columns:
        status_filter = "AND lower(trim(COALESCE(schedule_status, 'scheduled'))) NOT IN ('cancelled', 'canceled')"
    order_sql = "session_date DESC"
    if "order_index" in columns:
        order_sql += ", order_index DESC"
    try:
        row = conn.execute(
            f"""
            SELECT session_date
            FROM class_offering_sessions
            WHERE class_offering_id = ?
              AND TRIM(COALESCE(session_date, '')) != ''
              {status_filter}
            ORDER BY {order_sql}
            LIMIT 1
            """,
            (int(class_offering_id),),
        ).fetchone()
    except Exception:
        return ""
    return _format_cn_date(_row_value(row, "session_date", 0))


# ---------------------------------------------------------------------------
# Field auto-fill from a class offering (method-one prefill / method-two seed)
# ---------------------------------------------------------------------------
def build_fields_from_offering(
    conn: sqlite3.Connection, class_offering_id: int, *, teacher: dict[str, Any]
) -> dict[str, Any]:
    """Auto-fill template fields from offering → course → class → teacher + 教务 sync."""
    org = teacher_scope(conn, int(teacher["id"]))
    fields: dict[str, Any] = {
        "school": _text(org.get("school_name")) or "广西外国语学院",
        "teacher_name": _text(teacher.get("name") or teacher.get("username")),
        "examiner_name": _text(teacher.get("name") or teacher.get("username")),
        "date": _last_session_date(conn, int(class_offering_id)) or datetime.now().strftime("%Y年%m月%d日"),
    }
    row = conn.execute(
        """
        SELECT o.semester AS semester,
               o.academic_teaching_class_name AS academic_teaching_class_name,
               co.name AS course_name,
               co.college AS course_college,
               co.department AS course_department,
               co.school_name AS course_school_name,
               cl.name AS class_name,
               cl.academic_class_name AS academic_class_name,
               cl.academic_major AS class_academic_major,
               cl.major AS class_major,
               cl.department AS class_department,
               cl.description AS description,
               cl.academic_metadata_json AS academic_metadata_json,
               t.department AS teacher_department
        FROM class_offerings o
        LEFT JOIN courses co ON co.id = o.course_id
        LEFT JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if row:
        row = dict(row)
        fields["course_name"] = _text(row.get("course_name"))
        fields["class_name"] = _assessment_class_label(row)
        if row.get("course_school_name"):
            fields["school"] = _text(row.get("course_school_name"))
        semester_text = _text(row.get("semester"))
        year_match = re.search(r"(20\d{2})\s*[-—－]\s*(20\d{2})", semester_text)
        if year_match:
            fields["academic_year"] = f"{year_match.group(1)}-{year_match.group(2)}"
        if re.search(r"第一|(?:^|[-_\s])1(?:$|[-_\s])", semester_text):
            fields["semester"] = "第一学期"
        elif re.search(r"第二|(?:^|[-_\s])2(?:$|[-_\s])", semester_text):
            fields["semester"] = "第二学期"
        elif semester_text:
            fields["semester"] = semester_text

    # Best-effort 教务 course definition (考试/考查 + 笔试/非笔试).
    academic = _load_academic_course_row(conn, class_offering_id, int(teacher["id"]))
    if academic:
        if academic.get("course_nature"):
            fields["assessment_type"] = academic["course_nature"]
        if academic.get("exam_method"):
            fields.setdefault("assessment_type", academic["exam_method"])
        if academic.get("exam_mode"):
            fields["assessment_method"] = academic["exam_mode"]
            fields["assessment_mode"] = academic["exam_mode"]
    return fields


def _load_academic_course_row(conn: Any, class_offering_id: int, teacher_id: int) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT s.course_nature, s.exam_method, s.exam_mode
            FROM class_offerings o
            JOIN teacher_academic_course_sync_items s
              ON s.teacher_id = o.teacher_id
             AND (s.course_id = o.course_id OR TRIM(s.course_name) = (
                    SELECT TRIM(name) FROM courses WHERE id = o.course_id
                 ))
            WHERE o.id = ? AND o.teacher_id = ?
            ORDER BY s.synced_at DESC, s.id DESC
            LIMIT 1
            """,
            (int(class_offering_id), int(teacher_id)),
        ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


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


def create_assessment_plan(
    conn: sqlite3.Connection,
    *,
    teacher: dict[str, Any],
    title: str,
    fields: Any = None,
    items: Any = None,
    notes: Any = None,
    course_id: int | None = None,
    class_offering_id: int | None = None,
    source_type: str = "blank",
    status: str = "draft",
    scope_level: str = SCOPE_PRIVATE,
    tags: Any = None,
    examiner_signature_id: int | None = None,
    reviewer_signature_id: int | None = None,
    ai_gen_task_id: str | None = None,
    ai_gen_status: str | None = None,
    ai_gen_progress: Any = None,
    inherited_from: str | None = None,
) -> str:
    ensure_assessment_plan_schema(conn)
    plan_id = _new_id()
    org = teacher_scope(conn, int(teacher["id"]))
    normalized = normalize_plan_payload(fields or {}, items or [])
    resolved_notes = notes if isinstance(notes, list) and notes else normalized["notes"]
    now = _now()
    conn.execute(
        """
        INSERT INTO assessment_plans (
            id, teacher_id, title, course_id, class_offering_id,
            fields_json, items_json, notes_json,
            examiner_signature_id, reviewer_signature_id,
            tags_json, scope_level, source_type, status,
            ai_gen_task_id, ai_gen_status, ai_gen_error, ai_gen_progress,
            inherited_from, school_code, school_name, college, department,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            int(teacher["id"]),
            _text(title) or "课程考核计划表",
            int(course_id) if course_id else None,
            int(class_offering_id) if class_offering_id else None,
            _dump(normalized["fields"]),
            _dump(normalized["items"]),
            _dump(resolved_notes),
            int(examiner_signature_id) if examiner_signature_id else None,
            int(reviewer_signature_id) if reviewer_signature_id else None,
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


def get_assessment_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
    ensure_assessment_plan_schema(conn)
    row = conn.execute("SELECT * FROM assessment_plans WHERE id = ?", (str(plan_id),)).fetchone()
    if not row:
        return None
    return _hydrate(conn, dict(row))


def _hydrate(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    fields = _load(row.get("fields_json"), {}) or {}
    items = _load(row.get("items_json"), []) or []
    row["fields"] = fields if isinstance(fields, dict) else {}
    row["items"] = items if isinstance(items, list) else []
    row["notes"] = _load(row.get("notes_json"), list(ASSESSMENT_PLAN_NOTES)) or list(ASSESSMENT_PLAN_NOTES)
    row["tags"] = _normalize_tags(_load(row.get("tags_json"), []))
    row["ai_gen_progress_data"] = _load(row.get("ai_gen_progress"), {})
    row["import_preview"] = _load(row.get("import_preview_json"), {})
    row["scope_level"] = normalize_scope_level(row.get("scope_level"))
    row["scope_label"] = scope_label(row["scope_level"])
    row["score_total"] = _sum_scores(row["items"])
    row["score_balanced"] = abs(row["score_total"] - TARGET_TOTAL_SCORE) < 1e-6
    row["examiner_signature"] = _signature_brief(conn, row.get("examiner_signature_id"))
    row["reviewer_signature"] = _signature_brief(conn, row.get("reviewer_signature_id"))
    return row


def _signature_brief(conn: sqlite3.Connection, signature_id: Any) -> dict[str, Any] | None:
    if not signature_id:
        return None
    try:
        row = conn.execute(
            "SELECT id, name, subject_name FROM electronic_signatures "
            "WHERE id = ? AND status = 'active' AND deleted_at IS NULL LIMIT 1",
            (int(signature_id),),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    data = dict(row)
    return {
        "id": int(data["id"]),
        "name": data.get("name") or "",
        "subject_name": data.get("subject_name") or data.get("name") or "",
        "image_url": f"/api/signatures/{int(data['id'])}/image",
    }


def list_assessment_plans(conn: sqlite3.Connection, *, teacher: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_assessment_plan_schema(conn)
    teacher_id = int(teacher["id"])
    is_super = is_super_admin_teacher(conn, teacher_id)
    viewer = teacher_scope(conn, teacher_id)
    where_sql, params = visibility_where(viewer, teacher_id, is_super_admin=is_super, alias="ap")
    rows = conn.execute(
        f"""
        SELECT ap.*, t.name AS owner_teacher_name
        FROM assessment_plans ap
        LEFT JOIN teachers t ON t.id = ap.teacher_id
        WHERE {where_sql}
        ORDER BY ap.updated_at DESC, ap.id DESC
        """,
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = _hydrate(conn, dict(raw))
        row["is_owned"] = int(row.get("teacher_id") or 0) == teacher_id
        row["can_manage"] = row["is_owned"] or is_super
        result.append(serialize_card(row))
    return result


def serialize_card(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("fields") or {}
    return {
        "id": row["id"],
        "title": row.get("title") or "课程考核计划表",
        "course_name": fields.get("course_name") or "",
        "class_name": fields.get("class_name") or "",
        "semester_label": " ".join(
            p for p in (fields.get("academic_year") or "", fields.get("semester") or "") if p
        ).strip(),
        "assessment_type": fields.get("assessment_type") or "",
        "assessment_mode_label": fields.get("assessment_mode_label") or "",
        "item_count": len(row.get("items") or []),
        "score_total": row.get("score_total") or 0,
        "score_balanced": bool(row.get("score_balanced")),
        "tags": row.get("tags") or [],
        "scope_level": row.get("scope_level"),
        "scope_label": row.get("scope_label"),
        "source_type": row.get("source_type") or "blank",
        "status": row.get("status") or "draft",
        "ai_gen_status": row.get("ai_gen_status") or "",
        "ai_gen_error": row.get("ai_gen_error") or "",
        "ai_gen_progress": row.get("ai_gen_progress_data") or {},
        "examiner_signature": row.get("examiner_signature"),
        "reviewer_signature": row.get("reviewer_signature"),
        "is_owned": bool(row.get("is_owned")),
        "can_manage": bool(row.get("can_manage")),
        "owner_teacher_name": row.get("owner_teacher_name") or "",
        "inherited_from": row.get("inherited_from") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def update_content(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    fields: Any,
    items: Any,
    notes: Any = None,
    status: str | None = None,
) -> dict[str, Any]:
    ensure_assessment_plan_schema(conn)
    normalized = normalize_plan_payload(fields or {}, items or [])
    resolved_notes = notes if isinstance(notes, list) and notes else normalized["notes"]
    set_fields = ["fields_json = ?", "items_json = ?", "notes_json = ?", "updated_at = ?"]
    params: list[Any] = [
        _dump(normalized["fields"]),
        _dump(normalized["items"]),
        _dump(resolved_notes),
        _now(),
    ]
    if status is not None:
        set_fields.append("status = ?")
        params.append(status)
    params.append(str(plan_id))
    conn.execute(f"UPDATE assessment_plans SET {', '.join(set_fields)} WHERE id = ?", params)
    return normalized


def update_attributes(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    title: str | None = None,
    scope_level: str | None = None,
    course_id: int | None = None,
    class_offering_id: int | None = None,
) -> None:
    ensure_assessment_plan_schema(conn)
    set_fields: list[str] = []
    params: list[Any] = []
    if title is not None:
        set_fields.append("title = ?")
        params.append(_text(title) or "课程考核计划表")
    if scope_level is not None:
        set_fields.append("scope_level = ?")
        params.append(normalize_scope_level(scope_level))
    if course_id is not None:
        set_fields.append("course_id = ?")
        params.append(int(course_id) if course_id else None)
    if class_offering_id is not None:
        set_fields.append("class_offering_id = ?")
        params.append(int(class_offering_id) if class_offering_id else None)
    if not set_fields:
        return
    set_fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(plan_id))
    conn.execute(f"UPDATE assessment_plans SET {', '.join(set_fields)} WHERE id = ?", params)


def update_tags(conn: sqlite3.Connection, plan_id: str, tags: Any) -> list[str]:
    ensure_assessment_plan_schema(conn)
    normalized = _normalize_tags(tags)
    conn.execute(
        "UPDATE assessment_plans SET tags_json = ?, updated_at = ? WHERE id = ?",
        (_dump(normalized), _now(), str(plan_id)),
    )
    return normalized


def set_signature(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    role: str,
    signature_id: int | None,
) -> None:
    ensure_assessment_plan_schema(conn)
    column = "examiner_signature_id" if role == "examiner" else "reviewer_signature_id"
    conn.execute(
        f"UPDATE assessment_plans SET {column} = ?, updated_at = ? WHERE id = ?",
        (int(signature_id) if signature_id else None, _now(), str(plan_id)),
    )


def delete_assessment_plan(conn: sqlite3.Connection, plan_id: str) -> None:
    ensure_assessment_plan_schema(conn)
    conn.execute("DELETE FROM assessment_plans WHERE id = ?", (str(plan_id),))


def set_generation_status(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    status: str | None = None,
    ai_gen_status: str | None = None,
    ai_gen_error: str | None = None,
    progress: Any = None,
    task_id: str | None = None,
    import_preview: Any = None,
) -> None:
    ensure_assessment_plan_schema(conn)
    set_fields: list[str] = []
    params: list[Any] = []
    if status is not None:
        set_fields.append("status = ?")
        params.append(status)
    if ai_gen_status is not None:
        set_fields.append("ai_gen_status = ?")
        params.append(ai_gen_status)
    if ai_gen_error is not None:
        set_fields.append("ai_gen_error = ?")
        params.append(ai_gen_error)
    if progress is not None:
        set_fields.append("ai_gen_progress = ?")
        params.append(_dump(progress))
    if task_id is not None:
        set_fields.append("ai_gen_task_id = ?")
        params.append(task_id)
    if import_preview is not None:
        set_fields.append("import_preview_json = ?")
        params.append(_dump(import_preview))
    if not set_fields:
        return
    set_fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(plan_id))
    conn.execute(f"UPDATE assessment_plans SET {', '.join(set_fields)} WHERE id = ?", params)


def apply_imported_payload(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    fields: Any,
    items: Any,
    notes: Any,
    examiner_signature_id: int | None,
    reviewer_signature_id: int | None,
    import_preview: dict[str, Any],
    title: str | None = None,
) -> dict[str, Any]:
    normalized = update_content(conn, plan_id, fields=fields, items=items, notes=notes, status="ready")
    if title:
        update_attributes(conn, plan_id, title=title)
    set_signature(conn, plan_id, role="examiner", signature_id=examiner_signature_id)
    set_signature(conn, plan_id, role="reviewer", signature_id=reviewer_signature_id)
    set_generation_status(
        conn,
        plan_id,
        ai_gen_status="completed",
        ai_gen_error="",
        import_preview=import_preview,
        progress={"done": 1, "total": 1, "current_label": "完成"},
    )
    return normalized


def clone_for_inherit(conn: sqlite3.Connection, source_plan_id: str, *, teacher: dict[str, Any]) -> str:
    source = get_assessment_plan(conn, source_plan_id)
    if not source:
        raise ValueError("考核计划表不存在")
    org = teacher_scope(conn, int(teacher["id"]))
    fields = dict(source.get("fields") or {})
    fields["teacher_name"] = _text(teacher.get("name") or teacher.get("username"))
    fields["examiner_name"] = _text(teacher.get("name") or teacher.get("username"))
    fields["reviewer_name"] = ""
    fields["school"] = _text(org.get("school_name")) or fields.get("school", "")
    new_title = f"{source.get('title') or '课程考核计划表'}（继承）"
    return create_assessment_plan(
        conn,
        teacher=teacher,
        title=new_title,
        fields=fields,
        items=source.get("items"),
        notes=source.get("notes"),
        source_type=source.get("source_type") or "blank",
        status="ready",
        scope_level=SCOPE_PRIVATE,
        tags=source.get("tags"),
        inherited_from=str(source_plan_id),
    )


# ---------------------------------------------------------------------------
# Signature resolution + export + preview
# ---------------------------------------------------------------------------
def _signature_image_path(conn: sqlite3.Connection, signature_id: Any) -> tuple[str, str]:
    """Return ``(subject_name_text, image_filesystem_path)`` for a bound signature."""
    if not signature_id:
        return "", ""
    try:
        row = conn.execute(
            "SELECT * FROM electronic_signatures WHERE id = ? "
            "AND status = 'active' AND deleted_at IS NULL LIMIT 1",
            (int(signature_id),),
        ).fetchone()
    except Exception:
        return "", ""
    if not row:
        return "", ""
    data = dict(row)
    subject = str(data.get("subject_name") or data.get("name") or "")
    path = signature_service.resolve_signature_file_path(data)
    return subject, (str(path) if path else "")


def _signature_image_for_subject(conn: sqlite3.Connection, plan: dict[str, Any], subject_name: Any) -> tuple[str, str]:
    subject = _text(subject_name)
    if not subject:
        return "", ""
    where = [
        "status = 'active'",
        "deleted_at IS NULL",
        "(lower(trim(COALESCE(subject_name, ''))) = lower(trim(?)) "
        "OR lower(trim(COALESCE(name, ''))) = lower(trim(?)))",
    ]
    params: list[Any] = [subject, subject]
    school_code = _text(plan.get("school_code"))
    if school_code:
        where.append("lower(trim(COALESCE(school_code, ''))) = lower(trim(?))")
        params.append(school_code)
    department = _text(plan.get("department"))
    college = _text(plan.get("college"))
    rows = []
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM electronic_signatures
            WHERE {" AND ".join(where)}
            ORDER BY
              CASE WHEN lower(trim(COALESCE(department, ''))) = lower(trim(?)) THEN 0 ELSE 1 END,
              CASE WHEN lower(trim(COALESCE(college, ''))) = lower(trim(?)) THEN 0 ELSE 1 END,
              created_at DESC,
              id DESC
            LIMIT 1
            """,
            (*params, department, college),
        ).fetchall()
    except Exception:
        return "", ""
    if not rows:
        return "", ""
    data = dict(rows[0])
    path = signature_service.resolve_signature_file_path(data)
    return subject, (str(path) if path else "")


def build_export_fields(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    """Build the export fields, injecting bound signature image paths + names."""
    fields = dict(plan.get("fields") or {})
    examiner_subject, examiner_path = _signature_image_path(conn, plan.get("examiner_signature_id"))
    if not examiner_path:
        examiner_subject, examiner_path = _signature_image_for_subject(
            conn, plan, fields.get("examiner_name") or fields.get("teacher_name")
        )
    if examiner_path:
        fields["examiner_signature_image_path"] = examiner_path
    if examiner_subject and not fields.get("examiner_name"):
        fields["examiner_name"] = examiner_subject
    reviewer_subject, reviewer_path = _signature_image_path(conn, plan.get("reviewer_signature_id"))
    if not reviewer_path:
        reviewer_subject, reviewer_path = _signature_image_for_subject(conn, plan, fields.get("reviewer_name"))
    if reviewer_path:
        fields["reviewer_signature_image_path"] = reviewer_path
    if reviewer_subject and not fields.get("reviewer_name"):
        fields["reviewer_name"] = reviewer_subject
    if not _text(fields.get("reviewer_name")):
        fields["reviewer_name"] = "【系主任未填写】"
        fields["reviewer_missing_notice"] = "请填写系主任姓名，并从签名库绑定或上传签名。"
    return fields


def export_plan_docx(conn: sqlite3.Connection, plan: dict[str, Any]) -> tuple[bytes, str]:
    """Render the plan to the official 《课程考核计划表》 docx. Returns (bytes, filename)."""
    artifact = export_plan_artifact(conn, plan, requested_format="docx")
    return artifact.content, artifact.filename


def export_plan_artifact(conn: sqlite3.Connection, plan: dict[str, Any], *, requested_format: str = "docx"):
    """Render the plan through the canonical assessment-plan export template."""
    fields = build_export_fields(conn, plan)
    items = plan.get("items") or []
    notes = plan.get("notes") or list(ASSESSMENT_PLAN_NOTES)
    parse_payload = {
        "document_group": "final_material",
        "document_type": "assessment_plan",
        "document_type_label": "课程考核计划表",
        "metadata": fields,
        "content_markdown": "",
        "tables": [],
        "export_payload": {
            "template_key": "assessment_plan",
            "document_group": "final_material",
            "document_type": "assessment_plan",
            "fields": fields,
            "structured": {"assessment_items": items, "notes": notes},
        },
    }
    base_title = (plan.get("title") or "课程考核计划表").replace("/", "_").replace("\\", "_")
    return build_material_export_artifact(
        parse_payload, fallback_filename=base_title, requested_format=requested_format
    )


def render_preview_html(conn: sqlite3.Connection, plan: dict[str, Any]) -> str:
    """PDF-backed preview shell: same template path as actual export."""
    plan_id = _text(plan.get("id"))
    title = _text(plan.get("title")) or "课程考核计划表"

    def esc(value: Any) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>{esc(title)} · 预览</title>
<style>
  html,body{{height:100%;margin:0;background:#eef1f5;color:#111;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
  .ap-preview{{height:100%;display:flex;flex-direction:column;}}
  .ap-preview__bar{{display:flex;gap:8px;align-items:center;padding:10px 14px;background:#fff;border-bottom:1px solid #d8dee8;}}
  .ap-preview__bar strong{{font-size:14px;margin-right:auto;}}
  .ap-preview__bar a{{font-size:13px;text-decoration:none;color:#0f766e;border:1px solid #cbd5e1;border-radius:6px;padding:6px 10px;background:#fff;}}
  iframe{{flex:1;width:100%;border:0;background:#eef1f5;}}
</style></head><body>
<div class="ap-preview">
  <div class="ap-preview__bar">
    <strong>{esc(title)}</strong>
    <a href="/api/assessment-plans/{esc(plan_id)}/export?fmt=docx">Word</a>
    <a href="/api/assessment-plans/{esc(plan_id)}/export?fmt=pdf&inline=1" target="_blank" rel="noopener">PDF</a>
  </div>
  <iframe src="/api/assessment-plans/{esc(plan_id)}/export?fmt=pdf&inline=1" title="考核计划表 PDF 预览"></iframe>
</div>
</body></html>"""
