from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from .course_planning_service import load_course_lessons_by_course_id, serialize_course_row
from .organization_scope_service import apply_teacher_scope_to_org, load_teacher_org_scope
from .resource_access_service import (
    SCOPE_DEPARTMENT,
    SCOPE_PRIVATE,
    SCOPE_SCHOOL,
    normalize_scope_level,
    teacher_can_manage_class,
    teacher_can_manage_course,
    teacher_can_manage_exam_paper,
    teacher_can_manage_textbook,
    teacher_can_use_class,
    teacher_can_use_course,
    teacher_can_use_exam_paper,
    teacher_can_use_textbook,
)


RESOURCE_SCOPE_LABELS = {
    SCOPE_PRIVATE: "私有",
    "class": "本班",
    "classroom": "本课堂",
    SCOPE_DEPARTMENT: "本系部",
    SCOPE_SCHOOL: "本学校",
    "public": "公开",
}


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if key in row.keys():
            return row[key]
    except (AttributeError, KeyError):
        pass
    return default


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _scope_label(scope_level: Any, *, default: str = SCOPE_PRIVATE) -> str:
    normalized = normalize_scope_level(scope_level, default=default)
    return RESOURCE_SCOPE_LABELS.get(normalized, normalized)


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    if isinstance(row, sqlite3.Row):
        return int(row[0] or 0)
    return int(row[0] or 0)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _count_if_table(
    conn: sqlite3.Connection,
    table_names: str | tuple[str, ...],
    sql: str,
    params: tuple[Any, ...],
) -> int:
    names = (table_names,) if isinstance(table_names, str) else table_names
    if not all(_table_exists(conn, name) for name in names):
        return 0
    try:
        return _count(conn, sql, params)
    except sqlite3.OperationalError:
        return 0


def _positive_counts(counts: dict[str, int]) -> dict[str, int]:
    return {key: int(value or 0) for key, value in counts.items() if int(value or 0) > 0}


def raise_if_delete_blocked(resource_label: str, blockers: dict[str, int]) -> None:
    active = _positive_counts(blockers)
    if not active:
        return
    reason = "，".join(f"{label} {count} 条" for label, count in active.items())
    raise HTTPException(
        409,
        f"{resource_label}仍被业务数据引用，不能直接删除。请先解除引用或走归档/软删除流程：{reason}",
    )


def build_class_delete_blockers(conn: sqlite3.Connection, class_id: int) -> dict[str, int]:
    return _positive_counts(
        {
            "学生": _count_if_table(conn, "students", "SELECT COUNT(*) FROM students WHERE class_id = ?", (int(class_id),)),
            "课堂": _count_if_table(
                conn,
                "class_offerings",
                "SELECT COUNT(*) FROM class_offerings WHERE class_id = ?",
                (int(class_id),),
            ),
            "作业": _count_if_table(
                conn,
                ("assignments", "class_offerings"),
                """
                SELECT COUNT(*)
                FROM assignments a
                JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE o.class_id = ?
                """,
                (int(class_id),),
            ),
            "学生提交": _count_if_table(
                conn,
                ("submissions", "assignments", "class_offerings"),
                """
                SELECT COUNT(*)
                FROM submissions s
                JOIN assignments a ON a.id = s.assignment_id
                JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE o.class_id = ?
                """,
                (int(class_id),),
            ),
            "提交草稿": _count_if_table(
                conn,
                ("submission_drafts", "assignments", "class_offerings"),
                """
                SELECT COUNT(*)
                FROM submission_drafts d
                JOIN assignments a ON a.id = d.assignment_id
                JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE o.class_id = ?
                """,
                (int(class_id),),
            ),
            "学习阶段考试记录": _count_if_table(
                conn,
                "learning_stage_exam_attempts",
                "SELECT COUNT(*) FROM learning_stage_exam_attempts WHERE class_id = ?",
                (int(class_id),),
            ),
            "材料分配": _count_if_table(
                conn,
                ("course_material_assignments", "class_offerings"),
                """
                SELECT COUNT(*)
                FROM course_material_assignments a
                JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE o.class_id = ?
                """,
                (int(class_id),),
            ),
            "学生登录记录": _count_if_table(
                conn,
                "student_login_audit_logs",
                "SELECT COUNT(*) FROM student_login_audit_logs WHERE class_id = ?",
                (int(class_id),),
            ),
        }
    )


