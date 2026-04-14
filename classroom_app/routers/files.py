import asyncio
import json
import math
import uuid
import hashlib
import shutil
import aiofiles
from datetime import datetime
from urllib.parse import quote, urlsplit

# 导入聊天管理器和 json
from fastapi import WebSocket, status, WebSocketDisconnect, UploadFile, File, Form, APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..config import GLOBAL_FILES_DIR, UPLOAD_CHUNK_SIZE_BYTES, CHUNKED_UPLOADS_DIR
from ..dependencies import verify_token, get_current_user, get_current_teacher, normalize_ip
# 导入聊天管理器
from ..services.chat_handler import (
    load_older_history_payload,
    manager,
    row_to_chat_message,
    save_chat_message,
)

from typing import Optional
from pathlib import Path

from ..database import get_db_connection
from ..services.discussion_ai_service import (
    DISCUSSION_AI_ASSISTANT_NAME,
    DISCUSSION_AI_USER_ID,
    contains_discussion_ai_mention,
    generate_discussion_ai_reply,
    record_alias_switch_activity,
    record_message_activity,
)
from ..services.discussion_mood_service import (
    get_discussion_mood_payload,
    maybe_schedule_discussion_mood_refresh,
)
from ..services.discussion_attachment_service import (
    DISCUSSION_ATTACHMENT_MAX_BYTES,
    MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE,
    create_discussion_attachment,
    load_discussion_attachment_row,
    resolve_discussion_attachment_payloads,
)
from ..services.emoji_service import increment_emoji_usage, resolve_custom_emoji_payloads
from ..services.file_handler import delete_file_safely
from ..services.file_service import save_file_globally, get_file_lock, stream_file
from ..services.download_policy import apply_download_policy, ensure_download_allowed
from ..services.message_center_service import create_discussion_mention_notifications
from ..services.submission_preview_service import (
    build_submission_file_preview_payload,
    ensure_submission_file_access,
    serialize_submission_file_row,
)
from ..services.rate_limit_service import (
    RateLimitExceededError,
    build_rate_limit_window_start,
    calculate_retry_after_seconds,
)

# --- 新增：专门针对 Windows 系统的并发保护限流器 ---
# 允许同时最多 80 个物理读取流，既能打满内网千兆带宽，又能完美避开 Windows 文件句柄上限崩溃
windows_io_semaphore = asyncio.Semaphore(80)
DISCUSSION_MESSAGE_RATE_LIMIT = 10
DISCUSSION_MESSAGE_RATE_WINDOW_SECONDS = 60


def _ensure_classroom_access_for_user(conn, class_offering_id: int, user: Optional[dict]):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        user_pk = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Invalid user")

    offering = conn.execute(
        "SELECT id, class_id, teacher_id FROM class_offerings WHERE id = ?",
        (class_offering_id,),
    ).fetchone()
    if not offering:
        raise HTTPException(status_code=404, detail="Classroom not found")

    if user.get("role") == "teacher":
        if int(offering["teacher_id"]) != user_pk:
            raise HTTPException(status_code=403, detail="Permission denied")
    elif user.get("role") == "student":
        student_class = conn.execute(
            "SELECT class_id FROM students WHERE id = ?",
            (user_pk,),
        ).fetchone()
        if not student_class or int(student_class["class_id"]) != int(offering["class_id"]):
            raise HTTPException(status_code=403, detail="Permission denied")
    else:
        raise HTTPException(status_code=403, detail="Permission denied")

    return offering


def _ensure_course_file_access(conn, file_row, user: Optional[dict]):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        user_pk = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Invalid user")

    if user.get("role") == "teacher":
        if int(file_row["created_by_teacher_id"]) != user_pk:
            raise HTTPException(status_code=403, detail="Permission denied")
        return

    if user.get("role") != "student":
        raise HTTPException(status_code=403, detail="Permission denied")

    if int(file_row["is_teacher_resource"] or 0):
        raise HTTPException(status_code=403, detail="无权访问教师资源")
    if not int(file_row["is_public"] or 0):
        raise HTTPException(status_code=403, detail="当前文件未对学生开放")

    classroom = conn.execute(
        """
        SELECT o.id
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.course_id = ?
          AND s.id = ?
        LIMIT 1
        """,
        (file_row["course_id"], user_pk),
    ).fetchone()
    if classroom is None:
        raise HTTPException(status_code=403, detail="Permission denied")


def _normalize_shared_file_description(raw_description: object) -> str:
    description = str(raw_description or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return description[:5000]


def _normalize_shared_file_original_link(raw_link: object) -> str:
    normalized = str(raw_link or "").strip()
    if not normalized:
        return ""
    if len(normalized) > 2048:
        raise HTTPException(status_code=400, detail="原始链接长度不能超过 2048 个字符")

    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="原始链接仅支持 http 或 https 地址")

    return normalized


def _update_course_file_metadata(
    conn,
    *,
    file_id: int,
    teacher_id: int,
    description: str | None = None,
    original_link: str | None = None,
):
    file_row = conn.execute(
        """
        SELECT cf.id, cf.file_name, cf.course_id, cf.description, cf.original_link
        FROM course_files cf
        JOIN courses c ON cf.course_id = c.id
        WHERE cf.id = ? AND c.created_by_teacher_id = ?
        """,
        (file_id, teacher_id),
    ).fetchone()
    if not file_row:
        raise HTTPException(status_code=404, detail="文件不存在或无操作权限")

    normalized_description = (
        _normalize_shared_file_description(description)
        if description is not None
        else str(file_row["description"] or "")
    )
    normalized_original_link = (
        _normalize_shared_file_original_link(original_link)
        if original_link is not None
        else str(file_row["original_link"] or "")
    )

    conn.execute(
        "UPDATE course_files SET description = ?, original_link = ? WHERE id = ?",
        (normalized_description, normalized_original_link, file_id),
    )
    return {
        "id": int(file_row["id"]),
        "course_id": int(file_row["course_id"]),
        "file_name": str(file_row["file_name"] or ""),
        "description": normalized_description,
        "original_link": normalized_original_link,
    }


