"""Read-only effective grades, with release rules applied before aggregation.

Callers authorize the resource scope. This module never grants access, writes a
grade, converts the existing percentage scale, or treats a missing grade as zero.
"""
from __future__ import annotations

import math
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def project_submission_score(row: Any, *, student_view: bool = False) -> dict[str, Any]:
    item = dict(row)
    status = str(item.get("status") or "").lower()
    active = bool(item.get("effective_revision_id"))
    is_group = bool(item.get("group_binding_id"))
    revealed = bool(item.get("group_revealed")) and bool(item.get("group_membership_valid"))
    score = _number(item.get("revision_score")) if active else _number(item.get("score"))
    feedback = str(item.get("revision_feedback_md") or "") if active else str(item.get("feedback_md") or "")
    # Old group finalizations predate revisions; their ledger is the settled
    # grade and must not be replaced by an earlier, raw AI revision.
    if is_group and revealed:
        score = _number(item.get("group_final_score"))
        feedback = str(item.get("feedback_md") or feedback)
    returned = bool(item.get("resubmission_allowed"))
    valid = score is not None and not returned and (
        active or status in {"graded", "grading", "grading_review"} or bool(item.get("is_absence_score"))
    )
    visible = valid and (not student_view or not is_group or revealed)
    item.update(
        effective_score=score if visible else None,
        score=score if visible else None,
        feedback_md=feedback if visible else "",
        has_effective_score=bool(valid) if not student_view else bool(visible),
        score_visible=bool(visible),
        is_regrading=bool(valid and status in {"grading", "grading_review", "submitted"}),
        is_personal_stage=bool(item.get("personal_stage_assignment_id")),
        can_export_answer=bool(visible and not item.get("is_absence_score")),
        grade_display_state=("returned" if returned else "group_pending" if is_group and not revealed
                             else "regrading" if valid and status != "graded"
                             else "absence_zero" if valid and item.get("is_absence_score")
                             else "graded" if valid else "pending"),
    )
    # Never serialize an alternate raw score/revision through student callers.
    for key in ("revision_score", "revision_feedback_md", "group_final_score"):
        item.pop(key, None)
    if student_view and not visible:
        for key in ("score_before_late_penalty", "late_penalty_points", "active_grade_revision_id", "effective_revision_id"):
            item[key] = None
    if student_view:
        item.pop("grade_quality_audit_json", None)
        item.pop("grade_provenance_json", None)
    return item


def load_submission_score_facts(
    conn, *, submission_ids: Iterable[Any] | None = None,
    assignment_ids: Iterable[Any] | None = None, student_id: int | None = None,
    student_view: bool = False,
    include_content: bool = True,
) -> list[dict[str, Any]]:
    """Batch-load only an explicitly authorized scope; no schema writes/N+1."""
    if submission_ids is not None and assignment_ids is not None:
        raise ValueError("Choose submission_ids or assignment_ids")
    ids = list(dict.fromkeys(submission_ids if submission_ids is not None else assignment_ids or []))
    bounded = submission_ids is not None or assignment_ids is not None
    if bounded and not ids:
        return []
    if not bounded and student_id is None:
        raise ValueError("An explicit submission, assignment, or student scope is required")
    result = []
    chunks = [ids[i:i + 400] for i in range(0, len(ids), 400)] if bounded else [None]
    for chunk in chunks:
        where, params = [], []
        if chunk is not None:
            column = "s.id" if submission_ids is not None else "s.assignment_id"
            where.append(f"{column} IN ({','.join('?' for _ in chunk)})")
            params.extend(chunk)
        if student_id is not None:
            where.append("s.student_pk_id = ?")
            params.append(int(student_id))
        submission_columns = "s.*" if include_content else (
            "s.id, s.assignment_id, s.student_pk_id, s.status, s.score, s.submitted_at, "
            "s.resubmission_allowed, s.is_absence_score, s.is_late_submission, s.active_grade_revision_id, '' AS feedback_md"
        )
        revision_feedback = "r.feedback_md" if include_content else "''"
        quality_columns = ("NULL AS grade_quality_audit_json, NULL AS grade_provenance_json" if student_view else
                           "r.quality_audit_json AS grade_quality_audit_json, r.provenance_json AS grade_provenance_json")
        rows = conn.execute(f"""
            SELECT {submission_columns}, r.id AS effective_revision_id, r.score AS revision_score,
                   {revision_feedback} AS revision_feedback_md,
                   {quality_columns},
                   b.id AS group_binding_id, gr.revealed AS group_revealed,
                   gr.final_score AS group_final_score,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM study_group_members gm JOIN study_groups g ON g.id = gm.group_id
                       WHERE gm.student_id = s.student_pk_id AND gm.status = 'active'
                         AND g.scheme_id = b.scheme_id AND g.id = gr.group_id
                   ) THEN 1 ELSE 0 END AS group_membership_valid,
                   CASE WHEN EXISTS (SELECT 1 FROM learning_stage_exam_attempts p
                       WHERE p.assignment_id = s.assignment_id) THEN s.assignment_id END AS personal_stage_assignment_id
            FROM submissions s
            LEFT JOIN submission_grade_revisions r ON r.id = s.active_grade_revision_id
                AND r.submission_id = s.id AND r.status = 'active'
            LEFT JOIN assignment_group_bindings b ON b.assignment_id = CAST(s.assignment_id AS TEXT) AND b.status = 'active'
            LEFT JOIN group_assignment_member_results gr ON gr.assignment_id = CAST(s.assignment_id AS TEXT)
                AND gr.student_pk_id = s.student_pk_id
            WHERE {' AND '.join(where)}
            ORDER BY s.submitted_at, s.id
        """, tuple(params)).fetchall()
        result.extend(project_submission_score(row, student_view=student_view) for row in rows)
    return result