def build_course_delete_blockers(conn: sqlite3.Connection, course_id: int) -> dict[str, int]:
    return _positive_counts(
        {
            "课堂": _count_if_table(
                conn,
                "class_offerings",
                "SELECT COUNT(*) FROM class_offerings WHERE course_id = ?",
                (int(course_id),),
            ),
            "作业": _count_if_table(
                conn,
                "assignments",
                "SELECT COUNT(*) FROM assignments WHERE course_id = ?",
                (int(course_id),),
            ),
            "学生提交": _count_if_table(
                conn,
                ("submissions", "assignments"),
                """
                SELECT COUNT(*)
                FROM submissions s
                JOIN assignments a ON a.id = s.assignment_id
                WHERE a.course_id = ?
                """,
                (int(course_id),),
            ),
            "提交草稿": _count_if_table(
                conn,
                ("submission_drafts", "assignments"),
                """
                SELECT COUNT(*)
                FROM submission_drafts d
                JOIN assignments a ON a.id = d.assignment_id
                WHERE a.course_id = ?
                """,
                (int(course_id),),
            ),
            "课程文件": _count_if_table(
                conn,
                "course_files",
                "SELECT COUNT(*) FROM course_files WHERE course_id = ?",
                (int(course_id),),
            ),
        }
    )


def build_textbook_delete_blockers(conn: sqlite3.Connection, textbook_id: int) -> dict[str, int]:
    return _positive_counts(
        {
            "课堂绑定": _count_if_table(
                conn,
                "class_offerings",
                "SELECT COUNT(*) FROM class_offerings WHERE textbook_id = ?",
                (int(textbook_id),),
            ),
        }
    )


def build_exam_delete_blockers(conn: sqlite3.Connection, paper_id: str) -> dict[str, int]:
    return _positive_counts(
        {
            "作业": _count_if_table(
                conn,
                "assignments",
                "SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ?",
                (str(paper_id),),
            ),
            "学生提交": _count_if_table(
                conn,
                ("submissions", "assignments"),
                """
                SELECT COUNT(*)
                FROM submissions s
                JOIN assignments a ON a.id = s.assignment_id
                WHERE a.exam_paper_id = ?
                """,
                (str(paper_id),),
            ),
            "提交草稿": _count_if_table(
                conn,
                ("submission_drafts", "assignments"),
                """
                SELECT COUNT(*)
                FROM submission_drafts d
                JOIN assignments a ON a.id = d.assignment_id
                WHERE a.exam_paper_id = ?
                """,
                (str(paper_id),),
            ),
            "学习阶段考试记录": _count_if_table(
                conn,
                "learning_stage_exam_attempts",
                "SELECT COUNT(*) FROM learning_stage_exam_attempts WHERE exam_paper_id = ?",
                (str(paper_id),),
            ),
        }
    )