def _build_discussion_quote_payload(message_payload: dict | None) -> Optional[dict]:
    if not isinstance(message_payload, dict):
        return None

    quote_payload = {
        "id": message_payload.get("id"),
        "sender": message_payload.get("sender") or "课堂成员",
        "role": message_payload.get("role") or "student",
        "message": message_payload.get("message") or "",
        "timestamp": message_payload.get("timestamp") or "",
        "logged_at": message_payload.get("logged_at"),
        "message_type": message_payload.get("message_type") or "text",
    }
    if message_payload.get("custom_emojis"):
        quote_payload["custom_emojis"] = message_payload.get("custom_emojis")
    if message_payload.get("attachments"):
        quote_payload["attachments"] = message_payload.get("attachments")
    return quote_payload


def _load_discussion_quote_payload(conn, class_offering_id: int, quote_message_id: object) -> Optional[dict]:
    try:
        normalized_id = int(quote_message_id)
    except (TypeError, ValueError):
        return None
    if normalized_id <= 0:
        return None

    row = conn.execute(
        """
        SELECT
            id,
            user_id,
            user_name,
            user_role,
            message,
            timestamp,
            logged_at,
            message_type,
            emoji_payload_json,
            attachments_json,
            quote_message_id,
            quote_payload_json
        FROM chat_logs
        WHERE class_offering_id = ?
          AND id = ?
        LIMIT 1
        """,
        (class_offering_id, normalized_id),
    ).fetchone()
    if row is None:
        return None

    return _build_discussion_quote_payload(row_to_chat_message(row))


def _enforce_discussion_message_rate_limit(conn, *, user_pk: int, user_role: str) -> None:
    now, window_start = build_rate_limit_window_start(window_seconds=DISCUSSION_MESSAGE_RATE_WINDOW_SECONDS)
    rows = conn.execute(
        """
        SELECT id, COALESCE(logged_at, timestamp) AS logged_at_value
        FROM chat_logs
        WHERE user_id = ?
          AND user_role = ?
          AND COALESCE(logged_at, timestamp) >= ?
        ORDER BY COALESCE(logged_at, timestamp) ASC, id ASC
        LIMIT ?
        """,
        (
            str(user_pk),
            str(user_role),
            window_start,
            DISCUSSION_MESSAGE_RATE_LIMIT,
        ),
    ).fetchall()
    if len(rows) < DISCUSSION_MESSAGE_RATE_LIMIT:
        return

    retry_after_seconds = calculate_retry_after_seconds(
        oldest_event_at=rows[0]["logged_at_value"],
        window_seconds=DISCUSSION_MESSAGE_RATE_WINDOW_SECONDS,
        now=now,
    )
    raise RateLimitExceededError(
        "\u53d1\u4fe1\u592a\u9891\u7e41\u7a0d\u540e\u518d\u53d1",
        retry_after_seconds=retry_after_seconds,
    )


def _prepare_discussion_message_context_sync(
    *,
    class_offering_id: int,
    user: dict,
    user_pk: int,
    user_role: str,
    normalized_text: str,
    requested_custom_ids: list,
    requested_attachment_ids: list,
    quote_message_id: Optional[object],
) -> dict:
    with get_db_connection() as conn:
        _ensure_classroom_access_for_user(conn, class_offering_id, user)
        custom_emoji_payloads = resolve_custom_emoji_payloads(
            conn,
            class_offering_id,
            requested_custom_ids,
            user,
        )
        attachment_payloads = resolve_discussion_attachment_payloads(
            conn,
            class_offering_id,
            requested_attachment_ids,
            user,
        )
        quote_payload = _load_discussion_quote_payload(conn, class_offering_id, quote_message_id)

        if normalized_text or custom_emoji_payloads or attachment_payloads or quote_payload:
            _enforce_discussion_message_rate_limit(
                conn,
                user_pk=user_pk,
                user_role=user_role,
            )

    return {
        "custom_emoji_payloads": custom_emoji_payloads,
        "attachment_payloads": attachment_payloads,
        "quote_payload": quote_payload,
    }


def _finalize_discussion_message_sync(
    *,
    class_offering_id: int,
    user: dict,
    requested_unicode_emojis: list,
    custom_emoji_payloads: list[dict],
    normalized_text: str,
    display_name: str,
    stored_message_id: int,
    used_at: str,
) -> None:
    with get_db_connection() as conn:
        increment_emoji_usage(
            conn,
            class_offering_id,
            user,
            requested_unicode_emojis,
            [item["id"] for item in custom_emoji_payloads],
            used_at=used_at,
        )
        try:
            create_discussion_mention_notifications(
                conn,
                class_offering_id=class_offering_id,
                sender_user=user,
                sender_display_name=display_name,
                message_text=normalized_text,
                message_id=stored_message_id,
            )
        except Exception as exc:
            print(f"[MESSAGE_CENTER] discussion mention notify failed: {exc}")
        conn.commit()


def _ensure_websocket_room_access_sync(class_offering_id: int, user: dict, user_pk: int) -> None:
    with get_db_connection() as conn:
        offering = conn.execute(
            "SELECT id, class_id, teacher_id FROM class_offerings WHERE id = ?",
            (class_offering_id,),
        ).fetchone()

        if not offering:
            raise HTTPException(status_code=403, detail="Classroom not found")

        if user.get("role") == "teacher":
            if int(offering["teacher_id"]) != user_pk:
                raise HTTPException(status_code=403, detail="Permission denied")
            return

        if user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Permission denied")

        student_class = conn.execute(
            "SELECT class_id FROM students WHERE id = ?",
            (user_pk,),
        ).fetchone()
        if not student_class or int(student_class["class_id"]) != int(offering["class_id"]):
            raise HTTPException(status_code=403, detail="Permission denied")


