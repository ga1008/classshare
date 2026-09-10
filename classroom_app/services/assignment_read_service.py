"""Side-effect-free JSON views using normal assignment and grade policies."""
from __future__ import annotations

from fastapi import HTTPException

from .assignment_management_service import assignment_revision, load_assignment_row
from .assignment_lifecycle_service import enrich_assignment_runtime_view
from .assessment_classification_service import assessment_kind_info
from .learning_progress_service import is_personal_stage_exam_assignment
from .resource_access_service import ensure_classroom_access, student_can_read_assignment, teacher_can_manage_assignment
from .submission_assets import decode_allowed_file_types_json

_PUBLIC_FIELDS = ("id", "title", "course_id", "class_offering_id", "status", "availability_mode", "availability_mode_label",
                  "starts_at", "due_at", "duration_minutes", "auto_close", "remaining_seconds", "is_accepting_submissions",
                  "deadline_phase", "is_late_submission_open", "late_policy_label", "server_now", "learning_stage_key")
_SUBMISSION_FIELDS = ("id", "status", "submitted_at", "answers_json", "resubmission_allowed", "resubmission_due_at",
                      "returned_at", "is_absence_score", "is_late_submission", "score", "effective_score", "score_visible",
                      "feedback_md", "grade_display_state", "has_effective_score")


def _authorize(conn, row, user):
    if not row:
        raise HTTPException(404, "作业不存在。")
    if user.get("role") == "teacher":
        if is_personal_stage_exam_assignment(conn, row["id"]):
            raise HTTPException(404, "个人试炼属于学生资产，请查看课堂汇总。")
        allowed = teacher_can_manage_assignment(conn, int(user["id"]), row)
    elif user.get("role") == "student":
        allowed = str(row["status"] or "") != "new" and student_can_read_assignment(conn, row, int(user["id"]))
    else:
        allowed = False
    if not allowed:
        raise HTTPException(403, "无权查看该作业。")


def _project(row, user, *, detailed=False):
    raw = dict(row)
    view = enrich_assignment_runtime_view(raw)
    item = {key: view.get(key) for key in _PUBLIC_FIELDS}
    item.update(assessment_kind_info(view))
    is_exam = bool(raw.get("exam_paper_id"))
    item.update(is_exam=is_exam, url=f"/exam/take/{raw['id']}" if is_exam and user["role"] == "student" else f"/assignment/{raw['id']}")
    if user["role"] == "teacher":
        item["revision"] = assignment_revision(raw)
        item["grading_mode"] = raw.get("grading_mode")
    if detailed:
        item["allowed_file_types"] = decode_allowed_file_types_json(raw.get("allowed_file_types_json"))
        if not is_exam or user["role"] == "teacher":
            item["requirements_md"] = raw.get("requirements_md") or ""
        if user["role"] == "teacher":
            item["rubric_md"] = raw.get("rubric_md") or ""
        if is_exam and user["role"] == "student":
            item["exam_content_access"] = "use_normal_exam_answering_flow"
    return item


def list_classroom_assignments(conn, *, class_offering_id: int, user: dict, limit: int = 30, offset: int = 0):
    ensure_classroom_access(conn, class_offering_id, user)
    size, start = max(1, min(int(limit), 50)), max(0, min(int(offset), 10000))
    where = "a.class_offering_id=?"
    values = [class_offering_id]
    if user["role"] == "teacher":
        where += " AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts p WHERE p.assignment_id=a.id)"
    else:
        where += " AND a.status<>'new' AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts p WHERE p.assignment_id=a.id AND p.student_id<>?)"
        values.append(int(user["id"]))
    rows = conn.execute(f"""SELECT a.*, c.created_by_teacher_id, o.teacher_id AS offering_teacher_id
        FROM assignments a JOIN courses c ON c.id=a.course_id LEFT JOIN class_offerings o ON o.id=a.class_offering_id
        WHERE {where} ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?""", (*values, size + 1, start)).fetchall()
    items = []
    for row in rows[:size]:
        try:
            _authorize(conn, row, user)
        except HTTPException as exc:
            if exc.status_code in (403, 404):
                continue
            raise
        items.append(_project(row, user))
    return {"items": items, "limit": size, "offset": start, "has_more": len(rows) > size}


def get_assignment_details(conn, *, assignment_id: str, user: dict):
    row = load_assignment_row(conn, assignment_id)
    _authorize(conn, row, user)
    result = {"assignment": _project(row, user, detailed=True)}
    if user["role"] == "student":
        from .score_projection_service import load_submission_score_facts
        from .submission_write_guard import submission_write_version
        facts = load_submission_score_facts(conn, assignment_ids=[assignment_id], student_id=int(user["id"]), student_view=True)
        # This shared projection already checks release AND current membership;
        # unlike the legacy group view it does not run lazy schema writes.
        submission = facts[0] if facts else None
        result["submission"] = {key: submission.get(key) for key in _SUBMISSION_FIELDS} if submission else None
        result["submission_version"] = submission_write_version(submission)
    return result