def build_material_delete_blockers(conn: sqlite3.Connection, material_row: Any) -> dict[str, int]:
    material = _row_dict(material_row)
    root_id = int(material["root_id"])
    material_path = str(material["material_path"] or "")
    subtree_id_sql = """
        SELECT id
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
    """
    subtree_params = (root_id, material_path, f"{material_path}/%")
    return _positive_counts(
        {
            "课堂材料分配": _count_if_table(
                conn,
                "course_material_assignments",
                f"SELECT COUNT(*) FROM course_material_assignments WHERE material_id IN ({subtree_id_sql})",
                subtree_params,
            ),
            "课程课次引用": _count_if_table(
                conn,
                "course_lessons",
                f"SELECT COUNT(*) FROM course_lessons WHERE learning_material_id IN ({subtree_id_sql})",
                subtree_params,
            ),
            "课堂课次引用": _count_if_table(
                conn,
                "class_offering_sessions",
                f"SELECT COUNT(*) FROM class_offering_sessions WHERE learning_material_id IN ({subtree_id_sql})",
                subtree_params,
            ),
            "课堂首页材料": _count_if_table(
                conn,
                "class_offerings",
                f"SELECT COUNT(*) FROM class_offerings WHERE home_learning_material_id IN ({subtree_id_sql})",
                subtree_params,
            ),
            "AI导入记录": _count_if_table(
                conn,
                "material_ai_import_records",
                f"""
                SELECT COUNT(*)
                FROM material_ai_import_records
                WHERE package_material_id IN ({subtree_id_sql})
                   OR source_material_id IN ({subtree_id_sql})
                   OR parsed_material_id IN ({subtree_id_sql})
                   OR parent_material_id IN ({subtree_id_sql})
                """,
                subtree_params * 4,
            ),
            "材料生成任务": _count_if_table(
                conn,
                "session_material_generation_tasks",
                f"SELECT COUNT(*) FROM session_material_generation_tasks WHERE generated_material_id IN ({subtree_id_sql})",
                subtree_params,
            ),
        }
    )


def build_mode_permissions(
    *,
    can_use: bool,
    can_manage: bool,
    can_view_content: bool | None = None,
    can_edit_content: bool | None = None,
    content_locked: bool = False,
    lock_reason: str = "",
) -> dict[str, Any]:
    return {
        "can_view_attributes": bool(can_use),
        "can_edit_attributes": bool(can_manage),
        "can_view_content": bool(can_use if can_view_content is None else can_view_content),
        "can_edit_content": bool(can_manage if can_edit_content is None else can_edit_content),
        "can_manage": bool(can_manage),
        "content_locked": bool(content_locked),
        "lock_reason": lock_reason,
    }


