"""小程序"作业考试"列表：学生视角的作业/考试任务流.

直查 assignments（与教师端列表同口径的单一真源），LEFT JOIN 我的
提交分三桶：进行中（可作答未交）/ 已完成（已提交）/ 已截止（未交）。
首页统计复用同一查询（load_student_task_buckets），保证数字对齐。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...db.connection import get_db_connection
from ...services.assignment_lifecycle_service import (
    close_overdue_assignments,
    enrich_assignment_runtime_view,
    refresh_assignment_runtime_status,
    submission_is_returned,
    submission_resubmission_state,
)
from ...services.exam_json_service import strip_exam_scoring_for_student
from ...services.group_assignment_service import (
    get_student_display_state,
    get_student_group_context,
)
from ...services.learning_progress_service import student_can_access_assignment
from .deps import get_current_mp_student

router = APIRouter(prefix="/tasks")

TASK_LIMIT = 200

_SUB_STATUS_LABELS = {
    "submitted": "待批改",
    "grading": "AI批改中",
    "grading_review": "待确认",
    "graded": "已批改",
}


def load_student_task_buckets(conn: Any, student_id: int) -> dict[str, list[dict[str, Any]]]:
    """学生的作业/考试三桶列表。首页统计与列表页共用（数字必须对齐）。"""
    close_overdue_assignments(conn)
    rows = conn.execute(
        """
        SELECT a.*, o.id AS offering_id,
               c.name AS course_name, t.name AS teacher_name,
               s.id AS sub_id, s.status AS sub_status, s.score AS sub_score,
               COALESCE(s.is_absence_score, 0) AS sub_is_absence
        FROM assignments a
        JOIN class_offerings o ON o.id = a.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN submissions s
            ON s.assignment_id = a.id AND s.student_pk_id = ?
        WHERE o.class_id = (SELECT class_id FROM students WHERE id = ?)
          AND a.status != 'new'
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT ?
        """,
        (int(student_id), int(student_id), TASK_LIMIT),
    ).fetchall()

    buckets: dict[str, list[dict[str, Any]]] = {"pending": [], "completed": [], "expired": []}
    for row in rows:
        item = dict(row)
        runtime = enrich_assignment_runtime_view(item)
        submitted = bool(item.get("sub_id")) and not int(item.get("sub_is_absence") or 0)
        sub_status = str(item.get("sub_status") or "")
        task = {
            "source_type": "assignment",
            "source_id": item["id"],
            "is_exam": bool(item.get("exam_paper_id")),
            "title": item.get("title"),
            "course_name": item.get("course_name") or "",
            "teacher_name": item.get("teacher_name") or "",
            "due_at": runtime.get("due_at") or "",
            "no_deadline": not runtime.get("due_at"),
            "remaining_seconds": runtime.get("remaining_seconds"),
            "is_accepting": bool(runtime.get("is_accepting_submissions")),
            "score": item.get("sub_score"),
        }
        if submitted:
            task["status_label"] = _SUB_STATUS_LABELS.get(sub_status, sub_status or "已提交")
            buckets["completed"].append(task)
        elif task["is_accepting"]:
            task["status_label"] = "进行中"
            buckets["pending"].append(task)
        else:
            task["status_label"] = "已截止未交"
            buckets["expired"].append(task)

    buckets["pending"].sort(key=lambda t: (t["no_deadline"], t["due_at"] or "9999"))
    return buckets


@router.get("")
def mp_tasks(user: dict = Depends(get_current_mp_student)):
    with get_db_connection() as conn:
        buckets = load_student_task_buckets(conn, int(user["id"]))
        conn.commit()
    return {"success": True, "data": buckets, "error": None}


def _parse_submission_answers(raw: Any) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return []
    answers = data.get("answers") if isinstance(data, dict) else None
    return answers if isinstance(answers, list) else []


def _serialize_my_submission(conn: Any, submission: Optional[dict]) -> Optional[dict[str, Any]]:
    if not submission:
        return None
    file_rows = conn.execute(
        "SELECT id, original_filename, mime_type, file_size FROM submission_files "
        "WHERE submission_id = ? ORDER BY id",
        (int(submission["id"]),),
    ).fetchall()
    files = [
        {
            "id": row["id"],
            "file_name": row["original_filename"] or f"附件{row['id']}",
            "mime_type": row["mime_type"] or "",
            "file_size": row["file_size"],
            "is_image": str(row["mime_type"] or "").startswith("image/"),
        }
        for row in file_rows
    ]
    return {
        "status": submission.get("status"),
        "score": submission.get("score"),
        "feedback_md": submission.get("feedback_md") or "",
        "submitted_at": submission.get("submitted_at"),
        "answers": _parse_submission_answers(submission.get("answers_json")),
        "files": files,
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

        group_payload = _build_group_payload(conn, assignment_id, int(user["id"]))
        submission_payload = _serialize_my_submission(conn, submission)
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
            "submission": submission_payload,
            "group": group_payload,
        },
        "error": None,
    }


def _build_group_payload(conn: Any, assignment_id: str, student_id: int) -> Optional[dict[str, Any]]:
    """小组作业载荷：展示状态 + 组员名单 + 我的互评分。

    互评红线：只回传本人打出的分，绝不外露他人互评与均分明细。
    小组信息是锦上添花，任何失败都不阻断详情页。
    """
    try:
        state = get_student_display_state(conn, assignment_id, student_id)
        if not state:
            return None
        payload: dict[str, Any] = dict(state)
        payload["peers"] = []
        payload["my_ratings"] = {}
        if state.get("in_group"):
            context = get_student_group_context(conn, assignment_id, student_id) or {}
            payload["peers"] = context.get("peers", [])
            group = context.get("group") or {}
            if group.get("id"):
                rows = conn.execute(
                    """
                    SELECT reviewee_student_id, contribution_points
                    FROM peer_reviews
                    WHERE group_id = ? AND assignment_id = ? AND reviewer_student_id = ?
                    """,
                    (int(group["id"]), str(assignment_id), student_id),
                ).fetchall()
                payload["my_ratings"] = {
                    str(row["reviewee_student_id"]): row["contribution_points"]
                    for row in rows
                    if row["contribution_points"] is not None
                }
        return payload
    except Exception as exc:
        print(f"[WECHAT_MP] 小组载荷加载失败 assignment={assignment_id}: {exc}")
        return None
