"""小程序"作业考试"列表：学生视角的作业/考试任务流.

复用 todo_service.build_classroom_todo_overview（Web 待办同源），
按 offering 聚合后只保留作业/考试类条目，分 pending / completed。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...db.connection import get_db_connection
from ...services.dashboard_service import _load_student_offerings
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