def ensure_teacher_can_view_class_attributes(conn: sqlite3.Connection, class_id: int, teacher_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM classes WHERE id = ? LIMIT 1", (int(class_id),)).fetchone()
    if not row or not teacher_can_use_class(conn, teacher_id, row):
        raise HTTPException(404, "班级不存在或不在当前教师可见范围内")
    return row


def ensure_teacher_can_manage_class_attributes(conn: sqlite3.Connection, class_id: int, teacher_id: int) -> sqlite3.Row:
    row = ensure_teacher_can_view_class_attributes(conn, class_id, teacher_id)
    if not teacher_can_manage_class(conn, teacher_id, row):
        raise HTTPException(403, "该班级由其他维护人管理，仅可查看或复用，不能编辑属性")
    return row


def ensure_teacher_can_view_course_attributes(conn: sqlite3.Connection, course_id: int, teacher_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM courses WHERE id = ? LIMIT 1", (int(course_id),)).fetchone()
    if not row or not teacher_can_use_course(conn, teacher_id, row):
        raise HTTPException(404, "课程不存在或不在当前教师可见范围内")
    return row


def ensure_teacher_can_manage_course_attributes(conn: sqlite3.Connection, course_id: int, teacher_id: int) -> sqlite3.Row:
    row = ensure_teacher_can_view_course_attributes(conn, course_id, teacher_id)
    if not teacher_can_manage_course(conn, teacher_id, row):
        raise HTTPException(403, "该课程为共享资源，仅归属维护人或超管可编辑")
    return row


def ensure_teacher_can_view_textbook_attributes(conn: sqlite3.Connection, textbook_id: int, teacher_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM textbooks WHERE id = ? LIMIT 1", (int(textbook_id),)).fetchone()
    if not row or not teacher_can_use_textbook(conn, teacher_id, row):
        raise HTTPException(404, "教材不存在或不在当前教师可见范围内")
    return row


def ensure_teacher_can_manage_textbook_attributes(conn: sqlite3.Connection, textbook_id: int, teacher_id: int) -> sqlite3.Row:
    row = ensure_teacher_can_view_textbook_attributes(conn, textbook_id, teacher_id)
    if not teacher_can_manage_textbook(conn, teacher_id, row):
        raise HTTPException(403, "该教材为共享资源，仅归属维护人或超管可编辑")
    return row


def ensure_teacher_can_view_exam_attributes(conn: sqlite3.Connection, paper_id: str, teacher_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM exam_papers WHERE id = ? LIMIT 1", (str(paper_id),)).fetchone()
    if not row or not teacher_can_use_exam_paper(conn, teacher_id, row):
        raise HTTPException(404, "试卷不存在或不在当前教师可见范围内")
    return row


def ensure_teacher_can_manage_exam_attributes(conn: sqlite3.Connection, paper_id: str, teacher_id: int) -> sqlite3.Row:
    row = ensure_teacher_can_view_exam_attributes(conn, paper_id, teacher_id)
    if not teacher_can_manage_exam_paper(conn, teacher_id, row):
        raise HTTPException(403, "该试卷为共享资源，仅归属维护人或超管可编辑")
    return row


def serialize_class_attributes(conn: sqlite3.Connection, class_row: Any, teacher_id: int) -> dict[str, Any]:
    row = _row_dict(class_row)
    class_id = int(row["id"])
    total_students = _count(conn, "SELECT COUNT(*) FROM students WHERE class_id = ?", (class_id,))
    active_students = _count(
        conn,
        "SELECT COUNT(*) FROM students WHERE class_id = ? AND COALESCE(enrollment_status, 'active') = 'active'",
        (class_id,),
    )
    suspended_students = _count(
        conn,
        "SELECT COUNT(*) FROM students WHERE class_id = ? AND COALESCE(enrollment_status, 'active') != 'active'",
        (class_id,),
    )
    missing_email_students = _count(
        conn,
        "SELECT COUNT(*) FROM students WHERE class_id = ? AND TRIM(COALESCE(email, '')) = ''",
        (class_id,),
    )
    offering_count = _count(conn, "SELECT COUNT(*) FROM class_offerings WHERE class_id = ?", (class_id,))
    can_manage = teacher_can_manage_class(conn, teacher_id, row)
    scope_level = normalize_scope_level(row.get("scope_level"), default=SCOPE_SCHOOL)
    return {
        "id": class_id,
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "school_code": row.get("school_code") or "",
        "school_name": row.get("school_name") or "",
        "college": row.get("college") or "",
        "department": row.get("department") or "",
        "major": row.get("major") or row.get("academic_major") or "",
        "enrollment_year": _safe_int(row.get("enrollment_year")),
        "expected_graduation_year": _safe_int(row.get("expected_graduation_year")),
        "program_duration_years": _safe_int(row.get("program_duration_years")),
        "owner_role": row.get("owner_role") or "teacher",
        "owner_user_pk": _safe_int(row.get("owner_user_pk")) or _safe_int(row.get("created_by_teacher_id")),
        "created_by_teacher_id": _safe_int(row.get("created_by_teacher_id")),
        "scope_level": scope_level,
        "scope_label": _scope_label(scope_level, default=SCOPE_SCHOOL),
        "academic_source": row.get("academic_source") or "",
        "academic_class_code": row.get("academic_class_code") or "",
        "academic_class_name": row.get("academic_class_name") or "",
        "academic_grade": row.get("academic_grade") or "",
        "academic_major": row.get("academic_major") or "",
        "academic_sync_at": row.get("academic_sync_at"),
        "academic_sync_message": row.get("academic_sync_message") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "archived_at": row.get("archived_at"),
        "deleted_at": row.get("deleted_at"),
        "stats": {
            "student_count": total_students,
            "active_student_count": active_students,
            "suspended_student_count": suspended_students,
            "missing_email_count": missing_email_students,
            "offering_count": offering_count,
        },
        "permissions": build_mode_permissions(can_use=True, can_manage=can_manage),
    }


def serialize_course_attributes(conn: sqlite3.Connection, course_row: Any, teacher_id: int) -> dict[str, Any]:
    row = _row_dict(course_row)
    course_id = int(row["id"])
    lessons = load_course_lessons_by_course_id(conn, [course_id]).get(course_id, [])
    lesson_count = len(lessons)
    section_count = sum(int(item.get("section_count") or 0) for item in lessons)
    material_count = len({int(item["learning_material_id"]) for item in lessons if item.get("learning_material_id")})
    offering_count = _count(conn, "SELECT COUNT(*) FROM class_offerings WHERE course_id = ?", (course_id,))
    can_manage = teacher_can_manage_course(conn, teacher_id, row)
    scope_level = normalize_scope_level(row.get("scope_level"), default=SCOPE_SCHOOL)
    return {
        "id": course_id,
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "sect_name": row.get("sect_name") or "",
        "credits": row.get("credits") or 0,
        "total_hours": int(row.get("total_hours") or 0),
        "school_code": row.get("school_code") or "",
        "school_name": row.get("school_name") or "",
        "college": row.get("college") or "",
        "department": row.get("department") or "",
        "owner_role": row.get("owner_role") or "teacher",
        "owner_user_pk": _safe_int(row.get("owner_user_pk")) or _safe_int(row.get("created_by_teacher_id")),
        "created_by_teacher_id": _safe_int(row.get("created_by_teacher_id")),
        "scope_level": scope_level,
        "scope_label": _scope_label(scope_level, default=SCOPE_SCHOOL),
        "academic_source": row.get("academic_source") or "",
        "academic_course_code": row.get("academic_course_code") or "",
        "academic_sync_at": row.get("academic_sync_at"),
        "academic_sync_message": row.get("academic_sync_message") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "archived_at": row.get("archived_at"),
        "deleted_at": row.get("deleted_at"),
        "stats": {
            "lesson_count": lesson_count,
            "section_count": section_count,
            "bound_material_count": material_count,
            "offering_count": offering_count,
            "hours_match_lessons": int(row.get("total_hours") or 0) == section_count if row.get("total_hours") else True,
        },
        "permissions": build_mode_permissions(can_use=True, can_manage=can_manage),
    }


def serialize_textbook_attributes(conn: sqlite3.Connection, textbook_row: Any, teacher_id: int) -> dict[str, Any]:
    row = _row_dict(textbook_row)
    textbook_id = int(row["id"])
    offering_count = _count(conn, "SELECT COUNT(*) FROM class_offerings WHERE textbook_id = ?", (textbook_id,))
    can_manage = teacher_can_manage_textbook(conn, teacher_id, row)
    scope_level = normalize_scope_level(row.get("scope_level"), default=SCOPE_PRIVATE)
    return {
        "id": textbook_id,
        "title": row.get("title") or "",
        "authors": _json_list(row.get("authors_json")),
        "publisher": row.get("publisher") or "",
        "publication_date": row.get("publication_date") or "",
        "tags": _json_list(row.get("tags_json")),
        "teacher_id": _safe_int(row.get("teacher_id")),
        "owner_role": row.get("owner_role") or "teacher",
        "owner_user_pk": _safe_int(row.get("owner_user_pk")) or _safe_int(row.get("teacher_id")),
        "school_code": row.get("school_code") or "",
        "school_name": row.get("school_name") or "",
        "college": row.get("college") or "",
        "department": row.get("department") or "",
        "scope_level": scope_level,
        "scope_label": _scope_label(scope_level, default=SCOPE_PRIVATE),
        "attachment_name": row.get("attachment_name") or "",
        "attachment_size": int(row.get("attachment_size") or 0),
        "attachment_mime_type": row.get("attachment_mime_type") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "archived_at": row.get("archived_at"),
        "deleted_at": row.get("deleted_at"),
        "stats": {"offering_count": offering_count},
        "permissions": build_mode_permissions(can_use=True, can_manage=can_manage),
    }


def serialize_textbook_content(textbook_row: Any, teacher_id: int, conn: sqlite3.Connection) -> dict[str, Any]:
    row = _row_dict(textbook_row)
    can_manage = teacher_can_manage_textbook(conn, teacher_id, row)
    return {
        "id": int(row["id"]),
        "title": row.get("title") or "",
        "introduction": row.get("introduction") or "",
        "catalog_text": row.get("catalog_text") or "",
        "attachment": {
            "name": row.get("attachment_name") or "",
            "size": int(row.get("attachment_size") or 0),
            "mime_type": row.get("attachment_mime_type") or "",
            "download_url": f"/api/manage/textbooks/{int(row['id'])}/attachment" if row.get("attachment_path") else "",
        },
        "permissions": build_mode_permissions(can_use=True, can_manage=can_manage),
    }


def serialize_exam_attributes(conn: sqlite3.Connection, paper_row: Any, teacher_id: int) -> dict[str, Any]:
    row = _row_dict(paper_row)
    paper_id = str(row["id"])
    assigned_count = _count(conn, "SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ?", (paper_id,))
    submission_count = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE a.exam_paper_id = ?
        """,
        (paper_id,),
    )
    draft_count = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM submission_drafts sd
        JOIN assignments a ON a.id = sd.assignment_id
        WHERE a.exam_paper_id = ?
        """,
        (paper_id,),
    )
    can_manage = teacher_can_manage_exam_paper(conn, teacher_id, row)
    scope_level = normalize_scope_level(row.get("scope_level"), default=SCOPE_PRIVATE)
    config = _json_object(row.get("exam_config_json"))
    content_locked = submission_count > 0 or draft_count > 0
    return {
        "id": paper_id,
        "display_title": row.get("title") or "",
        "description": row.get("description") or "",
        "status": row.get("status") or "draft",
        "tags": _json_list(row.get("tags_json")),
        "teacher_id": _safe_int(row.get("teacher_id")),
        "owner_role": row.get("owner_role") or "teacher",
        "owner_user_pk": _safe_int(row.get("owner_user_pk")) or _safe_int(row.get("teacher_id")),
        "school_code": row.get("school_code") or "",
        "school_name": row.get("school_name") or "",
        "college": row.get("college") or "",
        "department": row.get("department") or "",
        "scope_level": scope_level,
        "scope_label": _scope_label(scope_level, default=SCOPE_PRIVATE),
        "ai_gen_task_id": row.get("ai_gen_task_id"),
        "ai_gen_status": row.get("ai_gen_status"),
        "ai_gen_error": row.get("ai_gen_error") or "",
        "published_at": row.get("published_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "archived_at": row.get("archived_at"),
        "deleted_at": row.get("deleted_at"),
        "default_grading": {
            "grading_mode": config.get("grading_mode") or config.get("default_grading_mode") or "",
            "ai_grading_enabled": bool(config.get("ai_grading_enabled", True)),
            "student_ai_assist_enabled": bool(config.get("student_ai_assist_enabled", False)),
        },
        "stats": {
            "assigned_count": assigned_count,
            "submission_count": submission_count,
            "draft_count": draft_count,
        },
        "permissions": build_mode_permissions(
            can_use=True,
            can_manage=can_manage,
            content_locked=content_locked,
            lock_reason="试卷已有学生提交或草稿，内容必须走版本化或保持冻结" if content_locked else "",
        ),
    }


def serialize_exam_content(conn: sqlite3.Connection, paper_row: Any, teacher_id: int) -> dict[str, Any]:
    row = _row_dict(paper_row)
    can_manage = teacher_can_manage_exam_paper(conn, teacher_id, row)
    return {
        "id": str(row["id"]),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "questions": _json_object(row.get("questions_json") or "{}"),
        "config": _json_object(row.get("exam_config_json") or "{}"),
        "status": row.get("status") or "draft",
        "updated_at": row.get("updated_at"),
        "permissions": build_mode_permissions(can_use=True, can_manage=can_manage),
    }


def update_class_attributes(
    conn: sqlite3.Connection,
    *,
    class_row: Any,
    teacher_id: int,
    payload: dict[str, Any],
) -> None:
    class_id = int(_row_value(class_row, "id"))
    name = str(payload.get("name", _row_value(class_row, "name") or "") or "").strip()
    if not name:
        raise HTTPException(400, "班级名称不能为空")
    description = str(payload.get("description", _row_value(class_row, "description") or "") or "").strip()
    scope_level = normalize_scope_level(payload.get("scope_level", _row_value(class_row, "scope_level")), default=SCOPE_SCHOOL)
    if scope_level not in {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL, "class"}:
        raise HTTPException(400, "班级可见范围不支持")
    org_scope = apply_teacher_scope_to_org(
        conn,
        teacher_id,
        school_code=payload.get("school_code", _row_value(class_row, "school_code") or ""),
        school_name=payload.get("school_name", _row_value(class_row, "school_name") or ""),
        college=payload.get("college", _row_value(class_row, "college") or ""),
        department=payload.get("department", _row_value(class_row, "department") or ""),
    )
    major = str(payload.get("major", _row_value(class_row, "major", _row_value(class_row, "academic_major", ""))) or "").strip()
    enrollment_year = _safe_int(payload.get("enrollment_year", _row_value(class_row, "enrollment_year")))
    graduation_year = _safe_int(payload.get("expected_graduation_year", _row_value(class_row, "expected_graduation_year")))
    duration_years = _safe_int(payload.get("program_duration_years", _row_value(class_row, "program_duration_years")))
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE classes
        SET name = ?,
            description = ?,
            school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            major = ?,
            enrollment_year = ?,
            expected_graduation_year = ?,
            program_duration_years = ?,
            owner_role = 'teacher',
            owner_user_pk = COALESCE(owner_user_pk, created_by_teacher_id),
            scope_level = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            name,
            description,
            org_scope["school_code"],
            org_scope["school_name"],
            org_scope["college"],
            org_scope["department"],
            major,
            enrollment_year,
            graduation_year,
            duration_years,
            scope_level,
            now,
            class_id,
        ),
    )


def update_course_attributes(
    conn: sqlite3.Connection,
    *,
    course_row: Any,
    teacher_id: int,
    payload: dict[str, Any],
) -> None:
    course_id = int(_row_value(course_row, "id"))
    name = str(payload.get("name", _row_value(course_row, "name") or "") or "").strip()
    if not name:
        raise HTTPException(400, "课程名称不能为空")
    scope_level = normalize_scope_level(payload.get("scope_level", _row_value(course_row, "scope_level")), default=SCOPE_SCHOOL)
    if scope_level not in {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL}:
        raise HTTPException(400, "课程可见范围不支持")
    org_scope = apply_teacher_scope_to_org(
        conn,
        teacher_id,
        school_code=payload.get("school_code", _row_value(course_row, "school_code") or ""),
        school_name=payload.get("school_name", _row_value(course_row, "school_name") or ""),
        college=payload.get("college", _row_value(course_row, "college") or ""),
        department=payload.get("department", _row_value(course_row, "department") or ""),
    )
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE courses
        SET name = ?,
            description = ?,
            sect_name = ?,
            credits = ?,
            total_hours = ?,
            school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            owner_role = 'teacher',
            owner_user_pk = COALESCE(owner_user_pk, created_by_teacher_id),
            scope_level = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            name,
            str(payload.get("description", _row_value(course_row, "description") or "") or "").strip(),
            str(payload.get("sect_name", _row_value(course_row, "sect_name") or "") or "").strip(),
            payload.get("credits", _row_value(course_row, "credits") or 0),
            _safe_int(payload.get("total_hours", _row_value(course_row, "total_hours"))) or 0,
            org_scope["school_code"],
            org_scope["school_name"],
            org_scope["college"],
            org_scope["department"],
            scope_level,
            now,
            course_id,
        ),
    )