async def _broadcast_discussion_ai_reply(class_offering_id: int, reply_text: str) -> None:
    now = datetime.now()
    stored_message = await save_chat_message(class_offering_id, {
        "type": "chat",
        "sender": DISCUSSION_AI_ASSISTANT_NAME,
        "role": "assistant",
        "message": reply_text,
        "message_type": "text",
        "timestamp": now.strftime("%H:%M"),
        "class_offering_id": class_offering_id,
        "user_id": DISCUSSION_AI_USER_ID,
        "logged_at": now.isoformat(),
    })
    await manager.broadcast(class_offering_id, json.dumps(stored_message, ensure_ascii=False))


async def _handle_discussion_ai_mention(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    caller_display_name: str,
    original_text: str,
    current_message_id: int,
    current_message_attachments: Optional[list[dict]] = None,
    current_quote: Optional[dict] = None,
) -> None:
    try:
        reply_text = await generate_discussion_ai_reply(
            class_offering_id=class_offering_id,
            user_pk=user_pk,
            user_role=user_role,
            caller_display_name=caller_display_name,
            original_text=original_text,
            current_message_id=current_message_id,
            current_message_attachments=current_message_attachments or [],
            current_quote=current_quote,
        )
        if not str(reply_text or "").strip():
            return
        await _broadcast_discussion_ai_reply(class_offering_id, reply_text)
    except Exception as exc:
        print(f"[DISCUSSION_AI] 课堂助教发送失败: {exc}")


async def _process_discussion_chat_message(
    class_offering_id: int,
    user: dict,
    ws_user: dict,
    user_pk: int,
    client_id: str,
    message_text: str,
    requested_custom_ids: Optional[list] = None,
    requested_unicode_emojis: Optional[list] = None,
    requested_attachment_ids: Optional[list] = None,
    quote_message_id: Optional[object] = None,
) -> None:
    normalized_text = str(message_text or "").strip()
    requested_custom_ids = requested_custom_ids or []
    requested_unicode_emojis = requested_unicode_emojis or []
    requested_attachment_ids = requested_attachment_ids or []

    prepared_context = await asyncio.to_thread(
        _prepare_discussion_message_context_sync,
        class_offering_id=class_offering_id,
        user=user,
        user_pk=user_pk,
        user_role=str(ws_user["role"]),
        normalized_text=normalized_text,
        requested_custom_ids=list(requested_custom_ids),
        requested_attachment_ids=list(requested_attachment_ids),
        quote_message_id=quote_message_id,
    )
    custom_emoji_payloads = prepared_context["custom_emoji_payloads"]
    attachment_payloads = prepared_context["attachment_payloads"]
    quote_payload = prepared_context["quote_payload"]

    if not normalized_text and not custom_emoji_payloads and not attachment_payloads and not quote_payload:
        return

    now = datetime.now()
    display_time = now.strftime("%H:%M")
    display_name = manager.get_display_name(class_offering_id, client_id, ws_user['name'])
    stored_message = await save_chat_message(class_offering_id, {
        "type": "chat",
        "sender": display_name,
        "role": ws_user['role'],
        "message": normalized_text,
        "message_type": "rich" if (custom_emoji_payloads or attachment_payloads or quote_payload) else "text",
        "custom_emojis": custom_emoji_payloads,
        "attachments": attachment_payloads,
        "quote_message_id": quote_payload.get("id") if isinstance(quote_payload, dict) else None,
        "quote": quote_payload,
        "timestamp": display_time,
        "class_offering_id": class_offering_id,
        "user_id": user_pk,
        "logged_at": now.isoformat(),
    })

    await asyncio.to_thread(
        _finalize_discussion_message_sync,
        class_offering_id=class_offering_id,
        user=user,
        requested_unicode_emojis=list(requested_unicode_emojis),
        custom_emoji_payloads=list(custom_emoji_payloads),
        normalized_text=normalized_text,
        display_name=display_name,
        stored_message_id=int(stored_message["id"]),
        used_at=now.isoformat(),
    )

    profile_trigger = record_message_activity(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=str(ws_user["role"]),
        display_name=display_name,
        message_text=normalized_text,
        unicode_emojis=[str(item) for item in requested_unicode_emojis if str(item).strip()],
        custom_emoji_labels=[str(item.get("name") or "自定义表情") for item in custom_emoji_payloads],
        attachment_names=[str(item.get("name") or "图片") for item in attachment_payloads],
        quoted_message_id=int(quote_payload["id"]) if isinstance(quote_payload, dict) and quote_payload.get("id") else None,
        mentioned_assistant=contains_discussion_ai_mention(normalized_text),
    )

    await manager.broadcast(class_offering_id, json.dumps(stored_message, ensure_ascii=False))
    await maybe_schedule_discussion_mood_refresh(
        class_offering_id,
        reason="message",
        latest_message_id=int(stored_message["id"]),
    )

    if contains_discussion_ai_mention(normalized_text):
        asyncio.create_task(
            _handle_discussion_ai_mention(
                class_offering_id=class_offering_id,
                user_pk=user_pk,
                user_role=str(ws_user["role"]),
                caller_display_name=display_name,
                original_text=normalized_text,
                current_message_id=int(stored_message["id"]),
                current_message_attachments=stored_message.get("attachments") or [],
                current_quote=stored_message.get("quote"),
            )
        )


