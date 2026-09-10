"""Assignment mutation and request normalization shared by Web and Agent."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from fastapi import HTTPException

from ..db.connection import get_configured_db_engine
from .assessment_classification_service import assessment_kind_info, set_assignment_assessment_kind
from .assignment_lifecycle_service import build_assignment_schedule_fields, refresh_assignment_runtime_status
from .assignment_reminder_service import sync_assignment_due_reminders
from .learning_progress_service import is_personal_stage_exam_assignment, normalize_assignment_stage_key
from .message_center_service import create_assignment_published_notifications
from .resource_access_service import teacher_can_manage_assignment
from .submission_assets import normalize_allowed_file_types, decode_allowed_file_types_json, encode_allowed_file_types_json

PERSONAL_STAGE_TEACHER_HIDDEN_MESSAGE = "学生个人试炼属于学生资产，不在教师作业与考试中展示；请查看班级修行统计。"


def load_assignment_row(conn, assignment_id):
    return conn.execute("""SELECT a.*, c.created_by_teacher_id, o.teacher_id AS offering_teacher_id
        FROM assignments a JOIN courses c ON c.id=a.course_id
        LEFT JOIN class_offerings o ON o.id=a.class_offering_id WHERE a.id=?""", (assignment_id,)).fetchone()


def assignment_revision(row):
    return hashlib.sha256(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _truthy_request_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "checked"}


def _wants_assignment_email_notification(data: dict[str, Any]) -> bool:
    for key in (
        "send_email_notification",
        "send_email_notifications",
        "notify_students_by_email",
        "email_notification_enabled",
    ):
        if key in data:
            return _truthy_request_flag(data.get(key))
    return False


def _get_allowed_file_types(data: dict, assignment_row=None) -> list[str]:
    if "allowed_file_types" in data:
        return normalize_allowed_file_types(data.get("allowed_file_types"))
    if "allowed_file_types_json" in data:
        return decode_allowed_file_types_json(data.get("allowed_file_types_json"))
    if assignment_row is not None:
        return decode_allowed_file_types_json(assignment_row["allowed_file_types_json"])
    return []


def _get_learning_stage_key(data: dict, *, class_offering_id: Any = None) -> str | None:
    raw_stage_key = data.get("learning_stage_key", data.get("stage_key"))
    try:
        stage_key = normalize_assignment_stage_key(raw_stage_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if stage_key and not class_offering_id:
        raise HTTPException(400, "仅课堂内作业或考试可以设定为阶段试炼")
    return stage_key


def _teacher_can_access_assignment(conn, assignment: dict[str, Any], teacher_id: int) -> bool:
    return teacher_can_manage_assignment(conn, int(teacher_id), assignment)


def _hide_personal_stage_asset() -> None:
    raise HTTPException(404, PERSONAL_STAGE_TEACHER_HIDDEN_MESSAGE)


def update_assignment_record(conn, *, assignment_id, teacher_id: int, data: dict, expected_revision: str | None = None):
    """Apply normal owner, grading, publication and reminder effects without committing."""
    if get_configured_db_engine() == "postgres":
        conn.execute("SELECT id FROM assignments WHERE id=? FOR UPDATE", (assignment_id,)).fetchone()
    assignment = conn.execute(
        """SELECT a.*,
                  c.created_by_teacher_id,
                  o.teacher_id AS offering_teacher_id
           FROM assignments a
           JOIN courses c ON a.course_id = c.id
           LEFT JOIN class_offerings o ON o.id = a.class_offering_id
           WHERE a.id = ?""",
        (assignment_id,)
    ).fetchone()
    if not assignment:
        raise HTTPException(404, "作业不存在")
    if not _teacher_can_access_assignment(conn, dict(assignment), int(teacher_id)):
        raise HTTPException(403, "无权修改该作业")
    if is_personal_stage_exam_assignment(conn, assignment_id):
        _hide_personal_stage_asset()
    assignment_dict = dict(assignment)
    if expected_revision is not None and assignment_revision(assignment_dict) != expected_revision:
        raise HTTPException(409, "作业已变化，请重新读取后再提交。")
    assignment_dict = refresh_assignment_runtime_status(conn, assignment_dict)
    classification = assessment_kind_info(assignment_dict)
    if "assessment_kind" in data:
        classification = set_assignment_assessment_kind(
            conn, assignment_dict, assessment_kind=data["assessment_kind"],
            expected_version=data.get("expected_version"), teacher_id=int(teacher_id),
            reason=data.get("classification_reason", ""),
        )

    previous_status = str(assignment_dict['status'] or '')
    allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data, assignment_dict))
    requirements_md = data.get('requirements_md', assignment_dict.get('requirements_md')) or ''
    rubric_md = data.get('rubric_md', assignment_dict.get('rubric_md')) or ''
    grading_inputs_changed = (
        requirements_md != (assignment_dict.get('requirements_md') or '')
        or rubric_md != (assignment_dict.get('rubric_md') or '')
        or allowed_file_types_json != encode_allowed_file_types_json(_get_allowed_file_types({}, assignment_dict))
    )
    if "learning_stage_key" in data or "stage_key" in data:
        learning_stage_key = _get_learning_stage_key(
            data,
            class_offering_id=assignment_dict.get("class_offering_id"),
        )
    else:
        learning_stage_key = assignment_dict.get("learning_stage_key")
    try:
        schedule_fields = build_assignment_schedule_fields(
            data,
            existing=assignment_dict,
            default_status=assignment_dict["status"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn.execute(
        """
        UPDATE assignments
        SET title = ?, requirements_md = ?, rubric_md = ?, grading_mode = ?,
            status = ?, allowed_file_types_json = ?,
            availability_mode = ?, starts_at = ?, due_at = ?, duration_minutes = ?, auto_close = ?, closed_at = ?,
            late_submission_enabled = ?, late_submission_until = ?, late_penalty_strategy = ?,
            late_penalty_interval_hours = ?, late_penalty_points = ?, late_penalty_min_score = ?, late_score_cap = ?,
            learning_stage_key = ?
        WHERE id = ?
        """,
        (
            data.get('title', assignment_dict.get('title', '')),
            requirements_md,
            rubric_md,
            data.get('grading_mode', assignment_dict['grading_mode']),
            schedule_fields["status"],
            allowed_file_types_json,
            schedule_fields["availability_mode"],
            schedule_fields["starts_at"],
            schedule_fields["due_at"],
            schedule_fields["duration_minutes"],
            schedule_fields["auto_close"],
            schedule_fields["closed_at"],
            schedule_fields["late_submission_enabled"],
            schedule_fields["late_submission_until"],
            schedule_fields["late_penalty_strategy"],
            schedule_fields["late_penalty_interval_hours"],
            schedule_fields["late_penalty_points"],
            schedule_fields["late_penalty_min_score"],
            schedule_fields["late_score_cap"],
            learning_stage_key,
            assignment_id,
        )
    )
    if grading_inputs_changed:
        from .ai_grading_service import invalidate_assignment_grading_inputs
        invalidate_assignment_grading_inputs(conn, [assignment_id])
    if previous_status != 'published' and schedule_fields["status"] == 'published':
        try:
            create_assignment_published_notifications(
                conn,
                assignment_id,
                send_email_notification=_wants_assignment_email_notification(data),
            )
        except Exception as exc:
            print(f"[MESSAGE_CENTER] assignment publish notify failed: {exc}")
    sync_assignment_due_reminders(
        conn,
        assignment_id,
        status=schedule_fields["status"],
        due_at=schedule_fields["due_at"],
        class_offering_id=assignment_dict.get("class_offering_id"),
        title=str(data.get('title', assignment_dict.get('title')) or ''),
    )
    return {
        "status": "success",
        "updated_assignment_id": assignment_id,
        "revision": assignment_revision(load_assignment_row(conn, assignment_id)),
        **{key: value for key, value in classification.items() if key not in {"assignment_id", "changed"}},
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
    }