def update_textbook_attributes(
    conn: sqlite3.Connection,
    *,
    textbook_row: Any,
    teacher_id: int,
    payload: dict[str, Any],
) -> None:
    textbook_id = int(_row_value(textbook_row, "id"))
    title = str(payload.get("title", _row_value(textbook_row, "title") or "") or "").strip()
    if not title:
        raise HTTPException(400, "教材名称不能为空")
    scope_level = normalize_scope_level(payload.get("scope_level", _row_value(textbook_row, "scope_level")), default=SCOPE_PRIVATE)
    if scope_level not in {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL}:
        raise HTTPException(400, "教材可见范围不支持")
    owner_scope = load_teacher_org_scope(conn, int(_row_value(textbook_row, "teacher_id") or teacher_id))
    authors = payload.get("authors", _json_list(_row_value(textbook_row, "authors_json")))
    tags = payload.get("tags", _json_list(_row_value(textbook_row, "tags_json")))
    if not isinstance(authors, list) or not isinstance(tags, list):
        raise HTTPException(400, "作者和标签必须是数组")
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE textbooks
        SET title = ?,
            authors_json = ?,
            publisher = ?,
            publication_date = ?,
            tags_json = ?,
            owner_role = 'teacher',
            owner_user_pk = COALESCE(owner_user_pk, teacher_id),
            scope_level = ?,
            school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            published_at = CASE WHEN ? != 'private' THEN COALESCE(published_at, ?) ELSE published_at END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            title,
            json.dumps([str(item).strip() for item in authors if str(item).strip()], ensure_ascii=False),
            str(payload.get("publisher", _row_value(textbook_row, "publisher") or "") or "").strip(),
            str(payload.get("publication_date", _row_value(textbook_row, "publication_date") or "") or "").strip(),
            json.dumps([str(item).strip() for item in tags if str(item).strip()], ensure_ascii=False),
            scope_level,
            owner_scope["school_code"],
            owner_scope["school_name"],
            owner_scope["college"],
            owner_scope["department"],
            scope_level,
            now,
            now,
            textbook_id,
        ),
    )