async def _send_discussion_rate_limit_message(websocket: WebSocket, exc: RateLimitExceededError) -> None:
    await websocket.send_text(json.dumps({
        "type": "send_rate_limited",
        "message": str(exc),
        "retry_after_seconds": max(int(getattr(exc, "retry_after_seconds", 1) or 1), 1),
    }, ensure_ascii=False))


def sync_save_chunk(chunk_path: Path, upload_file: UploadFile):
    """【线程池函数】快速将缓冲文件写入磁盘"""
    with open(chunk_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)


def sync_assemble_file(temp_dir: Path, total_chunks: int, final_dir: Path):
    """【线程池函数】高速重组大文件并计算哈希"""
    sha256_hash = hashlib.sha256()
    total_size = 0
    temp_assembled = temp_dir / "assembled"

    with open(temp_assembled, 'wb') as out_file:
        for i in range(total_chunks):
            chunk_path = temp_dir / f"chunk_{i:06d}"
            if not chunk_path.exists():
                raise FileNotFoundError(f"Missing chunk {i}")
            with open(chunk_path, 'rb') as in_file:
                # 增大读写缓冲至 10MB，极大提升 1GB 以上文件重组速度
                while data := in_file.read(1024 * 1024 * 10):
                    sha256_hash.update(data)
                    out_file.write(data)
                    total_size += len(data)

    file_hash = sha256_hash.hexdigest()
    final_path = final_dir / file_hash
    if not final_path.exists():
        shutil.move(str(temp_assembled), str(final_path))
    else:
        temp_assembled.unlink(missing_ok=True)
    return file_hash, total_size


# ==================== Pydantic 模型 ====================

class FileCheckRequest(BaseModel):
    file_name: str
    file_size: int
    course_id: int

class UploadInitRequest(BaseModel):
    file_name: str
    file_size: int
    course_id: int
    description: str = ""
    is_public: bool = True
    is_teacher_resource: bool = False

class UploadCompleteRequest(BaseModel):
    upload_id: str

class FileMetadataUpdateRequest(BaseModel):
    description: str = ""
    original_link: str = ""


class DescriptionUpdateRequest(BaseModel):
    description: str = ""


# ==================== 辅助函数 ====================

async def broadcast_file_update(course_id: int, message_text: str):
    """
    查找与某个课程ID关联的所有班级课堂 (聊天室)，并广播一条系统消息。
    """
    room_ids = []
    try:
        with get_db_connection() as conn:
            # 查找所有使用此 course_id 的 class_offering (即聊天室)
            rooms = conn.execute(
                "SELECT id FROM class_offerings WHERE course_id = ?",
                (course_id,)
            ).fetchall()
            room_ids = [room['id'] for room in rooms]

    except Exception as e:
        print(f"[ERROR] 广播文件更新时，查找聊天室失败: {e}")
        return

    if room_ids:
        # 格式化系统消息
        message_obj = {
            "type": "system",
            "message": message_text,
            "highlight": True
        }
        message_json = json.dumps(message_obj)

        # 广播到所有相关的聊天室
        for room_id in room_ids:
            try:
                await manager.broadcast(room_id, message_json)
            except Exception as e:
                print(f"[ERROR] 广播到聊天室 {room_id} 失败: {e}")


router = APIRouter()


# ==================== 分块上传协议 (新增) ====================

@router.post("/api/files/check")
async def check_file_exists(
    req: FileCheckRequest,
    user: dict = Depends(get_current_teacher)
):
    """预上传去重检查 — 全局统一文件库：按文件名+大小在所有课程中查找"""
    with get_db_connection() as conn:
        # 全局查找：不限 course_id
        existing = conn.execute(
            """SELECT id, file_name, file_size, file_hash, description, original_link, uploaded_at
               FROM course_files
               WHERE file_name = ? AND file_size = ?
               LIMIT 1""",
            (req.file_name, req.file_size)
        ).fetchone()

        if existing:
            # 检查当前课程是否已有此文件
            in_course = conn.execute(
                "SELECT id FROM course_files WHERE course_id = ? AND file_name = ? AND file_size = ?",
                (req.course_id, req.file_name, req.file_size)
            ).fetchone()

            if in_course:
                # 当前课程已有此文件
                return {
                    "exists": True,
                    "in_current_course": True,
                    "file": dict(in_course) | {
                        "file_name": existing["file_name"],
                        "file_size": existing["file_size"],
                        "description": existing["description"],
                        "original_link": existing["original_link"],
                        "uploaded_at": existing["uploaded_at"],
                    }
                }
            else:
                # 其他课程有此文件 — 自动关联到当前课程
                try:
                    conn.execute("""
                                 INSERT INTO course_files
                                 (course_id, file_name, file_hash, file_size, is_public, is_teacher_resource,
                                  description, original_link, uploaded_by_teacher_id)
                                 VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?)
                                 """, (req.course_id, existing["file_name"], existing["file_hash"],
                                       existing["file_size"], existing["description"],
                                       existing["original_link"], user['id']))
                    conn.commit()
                    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                except Exception as e:
                    raise HTTPException(500, f"关联文件失败: {e}")

                return {
                    "exists": True,
                    "in_current_course": False,
                    "linked": True,
                    "file": {
                        "id": new_id,
                        "file_name": existing["file_name"],
                        "file_size": existing["file_size"],
                        "description": existing["description"],
                        "original_link": existing["original_link"],
                        "uploaded_at": existing["uploaded_at"]
                    }
                }

        return {"exists": False}


