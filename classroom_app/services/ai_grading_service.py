from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..core import ai_client
from ..database import get_db_connection
from .ai_grading_attachments import ensure_ai_grading_attachments_supported
from .psych_profile_service import load_latest_hidden_profile
from .submission_file_alignment import resolve_submission_file_path


class AIGradingQueueError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


STALE_GRADING_MESSAGE = "AI 批改任务长时间未返回结果，系统已自动转为失败状态，请重新发起 AI 批改或手动批改。"


def expire_stale_ai_grading_submissions(
    conn,
    *,
    stale_minutes: int = 240,
) -> int:
    cutoff = (datetime.now() - timedelta(minutes=max(15, int(stale_minutes or 240)))).isoformat()
    stale_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id,
                   s.assignment_id,
                   lsea.id AS stage_attempt_id,
                   lsea.class_offering_id AS stage_class_offering_id,
                   lsea.student_id AS stage_student_id,
                   lsea.stage_key AS stage_key
            FROM submissions s
            LEFT JOIN learning_stage_exam_attempts lsea
                   ON lsea.assignment_id = s.assignment_id
                  AND lsea.student_id = s.student_pk_id
                  AND lsea.status = 'grading'
            WHERE s.status = 'grading'
              AND COALESCE(s.grading_started_at, s.submitted_at) < ?
            """,
            (cutoff,),
        ).fetchall()
    ]
    cursor = conn.execute(
        """
        UPDATE submissions
        SET status = 'grading_failed',
            feedback_md = ?,
            grading_started_at = NULL,
            grading_attempt_fingerprint = NULL
        WHERE status = 'grading'
          AND COALESCE(grading_started_at, submitted_at) < ?
        """,
        (STALE_GRADING_MESSAGE, cutoff),
    )
    stage_attempt_ids = sorted(
        {
            int(row["stage_attempt_id"])
            for row in stale_rows
            if row.get("stage_attempt_id") is not None
        }
    )
    if stage_attempt_ids:
        placeholders = ",".join("?" for _ in stage_attempt_ids)
        conn.execute(
            f"""
            UPDATE learning_stage_exam_attempts
            SET status = 'failed',
                ai_error = ?
            WHERE id IN ({placeholders})
              AND status = 'grading'
            """,
            (STALE_GRADING_MESSAGE, *stage_attempt_ids),
        )
        stage_targets = {
            (
                int(row["stage_class_offering_id"]),
                int(row["stage_student_id"]),
                str(row["stage_key"]),
            )
            for row in stale_rows
            if row.get("stage_attempt_id") is not None
            and row.get("stage_class_offering_id") is not None
            and row.get("stage_student_id") is not None
            and row.get("stage_key") is not None
        }
        for class_offering_id, student_id, stage_key in stage_targets:
            conn.execute(
                """
                UPDATE learning_stage_status
                SET status = 'challenge_ready',
                    last_calculated_at = ?
                WHERE class_offering_id = ?
                  AND student_id = ?
                  AND stage_key = ?
                  AND status IN ('generating', 'in_exam')
                """,
                (datetime.now().isoformat(), class_offering_id, student_id, stage_key),
            )
    if stale_rows:
        try:
            from .message_center_service import create_teacher_grading_issue_notification

            for row in stale_rows:
                create_teacher_grading_issue_notification(
                    conn,
                    int(row["id"]),
                    issue_detail=STALE_GRADING_MESSAGE,
                    ref_suffix="grading_stale",
                )
        except Exception as exc:
            print(f"[AI_GRADING] stale grading notification failed: {exc}")
    return int(cursor.rowcount or 0)


def _extract_answer_attachment_context(answers_json: str | None) -> dict[str, dict[str, str]]:
    if not answers_json:
        return {}
    try:
        payload = json.loads(answers_json) if isinstance(answers_json, str) else answers_json
    except (TypeError, json.JSONDecodeError):
        return {}
    answers = payload.get("answers", payload) if isinstance(payload, dict) else payload
    if isinstance(answers, dict):
        answer_items = []
        for key, value in answers.items():
            if isinstance(value, dict):
                item = {"question_id": key, **value}
            else:
                item = {"question_id": key, "answer": value}
            answer_items.append(item)
    elif isinstance(answers, list):
        answer_items = [item for item in answers if isinstance(item, dict)]
    else:
        return {}

    result: dict[str, dict[str, str]] = {}
    for index, item in enumerate(answer_items, start=1):
        question_id = str(item.get("question_id") or item.get("question_no") or index)
        question_text = str(item.get("question") or item.get("title") or f"第 {index} 题")
        attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            kind = str(attachment.get("kind") or attachment.get("type") or "").lower()
            relative_path = str(attachment.get("relative_path") or attachment.get("stored_relative_path") or "").strip()
            file_name = str(attachment.get("file_name") or attachment.get("filename") or "").strip()
            if not relative_path and not file_name:
                continue
            mime_type = str(attachment.get("mime_type") or attachment.get("content_type") or "").lower()
            is_image_attachment = (
                kind in {"drawing", "image", "screenshot"}
                or relative_path.startswith("exam_drawings/")
                or mime_type.startswith("image/")
            )
            label = f"第 {question_id} 题{'附图' if is_image_attachment else '附件'}"
            if question_text:
                label = f"{label} - {question_text[:80]}"
            context = {
                "question_id": question_id,
                "question": question_text,
                "label": label,
                "file_name": file_name,
                "relative_path": relative_path,
                "kind": kind,
                "mime_type": mime_type,
            }
            for key in {file_name, relative_path, relative_path.split("/")[-1] if relative_path else ""}:
                normalized_key = str(key or "").strip().lower()
                if normalized_key:
                    result[normalized_key] = context
    return result


def _apply_attachment_context_to_file(item: dict[str, Any], context_by_file: dict[str, dict[str, str]]) -> dict[str, Any]:
    keys = {
        str(item.get("relative_path") or "").strip().lower(),
        str(item.get("original_filename") or "").strip().lower(),
    }
    context = next((context_by_file[key] for key in keys if key and key in context_by_file), None)
    if not context:
        return item
    label = context.get("label") or f"第 {context.get('question_id') or ''} 题附件"
    original = item.get("relative_path") or item.get("original_filename") or ""
    item["relative_path"] = f"{label} | {original}"
    return item


def _extract_ai_service_http_error(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    detail: Any = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("message") or payload.get("error") or ""
        else:
            detail = payload
    except Exception:
        detail = (response.text or "").strip()

    if isinstance(detail, (dict, list)):
        detail_text = json.dumps(detail, ensure_ascii=False)
    else:
        detail_text = str(detail or "").strip()
    prefix = f"AI 助手服务返回 {response.status_code}"
    return f"{prefix}: {detail_text[:1200]}" if detail_text else f"{prefix}: {str(exc)}"


def _load_submission_for_grading(
    conn,
    submission_id: int,
    teacher_id: int | None = None,
) -> dict[str, Any]:
    submission = conn.execute(
        """
        SELECT s.*,
               a.requirements_md,
               a.rubric_md,
               a.allowed_file_types_json,
               a.class_offering_id,
               c.created_by_teacher_id,
               o.teacher_id AS offering_teacher_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not submission:
        raise AIGradingQueueError(404, "Submission not found")
    submission_dict = dict(submission)
    if teacher_id is not None:
        owner_id = int(submission_dict.get("created_by_teacher_id") or 0)
        offering_teacher_id = int(submission_dict.get("offering_teacher_id") or 0)
        if int(teacher_id) not in {owner_id, offering_teacher_id}:
            raise AIGradingQueueError(403, "Permission denied")
    return submission_dict


