from __future__ import annotations

import asyncio
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from ..core import ai_client
from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from ..db.errors import DatabaseProgrammingError
from .chat_image_derivatives import (
    CHAT_IMAGE_DERIVATIVE_MIME_TYPE,
    CHAT_IMAGE_TYPES,
    ChatImageDerivativeError,
    ChatImageTooLargeError,
    build_chat_image_derivative_sync,
    prepare_chat_image_derivatives,
    run_chat_image_processing,
)
from .file_service import resolve_global_file_path, save_file_globally
from .psych_profile_service import (
    build_explicit_user_profile_prompt,
    compose_classroom_chat_system_prompt,
    load_ai_class_config,
    load_explicit_user_profile,
    load_latest_hidden_profile,
    sanitize_hidden_profile_leaks,
)
from .student_support_service import build_student_support_signal_prompt
from .academic_service import build_classroom_ai_context
from .prompt_utils import build_time_context_text, polite_address
from .rate_limit_service import (
    RateLimitExceededError,
    build_rate_limit_window_start,
    calculate_retry_after_seconds,
)
from .email_notification_service import (
    SEVERITY_LABELS,
    notification_severity_for_category,
    queue_notification_email_if_applicable,
)

MESSAGE_CATEGORY_PRIVATE = "private_message"
MESSAGE_CATEGORY_ASSIGNMENT = "assignment"
MESSAGE_CATEGORY_DISCUSSION_MENTION = "discussion_mention"
MESSAGE_CATEGORY_SUBMISSION = "submission"
MESSAGE_CATEGORY_GRADING_RESULT = "grading_result"
MESSAGE_CATEGORY_AI_FEEDBACK = "ai_feedback"
MESSAGE_CATEGORY_LEARNING_PROGRESS = "learning_progress"
MESSAGE_CATEGORY_BLOG_COMMENT = "blog_comment"
MESSAGE_CATEGORY_BLOG_HOT = "blog_hot"
MESSAGE_CATEGORY_APP_FEEDBACK = "app_feedback"
MESSAGE_CATEGORY_PASSWORD_RESET = "password_reset_request"
MESSAGE_CATEGORY_TODO = "todo"
MESSAGE_CATEGORY_COLLABORATION = "collaboration"
MESSAGE_CATEGORY_ATTENDANCE_ALERT = "attendance_alert"
MESSAGE_CATEGORY_ACADEMIC_EXAM = "academic_exam"

AI_ASSISTANT_ROLE = "assistant"
AI_ASSISTANT_LABEL = "AI助教"
AI_REPLY_FALLBACK = "我在。刚才这条私信我已经收到，如果你愿意，我可以继续帮你把问题拆成更清楚的几个步骤。"
AI_REPLY_JOB_STATUS_PENDING = "pending"
AI_REPLY_JOB_STATUS_RUNNING = "running"
AI_REPLY_JOB_STATUS_COMPLETED = "completed"
AI_REPLY_JOB_STATUS_FAILED = "failed"
ACTIVE_AI_REPLY_JOB_STATUSES = {
    AI_REPLY_JOB_STATUS_PENDING,
    AI_REPLY_JOB_STATUS_RUNNING,
}

PRIVATE_MESSAGE_RATE_LIMIT = 5
PRIVATE_MESSAGE_RATE_WINDOW_SECONDS = 60
PRIVATE_MESSAGE_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024
PRIVATE_MESSAGE_ATTACHMENT_LIMIT = 8
PRIVATE_MESSAGE_IMAGE_TYPES = CHAT_IMAGE_TYPES
PRIVATE_MESSAGE_DERIVATIVE_MIME_TYPE = CHAT_IMAGE_DERIVATIVE_MIME_TYPE

PRIVATE_MESSAGE_ATTACHMENT_EXTRA_COLUMNS = {
    "image_width": "INTEGER",
    "image_height": "INTEGER",
    "thumbnail_file_hash": "TEXT",
    "thumbnail_mime_type": "TEXT",
    "thumbnail_file_size": "INTEGER NOT NULL DEFAULT 0",
    "thumbnail_width": "INTEGER",
    "thumbnail_height": "INTEGER",
    "preview_file_hash": "TEXT",
    "preview_mime_type": "TEXT",
    "preview_file_size": "INTEGER NOT NULL DEFAULT 0",
    "preview_width": "INTEGER",
    "preview_height": "INTEGER",
}

PRIVATE_MESSAGE_ATTACHMENT_REQUIRED_COLUMNS = (
    "id",
    "message_id",
    "conversation_key",
    "class_offering_id",
    "uploaded_by_identity",
    "uploaded_by_role",
    "file_hash",
    "original_filename",
    "mime_type",
    "file_size",
    "attachment_kind",
    "created_at",
    *PRIVATE_MESSAGE_ATTACHMENT_EXTRA_COLUMNS.keys(),
)

PRIVATE_MESSAGE_VARIANT_COLUMNS = {
    "thumbnail": {
        "hash": "thumbnail_file_hash",
        "mime_type": "thumbnail_mime_type",
        "file_size": "thumbnail_file_size",
        "width": "thumbnail_width",
        "height": "thumbnail_height",
    },
    "preview": {
        "hash": "preview_file_hash",
        "mime_type": "preview_mime_type",
        "file_size": "preview_file_size",
        "width": "preview_width",
        "height": "preview_height",
    },
}

_private_attachment_derivative_locks: dict[str, asyncio.Lock] = {}
_private_attachment_derivative_locks_guard = asyncio.Lock()

ALL_NOTIFICATION_CATEGORIES = (
    MESSAGE_CATEGORY_PRIVATE,
    MESSAGE_CATEGORY_ASSIGNMENT,
    MESSAGE_CATEGORY_DISCUSSION_MENTION,
    MESSAGE_CATEGORY_SUBMISSION,
    MESSAGE_CATEGORY_GRADING_RESULT,
    MESSAGE_CATEGORY_AI_FEEDBACK,
    MESSAGE_CATEGORY_LEARNING_PROGRESS,
    MESSAGE_CATEGORY_BLOG_COMMENT,
    MESSAGE_CATEGORY_BLOG_HOT,
    MESSAGE_CATEGORY_APP_FEEDBACK,
    MESSAGE_CATEGORY_PASSWORD_RESET,
    MESSAGE_CATEGORY_TODO,
    MESSAGE_CATEGORY_COLLABORATION,
    MESSAGE_CATEGORY_ATTENDANCE_ALERT,
    MESSAGE_CATEGORY_ACADEMIC_EXAM,
)

VISIBLE_NOTIFICATION_CATEGORIES = {
    "student": (
        "all",
        MESSAGE_CATEGORY_PRIVATE,
        MESSAGE_CATEGORY_ASSIGNMENT,
        MESSAGE_CATEGORY_DISCUSSION_MENTION,
        MESSAGE_CATEGORY_GRADING_RESULT,
        MESSAGE_CATEGORY_LEARNING_PROGRESS,
        MESSAGE_CATEGORY_BLOG_COMMENT,
        MESSAGE_CATEGORY_BLOG_HOT,
        MESSAGE_CATEGORY_TODO,
        MESSAGE_CATEGORY_COLLABORATION,
        MESSAGE_CATEGORY_ATTENDANCE_ALERT,
        MESSAGE_CATEGORY_ACADEMIC_EXAM,
    ),
    "teacher": (
        "all",
        MESSAGE_CATEGORY_PRIVATE,
        MESSAGE_CATEGORY_SUBMISSION,
        MESSAGE_CATEGORY_DISCUSSION_MENTION,
        MESSAGE_CATEGORY_AI_FEEDBACK,
        MESSAGE_CATEGORY_LEARNING_PROGRESS,
        MESSAGE_CATEGORY_BLOG_COMMENT,
        MESSAGE_CATEGORY_BLOG_HOT,
        MESSAGE_CATEGORY_APP_FEEDBACK,
        MESSAGE_CATEGORY_PASSWORD_RESET,
        MESSAGE_CATEGORY_TODO,
        MESSAGE_CATEGORY_COLLABORATION,
        MESSAGE_CATEGORY_ATTENDANCE_ALERT,
        MESSAGE_CATEGORY_ACADEMIC_EXAM,
    ),
}

CATEGORY_LABELS = {
    "all": "全部",
    MESSAGE_CATEGORY_PRIVATE: "私信",
    MESSAGE_CATEGORY_ASSIGNMENT: "作业通知",
    MESSAGE_CATEGORY_DISCUSSION_MENTION: "@提醒",
    MESSAGE_CATEGORY_SUBMISSION: "提交动态",
    MESSAGE_CATEGORY_GRADING_RESULT: "批改结果",
    MESSAGE_CATEGORY_AI_FEEDBACK: "AI反馈",
    MESSAGE_CATEGORY_LEARNING_PROGRESS: "学习成长",
    MESSAGE_CATEGORY_BLOG_COMMENT: "博客评论",
    MESSAGE_CATEGORY_BLOG_HOT: "博客热度",
    MESSAGE_CATEGORY_APP_FEEDBACK: "问题反馈",
    MESSAGE_CATEGORY_PASSWORD_RESET: "找回申请",
    MESSAGE_CATEGORY_TODO: "待办提醒",
    MESSAGE_CATEGORY_COLLABORATION: "小组协作",
    MESSAGE_CATEGORY_ATTENDANCE_ALERT: "考勤提醒",
    MESSAGE_CATEGORY_ACADEMIC_EXAM: "教务考试",
}

APP_FEEDBACK_TYPE_LABELS = {
    "bug": "Bug 修复反馈",
    "feature": "新功能建议",
    "report": "举报",
}

FILTER_LABELS = {
    "all": "全部",
    "unread": "仅未读",
    "important": "重要通知",
    "system": "系统通知",
    "normal": "普通通知",
    "today": "仅今日",
}

def _created_today_condition(column_sql: str = "created_at") -> str:
    if get_configured_db_engine() == "postgres":
        return f"{column_sql}::date = CURRENT_DATE"
    return f"date({column_sql}) = date('now', 'localtime')"


BROADCAST_DISCUSSION_TOKENS = (
    "@所有人",
    "@全体",
    "@all",
    "@All",
    "@ALL",
)

FORBIDDEN_AI_MARKERS = (
    "侧写",
    "画像",
    "后端分析",
    "系统提示",
    "隐藏提示",
    "内部分析",
    "画像更新",
)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate_text(text: Any, limit: int = 140) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _safe_json_loads(raw_value: Any, fallback: Any) -> Any:
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _parse_local_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _format_duration_label(started_at: Any, submitted_at: Any) -> str:
    submitted_dt = _parse_local_datetime(submitted_at)
    started_dt = _parse_local_datetime(started_at) or submitted_dt
    if not submitted_dt or not started_dt:
        return "待确认"
    total_seconds = max(0, int((submitted_dt - started_dt).total_seconds()))
    total_minutes = (total_seconds + 59) // 60 if total_seconds else 0
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}小时{minutes}分"


def _sanitize_student_notification_text(value: Any, *, limit: int = 140) -> str:
    lines = []
    for line in str(value or "").splitlines():
        if any(marker in line for marker in FORBIDDEN_AI_MARKERS):
            continue
        lines.append(line)
    text = " ".join(" ".join(lines).split())
    if any(marker in text for marker in FORBIDDEN_AI_MARKERS):
        return ""
    return _truncate_text(text, limit)


def _load_student_support_profile(conn, class_offering_id: Any, student_id: Any) -> str:
    normalized_offering_id = _safe_int(class_offering_id)
    normalized_student_id = _safe_int(student_id)
    if normalized_offering_id is None or normalized_student_id is None:
        return ""
    try:
        profile = load_latest_hidden_profile(conn, normalized_offering_id, normalized_student_id, "student")
    except Exception as exc:
        print(f"[MESSAGE_CENTER] student support profile skipped: {exc}")
        return ""
    if not profile:
        return ""
    parts = [
        profile.get("mental_state_summary"),
        profile.get("support_strategy"),
        profile.get("language_habit_summary"),
        profile.get("preferred_ai_style"),
        profile.get("interest_hypothesis") or profile.get("preference_summary"),
    ]
    return " ".join(str(item or "").strip() for item in parts if str(item or "").strip())


def _student_support_sentence(profile_text: str, *, event: str) -> str:
    text = str(profile_text or "")
    stress_tokens = ("焦虑", "紧张", "压力", "挫败", "自责", "不安", "害怕", "敏感")
    concise_tokens = ("简洁", "直接", "高效", "少废话", "要点")
    confidence_tokens = ("主动", "挑战", "自信", "好胜", "探索", "投入")
    if any(token in text for token in stress_tokens):
        if event == "progress":
            return "先别急着给自己加码，这一步已经很扎实；按自己的节奏往前走就好。"
        return "先别只盯着分数，反馈是帮你找到下一步的，不是给你贴标签。"
    if any(token in text for token in concise_tokens):
        if event == "progress":
            return "新的进度已经点亮，重点看下一步要做什么。"
        return "结果已更新，点开看得分、扣分点和下一步建议。"
    if any(token in text for token in confidence_tokens):
        if event == "progress":
            return "这一步走得很有劲，新的阶段已经向你打开。"
        return "这次反馈已经到位，看看哪些地方可以继续拔高。"
    if event == "progress":
        return "这一步值得认真记下，也值得给自己一点肯定。"
    return "这份反馈已经准备好，点开慢慢看，先抓住最有用的一两条建议。"


def _personalize_student_grading_body(conn, submission: dict[str, Any], score_text: str, feedback_preview: str) -> str:
    profile_text = _load_student_support_profile(
        conn,
        submission.get("class_offering_id"),
        submission.get("student_pk_id"),
    )
    support_sentence = _student_support_sentence(profile_text, event="grading")
    parts = [support_sentence]
    if score_text:
        parts.append(f"本次{score_text}。")
    if feedback_preview:
        parts.append(f"老师的反馈摘要：{feedback_preview}")
    return _sanitize_student_notification_text(" ".join(parts), limit=140)


def _personalize_student_learning_body(
    conn,
    *,
    class_offering_id: Any,
    student_id: Any,
    body_preview: str,
) -> str:
    profile_text = _load_student_support_profile(conn, class_offering_id, student_id)
    support_sentence = _student_support_sentence(profile_text, event="progress")
    base = _sanitize_student_notification_text(body_preview, limit=96)
    message = f"{support_sentence} {base}".strip() if base else support_sentence
    return _sanitize_student_notification_text(message, limit=140)


def ensure_private_message_attachment_schema(conn) -> None:
    db_engine = get_configured_db_engine()
    if db_engine == "postgres":
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = ?
              AND table_name = ?
            """,
            ("public", "private_message_attachments"),
        ).fetchall()
        columns = {
            str(row["column_name"] if hasattr(row, "keys") and "column_name" in row.keys() else row[0])
            for row in rows
        }
        missing = [column for column in PRIVATE_MESSAGE_ATTACHMENT_REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise DatabaseProgrammingError(
                "PostgreSQL private_message_attachments schema validation failed; missing columns: "
                + ", ".join(missing)
            )
        return
    if db_engine != "sqlite":
        raise ValueError(f"Unsupported private message attachment database engine: {db_engine!r}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS private_message_attachments
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            conversation_key TEXT NOT NULL,
            class_offering_id INTEGER,
            uploaded_by_identity TEXT NOT NULL,
            uploaded_by_role TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            attachment_kind TEXT NOT NULL DEFAULT 'file',
            image_width INTEGER,
            image_height INTEGER,
            thumbnail_file_hash TEXT,
            thumbnail_mime_type TEXT,
            thumbnail_file_size INTEGER NOT NULL DEFAULT 0,
            thumbnail_width INTEGER,
            thumbnail_height INTEGER,
            preview_file_hash TEXT,
            preview_mime_type TEXT,
            preview_file_size INTEGER NOT NULL DEFAULT 0,
            preview_width INTEGER,
            preview_height INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (message_id) REFERENCES private_messages (id) ON DELETE CASCADE,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL
        )
        """
    )
    existing_columns = {
        str(row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1])
        for row in conn.execute("PRAGMA table_info(private_message_attachments)").fetchall()
    }
    for column_name, column_type in PRIVATE_MESSAGE_ATTACHMENT_EXTRA_COLUMNS.items():
        if column_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE private_message_attachments ADD COLUMN {column_name} {column_type}")
            except Exception:
                pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_attachments_message "
        "ON private_message_attachments (message_id, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_private_message_attachments_conversation "
        "ON private_message_attachments (conversation_key, created_at DESC, id DESC)"
    )


def _detect_upload_size(file: Any) -> int | None:
    try:
        file.file.seek(0, 2)
        size = int(file.file.tell())
        file.file.seek(0)
        return size
    except Exception:
        return None


def _normalize_attachment_filename(raw_name: Any) -> str:
    normalized = str(raw_name or "attachment").replace("\\", "/").split("/")[-1].strip()
    normalized = normalized.replace("\x00", "")
    if not normalized:
        normalized = "attachment"
    return normalized[:180]


