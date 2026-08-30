"""小程序教师端：作业/考试任务列表（带提交进度计数）。

进度详情与批阅动作直接复用既有 Web API（bearer 直通）：
- GET  /api/assignments/{id}/submissions   （统计 + 含未交名单 + 答案）
- POST /api/submissions/{id}/grade          （打分，迟交策略自动生效）
- POST /api/assignments/{id}/submissions/batch-grade（AI 批量批改）
- POST /api/assignments/{id}/submissions/zero-unsubmitted（缺交记零）
本文件只补"跨课堂任务列表"这一个小程序专属聚合。
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException

from ...db.connection import get_db_connection
from ...services.submission_preview_service import ensure_submission_file_access
from .deps import get_current_mp_teacher

router = APIRouter(prefix="/teacher")

TASK_LIMIT = 100

_STATUS_LABELS = {"new": "草稿", "published": "进行中", "closed": "已截止"}

_SUBMISSION_STATUS_LABELS = {
    "submitted": "待批改",
    "grading": "AI批改中",
    "grading_review": "待确认",
    "graded": "已批改",
    "unsubmitted": "未提交",
}

_INLINE_MD_RE = re.compile(r"\*\*(.+?)\*\*|`(.+?)`")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _strip_inline_md(text: str) -> str:
    """去掉行内 **加粗** / `代码` 标记，保留内容本身。"""
    return _INLINE_MD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)


def _parse_feedback_blocks(feedback_md: str | None) -> list[dict]:
    """把 AI/教师评语的 markdown 解析成结构化块，前端按块排版。

    只覆盖平台批改评语实际会出现的语法（标题/列表/段落/加粗），
    未知语法一律退化为普通段落，绝不丢内容。
    """
    if not feedback_md:
        return []
    text = _HTML_COMMENT_RE.sub("", feedback_md)
    blocks: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = min(len(heading.group(1)), 3)
            blocks.append({"type": f"h{level}", "text": _strip_inline_md(heading.group(2))})
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if bullet:
            blocks.append({"type": "li", "text": _strip_inline_md(bullet.group(1))})
            continue
        is_strong = bool(re.fullmatch(r"\*\*.+\*\*", line))
        blocks.append({"type": "strong" if is_strong else "p", "text": _strip_inline_md(line)})
    return blocks


def _parse_answers(answers_json: str | None) -> list[dict]:
    """answers_json → [{question, answer}]，与 Web exam_take 存储格式同构。"""
    if not answers_json:
        return []
    try:
        parsed = json.loads(answers_json)
    except (ValueError, TypeError):
        return []
    answers = parsed.get("answers") if isinstance(parsed, dict) else None
    if not isinstance(answers, list):
        return []
    items: list[dict] = []
    for entry in answers:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "question": str(entry.get("question") or ""),
                "answer": str(entry.get("answer") or ""),
            }
        )
    return items


def _get_teacher_assignment(conn, assignment_id: int, teacher_id: int) -> dict:
    row = conn.execute(
        """
        SELECT a.id, a.title, a.status, a.due_at, a.exam_paper_id,
               o.class_id, c.name AS course_name, cl.name AS class_name
        FROM assignments a
        JOIN class_offerings o ON o.id = a.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE a.id = ? AND o.teacher_id = ?
        """,
        (assignment_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在或无权查看")
    return dict(row)


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
                     WHERE (s.class_id = o.class_id OR EXISTS (SELECT 1 FROM class_offering_class_links cocl_m WHERE cocl_m.offering_id = o.id AND cocl_m.class_id = s.class_id))
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


@router.get("/assignment/{assignment_id}/grading")
def mp_teacher_grading(assignment_id: int, user: dict = Depends(get_current_mp_teacher)):
    """批阅队列聚合：统计 + 按学号排序的全名单（含作答/评语块/附件清单）。

    一次请求供进度页与批阅页共用；作答与评语的 markdown 解析放在
    服务端完成，前端只做排版。附件权限按任务级校验（教师已确认
    拥有该任务，无需逐文件复查）。
    """
    teacher_id = int(user["id"])
    with get_db_connection() as conn:
        assignment = _get_teacher_assignment(conn, assignment_id, teacher_id)

        roster = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, student_id_number, name
                FROM students
                WHERE class_id = ?
                  AND COALESCE(enrollment_status, 'active') = 'active'
                ORDER BY student_id_number
                """,
                (assignment["class_id"],),
            ).fetchall()
        ]

        submissions = {
            int(row["student_pk_id"]): dict(row)
            for row in conn.execute(
                """
                SELECT s.id, s.student_pk_id, s.status, s.score, s.feedback_md,
                       s.submitted_at, s.answers_json,
                       COALESCE(s.is_late_submission, 0) AS is_late_submission,
                       COALESCE(s.is_absence_score, 0) AS is_absence_score,
                       COALESCE(s.resubmission_allowed, 0) AS resubmission_allowed
                FROM submissions s
                WHERE s.assignment_id = ?
                """,
                (assignment_id,),
            ).fetchall()
        }

        file_rows = conn.execute(
            """
            SELECT sf.id, sf.submission_id, sf.original_filename, sf.mime_type, sf.file_size
            FROM submission_files sf
            JOIN submissions s ON s.id = sf.submission_id
            WHERE s.assignment_id = ?
            ORDER BY sf.submission_id, sf.id
            """,
            (assignment_id,),
        ).fetchall()
        conn.commit()

    files_by_submission: dict[int, list[dict]] = {}
    for row in file_rows:
        item = dict(row)
        mime_type = str(item.get("mime_type") or "")
        files_by_submission.setdefault(int(item["submission_id"]), []).append(
            {
                "id": item["id"],
                "file_name": item.get("original_filename") or f"附件{item['id']}",
                "mime_type": mime_type,
                "file_size": item.get("file_size"),
                "is_image": mime_type.startswith("image/"),
            }
        )

    # 花名册为空时退回只显示已提交学生（与 Web 端一致）
    if not roster:
        roster = [
            {
                "id": sub["student_pk_id"],
                "student_id_number": "",
                "name": f"学生{sub['student_pk_id']}",
            }
            for sub in sorted(submissions.values(), key=lambda s: str(s.get("submitted_at") or ""))
        ]

    entries = []
    for student in roster:
        sub = submissions.get(int(student["id"]))
        status = str(sub["status"]) if sub else "unsubmitted"
        # 缺交记零占位（is_absence_score）在名单里仍归为未提交
        if sub and int(sub.get("is_absence_score") or 0):
            status = "unsubmitted"
        entry = {
            "submission_id": sub["id"] if sub else None,
            "student_pk_id": student["id"],
            "student_name": student["name"],
            "student_id_number": student["student_id_number"],
            "status": status,
            "status_label": _SUBMISSION_STATUS_LABELS.get(status, status),
            "score": sub.get("score") if sub else None,
            "submitted_at": (sub.get("submitted_at") or "") if sub else "",
            "is_late": bool(int(sub.get("is_late_submission") or 0)) if sub else False,
            "is_absence_zero": bool(int(sub.get("is_absence_score") or 0)) if sub else False,
            "answers": _parse_answers(sub.get("answers_json")) if sub else [],
            "feedback_md": (sub.get("feedback_md") or "") if sub else "",
            "feedback_blocks": _parse_feedback_blocks(sub.get("feedback_md")) if sub else [],
            "files": files_by_submission.get(int(sub["id"]), []) if sub and sub.get("id") else [],
        }
        entries.append(entry)

    submitted = [e for e in entries if e["status"] != "unsubmitted"]
    graded = [e for e in submitted if e["status"] == "graded" and e["score"] is not None]
    pending = [e for e in submitted if e["status"] == "submitted"]
    scores = [float(e["score"]) for e in graded]
    average = round(sum(scores) / len(scores), 1) if scores else 0

    return {
        "success": True,
        "data": {
            "assignment": {
                "id": assignment["id"],
                "title": assignment["title"],
                "is_exam": bool(assignment.get("exam_paper_id")),
                "course_name": assignment.get("course_name") or "",
                "class_name": assignment.get("class_name") or "",
            },
            "stats": {
                "total_students": len(roster) or len(submitted),
                "submitted_count": len(submitted),
                "graded_count": len(graded),
                "pending_grade_count": len(pending),
                "average_score": average,
            },
            "entries": entries,
        },
        "error": None,
    }


@router.get("/submission/{submission_id}/files")
def mp_teacher_submission_files(
    submission_id: int, user: dict = Depends(get_current_mp_teacher)
):
    """批阅面板的附件清单。逐文件复用 ensure_submission_file_access
    （教师需对该作业有管理权），下载走既有 /submissions/download/{id}。"""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM submission_files WHERE submission_id = ? ORDER BY id",
            (int(submission_id),),
        ).fetchall()
        files = []
        for row in rows:
            try:
                info = ensure_submission_file_access(conn, int(row["id"]), user)
            except HTTPException:
                raise
            mime_type = str(info.get("mime_type") or "")
            files.append(
                {
                    "id": info["id"],
                    "file_name": info.get("original_filename") or f"附件{info['id']}",
                    "mime_type": mime_type,
                    "file_size": info.get("file_size"),
                    "is_image": mime_type.startswith("image/"),
                }
            )
        conn.commit()
    return {"success": True, "data": {"files": files}, "error": None}
