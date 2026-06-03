from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..core import ai_client
from ..database import get_db_connection
from .assignment_lifecycle_service import close_overdue_assignments, refresh_assignment_runtime_status


PROMPT_VERSION = "wrong-question-summary-v2"
TEXT_QUESTION_TYPES = {"text", "textarea"}
CHOICE_QUESTION_TYPES = {"radio", "checkbox"}
WRONG_SUMMARY_JOB_STATUS_QUEUED = "queued"
WRONG_SUMMARY_JOB_STATUS_RUNNING = "running"
WRONG_SUMMARY_JOB_STATUS_COMPLETED = "completed"
WRONG_SUMMARY_JOB_STATUS_FAILED = "failed"
ACTIVE_WRONG_SUMMARY_JOB_STATUSES = {WRONG_SUMMARY_JOB_STATUS_QUEUED, WRONG_SUMMARY_JOB_STATUS_RUNNING}
STALE_WRONG_SUMMARY_JOB_MINUTES = 30

_active_wrong_summary_jobs: set[str] = set()

QUESTION_TYPE_LABELS = {
    "radio": "单选题",
    "checkbox": "多选题",
    "text": "填空题",
    "textarea": "问答题",
}


def ensure_wrong_summary_cache_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assignment_wrong_answer_ai_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id TEXT NOT NULL,
            question_key TEXT NOT NULL,
            answer_signature TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (assignment_id, question_key, answer_signature, prompt_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_paper_difficulty_ai_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_paper_id TEXT NOT NULL,
            questions_signature TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (exam_paper_id, questions_signature, prompt_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assignment_wrong_summary_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id TEXT NOT NULL,
            teacher_id INTEGER NOT NULL,
            questions_signature TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            pending_text_questions INTEGER NOT NULL DEFAULT 0,
            pending_difficulty INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (assignment_id, questions_signature, prompt_version)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wrong_answer_ai_cache_assignment "
        "ON assignment_wrong_answer_ai_cache (assignment_id, question_key, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exam_difficulty_ai_cache_paper "
        "ON exam_paper_difficulty_ai_cache (exam_paper_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wrong_summary_jobs_assignment "
        "ON assignment_wrong_summary_jobs (assignment_id, questions_signature, status, updated_at DESC)"
    )


async def build_assignment_wrong_question_summary(
    assignment_id: str,
    teacher_id: int,
    *,
    ai_mode: str = "sync",
    schedule_ai: bool = False,
) -> dict[str, Any]:
    source = _load_summary_source(assignment_id, teacher_id)
    if source.get("unsupported_reason"):
        source["ai_status"] = _empty_ai_status()
        return source

    questions = source["questions"]
    submissions = source["submissions"]
    question_stats = _build_question_error_stats(questions, submissions)

    allow_ai_generation = ai_mode == "sync"
    await _attach_text_answer_clusters(
        str(source["assignment"]["id"]),
        question_stats,
        allow_generate=allow_ai_generation,
    )
    wrong_questions = [item for item in question_stats if item["wrong_count"] > 0]
    hard_questions = _build_score_based_hard_questions(wrong_questions)
    difficulty = _score_based_difficulty_summary(hard_questions)
    worst_wrong_count = wrong_questions[0]["wrong_count"] if wrong_questions else 0
    source["stats"].update(
        {
            "question_count": len(questions),
            "wrong_question_count": len(wrong_questions),
            "worst_wrong_count": worst_wrong_count,
            "correct_question_count": max(0, len(questions) - len(wrong_questions)),
        }
    )
    source["wrong_questions"] = wrong_questions
    source["hard_questions"] = hard_questions
    source["difficulty"] = difficulty
    source["ai_status"] = _build_ai_status(source, question_stats, difficulty)
    if schedule_ai and source["ai_status"]["needs_ai"]:
        _ensure_wrong_summary_ai_job(source, question_stats, teacher_id, difficulty)
        source["ai_status"] = _build_ai_status(source, question_stats, difficulty)
    return source


async def run_assignment_wrong_summary_ai_job(
    assignment_id: str,
    teacher_id: int,
    *,
    questions_signature: str | None = None,
) -> None:
    assignment_key = str(assignment_id)
    job_key = _wrong_summary_job_key(assignment_key, questions_signature or "")
    try:
        source = _load_summary_source(assignment_id, teacher_id)
        if source.get("unsupported_reason"):
            return
        if questions_signature and source.get("questions_signature") != questions_signature:
            return

        assignment_key = str(source["assignment"]["id"])
        job_key = _wrong_summary_job_key(assignment_key, source["questions_signature"])
        _mark_wrong_summary_job_running(assignment_key, source["questions_signature"])
        question_stats = _build_question_error_stats(source["questions"], source["submissions"])
        await _attach_text_answer_clusters(assignment_key, question_stats, allow_generate=True)
        difficulty = _score_based_difficulty_summary(_build_score_based_hard_questions(question_stats))
        errors = _collect_ai_job_errors(question_stats, difficulty)
        if errors:
            _mark_wrong_summary_job_failed(assignment_key, source["questions_signature"], "；".join(errors))
        else:
            _mark_wrong_summary_job_completed(assignment_key, source["questions_signature"])
    except Exception as exc:
        signature = questions_signature or (source.get("questions_signature") if "source" in locals() else "")
        _mark_wrong_summary_job_failed(assignment_key, str(signature or ""), _clip_text(str(exc), 260))
    finally:
        _active_wrong_summary_jobs.discard(job_key)


def _load_summary_source(assignment_id: str, teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        close_overdue_assignments(conn)
        assignment_row = conn.execute(
            """
            SELECT a.*,
                   c.name AS course_name,
                   c.created_by_teacher_id,
                   o.teacher_id AS offering_teacher_id,
                   o.class_id AS offering_class_id,
                   o.id AS offering_id,
                   cl.name AS class_name,
                   lsea.id AS personal_stage_attempt_id
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            LEFT JOIN classes cl ON cl.id = o.class_id
            LEFT JOIN learning_stage_exam_attempts lsea ON lsea.assignment_id = a.id
            WHERE a.id = ?
            LIMIT 1
            """,
            (assignment_id,),
        ).fetchone()
        if not assignment_row:
            raise HTTPException(404, "作业不存在")

        assignment = refresh_assignment_runtime_status(conn, assignment_row)
        if int(assignment.get("created_by_teacher_id") or 0) != int(teacher_id) and int(
            assignment.get("offering_teacher_id") or 0
        ) != int(teacher_id):
            raise HTTPException(403, "无权查看该作业的错题归集")
        if assignment.get("personal_stage_attempt_id") is not None:
            raise HTTPException(404, "学生个人试炼不进入教师错题归集")

        assignment = dict(assignment)
        base = {
            "assignment": assignment,
            "paper": None,
            "questions": [],
            "wrong_questions": [],
            "hard_questions": [],
            "difficulty": _empty_difficulty_summary("仅试卷型任务支持难题归集。"),
            "stats": {
                "total_students": _count_assignment_students(conn, assignment),
                "submitted_count": 0,
                "unsubmitted_count": 0,
                "question_count": 0,
                "wrong_question_count": 0,
                "correct_question_count": 0,
                "worst_wrong_count": 0,
            },
            "unsupported_reason": None,
        }

        if not assignment.get("exam_paper_id"):
            base["unsupported_reason"] = "当前任务不是试卷库发布的考试，暂时没有结构化题目可归集。"
            conn.commit()
            return base

        paper_row = conn.execute(
            "SELECT * FROM exam_papers WHERE id = ? LIMIT 1",
            (assignment["exam_paper_id"],),
        ).fetchone()
        if not paper_row:
            base["unsupported_reason"] = "未找到该任务绑定的试卷。"
            conn.commit()
            return base

        paper = dict(paper_row)
        exam_data = _load_json_object(paper.get("questions_json"))
        questions = _extract_exam_questions(exam_data)
        questions_signature = _signature(
            {
                "paper_id": paper.get("id"),
                "questions": [
                    {
                        "id": item["id"],
                        "type": item["type"],
                        "text": item["text"],
                        "options": item["options"],
                        "answer": item["answer"],
                        "points": item["points"],
                    }
                    for item in questions
                ],
            }
        )

        submissions = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, assignment_id, student_pk_id, student_name, status, score,
                       answers_json, feedback_md, submitted_at, is_absence_score
                FROM submissions
                WHERE assignment_id = ?
                ORDER BY submitted_at DESC, id DESC
                """,
                (assignment_id,),
            )
        ]
        submitted_count = len([item for item in submissions if not _is_absence_or_unsubmitted(item)])
        total_students = base["stats"]["total_students"] or submitted_count
        base.update(
            {
                "paper": paper,
                "questions": questions,
                "submissions": submissions,
                "questions_signature": questions_signature,
                "stats": {
                    **base["stats"],
                    "total_students": total_students,
                    "submitted_count": submitted_count,
                    "unsubmitted_count": max(0, total_students - submitted_count),
                    "question_count": len(questions),
                },
            }
        )
        conn.commit()
        return base


def _count_assignment_students(conn, assignment: dict[str, Any]) -> int:
    class_id = assignment.get("offering_class_id")
    if not class_id and assignment.get("class_offering_id"):
        offering = conn.execute(
            "SELECT class_id FROM class_offerings WHERE id = ?",
            (assignment["class_offering_id"],),
        ).fetchone()
        class_id = offering["class_id"] if offering else None
    if not class_id:
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM students
        WHERE class_id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        """,
        (class_id,),
    ).fetchone()
    return int(row["total"] if row else 0)


def _ensure_wrong_summary_ai_job(
    source: dict[str, Any],
    question_stats: list[dict[str, Any]],
    teacher_id: int,
    difficulty: dict[str, Any],
) -> dict[str, Any] | None:
    assignment_id = str(source["assignment"]["id"])
    questions_signature = str(source.get("questions_signature") or "")
    if not questions_signature:
        return None

    pending_text_questions = _pending_text_question_count(question_stats)
    pending_difficulty = 0
    if pending_text_questions <= 0 and pending_difficulty <= 0:
        return None

    now = datetime.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        _recover_stale_wrong_summary_jobs(conn)
        existing = conn.execute(
            """
            SELECT *
            FROM assignment_wrong_summary_jobs
            WHERE assignment_id = ?
              AND questions_signature = ?
              AND prompt_version = ?
            LIMIT 1
            """,
            (assignment_id, questions_signature, PROMPT_VERSION),
        ).fetchone()
        if existing and str(existing["status"] or "") in ACTIVE_WRONG_SUMMARY_JOB_STATUSES:
            row = existing
        else:
            conn.execute(
                """
                INSERT INTO assignment_wrong_summary_jobs (
                    assignment_id, teacher_id, questions_signature, prompt_version,
                    status, pending_text_questions, pending_difficulty,
                    error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                ON CONFLICT(assignment_id, questions_signature, prompt_version)
                DO UPDATE SET
                    teacher_id = excluded.teacher_id,
                    status = excluded.status,
                    pending_text_questions = excluded.pending_text_questions,
                    pending_difficulty = excluded.pending_difficulty,
                    error_message = '',
                    started_at = NULL,
                    completed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    assignment_id,
                    int(teacher_id),
                    questions_signature,
                    PROMPT_VERSION,
                    WRONG_SUMMARY_JOB_STATUS_QUEUED,
                    pending_text_questions,
                    pending_difficulty,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT *
                FROM assignment_wrong_summary_jobs
                WHERE assignment_id = ?
                  AND questions_signature = ?
                  AND prompt_version = ?
                LIMIT 1
                """,
                (assignment_id, questions_signature, PROMPT_VERSION),
            ).fetchone()
        conn.commit()

    serialized = _serialize_wrong_summary_job(row) if row else None
    if serialized and serialized["status"] in ACTIVE_WRONG_SUMMARY_JOB_STATUSES:
        _schedule_wrong_summary_ai_task(assignment_id, int(teacher_id), questions_signature)
    return serialized


def _build_ai_status(
    source: dict[str, Any],
    question_stats: list[dict[str, Any]],
    difficulty: dict[str, Any],
) -> dict[str, Any]:
    if source.get("unsupported_reason"):
        return _empty_ai_status()

    assignment_id = str(source["assignment"]["id"])
    questions_signature = str(source.get("questions_signature") or "")
    job = _load_wrong_summary_job(assignment_id, questions_signature) if questions_signature else None
    pending_text_questions = _pending_text_question_count(question_stats)
    pending_difficulty = 0
    needs_ai = pending_text_questions > 0 or pending_difficulty > 0

    job_status = str((job or {}).get("status") or "").strip().lower()
    if job_status == WRONG_SUMMARY_JOB_STATUS_FAILED:
        is_active = False
        label = "AI 归集失败"
        message = (job or {}).get("error_message") or "后台 AI 归集没有完成，请稍后刷新或重新进入页面触发重试。"
    elif needs_ai:
        is_active = True
        job_status = job_status if job_status in ACTIVE_WRONG_SUMMARY_JOB_STATUSES else WRONG_SUMMARY_JOB_STATUS_QUEUED
        label = "后台归集中" if job_status == WRONG_SUMMARY_JOB_STATUS_RUNNING else "等待归集"
        pieces = []
        if pending_text_questions:
            pieces.append(f"{pending_text_questions} 道主观题错误答案/原因")
        message = "快速 AI 正在后台整理：" + "、".join(pieces) + "，页面会自动刷新结果。"
    else:
        is_active = False
        job_status = job_status or WRONG_SUMMARY_JOB_STATUS_COMPLETED
        label = "归集完成"
        message = "错题归集已完成。"

    return {
        "needs_ai": needs_ai,
        "is_active": is_active,
        "job_status": job_status,
        "status_label": label,
        "message": message,
        "pending_text_questions": pending_text_questions,
        "pending_difficulty": pending_difficulty,
        "job": job,
    }


def _empty_ai_status() -> dict[str, Any]:
    return {
        "needs_ai": False,
        "is_active": False,
        "job_status": "idle",
        "status_label": "无需归集",
        "message": "",
        "pending_text_questions": 0,
        "pending_difficulty": 0,
        "job": None,
    }


def _pending_text_question_count(question_stats: list[dict[str, Any]]) -> int:
    return len(
        [
            item
            for item in question_stats
            if item["question"]["type"] in TEXT_QUESTION_TYPES
            and item["wrong_count"] > 0
            and item.get("text_cluster_status") == "pending"
        ]
    )


def _collect_ai_job_errors(question_stats: list[dict[str, Any]], difficulty: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for item in question_stats:
        if item.get("text_cluster_status") == "fallback" and item.get("text_cluster_error"):
            errors.append(f"第 {item['question']['ordinal']} 题错答归集失败：{item['text_cluster_error']}")
    return [_clip_text(error, 180) for error in errors[:4]]


def _wrong_summary_job_key(assignment_id: str, questions_signature: str) -> str:
    return f"{assignment_id}:{questions_signature}:{PROMPT_VERSION}"


def _schedule_wrong_summary_ai_task(assignment_id: str, teacher_id: int, questions_signature: str) -> None:
    job_key = _wrong_summary_job_key(assignment_id, questions_signature)
    if job_key in _active_wrong_summary_jobs:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    _active_wrong_summary_jobs.add(job_key)
    asyncio.create_task(
        run_assignment_wrong_summary_ai_job(
            assignment_id,
            teacher_id,
            questions_signature=questions_signature,
        )
    )


def _recover_stale_wrong_summary_jobs(conn) -> None:
    cutoff = datetime.fromtimestamp(datetime.now().timestamp() - STALE_WRONG_SUMMARY_JOB_MINUTES * 60).isoformat(
        timespec="seconds"
    )
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE assignment_wrong_summary_jobs
        SET status = ?,
            error_message = CASE
                WHEN TRIM(COALESCE(error_message, '')) = '' THEN ?
                ELSE error_message
            END,
            completed_at = COALESCE(completed_at, ?),
            updated_at = ?
        WHERE status = ?
          AND COALESCE(started_at, updated_at, created_at) < ?
        """,
        (
            WRONG_SUMMARY_JOB_STATUS_FAILED,
            "后台 AI 归集长时间未完成，系统已停止等待。",
            now,
            now,
            WRONG_SUMMARY_JOB_STATUS_RUNNING,
            cutoff,
        ),
    )


def _load_wrong_summary_job(assignment_id: str, questions_signature: str) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        _recover_stale_wrong_summary_jobs(conn)
        row = conn.execute(
            """
            SELECT *
            FROM assignment_wrong_summary_jobs
            WHERE assignment_id = ?
              AND questions_signature = ?
              AND prompt_version = ?
            LIMIT 1
            """,
            (assignment_id, questions_signature, PROMPT_VERSION),
        ).fetchone()
        conn.commit()
    return _serialize_wrong_summary_job(row) if row else None


def _serialize_wrong_summary_job(row) -> dict[str, Any]:
    item = dict(row)
    return {
        "id": int(item.get("id") or 0),
        "assignment_id": str(item.get("assignment_id") or ""),
        "teacher_id": int(item.get("teacher_id") or 0),
        "questions_signature": str(item.get("questions_signature") or ""),
        "status": str(item.get("status") or WRONG_SUMMARY_JOB_STATUS_QUEUED),
        "pending_text_questions": int(item.get("pending_text_questions") or 0),
        "pending_difficulty": int(item.get("pending_difficulty") or 0),
        "error_message": str(item.get("error_message") or ""),
        "created_at": str(item.get("created_at") or ""),
        "started_at": str(item.get("started_at") or ""),
        "completed_at": str(item.get("completed_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def _mark_wrong_summary_job_running(assignment_id: str, questions_signature: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        conn.execute(
            """
            UPDATE assignment_wrong_summary_jobs
            SET status = ?,
                started_at = COALESCE(started_at, ?),
                error_message = '',
                updated_at = ?
            WHERE assignment_id = ?
              AND questions_signature = ?
              AND prompt_version = ?
            """,
            (
                WRONG_SUMMARY_JOB_STATUS_RUNNING,
                now,
                now,
                assignment_id,
                questions_signature,
                PROMPT_VERSION,
            ),
        )
        conn.commit()


def _mark_wrong_summary_job_completed(assignment_id: str, questions_signature: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        conn.execute(
            """
            UPDATE assignment_wrong_summary_jobs
            SET status = ?,
                error_message = '',
                completed_at = COALESCE(completed_at, ?),
                updated_at = ?
            WHERE assignment_id = ?
              AND questions_signature = ?
              AND prompt_version = ?
            """,
            (
                WRONG_SUMMARY_JOB_STATUS_COMPLETED,
                now,
                now,
                assignment_id,
                questions_signature,
                PROMPT_VERSION,
            ),
        )
        conn.commit()


def _mark_wrong_summary_job_failed(assignment_id: str, questions_signature: str, error_message: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        conn.execute(
            """
            UPDATE assignment_wrong_summary_jobs
            SET status = ?,
                error_message = ?,
                completed_at = COALESCE(completed_at, ?),
                updated_at = ?
            WHERE assignment_id = ?
              AND questions_signature = ?
              AND prompt_version = ?
            """,
            (
                WRONG_SUMMARY_JOB_STATUS_FAILED,
                _clip_text(error_message, 500),
                now,
                now,
                assignment_id,
                questions_signature,
                PROMPT_VERSION,
            ),
        )
        conn.commit()


def _extract_exam_questions(exam_data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = exam_data.get("pages") if isinstance(exam_data, dict) else []
    if not isinstance(pages, list):
        pages = []
    questions: list[dict[str, Any]] = []
    ordinal = 0
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_name = str(page.get("name") or f"第 {page_index} 部分").strip()
        for question_index, raw_question in enumerate(page.get("questions") or [], start=1):
            if not isinstance(raw_question, dict):
                continue
            ordinal += 1
            question_id = str(raw_question.get("id") or f"p{page_index}_q{question_index}").strip()
            question_type = str(raw_question.get("type") or "").strip().lower() or "textarea"
            options = raw_question.get("options") if isinstance(raw_question.get("options"), list) else []
            question = {
                "id": question_id,
                "key": question_id or f"q{ordinal}",
                "ordinal": ordinal,
                "page_name": page_name,
                "type": question_type,
                "type_label": QUESTION_TYPE_LABELS.get(question_type, question_type or "题目"),
                "text": str(raw_question.get("text") or raw_question.get("question") or "").strip(),
                "options": [str(item).strip() for item in options if str(item).strip()],
                "answer": raw_question.get("answer"),
                "answer_text": "",
                "points": raw_question.get("points")
                or (raw_question.get("grading") or {}).get("points")
                if isinstance(raw_question.get("grading"), dict)
                else raw_question.get("points"),
            }
            question["option_meta"] = _build_option_meta(question)
            question["answer_text"] = _format_correct_answer(question)
            questions.append(question)
    return questions


def _build_question_error_stats(
    questions: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    answer_maps = [
        {
            "submission": submission,
            "answers": _answers_by_question(submission.get("answers_json")),
            "feedback_scores": _feedback_scores_by_question(submission.get("feedback_md")),
        }
        for submission in submissions
        if not _is_absence_or_unsubmitted(submission)
    ]

    for question in questions:
        answer_buckets: dict[str, list[dict[str, Any]]] = {}
        wrong_records: list[dict[str, Any]] = []
        option_counter: Counter[str] = Counter()
        wrong_samples: list[str] = []
        attempted_count = 0
        correct_count = 0
        blank_wrong_count = 0
        scored_count = 0
        score_total = 0.0
        max_score_total = 0.0
        score_loss_total = 0.0
        full_score = _question_full_score(question)

        for item in answer_maps:
            answer_record = _get_answer_record(item["answers"], question)
            raw_answer = _answer_value(answer_record)
            if _answer_has_value(raw_answer):
                attempted_count += 1
            score_record = _score_record_for_question(question, answer_record, item["feedback_scores"])
            score = score_record.get("score")
            max_score = score_record.get("max_score") or full_score
            if score is None or max_score is None or float(max_score) <= 0:
                continue

            score = float(score)
            max_score = float(max_score)
            scored_count += 1
            score_total += score
            max_score_total += max_score
            if not _is_not_full_score(score, max_score):
                correct_count += 1
                continue

            score_loss_total += max(0.0, max_score - score)
            display = _format_student_answer(question, raw_answer)
            detail = {
                "submission_id": item["submission"].get("id"),
                "student_pk_id": item["submission"].get("student_pk_id"),
                "student_name": _clip_text(item["submission"].get("student_name") or "未命名学生", 80),
                "answer": _clip_text(display, 600),
                "answer_raw": _clip_text(_raw_answer_text(raw_answer), 800),
                "score": score,
                "max_score": max_score,
                "score_text": f"{_format_score_value(score)}/{_format_score_value(max_score)}",
            }
            wrong_records.append(detail)
            answer_buckets.setdefault(display, []).append(detail)
            if not _answer_has_value(raw_answer):
                blank_wrong_count += 1
            if len(wrong_samples) < 12 and display not in wrong_samples:
                wrong_samples.append(display)
            if question["type"] in CHOICE_QUESTION_TYPES:
                for selected_key in _selected_choice_keys(question, raw_answer):
                    option_counter[selected_key] += 1

        wrong_count = len(wrong_records)
        top_wrong_answers = _build_local_wrong_answer_groups(answer_buckets, wrong_count)
        average_score_percent = _percent_float(score_total, max_score_total)
        wrong_percent = _percent(wrong_count, scored_count)
        difficulty_score = _score_difficulty_index(wrong_percent, average_score_percent, score_loss_total, max_score_total)
        stats.append(
            {
                "question": question,
                "attempted_count": attempted_count,
                "correct_count": correct_count,
                "wrong_count": wrong_count,
                "wrong_percent": wrong_percent,
                "blank_wrong_count": blank_wrong_count,
                "scored_count": scored_count,
                "average_score": round(score_total / scored_count, 2) if scored_count else None,
                "average_score_percent": round(average_score_percent) if average_score_percent is not None else None,
                "score_loss_total": round(score_loss_total, 2),
                "difficulty_score": difficulty_score,
                "difficulty_label": _score_difficulty_label(difficulty_score),
                "difficulty_reason": _score_difficulty_reason(wrong_count, scored_count, wrong_percent, average_score_percent),
                "top_wrong_answers": top_wrong_answers,
                "wrong_answer_counter": {label: len(records) for label, records in answer_buckets.items()},
                "wrong_records": wrong_records,
                "wrong_samples": wrong_samples,
                "option_bars": _build_option_bars(question, option_counter, wrong_count),
                "text_cluster_status": "not_required",
                "text_cluster_error": "",
            }
        )

    stats.sort(key=lambda item: (-int(item["wrong_count"]), int(item["question"]["ordinal"])))
    return stats


def _score_record_for_question(
    question: dict[str, Any],
    answer_record: Any,
    feedback_scores: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    direct_score = _score_record_from_answer(answer_record)
    feedback_score = _lookup_feedback_score(question, feedback_scores)
    score = direct_score.get("score") if direct_score.get("score") is not None else feedback_score.get("score")
    max_score = (
        direct_score.get("max_score")
        if direct_score.get("max_score") is not None
        else feedback_score.get("max_score")
    )
    if max_score is None:
        max_score = _question_full_score(question)
    return {"score": score, "max_score": max_score}


def _score_record_from_answer(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"score": None, "max_score": None}
    score = _first_float(record, ("score", "question_score", "earned_score", "awarded_score", "points_awarded", "得分"))
    max_score = _first_float(record, ("max_score", "full_score", "total_score", "points", "满分"))
    grading = record.get("grading")
    if isinstance(grading, dict):
        if score is None:
            score = _first_float(grading, ("score", "question_score", "earned_score", "awarded_score", "points_awarded", "得分"))
        if max_score is None:
            max_score = _first_float(grading, ("max_score", "full_score", "total_score", "points", "满分"))
    return {"score": score, "max_score": max_score}


def _feedback_scores_by_question(feedback_md: Any) -> dict[str, dict[str, Any]]:
    raw = str(feedback_md or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return {}

    result: dict[str, dict[str, Any]] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if not current_key:
            current_lines = []
            return
        section_text = "\n".join(current_lines)
        score, max_score = _extract_score_pair(section_text)
        if score is not None:
            _store_feedback_score(result, current_key, score, max_score)
        current_lines = []

    for line in raw.split("\n"):
        heading = _feedback_question_heading(line)
        if heading:
            flush()
            current_key = heading
            current_lines = [line]
            continue
        if current_key:
            current_lines.append(line)
    flush()

    if result:
        return result

    score_pairs = re.findall(r"(?:第\s*)?(\d+)\s*(?:题|question|q)?[^\n]{0,80}?(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", raw, flags=re.I)
    for question_no, score, max_score in score_pairs:
        _store_feedback_score(result, question_no, float(score), float(max_score))
    return result


def _feedback_question_heading(line: Any) -> str | None:
    text = str(line or "").strip()
    match = re.match(r"^#{1,6}\s*(?:第\s*)?([0-9A-Za-z_-]+)\s*(?:题|question|q)?(?:\s|$|[：:.\-、])", text, flags=re.I)
    if match:
        value = match.group(1).strip()
        if value:
            return value
    return None


def _extract_score_pair(text: Any) -> tuple[float | None, float | None]:
    raw = str(text or "")
    pair = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", raw)
    if pair:
        return float(pair.group(1)), float(pair.group(2))
    number = re.search(r"(?:本题得分|得分|score)\s*[：:]\s*(-?\d+(?:\.\d+)?)", raw, flags=re.I)
    if number:
        return float(number.group(1)), None
    return None, None


def _store_feedback_score(
    result: dict[str, dict[str, Any]],
    raw_key: Any,
    score: float | None,
    max_score: float | None,
) -> None:
    if score is None:
        return
    for key in _question_score_aliases(raw_key):
        result[key] = {"score": score, "max_score": max_score}


def _lookup_feedback_score(question: dict[str, Any], feedback_scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for raw_key in (question.get("id"), question.get("key"), question.get("ordinal")):
        for alias in _question_score_aliases(raw_key):
            if alias in feedback_scores:
                return feedback_scores[alias]
    return {}


def _question_score_aliases(raw_key: Any) -> set[str]:
    text = str(raw_key or "").strip()
    if not text:
        return set()
    aliases = {text, text.casefold()}
    number = re.search(r"\d+", text)
    if number:
        value = str(int(number.group(0)))
        aliases.update({value, f"q{value}", f"Q{value}", f"第{value}题"})
    return {alias for alias in aliases if alias}


def _question_full_score(question: dict[str, Any]) -> float | None:
    return _coerce_float(question.get("points"))


def _first_float(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in mapping:
            value = _coerce_float(mapping.get(key))
            if value is not None:
                return value
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if match:
            return float(match.group(0))
    return None


def _is_not_full_score(score: float, max_score: float) -> bool:
    return score < max_score - 1e-6


def _format_score_value(value: Any) -> str:
    number = _coerce_float(value)
    if number is None:
        return ""
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _raw_answer_text(raw_answer: Any) -> str:
    if isinstance(raw_answer, list):
        return "；".join(str(item).strip() for item in raw_answer if str(item).strip())
    if isinstance(raw_answer, dict):
        return json.dumps(raw_answer, ensure_ascii=False)
    return str(raw_answer or "").strip() or "未作答"


def _build_local_wrong_answer_groups(
    answer_buckets: dict[str, list[dict[str, Any]]],
    wrong_count: int,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for label, records in sorted(answer_buckets.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:3]:
        details = _wrong_answer_details(records)
        groups.append(
            {
                "label": _clip_text(label, 100),
                "count": len(records),
                "percent": _percent(len(records), wrong_count),
                "examples": [_clip_text(label, 90)],
                "likely_issue": "",
                "source": "local",
                "details": details,
            }
        )
    return groups


def _wrong_answer_details(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "student_name": record.get("student_name") or "未命名学生",
            "answer": _clip_text(record.get("answer_raw") or record.get("answer") or "未作答", 600),
            "score_text": record.get("score_text") or "",
            "submission_id": record.get("submission_id"),
        }
        for record in records
    ]


def _selected_choice_keys(question: dict[str, Any], raw_value: Any) -> list[str]:
    values = _split_answer_values(raw_value) if question.get("type") == "checkbox" else [raw_value]
    keys: list[str] = []
    for value in values:
        canonical = _canonical_choice_value(question, value)
        if canonical:
            keys.append(canonical)
    return keys


def _build_option_bars(question: dict[str, Any], option_counter: Counter[str], wrong_count: int) -> list[dict[str, Any]]:
    if question["type"] not in CHOICE_QUESTION_TYPES or wrong_count <= 0:
        return []
    option_meta = question.get("option_meta") or {}
    labels = option_meta.get("labels") or {}
    order = option_meta.get("order") or {}
    correct_keys = set(_selected_choice_keys(question, question.get("answer")))
    bars: list[dict[str, Any]] = []
    for key, label in sorted(labels.items(), key=lambda pair: int(order.get(pair[0], 9999))):
        count = int(option_counter.get(key) or 0)
        is_correct = key in correct_keys
        if is_correct:
            tone = "correct"
        elif count > 0:
            tone = "wrong"
        else:
            tone = "muted"
        bars.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "percent": _percent(count, wrong_count),
                "is_correct": is_correct,
                "tone": tone,
            }
        )
    return bars


def _percent_float(part: float, total: float) -> float | None:
    if total <= 0:
        return None
    return max(0.0, min(100.0, float(part) * 100.0 / float(total)))


def _score_difficulty_index(
    wrong_percent: int,
    average_score_percent: float | None,
    score_loss_total: float,
    max_score_total: float,
) -> int:
    loss_percent = _percent_float(score_loss_total, max_score_total) or 0.0
    average_loss_percent = 100.0 - average_score_percent if average_score_percent is not None else loss_percent
    value = 0.55 * float(wrong_percent) + 0.35 * average_loss_percent + 0.10 * loss_percent
    return int(round(max(0.0, min(100.0, value))))


def _score_difficulty_label(value: int) -> str:
    if value >= 70:
        return "高难点"
    if value >= 40:
        return "中等难点"
    return "轻微风险"


def _score_difficulty_reason(
    wrong_count: int,
    scored_count: int,
    wrong_percent: int,
    average_score_percent: float | None,
) -> str:
    if scored_count <= 0:
        return "暂无逐题得分，暂不纳入难题排序。"
    average_text = f"{round(average_score_percent)}%" if average_score_percent is not None else "未识别"
    return f"{wrong_count} 人未满分，未满分率 {wrong_percent}%，平均得分率 {average_text}。"


def _build_score_based_hard_questions(question_stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [item for item in question_stats if int(item.get("wrong_count") or 0) > 0],
        key=lambda item: (
            -int(item.get("difficulty_score") or 0),
            -int(item.get("wrong_count") or 0),
            int(item["question"]["ordinal"]),
        ),
    )


def _score_based_difficulty_summary(hard_questions: list[dict[str, Any]]) -> dict[str, Any]:
    if not hard_questions:
        return _empty_difficulty_summary("所有已识别逐题得分的题目均为满分，暂未形成难题归集。")
    hardest = hard_questions[0]
    return {
        "summary": f"按逐题得分自动排序：第 {hardest['question']['ordinal']} 题当前最需要优先讲评，{hardest.get('difficulty_reason')}",
        "items": hard_questions,
        "source": "score_based",
        "error": "",
    }


async def _attach_text_answer_clusters(
    assignment_id: str,
    question_stats: list[dict[str, Any]],
    *,
    allow_generate: bool,
) -> None:
    pending: list[tuple[dict[str, Any], str]] = []
    for item in question_stats:
        question = item["question"]
        if question["type"] not in TEXT_QUESTION_TYPES or item["wrong_count"] <= 0:
            continue
        signature = _signature(
            {
                "question_id": question["id"],
                "question_type": question["type"],
                "correct_answer": question.get("answer"),
                "wrong_answers": item["wrong_answer_counter"],
                "wrong_count": item["wrong_count"],
            }
        )
        cached = _load_text_cluster_cache(assignment_id, question["key"], signature)
        if cached:
            groups = _attach_details_to_wrong_groups(
                cached.get("groups") or [],
                item["wrong_records"],
                int(item["wrong_count"]),
            )
            item["top_wrong_answers"] = groups or item["top_wrong_answers"]
            item["text_cluster_status"] = "cached"
            continue
        item["text_cluster_status"] = "pending"
        pending.append((item, signature))

    if not allow_generate:
        return

    for item, signature in pending:
        question = item["question"]
        try:
            result = await _generate_text_wrong_clusters(question, item["wrong_records"], int(item["wrong_count"]))
            groups = _normalize_text_cluster_groups(result, int(item["wrong_count"]))
            groups = _attach_details_to_wrong_groups(groups, item["wrong_records"], int(item["wrong_count"]))
            if groups:
                item["top_wrong_answers"] = groups
                item["text_cluster_status"] = "generated"
                _save_text_cluster_cache(
                    assignment_id,
                    question["key"],
                    signature,
                    {"groups": _strip_group_details_for_cache(groups)},
                )
            else:
                item["text_cluster_status"] = "fallback"
        except Exception as exc:
            item["text_cluster_status"] = "fallback"
            item["text_cluster_error"] = _clip_text(str(exc), 180)


async def _generate_text_wrong_clusters(
    question: dict[str, Any],
    wrong_records: list[dict[str, Any]],
    wrong_count: int,
) -> dict[str, Any]:
    question_type = str(question.get("type") or "")
    answer_lines: list[str] = []
    if question_type == "textarea":
        for idx, record in enumerate(wrong_records[:60], start=1):
            answer_lines.append(
                f"{idx}. 得分 {record.get('score_text') or '-'}："
                f"{_clip_text(record.get('answer_raw') or record.get('answer'), 360)}"
            )
        task_instruction = "请归集这道简答题未满分答案的高频错误原因，最多返回 5 类；label 写成清晰的错误原因。"
        label_hint = "错误原因概括"
        task_label = "short_answer_error_cluster"
    else:
        raw_counter = Counter(
            _clip_text(record.get("answer_raw") or record.get("answer") or "未作答", 220)
            for record in wrong_records
        )
        for idx, (answer, count) in enumerate(
            sorted(raw_counter.items(), key=lambda pair: (-int(pair[1]), pair[0]))[:40],
            start=1,
        ):
            answer_lines.append(f"{idx}. {answer}：{count}人")
        task_instruction = "请归集这道填空题的高频错误写法，最多返回 3 类；label 写成最有代表性的错误答案或写法。"
        label_hint = "错误写法概括"
        task_label = "fill_answer_error_cluster"
    system_prompt = (
        "你是教学数据分析助手。请只根据教师提供的未满分学生答案做归集，"
        "不要判断答案是否正确，不要改写正确答案，不要编造未出现的学生答案。返回严格 JSON。"
    )
    user_message = "\n".join(
        [
            task_instruction,
            f"题目：{question.get('text') or '未填写题干'}",
            f"正确答案：{question.get('answer_text') or question.get('answer') or '未提供'}",
            f"未满分人数：{wrong_count}",
            "学生原始答案：",
            "\n".join(answer_lines) or "暂无",
            (
                f'返回 JSON：{{"groups":[{{"label":"{label_hint}","count":3,'
                '"examples":["必须来自上方学生原始答案"],"likely_issue":"可能误区或讲评提醒"}]}}。'
            ),
        ]
    )
    return await _call_fast_json_ai(system_prompt, user_message, task_label, timeout=55.0)


async def _call_fast_json_ai(system_prompt: str, user_message: str, task_label: str, *, timeout: float) -> dict[str, Any]:
    response = await ai_client.post(
        "/api/ai/chat",
        json={
            "system_prompt": system_prompt,
            "messages": [],
            "new_message": user_message,
            "model_capability": "standard",
            "task_type": "fast_text_response",
            "response_format": "json",
            "task_priority": "interactive",
            "task_label": task_label,
            "web_search_enabled": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"AI 返回失败：{str(payload)[:240]}")
    result = payload.get("response_json")
    if isinstance(result, dict):
        return result
    result = _extract_json_object(payload.get("response_text"))
    if isinstance(result, dict):
        return result
    raise RuntimeError("AI 未返回可解析的 JSON")


def _load_text_cluster_cache(assignment_id: str, question_key: str, answer_signature: str) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        row = conn.execute(
            """
            SELECT result_json
            FROM assignment_wrong_answer_ai_cache
            WHERE assignment_id = ?
              AND question_key = ?
              AND answer_signature = ?
              AND prompt_version = ?
            LIMIT 1
            """,
            (assignment_id, question_key, answer_signature, PROMPT_VERSION),
        ).fetchone()
        conn.commit()
    return _load_json_object(row["result_json"]) if row else None


def _save_text_cluster_cache(assignment_id: str, question_key: str, answer_signature: str, result: dict[str, Any]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ensure_wrong_summary_cache_tables(conn)
        conn.execute(
            """
            INSERT INTO assignment_wrong_answer_ai_cache (
                assignment_id, question_key, answer_signature, prompt_version,
                result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(assignment_id, question_key, answer_signature, prompt_version)
            DO UPDATE SET result_json = excluded.result_json, updated_at = excluded.updated_at
            """,
            (
                assignment_id,
                question_key,
                answer_signature,
                PROMPT_VERSION,
                json.dumps(result, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()


def _answers_by_question(raw_answers: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw_answers) if isinstance(raw_answers, str) else raw_answers
    except (TypeError, json.JSONDecodeError):
        payload = {}
    answers = payload.get("answers") if isinstance(payload, dict) and "answers" in payload else payload
    result: dict[str, Any] = {}
    if isinstance(answers, list):
        for item in answers:
            if not isinstance(item, dict):
                continue
            key = str(item.get("question_id") or item.get("question") or "").strip()
            if key:
                result[key] = item
    elif isinstance(answers, dict):
        for key, value in answers.items():
            result[str(key)] = value
    return result


def _get_answer_record(answer_map: dict[str, Any], question: dict[str, Any]) -> Any:
    for key in (question.get("id"), question.get("key"), question.get("text")):
        key_text = str(key or "").strip()
        if key_text and key_text in answer_map:
            return answer_map[key_text]
    return None


def _answer_value(record: Any) -> Any:
    if isinstance(record, dict):
        for key in ("answer", "content", "text", "value"):
            if key in record:
                return record.get(key)
        return ""
    return record


def _canonical_answer_key(question: dict[str, Any], raw_value: Any) -> str:
    question_type = question.get("type")
    if question_type == "checkbox":
        values = _split_answer_values(raw_value)
        canonical_values = [_canonical_choice_value(question, value) for value in values]
        return "|||".join(sorted(value for value in canonical_values if value))
    if question_type == "radio":
        return _canonical_choice_value(question, raw_value)
    return _normalize_free_text(raw_value)


def _canonical_choice_value(question: dict[str, Any], raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    normalized = _normalize_choice_token(value)
    option_meta = question.get("option_meta") or {}
    aliases = option_meta.get("aliases") or {}
    return aliases.get(normalized, normalized)


def _build_option_meta(question: dict[str, Any]) -> dict[str, Any]:
    aliases: dict[str, str] = {}
    labels: dict[str, str] = {}
    order: dict[str, int] = {}
    for idx, option in enumerate(question.get("options") or []):
        label = _option_label(option, idx)
        canonical = f"opt:{label}"
        labels[canonical] = option
        order[canonical] = idx
        raw_label = label.upper()
        option_body = _option_body(option)
        alias_values = {
            option,
            option_body,
            raw_label,
            f"{raw_label}.",
            f"{raw_label}、",
            f"{raw_label})",
        }
        for alias in alias_values:
            token = _normalize_choice_token(alias)
            if token:
                aliases[token] = canonical
    return {"aliases": aliases, "labels": labels, "order": order}


def _option_label(option: str, index: int) -> str:
    match = re.match(r"^\s*([A-Za-z0-9]+)\s*[\.\)、:：]\s+", str(option or ""))
    if match:
        return match.group(1).upper()
    if 0 <= index < 26:
        return chr(ord("A") + index)
    return str(index + 1)


def _option_body(option: str) -> str:
    return re.sub(r"^\s*[A-Za-z0-9]+\s*[\.\)、:：]\s+", "", str(option or "")).strip()


def _format_student_answer(question: dict[str, Any], raw_value: Any) -> str:
    if not _answer_has_value(raw_value):
        return "未作答"
    question_type = question.get("type")
    if question_type in CHOICE_QUESTION_TYPES:
        values = _split_answer_values(raw_value) if question_type == "checkbox" else [raw_value]
        display_values = [_choice_display(question, value) for value in values]
        return "；".join([value for value in display_values if value]) or str(raw_value)
    return _clip_text(str(raw_value).strip(), 220)


def _format_correct_answer(question: dict[str, Any]) -> str:
    answer = question.get("answer")
    question_type = question.get("type")
    if question_type in CHOICE_QUESTION_TYPES:
        return _format_student_answer(question, answer)
    if isinstance(answer, list):
        return "；".join(str(item).strip() for item in answer if str(item).strip())
    return str(answer or "").strip()


def _choice_display(question: dict[str, Any], raw_value: Any) -> str:
    canonical = _canonical_choice_value(question, raw_value)
    labels = (question.get("option_meta") or {}).get("labels") or {}
    if canonical in labels:
        return labels[canonical]
    return _clip_text(str(raw_value or "").strip(), 180)


def _split_answer_values(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    value = str(raw_value or "").strip()
    if not value:
        return []
    if "|||" in value:
        return [item.strip() for item in value.split("|||") if item.strip()]
    for sep in ("；", ";", "，", ",", "、", "|"):
        if sep in value:
            return [item.strip() for item in value.split(sep) if item.strip()]
    return [value]


def _normalize_choice_token(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"\s+", "", text)
    text = text.strip(".。)、:：")
    return text


def _normalize_free_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"\s+", "", text)
    return text.strip(".,，。;；:：、")


def _normalize_text_cluster_groups(raw: dict[str, Any], wrong_count: int) -> list[dict[str, Any]]:
    groups = raw.get("groups") if isinstance(raw, dict) else None
    if not isinstance(groups, list):
        return []
    result: list[dict[str, Any]] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        label = _clip_text(str(item.get("label") or item.get("name") or "").strip(), 80)
        if not label:
            continue
        try:
            count = int(item.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        count = max(0, min(count, wrong_count))
        examples = item.get("examples") if isinstance(item.get("examples"), list) else []
        result.append(
            {
                "label": label,
                "count": count,
                "percent": _percent(count, wrong_count),
                "examples": [_clip_text(str(example), 90) for example in examples[:3] if str(example).strip()],
                "likely_issue": _clip_text(str(item.get("likely_issue") or item.get("reason") or "").strip(), 120),
                "source": "ai",
            }
        )
        if len(result) >= 3:
            break
    result.sort(key=lambda item: (-int(item["count"]), item["label"]))
    return result


def _attach_details_to_wrong_groups(
    groups: list[dict[str, Any]],
    wrong_records: list[dict[str, Any]],
    wrong_count: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "").strip()
        examples = [str(item).strip() for item in group.get("examples") or [] if str(item).strip()]
        matched = _match_wrong_records_for_group(label, examples, wrong_records)
        count = len(matched) if matched else int(group.get("count") or 0)
        result.append(
            {
                "label": _clip_text(label, 100),
                "count": max(0, min(count, wrong_count)),
                "percent": _percent(max(0, min(count, wrong_count)), wrong_count),
                "examples": [_clip_text(example, 90) for example in examples[:3]],
                "likely_issue": _clip_text(str(group.get("likely_issue") or group.get("reason") or "").strip(), 160),
                "source": group.get("source") or "ai",
                "details": _wrong_answer_details(matched),
            }
        )
        if len(result) >= 5:
            break
    result.sort(key=lambda item: (-int(item["count"]), item["label"]))
    return result[:5]


def _match_wrong_records_for_group(
    label: str,
    examples: list[str],
    wrong_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not wrong_records:
        return []
    example_keys = {_normalize_free_text(example) for example in examples if example}
    label_key = _normalize_free_text(label)
    matched: list[dict[str, Any]] = []
    for record in wrong_records:
        answer_text = str(record.get("answer_raw") or record.get("answer") or "").strip()
        answer_key = _normalize_free_text(answer_text)
        display_key = _normalize_free_text(record.get("answer") or "")
        if answer_key in example_keys or display_key in example_keys or (label_key and answer_key == label_key):
            matched.append(record)
    return matched


def _strip_group_details_for_cache(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups:
        result.append(
            {
                "label": group.get("label") or "",
                "count": int(group.get("count") or 0),
                "percent": int(group.get("percent") or 0),
                "examples": group.get("examples") or [],
                "likely_issue": group.get("likely_issue") or "",
                "source": group.get("source") or "ai",
            }
        )
    return result


def _empty_difficulty_summary(message: str, *, source: str = "none") -> dict[str, Any]:
    return {"summary": message, "items": [], "source": source, "error": message}


def _load_json_object(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if raw_value in (None, ""):
        return {}
    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_json_object(value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _signature(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _answer_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(_answer_has_value(item) for item in value)
    return bool(str(value).strip())


def _is_absence_or_unsubmitted(submission: dict[str, Any]) -> bool:
    return int(submission.get("is_absence_score") or 0) == 1 or str(submission.get("status") or "") == "unsubmitted"


def _percent(part: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round(part / total * 100))


def _clip_text(value: Any, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
