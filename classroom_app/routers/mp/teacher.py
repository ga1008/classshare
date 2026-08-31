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
from ...services.deterministic_exam_grading import (
    _choice_set,
    build_deterministic_grading_evidence,
)
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


_QUESTION_TYPE_LABELS = {
    "radio": "单选",
    "checkbox": "多选",
    "text": "填空",
    "textarea": "问答",
}

_VERDICT_LABELS = {
    "full": "满分",
    "partial": "部分正确",
    "zero": "0分",
    "blank": "未作答",
    "doubt": "待评判",
    "manual": "人工评判",
}

_CHECKBOX_SEP = "|||"

# 与 Web 端 static/js/grading_feedback.js 同口径的逐题评语解析：
# AI/教师评语里 "### 第 N 题" 小节下的 本题得分/扣分点/评价 行。
_Q_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:第\s*)?(\d+)\s*(?:题|问|小题)?(?:\s*[：:.\-、]\s*(.*))?$", re.I
)
_Q_ALT_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:q|question)\s*\.?\s*(\d+)(?:\s*[：:.\-、]\s*(.*))?$", re.I
)
_FB_LABEL_RE = re.compile(
    r"^(本题得分|得分|score|扣分点描述|扣分点|失分点|评价|评语|evaluation)\s*[：:]\s*(.*)$",
    re.I,
)
_FB_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_question_feedback(feedback_md: str | None) -> dict[int, dict]:
    """feedback_md → {题号: {score, max_score, deduction, evaluation}}。

    只认已成结构的小节，解析不出就返回空——批阅视图会退回客观判定，
    绝不因评语格式异常丢内容。
    """
    sections: dict[int, dict] = {}
    current_no: int | None = None
    for raw_line in str(feedback_md or "").splitlines():
        stripped = raw_line.strip()
        heading = _Q_HEADING_RE.match(stripped) or _Q_ALT_HEADING_RE.match(stripped)
        if heading:
            current_no = int(heading.group(1))
            sections.setdefault(
                current_no,
                {"score": None, "max_score": None, "deduction": "", "evaluation": ""},
            )
            continue
        if current_no is None:
            continue
        normalized = _FB_BULLET_PREFIX_RE.sub("", stripped).replace("**", "").strip()
        labeled = _FB_LABEL_RE.match(normalized)
        if not labeled:
            continue
        label = labeled.group(1).lower()
        value = labeled.group(2).strip()
        section = sections[current_no]
        if label in ("本题得分", "得分", "score"):
            numbers = _NUMBER_RE.findall(value)
            section["score"] = float(numbers[0]) if numbers else None
            section["max_score"] = float(numbers[1]) if len(numbers) > 1 else None
        elif label in ("评价", "评语", "evaluation"):
            section["evaluation"] = value
        else:
            section["deduction"] = value
    return {
        no: section
        for no, section in sections.items()
        if section["score"] is not None or section["deduction"] or section["evaluation"]
    }


def _normalize_answer_entries(answer_entries: list[dict]) -> list[dict]:
    """多选答案是完整选项文本用 ||| 连接的字符串；确定性判卷的分词器
    会把长文本拆碎导致永不匹配（v0.10.1 线上 bug）。先拆成列表再喂。"""
    normalized: list[dict] = []
    for entry in answer_entries:
        item = dict(entry)
        answer = item.get("answer")
        if isinstance(answer, str) and _CHECKBOX_SEP in answer:
            item["answer"] = [part for part in answer.split(_CHECKBOX_SEP) if part.strip()]
        normalized.append(item)
    return normalized


def _format_points(value: float) -> str:
    return f"{value:g}"


def _judge_question(
    question: dict,
    answer_entry: dict | None,
    evidence_item: dict | None,
) -> tuple[str, float | None]:
    """(verdict, earned)。earned 仅在客观可确定时给出，绝不猜测主观分。

    verdict: full 满分 / partial 部分正确(多选漏选) / zero 0分(答错或含错选)
    / blank 未作答 / doubt 待评判(填空不匹配) / manual 人工评判(主观题)。
    """
    if evidence_item is not None and evidence_item.get("fixed_score") is not None:
        fixed = float(evidence_item["fixed_score"])
        max_score = float(evidence_item.get("max_score") or 0)
        if str(evidence_item.get("reason") or "") == "blank_without_attachment":
            return "blank", 0.0
        if max_score > 0 and fixed >= max_score:
            return "full", fixed
        return "zero", fixed

    qtype = str(question.get("type") or "").strip().lower()
    if qtype == "checkbox":
        options = question.get("options") or []
        expected = _choice_set(question.get("answer_text"), options)
        actual_raw = (answer_entry or {}).get("answer")
        actual = _choice_set(actual_raw, options)
        if expected and actual:
            if actual == expected:
                # 理论上 evidence 已 fixed；兜底一致性
                return "full", float(question.get("points") or 0)
            if actual.issubset(expected):
                return "partial", None
            return "zero", None
        return "manual", None
    if qtype == "text":
        return "doubt", None
    return "manual", None