def _guess_attachment_mime(file: Any, filename: str) -> str:
    content_type = str(getattr(file, "content_type", "") or "").strip().lower()
    if content_type and content_type != "application/octet-stream":
        return content_type
    guessed_type = mimetypes.guess_type(filename)[0]
    return str(guessed_type or "application/octet-stream").lower()


def _private_attachment_kind(mime_type: str) -> str:
    return "image" if str(mime_type or "").lower() in PRIVATE_MESSAGE_IMAGE_TYPES else "file"


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


async def _get_private_attachment_derivative_lock(attachment_id: int, variant: str) -> asyncio.Lock:
    key = f"{int(attachment_id)}:{variant}"
    async with _private_attachment_derivative_locks_guard:
        lock = _private_attachment_derivative_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _private_attachment_derivative_locks[key] = lock
        return lock


async def prepare_private_message_uploads(files: list[Any] | tuple[Any, ...] | None) -> list[dict[str, Any]]:
    selected_files = [file for file in (files or []) if file is not None]
    if not selected_files:
        return []
    if len(selected_files) > PRIVATE_MESSAGE_ATTACHMENT_LIMIT:
        raise ValueError(f"单条私信最多只能发送 {PRIVATE_MESSAGE_ATTACHMENT_LIMIT} 个附件")

    prepared: list[dict[str, Any]] = []
    for file in selected_files:
        filename = _normalize_attachment_filename(getattr(file, "filename", "") or "attachment")
        detected_size = _detect_upload_size(file)
        if detected_size is not None and detected_size > PRIVATE_MESSAGE_ATTACHMENT_MAX_BYTES:
            raise ValueError("私信附件不能超过 100MB")

        mime_type = _guess_attachment_mime(file, filename)
        save_result = await save_file_globally(file)
        if not save_result:
            raise ValueError(f"附件 {filename} 保存失败")

        saved_size = int(save_result.get("size") or 0)
        if saved_size > PRIVATE_MESSAGE_ATTACHMENT_MAX_BYTES:
            raise ValueError("私信附件不能超过 100MB")

        attachment_kind = _private_attachment_kind(mime_type)
        derivative_payload = None
        if attachment_kind == "image":
            try:
                derivative_payload = await prepare_chat_image_derivatives(Path(save_result["path"]))
            except ChatImageTooLargeError as exc:
                raise ValueError(f"Image {filename} dimensions are too large") from exc
            except ChatImageDerivativeError as exc:
                raise ValueError(f"Image {filename} is invalid") from exc

        prepared_item = {
            "file_hash": str(save_result["hash"]),
            "original_filename": filename,
            "mime_type": mime_type,
            "file_size": saved_size,
            "attachment_kind": attachment_kind,
        }
        if derivative_payload:
            thumbnail = derivative_payload["thumbnail"]
            preview = derivative_payload["preview"]
            prepared_item.update({
                "image_width": int(derivative_payload["width"] or 0),
                "image_height": int(derivative_payload["height"] or 0),
                "thumbnail_file_hash": str(thumbnail["file_hash"]),
                "thumbnail_mime_type": str(thumbnail["mime_type"]),
                "thumbnail_file_size": int(thumbnail["file_size"] or 0),
                "thumbnail_width": int(thumbnail["width"] or 0),
                "thumbnail_height": int(thumbnail["height"] or 0),
                "preview_file_hash": str(preview["file_hash"]),
                "preview_mime_type": str(preview["mime_type"]),
                "preview_file_size": int(preview["file_size"] or 0),
                "preview_width": int(preview["width"] or 0),
                "preview_height": int(preview["height"] or 0),
            })
        prepared.append(prepared_item)
    return prepared


def _build_private_attachment_payload(row) -> dict[str, Any]:
    attachment_id = int(row["id"])
    mime_type = str(row["mime_type"] or "application/octet-stream")
    kind = str(row["attachment_kind"] or _private_attachment_kind(mime_type))
    base_url = _private_attachment_variant_url(attachment_id, "")
    original_url = _private_attachment_variant_url(attachment_id, "original")
    thumbnail_url = _private_attachment_variant_url(attachment_id, "thumbnail")
    preview_url = _private_attachment_variant_url(attachment_id, "preview")
    payload = {
        "attachment_id": attachment_id,
        "id": attachment_id,
        "type": kind,
        "is_image": kind == "image",
        "name": str(row["original_filename"] or "attachment"),
        "mime_type": mime_type,
        "file_size": int(row["file_size"] or 0),
        "width": int(_row_value(row, "image_width", 0) or 0),
        "height": int(_row_value(row, "image_height", 0) or 0),
        "url": base_url,
        "download_url": f"{base_url}?download=1",
        "created_at": str(row["created_at"] or ""),
    }
    if kind == "image":
        payload.update({
            "url": thumbnail_url,
            "thumbnail_url": thumbnail_url,
            "thumbnail_file_size": int(_row_value(row, "thumbnail_file_size", 0) or 0),
            "thumbnail_width": int(_row_value(row, "thumbnail_width", 0) or 0),
            "thumbnail_height": int(_row_value(row, "thumbnail_height", 0) or 0),
            "preview_url": preview_url,
            "preview_file_size": int(_row_value(row, "preview_file_size", 0) or 0),
            "preview_width": int(_row_value(row, "preview_width", 0) or 0),
            "preview_height": int(_row_value(row, "preview_height", 0) or 0),
            "original_url": original_url,
            "download_url": f"{original_url}?download=1",
        })
    return payload


def _private_attachment_variant_url(attachment_id: int, variant: str) -> str:
    base_url = f"/api/message-center/private/attachments/{int(attachment_id)}"
    normalized_variant = str(variant or "").strip().lower()
    return f"{base_url}/{normalized_variant}" if normalized_variant else base_url


def _resolve_private_original_file_payload(row) -> dict[str, Any] | None:
    file_path = resolve_global_file_path(str(row["file_hash"]))
    if not file_path:
        return None
    mime_type = str(row["mime_type"] or "application/octet-stream")
    kind = str(row["attachment_kind"] or _private_attachment_kind(mime_type))
    return {
        "path": file_path,
        "mime_type": mime_type,
        "file_size": int(row["file_size"] or 0),
        "filename": str(row["original_filename"] or "attachment"),
        "attachment_kind": kind,
        "width": int(_row_value(row, "image_width", 0) or 0),
        "height": int(_row_value(row, "image_height", 0) or 0),
        "variant": "original",
    }


def _resolve_private_variant_file_payload(row, variant: str) -> dict[str, Any] | None:
    if str(row["attachment_kind"] or _private_attachment_kind(str(row["mime_type"] or ""))) != "image":
        return None
    columns = PRIVATE_MESSAGE_VARIANT_COLUMNS.get(variant)
    if not columns:
        return None

    file_hash = str(_row_value(row, columns["hash"], "") or "").strip()
    if not file_hash:
        return None
    file_path = resolve_global_file_path(file_hash)
    if not file_path:
        return None

    original_name = str(row["original_filename"] or "image")
    stem = Path(original_name).stem or "image"
    suffix = "thumb" if variant == "thumbnail" else "preview"
    return {
        "path": file_path,
        "mime_type": str(
            _row_value(row, columns["mime_type"], PRIVATE_MESSAGE_DERIVATIVE_MIME_TYPE)
            or PRIVATE_MESSAGE_DERIVATIVE_MIME_TYPE
        ),
        "file_size": int(_row_value(row, columns["file_size"], 0) or file_path.stat().st_size),
        "filename": f"{stem}-{suffix}.jpg",
        "attachment_kind": "image",
        "width": int(_row_value(row, columns["width"], 0) or 0),
        "height": int(_row_value(row, columns["height"], 0) or 0),
        "variant": variant,
    }


def resolve_private_message_attachment_file_payload(row, variant: str) -> dict[str, Any] | None:
    normalized_variant = str(variant or "original").lower()
    if normalized_variant == "original":
        return _resolve_private_original_file_payload(row)
    if normalized_variant in PRIVATE_MESSAGE_VARIANT_COLUMNS:
        return _resolve_private_variant_file_payload(row, normalized_variant)
    return None


def _load_private_message_attachments(conn, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    normalized_ids = [int(message_id) for message_id in message_ids if int(message_id or 0) > 0]
    if not normalized_ids:
        return {}
    ensure_private_message_attachment_schema(conn)
    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM private_message_attachments
        WHERE message_id IN ({placeholders})
        ORDER BY message_id ASC, id ASC
        """,
        tuple(normalized_ids),
    ).fetchall()
    attachments: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        message_id = int(row["message_id"])
        attachments.setdefault(message_id, []).append(_build_private_attachment_payload(row))
    return attachments


def _format_private_attachment_preview(attachment_count: int) -> str:
    count = max(int(attachment_count or 0), 0)
    if count <= 0:
        return ""
    return f"{count} 个附件"


def _private_message_preview(content: Any, attachment_count: int = 0, limit: int = 90) -> str:
    text_preview = _truncate_text(content, limit)
    attachment_preview = _format_private_attachment_preview(attachment_count)
    if text_preview and attachment_preview:
        return f"{text_preview} | {attachment_preview}"
    return text_preview or attachment_preview or "私信"


def _enforce_private_message_rate_limit(conn, *, sender_identity: str) -> None:
    now, window_start = build_rate_limit_window_start(window_seconds=PRIVATE_MESSAGE_RATE_WINDOW_SECONDS)
    rows = conn.execute(
        """
        SELECT id, created_at
        FROM private_messages
        WHERE sender_identity = ?
          AND created_at >= ?
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (
            str(sender_identity),
            window_start,
            PRIVATE_MESSAGE_RATE_LIMIT,
        ),
    ).fetchall()
    if len(rows) < PRIVATE_MESSAGE_RATE_LIMIT:
        return

    retry_after_seconds = calculate_retry_after_seconds(
        oldest_event_at=rows[0]["created_at"],
        window_seconds=PRIVATE_MESSAGE_RATE_WINDOW_SECONDS,
        now=now,
    )
    raise RateLimitExceededError(
        "\u53d1\u4fe1\u592a\u9891\u7e41\u8bf7\u7b49\u4e00\u7b49\u518d\u53d1\u9001",
        retry_after_seconds=retry_after_seconds,
    )


def build_user_identity(role: str, user_pk: int | str) -> str:
    normalized_role = str(role or "").strip().lower()
    normalized_pk = _safe_int(user_pk)
    if normalized_role not in {"student", "teacher"} or normalized_pk is None:
        raise ValueError("invalid user identity")
    return f"{normalized_role}:{normalized_pk}"


def build_ai_identity(class_offering_id: int | str) -> str:
    normalized_offering_id = _safe_int(class_offering_id)
    if normalized_offering_id is None:
        raise ValueError("invalid assistant identity")
    return f"{AI_ASSISTANT_ROLE}:{normalized_offering_id}"


def parse_identity(identity: str) -> dict[str, Any]:
    normalized = str(identity or "").strip()
    if not normalized or ":" not in normalized:
        raise ValueError("invalid identity")
    role, raw_value = normalized.split(":", 1)
    role = role.strip().lower()
    value = _safe_int(raw_value)
    if role == AI_ASSISTANT_ROLE:
        if value is None:
            raise ValueError("invalid assistant identity")
        return {
            "identity": normalized,
            "role": AI_ASSISTANT_ROLE,
            "user_pk": None,
            "class_offering_id": value,
        }
    if role not in {"student", "teacher"} or value is None:
        raise ValueError("invalid identity")
    return {
        "identity": normalized,
        "role": role,
        "user_pk": value,
        "class_offering_id": None,
    }


def build_contact_key(identity: str, class_offering_id: Optional[int]) -> str:
    return f"{identity}|scope:{int(class_offering_id or 0)}"


def build_conversation_key(identity_a: str, identity_b: str, class_offering_id: Optional[int]) -> str:
    first, second = sorted((identity_a, identity_b))
    return f"{first}|{second}|scope:{int(class_offering_id or 0)}"


def build_message_center_link(contact_identity: Optional[str] = None, class_offering_id: Optional[int] = None) -> str:
    if not contact_identity:
        return "/profile?section=notifications"
    params = [f"section=private", f"tab={quote(MESSAGE_CATEGORY_PRIVATE)}", f"contact={quote(contact_identity)}"]
    if class_offering_id:
        params.append(f"scope={int(class_offering_id)}")
    return "/profile?" + "&".join(params)


def build_actor_display_name(name: str, role: str) -> str:
    normalized_name = str(name or "").strip()
    normalized_role = str(role or "").strip().lower()
    if normalized_role == AI_ASSISTANT_ROLE:
        return AI_ASSISTANT_LABEL
    if normalized_role == "teacher" and normalized_name:
        return normalized_name if normalized_name.endswith("老师") else f"{normalized_name} 老师"
    return normalized_name or ("老师" if normalized_role == "teacher" else "课堂成员")


def is_blockable_role(role: str) -> bool:
    return str(role or "").strip().lower() not in {AI_ASSISTANT_ROLE, "teacher"}


def get_visible_categories(user_role: str, *, include_private: bool = True) -> tuple[str, ...]:
    categories = VISIBLE_NOTIFICATION_CATEGORIES.get(str(user_role or "").strip().lower(), ("all", MESSAGE_CATEGORY_PRIVATE))
    if include_private:
        return categories
    return tuple(category for category in categories if category != MESSAGE_CATEGORY_PRIVATE)


def _ensure_user_identity(user: dict) -> tuple[int, str, str]:
    user_pk = _safe_int(user.get("id"))
    role = str(user.get("role") or "").strip().lower()
    if user_pk is None or role not in {"student", "teacher"}:
        raise ValueError("invalid current user")
    return user_pk, role, build_user_identity(role, user_pk)


