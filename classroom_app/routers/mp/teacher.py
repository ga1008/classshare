"""小程序教师端：作业/考试任务列表（带提交进度计数）。

进度详情与批阅动作直接复用既有 Web API（bearer 直通）：
- GET  /api/assignments/{id}/submissions   （统计 + 含未交名单 + 答案）
- POST /api/submissions/{id}/grade          （打分，迟交策略自动生效）
- POST /api/assignments/{id}/submissions/batch-grade（AI 批量批改）
- POST /api/assignments/{id}/submissions/zero-unsubmitted（缺交记零）
本文件只补"跨课堂任务列表"这一个小程序专属聚合。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...db.connection import get_db_connection
from .deps import get_current_mp_teacher

router = APIRouter(prefix="/teacher")

TASK_LIMIT = 100

_STATUS_LABELS = {"new": "草稿", "published": "进行中", "closed": "已截止"}


@router.get("/tasks")
def mp_teacher_tasks(user: dict = Depends(get_current_mp_teacher)):
    """教师名下（按授课 offering）的作业/考试列表 + 提交进度计数.

    仅覆盖绑定了 class_offering 的作业（当前发布流程默认绑定；
    课程级未绑定课堂的历史作业请在网页端查看）。个人破境试炼按
    全平台惯例排除。
    """
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.status, a.due_at, a.exam_paper_id, a.created_at,
                   o.id AS offering_id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   (SELECT COUNT(*) FROM students s
                     WHERE s.class_id = o.class_id
                       AND COALESCE(s.enrollment_status, 'active') = 'active') AS student_total,
                   (SELECT COUNT(*) FROM submissions sub
                     WHERE sub.assignment_id = a.id
                       AND COALESCE(sub.is_absence_score, 0) = 0) AS submitted_count,
                   (SELECT COUNT(*) FROM submissions sub
                     WHERE sub.assignment_id = a.id
                       AND sub.status = 'graded') AS graded_count,
                   (SELECT COUNT(*) FROM submissions sub
                     WHERE sub.assignment_id = a.id
                       AND sub.status = 'submitted'
                       AND COALESCE(sub.resubmission_allowed, 0) = 0
                       AND COALESCE(sub.is_absence_score, 0) = 0) AS pending_grade_count
            FROM assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            WHERE o.teacher_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM learning_stage_exam_attempts lsea
                  WHERE lsea.assignment_id = a.id
              )
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (int(user["id"]), TASK_LIMIT),
        ).fetchall()
        conn.commit()

    tasks = []
    for row in rows:
        item = dict(row)
        status = str(item.get("status") or "")
        tasks.append(
            {
                "id": item["id"],
                "title": item["title"],
                "status": status,
                "status_label": _STATUS_LABELS.get(status, status),
                "is_exam": bool(item.get("exam_paper_id")),
                "due_at": item.get("due_at") or "",
                "course_name": item.get("course_name") or "",
                "class_name": item.get("class_name") or "",
                "student_total": int(item.get("student_total") or 0),
                "submitted_count": int(item.get("submitted_count") or 0),
                "graded_count": int(item.get("graded_count") or 0),
                "pending_grade_count": int(item.get("pending_grade_count") or 0),
            }
        )
    return {"success": True, "data": {"tasks": tasks}, "error": None}
