"""Assignment creation shared by Web and Agent, within the caller transaction."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.connection import execute_insert_returning_id
from .assessment_classification_service import initialize_assignment_assessment_kind, normalize_assessment_kind
from .assignment_lifecycle_service import build_assignment_schedule_fields


def create_assignment_record(conn, *, teacher_id: int, course_id: int, data: dict[str, Any],
                             allowed_file_types_json: str = "[]", learning_stage_key: str | None = None) -> dict[str, Any]:
    """Apply normal ownership, scheduling and formal classification; no commit/files."""
    try:
        assessment_kind = normalize_assessment_kind(data.get("assessment_kind") if "assessment_kind" in data else "homework")
        schedule_fields = build_assignment_schedule_fields(data, default_status="new")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    class_offering_id = data.get("class_offering_id")
    actual_course_id = int(course_id)
    if class_offering_id:
        offering = conn.execute("SELECT id,course_id FROM class_offerings WHERE id=? AND teacher_id=?", (int(class_offering_id), int(teacher_id))).fetchone()
        if not offering:
            raise HTTPException(404, "当前课堂不存在或您无权操作")
        actual_course_id = int(offering["course_id"])
    elif not conn.execute("SELECT id FROM courses WHERE id=? AND created_by_teacher_id=?", (actual_course_id, int(teacher_id))).fetchone():
        raise HTTPException(404, "课程不存在或您无权操作")
    from .teaching_lifecycle_service import lock_teaching_context
    current = lock_teaching_context(conn, course_id=actual_course_id,
        class_offering_id=int(class_offering_id) if class_offering_id else None)
    owner = current.get("teacher_id") if class_offering_id else current.get("created_by_teacher_id")
    if int(owner or 0) != int(teacher_id):
        raise HTTPException(403, "教学资源归属已变化，请重新选择。")
    created_at = datetime.now().isoformat()
    new_id = execute_insert_returning_id(conn, """
        INSERT INTO assignments (
            course_id,title,status,requirements_md,rubric_md,grading_mode,class_offering_id,created_at,allowed_file_types_json,
            availability_mode,starts_at,due_at,duration_minutes,auto_close,closed_at,
            late_submission_enabled,late_submission_until,late_penalty_strategy,late_penalty_interval_hours,
            late_penalty_points,late_penalty_min_score,late_score_cap,learning_stage_key
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (actual_course_id,data["title"],schedule_fields["status"],data.get("requirements_md", ""),data.get("rubric_md", ""),
           data.get("grading_mode", "manual"),int(class_offering_id) if class_offering_id else None,created_at,allowed_file_types_json,
           *(schedule_fields[key] for key in ("availability_mode","starts_at","due_at","duration_minutes","auto_close","closed_at",
             "late_submission_enabled","late_submission_until","late_penalty_strategy","late_penalty_interval_hours",
             "late_penalty_points","late_penalty_min_score","late_score_cap")),learning_stage_key))
    classification = initialize_assignment_assessment_kind(conn, new_id, assessment_kind=assessment_kind, teacher_id=int(teacher_id),
                    source="teacher_create" if "assessment_kind" in data else "ordinary_create_default")
    return {"id": new_id, "course_id": actual_course_id, "created_at": created_at, "schedule_fields": schedule_fields, "classification": classification}
