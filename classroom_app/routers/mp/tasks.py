"""小程序"作业考试"列表：学生视角的作业/考试任务流.

复用 todo_service.build_classroom_todo_overview（Web 待办同源），
按 offering 聚合后只保留作业/考试类条目，分 pending / completed。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...db.connection import get_db_connection
from ...services.assignment_lifecycle_service import (
    close_overdue_assignments,
    refresh_assignment_runtime_status,
    submission_is_returned,
    submission_resubmission_state,
)
from ...services.dashboard_service import _load_student_offerings
from ...services.exam_json_service import strip_exam_scoring_for_student
from ...services.learning_progress_service import student_can_access_assignment
from ...services.todo_service import (
    TODO_SOURCE_ACADEMIC_EXAM,
    TODO_SOURCE_ASSIGNMENT,
    TODO_SOURCE_STAGE,
    build_classroom_todo_overview,
)
from .deps import get_current_mp_student

router = APIRouter(prefix="/tasks")

_TASK_SOURCE_TYPES = {TODO_SOURCE_ASSIGNMENT, TODO_SOURCE_STAGE, TODO_SOURCE_ACADEMIC_EXAM}


def _project_task(item: dict[str, Any], offering: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    is_exam = item.get("source_type") != TODO_SOURCE_ASSIGNMENT or bool(metadata.get("is_exam"))
    return {
        "id": item.get("id"),
        "source_type": item.get("source_type"),
        "source_id": item.get("source_id"),
        "is_exam": is_exam,
        "title": item.get("title"),
        "subtitle": item.get("subtitle"),
        "status": item.get("status"),
        "status_label": item.get("status_label"),
        "tone": item.get("tone"),
        "is_completed": bool(item.get("is_completed")),
        "no_deadline": bool(item.get("no_deadline")),
        "due_at": item.get("due_at"),
        "deadline_label": item.get("deadline_label"),
        "relative_due_label": item.get("relative_due_label"),
        "link_url": item.get("link_url"),
        "offering_id": offering.get("id"),
        "course_name": offering.get("course_name"),
        "teacher_name": offering.get("teacher_name"),
    }


@router.get("")
def mp_tasks(user: dict = Depends(get_current_mp_student)):
    pending: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    with get_db_connection() as conn:
        offerings = _load_student_offerings(conn, int(user["id"]))
        for offering in offerings:
            try:
                overview = build_classroom_todo_overview(
                    conn,
                    class_offering_id=int(offering["id"]),
                    user=user,
                )
            except Exception as exc:
                # 单个课程失败不拖垮整个列表。
                print(f"[WECHAT_MP] 任务列表加载失败 offering={offering.get('id')}: {exc}")
                continue
            for item in overview.get("items", []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("source_type") or "") not in _TASK_SOURCE_TYPES:
                    continue
                task = _project_task(item, offering)
                (completed if task["is_completed"] else pending).append(task)
        conn.commit()

    pending.sort(key=lambda t: (t["no_deadline"], t["due_at"] or "9999"))
    completed.sort(key=lambda t: t["due_at"] or "", reverse=True)
    return {
        "success": True,
        "data": {"pending": pending, "completed": completed},
        "error": None,
    }


def _parse_submission_answers(raw: Any) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return []
    answers = data.get("answers") if isinstance(data, dict) else None
    return answers if isinstance(answers, list) else []


def _serialize_my_submission(submission: Optional[dict]) -> Optional[dict[str, Any]]:
    if not submission:
        return None
    return {
        "status": submission.get("status"),
        "score": submission.get("score"),
        "feedback_md": submission.get("feedback_md") or "",
        "submitted_at": submission.get("submitted_at"),
        "answers": _parse_submission_answers(submission.get("answers_json")),
        "is_returned": submission_is_returned(submission),
        "resubmission_state": submission_resubmission_state(submission),
        "resubmission_due_at": submission.get("resubmission_due_at"),
    }


@router.get("/assignment/{assignment_id}")
def mp_task_detail(assignment_id: str, user: dict = Depends(get_current_mp_student)):
    """作答页数据：作业信息 + 学生视图题目（已剥答案）+ 我的提交。

    作答/草稿/提交动作全部走既有 /api 端点（bearer 直通），
    这里只负责把 Web 作答页的模板上下文投影成 JSON。
    """
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "作业不存在")
        assignment = refresh_assignment_runtime_status(conn, row)
        if not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该任务只对指定学生开放")
        if assignment.get("status") == "new":
            raise HTTPException(403, "该任务尚未发布")

        paper_payload = None
        if assignment.get("exam_paper_id"):
            paper_row = conn.execute(
                "SELECT title, description, questions_json FROM exam_papers WHERE id = ?",
                (assignment["exam_paper_id"],),
            ).fetchone()
            if paper_row:
                try:
                    paper_data = json.loads(paper_row["questions_json"] or "{}")
                except (TypeError, ValueError):
                    paper_data = {"pages": []}
                paper_payload = {
                    "title": paper_row["title"],
                    "description": paper_row["description"] or "",
                    **strip_exam_scoring_for_student(paper_data),
                }

        submission_row = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, int(user["id"])),
        ).fetchone()
        submission = dict(submission_row) if submission_row else None
        if submission and int(submission.get("is_absence_score") or 0):
            submission = None
        conn.commit()

    course_row_fields = {
        "id": assignment.get("id"),
        "title": assignment.get("title"),
        "requirements_md": assignment.get("requirements_md") or "",
        "status": assignment.get("status"),
        "is_exam": bool(assignment.get("exam_paper_id")),
        "starts_at": assignment.get("starts_at"),
        "due_at": assignment.get("due_at"),
        "remaining_seconds": assignment.get("remaining_seconds"),
        "availability_mode_label": assignment.get("availability_mode_label"),
        "deadline_phase": assignment.get("deadline_phase"),
        "is_accepting_submissions": bool(assignment.get("is_accepting_submissions")),
        "is_late_submission_open": bool(assignment.get("is_late_submission_open")),
        "late_policy_label": assignment.get("late_policy_label") or "",
    }
    return {
        "success": True,
        "data": {
            "assignment": course_row_fields,
            "paper": paper_payload,
            "submission": _serialize_my_submission(submission),
        },
        "error": None,
    }