def _load_submission_files_for_grading(conn, submission_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, stored_path, original_filename, relative_path, mime_type, file_size, file_ext, file_hash
        FROM submission_files
        WHERE submission_id = ?
        ORDER BY COALESCE(relative_path, original_filename), id
        """,
        (submission_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _resolve_grading_files(submission_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved_files = []
    missing_names = []
    for item in submission_files:
        resolved_path = resolve_submission_file_path(str(item.get("stored_path") or ""))
        if not resolved_path:
            missing_names.append(str(item.get("relative_path") or item.get("original_filename") or item.get("stored_path") or "附件"))
            continue
        item["resolved_path"] = str(Path(resolved_path).resolve())
        item["display_name"] = item.get("relative_path") or item.get("original_filename") or Path(resolved_path).name
        resolved_files.append(item)
    if missing_names:
        raise AIGradingQueueError(
            400,
            "AI 批改前检查未通过：以下附件文件已丢失或路径失效，请先删除或重新上传："
            + "、".join(missing_names[:8]),
        )
    return resolved_files


def build_submission_grading_fingerprint(
    submission: dict[str, Any],
    submission_files: list[dict[str, Any]],
) -> str:
    """Build a stable version token for an AI grading job."""
    files_payload = []
    for item in sorted(
        submission_files,
        key=lambda file_info: (
            str(file_info.get("relative_path") or file_info.get("original_filename") or ""),
            str(file_info.get("id") or ""),
        ),
    ):
        files_payload.append(
            {
                "id": item.get("id"),
                "relative_path": item.get("relative_path"),
                "stored_path": item.get("stored_path"),
                "original_filename": item.get("original_filename"),
                "file_size": item.get("file_size"),
                "file_hash": item.get("file_hash"),
            }
        )
    payload = {
        "submission_id": submission.get("id"),
        "submitted_at": submission.get("submitted_at"),
        "answers_json": submission.get("answers_json"),
        "files": files_payload,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _prepare_grading_inputs(
    submission: dict[str, Any],
    submission_files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, bool]:
    resolved_files = _resolve_grading_files(submission_files)
    has_files = bool(resolved_files)
    has_answers = bool(submission["answers_json"])
    if not has_files and not has_answers:
        raise AIGradingQueueError(400, "该提交没有可批改的内容（无文件也无答案）。")

    try:
        ensure_ai_grading_attachments_supported(resolved_files)
    except ValueError as exc:
        raise AIGradingQueueError(400, str(exc)) from exc

    context_by_file = _extract_answer_attachment_context(submission["answers_json"] if has_answers else None)
    resolved_files = [_apply_attachment_context_to_file(item, context_by_file) for item in resolved_files]
    return resolved_files, has_files, has_answers


def _clip_profile_field(value: Any, *, limit: int = 360) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _build_hidden_student_profile_context(conn, submission: dict[str, Any]) -> str:
    class_offering_id = submission.get("class_offering_id")
    student_pk_id = submission.get("student_pk_id")
    if not class_offering_id or not student_pk_id:
        return ""
    try:
        profile = load_latest_hidden_profile(
            conn,
            int(class_offering_id),
            int(student_pk_id),
            "student",
        )
    except Exception as exc:
        print(f"[AI_GRADING] 加载学生支持参考失败，已跳过个性化参考: {exc}")
        return ""
    if not profile:
        return ""

    fields = (
        ("学习支持摘要", profile.get("profile_summary")),
        ("近期状态", profile.get("mental_state_summary")),
        ("支持策略", profile.get("support_strategy")),
        ("表达习惯", profile.get("language_habit_summary")),
        ("偏好回应方式", profile.get("preferred_ai_style")),
        ("兴趣线索", profile.get("interest_hypothesis") or profile.get("preference_summary")),
    )
    lines = []
    for label, value in fields:
        clipped = _clip_profile_field(value)
        if clipped:
            lines.append(f"{label}：{clipped}")
    if not lines:
        return ""
    confidence = _clip_profile_field(profile.get("confidence"), limit=40)
    if confidence:
        lines.append(f"置信度：{confidence}")
    lines.append("使用边界：只用于让反馈更贴近学生当前学习状态；不得透露来源，不得做心理诊断，不得影响评分公平性。")
    return "\n".join(lines)


async def submit_submission_for_ai_grading(
    submission_id: int,
    *,
    teacher_id: int | None = None,
    allow_graded: bool = True,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        submission = _load_submission_for_grading(conn, submission_id, teacher_id=teacher_id)
        if int(submission.get("resubmission_allowed") or 0):
            raise AIGradingQueueError(400, "该提交已撤回并等待重交，不能批改旧版本")
        if submission["status"] == "grading":
            return {"status": "already_grading"}
        if not allow_graded and submission["status"] == "graded":
            return {"status": "already_graded"}

        submission_files = _load_submission_files_for_grading(conn, submission_id)
        submission_fingerprint = build_submission_grading_fingerprint(submission, submission_files)
        resolved_files, has_files, has_answers = _prepare_grading_inputs(submission, submission_files)

        # 同一连接内二次确认指纹，消除 TOCTOU 窗口
        current = _load_submission_for_grading(conn, submission_id, teacher_id=teacher_id)
        if int(current.get("resubmission_allowed") or 0):
            raise AIGradingQueueError(400, "该提交已撤回并等待重交，不能批改旧版本")
        if current["status"] == "grading":
            return {"status": "already_grading"}
        if not allow_graded and current["status"] == "graded":
            return {"status": "already_graded"}
        current_files = _load_submission_files_for_grading(conn, submission_id)
        current_fingerprint = build_submission_grading_fingerprint(current, current_files)
        if current_fingerprint != submission_fingerprint:
            submission = current
            submission_files = current_files
            submission_fingerprint = current_fingerprint
            resolved_files, has_files, has_answers = _prepare_grading_inputs(submission, submission_files)
        cursor = conn.execute(
            """
            UPDATE submissions
            SET status = 'grading',
                grading_started_at = ?,
                grading_attempt_fingerprint = ?
            WHERE id = ? AND COALESCE(resubmission_allowed, 0) = 0
            """,
            (datetime.now().isoformat(), submission_fingerprint, submission_id),
        )
        if cursor.rowcount != 1:
            raise AIGradingQueueError(409, "该提交状态刚刚发生变化，请刷新后重试")
        conn.commit()
        student_profile_context = _build_hidden_student_profile_context(conn, current)

    job_data = {
        "submission_id": submission_id,
        "rubric_md": submission["rubric_md"],
        "requirements_md": submission["requirements_md"] or "",
        "allowed_file_types_json": submission["allowed_file_types_json"],
        "files": [
            {
                "stored_path": item["resolved_path"],
                "original_filename": item.get("original_filename"),
                "relative_path": item.get("relative_path") or item.get("original_filename"),
                "mime_type": item.get("mime_type"),
                "file_size": item.get("file_size"),
                "file_ext": item.get("file_ext"),
                "file_hash": item.get("file_hash"),
            }
            for item in resolved_files
        ] if has_files else [],
        "file_paths": [item["resolved_path"] for item in resolved_files] if has_files else [],
        "answers_json": submission["answers_json"] if has_answers else None,
        "student_profile_context": student_profile_context,
        "submitted_at": submission.get("submitted_at"),
        "submission_fingerprint": submission_fingerprint,
    }

    try:
        response = await ai_client.post("/api/ai/submit-grading-job", json=job_data)
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError as exc:
        detail = "AI 助手服务未运行，请先启动 ai_assistant.py。"
        _reset_submission_after_queue_failure(submission_id, detail)
        raise AIGradingQueueError(503, detail) from exc
    except httpx.HTTPStatusError as exc:
        detail = _extract_ai_service_http_error(exc)
        _reset_submission_after_queue_failure(submission_id, detail)
        raise AIGradingQueueError(exc.response.status_code, detail) from exc
    except Exception as exc:
        detail = f"AI 任务提交失败: {exc}"
        _reset_submission_after_queue_failure(submission_id, detail)
        raise AIGradingQueueError(500, detail) from exc


def _reset_submission_after_queue_failure(submission_id: int, error_message: str = "") -> None:
    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE submissions
                SET status = 'submitted',
                    grading_started_at = NULL,
                    grading_attempt_fingerprint = NULL
                WHERE id = ? AND status = 'grading'
                """,
                (submission_id,),
            )
            try:
                from .message_center_service import create_teacher_grading_issue_notification

                create_teacher_grading_issue_notification(
                    conn,
                    int(submission_id),
                    issue_detail=error_message or "AI 批改任务未能进入队列，需要教师查看并处理。",
                    ref_suffix="grading_queue_failed",
                )
            except Exception as notify_exc:
                print(f"[AI_GRADING] queue failure notification failed: {notify_exc}")
            conn.commit()
    except Exception as exc:
        print(f"[AI_GRADING] failed to reset submission {submission_id} after queue failure: {exc}")