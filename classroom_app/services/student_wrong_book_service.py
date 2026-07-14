"""学生个人错题本。

把教师端错题归集（``wrong_question_summary_service``，按作业聚合全班错答）
的解析机器反过来用：按**学生**跨课程聚合他本人做错的题，并统计知识点掌握度。
只读已批改的考试型提交（answers_json + feedback_md），不新增任何写入。
"""

from __future__ import annotations

import json
from typing import Any

from . import wrong_question_summary_service as wq

# 单个学生的错题上限，防止极端账号把页面撑爆。
MAX_WRONG_ITEMS = 300

MASTERY_TIERS: tuple[tuple[int, str, str], ...] = (
    (85, "掌握牢固", "solid"),
    (70, "基本掌握", "good"),
    (50, "还需巩固", "weak"),
    (0, "急需补强", "danger"),
)


def _mastery_tier(percent: int) -> tuple[str, str]:
    for threshold, label, tone in MASTERY_TIERS:
        if percent >= threshold:
            return label, tone
    return MASTERY_TIERS[-1][1], MASTERY_TIERS[-1][2]


def _load_graded_exam_submissions(conn, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id AS submission_id, s.assignment_id, s.answers_json, s.feedback_md,
               s.score AS submission_score, s.submitted_at, s.student_pk_id, s.student_name,
               a.title AS assignment_title, a.exam_paper_id, a.class_offering_id,
               a.course_id, c.name AS course_name,
               ep.questions_json
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        JOIN exam_papers ep ON ep.id = a.exam_paper_id
        WHERE s.student_pk_id = ?
          AND s.status = 'graded'
          AND COALESCE(s.is_absence_score, 0) = 0
        ORDER BY s.submitted_at DESC, s.id DESC
        """,
        (int(student_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_exam_questions(questions_json: Any) -> list[dict[str, Any]]:
    try:
        exam_data = json.loads(str(questions_json or "{}"))
    except (TypeError, ValueError):
        return []
    if not isinstance(exam_data, dict):
        return []
    return wq._extract_exam_questions(exam_data)


def _evaluate_question(
    question: dict[str, Any],
    answers: dict[str, Any],
    feedback_scores: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """返回 {is_wrong, score, max_score, raw_answer}；无法判定的题返回 None。"""
    answer_record = wq._get_answer_record(answers, question)
    raw_answer = wq._answer_value(answer_record)
    score_record = wq._score_record_for_question(question, answer_record, feedback_scores)
    score = wq._coerce_float(score_record.get("score"))
    full_score = wq._question_full_score(question)
    max_score = wq._coerce_float(score_record.get("max_score") or full_score)
    if max_score is not None and max_score <= 0:
        max_score = None

    if question["type"] in wq.CHOICE_QUESTION_TYPES:
        is_wrong = not wq._choice_answer_matches_correct(question, raw_answer)
        if max_score is not None:
            score = 0.0 if is_wrong else max_score
        return {
            "is_wrong": is_wrong,
            "score": score,
            "max_score": max_score,
            "raw_answer": raw_answer,
        }

    if score is None or max_score is None:
        return None
    return {
        "is_wrong": wq._is_not_full_score(score, max_score),
        "score": score,
        "max_score": max_score,
        "raw_answer": raw_answer,
    }


def build_student_wrong_book(conn, *, student_id: int) -> dict[str, Any]:
    submissions = _load_graded_exam_submissions(conn, student_id)

    items: list[dict[str, Any]] = []
    knowledge_stats: dict[str, dict[str, int]] = {}
    course_index: dict[int, dict[str, Any]] = {}
    evaluated_total = 0

    for submission in submissions:
        questions = _parse_exam_questions(submission.get("questions_json"))
        if not questions:
            continue
        answers = wq._answers_by_question(submission.get("answers_json"))
        feedback_scores = wq._feedback_scores_by_question(submission.get("feedback_md"))
        course_id = int(submission["course_id"])
        course_entry = course_index.setdefault(
            course_id,
            {"course_id": course_id, "course_name": str(submission["course_name"] or "课程"), "wrong_count": 0},
        )

        for question in questions:
            evaluation = _evaluate_question(question, answers, feedback_scores)
            if evaluation is None:
                continue
            evaluated_total += 1

            for point in question.get("knowledge_points") or []:
                stat = knowledge_stats.setdefault(point, {"total": 0, "wrong": 0})
                stat["total"] += 1
                if evaluation["is_wrong"]:
                    stat["wrong"] += 1

            if not evaluation["is_wrong"]:
                continue
            course_entry["wrong_count"] += 1
            if len(items) >= MAX_WRONG_ITEMS:
                continue
            detail = wq._answer_detail_record(
                submission,
                question,
                evaluation["raw_answer"],
                score=evaluation["score"],
                max_score=evaluation["max_score"],
            )
            items.append(
                {
                    "assignment_id": submission["assignment_id"],
                    "assignment_title": str(submission["assignment_title"] or "考试"),
                    "course_id": course_id,
                    "course_name": course_entry["course_name"],
                    "submitted_at": str(submission["submitted_at"] or ""),
                    "question_ordinal": question["ordinal"],
                    "question_type": question["type"],
                    "question_type_label": question["type_label"],
                    "question_text": question["text"],
                    "options": question.get("options") or [],
                    "correct_answer": str(question.get("answer_text") or ""),
                    "my_answer": str(detail.get("answer") or "（未作答）"),
                    "score": evaluation["score"],
                    "max_score": evaluation["max_score"],
                    "knowledge_points": question.get("knowledge_points") or [],
                    "link_url": f"/assignment/{submission['assignment_id']}",
                }
            )

    knowledge_mastery = []
    for point, stat in knowledge_stats.items():
        if stat["total"] <= 0:
            continue
        percent = round((stat["total"] - stat["wrong"]) * 100 / stat["total"])
        label, tone = _mastery_tier(percent)
        knowledge_mastery.append(
            {
                "name": point,
                "total": stat["total"],
                "wrong": stat["wrong"],
                "mastery_percent": percent,
                "tier_label": label,
                "tier_tone": tone,
            }
        )
    # 先按掌握度升序（薄弱在前），再按样本量降序让高频考点优先展示。
    knowledge_mastery.sort(key=lambda item: (item["mastery_percent"], -item["total"]))

    courses = sorted(course_index.values(), key=lambda item: -item["wrong_count"])
    wrong_total = sum(course["wrong_count"] for course in courses)
    return {
        "items": items,
        "items_truncated": wrong_total > len(items),
        "courses": courses,
        "knowledge_mastery": knowledge_mastery,
        "summary": {
            "wrong_total": wrong_total,
            "evaluated_total": evaluated_total,
            "exam_count": len(submissions),
            "correct_percent": (
                round((evaluated_total - wrong_total) * 100 / evaluated_total)
                if evaluated_total
                else None
            ),
            "weakest_points": [item["name"] for item in knowledge_mastery[:3] if item["mastery_percent"] < 85],
        },
    }
