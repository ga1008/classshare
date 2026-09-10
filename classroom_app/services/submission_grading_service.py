"""Normal manual grading in the caller's transaction, shared by Web and Agent.

Authorization, answer revision, AI fencing, late policy, grade history, group
settlement and notifications stay together. This module never commits.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from fastapi import HTTPException

from ..db.connection import begin_immediate_transaction, get_configured_db_engine
from .assignment_management_service import (
    _teacher_can_access_assignment, _hide_personal_stage_asset, assignment_revision, load_assignment_row,
)
from .grading_revision_service import activate_submission_grade_revision
from .group_assignment_service import lock_group_grading_for_submission
from .submission_grade_guard_service import (
    editable_manual_feedback, ensure_manual_grade_revision, lock_submission_for_manual_grade,
    submission_review_revision, validate_manual_grade,
)
from .late_submission_policy import apply_late_policy_to_score, append_late_policy_feedback
from .message_center_service import create_student_grading_notification
from .learning_progress_service import (
    handle_stage_exam_grading_complete, handle_assignment_stage_grading_complete,
    refresh_student_learning_state,
)


def load_teacher_submission(conn, submission_id: int, teacher_id: int) -> dict[str, Any]:
    submission = conn.execute(
        """
        SELECT s.*,
               a.course_id,
               a.class_offering_id,
               a.allowed_file_types_json,
               a.due_at AS assignment_due_at,
               a.late_submission_enabled AS assignment_late_submission_enabled,
               a.late_submission_until AS assignment_late_submission_until,
               a.late_penalty_strategy AS assignment_late_penalty_strategy,
               a.late_penalty_interval_hours AS assignment_late_penalty_interval_hours,
               a.late_penalty_points AS assignment_late_penalty_points,
               a.late_penalty_min_score AS assignment_late_penalty_min_score,
               a.late_score_cap AS assignment_late_score_cap,
               a.title AS assignment_title,
               c.created_by_teacher_id,
               o.teacher_id AS offering_teacher_id,
               lsea.id AS personal_stage_attempt_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN learning_stage_exam_attempts lsea ON lsea.assignment_id = a.id
        WHERE s.id = ?
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not submission:
        raise HTTPException(404, "提交记录不存在")
    submission_dict = dict(submission)
    if not _teacher_can_access_assignment(conn, submission_dict, int(teacher_id)):
        raise HTTPException(403, "无权操作该提交")
    if submission_dict.get("personal_stage_attempt_id") is not None:
        _hide_personal_stage_asset()
    return submission_dict


def grade_submission_record(conn, *, submission_id: int, teacher_id: int, data: dict,
                            actor_display_name: str = "") -> dict:
    """Write one manual grade; the caller commits the business and its receipt."""
    score = validate_manual_grade(data)
    # Editors already lock assignment -> submission when invalidating AI input.
    # Use the same order before group/submission locks so the rubric reviewed by
    # a new client cannot change between its version check and the grade commit.
    engine = get_configured_db_engine()
    if engine != "sqlite" or not bool(getattr(conn, "in_transaction", False)):
        begin_immediate_transaction(conn, engine=engine)
    identity = conn.execute("SELECT assignment_id FROM submissions WHERE id=?", (submission_id,)).fetchone()
    if identity and engine == "postgres":
        conn.execute("SELECT id FROM assignments WHERE id=? FOR UPDATE", (identity["assignment_id"],)).fetchone()
    lock_group_grading_for_submission(conn, submission_id)
    lock_submission_for_manual_grade(conn, submission_id)
    submission = load_teacher_submission(conn, submission_id, teacher_id)
    ensure_manual_grade_revision(submission, data)
    assignment = load_assignment_row(conn, submission["assignment_id"])
    if "expected_assignment_revision" in data and (
        not assignment or data["expected_assignment_revision"] != assignment_revision(assignment)
    ):
        raise HTTPException(409, "作业要求或评分标准已变化，请重新读取答卷和评分标准。")
    if int(submission.get("resubmission_allowed") or 0):
        raise HTTPException(400, "该提交已撤回并等待重交，不能批改旧版本")
    assignment_for_late_policy = {
        "id": submission.get("assignment_id"),
        "due_at": submission.get("assignment_due_at"),
        "late_submission_enabled": submission.get("assignment_late_submission_enabled"),
        "late_submission_until": submission.get("assignment_late_submission_until"),
        "late_penalty_strategy": submission.get("assignment_late_penalty_strategy"),
        "late_penalty_interval_hours": submission.get("assignment_late_penalty_interval_hours"),
        "late_penalty_points": submission.get("assignment_late_penalty_points"),
        "late_penalty_min_score": submission.get("assignment_late_penalty_min_score"),
        "late_score_cap": submission.get("assignment_late_score_cap"),
    }
    adjustment = apply_late_policy_to_score(
        score,
        submission=submission,
        assignment=assignment_for_late_policy,
    )
    final_score = adjustment.get("final_score")
    feedback_md = append_late_policy_feedback(editable_manual_feedback(data.get("feedback_md")), adjustment)
    active_ai_job_id = submission.get("grading_job_id")
    if active_ai_job_id:
        conn.execute(
            """
            UPDATE ai_jobs
            SET status = 'superseded', lease_token = '', lease_expires_at = NULL,
                locked_at = NULL, locked_by = '', updated_at = ?, finished_at = ?
            WHERE id = ? AND status IN ('queued', 'retry_wait', 'running', 'result_ready')
            """,
            (datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"), int(active_ai_job_id)),
        )
    conn.execute(
        """
        UPDATE submissions
        SET status = 'graded',
            score = ?,
            feedback_md = ?,
            score_before_late_penalty = ?,
            late_penalty_points = ?,
            late_score_cap_applied = ?,
            grading_started_at = NULL,
            grading_attempt_fingerprint = NULL,
            grading_revision_hash = NULL,
            grading_job_id = NULL,
            resubmission_allowed = 0,
            resubmission_due_at = NULL,
            returned_at = NULL,
            returned_by_teacher_id = NULL,
            returned_reason = NULL
        WHERE id = ?
        """,
        (
            final_score,
            feedback_md,
            adjustment.get("original_score") if adjustment.get("applied") else None,
            adjustment.get("penalty_points") or 0,
            1 if adjustment.get("score_cap_applied") else 0,
            submission_id,
        ),
    )
    activate_submission_grade_revision(
        conn,
        submission={**submission, "grading_job_id": None},
        data={
            "grading_revision_hash": f"manual:{submission_id}:{uuid.uuid4().hex}",
            "source": "manual",
            "actor_role": "teacher",
            "actor_user_pk": int(teacher_id),
            "quality_audit": {"manual_grade": True},
        },
        score=final_score,
        feedback_md=feedback_md,
    )
    try:
        create_student_grading_notification(
            conn,
            submission_id,
            actor_role="teacher",
            actor_user_pk=int(teacher_id),
            actor_display_name=str(actor_display_name or ""),
        )
    except Exception as exc:
        print(f"[MESSAGE_CENTER] manual grading notify failed: {exc}")
    try:
        handle_stage_exam_grading_complete(conn, submission_id)
    except Exception as exc:
        print(f"[LEARNING_PROGRESS] manual grading stage handling failed: {exc}")
    try:
        handle_assignment_stage_grading_complete(conn, submission_id)
    except Exception as exc:
        print(f"[LEARNING_PROGRESS] manual grading teacher-stage handling failed: {exc}")
    # Group settlement is part of this grade: propagate failure and rollback.
    from .group_assignment_service import record_member_work_score

    record_member_work_score(conn, submission_id)
    if submission.get("class_offering_id") and submission.get("student_pk_id"):
        try:
            refresh_student_learning_state(
                conn,
                int(submission["class_offering_id"]),
                int(submission["student_pk_id"]),
                event_source_ref=f"grading:{submission_id}",
            )
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] manual grading snapshot refresh failed: {exc}")
    # A downstream best-effort hook may have caught its own SQL exception.
    # PostgreSQL rejects this probe on an aborted transaction; COMMIT alone
    # would silently roll back and let the endpoint report false success.
    conn.execute("SELECT 1")
    current = load_teacher_submission(conn, submission_id, teacher_id)
    return {"status": "success", "graded_submission_id": int(submission_id),
            "grade_revision_id": current.get("active_grade_revision_id"),
            "score": current.get("score"), "review_revision": submission_review_revision(current)}