@router.post("/api/files/upload/init")
async def init_chunked_upload(
    req: UploadInitRequest,
    user: dict = Depends(get_current_teacher)
):
    """初始化分块上传会话"""
    # 验证课程权限
    with get_db_connection() as conn:
        course = conn.execute(
            "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
            (req.course_id, user['id'])
        ).fetchone()
        if not course:
            raise HTTPException(403, "无权操作此课程")
    upload_id = str(uuid.uuid4())
    chunk_size = UPLOAD_CHUNK_SIZE_BYTES
    total_chunks = max(1, math.ceil(req.file_size / chunk_size))

    # 创建临时目录
    temp_dir = CHUNKED_UPLOADS_DIR / upload_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO chunked_uploads
            (upload_id, course_id, teacher_id, file_name, file_size,
             chunk_size, total_chunks, temp_dir, description,
             is_public, is_teacher_resource)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            upload_id, req.course_id, user['id'], req.file_name,
            req.file_size, chunk_size, total_chunks, str(temp_dir),
            req.description, req.is_public, req.is_teacher_resource
        ))
        conn.commit()

    return {
        "upload_id": upload_id,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks
    }


@router.post("/api/files/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    chunk: UploadFile = File(...),
    user: dict = Depends(get_current_teacher)
):
    """上传单个文件分块"""
    with get_db_connection() as conn:
        upload = conn.execute(
            "SELECT * FROM chunked_uploads WHERE upload_id = ? AND teacher_id = ? AND status = 'uploading'",
            (upload_id, user['id'])
        ).fetchone()
        if not upload:
            raise HTTPException(404, "上传会话不存在或已完成")

        if chunk_index < 0 or chunk_index >= upload['total_chunks']:
            raise HTTPException(400, f"无效的分块索引: {chunk_index}")

    # 保存分块到临时目录
    temp_dir = Path(upload['temp_dir'])
    chunk_path = temp_dir / f"chunk_{chunk_index:06d}"

    # 核心优化：丢给子线程处理磁盘写入，绝对不卡死聊天室和主线程
    await asyncio.to_thread(sync_save_chunk, chunk_path, chunk)

    # async with aiofiles.open(chunk_path, 'wb') as f:
    #     while data := await chunk.read(65536):  # 64KB 读取缓冲
    #         await f.write(data)

    # 更新已接收分块列表
    with get_db_connection() as conn:
        upload_row = conn.execute(
            "SELECT received_chunks FROM chunked_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()
        received = json.loads(upload_row['received_chunks'])
        if chunk_index not in received:
            received.append(chunk_index)
            received.sort()
        conn.execute(
            "UPDATE chunked_uploads SET received_chunks = ? WHERE upload_id = ?",
            (json.dumps(received), upload_id)
        )
        conn.commit()

    return {
        "status": "ok",
        "chunk_index": chunk_index,
        "received_count": len(received),
        "total_chunks": upload['total_chunks']
    }


@router.post("/api/files/upload/complete")
async def complete_chunked_upload(
    req: UploadCompleteRequest,
    user: dict = Depends(get_current_teacher)
):
    """完成分块上传：重组文件、计算哈希、存入全局存储、写入数据库"""
    with get_db_connection() as conn:
        upload = conn.execute(
            "SELECT * FROM chunked_uploads WHERE upload_id = ? AND teacher_id = ?",
            (req.upload_id, user['id'])
        ).fetchone()
        if not upload:
            raise HTTPException(404, "上传会话不存在")
        if upload['status'] != 'uploading':
            raise HTTPException(400, f"上传已处于状态: {upload['status']}")

        received = json.loads(upload['received_chunks'])
        if len(received) != upload['total_chunks']:
            raise HTTPException(400,
                f"分块未完整: 已接收 {len(received)}/{upload['total_chunks']}")

        # 标记为 completing 防止重复完成
        conn.execute(
            "UPDATE chunked_uploads SET status = 'completing' WHERE upload_id = ?",
            (req.upload_id,)
        )
        conn.commit()

    temp_dir = Path(upload['temp_dir'])
    total_chunks = upload['total_chunks']

    try:
        file_hash, total_size = await asyncio.to_thread(
            sync_assemble_file, temp_dir, total_chunks, GLOBAL_FILES_DIR
        )

        # 阶段 2：写入数据库
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO course_files
                (course_id, file_name, file_hash, file_size,
                 is_public, is_teacher_resource, description, original_link, uploaded_by_teacher_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                upload['course_id'], upload['file_name'], file_hash, total_size,
                upload['is_public'], upload['is_teacher_resource'],
                upload['description'], '', upload['teacher_id']
            ))
            conn.execute(
                "UPDATE chunked_uploads SET status = 'completed' WHERE upload_id = ?",
                (req.upload_id,)
            )
            conn.commit()

        # 阶段 3：广播通知
        try:
            await broadcast_file_update(
                upload['course_id'],
                f"老师上传了新文件: {upload['file_name']}。请刷新列表查看。"
            )
        except Exception as e:
            print(f"[ERROR] 广播上传消息失败: {e}")

        # 阶段 4：清理临时目录
        shutil.rmtree(str(temp_dir), ignore_errors=True)

        return {
            "status": "success",
            "message": f"文件 '{upload['file_name']}' 上传成功",
            "file_hash": file_hash,
            "file_size": total_size
        }

    except HTTPException:
        raise
    except Exception as e:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE chunked_uploads SET status = 'failed' WHERE upload_id = ?",
                (req.upload_id,)
            )
            conn.commit()
        raise HTTPException(500, f"上传完成失败: {e}")


@router.put("/api/files/{file_id}/metadata")
async def update_file_metadata(
    file_id: int,
    req: FileMetadataUpdateRequest,
    user: dict = Depends(get_current_teacher)
):
    """更新共享文件详情（仅教师）"""
    with get_db_connection() as conn:
        metadata = _update_course_file_metadata(
            conn,
            file_id=file_id,
            teacher_id=int(user["id"]),
            description=req.description,
            original_link=req.original_link,
        )
        conn.commit()
    return {
        "status": "success",
        "message": "文件详情已更新",
        "file": metadata,
    }


