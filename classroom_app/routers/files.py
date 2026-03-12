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
from ..dependencies import verify_token, get_current_user, get_current_teacher, get_client_ip
# 导入聊天管理器
from ..services.chat_handler import manager, save_chat_message

from typing import Optional
from pathlib import Path

from ..database import get_db_connection
from ..services.file_handler import delete_file_safely
from ..services.file_service import save_file_globally, get_file_lock, stream_file

# --- 新增：专门针对 Windows 系统的并发保护限流器 ---
# 允许同时最多 80 个物理读取流，既能打满内网千兆带宽，又能完美避开 Windows 文件句柄上限崩溃
windows_io_semaphore = asyncio.Semaphore(80)


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
        course = conn.execute(
            "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
            (course_id, user['id'])
        ).fetchone()
        if not course:
            raise HTTPException(403, "无权操作此课程")

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
                             course_id,
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
            await broadcast_file_update(course_id, f"老师上传了新文件: {file.filename}。请刷新列表查看。")
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
        course = conn.execute(
            "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
            (course_id, user['id'])
        ).fetchone()
        if not course:
            raise HTTPException(403, "无权操作此课程")

        # 获取文件信息
        file_data = conn.execute(
            "SELECT file_name, file_hash FROM course_files WHERE id = ? AND course_id = ?",
            (file_id, course_id)
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
            await broadcast_file_update(course_id, f"老师删除了文件: {file_data['file_name']}")
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
        if forwarded_for:
            client_ip = forwarded_for.split(',')[0].strip()
        else:
            # 使用连接IP
            client_ip = websocket.client.host
    except Exception as e:
        print(f"[WS ERROR] 获取客户端IP失败: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token = websocket.cookies.get("access_token")

    # 使用新的verify_token函数，传入client_ip
    user = verify_token(token, client_ip)
    if user is None:
        print(f"[WS ERROR] Token验证失败 - IP: {client_ip}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # TODO: 验证用户是否有权进入这个 class_offering_id 房间

    client_id = f"{user['role']}_{user['id']}"
    user['id'] = client_id  # 使用唯一的 client_id

    await manager.connect(websocket, user)
    try:
        while True:
            data = await websocket.receive_text()
            message_obj = {
                "type": "chat",
                "sender": user['name'],
                "role": user['role'],
                "message": data,
                "timestamp": datetime.now().strftime("%H:%M")
            }
            # 保存消息
            db_message = {
                "class_offering_id": class_offering_id,
                "user_id": user['id'],
                "user_name": user['name'],
                "user_role": user['role'],
                "message": data,
                "timestamp": datetime.now().isoformat()
            }
            await save_chat_message(class_offering_id, db_message)
            # 广播消息
            await manager.broadcast(class_offering_id, json.dumps(message_obj))
    except WebSocketDisconnect:
        await manager.disconnect(websocket, client_id)
    except Exception as e:
        print(f"[WS ERROR] {e}")
        await manager.disconnect(websocket, client_id)