def _flatten_paper_questions(questions_json: str | None) -> list[dict]:
    """exam_papers.questions_json → 顺序展开的题目列表（教师视角，含答案）。"""
    try:
        data = json.loads(questions_json or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    questions: list[dict] = []
    for page in data.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for raw in page.get("questions") or []:
            if not isinstance(raw, dict):
                continue
            text = ""
            for key in ("text", "title", "question", "content"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
            options = raw.get("options") if isinstance(raw.get("options"), list) else []
            try:
                points = float(raw.get("points") or (raw.get("grading") or {}).get("points") or 0)
            except (TypeError, ValueError):
                points = 0.0
            answer = raw.get("answer")
            if isinstance(answer, list):
                answer_text = "、".join(str(item) for item in answer if str(item).strip())
            else:
                answer_text = str(answer or "").strip()
            questions.append(
                {
                    "id": str(raw.get("id") or f"q{len(questions) + 1}"),
                    "type": str(raw.get("type") or "").strip().lower(),
                    "text": text,
                    "options": [str(opt) for opt in options],
                    "points": points,
                    "answer_text": answer_text,
                }
            )
    return questions


def _serialize_review_file(row: dict) -> dict:
    mime_type = str(row.get("mime_type") or "")
    return {
        "id": row["id"],
        "file_name": row.get("original_filename") or f"附件{row['id']}",
        "mime_type": mime_type,
        "file_size": row.get("file_size"),
        "is_image": mime_type.startswith("image/"),
    }


def build_submission_review(
    questions_json: str | None,
    answers_json: str | None,
    file_rows: list[dict],
    feedback_md: str | None = None,
) -> dict:
    """批阅页逐题视图：题干/选项/标准答案 + 学生作答 + 客观判定 + 按题附件。

    附件归属：answers_json 里作答条目内嵌的 attachments（relative_path/
    file_name）与 submission_files 按文件名匹配；匹配不上的（含 Web 端
    历史提交、普通作业整卷附件）统一归入 paper_files 兜底，绝不丢附件。
    """
    paper_questions = _flatten_paper_questions(questions_json)

    try:
        parsed = json.loads(answers_json or "{}")
    except (TypeError, ValueError):
        parsed = {}
    raw_answers = parsed.get("answers") if isinstance(parsed, dict) else None
    answer_entries = [item for item in raw_answers if isinstance(item, dict)] if isinstance(raw_answers, list) else []

    normalized_entries = _normalize_answer_entries(answer_entries)

    answers_by_id: dict[str, dict] = {}
    for idx, entry in enumerate(normalized_entries, start=1):
        key = str(entry.get("question_id") or entry.get("id") or idx).strip().casefold()
        answers_by_id.setdefault(key, entry)

    # 附件名 → question_id（answers_json 内嵌清单是唯一可靠的按题归属来源）
    attachment_owner: dict[str, str] = {}
    for entry in answer_entries:
        qid = str(entry.get("question_id") or "").strip()
        for att in entry.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            for key in ("relative_path", "file_name"):
                name = str(att.get(key) or "").strip().casefold()
                if name:
                    attachment_owner[name] = qid

    files_by_question: dict[str, list[dict]] = {}
    paper_files: list[dict] = []
    for row in file_rows:
        serialized = _serialize_review_file(row)
        owner = ""
        for key in ("relative_path", "original_filename"):
            name = str(row.get(key) or "").strip().casefold()
            if name and name in attachment_owner:
                owner = attachment_owner[name]
                break
        if owner:
            files_by_question.setdefault(owner, []).append(serialized)
        else:
            paper_files.append(serialized)

    # ||| 归一化后的答案喂确定性判卷（v0.10.1 的多选永不匹配 bug 修复点）
    evidence = build_deterministic_grading_evidence(
        questions_json, {"answers": normalized_entries}
    )
    evidence_by_id = {
        str(item.get("question_id") or "").casefold(): item
        for item in (evidence.get("questions") or [])
    }

    feedback_by_no = parse_question_feedback(feedback_md)

    questions: list[dict] = []
    if paper_questions:
        for no, question in enumerate(paper_questions, start=1):
            qid = question["id"]
            answer_entry = answers_by_id.get(qid.casefold())
            if answer_entry is None and no <= len(normalized_entries):
                answer_entry = normalized_entries[no - 1]
            raw_answer = (answer_entry or {}).get("answer")
            if isinstance(raw_answer, list):
                student_answer = "、".join(str(part) for part in raw_answer)
            else:
                student_answer = str(raw_answer or "")
            verdict, earned = _judge_question(
                question, answer_entry, evidence_by_id.get(qid.casefold())
            )
            points = float(question["points"] or 0)
            # 实际批改的逐题分（AI/教师评语解析）优先于客观预测
            feedback = feedback_by_no.get(no) or {}
            if feedback.get("score") is not None:
                earned = float(feedback["score"])
                max_ref = points or float(feedback.get("max_score") or 0)
                if max_ref and earned >= max_ref:
                    verdict = "full"
                elif earned <= 0:
                    verdict = "zero"
                else:
                    verdict = "partial"
            earned_text = _format_points(earned) if earned is not None else "—"
            questions.append(
                {
                    "no": no,
                    "question_id": qid,
                    "type": question["type"],
                    "type_label": _QUESTION_TYPE_LABELS.get(question["type"], "题目"),
                    "text": question["text"],
                    "options": question["options"],
                    "points": question["points"],
                    "standard_answer": question["answer_text"],
                    "student_answer": student_answer,
                    "verdict": verdict,
                    "verdict_label": _VERDICT_LABELS.get(verdict, verdict),
                    "earned": earned,
                    "score_display": f"{earned_text}/{_format_points(points)}" if points else "",
                    "deduction": str(feedback.get("deduction") or ""),
                    "evaluation": str(feedback.get("evaluation") or ""),
                    "attachments": files_by_question.get(qid, []),
                }
            )
    else:
        # 普通作业 / 无试卷：作答条目直接成题（一律人工评判）
        for no, entry in enumerate(answer_entries, start=1):
            qid = str(entry.get("question_id") or "").strip()
            questions.append(
                {
                    "no": no,
                    "question_id": qid or f"a{no}",
                    "type": "textarea",
                    "type_label": "作答",
                    "text": str(entry.get("question") or f"作答 {no}"),
                    "options": [],
                    "points": 0,
                    "standard_answer": "",
                    "student_answer": str(entry.get("answer") or ""),
                    "verdict": "manual",
                    "verdict_label": _VERDICT_LABELS["manual"],
                    "earned": None,
                    "score_display": "",
                    "deduction": "",
                    "evaluation": "",
                    "attachments": files_by_question.get(qid, []) if qid else [],
                }
            )

    total_points = sum(float(q["points"]) for q in questions)
    return {
        "questions": questions,
        "paper_files": paper_files,
        "total_points": total_points,
    }


def _get_teacher_assignment(conn, assignment_id: int, teacher_id: int) -> dict:
    row = conn.execute(
        """
        SELECT a.id, a.title, a.status, a.due_at, a.exam_paper_id,
               o.id AS offering_id, o.class_id,
               c.name AS course_name, cl.name AS class_name
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


@router.post("/assignment/{assignment_id}/nudge")
def mp_teacher_nudge(assignment_id: int, user: dict = Depends(get_current_mp_teacher)):
    """一键催交：给未提交学生发"作业催交通知"订阅消息。

    额度制（学生须先在小程序里允许过该模板）；同一作业同一学生每天
    最多推一次（dedupe 按日）。缺交记零占位视为未提交。
    """
    from datetime import date

    from ...services.wechat_mp_subscribe_service import (
        build_nudge_values,
        send_subscribe_message,
    )

    teacher_id = int(user["id"])
    with get_db_connection() as conn:
        assignment = _get_teacher_assignment(conn, assignment_id, teacher_id)
        rows = conn.execute(
            """
            SELECT s.id
            FROM students s
            WHERE (
                s.class_id = ?
                OR EXISTS (
                    SELECT 1 FROM class_offering_class_links cocl
                    WHERE cocl.offering_id = ? AND cocl.class_id = s.class_id
                )
            )
              AND COALESCE(s.enrollment_status, 'active') = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM submissions sub
                  WHERE sub.assignment_id = ?
                    AND sub.student_pk_id = s.id
                    AND COALESCE(sub.is_absence_score, 0) = 0
              )
            """,
            (assignment["class_id"], assignment["offering_id"], assignment_id),
        ).fetchall()

        values = build_nudge_values(
            assignment["title"], assignment.get("course_name"), assignment.get("due_at")
        )
        today = date.today().isoformat()
        stats = {"total_unsubmitted": len(rows), "pushed": 0, "no_grant": 0, "skipped": 0}
        for row in rows:
            student_id = int(row["id"])
            status = send_subscribe_message(
                conn,
                user_role="student",
                user_pk=student_id,
                template_key="nudge",
                values=values,
                page="pages/tasks/index",
                dedupe_key=f"nudge:{assignment_id}:{student_id}:{today}",
            )
            if status == "sent":
                stats["pushed"] += 1
            elif status == "no_grant":
                stats["no_grant"] += 1
            else:
                stats["skipped"] += 1
        conn.commit()
    return {"success": True, "data": stats, "error": None}


@router.get("/submission/{submission_id}/review")
def mp_teacher_submission_review(
    submission_id: int, user: dict = Depends(get_current_mp_teacher)
):
    """批阅页逐题视图聚合：题目/标准答案/学生作答/客观判定/按题附件。

    只读投影。打分仍走既有 POST /api/submissions/{id}/grade（迟交罚分、
    AI job 冲正、修订台账、小组分联动全在该端点内，绝不在 mp 侧复制）。
    """
    teacher_id = int(user["id"])
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.status, s.score, s.feedback_md, s.submitted_at,
                   s.answers_json,
                   COALESCE(s.is_late_submission, 0) AS is_late_submission,
                   COALESCE(s.is_absence_score, 0) AS is_absence_score,
                   COALESCE(s.resubmission_allowed, 0) AS resubmission_allowed,
                   s.score_before_late_penalty,
                   COALESCE(s.late_penalty_points, 0) AS late_penalty_points,
                   a.id AS assignment_id, a.title AS assignment_title, a.exam_paper_id,
                   c.name AS course_name, cl.name AS class_name,
                   st.name AS student_name, st.student_id_number
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN students st ON st.id = s.student_pk_id
            WHERE s.id = ? AND o.teacher_id = ?
            """,
            (int(submission_id), teacher_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="提交不存在或无权查看")
        submission = dict(row)

        questions_json = None
        if submission.get("exam_paper_id"):
            paper_row = conn.execute(
                "SELECT questions_json FROM exam_papers WHERE id = ?",
                (submission["exam_paper_id"],),
            ).fetchone()
            if paper_row:
                questions_json = paper_row["questions_json"]

        file_rows = [
            dict(item)
            for item in conn.execute(
                """
                SELECT id, original_filename, relative_path, mime_type, file_size
                FROM submission_files
                WHERE submission_id = ?
                ORDER BY id
                """,
                (int(submission_id),),
            ).fetchall()
        ]
        conn.commit()

    review = build_submission_review(
        questions_json,
        submission.get("answers_json"),
        file_rows,
        feedback_md=submission.get("feedback_md"),
    )
    status = str(submission.get("status") or "")
    return {
        "success": True,
        "data": {
            "assignment": {
                "id": submission["assignment_id"],
                "title": submission.get("assignment_title") or "",
                "is_exam": bool(submission.get("exam_paper_id")),
                "course_name": submission.get("course_name") or "",
                "class_name": submission.get("class_name") or "",
            },
            "student": {
                "name": submission.get("student_name") or "",
                "student_id_number": submission.get("student_id_number") or "",
            },
            "submission": {
                "id": submission["id"],
                "status": status,
                "status_label": _SUBMISSION_STATUS_LABELS.get(status, status),
                "score": submission.get("score"),
                "score_before_late_penalty": submission.get("score_before_late_penalty"),
                "late_penalty_points": submission.get("late_penalty_points"),
                "is_late": bool(int(submission.get("is_late_submission") or 0)),
                "is_absence_zero": bool(int(submission.get("is_absence_score") or 0)),
                "resubmission_allowed": bool(int(submission.get("resubmission_allowed") or 0)),
                "submitted_at": submission.get("submitted_at") or "",
                "feedback_md": submission.get("feedback_md") or "",
                "feedback_blocks": _parse_feedback_blocks(submission.get("feedback_md")),
            },
            **review,
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