@router.put("/api/files/{file_id}/description")
async def update_file_description(
    file_id: int,
    req: DescriptionUpdateRequest,
    user: dict = Depends(get_current_teacher)
):
    """兼容旧版，仅更新文件简介。"""
    with get_db_connection() as conn:
        metadata = _update_course_file_metadata(
            conn,
            file_id=file_id,
            teacher_id=int(user["id"]),
            description=req.description,
            original_link=None,
        )
        conn.commit()
    return {
        "status": "success",
        "message": "文件简介已更新",
        "file": metadata,
    }


# ==================== 原有端点 (保留) ====================

def resolve_teacher_course_id(conn, course_or_offering_id: int, teacher_id: int) -> int:
    """兼容旧前端把 class_offering_id 当作 course_id 传入的情况。"""
    offering = conn.execute(
        "SELECT course_id FROM class_offerings WHERE id = ? AND teacher_id = ?",
        (course_or_offering_id, teacher_id)
    ).fetchone()
    if offering:
        return int(offering['course_id'])

    course = conn.execute(
        "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
        (course_or_offering_id, teacher_id)
    ).fetchone()
    if course:
        return int(course['id'])

    raise HTTPException(403, "无权操作当前课堂资源")


@router.post("/api/courses/{course_id}/files/upload")
async def upload_course_file(
        course_id: int,
        file: UploadFile = File(...),
        is_public: bool = Form(True),
        is_teacher_resource: bool = Form(False),
        user: dict = Depends(get_current_teacher)
):
    """上传课程资源文件(教师) — 兼容旧版小文件直传"""
    # 检查课程权限
    with get_db_connection() as conn:
        resolved_course_id = resolve_teacher_course_id(conn, course_id, user['id'])
        # Permission already validated by resolve_teacher_course_id.

        # 全局保存文件
        file_info = await save_file_globally(file)
        if not file_info:
            raise HTTPException(500, "文件保存失败")

        try:
            conn.execute("""
                         INSERT INTO course_files
                         (course_id, file_name, file_hash, file_size, is_public, is_teacher_resource, uploaded_by_teacher_id)
                         VALUES (?, ?, ?, ?, ?, ?, ?)
                         """, (
                             resolved_course_id,
                             file.filename,
                             file_info["hash"],
                             file_info["size"],
                             is_public,
                             is_teacher_resource,
                             user['id']
                         ))
            conn.commit()

        except Exception as e:
            raise HTTPException(500, f"数据库操作失败: {e}")

        # 广播消息
        try:
            await broadcast_file_update(resolved_course_id, f"老师上传了新文件: {file.filename}。请刷新列表查看。")
        except Exception as e:
            print(f"[ERROR] 广播上传消息失败: {e}")

    return {
        "status": "success",
        "message": f"文件 '{file.filename}' 上传成功"
    }


@router.delete("/api/courses/{course_id}/files/{file_id}")
async def delete_course_file(
        course_id: int,
        file_id: int,
        user: dict = Depends(get_current_teacher)
):
    """删除课程文件"""
    with get_db_connection() as conn:
        # 检查权限
        resolved_course_id = resolve_teacher_course_id(conn, course_id, user['id'])

        # 获取文件信息
        file_data = conn.execute(
            "SELECT file_name, file_hash FROM course_files WHERE id = ? AND course_id = ?",
            (file_id, resolved_course_id)
        ).fetchone()
        if not file_data:
            raise HTTPException(404, "文件不存在")

        # 检查是否有其他课程引用同一物理文件
        ref_count = conn.execute(
            "SELECT COUNT(*) FROM course_files WHERE file_hash = ?",
            (file_data['file_hash'],)
        ).fetchone()[0]

        # 删除数据库记录
        conn.execute("DELETE FROM course_files WHERE id = ?", (file_id,))
        conn.commit()

        # 仅当没有其他引用时才删除物理文件
        save_status = True
        if ref_count <= 1:
            save_status = await delete_file_safely(Path(GLOBAL_FILES_DIR) / file_data['file_hash'])

        # 广播消息
        try:
            await broadcast_file_update(resolved_course_id, f"老师删除了文件: {file_data['file_name']}")
        except Exception as e:
            print(f"[ERROR] 广播删除消息失败: {e}")

    return {"status": "success",
            "message": "文件删除成功，" + ("物理文件已删除。" if save_status else "但物理文件删除失败。")}


# 修改 classroom_app/routers/files.py 中的 download_course_file 函数
@router.get("/download/course_file/{file_id}")
async def download_course_file(
        file_id: int,
        user: Optional[dict] = Depends(get_current_user)
):
    """下载课程文件(支持断点续传、高并发控制、中文防乱码)"""
    if not user:
        raise HTTPException(401, "Not authenticated")

    with get_db_connection() as conn:
        file_info = conn.execute("""
                                 SELECT cf.*, c.created_by_teacher_id
                                 FROM course_files cf
                                          JOIN courses c ON cf.course_id = c.id
                                 WHERE cf.id = ?
                                 """, (file_id,)).fetchone()

    if not file_info:
        raise HTTPException(404, "文件不存在")

    with get_db_connection() as conn:
        _ensure_course_file_access(conn, file_info, user)

    ensure_download_allowed(file_info["file_size"], resource_label="共享文件")

    file_path = Path(GLOBAL_FILES_DIR) / file_info['file_hash']
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")

    async def streamed_file():
        # 【Windows 高并发守护】使用信号量代替 Lock，控制极限并发但不引起单线程阻塞排队
        async with windows_io_semaphore:
            async for chunk in stream_file(file_path):
                yield chunk

    # 【Windows 中文名防乱码】使用 RFC 5987 URL编码标准
    safe_filename = quote(file_info['file_name'])

    return StreamingResponse(
        streamed_file(),
        media_type='application/octet-stream',
        headers={
            # 兼容旧版与现代浏览器的中文字符规范声明
            'Content-Disposition': f"attachment; filename*=utf-8''{safe_filename}",
            'Content-Length': str(file_info['file_size'])
        }
    )