def _load_offering_labels(conn, offering_ids: set[int]) -> dict[int, dict[str, str]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT o.id, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.id IN ({placeholders})
        """,
        tuple(sorted(offering_ids)),
    ).fetchall()
    labels: dict[int, dict[str, str]] = {}
    for row in rows:
        labels[int(row["id"])] = {
            "course_name": str(row["course_name"] or ""),
            "class_name": str(row["class_name"] or ""),
        }
    return labels


def _load_accessible_classroom_private_scope(conn, user: dict, class_offering_id: int):
    current_user_pk, current_role, _ = _ensure_user_identity(user)
    offering = conn.execute(
        """
        SELECT o.id, o.class_id, o.teacher_id, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if offering is None:
        return None

    if current_role == "teacher":
        return offering if int(offering["teacher_id"]) == current_user_pk else None

    student_row = conn.execute(
        """
        SELECT class_id
        FROM students
        WHERE id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        LIMIT 1
        """,
        (current_user_pk,),
    ).fetchone()
    if student_row is None or int(student_row["class_id"]) != int(offering["class_id"]):
        return None
    return offering


def _load_scoped_student_contact(conn, student_id: int, class_offering_id: Optional[int]) -> Optional[dict[str, Any]]:
    if class_offering_id is None:
        return None
    row = conn.execute(
        """
        SELECT s.id, s.name, s.student_id_number,
               o.id AS class_offering_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id = ?
          AND s.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id)),
    ).fetchone()
    if row is None:
        return None
    subtitle_parts = [
        f"{row['course_name']} / {row['class_name']}",
        "本班同学",
    ]
    if row["student_id_number"]:
        subtitle_parts.append(f"学号 {row['student_id_number']}")
    return _build_contact_entry(
        identity=build_user_identity("student", row["id"]),
        role="student",
        display_name=str(row["name"] or "同学"),
        subtitle=" · ".join(subtitle_parts),
        class_offering_id=int(row["class_offering_id"]),
        user_pk=int(row["id"]),
        can_send=True,
    )


def _load_scoped_teacher_contact(conn, teacher_id: int, class_offering_id: Optional[int]) -> Optional[dict[str, Any]]:
    if class_offering_id is None:
        return None
    row = conn.execute(
        """
        SELECT t.id, t.name,
               o.id AS class_offering_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
          AND t.id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        return None
    return _build_contact_entry(
        identity=build_user_identity("teacher", row["id"]),
        role="teacher",
        display_name=build_actor_display_name(str(row["name"] or ""), "teacher"),
        subtitle=f"{row['course_name']} / {row['class_name']}",
        class_offering_id=int(row["class_offering_id"]),
        user_pk=int(row["id"]),
        can_send=True,
    )


def _build_contact_entry(
    *,
    identity: str,
    role: str,
    display_name: str,
    subtitle: str,
    class_offering_id: Optional[int],
    user_pk: Optional[int],
    can_send: bool = True,
) -> dict[str, Any]:
    return {
        "contact_key": build_contact_key(identity, class_offering_id),
        "identity": identity,
        "role": role,
        "display_name": display_name,
        "subtitle": subtitle,
        "class_offering_id": class_offering_id,
        "user_pk": user_pk,
        "can_send": can_send,
        "can_block": is_blockable_role(role),
        "is_blocked": False,
        "is_available": can_send,
        "unread_count": 0,
        "last_message_preview": "",
        "last_message_at": "",
        "last_message_is_outgoing": False,
    }


def _register_contact(catalog: dict[str, dict[str, Any]], contact: dict[str, Any]) -> None:
    catalog[contact["contact_key"]] = contact


def _load_student_contact_catalog(conn, student_id: int) -> dict[str, dict[str, Any]]:
    student_row = conn.execute(
        """
        SELECT s.id, s.name, s.class_id, c.name AS class_name
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    if not student_row:
        return {}

    class_id = int(student_row["class_id"])
    class_name = str(student_row["class_name"] or "")
    catalog: dict[str, dict[str, Any]] = {}

    classmates = conn.execute(
        """
        SELECT id, name, student_id_number
        FROM students
        WHERE class_id = ? AND id != ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        ORDER BY student_id_number, id
        """,
        (class_id, student_id),
    ).fetchall()
    for row in classmates:
        identity = build_user_identity("student", row["id"])
        _register_contact(
            catalog,
            _build_contact_entry(
                identity=identity,
                role="student",
                display_name=str(row["name"] or "同学"),
                subtitle=f"同班同学 · {class_name}",
                class_offering_id=None,
                user_pk=int(row["id"]),
            ),
        )

    offerings = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               c.name AS course_name,
               cl.name AS class_name,
               t.id AS teacher_id,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.class_id = ?
        ORDER BY c.name, o.id
        """,
        (class_id,),
    ).fetchall()
    for row in offerings:
        class_offering_id = int(row["class_offering_id"])
        subtitle = f"{row['course_name']} / {row['class_name']}"
        teacher_identity = build_user_identity("teacher", row["teacher_id"])
        _register_contact(
            catalog,
            _build_contact_entry(
                identity=teacher_identity,
                role="teacher",
                display_name=build_actor_display_name(str(row["teacher_name"] or ""), "teacher"),
                subtitle=subtitle,
                class_offering_id=class_offering_id,
                user_pk=int(row["teacher_id"]),
            ),
        )
        assistant_identity = build_ai_identity(class_offering_id)
        _register_contact(
            catalog,
            _build_contact_entry(
                identity=assistant_identity,
                role=AI_ASSISTANT_ROLE,
                display_name=f"{AI_ASSISTANT_LABEL} · {row['course_name']}",
                subtitle=subtitle,
                class_offering_id=class_offering_id,
                user_pk=None,
            ),
        )
    return catalog


def _load_teacher_contact_catalog(conn, teacher_id: int) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    offerings = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               c.name AS course_name,
               cl.name AS class_name,
               s.id AS student_id,
               s.name AS student_name,
               s.student_id_number
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN students s ON s.class_id = o.class_id
        WHERE o.teacher_id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY c.name, cl.name, s.student_id_number, s.id
        """,
        (teacher_id,),
    ).fetchall()
    seen_assistants: set[int] = set()
    for row in offerings:
        class_offering_id = int(row["class_offering_id"])
        subtitle = f"{row['course_name']} / {row['class_name']} · 学号 {row['student_id_number']}"
        student_identity = build_user_identity("student", row["student_id"])
        _register_contact(
            catalog,
            _build_contact_entry(
                identity=student_identity,
                role="student",
                display_name=str(row["student_name"] or "学生"),
                subtitle=subtitle,
                class_offering_id=class_offering_id,
                user_pk=int(row["student_id"]),
            ),
        )
        if class_offering_id in seen_assistants:
            continue
        seen_assistants.add(class_offering_id)
        assistant_identity = build_ai_identity(class_offering_id)
        _register_contact(
            catalog,
            _build_contact_entry(
                identity=assistant_identity,
                role=AI_ASSISTANT_ROLE,
                display_name=f"{AI_ASSISTANT_LABEL} · {row['course_name']}",
                subtitle=f"{row['course_name']} / {row['class_name']}",
                class_offering_id=class_offering_id,
                user_pk=None,
            ),
        )
    return catalog


def load_private_message_contact_catalog(conn, user: dict) -> dict[str, dict[str, Any]]:
    user_pk, role, _ = _ensure_user_identity(user)
    if role == "student":
        return _load_student_contact_catalog(conn, user_pk)
    return _load_teacher_contact_catalog(conn, user_pk)


def _load_blocked_identity_map(conn, owner_identity: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT blocked_identity, blocked_role, blocked_user_pk, blocked_display_name, created_at
        FROM private_message_blocks
        WHERE owner_identity = ?
        ORDER BY created_at DESC, id DESC
        """,
        (owner_identity,),
    ).fetchall()
    blocked: dict[str, dict[str, Any]] = {}
    for row in rows:
        blocked[str(row["blocked_identity"])] = {
            "identity": str(row["blocked_identity"]),
            "role": str(row["blocked_role"] or ""),
            "user_pk": _safe_int(row["blocked_user_pk"]),
            "display_name": str(row["blocked_display_name"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
    return blocked


def _merge_private_message_summaries(
    conn,
    *,
    current_identity: str,
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ensure_private_message_attachment_schema(conn)
    rows = conn.execute(
        """
        SELECT pm.id, pm.class_offering_id,
               pm.sender_identity, pm.sender_role, pm.sender_user_pk, pm.sender_display_name,
               pm.recipient_identity, pm.recipient_role, pm.recipient_user_pk, pm.recipient_display_name,
               pm.content, pm.read_at, pm.created_at,
               COALESCE((
                   SELECT COUNT(*)
                   FROM private_message_attachments pma
                   WHERE pma.message_id = pm.id
               ), 0) AS attachment_count
        FROM private_messages pm
        WHERE pm.sender_identity = ? OR pm.recipient_identity = ?
        ORDER BY pm.created_at DESC, pm.id DESC
        """,
        (current_identity, current_identity),
    ).fetchall()
    offering_ids = {
        int(row["class_offering_id"])
        for row in rows
        if _safe_int(row["class_offering_id"]) is not None
    }
    offering_labels = _load_offering_labels(conn, offering_ids)

    for row in rows:
        is_outgoing = str(row["sender_identity"]) == current_identity
        contact_identity = str(row["recipient_identity"] if is_outgoing else row["sender_identity"])
        contact_role = str(row["recipient_role"] if is_outgoing else row["sender_role"])
        contact_user_pk = _safe_int(row["recipient_user_pk"] if is_outgoing else row["sender_user_pk"])
        contact_name = str(row["recipient_display_name"] if is_outgoing else row["sender_display_name"] or "")
        class_offering_id = _safe_int(row["class_offering_id"])
        contact_key = build_contact_key(contact_identity, class_offering_id)

        if contact_key not in catalog:
            subtitle = "历史联系人"
            if class_offering_id and class_offering_id in offering_labels:
                label = offering_labels[class_offering_id]
                subtitle = f"{label['course_name']} / {label['class_name']}"
            catalog[contact_key] = _build_contact_entry(
                identity=contact_identity,
                role=contact_role,
                display_name=contact_name or ("未知联系人" if contact_role != AI_ASSISTANT_ROLE else AI_ASSISTANT_LABEL),
                subtitle=subtitle,
                class_offering_id=class_offering_id,
                user_pk=contact_user_pk,
                can_send=False,
            )

        contact = catalog[contact_key]
        if not contact["last_message_at"]:
            contact["last_message_at"] = str(row["created_at"] or "")
            contact["last_message_preview"] = _private_message_preview(
                row["content"],
                int(row["attachment_count"] or 0),
                72,
            )
            contact["last_message_is_outgoing"] = is_outgoing
        if not is_outgoing and not row["read_at"]:
            contact["unread_count"] += 1

    return list(catalog.values())


def list_private_message_contacts(conn, user: dict) -> list[dict[str, Any]]:
    _, _, current_identity = _ensure_user_identity(user)
    ensure_private_message_attachment_schema(conn)
    catalog = load_private_message_contact_catalog(conn, user)
    blocked_map = _load_blocked_identity_map(conn, current_identity)
    contacts = _merge_private_message_summaries(conn, current_identity=current_identity, catalog=catalog)
    for contact in contacts:
        blocked_info = blocked_map.get(contact["identity"])
        contact["is_blocked"] = blocked_info is not None
        if blocked_info and not contact["display_name"]:
            contact["display_name"] = blocked_info["display_name"] or contact["display_name"]
    contacts.sort(key=lambda item: str(item["display_name"] or ""))
    contacts.sort(key=lambda item: str(item["last_message_at"] or ""), reverse=True)
    contacts.sort(key=lambda item: 0 if item["last_message_at"] else 1)
    contacts.sort(key=lambda item: 0 if item["unread_count"] > 0 else 1)
    return contacts


def _resolve_contact(
    conn,
    *,
    user: dict,
    contact_identity: str,
    class_offering_id: Optional[int],
) -> Optional[dict[str, Any]]:
    normalized_scope = class_offering_id
    parsed_identity = parse_identity(contact_identity)
    if parsed_identity["role"] == AI_ASSISTANT_ROLE and normalized_scope is None:
        normalized_scope = int(parsed_identity["class_offering_id"])
    if normalized_scope is not None and parsed_identity["role"] in {"student", "teacher"}:
        accessible_scope = _load_accessible_classroom_private_scope(conn, user, int(normalized_scope))
        if accessible_scope is None:
            return None
        scoped_contact = _resolve_direct_user_contact(
            conn,
            contact_identity,
            class_offering_id=int(accessible_scope["id"]),
        )
        if scoped_contact and scoped_contact["identity"] != _ensure_user_identity(user)[2]:
            return scoped_contact
    for contact in list_private_message_contacts(conn, user):
        if contact["identity"] == contact_identity and int(contact["class_offering_id"] or 0) == int(normalized_scope or 0):
            return contact
    direct_contact = _resolve_direct_user_contact(conn, contact_identity, class_offering_id=normalized_scope)
    if direct_contact and direct_contact["identity"] != _ensure_user_identity(user)[2]:
        return direct_contact
    return None


def _resolve_direct_user_contact(
    conn,
    contact_identity: str,
    *,
    class_offering_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    try:
        parsed = parse_identity(contact_identity)
    except ValueError:
        return None

    role = str(parsed.get("role") or "")
    if role == AI_ASSISTANT_ROLE:
        return None
    if role == "student":
        scoped_contact = _load_scoped_student_contact(conn, int(parsed["user_pk"]), class_offering_id)
        if scoped_contact is not None:
            return scoped_contact
        row = conn.execute(
            """
            SELECT s.id, s.name, s.student_id_number, c.name AS class_name
            FROM students s
            LEFT JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            LIMIT 1
            """,
            (parsed["user_pk"],),
        ).fetchone()
        if row is None:
            return None
        subtitle_parts = ["跨班联系人"]
        if row["class_name"]:
            subtitle_parts.append(str(row["class_name"]))
        if row["student_id_number"]:
            subtitle_parts.append(f"学号 {row['student_id_number']}")
        return _build_contact_entry(
            identity=build_user_identity("student", row["id"]),
            role="student",
            display_name=str(row["name"] or "同学"),
            subtitle=" · ".join(subtitle_parts),
            class_offering_id=class_offering_id,
            user_pk=int(row["id"]),
            can_send=True,
        )

    if role == "teacher":
        scoped_contact = _load_scoped_teacher_contact(conn, int(parsed["user_pk"]), class_offering_id)
        if scoped_contact is not None:
            return scoped_contact
        row = conn.execute(
            """
            SELECT id, name
            FROM teachers
            WHERE id = ?
            LIMIT 1
            """,
            (parsed["user_pk"],),
        ).fetchone()
        if row is None:
            return None
        return _build_contact_entry(
            identity=build_user_identity("teacher", row["id"]),
            role="teacher",
            display_name=build_actor_display_name(str(row["name"] or ""), "teacher"),
            subtitle="教师",
            class_offering_id=class_offering_id,
            user_pk=int(row["id"]),
            can_send=True,
        )

    return None


def list_classroom_private_message_contacts(conn, user: dict, class_offering_id: int) -> dict[str, Any]:
    current_user_pk, current_role, current_identity = _ensure_user_identity(user)
    offering = _load_accessible_classroom_private_scope(conn, user, int(class_offering_id))
    if offering is None:
        raise PermissionError("permission denied")

    students = conn.execute(
        """
        SELECT id, name, student_id_number
        FROM students
        WHERE class_id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        ORDER BY student_id_number, id
        """,
        (int(offering["class_id"]),),
    ).fetchall()
    blocked_map = _load_blocked_identity_map(conn, current_identity)
    contact_by_key: dict[str, dict[str, Any]] = {}

    if current_role == "student":
        teacher_contact = _load_scoped_teacher_contact(
            conn,
            int(offering["teacher_id"]),
            int(offering["id"]),
        )
        if teacher_contact is not None and teacher_contact["identity"] != current_identity:
            teacher_contact["subtitle"] = f"{teacher_contact['subtitle']} · 任课教师"
            contact_by_key[teacher_contact["contact_key"]] = teacher_contact

    for row in students:
        identity = build_user_identity("student", row["id"])
        if identity == current_identity:
            continue
        subtitle_parts = [
            f"{offering['course_name']} / {offering['class_name']}",
            "本班同学",
        ]
        if row["student_id_number"]:
            subtitle_parts.append(f"学号 {row['student_id_number']}")
        contact = _build_contact_entry(
            identity=identity,
            role="student",
            display_name=str(row["name"] or "同学"),
            subtitle=" · ".join(subtitle_parts),
            class_offering_id=int(offering["id"]),
            user_pk=int(row["id"]),
            can_send=True,
        )
        if identity in blocked_map:
            contact["is_blocked"] = True
            contact["can_send"] = False
        contact["contact_key"] = build_contact_key(identity, int(offering["id"]))
        contact["identity"] = identity
        contact["class_offering_id"] = int(offering["id"])
        contact_by_key[contact["contact_key"]] = contact

    ensure_private_message_attachment_schema(conn)
    rows = conn.execute(
        """
        SELECT pm.id, pm.class_offering_id,
               pm.sender_identity, pm.recipient_identity,
               pm.content, pm.read_at, pm.created_at,
               COALESCE((
                   SELECT COUNT(*)
                   FROM private_message_attachments pma
                   WHERE pma.message_id = pm.id
               ), 0) AS attachment_count
        FROM private_messages pm
        WHERE pm.class_offering_id = ?
          AND (pm.sender_identity = ? OR pm.recipient_identity = ?)
        ORDER BY pm.created_at DESC, pm.id DESC
        """,
        (int(offering["id"]), current_identity, current_identity),
    ).fetchall()
    for row in rows:
        is_outgoing = str(row["sender_identity"]) == current_identity
        contact_identity = str(row["recipient_identity"] if is_outgoing else row["sender_identity"])
        contact_key = build_contact_key(contact_identity, int(offering["id"]))
        contact = contact_by_key.get(contact_key)
        if contact is None:
            continue
        if not contact["last_message_at"]:
            contact["last_message_at"] = str(row["created_at"] or "")
            contact["last_message_preview"] = _private_message_preview(
                row["content"],
                int(row["attachment_count"] or 0),
                72,
            )
            contact["last_message_is_outgoing"] = is_outgoing
        if not is_outgoing and not row["read_at"]:
            contact["unread_count"] += 1

    contacts = list(contact_by_key.values())
    contacts.sort(key=lambda item: str(item.get("display_name") or ""))
    contacts.sort(key=lambda item: 0 if item.get("role") == "teacher" else 1)
    contacts.sort(key=lambda item: str(item.get("last_message_at") or ""), reverse=True)
    contacts.sort(key=lambda item: 0 if item.get("last_message_at") else 1)
    contacts.sort(key=lambda item: 0 if int(item.get("unread_count") or 0) > 0 else 1)
    return {
        "class_offering_id": int(offering["id"]),
        "course_name": str(offering["course_name"] or ""),
        "class_name": str(offering["class_name"] or ""),
        "contacts": contacts,
        "limits": {
            "max_attachment_count": PRIVATE_MESSAGE_ATTACHMENT_LIMIT,
            "max_attachment_bytes": PRIVATE_MESSAGE_ATTACHMENT_MAX_BYTES,
        },
    }


def _build_notification_payload(
    *,
    recipient_role: str,
    recipient_user_pk: int,
    category: str,
    title: str,
    severity: Optional[str] = None,
    body_preview: str = "",
    actor_role: str = "",
    actor_user_pk: Optional[int] = None,
    actor_display_name: str = "",
    link_url: str = "",
    class_offering_id: Optional[int] = None,
    ref_type: str = "",
    ref_id: str = "",
    metadata: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    if actor_role == AI_ASSISTANT_ROLE and class_offering_id:
        actor_identity = build_ai_identity(class_offering_id)
    elif actor_role in {"student", "teacher"} and actor_user_pk is not None:
        actor_identity = build_user_identity(actor_role, actor_user_pk)
    else:
        actor_identity = ""
    normalized_category = str(category or "").strip().lower()
    normalized_severity = str(severity or notification_severity_for_category(normalized_category)).strip().lower()

    return {
        "recipient_identity": build_user_identity(recipient_role, recipient_user_pk),
        "recipient_role": recipient_role,
        "recipient_user_pk": recipient_user_pk,
        "category": normalized_category,
        "severity": normalized_severity,
        "actor_identity": actor_identity,
        "actor_role": actor_role,
        "actor_user_pk": actor_user_pk,
        "actor_display_name": actor_display_name,
        "title": title,
        "body_preview": body_preview,
        "link_url": link_url,
        "class_offering_id": class_offering_id,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        "created_at": created_at or _now_iso(),
    }


def _insert_notification(conn, payload: dict[str, Any]) -> int:
    db_engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO message_center_notifications (
            recipient_identity, recipient_role, recipient_user_pk,
            category, severity, actor_identity, actor_role, actor_user_pk, actor_display_name,
            title, body_preview, link_url, class_offering_id,
            ref_type, ref_id, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    notification_id = execute_insert_returning_id(
        conn,
        insert_sql,
        (
            payload["recipient_identity"],
            payload["recipient_role"],
            payload["recipient_user_pk"],
            payload["category"],
            payload.get("severity") or notification_severity_for_category(payload["category"]),
            payload["actor_identity"],
            payload["actor_role"],
            payload["actor_user_pk"],
            payload["actor_display_name"],
            payload["title"],
            payload["body_preview"],
            payload["link_url"],
            payload["class_offering_id"],
            payload["ref_type"],
            payload["ref_id"],
            payload["metadata_json"],
            payload["created_at"],
        ),
        engine=db_engine,
    )
    try:
        conn.execute("SAVEPOINT notification_email_enqueue")
        queue_notification_email_if_applicable(conn, notification_id=notification_id, payload=payload)
        conn.execute("RELEASE SAVEPOINT notification_email_enqueue")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT notification_email_enqueue")
            conn.execute("RELEASE SAVEPOINT notification_email_enqueue")
        except Exception:
            pass
        print(f"[EMAIL] queue notification email failed: {exc}")
    return notification_id


def _insert_private_message(
    conn,
    *,
    conversation_key: str,
    class_offering_id: Optional[int],
    sender_identity: str,
    sender_role: str,
    sender_user_pk: Optional[int],
    sender_display_name: str,
    recipient_identity: str,
    recipient_role: str,
    recipient_user_pk: Optional[int],
    recipient_display_name: str,
    content: str,
    read_at: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    timestamp = created_at or _now_iso()
    db_engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO private_messages (
            conversation_key, class_offering_id,
            sender_identity, sender_role, sender_user_pk, sender_display_name,
            recipient_identity, recipient_role, recipient_user_pk, recipient_display_name,
            content, read_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    message_id = execute_insert_returning_id(
        conn,
        insert_sql,
        (
            conversation_key,
            class_offering_id,
            sender_identity,
            sender_role,
            sender_user_pk,
            sender_display_name,
            recipient_identity,
            recipient_role,
            recipient_user_pk,
            recipient_display_name,
            content,
            read_at,
            timestamp,
        ),
        engine=db_engine,
    )
    return {
        "id": message_id,
        "conversation_key": conversation_key,
        "class_offering_id": class_offering_id,
        "sender_identity": sender_identity,
        "sender_role": sender_role,
        "sender_user_pk": sender_user_pk,
        "sender_display_name": sender_display_name,
        "recipient_identity": recipient_identity,
        "recipient_role": recipient_role,
        "recipient_user_pk": recipient_user_pk,
        "recipient_display_name": recipient_display_name,
        "content": content,
        "read_at": read_at,
        "created_at": timestamp,
    }


def _insert_private_message_attachments(
    conn,
    message_row: dict[str, Any],
    attachment_payloads: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    prepared = list(attachment_payloads or [])
    if not prepared:
        return []
    ensure_private_message_attachment_schema(conn)
    db_engine = get_configured_db_engine()
    if db_engine not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported private message attachment insert database engine: {db_engine!r}")
    inserted_ids: list[int] = []
    for item in prepared:
        insert_sql = """
            INSERT INTO private_message_attachments (
                message_id, conversation_key, class_offering_id,
                uploaded_by_identity, uploaded_by_role,
                file_hash, original_filename, mime_type, file_size, attachment_kind,
                image_width, image_height,
                thumbnail_file_hash, thumbnail_mime_type, thumbnail_file_size, thumbnail_width, thumbnail_height,
                preview_file_hash, preview_mime_type, preview_file_size, preview_width, preview_height
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        attachment_id = execute_insert_returning_id(
            conn,
            insert_sql,
            (
                int(message_row["id"]),
                str(message_row["conversation_key"]),
                message_row["class_offering_id"],
                str(message_row["sender_identity"]),
                str(message_row["sender_role"]),
                str(item["file_hash"]),
                str(item["original_filename"]),
                str(item["mime_type"]),
                int(item["file_size"] or 0),
                str(item.get("attachment_kind") or _private_attachment_kind(str(item.get("mime_type") or ""))),
                int(item.get("image_width") or 0) if item.get("image_width") is not None else None,
                int(item.get("image_height") or 0) if item.get("image_height") is not None else None,
                item.get("thumbnail_file_hash"),
                item.get("thumbnail_mime_type"),
                int(item.get("thumbnail_file_size") or 0),
                int(item.get("thumbnail_width") or 0) if item.get("thumbnail_width") is not None else None,
                int(item.get("thumbnail_height") or 0) if item.get("thumbnail_height") is not None else None,
                item.get("preview_file_hash"),
                item.get("preview_mime_type"),
                int(item.get("preview_file_size") or 0),
                int(item.get("preview_width") or 0) if item.get("preview_width") is not None else None,
                int(item.get("preview_height") or 0) if item.get("preview_height") is not None else None,
            ),
            engine=db_engine,
        )
        inserted_ids.append(attachment_id)

    placeholders = ",".join("?" for _ in inserted_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM private_message_attachments
        WHERE id IN ({placeholders})
        ORDER BY id ASC
        """,
        tuple(inserted_ids),
    ).fetchall()
    return [_build_private_attachment_payload(row) for row in rows]


def _insert_private_message_audit(conn, message_row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO private_message_audit_logs (
            message_id, class_offering_id,
            sender_identity, sender_role, sender_user_pk, sender_display_name,
            recipient_identity, recipient_role, recipient_user_pk, recipient_display_name,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_row["id"],
            message_row["class_offering_id"],
            message_row["sender_identity"],
            message_row["sender_role"],
            message_row["sender_user_pk"],
            message_row["sender_display_name"],
            message_row["recipient_identity"],
            message_row["recipient_role"],
            message_row["recipient_user_pk"],
            message_row["recipient_display_name"],
            message_row["created_at"],
        ),
    )


def _create_private_message_notification(
    conn,
    message_row: dict[str, Any],
    attachments: list[dict[str, Any]] | None = None,
) -> None:
    if message_row["recipient_role"] not in {"student", "teacher"} or message_row["recipient_user_pk"] is None:
        return
    attachment_count = len(attachments or [])
    _insert_notification(
        conn,
        _build_notification_payload(
            recipient_role=message_row["recipient_role"],
            recipient_user_pk=int(message_row["recipient_user_pk"]),
            category=MESSAGE_CATEGORY_PRIVATE,
            title=f"来自 {message_row['sender_display_name']} 的私信",
            body_preview=_private_message_preview(message_row["content"], attachment_count, 90),
            actor_role=message_row["sender_role"],
            actor_user_pk=message_row["sender_user_pk"],
            actor_display_name=message_row["sender_display_name"],
            link_url=build_message_center_link(
                contact_identity=message_row["sender_identity"],
                class_offering_id=message_row["class_offering_id"],
            ),
            class_offering_id=message_row["class_offering_id"],
            ref_type=MESSAGE_CATEGORY_PRIVATE,
            ref_id=str(message_row["id"]),
            metadata={
                "contact_identity": message_row["sender_identity"],
                "class_offering_id": message_row["class_offering_id"],
            },
            created_at=message_row["created_at"],
        ),
    )


def _serialize_notification(row) -> dict[str, Any]:
    item = dict(row)
    item["id"] = int(item["id"])
    item["recipient_user_pk"] = _safe_int(item.get("recipient_user_pk"))
    item["actor_user_pk"] = _safe_int(item.get("actor_user_pk"))
    item["class_offering_id"] = _safe_int(item.get("class_offering_id"))
    item["is_unread"] = not bool(item.get("read_at"))
    item["metadata"] = _safe_json_loads(item.get("metadata_json"), {})
    item["category_label"] = CATEGORY_LABELS.get(item["category"], item["category"])
    item["severity"] = str(item.get("severity") or notification_severity_for_category(item["category"]))
    item["severity_label"] = SEVERITY_LABELS.get(item["severity"], item["severity"])
    item["open_url"] = f"/message-center/notifications/{item['id']}/open"
    if (
        item["category"] == MESSAGE_CATEGORY_DISCUSSION_MENTION
        and _contains_broadcast_discussion_mention(item.get("body_preview"))
    ):
        actor_display_name = str(item.get("actor_display_name") or "").strip() or "\u8bfe\u5802\u6210\u5458"
        item["title"] = (
            f"{actor_display_name} "
            "\u5728\u8bfe\u5802\u8ba8\u8bba\u4e2d @\u4e86\u6240\u6709\u4eba"
        )
    item.pop("metadata_json", None)
    return item


def get_latest_unread_notification(conn, user: dict) -> Optional[dict[str, Any]]:
    user_pk, role, _ = _ensure_user_identity(user)
    row = conn.execute(
        """
        SELECT *
        FROM message_center_notifications
        WHERE recipient_role = ?
          AND recipient_user_pk = ?
          AND read_at IS NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (role, user_pk),
    ).fetchone()
    if row is None:
        return None
    return _serialize_notification(row)


def _serialize_private_message(
    row,
    *,
    current_identity: str,
    blocked_identities: set[str],
    attachments_by_message: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    sender_identity = str(row["sender_identity"])
    sender_role = str(row["sender_role"] or "")
    message_id = int(row["id"])
    return {
        "id": message_id,
        "sender_identity": sender_identity,
        "sender_role": sender_role,
        "sender_display_name": str(row["sender_display_name"] or ""),
        "recipient_identity": str(row["recipient_identity"] or ""),
        "recipient_role": str(row["recipient_role"] or ""),
        "recipient_display_name": str(row["recipient_display_name"] or ""),
        "content": str(row["content"] or ""),
        "created_at": str(row["created_at"] or ""),
        "read_at": str(row["read_at"] or ""),
        "is_outgoing": sender_identity == current_identity,
        "can_block_sender": sender_identity != current_identity and is_blockable_role(sender_role),
        "is_sender_blocked": sender_identity in blocked_identities,
        "attachments": list((attachments_by_message or {}).get(message_id, [])),
    }


def _serialize_private_ai_reply_job(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "conversation_key": str(row["conversation_key"] or ""),
        "class_offering_id": _safe_int(row["class_offering_id"]),
        "request_message_id": _safe_int(row["request_message_id"]),
        "requester_identity": str(row["requester_identity"] or ""),
        "requester_role": str(row["requester_role"] or ""),
        "requester_user_pk": _safe_int(row["requester_user_pk"]),
        "status": str(row["status"] or AI_REPLY_JOB_STATUS_PENDING),
        "error_message": _truncate_text(row["error_message"], 180),
        "reply_message_id": _safe_int(row["reply_message_id"]),
        "attempt_count": int(row["attempt_count"] or 0),
        "created_at": str(row["created_at"] or ""),
        "started_at": str(row["started_at"] or ""),
        "finished_at": str(row["finished_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "is_active": str(row["status"] or "") in ACTIVE_AI_REPLY_JOB_STATUSES,
    }


def get_message_center_summary(conn, user: dict, *, include_private: bool = True) -> dict[str, Any]:
    user_pk, role, _ = _ensure_user_identity(user)
    conditions = ["recipient_role = ?", "recipient_user_pk = ?"]
    params: list[Any] = [role, user_pk]
    if not include_private:
        conditions.append("category != ?")
        params.append(MESSAGE_CATEGORY_PRIVATE)
    rows = conn.execute(
        """
        SELECT category,
               COUNT(*) AS total_count,
               SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread_count
        FROM message_center_notifications
        WHERE {conditions}
        GROUP BY category
        """.format(conditions=" AND ".join(conditions)),
        tuple(params),
    ).fetchall()
    severity_rows = conn.execute(
        """
        SELECT severity,
               COUNT(*) AS total_count,
               SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread_count
        FROM message_center_notifications
        WHERE {conditions}
        GROUP BY severity
        """.format(conditions=" AND ".join(conditions)),
        tuple(params),
    ).fetchall()

    available_categories = (
        ALL_NOTIFICATION_CATEGORIES
        if include_private
        else tuple(category for category in ALL_NOTIFICATION_CATEGORIES if category != MESSAGE_CATEGORY_PRIVATE)
    )
    counts = {
        category: {
            "category": category,
            "label": CATEGORY_LABELS.get(category, category),
            "total_count": 0,
            "unread_count": 0,
        }
        for category in available_categories
    }
    unread_total = 0
    for row in rows:
        category = str(row["category"])
        unread_count = int(row["unread_count"] or 0)
        total_count = int(row["total_count"] or 0)
        counts[category] = {
            "category": category,
            "label": CATEGORY_LABELS.get(category, category),
            "total_count": total_count,
            "unread_count": unread_count,
        }
        unread_total += unread_count

    tabs: list[dict[str, Any]] = [{
        "category": "all",
        "label": CATEGORY_LABELS["all"],
        "unread_count": unread_total,
        "total_count": sum(item["total_count"] for item in counts.values()),
    }]
    visible_categories = get_visible_categories(role, include_private=include_private)
    for category in visible_categories:
        if category == "all":
            continue
        if category in counts:
            tabs.append(counts[category])
    severity_counts = {
        key: {
            "severity": key,
            "label": label,
            "total_count": 0,
            "unread_count": 0,
        }
        for key, label in SEVERITY_LABELS.items()
    }
    for row in severity_rows:
        severity = str(row["severity"] or notification_severity_for_category(""))
        if severity not in severity_counts:
            severity_counts[severity] = {
                "severity": severity,
                "label": severity,
                "total_count": 0,
                "unread_count": 0,
            }
        severity_counts[severity]["total_count"] = int(row["total_count"] or 0)
        severity_counts[severity]["unread_count"] = int(row["unread_count"] or 0)

    return {
        "unread_total": unread_total,
        "tabs": tabs,
        "severity_counts": list(severity_counts.values()),
        "visible_categories": list(visible_categories),
        "filters": [{"value": key, "label": value} for key, value in FILTER_LABELS.items()],
    }


def list_message_center_items(
    conn,
    user: dict,
    *,
    category: str = "all",
    keyword: str = "",
    filter_key: str = "all",
    limit: int = 120,
    include_private: bool = True,
) -> list[dict[str, Any]]:
    user_pk, role, _ = _ensure_user_identity(user)
    conditions = ["recipient_role = ?", "recipient_user_pk = ?"]
    params: list[Any] = [role, user_pk]
    if not include_private:
        conditions.append("category != ?")
        params.append(MESSAGE_CATEGORY_PRIVATE)

    normalized_category = str(category or "all").strip()
    if normalized_category != "all":
        if not include_private and normalized_category == MESSAGE_CATEGORY_PRIVATE:
            return []
        conditions.append("category = ?")
        params.append(normalized_category)

    normalized_filter = str(filter_key or "all").strip().lower()
    if normalized_filter == "unread":
        conditions.append("read_at IS NULL")
    elif normalized_filter in {"normal", "important", "system"}:
        conditions.append("severity = ?")
        params.append(normalized_filter)
    elif normalized_filter == "today":
        conditions.append(_created_today_condition("created_at"))

    normalized_keyword = str(keyword or "").strip()
    if normalized_keyword:
        like_value = f"%{normalized_keyword}%"
        conditions.append("(title LIKE ? OR body_preview LIKE ? OR actor_display_name LIKE ?)")
        params.extend((like_value, like_value, like_value))

    rows = conn.execute(
        f"""
        SELECT *
        FROM message_center_notifications
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE WHEN read_at IS NULL THEN 0 ELSE 1 END,
            created_at DESC,
            id DESC
        LIMIT ?
        """,
        (*params, max(1, min(int(limit), 300))),
    ).fetchall()
    return [_serialize_notification(row) for row in rows]


def mark_message_center_items_read(
    conn,
    user: dict,
    *,
    notification_ids: Optional[list[int]] = None,
    category: str = "all",
    contact_identity: str = "",
    class_offering_id: Optional[int] = None,
    include_private: bool = True,
) -> int:
    user_pk, role, _ = _ensure_user_identity(user)
    read_at = _now_iso()

    if notification_ids:
        normalized_ids = sorted({
            int(item)
            for item in (notification_ids or [])
            if _safe_int(item) is not None
        })
        if not normalized_ids:
            return 0
        placeholders = ",".join("?" for _ in normalized_ids)
        private_filter_sql = "" if include_private else f" AND category != ?"
        private_filter_params: tuple[Any, ...] = () if include_private else (MESSAGE_CATEGORY_PRIVATE,)
        cursor = conn.execute(
            f"""
            UPDATE message_center_notifications
            SET read_at = ?
            WHERE recipient_role = ?
              AND recipient_user_pk = ?
              AND read_at IS NULL
              {private_filter_sql}
              AND id IN ({placeholders})
            """,
            (read_at, role, user_pk, *private_filter_params, *normalized_ids),
        )
        return int(cursor.rowcount or 0)

    conditions = ["recipient_role = ?", "recipient_user_pk = ?", "read_at IS NULL"]
    params: list[Any] = [role, user_pk]
    if not include_private:
        conditions.append("category != ?")
        params.append(MESSAGE_CATEGORY_PRIVATE)

    normalized_category = str(category or "all").strip()
    if normalized_category != "all":
        if not include_private and normalized_category == MESSAGE_CATEGORY_PRIVATE:
            return 0
        conditions.append("category = ?")
        params.append(normalized_category)

    normalized_contact = str(contact_identity or "").strip()
    if normalized_contact:
        conditions.append("actor_identity = ?")
        params.append(normalized_contact)
        conditions.append("category = ?")
        params.append(MESSAGE_CATEGORY_PRIVATE)
        if class_offering_id is not None:
            conditions.append("COALESCE(class_offering_id, 0) = ?")
            params.append(int(class_offering_id))

    cursor = conn.execute(
        f"""
        UPDATE message_center_notifications
        SET read_at = ?
        WHERE {' AND '.join(conditions)}
        """,
        (read_at, *params),
    )
    return int(cursor.rowcount or 0)


def open_message_center_notification(conn, user: dict, notification_id: int | str) -> dict[str, Any]:
    user_pk, role, _ = _ensure_user_identity(user)
    normalized_id = _safe_int(notification_id)
    if normalized_id is None:
        raise ValueError("notification not found")
    row = conn.execute(
        """
        SELECT *
        FROM message_center_notifications
        WHERE id = ?
          AND recipient_role = ?
          AND recipient_user_pk = ?
        LIMIT 1
        """,
        (normalized_id, role, user_pk),
    ).fetchone()
    if row is None:
        raise ValueError("notification not found")
    if not row["read_at"]:
        conn.execute(
            """
            UPDATE message_center_notifications
            SET read_at = ?
            WHERE id = ?
              AND recipient_role = ?
              AND recipient_user_pk = ?
              AND read_at IS NULL
            """,
            (_now_iso(), normalized_id, role, user_pk),
        )
        row = conn.execute(
            "SELECT * FROM message_center_notifications WHERE id = ? LIMIT 1",
            (normalized_id,),
        ).fetchone()
    return _serialize_notification(row)


def _mark_private_conversation_read(
    conn,
    *,
    current_identity: str,
    current_role: str,
    current_user_pk: int,
    contact_identity: str,
    class_offering_id: Optional[int],
) -> dict[str, int]:
    read_at = _now_iso()
    conditions = [
        "recipient_identity = ?",
        "sender_identity = ?",
        "read_at IS NULL",
    ]
    params: list[Any] = [current_identity, contact_identity]
    if class_offering_id is not None:
        conditions.append("COALESCE(class_offering_id, 0) = ?")
        params.append(int(class_offering_id))

    message_cursor = conn.execute(
        f"""
        UPDATE private_messages
        SET read_at = ?
        WHERE {' AND '.join(conditions)}
        """,
        (read_at, *params),
    )
    notification_cursor = conn.execute(
        """
        UPDATE message_center_notifications
        SET read_at = ?
        WHERE recipient_role = ?
          AND recipient_user_pk = ?
          AND category = ?
          AND actor_identity = ?
          AND read_at IS NULL
          AND COALESCE(class_offering_id, 0) = ?
        """,
        (
            read_at,
            current_role,
            current_user_pk,
            MESSAGE_CATEGORY_PRIVATE,
            contact_identity,
            int(class_offering_id or 0),
        ),
    )
    return {
        "message_count": int(message_cursor.rowcount or 0),
        "notification_count": int(notification_cursor.rowcount or 0),
    }


def _lookup_identity_display_name(conn, identity: str) -> dict[str, Any]:
    parsed = parse_identity(identity)
    role = str(parsed["role"])
    if role == AI_ASSISTANT_ROLE:
        offering_id = int(parsed["class_offering_id"])
        offering = conn.execute(
            """
            SELECT c.name AS course_name, cl.name AS class_name
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            WHERE o.id = ?
            LIMIT 1
            """,
            (offering_id,),
        ).fetchone()
        subtitle = ""
        if offering:
            subtitle = f"{offering['course_name']} / {offering['class_name']}"
        return {
            "identity": identity,
            "role": AI_ASSISTANT_ROLE,
            "user_pk": None,
            "display_name": f"{AI_ASSISTANT_LABEL} 路 {offering['course_name']}" if offering else AI_ASSISTANT_LABEL,
            "subtitle": subtitle,
            "class_offering_id": offering_id,
        }

    table_name = "students" if role == "student" else "teachers"
    row = conn.execute(
        f"SELECT id, name FROM {table_name} WHERE id = ? LIMIT 1",
        (parsed["user_pk"],),
    ).fetchone()
    display_name = build_actor_display_name(str(row["name"] or ""), role) if row else build_actor_display_name("", role)
    return {
        "identity": identity,
        "role": role,
        "user_pk": int(parsed["user_pk"]),
        "display_name": display_name,
        "subtitle": "",
        "class_offering_id": None,
    }


def _is_blocked(conn, owner_identity: str, blocked_identity: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM private_message_blocks
        WHERE owner_identity = ? AND blocked_identity = ?
        LIMIT 1
        """,
        (owner_identity, blocked_identity),
    ).fetchone()
    return row is not None


def list_private_message_blocks(conn, user: dict) -> list[dict[str, Any]]:
    _, _, current_identity = _ensure_user_identity(user)
    items = list(_load_blocked_identity_map(conn, current_identity).values())
    items.sort(
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("display_name") or ""),
        ),
        reverse=True,
    )
    return items


def add_private_message_block(
    conn,
    user: dict,
    *,
    contact_identity: str,
    class_offering_id: Optional[int] = None,
) -> dict[str, Any]:
    current_user_pk, current_role, current_identity = _ensure_user_identity(user)
    normalized_identity = str(contact_identity or "").strip()
    if not normalized_identity or normalized_identity == current_identity:
        raise ValueError("invalid block target")

    parsed = parse_identity(normalized_identity)
    if not is_blockable_role(parsed["role"]):
        raise ValueError("target cannot be blocked")

    contact = _resolve_contact(
        conn,
        user=user,
        contact_identity=normalized_identity,
        class_offering_id=class_offering_id,
    )
    display_info = contact or _lookup_identity_display_name(conn, normalized_identity)
    timestamp = _now_iso()
    conn.execute(
        """
        INSERT INTO private_message_blocks (
            owner_identity, owner_role, owner_user_pk,
            blocked_identity, blocked_role, blocked_user_pk, blocked_display_name, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_identity, blocked_identity) DO UPDATE SET
            blocked_role = excluded.blocked_role,
            blocked_user_pk = excluded.blocked_user_pk,
            blocked_display_name = excluded.blocked_display_name,
            created_at = excluded.created_at
        """,
        (
            current_identity,
            current_role,
            current_user_pk,
            normalized_identity,
            display_info["role"],
            display_info["user_pk"],
            display_info["display_name"],
            timestamp,
        ),
    )
    return {
        "identity": normalized_identity,
        "role": display_info["role"],
        "user_pk": display_info["user_pk"],
        "display_name": display_info["display_name"],
        "created_at": timestamp,
    }


def remove_private_message_block(conn, user: dict, *, contact_identity: str) -> int:
    _, _, current_identity = _ensure_user_identity(user)
    cursor = conn.execute(
        """
        DELETE FROM private_message_blocks
        WHERE owner_identity = ? AND blocked_identity = ?
        """,
        (current_identity, str(contact_identity or "").strip()),
    )
    return int(cursor.rowcount or 0)


def _load_latest_private_ai_reply_job_row(
    conn,
    *,
    requester_identity: str,
    conversation_key: str,
):
    return conn.execute(
        """
        SELECT *
        FROM private_message_ai_jobs
        WHERE requester_identity = ?
          AND conversation_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (requester_identity, conversation_key),
    ).fetchone()


def _load_visible_private_ai_reply_job(
    conn,
    *,
    requester_identity: str,
    conversation_key: str,
) -> Optional[dict[str, Any]]:
    row = _load_latest_private_ai_reply_job_row(
        conn,
        requester_identity=requester_identity,
        conversation_key=conversation_key,
    )
    if row is None:
        return None
    job = _serialize_private_ai_reply_job(row)
    if job["status"] == AI_REPLY_JOB_STATUS_COMPLETED:
        return None
    return job


def create_private_ai_reply_job(
    conn,
    user: dict,
    *,
    conversation_key: str,
    class_offering_id: int,
    request_message_id: int,
) -> dict[str, Any]:
    current_user_pk, current_role, current_identity = _ensure_user_identity(user)
    timestamp = _now_iso()
    engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO private_message_ai_jobs (
            conversation_key, class_offering_id, request_message_id,
            requester_identity, requester_role, requester_user_pk,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    job_id = execute_insert_returning_id(
        conn,
        insert_sql,
        (
            conversation_key,
            class_offering_id,
            request_message_id,
            current_identity,
            current_role,
            current_user_pk,
            AI_REPLY_JOB_STATUS_PENDING,
            timestamp,
            timestamp,
        ),
        engine=engine,
    )
    row = conn.execute(
        """
        SELECT *
        FROM private_message_ai_jobs
        WHERE id = ?
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    return _serialize_private_ai_reply_job(row)


def get_private_ai_reply_job(conn, user: dict, *, job_id: int | str) -> dict[str, Any]:
    _, _, current_identity = _ensure_user_identity(user)
    row = conn.execute(
        """
        SELECT *
        FROM private_message_ai_jobs
        WHERE id = ?
          AND requester_identity = ?
        LIMIT 1
        """,
        (int(job_id), current_identity),
    ).fetchone()
    if row is None:
        raise ValueError("AI reply job not found")
    return _serialize_private_ai_reply_job(row)


def get_private_message_conversation(
    conn,
    user: dict,
    *,
    contact_identity: str,
    class_offering_id: Optional[int] = None,
    limit: int = 120,
) -> dict[str, Any]:
    current_user_pk, current_role, current_identity = _ensure_user_identity(user)
    contact = _resolve_contact(
        conn,
        user=user,
        contact_identity=contact_identity,
        class_offering_id=class_offering_id,
    )
    if not contact:
        raise ValueError("contact not found")

    ensure_private_message_attachment_schema(conn)
    normalized_scope = _safe_int(contact.get("class_offering_id"))
    if normalized_scope is None:
        normalized_scope = _safe_int(class_offering_id)
    conversation_key = build_conversation_key(current_identity, contact["identity"], normalized_scope)

    rows = conn.execute(
        """
        SELECT *
        FROM (
            SELECT *
            FROM private_messages
            WHERE conversation_key = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        ) recent_messages
        ORDER BY created_at ASC, id ASC
        """,
        (conversation_key, max(1, min(int(limit), 300))),
    ).fetchall()

    read_result = _mark_private_conversation_read(
        conn,
        current_identity=current_identity,
        current_role=current_role,
        current_user_pk=current_user_pk,
        contact_identity=str(contact["identity"]),
        class_offering_id=normalized_scope,
    )
    blocked_map = _load_blocked_identity_map(conn, current_identity)
    blocked_identities = set(blocked_map.keys())
    is_blocked_by_contact = _is_blocked(conn, str(contact["identity"]), current_identity)
    patched_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if (
            read_result["message_count"] > 0
            and item.get("recipient_identity") == current_identity
            and item.get("sender_identity") == contact["identity"]
            and not item.get("read_at")
        ):
            item["read_at"] = _now_iso()
        patched_rows.append(item)

    attachments_by_message = _load_private_message_attachments(
        conn,
        [int(item["id"]) for item in patched_rows],
    )
    contact_payload = dict(contact)
    contact_payload["is_blocked"] = str(contact["identity"]) in blocked_identities
    contact_payload["is_blocked_by_contact"] = is_blocked_by_contact
    contact_payload["can_send"] = bool(contact_payload.get("can_send")) and not contact_payload["is_blocked"] and not is_blocked_by_contact

    return {
        "contact": contact_payload,
        "conversation_key": conversation_key,
        "class_offering_id": normalized_scope,
        "ai_reply_job": _load_visible_private_ai_reply_job(
            conn,
            requester_identity=current_identity,
            conversation_key=conversation_key,
        ),
        "messages": [
            _serialize_private_message(
                item,
                current_identity=current_identity,
                blocked_identities=blocked_identities,
                attachments_by_message=attachments_by_message,
            )
            for item in patched_rows
        ],
        "read_result": read_result,
    }


def load_private_message_attachment_for_user(conn, user: dict, attachment_id: int):
    _, _, current_identity = _ensure_user_identity(user)
    ensure_private_message_attachment_schema(conn)
    row = conn.execute(
        """
        SELECT pma.*,
               pm.sender_identity,
               pm.recipient_identity
        FROM private_message_attachments pma
        JOIN private_messages pm ON pm.id = pma.message_id
        WHERE pma.id = ?
        LIMIT 1
        """,
        (int(attachment_id),),
    ).fetchone()
    if row is None:
        return None
    if current_identity not in {str(row["sender_identity"]), str(row["recipient_identity"])}:
        raise PermissionError("permission denied")
    return row


def _update_private_derivative_columns(conn, attachment_id: int, variant: str, derivative: dict) -> None:
    columns = PRIVATE_MESSAGE_VARIANT_COLUMNS[variant]
    conn.execute(
        f"""
        UPDATE private_message_attachments
        SET
            {columns["hash"]} = ?,
            {columns["mime_type"]} = ?,
            {columns["file_size"]} = ?,
            {columns["width"]} = ?,
            {columns["height"]} = ?
        WHERE id = ?
        """,
        (
            str(derivative["file_hash"]),
            str(derivative["mime_type"]),
            int(derivative["file_size"] or 0),
            int(derivative["width"] or 0),
            int(derivative["height"] or 0),
            int(attachment_id),
        ),
    )


def _ensure_private_message_attachment_derivative_sync(
    user: dict,
    attachment_id: int,
    variant: str,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        row = load_private_message_attachment_for_user(conn, user, attachment_id)
        if row is None:
            raise ValueError("Private message attachment not found")
        existing = resolve_private_message_attachment_file_payload(row, variant)
        if existing:
            return existing
        if str(row["attachment_kind"] or _private_attachment_kind(str(row["mime_type"] or ""))) != "image":
            raise ValueError("Private message attachment variant not found")
        original_file = _resolve_private_original_file_payload(row)
        if not original_file:
            raise ValueError("Private message attachment not found")

    try:
        derivative = build_chat_image_derivative_sync(original_file["path"], variant)
    except ChatImageTooLargeError as exc:
        raise ValueError("Private message image dimensions are too large") from exc
    except ChatImageDerivativeError as exc:
        raise ValueError("Private message image is invalid") from exc

    with get_db_connection() as conn:
        row = load_private_message_attachment_for_user(conn, user, attachment_id)
        if row is None:
            raise ValueError("Private message attachment not found")
        existing = resolve_private_message_attachment_file_payload(row, variant)
        if existing:
            return existing
        _update_private_derivative_columns(conn, attachment_id, variant, derivative)
        conn.commit()
        row = load_private_message_attachment_for_user(conn, user, attachment_id)
        payload = resolve_private_message_attachment_file_payload(row, variant)
        if not payload:
            raise ValueError("Private message image derivative unavailable")
        return payload


async def ensure_private_message_attachment_file_payload(
    user: dict,
    attachment_id: int,
    variant: str = "original",
) -> dict[str, Any]:
    normalized_variant = str(variant or "original").strip().lower()
    if normalized_variant == "original":
        with get_db_connection() as conn:
            row = load_private_message_attachment_for_user(conn, user, attachment_id)
        if row is None:
            raise ValueError("Private message attachment not found")
        payload = resolve_private_message_attachment_file_payload(row, "original")
        if not payload:
            raise ValueError("Private message attachment not found")
        return payload
    if normalized_variant not in PRIVATE_MESSAGE_VARIANT_COLUMNS:
        raise ValueError("Private message attachment variant not found")

    with get_db_connection() as conn:
        row = load_private_message_attachment_for_user(conn, user, attachment_id)
    if row is None:
        raise ValueError("Private message attachment not found")
    payload = resolve_private_message_attachment_file_payload(row, normalized_variant)
    if payload:
        return payload

    lock = await _get_private_attachment_derivative_lock(attachment_id, normalized_variant)
    async with lock:
        return await run_chat_image_processing(
            _ensure_private_message_attachment_derivative_sync,
            user,
            attachment_id,
            normalized_variant,
        )


def _insert_notification_if_allowed(
    conn,
    payload: dict[str, Any],
    *,
    allow_duplicates: bool = False,
) -> int:
    if allow_duplicates or not payload.get("ref_type") or not payload.get("ref_id"):
        return _insert_notification(conn, payload)

    existing = conn.execute(
        """
        SELECT id
        FROM message_center_notifications
        WHERE recipient_role = ?
          AND recipient_user_pk = ?
          AND category = ?
          AND ref_type = ?
          AND ref_id = ?
        LIMIT 1
        """,
        (
            payload["recipient_role"],
            payload["recipient_user_pk"],
            payload["category"],
            payload["ref_type"],
            payload["ref_id"],
        ),
    ).fetchone()
    if existing is not None:
        return 0
    return _insert_notification(conn, payload)


def is_super_admin_teacher(conn, teacher_id: int | str | None) -> bool:
    normalized_teacher_id = _safe_int(teacher_id)
    if normalized_teacher_id is None:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM teachers
        WHERE id = ?
          AND COALESCE(is_active, 1) = 1
          AND COALESCE(is_super_admin, 0) = 1
        LIMIT 1
        """,
        (normalized_teacher_id,),
    ).fetchone()
    return row is not None


def list_super_admin_teachers(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, email
        FROM teachers
        WHERE COALESCE(is_super_admin, 0) = 1
          AND COALESCE(is_active, 1) = 1
        ORDER BY id ASC
        """
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "email": str(row["email"] or ""),
        }
        for row in rows
    ]


def create_app_feedback_notifications(conn, feedback_id: int | str) -> int:
    feedback = conn.execute(
        """
        SELECT f.id, f.user_id, f.user_role, f.user_name, f.feedback_type,
               f.section, f.title, f.description, f.page_url, f.created_at,
               (
                   SELECT COUNT(*)
                   FROM app_feedback_attachments a
                   WHERE a.feedback_id = f.id
               ) AS attachment_count
        FROM app_feedback f
        WHERE f.id = ?
        LIMIT 1
        """,
        (feedback_id,),
    ).fetchone()
    if not feedback:
        return 0

    recipients = list_super_admin_teachers(conn)
    if not recipients:
        return 0

    feedback_type = str(feedback["feedback_type"] or "").strip().lower()
    type_label = APP_FEEDBACK_TYPE_LABELS.get(feedback_type, "问题反馈")
    actor_role = str(feedback["user_role"] or "").strip().lower()
    actor_user_pk = _safe_int(feedback["user_id"])
    actor_display_name = str(feedback["user_name"] or "").strip() or build_actor_display_name("", actor_role)
    section = str(feedback["section"] or "").strip()
    title = str(feedback["title"] or "").strip()
    description_preview = _truncate_text(feedback["description"], 110)
    attachment_count = int(feedback["attachment_count"] or 0)
    preview_parts = [item for item in (section, title, description_preview) if item]
    if attachment_count:
        preview_parts.append(f"{attachment_count} 张截图")

    inserted_count = 0
    for recipient in recipients:
        payload = _build_notification_payload(
            recipient_role="teacher",
            recipient_user_pk=int(recipient["id"]),
            category=MESSAGE_CATEGORY_APP_FEEDBACK,
            title=f"{actor_display_name} 提交了{type_label}",
            body_preview=" | ".join(preview_parts),
            actor_role=actor_role if actor_role in {"student", "teacher"} else "",
            actor_user_pk=actor_user_pk,
            actor_display_name=actor_display_name,
            link_url="/manage/system/feedback",
            ref_type=MESSAGE_CATEGORY_APP_FEEDBACK,
            ref_id=str(feedback["id"]),
            metadata={
                "feedback_id": int(feedback["id"]),
                "feedback_type": feedback_type,
                "feedback_type_label": type_label,
                "section": section,
                "page_url": str(feedback["page_url"] or ""),
                "attachment_count": attachment_count,
            },
            created_at=str(feedback["created_at"] or _now_iso()),
        )
        inserted_count += 1 if _insert_notification_if_allowed(conn, payload) else 0
    return inserted_count


def create_password_reset_request_notification(conn, request_id: int | str) -> int:
    request_row = conn.execute(
        """
        SELECT r.id, r.student_id, r.class_id, r.teacher_id, r.status,
               r.request_name, r.request_student_id_number, r.request_class_name,
               r.requester_ip, r.requester_device_label, r.submitted_at
        FROM student_password_reset_requests r
        JOIN teachers t ON t.id = r.teacher_id
        WHERE r.id = ?
          AND COALESCE(t.is_active, 1) = 1
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()
    if not request_row or request_row["status"] != "pending":
        return 0

    student_name = str(request_row["request_name"] or "").strip() or build_actor_display_name("", "student")
    student_number = str(request_row["request_student_id_number"] or "").strip()
    class_name = str(request_row["request_class_name"] or "").strip()
    device_label = str(request_row["requester_device_label"] or "").strip()
    requester_ip = str(request_row["requester_ip"] or "").strip()

    preview_parts = [item for item in (class_name, f"学号 {student_number}" if student_number else "", device_label) if item]
    if requester_ip:
        preview_parts.append(f"IP {requester_ip}")

    payload = _build_notification_payload(
        recipient_role="teacher",
        recipient_user_pk=int(request_row["teacher_id"]),
        category=MESSAGE_CATEGORY_PASSWORD_RESET,
        title=f"{student_name} 提交了找回密码申请",
        body_preview=" | ".join(preview_parts),
        actor_role="student",
        actor_user_pk=_safe_int(request_row["student_id"]),
        actor_display_name=student_name,
        link_url=f"/manage/system/password-resets?request_id={int(request_row['id'])}",
        ref_type=MESSAGE_CATEGORY_PASSWORD_RESET,
        ref_id=str(request_row["id"]),
        metadata={
            "request_id": int(request_row["id"]),
            "student_id": int(request_row["student_id"]),
            "class_id": int(request_row["class_id"]),
            "status": str(request_row["status"] or ""),
        },
        created_at=str(request_row["submitted_at"] or _now_iso()),
    )
    inserted_count = 1 if _insert_notification_if_allowed(conn, payload) else 0
    extra_recipients = conn.execute(
        """
        SELECT DISTINCT t.id
        FROM teachers t
        WHERE COALESCE(t.is_active, 1) = 1
          AND t.id != ?
          AND (
                t.id IN (
                    SELECT c.created_by_teacher_id
                    FROM classes c
                    WHERE c.id = ?
                )
                OR t.id IN (
                    SELECT o.teacher_id
                    FROM class_offerings o
                    WHERE o.class_id = ?
                )
          )
        """,
        (
            int(request_row["teacher_id"]),
            int(request_row["class_id"]),
            int(request_row["class_id"]),
        ),
    ).fetchall()
    for recipient in extra_recipients:
        payload["recipient_user_pk"] = int(recipient["id"])
        inserted_count += 1 if _insert_notification_if_allowed(conn, dict(payload)) else 0
    return inserted_count


def mark_password_reset_request_notification_read(conn, request_id: int | str, teacher_id: int | str) -> int:
    normalized_request_id = _safe_int(request_id)
    normalized_teacher_id = _safe_int(teacher_id)
    if normalized_request_id is None or normalized_teacher_id is None:
        return 0

    cursor = conn.execute(
        """
        UPDATE message_center_notifications
        SET read_at = COALESCE(read_at, ?)
        WHERE recipient_role = 'teacher'
          AND recipient_user_pk = ?
          AND category = ?
          AND ref_type = ?
          AND ref_id = ?
        """,
        (
            _now_iso(),
            normalized_teacher_id,
            MESSAGE_CATEGORY_PASSWORD_RESET,
            MESSAGE_CATEGORY_PASSWORD_RESET,
            str(normalized_request_id),
        ),
    )
    return int(cursor.rowcount or 0)


def create_private_message(
    conn,
    user: dict,
    *,
    contact_identity: str,
    class_offering_id: Optional[int] = None,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_user_pk, current_role, current_identity = _ensure_user_identity(user)
    ensure_private_message_attachment_schema(conn)
    contact = _resolve_contact(
        conn,
        user=user,
        contact_identity=contact_identity,
        class_offering_id=class_offering_id,
    )
    if not contact:
        raise ValueError("contact not found")

    normalized_content = str(content or "").strip()
    prepared_attachments = list(attachments or [])
    if len(prepared_attachments) > PRIVATE_MESSAGE_ATTACHMENT_LIMIT:
        raise ValueError(f"单条私信最多只能发送 {PRIVATE_MESSAGE_ATTACHMENT_LIMIT} 个附件")
    if not normalized_content and not prepared_attachments:
        raise ValueError("message content is required")
    if len(normalized_content) > 4000:
        raise ValueError("message content is too long")
    if str(contact["identity"]) == current_identity:
        raise ValueError("cannot send message to self")
    if not bool(contact.get("can_send")):
        raise ValueError("contact is not available")
    if contact.get("is_blocked"):
        raise PermissionError("please unblock the contact before sending")
    if _is_blocked(conn, str(contact["identity"]), current_identity):
        raise PermissionError("the recipient is not accepting messages from you")
    if prepared_attachments and str(contact["role"]) == AI_ASSISTANT_ROLE:
        raise ValueError("AI 助教私信暂不支持附件，请先发送文字内容")
    _enforce_private_message_rate_limit(conn, sender_identity=current_identity)

    normalized_scope = _safe_int(contact.get("class_offering_id"))
    if normalized_scope is None:
        normalized_scope = _safe_int(class_offering_id)
    conversation_key = build_conversation_key(current_identity, str(contact["identity"]), normalized_scope)
    latest_ai_reply_job = _load_latest_private_ai_reply_job_row(
        conn,
        requester_identity=current_identity,
        conversation_key=conversation_key,
    )
    if (
        str(contact["role"]) == AI_ASSISTANT_ROLE
        and latest_ai_reply_job is not None
        and str(latest_ai_reply_job["status"] or "") in ACTIVE_AI_REPLY_JOB_STATUSES
    ):
        raise ValueError("AI 助教正在回复上一条消息，请稍候")
    sender_display_name = build_actor_display_name(str(user.get("name") or user.get("username") or ""), current_role)

    message_row = _insert_private_message(
        conn,
        conversation_key=conversation_key,
        class_offering_id=normalized_scope,
        sender_identity=current_identity,
        sender_role=current_role,
        sender_user_pk=current_user_pk,
        sender_display_name=sender_display_name,
        recipient_identity=str(contact["identity"]),
        recipient_role=str(contact["role"]),
        recipient_user_pk=_safe_int(contact.get("user_pk")),
        recipient_display_name=str(contact.get("display_name") or ""),
        content=normalized_content,
    )
    attachment_payloads = _insert_private_message_attachments(conn, message_row, prepared_attachments)
    _insert_private_message_audit(conn, message_row)
    if str(contact["role"]) in {"student", "teacher"}:
        _create_private_message_notification(conn, message_row, attachment_payloads)

    serialized = _serialize_private_message(
        message_row,
        current_identity=current_identity,
        blocked_identities=set(_load_blocked_identity_map(conn, current_identity).keys()),
        attachments_by_message={int(message_row["id"]): attachment_payloads},
    )
    return {
        "contact": dict(contact),
        "class_offering_id": normalized_scope,
        "conversation_key": conversation_key,
        "message": message_row,
        "message_serialized": serialized,
        "requires_ai_reply": str(contact["role"]) == AI_ASSISTANT_ROLE,
    }


def _build_ai_private_user_context(conn, user: dict, class_offering_id: int) -> str:
    offering = conn.execute(
        """
        SELECT o.id, c.name AS course_name, cl.name AS class_name, t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (class_offering_id,),
    ).fetchone()
    if not offering:
        return str(user.get("name") or "")

    explicit_profile = load_explicit_user_profile(conn, int(user["id"]), str(user.get("role") or ""))
    time_context = build_time_context_text()
    polite_name = polite_address(
        str(explicit_profile.get("name") or user.get("name") or ""),
        str(user.get("role") or "student"),
    )
    explicit_profile_prompt = build_explicit_user_profile_prompt(
        explicit_profile,
        heading="【用户在个人中心维护的资料与沟通信号】",
    )

    if str(user.get("role")) == "student":
        student_id_number = str(explicit_profile.get("student_id_number") or "")
        student_name = str(explicit_profile.get("name") or user.get("name") or "")
        lines = [
            f"姓名：{student_name}",
            f"礼貌称呼：{polite_name}",
            "身份：学生",
            f"学号：{student_id_number or '未提供'}",
            f"课程：{offering['course_name']}",
            f"班级：{offering['class_name']}",
            f"授课教师：{offering['teacher_name']}",
        ]
        lines.append(explicit_profile_prompt)
        support_signal_prompt = build_student_support_signal_prompt(
            conn,
            student_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
            include_teacher_note=True,
            include_course_signals=True,
        )
        if support_signal_prompt:
            lines.append(support_signal_prompt)
        lines.append("当前场景：与课堂 AI 助教进行一对一私信交流。")
        lines.append(time_context)
        return "\n".join(lines)

    return "\n".join([
        f"姓名：{explicit_profile.get('name') or user.get('name') or ''}",
        f"礼貌称呼：{polite_name}",
        "身份：教师",
        f"课程：{offering['course_name']}",
        f"班级：{offering['class_name']}",
        explicit_profile_prompt,
        "当前场景：与课堂 AI 助教进行一对一私信交流。",
        time_context,
    ])


def _sanitize_ai_private_reply(reply_text: Any) -> str:
    normalized = str(reply_text or "").strip()
    if not normalized:
        return AI_REPLY_FALLBACK

    sanitized_lines = [
        line.strip()
        for line in normalized.splitlines()
        if line.strip() and not any(marker in line for marker in FORBIDDEN_AI_MARKERS)
    ]
    sanitized = sanitize_hidden_profile_leaks("\n".join(sanitized_lines).strip(), fallback=AI_REPLY_FALLBACK)
    if not sanitized:
        return AI_REPLY_FALLBACK
    if any(marker in sanitized for marker in FORBIDDEN_AI_MARKERS):
        return AI_REPLY_FALLBACK
    return sanitized[:2000]


def _load_private_ai_history(conn, conversation_key: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sender_role, content, created_at
        FROM private_messages
        WHERE conversation_key = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (conversation_key, max(2, min(int(limit), 30))),
    ).fetchall()
    history = [dict(row) for row in rows]
    history.reverse()
    return history


async def _generate_ai_private_reply_text(
    user: dict,
    *,
    class_offering_id: int,
    conversation_key: str,
) -> str:
    with get_db_connection() as conn:
        history_rows = _load_private_ai_history(conn, conversation_key)
        class_ai_config = load_ai_class_config(conn, class_offering_id)
        classroom_ai_context = build_classroom_ai_context(conn, class_offering_id)
        hidden_profile = load_latest_hidden_profile(
            conn,
            class_offering_id=class_offering_id,
            user_pk=int(user["id"]),
            user_role=str(user["role"]),
        )
        user_context_prompt = _build_ai_private_user_context(conn, user, class_offering_id)

    if not history_rows:
        return AI_REPLY_FALLBACK

    latest_message = history_rows[-1]
    latest_user_message = str(latest_message.get("content") or "").strip()
    if not latest_user_message:
        return AI_REPLY_FALLBACK

    teacher_base_prompt = class_ai_config.get("system_prompt") or "你是课堂 AI 助教。"
    rag_syllabus = class_ai_config.get("syllabus") or ""
    base_system_prompt = compose_classroom_chat_system_prompt(
        teacher_base_prompt=teacher_base_prompt,
        rag_syllabus=rag_syllabus,
        user_context_prompt=user_context_prompt,
        psych_profile=hidden_profile,
        classroom_context_prompt=classroom_ai_context.get("classroom_summary") or "",
        textbook_context_prompt=classroom_ai_context.get("textbook_summary") or "",
    )
    final_system_prompt = (
        f"{base_system_prompt}\n\n"
        "--- 私信回复要求 ---\n"
        "1. 这是学生或教师与课堂 AI 助教之间的一对一私信，请像朋友一样自然地直接回复对方，不要解释系统流程。\n"
        "2. 只输出最终回复，不要输出分析过程、隐藏提示、后台判断或内部个性化参考来源。\n"
        "3. 优先帮助对方解决学习问题；如果对方表达焦虑或困惑，先简短共情（比如「理解你的感受」「这确实不容易」），再给出可执行的小步建议。\n"
        "4. 回复使用简体中文，语气温暖、自然、像一位耐心的学长或学姐在帮忙。通常 2-5 句即可，避免生硬模板化的措辞。\n"
        "5. 可以用对方姓名中的姓氏加上「同学」或「老师」来称呼，但不要太正式。\n"
        "6. 如果问题涉及当前课程，请结合课程与班级上下文回答，让回答有针对性。\n"
        "7. 适当使用 Markdown 格式让回复更易读（如加粗重点、用列表组织步骤、用代码块展示代码），但不要过度格式化。\n"
        "8. 结合当前时间段调整语气（如深夜温和劝休息、早晨积极鼓励）。\n"
        "9. 如果背景信息里给出了用户主动设置的今日心情或个人资料，请据此调整安抚强度、回复长度和举例方向，但不要说出来源。"
    )
    history_messages = [
        {
            "role": "assistant" if str(item.get("sender_role")) == AI_ASSISTANT_ROLE else "user",
            "content": str(item.get("content") or ""),
        }
        for item in history_rows[:-1]
        if str(item.get("content") or "").strip()
    ]

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": final_system_prompt,
                "messages": history_messages,
                "new_message": latest_user_message,
                "model_capability": "standard",
                "task_type": "fast_text_response",
                "task_priority": "interactive",
                "task_label": "private_message_reply",
                "web_search_enabled": False,
            },
            timeout=90.0,
        )
        response.raise_for_status()
        response_data = response.json()
        if response_data.get("status") != "success":
            return AI_REPLY_FALLBACK
        return _sanitize_ai_private_reply(response_data.get("response_text") or "")
    except Exception:
        return AI_REPLY_FALLBACK


async def generate_ai_private_reply(
    user: dict,
    *,
    class_offering_id: int,
    conversation_key: str,
) -> Optional[dict[str, Any]]:
    reply_text = await _generate_ai_private_reply_text(
        user,
        class_offering_id=class_offering_id,
        conversation_key=conversation_key,
    )
    if not str(reply_text or "").strip():
        return None

    current_user_pk, current_role, current_identity = _ensure_user_identity(user)
    assistant_identity = build_ai_identity(class_offering_id)
    read_at = _now_iso()

    with get_db_connection() as conn:
        assistant_info = _lookup_identity_display_name(conn, assistant_identity)
        message_row = _insert_private_message(
            conn,
            conversation_key=conversation_key,
            class_offering_id=class_offering_id,
            sender_identity=assistant_identity,
            sender_role=AI_ASSISTANT_ROLE,
            sender_user_pk=None,
            sender_display_name=str(assistant_info["display_name"]),
            recipient_identity=current_identity,
            recipient_role=current_role,
            recipient_user_pk=current_user_pk,
            recipient_display_name=build_actor_display_name(str(user.get("name") or ""), current_role),
            content=reply_text,
            read_at=read_at,
        )
        _insert_private_message_audit(conn, message_row)
        conn.commit()
        serialized = _serialize_private_message(
            message_row,
            current_identity=current_identity,
            blocked_identities=set(_load_blocked_identity_map(conn, current_identity).keys()),
        )
    serialized["read_at"] = read_at
    return serialized


def _build_private_ai_job_user_snapshot(conn, *, requester_identity: str) -> dict[str, Any]:
    parsed = parse_identity(requester_identity)
    role = str(parsed["role"] or "")
    user_pk = _safe_int(parsed["user_pk"])
    if role not in {"student", "teacher"} or user_pk is None:
        raise ValueError("invalid AI reply requester")

    table_name = "students" if role == "student" else "teachers"
    row = conn.execute(
        f"SELECT id, name FROM {table_name} WHERE id = ? LIMIT 1",
        (user_pk,),
    ).fetchone()
    if row is None:
        raise ValueError("AI reply requester not found")
    return {
        "id": int(row["id"]),
        "role": role,
        "name": str(row["name"] or ""),
    }


def _claim_private_ai_reply_job(
    conn,
    job_id: int | str,
    *,
    engine: str | None = None,
) -> Optional[dict[str, Any]]:
    timestamp = _now_iso()
    db_engine = (engine or get_configured_db_engine()).strip().lower()
    if db_engine not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported private AI reply job database engine: {db_engine}")
    if db_engine == "postgres":
        cursor = conn.execute(
            """
            UPDATE private_message_ai_jobs
            SET status = ?,
                started_at = COALESCE(started_at, ?),
                finished_at = NULL,
                updated_at = ?,
                error_message = '',
                attempt_count = attempt_count + 1
            WHERE id IN (
                SELECT id
                FROM private_message_ai_jobs
                WHERE id = ?
                  AND status = ?
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (
                AI_REPLY_JOB_STATUS_RUNNING,
                timestamp,
                timestamp,
                int(job_id),
                AI_REPLY_JOB_STATUS_PENDING,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        conn.commit()
        return dict(row)

    cursor = conn.execute(
        """
        UPDATE private_message_ai_jobs
        SET status = ?,
            started_at = COALESCE(started_at, ?),
            finished_at = NULL,
            updated_at = ?,
            error_message = '',
            attempt_count = attempt_count + 1
        WHERE id = ?
          AND status = ?
        """,
        (
            AI_REPLY_JOB_STATUS_RUNNING,
            timestamp,
            timestamp,
            int(job_id),
            AI_REPLY_JOB_STATUS_PENDING,
        ),
    )
    if int(cursor.rowcount or 0) <= 0:
        return None
    row = conn.execute(
        """
        SELECT *
        FROM private_message_ai_jobs
        WHERE id = ?
        LIMIT 1
        """,
        (int(job_id),),
    ).fetchone()
    conn.commit()
    return dict(row) if row is not None else None


def _finish_private_ai_reply_job(
    conn,
    job_id: int | str,
    *,
    status: str,
    reply_message_id: Optional[int] = None,
    error_message: str = "",
) -> bool:
    timestamp = _now_iso()
    cursor = conn.execute(
        """
        UPDATE private_message_ai_jobs
        SET status = ?,
            reply_message_id = ?,
            error_message = ?,
            finished_at = ?,
            updated_at = ?
        WHERE id = ?
          AND status = ?
        """,
        (
            str(status or AI_REPLY_JOB_STATUS_FAILED),
            _safe_int(reply_message_id),
            _truncate_text(error_message, 240),
            timestamp,
            timestamp,
            int(job_id),
            AI_REPLY_JOB_STATUS_RUNNING,
        ),
    )
    return int(cursor.rowcount or 0) == 1


async def _process_claimed_private_ai_reply_job_row(job_row: dict[str, Any]) -> Optional[dict[str, Any]]:
    try:
        def _load_user_sync() -> dict[str, Any]:
            with get_db_connection() as conn:
                return _build_private_ai_job_user_snapshot(
                    conn,
                    requester_identity=str(job_row["requester_identity"] or ""),
                )

        user = await asyncio.to_thread(_load_user_sync)
        reply = await generate_ai_private_reply(
            user,
            class_offering_id=int(job_row["class_offering_id"]),
            conversation_key=str(job_row["conversation_key"] or ""),
        )
        def _finish_job_sync() -> None:
            with get_db_connection() as conn:
                _finish_private_ai_reply_job(
                    conn,
                    job_row["id"],
                    status=AI_REPLY_JOB_STATUS_COMPLETED if reply else AI_REPLY_JOB_STATUS_FAILED,
                    reply_message_id=reply.get("id") if reply else None,
                    error_message="" if reply else "AI 助教暂时没有成功生成回复",
                )
                conn.commit()

        await asyncio.to_thread(_finish_job_sync)
        return reply
    except Exception as exc:
        def _fail_job_sync() -> None:
            with get_db_connection() as conn:
                _finish_private_ai_reply_job(
                    conn,
                    job_row["id"],
                    status=AI_REPLY_JOB_STATUS_FAILED,
                    error_message=str(exc) or "AI 助教回复失败",
                )
                conn.commit()

        await asyncio.to_thread(_fail_job_sync)
        return None


async def process_private_ai_reply_job(job_id: int | str) -> Optional[dict[str, Any]]:
    def _claim_job_sync() -> Optional[dict[str, Any]]:
        with get_db_connection() as conn:
            return _claim_private_ai_reply_job(conn, job_id)

    job_row = await asyncio.to_thread(_claim_job_sync)
    if job_row is None:
        return None
    return await _process_claimed_private_ai_reply_job_row(job_row)


def _claim_pending_private_ai_reply_jobs_for_schedule(
    conn,
    *,
    limit: int,
    engine: str | None = None,
) -> list[dict[str, Any]]:
    db_engine = (engine or get_configured_db_engine()).strip().lower()
    if db_engine not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported private AI reply schedule database engine: {db_engine}")
    if db_engine != "postgres":
        raise ValueError("batch schedule claiming is only supported for PostgreSQL")
    timestamp = _now_iso()
    cursor = conn.execute(
        """
        UPDATE private_message_ai_jobs
        SET status = ?,
            started_at = COALESCE(started_at, ?),
            finished_at = NULL,
            updated_at = ?,
            error_message = '',
            attempt_count = attempt_count + 1
        WHERE id IN (
            SELECT id
            FROM private_message_ai_jobs
            WHERE status = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (
            AI_REPLY_JOB_STATUS_RUNNING,
            timestamp,
            timestamp,
            AI_REPLY_JOB_STATUS_PENDING,
            max(1, min(int(limit), 256)),
        ),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.commit()
    return rows


def schedule_pending_private_ai_reply_jobs(limit: int = 64) -> int:
    timestamp = _now_iso()
    engine = get_configured_db_engine()
    claimed_jobs: list[dict[str, Any]] = []
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE private_message_ai_jobs
            SET status = ?,
                updated_at = ?
            WHERE status = ?
            """,
            (
                AI_REPLY_JOB_STATUS_PENDING,
                timestamp,
                AI_REPLY_JOB_STATUS_RUNNING,
            ),
        )
        if engine == "postgres":
            claimed_jobs = _claim_pending_private_ai_reply_jobs_for_schedule(
                conn,
                limit=limit,
                engine=engine,
            )
            rows = []
        else:
            rows = conn.execute(
                """
                SELECT id
                FROM private_message_ai_jobs
                WHERE status = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (
                    AI_REPLY_JOB_STATUS_PENDING,
                    max(1, min(int(limit), 256)),
                ),
            ).fetchall()
            conn.commit()

    for job_row in claimed_jobs:
        asyncio.create_task(_process_claimed_private_ai_reply_job_row(job_row))
    for row in rows:
        asyncio.create_task(process_private_ai_reply_job(int(row["id"])))
    return len(claimed_jobs) if claimed_jobs else len(rows)


async def send_private_message_and_maybe_reply(
    user: dict,
    *,
    contact_identity: str,
    class_offering_id: Optional[int] = None,
    content: str,
    attachments: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    prepared_attachments = await prepare_private_message_uploads(list(attachments or []))

    def _send_message_sync() -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        with get_db_connection() as conn:
            result = create_private_message(
                conn,
                user,
                contact_identity=contact_identity,
                class_offering_id=class_offering_id,
                content=content,
                attachments=prepared_attachments,
            )
            ai_reply_job = None
            if result["requires_ai_reply"] and result["class_offering_id"] is not None:
                ai_reply_job = create_private_ai_reply_job(
                    conn,
                    user,
                    conversation_key=str(result["conversation_key"]),
                    class_offering_id=int(result["class_offering_id"]),
                    request_message_id=int(result["message"]["id"]),
                )
            conn.commit()
            return result, ai_reply_job

    result, ai_reply_job = await asyncio.to_thread(_send_message_sync)

    payload = {
        "contact": result["contact"],
        "class_offering_id": result["class_offering_id"],
        "conversation_key": result["conversation_key"],
        "sent_message": result["message_serialized"],
        "assistant_reply": None,
        "ai_reply_job": ai_reply_job,
    }
    return payload


def get_message_center_bootstrap(
    conn,
    user: dict,
    *,
    include_private: bool = True,
    include_private_data: bool = True,
) -> dict[str, Any]:
    payload = {
        "summary": get_message_center_summary(conn, user, include_private=include_private),
        "private_contacts": [],
        "private_blocks": [],
    }
    if include_private_data:
        payload["private_contacts"] = list_private_message_contacts(conn, user)
        payload["private_blocks"] = list_private_message_blocks(conn, user)
    return payload


def create_assignment_published_notifications(
    conn,
    assignment_id: int | str,
    *,
    send_email_notification: bool = False,
) -> int:
    assignment = conn.execute(
        """
        SELECT a.id, a.title, a.requirements_md, a.class_offering_id, a.course_id,
               c.name AS course_name, c.created_by_teacher_id,
               owner_t.name AS course_teacher_name,
               offering_t.id AS offering_teacher_id,
               offering_t.name AS offering_teacher_name
        FROM assignments a
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN teachers owner_t ON owner_t.id = c.created_by_teacher_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN teachers offering_t ON offering_t.id = o.teacher_id
        WHERE a.id = ?
        LIMIT 1
        """,
        (assignment_id,),
    ).fetchone()
    if not assignment:
        return 0

    teacher_id = _safe_int(assignment["offering_teacher_id"]) or _safe_int(assignment["created_by_teacher_id"])
    teacher_name = str(assignment["offering_teacher_name"] or assignment["course_teacher_name"] or "")
    if teacher_id is None:
        return 0

    if _safe_int(assignment["class_offering_id"]) is not None:
        student_rows = conn.execute(
            """
            SELECT s.id
            FROM class_offerings o
            JOIN students s ON s.class_id = o.class_id
            WHERE o.id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.id
            """,
            (int(assignment["class_offering_id"]),),
        ).fetchall()
    else:
        student_rows = conn.execute(
            """
            SELECT DISTINCT s.id
            FROM class_offerings o
            JOIN students s ON s.class_id = o.class_id
            WHERE o.course_id = ? AND o.teacher_id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.id
            """,
            (int(assignment["course_id"]), teacher_id),
        ).fetchall()

    inserted_count = 0
    actor_display_name = build_actor_display_name(teacher_name, "teacher")
    sender_display_name = actor_display_name
    mention_everyone = False
    for row in student_rows:
        notification_title = (
            f"{sender_display_name} 在课堂讨论中 @了所有人"
            if mention_everyone
            else f"{sender_display_name} 在课堂讨论中 @了你"
        )
        payload = _build_notification_payload(
            recipient_role="student",
            recipient_user_pk=int(row["id"]),
            category=MESSAGE_CATEGORY_ASSIGNMENT,
            title=f"新作业已发布：{assignment['title']}",
            body_preview=_truncate_text(assignment["requirements_md"] or assignment["course_name"], 120),
            actor_role="teacher",
            actor_user_pk=teacher_id,
            actor_display_name=actor_display_name,
            link_url=f"/assignment/{assignment['id']}",
            class_offering_id=_safe_int(assignment["class_offering_id"]),
            ref_type=MESSAGE_CATEGORY_ASSIGNMENT,
            ref_id=str(assignment["id"]),
            metadata={
                "assignment_id": assignment["id"],
                "course_id": assignment["course_id"],
                "send_email_notification": bool(send_email_notification),
            },
        )
        payload["email_notification_allowed"] = bool(send_email_notification)
        inserted_count += 1 if _insert_notification_if_allowed(
            conn,
            payload,
            allow_duplicates=bool(send_email_notification),
        ) else 0
    return inserted_count


def _load_submission_notification_context(conn, submission_id: int | str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT s.id, s.student_pk_id, s.student_name, s.started_at, s.submitted_at,
               s.status, s.score, s.feedback_md,
               a.id AS assignment_id, a.title AS assignment_title, a.class_offering_id,
               a.course_id, a.exam_paper_id,
               course.name AS course_name,
               course.department AS course_department,
               course.created_by_teacher_id,
               student_class.name AS student_class_name,
               student_class.department AS student_class_department,
               offering_class.name AS offering_class_name,
               offering_class.department AS offering_class_department,
               owner_t.name AS course_teacher_name,
               offering_t.id AS offering_teacher_id,
               offering_t.name AS offering_teacher_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses course ON course.id = a.course_id
        LEFT JOIN students st ON st.id = s.student_pk_id
        LEFT JOIN classes student_class ON student_class.id = st.class_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN classes offering_class ON offering_class.id = o.class_id
        LEFT JOIN teachers owner_t ON owner_t.id = course.created_by_teacher_id
        LEFT JOIN teachers offering_t ON offering_t.id = o.teacher_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["teacher_id"] = _safe_int(item.get("offering_teacher_id")) or _safe_int(item.get("created_by_teacher_id"))
    item["teacher_name"] = str(item.get("offering_teacher_name") or item.get("course_teacher_name") or "")
    item["class_name"] = str(item.get("offering_class_name") or item.get("student_class_name") or "").strip()
    item["department"] = str(
        item.get("offering_class_department")
        or item.get("student_class_department")
        or item.get("course_department")
        or ""
    ).strip()
    item["work_type"] = "考试" if str(item.get("exam_paper_id") or "").strip() else "作业"
    item["duration_label"] = _format_duration_label(item.get("started_at"), item.get("submitted_at"))
    return item


def _teacher_submission_body_preview(context: dict[str, Any], *, issue_detail: str = "") -> str:
    department = str(context.get("department") or "未填写系别")
    class_name = str(context.get("class_name") or "未填写班级")
    student_name = str(context.get("student_name") or "学生")
    work_type = str(context.get("work_type") or "作业")
    assignment_title = str(context.get("assignment_title") or "未命名任务")
    duration_label = str(context.get("duration_label") or "待确认")
    parts = [
        f"系别：{department}",
        f"班级：{class_name}",
        f"姓名：{student_name}",
        f"完成：{work_type}《{assignment_title}》",
        f"耗时：{duration_label}",
    ]
    if issue_detail:
        parts.append(f"处理：{_truncate_text(issue_detail, 80)}")
    return " | ".join(parts)


def _teacher_submission_metadata(context: dict[str, Any], *, issue_detail: str = "") -> dict[str, Any]:
    metadata = {
        "submission_id": context.get("id"),
        "assignment_id": context.get("assignment_id"),
        "course_id": context.get("course_id"),
        "course_name": context.get("course_name"),
        "department": context.get("department"),
        "class_name": context.get("class_name"),
        "student_name": context.get("student_name"),
        "work_type": context.get("work_type"),
        "assignment_title": context.get("assignment_title"),
        "started_at": context.get("started_at"),
        "submitted_at": context.get("submitted_at"),
        "duration_label": context.get("duration_label"),
    }
    if issue_detail:
        metadata["issue_detail"] = _truncate_text(issue_detail, 500)
    return metadata


def create_submission_notification(conn, submission_id: int | str) -> int:
    submission = _load_submission_notification_context(conn, submission_id)
    if not submission:
        return 0

    teacher_id = _safe_int(submission.get("teacher_id"))
    if teacher_id is None:
        return 0

    work_type = str(submission.get("work_type") or "作业")
    payload = _build_notification_payload(
        recipient_role="teacher",
        recipient_user_pk=teacher_id,
        category=MESSAGE_CATEGORY_SUBMISSION,
        title=f"{submission['student_name']} 提交了{work_type}",
        body_preview=_teacher_submission_body_preview(submission),
        actor_role="student",
        actor_user_pk=int(submission["student_pk_id"]),
        actor_display_name=str(submission["student_name"] or "学生"),
        link_url=f"/submission/{submission['id']}",
        class_offering_id=_safe_int(submission["class_offering_id"]),
        ref_type=MESSAGE_CATEGORY_SUBMISSION,
        ref_id=f"{submission['id']}:{submission.get('submitted_at') or ''}",
        metadata=_teacher_submission_metadata(submission),
    )
    return 1 if _insert_notification_if_allowed(conn, payload) else 0


def create_teacher_grading_issue_notification(
    conn,
    submission_id: int | str,
    *,
    issue_detail: str = "",
    ref_suffix: str = "grading_failed",
) -> int:
    submission = _load_submission_notification_context(conn, submission_id)
    if not submission:
        return 0

    teacher_id = _safe_int(submission.get("teacher_id"))
    if teacher_id is None:
        return 0

    work_type = str(submission.get("work_type") or "作业")
    detail = _truncate_text(issue_detail or "批改过程遇到异常，需要教师查看并处理。", 180)
    timestamp = _now_iso()
    payload = _build_notification_payload(
        recipient_role="teacher",
        recipient_user_pk=teacher_id,
        category=MESSAGE_CATEGORY_AI_FEEDBACK,
        severity="system",
        title=f"批改需要处理：{submission['student_name']} 的{work_type}",
        body_preview=_teacher_submission_body_preview(submission, issue_detail=detail),
        actor_role=AI_ASSISTANT_ROLE,
        actor_user_pk=None,
        actor_display_name=AI_ASSISTANT_LABEL,
        link_url=f"/submission/{submission['id']}",
        class_offering_id=_safe_int(submission["class_offering_id"]),
        ref_type=MESSAGE_CATEGORY_AI_FEEDBACK,
        ref_id=f"{submission['id']}:{ref_suffix}:{submission.get('submitted_at') or ''}",
        metadata=_teacher_submission_metadata(submission, issue_detail=detail),
        created_at=timestamp,
    )
    return 1 if _insert_notification_if_allowed(conn, payload) else 0


def create_student_grading_notification(
    conn,
    submission_id: int | str,
    *,
    actor_role: str,
    actor_user_pk: Optional[int] = None,
    actor_display_name: str = "",
) -> int:
    submission_row = conn.execute(
        """
        SELECT s.id, s.student_pk_id, s.student_name, s.score, s.feedback_md,
               a.id AS assignment_id, a.title AS assignment_title, a.class_offering_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not submission_row:
        return 0
    submission = dict(submission_row)

    normalized_actor_role = str(actor_role or "").strip().lower()
    normalized_actor_name = str(actor_display_name or "").strip()
    if normalized_actor_role == AI_ASSISTANT_ROLE and not normalized_actor_name:
        normalized_actor_name = AI_ASSISTANT_LABEL
    timestamp = _now_iso()
    score_text = "待公布" if submission["score"] is None else f"得分 {submission['score']}"
    feedback_preview = _truncate_text(submission["feedback_md"] or "", 80)
    body_preview = _personalize_student_grading_body(conn, submission, score_text, feedback_preview)
    payload = _build_notification_payload(
        recipient_role="student",
        recipient_user_pk=int(submission["student_pk_id"]),
        category=MESSAGE_CATEGORY_GRADING_RESULT,
        title=f"作业已批改：{submission['assignment_title']}",
        body_preview=body_preview,
        actor_role=normalized_actor_role,
        actor_user_pk=actor_user_pk,
        actor_display_name=normalized_actor_name,
        link_url=f"/submission/{submission['id']}",
        class_offering_id=_safe_int(submission["class_offering_id"]),
        ref_type=MESSAGE_CATEGORY_GRADING_RESULT,
        ref_id=f"{submission['id']}:{timestamp}",
        metadata={
            "submission_id": submission["id"],
            "assignment_id": submission["assignment_id"],
            "score": submission["score"],
        },
        created_at=timestamp,
    )
    return 1 if _insert_notification_if_allowed(conn, payload, allow_duplicates=True) else 0


def create_learning_progress_notification(
    conn,
    *,
    recipient_role: str,
    recipient_user_pk: int,
    title: str,
    body_preview: str = "",
    link_url: str = "",
    class_offering_id: Optional[int] = None,
    ref_id: str = "",
    actor_role: str = "",
    actor_user_pk: Optional[int] = None,
    actor_display_name: str = "",
    metadata: Optional[dict[str, Any]] = None,
    allow_duplicates: bool = False,
) -> int:
    timestamp = _now_iso()
    normalized_recipient_role = str(recipient_role or "").strip().lower()
    normalized_body_preview = _truncate_text(body_preview, 140)
    if normalized_recipient_role == "student":
        normalized_body_preview = _personalize_student_learning_body(
            conn,
            class_offering_id=class_offering_id,
            student_id=recipient_user_pk,
            body_preview=normalized_body_preview,
        )
    payload = _build_notification_payload(
        recipient_role=normalized_recipient_role,
        recipient_user_pk=int(recipient_user_pk),
        category=MESSAGE_CATEGORY_LEARNING_PROGRESS,
        title=_truncate_text(title, 80),
        body_preview=normalized_body_preview,
        actor_role=str(actor_role or "").strip().lower(),
        actor_user_pk=actor_user_pk,
        actor_display_name=str(actor_display_name or "").strip(),
        link_url=link_url,
        class_offering_id=_safe_int(class_offering_id),
        ref_type=MESSAGE_CATEGORY_LEARNING_PROGRESS,
        ref_id=ref_id or f"learning:{recipient_role}:{recipient_user_pk}:{timestamp}",
        metadata=metadata or {},
        created_at=timestamp,
    )
    return 1 if _insert_notification_if_allowed(conn, payload, allow_duplicates=allow_duplicates) else 0


def create_todo_notification(
    conn,
    *,
    recipient_role: str,
    recipient_user_pk: int,
    title: str,
    body_preview: str = "",
    link_url: str = "",
    class_offering_id: Optional[int] = None,
    ref_id: str = "",
    actor_role: str = "",
    actor_user_pk: Optional[int] = None,
    actor_display_name: str = "",
    metadata: Optional[dict[str, Any]] = None,
    allow_duplicates: bool = False,
) -> int:
    timestamp = _now_iso()
    payload = _build_notification_payload(
        recipient_role=str(recipient_role or "").strip().lower(),
        recipient_user_pk=int(recipient_user_pk),
        category=MESSAGE_CATEGORY_TODO,
        title=_truncate_text(title, 80),
        body_preview=_truncate_text(body_preview, 140),
        actor_role=str(actor_role or "").strip().lower(),
        actor_user_pk=actor_user_pk,
        actor_display_name=str(actor_display_name or "").strip(),
        link_url=link_url,
        class_offering_id=_safe_int(class_offering_id),
        ref_type=MESSAGE_CATEGORY_TODO,
        ref_id=ref_id or f"todo:{recipient_role}:{recipient_user_pk}:{timestamp}",
        metadata=metadata or {},
        created_at=timestamp,
    )
    return 1 if _insert_notification_if_allowed(conn, payload, allow_duplicates=allow_duplicates) else 0


def create_academic_exam_notification(
    conn,
    *,
    recipient_role: str,
    recipient_user_pk: int,
    title: str,
    body_preview: str = "",
    link_url: str = "",
    class_offering_id: Optional[int] = None,
    ref_id: str = "",
    actor_display_name: str = "",
    metadata: Optional[dict[str, Any]] = None,
    allow_duplicates: bool = False,
) -> int:
    timestamp = _now_iso()
    payload = _build_notification_payload(
        recipient_role=str(recipient_role or "").strip().lower(),
        recipient_user_pk=int(recipient_user_pk),
        category=MESSAGE_CATEGORY_ACADEMIC_EXAM,
        severity="important",
        title=_truncate_text(title, 80),
        body_preview=_truncate_text(body_preview, 140),
        actor_role="",
        actor_user_pk=None,
        actor_display_name=str(actor_display_name or "").strip(),
        link_url=link_url,
        class_offering_id=_safe_int(class_offering_id),
        ref_type=MESSAGE_CATEGORY_ACADEMIC_EXAM,
        ref_id=ref_id or f"academic-exam:{recipient_role}:{recipient_user_pk}:{timestamp}",
        metadata=metadata or {},
        created_at=timestamp,
    )
    return 1 if _insert_notification_if_allowed(conn, payload, allow_duplicates=allow_duplicates) else 0


def create_smart_attendance_alert_notification(
    conn,
    *,
    student_id: int | str,
    class_offering_id: int | str,
    course_name: str,
    absences: list[dict[str, Any]],
    reminder_date: str,
) -> int:
    normalized_student_id = _safe_int(student_id)
    normalized_class_offering_id = _safe_int(class_offering_id)
    if normalized_student_id is None or normalized_class_offering_id is None or not absences:
        return 0

    course_title = _sanitize_student_notification_text(course_name or "本课程", limit=48)
    detail_items: list[str] = []
    for item in absences[:12]:
        item_course = _sanitize_student_notification_text(item.get("course_name") or course_title, limit=48)
        date_label = _sanitize_student_notification_text(item.get("date_label") or item.get("date") or "日期待确认", limit=24)
        weekday_label = _sanitize_student_notification_text(item.get("weekday_label") or "", limit=12)
        week_label = _sanitize_student_notification_text(item.get("week_label") or "", limit=16)
        suffix = "，".join(part for part in (weekday_label, week_label) if part)
        detail_items.append(f"{item_course}：{date_label}{f'（{suffix}）' if suffix else ''}")
    if len(absences) > len(detail_items):
        detail_items.append(f"另有 {len(absences) - len(detail_items)} 条记录请进入课堂查看。")

    body_preview = _sanitize_student_notification_text(
        "考勤有异常，请登录考勤网站/小程序/app确认。缺勤课程、日期、星期、周次："
        + "；".join(detail_items),
        limit=700,
    )
    payload = _build_notification_payload(
        recipient_role="student",
        recipient_user_pk=normalized_student_id,
        category=MESSAGE_CATEGORY_ATTENDANCE_ALERT,
        severity="important",
        title=f"考勤异常提醒：{course_title}",
        body_preview=body_preview,
        link_url=f"/classroom/{normalized_class_offering_id}",
        class_offering_id=normalized_class_offering_id,
        ref_type="smart_attendance_absence_daily",
        ref_id=f"{normalized_class_offering_id}:{normalized_student_id}:{str(reminder_date or '').strip()}",
        metadata={
            "course_name": course_title,
            "reminder_date": str(reminder_date or "").strip(),
            "absence_count": len(absences),
            "absences": absences[:30],
        },
    )
    payload["email_notification_allowed"] = False
    return 1 if _insert_notification_if_allowed(conn, payload) else 0


def create_collaboration_notification(
    conn,
    *,
    recipient_role: str,
    recipient_user_pk: int,
    title: str,
    body_preview: str = "",
    link_url: str = "",
    class_offering_id: Optional[int] = None,
    ref_id: str = "",
    actor_role: str = "",
    actor_user_pk: Optional[int] = None,
    actor_display_name: str = "",
    metadata: Optional[dict[str, Any]] = None,
    allow_duplicates: bool = False,
) -> int:
    timestamp = _now_iso()
    payload = _build_notification_payload(
        recipient_role=str(recipient_role or "").strip().lower(),
        recipient_user_pk=int(recipient_user_pk),
        category=MESSAGE_CATEGORY_COLLABORATION,
        title=_truncate_text(title, 80),
        body_preview=_truncate_text(body_preview, 140),
        actor_role=str(actor_role or "").strip().lower(),
        actor_user_pk=actor_user_pk,
        actor_display_name=str(actor_display_name or "").strip(),
        link_url=link_url,
        class_offering_id=_safe_int(class_offering_id),
        ref_type=MESSAGE_CATEGORY_COLLABORATION,
        ref_id=ref_id or f"collaboration:{recipient_role}:{recipient_user_pk}:{timestamp}",
        metadata=metadata or {},
        created_at=timestamp,
    )
    return 1 if _insert_notification_if_allowed(conn, payload, allow_duplicates=allow_duplicates) else 0


def create_teacher_ai_feedback_notification(conn, submission_id: int | str) -> int:
    return 0


def _contains_broadcast_discussion_mention(text: str) -> bool:
    normalized_text = str(text or "").strip()
    return any(token in normalized_text for token in BROADCAST_DISCUSSION_TOKENS)


def create_discussion_mention_notifications(
    conn,
    *,
    class_offering_id: int,
    sender_user: dict,
    sender_display_name: str,
    message_text: str,
    message_id: int | str,
) -> int:
    normalized_text = str(message_text or "").strip()
    if "@" not in normalized_text:
        return 0

    from .chat_handler import manager

    sender_user_pk, sender_role, sender_identity = _ensure_user_identity(sender_user)
    mention_everyone = sender_role == "teacher" and _contains_broadcast_discussion_mention(normalized_text)
    offering = conn.execute(
        """
        SELECT o.class_id, t.id AS teacher_id, t.name AS teacher_name
        FROM class_offerings o
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (class_offering_id,),
    ).fetchone()
    if not offering:
        return 0

    candidates: dict[str, dict[str, Any]] = {}

    teacher_identity = build_user_identity("teacher", offering["teacher_id"])
    candidates[teacher_identity] = {
        "recipient_role": "teacher",
        "recipient_user_pk": int(offering["teacher_id"]),
        "display_name": build_actor_display_name(str(offering["teacher_name"] or ""), "teacher"),
        "tokens": {
            f"@{str(offering['teacher_name'] or '').strip()}",
            f"@{build_actor_display_name(str(offering['teacher_name'] or ''), 'teacher')}",
            "@老师",
        },
    }

    student_rows = conn.execute(
        """
        SELECT id, name
        FROM students
        WHERE class_id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        ORDER BY id
        """,
        (int(offering["class_id"]),),
    ).fetchall()
    for row in student_rows:
        identity = build_user_identity("student", row["id"])
        candidates[identity] = {
            "recipient_role": "student",
            "recipient_user_pk": int(row["id"]),
            "display_name": str(row["name"] or "同学"),
            "tokens": {f"@{str(row['name'] or '').strip()}"},
        }

    room_user_info = getattr(manager, "room_user_info", {}).get(class_offering_id, {})
    for participant_key, room_user in room_user_info.items():
        role = str(room_user.get("role") or "").strip().lower()
        user_pk = _safe_int(room_user.get("id"))
        if role not in {"student", "teacher"} or user_pk is None:
            continue
        identity = build_user_identity(role, user_pk)
        display_name = manager.get_display_name(
            class_offering_id,
            str(participant_key),
            str(room_user.get("name") or ""),
        )
        if identity not in candidates:
            candidates[identity] = {
                "recipient_role": role,
                "recipient_user_pk": user_pk,
                "display_name": display_name or build_actor_display_name(str(room_user.get("name") or ""), role),
                "tokens": set(),
            }
        if display_name:
            candidates[identity]["tokens"].add(f"@{display_name}")

    inserted_count = 0
    for identity, candidate in candidates.items():
        if identity == sender_identity:
            continue
        tokens = [
            token
            for token in candidate["tokens"]
            if str(token or "").strip()
        ]
        tokens.sort(key=len, reverse=True)
        if not mention_everyone and not any(token in normalized_text for token in tokens):
            continue

        payload = _build_notification_payload(
            recipient_role=str(candidate["recipient_role"]),
            recipient_user_pk=int(candidate["recipient_user_pk"]),
            category=MESSAGE_CATEGORY_DISCUSSION_MENTION,
            title=f"{sender_display_name} 在课堂讨论中 @了你",
            body_preview=_truncate_text(normalized_text, 120),
            actor_role=sender_role,
            actor_user_pk=sender_user_pk,
            actor_display_name=str(sender_display_name or ""),
            link_url=f"/classroom/{class_offering_id}",
            class_offering_id=class_offering_id,
            ref_type=MESSAGE_CATEGORY_DISCUSSION_MENTION,
            ref_id=f"{message_id}:{identity}",
            metadata={
                "class_offering_id": class_offering_id,
                "message_id": message_id,
            },
        )
        if mention_everyone:
            payload["title"] = f"{sender_display_name} 在课堂讨论中 @了所有人"
        inserted_count += 1 if _insert_notification_if_allowed(conn, payload) else 0
    return inserted_count
