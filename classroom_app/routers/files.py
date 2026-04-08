import asyncio
import json
import math
import uuid
import hashlib
import shutil
import aiofiles
from datetime import datetime
from urllib.parse import quote

# 导入聊天管理器和 json
from fastapi import WebSocket, status, WebSocketDisconnect, UploadFile, File, Form, APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..config import GLOBAL_FILES_DIR, UPLOAD_CHUNK_SIZE_BYTES, CHUNKED_UPLOADS_DIR
from ..dependencies import verify_token, get_current_user, get_current_teacher, normalize_ip
# 导入聊天管理器
from ..services.chat_handler import manager, save_chat_message, get_older_history_payload

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
from ..services.emoji_service import increment_emoji_usage, resolve_custom_emoji_payloads
from ..services.file_handler import delete_file_safely
from ..services.file_service import save_file_globally, get_file_lock, stream_file
from ..services.message_center_service import create_discussion_mention_notifications
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
) -> None:
    try:
        reply_text = await generate_discussion_ai_reply(
            class_offering_id=class_offering_id,
            user_pk=user_pk,
            user_role=user_role,
            caller_display_name=caller_display_name,
            original_text=original_text,
            current_message_id=current_message_id,
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
) -> None:
    normalized_text = str(message_text or "").strip()
    requested_custom_ids = requested_custom_ids or []
    requested_unicode_emojis = requested_unicode_emojis or []

    with get_db_connection() as conn:
        custom_emoji_payloads = resolve_custom_emoji_payloads(
            conn,
            class_offering_id,
            requested_custom_ids,
            user,
        )

    if not normalized_text and not custom_emoji_payloads:
        return

    with get_db_connection() as conn:
        _enforce_discussion_message_rate_limit(
            conn,
            user_pk=user_pk,
            user_role=str(ws_user["role"]),
        )

    now = datetime.now()
    display_time = now.strftime("%H:%M")
    display_name = manager.get_display_name(class_offering_id, client_id, ws_user['name'])
    stored_message = await save_chat_message(class_offering_id, {
        "type": "chat",
        "sender": display_name,
        "role": ws_user['role'],
        "message": normalized_text,
        "message_type": "rich" if custom_emoji_payloads else "text",
        "custom_emojis": custom_emoji_payloads,
        "timestamp": display_time,
        "class_offering_id": class_offering_id,
        "user_id": user_pk,
        "logged_at": now.isoformat(),
    })

    with get_db_connection() as conn:
        increment_emoji_usage(
            conn,
            class_offering_id,
            user,
            requested_unicode_emojis,
            [item["id"] for item in custom_emoji_payloads],
            used_at=now.isoformat(),
        )
        try:
            create_discussion_mention_notifications(
                conn,
                class_offering_id=class_offering_id,
                sender_user=user,
                sender_display_name=display_name,
                message_text=normalized_text,
                message_id=int(stored_message["id"]),
            )
        except Exception as exc:
            print(f"[MESSAGE_CENTER] discussion mention notify failed: {exc}")
        conn.commit()

    profile_trigger = record_message_activity(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=str(ws_user["role"]),
        display_name=display_name,
        message_text=normalized_text,
        unicode_emojis=[str(item) for item in requested_unicode_emojis if str(item).strip()],
        custom_emoji_labels=[str(item.get("name") or "自定义表情") for item in custom_emoji_payloads],
        mentioned_assistant=contains_discussion_ai_mention(normalized_text),
    )

    await manager.broadcast(class_offering_id, json.dumps(stored_message, ensure_ascii=False))

    if contains_discussion_ai_mention(normalized_text):
        asyncio.create_task(
            _handle_discussion_ai_mention(
                class_offering_id=class_offering_id,
                user_pk=user_pk,
                user_role=str(ws_user["role"]),
                caller_display_name=display_name,
                original_text=normalized_text,
                current_message_id=int(stored_message["id"]),
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

class DescriptionUpdateRequest(BaseModel):
    description: str


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
            """SELECT id, file_name, file_size, file_hash, description, uploaded_at
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
                    "file": dict(in_course) | {"file_name": existing["file_name"], "file_size": existing["file_size"],
                                                "description": existing["description"], "uploaded_at": existing["uploaded_at"]}
                }
            else:
                # 其他课程有此文件 — 自动关联到当前课程
                try:
                    conn.execute("""
                                 INSERT INTO course_files
                                 (course_id, file_name, file_hash, file_size, is_public, is_teacher_resource,
                                  description, uploaded_by_teacher_id)
                                 VALUES (?, ?, ?, ?, 1, 0, ?, ?)
                                 """, (req.course_id, existing["file_name"], existing["file_hash"],
                                       existing["file_size"], existing["description"],
                                       user['id']))  # 使用 existing["description"]
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
                        "description": "",
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

    # 阶段 1：重组文件 + 同时计算 SHA256（内存高效，流式处理）
    global_dir = Path(GLOBAL_FILES_DIR)
    global_dir.mkdir(parents=True, exist_ok=True)
    sha256_hash = hashlib.sha256()
    temp_assembled = temp_dir / "assembled"
    total_size = 0

    try:
        async with aiofiles.open(temp_assembled, 'wb') as out_file:
            for i in range(total_chunks):
                chunk_path = temp_dir / f"chunk_{i:06d}"
                if not chunk_path.exists():
                    raise HTTPException(500, f"分块文件丢失: chunk_{i:06d}")
                async with aiofiles.open(chunk_path, 'rb') as chunk_file:
                    # 使用较大的缓冲区提高合并 1Gb 以上大文件的速度
                    while data := await chunk_file.read(1024 * 1024 * 5):
                        sha256_hash.update(data)
                        await out_file.write(data)
                        total_size += len(data)

        file_hash = sha256_hash.hexdigest()
        final_path = global_dir / file_hash

        # 内容哈希去重：如果全局存储已有此内容，跳过复制
        if not final_path.exists():
            shutil.move(str(temp_assembled), str(final_path))
        else:
            temp_assembled.unlink(missing_ok=True)

        # 核心优化：异步调用线程池，后端瞬间丝滑
        try:
            file_hash, total_size = await asyncio.to_thread(
                sync_assemble_file, temp_dir, total_chunks, GLOBAL_FILES_DIR
            )
        except Exception as e:
            # 失败处理...
            raise HTTPException(500, f"上传完成失败: {e}")

        file_hash = sha256_hash.hexdigest()
        final_path = GLOBAL_FILES_DIR / file_hash

        # 内容哈希去重：如果全局存储已有此内容，跳过复制
        if not final_path.exists():
            shutil.move(str(temp_assembled), str(final_path))
        else:
            temp_assembled.unlink(missing_ok=True)

        # 阶段 2：写入数据库
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO course_files
                (course_id, file_name, file_hash, file_size,
                 is_public, is_teacher_resource, description, uploaded_by_teacher_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                upload['course_id'], upload['file_name'], file_hash, total_size,
                upload['is_public'], upload['is_teacher_resource'],
                upload['description'], upload['teacher_id']
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


@router.put("/api/files/{file_id}/description")
async def update_file_description(
    file_id: int,
    req: DescriptionUpdateRequest,
    user: dict = Depends(get_current_teacher)
):
    """更新文件简介（仅教师）"""
    with get_db_connection() as conn:
        file_row = conn.execute("""
            SELECT cf.id, cf.course_id FROM course_files cf
            JOIN courses c ON cf.course_id = c.id
            WHERE cf.id = ? AND c.created_by_teacher_id = ?
        """, (file_id, user['id'])).fetchone()
        if not file_row:
            raise HTTPException(404, "文件不存在或无操作权限")

        conn.execute(
            "UPDATE course_files SET description = ? WHERE id = ?",
            (req.description, file_id)
        )
        conn.commit()
    return {"status": "success", "message": "文件简介已更新"}


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

    # 权限检查
    if file_info['is_teacher_resource'] and user['role'] != 'teacher':
        raise HTTPException(403, "无权访问教师资源")

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
    """获取指定课堂(课程)的文件列表"""
    with get_db_connection() as conn:
        # 1. 根据课堂ID找到课程ID
        offering = conn.execute(
            "SELECT course_id FROM class_offerings WHERE id = ?",
            (class_offering_id,)
        ).fetchone()

        if not offering:
            raise HTTPException(404, "Classroom not found")
        course_id = offering['course_id']

        # 2. 根据用户角色获取文件列表（含新字段）
        query = """SELECT id, file_name, file_size, description, uploaded_at, uploaded_by_teacher_id
                   FROM course_files WHERE course_id = ?"""
        params = [course_id]

        # 学生看不到教师资源
        if user['role'] != 'teacher':
            query += " AND is_teacher_resource = 0"

        query += " ORDER BY uploaded_at DESC"
        files = conn.execute(query, params).fetchall()

        # 3. 告知前端当前是否是教师 (用于显示操作按钮)
        is_teacher = (user['role'] == 'teacher')

    # 将 sqlite3.Row 转换为字典列表
    return {"files": [dict(f) for f in files], "is_teacher": is_teacher}


# (原有的 download_submission_file V4.0)

@router.get("/submissions/download/{file_id}", response_class=FileResponse)
async def download_submission_file(file_id: int, user: Optional[dict] = Depends(get_current_user)):
    """V4.0: 下载学生提交的文件"""
    if not user: raise HTTPException(401, "Not authenticated")

    with get_db_connection() as conn:
        file_info = conn.execute(
            """SELECT sf.*, s.student_pk_id
               FROM submission_files sf
                        JOIN submissions s ON sf.submission_id = s.id
               WHERE sf.id = ?""", (file_id,)
        ).fetchone()

    if not file_info: raise HTTPException(404, "File not found")

    # 安全检查：只允许教师 或 文件所有者学生 下载
    if user['role'] != 'teacher' and file_info['student_pk_id'] != user['id']:
        raise HTTPException(403, "Permission denied")

    file_path = Path(file_info['stored_path'])
    if not file_path.exists(): raise HTTPException(404, "File not found on disk")

    return FileResponse(file_path, filename=file_info['original_filename'])


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
    user = verify_token(token, client_ip)
    # 兼容代理环境中 WebSocket IP 与 HTTP 登录 IP 不一致的情况
    if user is None and token is not None:
        user = verify_token(token, None)
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

    # 验证用户是否有权进入当前课堂房间
    with get_db_connection() as conn:
        offering = conn.execute(
            "SELECT id, class_id, teacher_id FROM class_offerings WHERE id = ?",
            (class_offering_id,)
        ).fetchone()

        if not offering:
            print(f"[WS ERROR] 课堂不存在 - class_offering_id: {class_offering_id}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if user.get("role") == "teacher":
            if int(offering["teacher_id"]) != user_pk:
                print(f"[WS ERROR] 教师越权访问课堂 - teacher_id: {user_pk}, classroom: {class_offering_id}")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        elif user.get("role") == "student":
            student_class = conn.execute(
                "SELECT class_id FROM students WHERE id = ?",
                (user_pk,)
            ).fetchone()
            if not student_class or int(student_class["class_id"]) != int(offering["class_id"]):
                print(f"[WS ERROR] 学生越权访问课堂 - student_id: {user_pk}, classroom: {class_offering_id}")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        else:
            print(f"[WS ERROR] 非法用户角色: {user.get('role')}")
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
                        json.dumps(get_older_history_payload(class_offering_id, before_id), ensure_ascii=False)
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