# --- 用于异步刷新文件列表的 API ---
@router.get("/api/courses/{class_offering_id}/files")
async def get_classroom_files(
        class_offering_id: int,
        user: dict = Depends(get_current_user)
):
    """获取指定课堂(课程)的文件列表。"""
    """获取指定课堂(课程)的文件列表"""
    with get_db_connection() as conn:
        # 1. 根据课堂ID找到课程ID
        _ensure_classroom_access_for_user(conn, class_offering_id, user)

        offering = conn.execute(
            "SELECT course_id FROM class_offerings WHERE id = ?",
            (class_offering_id,)
        ).fetchone()

        if not offering:
            raise HTTPException(404, "Classroom not found")
        course_id = offering['course_id']

        # 2. 根据用户角色获取文件列表（含新字段）
        query = """SELECT id, file_name, file_size, description, original_link, uploaded_at, uploaded_by_teacher_id
                   FROM course_files WHERE course_id = ?"""
        params = [course_id]

        # 学生看不到教师资源
        if user['role'] != 'teacher':
            query += " AND is_public = 1 AND is_teacher_resource = 0"

        query += " ORDER BY uploaded_at DESC"
        files = conn.execute(query, params).fetchall()

        # 3. 告知前端当前是否是教师 (用于显示操作按钮)
        is_teacher = (user['role'] == 'teacher')

    payload_files = []

    # 将 sqlite3.Row 转换为字典列表
    payload_files = []
    for row in files:
        item = dict(row)
        item["download_url"] = f"/download/course_file/{item['id']}"
        payload_files.append(apply_download_policy(item, resource_label="共享文件"))

    return {"files": payload_files, "is_teacher": is_teacher}


@router.post("/api/classrooms/{class_offering_id}/discussion-attachments")
async def upload_discussion_attachments(
        class_offering_id: int,
        files: list[UploadFile] = File(...),
        user: dict = Depends(get_current_user)
):
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一张图片")
    if len(files) > MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(
            status_code=400,
            detail=f"单条讨论消息最多只能发送 {MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE} 张图片",
        )

    with get_db_connection() as conn:
        _ensure_classroom_access_for_user(conn, class_offering_id, user)
        attachment_payloads = []
        for file in files:
            try:
                attachment_payloads.append(
                    await create_discussion_attachment(conn, class_offering_id, user, file)
                )
            finally:
                await file.close()
        conn.commit()

    return {
        "attachments": attachment_payloads,
        "limits": {
            "max_attachment_count": MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE,
            "max_upload_bytes": DISCUSSION_ATTACHMENT_MAX_BYTES,
        },
    }


@router.get("/api/classrooms/{class_offering_id}/discussion-mood")
async def get_discussion_mood(
        class_offering_id: int,
        user: dict = Depends(get_current_user)
):
    with get_db_connection() as conn:
        _ensure_classroom_access_for_user(conn, class_offering_id, user)
        payload = get_discussion_mood_payload(conn, class_offering_id)

    await maybe_schedule_discussion_mood_refresh(
        class_offering_id,
        reason="poll",
    )
    return payload