def update_exam_attributes(
    conn: sqlite3.Connection,
    *,
    paper_row: Any,
    teacher_id: int,
    payload: dict[str, Any],
) -> None:
    paper_id = str(_row_value(paper_row, "id"))
    scope_level = normalize_scope_level(payload.get("scope_level", _row_value(paper_row, "scope_level")), default=SCOPE_PRIVATE)
    if scope_level not in {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL}:
        raise HTTPException(400, "试卷可见范围不支持")
    tags = payload.get("tags", _json_list(_row_value(paper_row, "tags_json")))
    if not isinstance(tags, list):
        raise HTTPException(400, "标签必须是数组")
    status = str(payload.get("status", _row_value(paper_row, "status") or "draft") or "draft").strip().lower()
    if status not in {"draft", "ready", "published", "archived", "closed"}:
        raise HTTPException(400, "试卷状态不支持")
    owner_scope = load_teacher_org_scope(conn, int(_row_value(paper_row, "teacher_id") or teacher_id))
    config = _json_object(_row_value(paper_row, "exam_config_json"))
    default_grading = payload.get("default_grading")
    if isinstance(default_grading, dict):
        for key in ("grading_mode", "default_grading_mode", "ai_grading_enabled", "student_ai_assist_enabled"):
            if key in default_grading:
                config[key] = default_grading[key]
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE exam_papers
        SET tags_json = ?,
            status = ?,
            exam_config_json = ?,
            owner_role = 'teacher',
            owner_user_pk = COALESCE(owner_user_pk, teacher_id),
            scope_level = ?,
            school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            published_at = CASE WHEN ? = 'published' THEN COALESCE(published_at, ?) ELSE published_at END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps([str(item).strip() for item in tags if str(item).strip()], ensure_ascii=False),
            status,
            json.dumps(config, ensure_ascii=False),
            scope_level,
            owner_scope["school_code"],
            owner_scope["school_name"],
            owner_scope["college"],
            owner_scope["department"],
            status,
            now,
            now,
            paper_id,
        ),
    )


def serialize_course_content(conn: sqlite3.Connection, course_row: Any, teacher_id: int) -> dict[str, Any]:
    row = _row_dict(course_row)
    course_id = int(row["id"])
    lessons = load_course_lessons_by_course_id(conn, [course_id]).get(course_id, [])
    course = serialize_course_row(row, lessons=lessons)
    can_manage = teacher_can_manage_course(conn, teacher_id, row)
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "lessons": course.get("lessons", []),
        "permissions": build_mode_permissions(can_use=True, can_manage=can_manage),
    }