def get_teacher_submission_review(conn, *, submission_id: int, teacher_id: int) -> dict:
    """Expose the answer and the two versions used for a subsequent grade."""
    submission = load_teacher_submission(conn, submission_id, teacher_id)
    assignment = load_assignment_row(conn, submission["assignment_id"])
    fields = ("id", "assignment_id", "student_pk_id", "student_name", "status", "answers_json",
              "feedback_md", "score", "submitted_at", "resubmission_allowed", "is_absence_score",
              "is_late_submission", "late_by_seconds", "score_before_late_penalty")
    attachments = list_teacher_submission_files(conn, submission_id=submission_id, teacher_id=teacher_id)
    return {"submission": {key: submission.get(key) for key in fields}, "files": attachments["items"],
            "files_has_more": attachments["has_more"], "files_next_offset": attachments["next_offset"],
            "expected_review_revision": submission_review_revision(submission),
            "expected_assignment_revision": assignment_revision(assignment),
            "assignment": {key: dict(assignment).get(key) for key in
                           ("id", "title", "requirements_md", "rubric_md", "class_offering_id", "exam_paper_id")}}


def list_teacher_submission_files(conn, *, submission_id: int, teacher_id: int,
                                  limit: int = 50, offset: int = 0) -> dict:
    load_teacher_submission(conn, submission_id, teacher_id)
    if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
        raise HTTPException(400, "附件分页范围不正确。")
    rows = conn.execute("""SELECT id,original_filename,mime_type,file_size FROM submission_files
                           WHERE submission_id=? ORDER BY id LIMIT ? OFFSET ?""",
                        (submission_id, limit + 1, offset)).fetchall()
    return {"items": [{**dict(row), "submission_file_id": row["id"]} for row in rows[:limit]],
            "has_more": len(rows) > limit, "next_offset": offset + min(len(rows), limit)}


def list_teacher_review_submissions(conn, *, assignment_id: str, teacher_id: int,
                                    limit: int = 30, offset: int = 0) -> dict:
    from .learning_progress_service import is_personal_stage_exam_assignment

    assignment = load_assignment_row(conn, assignment_id)
    if not assignment:
        raise HTTPException(404, "作业不存在。")
    if not _teacher_can_access_assignment(conn, dict(assignment), teacher_id):
        raise HTTPException(403, "无权查看该作业答卷。")
    if is_personal_stage_exam_assignment(conn, assignment_id):
        _hide_personal_stage_asset()
    size, start = max(1, min(int(limit), 50)), max(0, min(int(offset), 10000))
    rows = conn.execute("""SELECT id,student_pk_id,student_name,status,submitted_at,score,resubmission_allowed
                           FROM submissions WHERE assignment_id=? ORDER BY id LIMIT ? OFFSET ?""",
                        (assignment_id, size + 1, start)).fetchall()
    return {"items": [dict(row) for row in rows[:size]], "limit": size, "offset": start,
            "has_more": len(rows) > size}