@router.get("/api/classrooms/{class_offering_id}/discussion-attachments/{attachment_id}")
async def download_discussion_attachment(
        class_offering_id: int,
        attachment_id: int,
        user: dict = Depends(get_current_user)
):
    with get_db_connection() as conn:
        _ensure_classroom_access_for_user(conn, class_offering_id, user)
        attachment_row = load_discussion_attachment_row(conn, class_offering_id, attachment_id)

    if attachment_row is None:
        raise HTTPException(status_code=404, detail="讨论区图片不存在")

    file_path = Path(GLOBAL_FILES_DIR) / str(attachment_row["file_hash"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="讨论区图片不存在")

    async def streamed_file():
        async with windows_io_semaphore:
            async for chunk in stream_file(file_path):
                yield chunk

    safe_filename = quote(str(attachment_row["original_filename"] or "image"))
    return StreamingResponse(
        streamed_file(),
        media_type=str(attachment_row["mime_type"] or "application/octet-stream"),
        headers={
            "Content-Disposition": f"inline; filename*=utf-8''{safe_filename}",
            "Content-Length": str(int(attachment_row["file_size"] or 0)),
        },
    )


# (原有的 download_submission_file V4.0)

@router.get("/api/submission-files/{file_id}/preview", response_class=JSONResponse)
async def get_submission_file_preview(file_id: int, user: Optional[dict] = Depends(get_current_user)):
    with get_db_connection() as conn:
        file_info = ensure_submission_file_access(conn, file_id, user)

    preview_payload = await build_submission_file_preview_payload(file_info)
    return {"status": "success", "file": preview_payload}


@router.get("/submission-files/raw/{file_id}", response_class=FileResponse)
async def get_submission_file_raw(file_id: int, user: Optional[dict] = Depends(get_current_user)):
    with get_db_connection() as conn:
        file_info = ensure_submission_file_access(conn, file_id, user)

    preview_type = str(serialize_submission_file_row(file_info).get("preview_type") or "").lower()
    if preview_type != "image":
        raise HTTPException(400, "Only image files support raw preview")

    file_path = Path(str(file_info["stored_path"]))
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")

    return FileResponse(file_path, media_type=file_info.get("mime_type") or "application/octet-stream")


@router.get("/submissions/download/{file_id}", response_class=FileResponse)
async def download_submission_file(file_id: int, user: Optional[dict] = Depends(get_current_user)):
    """V4.0: 下载学生提交的文件"""
    if not user: raise HTTPException(401, "Not authenticated")

    with get_db_connection() as conn:
        file_info = ensure_submission_file_access(conn, file_id, user)


    # 安全检查：只允许教师 或 文件所有者学生 下载

    file_path = Path(str(file_info['stored_path']))
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")

    return FileResponse(
        file_path,
        media_type=file_info.get('mime_type') or 'application/octet-stream',
        filename=file_info['original_filename'],
    )


@router.websocket("/ws/{class_offering_id}")
async def websocket_endpoint(websocket: WebSocket, class_offering_id: int):
    """V4.0: 支持多房间的 WebSocket"""
    # 获取客户端IP
    client_ip = None
    try:
        # 首先尝试从 headers 获取真实IP（反向代理情况）
        forwarded_for = websocket.headers.get("x-forwarded-for")
        real_ip = websocket.headers.get("x-real-ip")
        if forwarded_for:
            client_ip = forwarded_for.split(',')[0].strip()
        elif real_ip:
            client_ip = real_ip.strip()
        else:
            # 使用连接IP
            client_ip = websocket.client.host
        client_ip = normalize_ip(client_ip) or client_ip
    except Exception as e:
        print(f"[WS ERROR] 获取客户端IP失败: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token = websocket.cookies.get("access_token")

    # 使用新的verify_token函数，传入client_ip
    user = await asyncio.to_thread(verify_token, token, client_ip)
    # 兼容代理环境中 WebSocket IP 与 HTTP 登录 IP 不一致的情况
    if user is None and token is not None:
        user = await asyncio.to_thread(verify_token, token, None)
        if user is not None:
            print(f"[WS WARN] 回退到会话级验证通过 - IP: {client_ip}")

    if user is None:
        print(f"[WS ERROR] Token验证失败 - IP: {client_ip}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        user_pk = int(user.get("id"))
    except (TypeError, ValueError):
        print("[WS ERROR] 非法用户ID")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        await asyncio.to_thread(_ensure_websocket_room_access_sync, class_offering_id, user, user_pk)
    except HTTPException as exc:
        print(f"[WS ERROR] WebSocket 房间鉴权失败 - user_id: {user_pk}, classroom: {class_offering_id}, detail: {exc.detail}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    client_id = f"{user['role']}_{user_pk}"
    ws_user = dict(user)
    ws_user['id'] = client_id

    connection_id = await manager.connect(websocket, ws_user)
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = raw_data.strip()
            if not data:
                continue

            command = None
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict) and parsed.get("action") in {"switch_alias", "load_history", "send_message"}:
                    command = parsed
            except json.JSONDecodeError:
                command = None

            if command:
                if command["action"] == "switch_alias":
                    switch_result = await manager.switch_temporary_name(class_offering_id, client_id)
                    alias_trigger = record_alias_switch_activity(
                        class_offering_id=class_offering_id,
                        user_pk=user_pk,
                        user_role=str(ws_user["role"]),
                        display_name=manager.get_display_name(class_offering_id, client_id, ws_user['name']),
                        success=bool(switch_result.get("success")),
                        previous_name=switch_result.get("previous_name"),
                        new_name=switch_result.get("new_name"),
                        reason=switch_result.get("reason"),
                    )

                    await websocket.send_text(json.dumps({
                        "type": "alias_switch_result",
                        "success": bool(switch_result.get("success")),
                        "message": switch_result.get("message"),
                        "reason": switch_result.get("reason"),
                        "alias_state": switch_result.get("alias_state"),
                    }, ensure_ascii=False))

                    if not switch_result.get("success"):
                        continue

                    previous_name = switch_result.get("previous_name")
                    new_name = switch_result.get("new_name")
                    await manager.broadcast(
                        class_offering_id,
                        json.dumps({
                            "type": "system",
                            "message": f"{previous_name} 已更换代号为 {new_name}。",
                        }, ensure_ascii=False),
                    )
                    await manager.broadcast_user_list(class_offering_id)
                    await manager.broadcast_alias_states(class_offering_id)
                    continue

                if command["action"] == "load_history":
                    before_id = command.get("before_id")
                    if before_id is not None:
                        try:
                            before_id = int(before_id)
                        except (TypeError, ValueError):
                            before_id = None

                    await websocket.send_text(
                        json.dumps(await load_older_history_payload(class_offering_id, before_id), ensure_ascii=False)
                    )
                    continue

                try:
                    await _process_discussion_chat_message(
                        class_offering_id=class_offering_id,
                        user=user,
                        ws_user=ws_user,
                        user_pk=user_pk,
                        client_id=client_id,
                        message_text=str(command.get("text") or ""),
                        requested_custom_ids=command.get("custom_emoji_ids") or [],
                        requested_unicode_emojis=command.get("used_unicode_emojis") or [],
                        requested_attachment_ids=command.get("attachment_ids") or [],
                        quote_message_id=command.get("quote_message_id"),
                    )
                except RateLimitExceededError as exc:
                    await _send_discussion_rate_limit_message(websocket, exc)
                continue

            try:
                await _process_discussion_chat_message(
                    class_offering_id=class_offering_id,
                    user=user,
                    ws_user=ws_user,
                    user_pk=user_pk,
                    client_id=client_id,
                    message_text=data,
                )
            except RateLimitExceededError as exc:
                await _send_discussion_rate_limit_message(websocket, exc)
    except WebSocketDisconnect:
        await manager.disconnect(connection_id)
    except Exception as e:
        print(f"[WS ERROR] {e}")
        await manager.disconnect(connection_id)
