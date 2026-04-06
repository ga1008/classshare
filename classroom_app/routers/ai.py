import base64
import json
import uuid
import asyncio
import time
import traceback
from pathlib import Path
from typing import List, Literal, Dict, Any, Optional
from enum import Enum
from datetime import datetime

from fastapi.responses import StreamingResponse

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from ..config import MAX_UPLOAD_SIZE_MB, MAX_UPLOAD_SIZE_BYTES
from ..core import ai_client
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user

router = APIRouter(prefix="/api")

# ============================
# AI试卷生成任务管理
# ============================

class ExamGenTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

# 内存存储任务状态 (简单实现，生产环境应使用数据库)
_exam_gen_tasks: Dict[str, Dict[str, Any]] = {}
_exam_gen_tasks_lock = asyncio.Lock()


@router.post("/ai/generate_assignment", response_class=JSONResponse)
async def ai_generate_assignment(request: Request, user: dict = Depends(get_current_teacher)):
    """向 AI 助手服务请求生成作业"""
    try:
        data = await request.json()
        response = await ai_client.post("/api/ai/generate-assignment", json={"prompt": data.get('prompt'),
                                                                             "model_type": data.get('model_type',
                                                                                                    'standard')})
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"AI 服务错误: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 请求失败: {e}")


@router.post("/submissions/{submission_id}/regrade", response_class=JSONResponse)
async def ai_regrade_submission(submission_id: int, user: dict = Depends(get_current_teacher)):
    """向 AI 助手服务提交一个异步批改任务 (支持文件 + JSON 答案)"""
    with get_db_connection() as conn:
        submission = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if not submission: raise HTTPException(status_code=404, detail="Submission not found")
        if submission['status'] == 'grading': return {"status": "already_grading"}
        assignment = conn.execute("SELECT requirements_md, rubric_md FROM assignments WHERE id = ?",
                                  (submission['assignment_id'],)).fetchone()
        files_cursor = conn.execute("SELECT stored_path FROM submission_files WHERE submission_id = ?",
                                    (submission_id,))
        file_paths = [row['stored_path'] for row in files_cursor]

    # 检查是否有可批改的内容（文件或JSON答案均可）
    has_files = bool(file_paths)
    has_answers = bool(submission['answers_json'])
    if not has_files and not has_answers:
        raise HTTPException(status_code=400, detail="该提交没有可批改的内容（无文件也无答案）。")

    job_data = {
        "submission_id": submission_id,
        "rubric_md": assignment['rubric_md'],
        "requirements_md": assignment['requirements_md'] or '',
        "file_paths": [str(Path(p).resolve()) for p in file_paths] if has_files else [],
        "answers_json": submission['answers_json'] if has_answers else None,
    }
    try:
        response = await ai_client.post("/api/ai/submit-grading-job", json=job_data)
        response.raise_for_status()
        with get_db_connection() as conn:
            conn.execute("UPDATE submissions SET status = 'grading' WHERE id = ?", (submission_id,))
            conn.commit()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="AI 助手服务未运行，请先启动 ai_assistant.py。")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 任务提交失败: {e}")


@router.post("/internal/grading-complete", response_class=JSONResponse, include_in_schema=False)
async def handle_ai_grading_callback(request: Request):
    """(内部接口) 接收来自 AI 助手的批改结果"""
    try:
        data = await request.json()
        submission_id = data['submission_id']
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE submissions SET status = ?, score = ?, feedback_md = ? WHERE id = ?",
                (data['status'], data.get('score'), data.get('feedback_md'), submission_id)
            )
            conn.commit()
        print(f"[CALLBACK] 成功接收并更新 AI 批改结果 (Submission ID: {submission_id})")
        # TODO: 通过 WebSocket 向教师推送更新
        return {"status": "received"}
    except Exception as e:
        print(f"[ERROR] AI 回调处理失败: {e}")
        raise HTTPException(status_code=500, detail="Callback processing failed")


# ============================
# V4.2: 课堂 AI 聊天 API
# ============================

def _get_user_pk_role(user: dict) -> (int, str):
    """辅助函数：从 token 中获取用户 PK 和角色"""
    user_pk = user.get('id')
    user_role = user.get('role')
    if not user_pk or not user_role:
        raise HTTPException(status_code=401, detail="无效的用户凭证")
    return user_pk, user_role


async def _upload_file_to_base64(file: UploadFile) -> str:
    """辅助函数：将 UploadFile 转换为 base64 data URL"""
    if file.content_type not in ["image/jpeg", "image/png", "image/gif", "image/webp"]:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}。仅支持图片。")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:  # 借用 config 中的设置
        raise HTTPException(status_code=413, detail=f"文件大小不能超过 {MAX_UPLOAD_SIZE_MB}MB")

    base64_data = base64.b64encode(contents).decode('utf-8')
    return f"data:{file.content_type};base64,{base64_data}"


def format_system_prompt(user_id: int, user_role, class_offering_id: int=None) -> str:
    if user_role == 'teacher':
        return format_system_prompt_teacher(user_id, class_offering_id)
    else:
        return format_system_prompt_student(user_id, class_offering_id)


def format_system_prompt_teacher(user_id: int, class_offering_id: int) -> str:
    """格式化教师的 System Prompt 信息"""
    prompt_parts = ["你是一个课堂AI助手。正在向你提问的教师信息如下："]

    # 定义一个用于生成基础画像的变量
    teacher_description = ""

    with get_db_connection() as conn:
        # 1. 获取教师基本信息
        teacher_info = conn.execute(
            "SELECT id, name, email, description FROM teachers WHERE id = ?",
            (user_id,)
        ).fetchone()

        # 2. 获取当前课堂的详细信息
        current_offering_info = conn.execute(
            """
            SELECT c.name as course_name, cl.name as class_name
            FROM class_offerings co
                     JOIN courses c ON co.course_id = c.id
                     JOIN classes cl ON co.class_id = cl.id
            WHERE co.id = ?
              AND co.teacher_id = ?
            """,
            (class_offering_id, user_id)
        ).fetchone()

        if teacher_info:
            prompt_parts.append(f"- 身份: 教师")
            prompt_parts.append(f"- 姓名: {teacher_info['name']}")
            prompt_parts.append(f"- 邮箱: {teacher_info['email']}")

            # --- [核心修改] ---
            # 优先使用数据库中的画像
            if teacher_info['description']:
                teacher_description = teacher_info['description']
            else:
                # 如果为空，动态生成一个“基础画像”
                teacher_description = f"该用户是教师 {teacher_info['name']}。目前暂无个性化画像，请在交流中逐步了解。"

                # [智能方案] 立即将这个基础画像写回数据库，解决“冷启动”
                try:
                    conn.execute("UPDATE teachers SET description = ? WHERE id = ?", (teacher_description, user_id))
                    conn.commit()
                    print(f"[PROFILE_INIT] 已为教师 {user_id} 初始化基础画像。")
                except Exception as e:
                    print(f"[ERROR] 初始化教师 {user_id} 画像失败: {e}")

            prompt_parts.append(f"- 个人描述: {teacher_description}")
            # --- [修改结束] ---

        prompt_parts.append(f"\n--- 教学与课堂信息 ---")
        if current_offering_info:
            prompt_parts.append(
                f"- 当前所在课堂: 《{current_offering_info['course_name']}》 - {current_offering_info['class_name']} (ID: {class_offering_id})")
        else:
            # 这种情况理论上不应该发生，除非教师在访问不属于自己的课堂
            prompt_parts.append(f"- 当前所在课堂ID: {class_offering_id} (注意: 该课堂可能不属于此教师)")

        # 3. 统计教师关联的其他信息
        course_count = \
        conn.execute("SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?", (user_id,)).fetchone()[0]
        offering_count = \
        conn.execute("SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?", (user_id,)).fetchone()[0]

        prompt_parts.append(f"- 该教师共创建了 {course_count} 门课程模板")
        prompt_parts.append(f"- 该教师共开设了 {offering_count} 个课堂")

        # 4. (可选) 列出该教师教授的所有课程
        courses_taught = conn.execute(
            """
            SELECT DISTINCT c.name
            FROM courses c
                     JOIN class_offerings co ON c.id = co.course_id
            WHERE co.teacher_id = ? LIMIT 5
            """,
            (user_id,)
        ).fetchall()

        if courses_taught:
            course_names = ", ".join([row['name'] for row in courses_taught])
            prompt_parts.append(f"- 教授的课程(示例): {course_names}")

    prompt_parts.append("\n请根据以上信息，辅助教师进行教学管理、课程答疑或内容生成。")
    return "\n".join(prompt_parts)


def format_system_prompt_student(user_id: int, class_offering_id: int) -> str:
    """格式化学生的 System Prompt 信息"""
    prompt_parts = ["你是一个课堂AI助手。正在向你提问的学生信息如下："]

    # 定义一个用于生成基础画像的变量
    student_description = ""

    with get_db_connection() as conn:
        # 1. 获取学生和班级信息
        student_info = conn.execute(
            """
            SELECT s.id,
                   s.name,
                   s.student_id_number,
                   s.gender,
                   s.email,
                   s.phone,
                   s.description,
                   c.name as class_name,
                   s.class_id
            FROM students s
                     JOIN classes c ON s.class_id = c.id
            WHERE s.id = ?
            """,
            (user_id,)
        ).fetchone()

        # 2. 获取课堂、课程和教师信息
        offering_info = conn.execute(
            """
            SELECT c.name as course_name, t.name as teacher_name
            FROM class_offerings co
                     JOIN courses c ON co.course_id = c.id
                     JOIN teachers t ON co.teacher_id = t.id
            WHERE co.id = ?
            """,
            (class_offering_id,)
        ).fetchone()

        if student_info:
            prompt_parts.append(f"- 身份: 学生")
            prompt_parts.append(f"- 姓名: {student_info['name']}")
            prompt_parts.append(f"- 学号: {student_info['student_id_number']}")
            if student_info['gender']:
                prompt_parts.append(f"- 性别: {student_info['gender']}")
            if student_info['email']:
                prompt_parts.append(f"- 邮箱: {student_info['email']}")
            if student_info['phone']:
                prompt_parts.append(f"- 手机: {student_info['phone']}")

            # --- [核心修改] ---
            # 优先使用数据库中的画像
            if student_info['description']:
                student_description = student_info['description']
            else:
                # 如果为空，动态生成一个“基础画像”
                student_description = f"该用户是 {student_info['class_name']} 的学生 {student_info['name']} (学号: {student_info['student_id_number']})。目前暂无个性化画像，请在交流中逐步了解。"

                # [智能方案] 立即将这个基础画像写回数据库，解决“冷启动”
                try:
                    conn.execute("UPDATE students SET description = ? WHERE id = ?", (student_description, user_id))
                    conn.commit()
                    print(f"[PROFILE_INIT] 已为学生 {user_id} 初始化基础画像。")
                except Exception as e:
                    print(f"[ERROR] 初始化学生 {user_id} 画像失败: {e}")

            prompt_parts.append(f"- 个人描述: {student_description}")
            # --- [修改结束] ---

            prompt_parts.append(f"\n--- 班级与课堂信息 ---")
            prompt_parts.append(f"- 所在行政班级: {student_info['class_name']}")

            # 3. 获取班级人数
            count_result = conn.execute("SELECT COUNT(*) FROM students WHERE class_id = ?",
                                        (student_info['class_id'],)).fetchone()
            if count_result:
                prompt_parts.append(f"- 行政班级人数: {count_result[0]}")

        if offering_info:
            prompt_parts.append(f"- 正在学习的课程: {offering_info['course_name']}")
            prompt_parts.append(f"- 授课教师: {offering_info['teacher_name']}")

        prompt_parts.append(f"- 所在课堂 ID: {class_offering_id}")

    prompt_parts.append("\n请根据以上信息，并结合你掌握的课程大纲和知识点（RAG材料）来回答问题。")
    return "\n".join(prompt_parts)


async def update_user_profile(user_pk: int, user_role: str, session_db_id: int):
    """
    (后台任务) 异步总结用户画像并更新数据库。
    """
    print(f"[PROFILE_TASK] 触发画像更新: {user_role} {user_pk}, session {session_db_id}")
    try:
        table_name = "teachers" if user_role == "teacher" else "students"

        with get_db_connection() as conn:
            # 1. 获取当前画像

            # [核心修改] 我们需要更详细的信息来生成“基础画像” (如果需要的话)
            if user_role == 'teacher':
                user_data = conn.execute(
                    "SELECT description, name, email FROM teachers WHERE id = ?", (user_pk,)
                ).fetchone()
            else:
                user_data = conn.execute(
                    """
                    SELECT s.description, s.name, s.student_id_number, c.name as class_name
                    FROM students s
                             JOIN classes c ON s.class_id = c.id
                    WHERE s.id = ?
                    """, (user_pk,)
                ).fetchone()

            if not user_data:
                print(f"[PROFILE_TASK] [ERROR] 未找到用户 {user_role} {user_pk}。")
                return

            current_desc = user_data['description']

            # 如果画像为空（理论上已被 format_system_prompt 填充，但作为双重保险）
            if not current_desc:
                print(f"[PROFILE_TASK] [WARN] {user_role} {user_pk} 画像为空，正在生成基础画像...")
                if user_role == 'teacher':
                    current_desc = f"该用户是教师 {user_data['name']}。目前暂无个性化画像。"
                else:
                    current_desc = f"该用户是 {user_data['class_name']} 的学生 {user_data['name']} (学号: {user_data['student_id_number']})。目前暂无个性化画像。"

                # (这里也写入，确保万无一失)
                try:
                    conn.execute(f"UPDATE {table_name} SET description = ? WHERE id = ?", (current_desc, user_pk))
                    conn.commit()
                except Exception as e:
                    print(f"[ERROR] 在 update_user_profile 中初始化画像失败: {e}")
            # [修改结束]

            # 2. 获取本会话中用户最近的 10 条发言
            messages_cursor = conn.execute(
                """
                SELECT message
                FROM ai_chat_messages
                WHERE session_id = ?
                  AND role = 'user'
                ORDER BY timestamp DESC
                    LIMIT 10
                """,
                (session_db_id,)
            )
            # 反转顺序，让AI按时间顺序阅读
            user_messages = [row['message'] for row in messages_cursor][::-1]

            if not user_messages:
                print(f"[PROFILE_TASK] 未找到用户发言。跳过。")
                return

        # 3. 准备 AI 提示词
        history_text = "\n".join([f"- {msg}" for msg in user_messages])
        profile_prompt = f"""
你是一个资深的心理学家和用户画像专家。
你的任务是根据用户的【当前画像】和他们的【最近聊天记录】，将画像更新或精炼。
画像必须保持在100字以内，用语简洁、客观，重点描述用户的提问风格、知识水平和个性特征。

【当前画像】:
{current_desc}

【最近聊天记录 (仅用户发言)】:
{history_text}

请输出更新后的100字画像:
"""

        # 4. 调用 AI (重用 /api/ai/chat 接口, 请求 "thinking" 模型)
        chat_payload = {
            "system_prompt": "你是一个资深的心理学家和用户画像专家。你的任务是总结用户画像。",
            "messages": [],  # 无需历史记录，提示词已包含所有信息
            "new_message": profile_prompt,
            "model_capability": "thinking",  # 使用高阶模型进行总结
            "user_id": str(user_pk),
            "user_role": user_role
        }

        # ai_client 是在 ai.py 顶部导入的
        response = await ai_client.post("/api/ai/chat", json=chat_payload, timeout=120.0)
        response.raise_for_status()
        ai_response_data = response.json()

        if ai_response_data.get("status") == "success":
            new_description = ai_response_data.get("response_text", "").strip()

            # 过滤掉AI的额外发言 (例如 "好的，这是更新后的画像：...")
            if "：" in new_description:
                new_description = new_description.split("：", 1)[-1].strip()
            if ":" in new_description:
                new_description = new_description.split(":", 1)[-1].strip()

            if new_description and len(new_description) > 5:  # 确保不是空响应
                # 5. 更新数据库
                with get_db_connection() as conn:
                    conn.execute(
                        f"UPDATE {table_name} SET description = ? WHERE id = ?",
                        (new_description, user_pk)
                    )
                    conn.commit()
                print(f"[PROFILE_TASK] 成功更新画像: {user_role} {user_pk}")
            else:
                print(f"[PROFILE_TASK] AI 返回的画像无效: {new_description}")
        else:
            print(f"[PROFILE_TASK] AI 总结失败: {ai_response_data.get('detail')}")

    except Exception as e:
        print(f"[PROFILE_TASK] [ERROR] 更新画像失败: {e}")


@router.get("/ai/chat/sessions/{class_offering_id}", response_class=JSONResponse)
async def get_ai_chat_sessions(class_offering_id: int, user: dict = Depends(get_current_user)):
    """获取当前用户在此课堂的所有 AI 聊天会话列表"""
    user_pk, user_role = _get_user_pk_role(user)

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, session_uuid, title, created_at
            FROM ai_chat_sessions
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            ORDER BY created_at DESC
            """,
            (class_offering_id, user_pk, user_role)
        )
        sessions = [dict(row) for row in cursor.fetchall()]

    return {"status": "success", "sessions": sessions}


@router.post("/ai/chat/session/new/{class_offering_id}", response_class=JSONResponse)
async def create_new_ai_chat_session(class_offering_id: int, user: dict = Depends(get_current_user)):
    """为当前用户在此课堂创建一个新的 AI 聊天会话"""
    user_pk, user_role = _get_user_pk_role(user)
    new_uuid = str(uuid.uuid4())
    default_title = "新对话"
    # --- 新增：在创建会话时生成并缓存用户背景 ---
    try:
        user_context_prompt = format_system_prompt(user_pk, user_role, class_offering_id)
    except Exception as e:
        # 如果（极罕见）生成 prompt 失败，也继续，后续聊天时会再次尝试
        print(f"[ERROR] 创建会话时生成 context_prompt 失败: {e}")
        user_context_prompt = ""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ai_chat_sessions (session_uuid, class_offering_id, user_pk, user_role, title, context_prompt)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_uuid, class_offering_id, user_pk, user_role, default_title, user_context_prompt)
            )
            session_id = cursor.lastrowid
            conn.commit()

        return {
            "status": "success",
            "session": {
                "id": session_id,
                "session_uuid": new_uuid,
                "title": default_title,
                "created_at": "now"  # 简化返回
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {e}")


@router.get("/ai/chat/history/{session_uuid}", response_class=JSONResponse)
async def get_ai_chat_history(session_uuid: str, user: dict = Depends(get_current_user)):
    """获取特定 AI 聊天会话的所有消息"""
    user_pk, user_role = _get_user_pk_role(user)

    with get_db_connection() as conn:
        # 1. 验证会话所有权
        session = conn.execute(
            """
            SELECT id
            FROM ai_chat_sessions
            WHERE session_uuid = ?
              AND user_pk = ?
              AND user_role = ?
            """,
            (session_uuid, user_pk, user_role)
        ).fetchone()

        if not session:
            raise HTTPException(status_code=403, detail="无权访问此会话")

        # 2. 获取消息
        cursor = conn.execute(
            """
            SELECT role, message, attachments_json, timestamp
            FROM ai_chat_messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
            """,
            (session['id'],)
        )
        messages = []
        for row in cursor.fetchall():
            msg = dict(row)
            msg['attachments'] = json.loads(msg['attachments_json']) if msg['attachments_json'] else []
            del msg['attachments_json']
            messages.append(msg)

    return {"status": "success", "messages": messages}


@router.post("/ai/chat")  # (路由保持不变, 但返回类型变为 StreamingResponse)
async def handle_ai_chat(
        request: Request,
        files: List[UploadFile] = File([]),  # 接收文件
        message: str = Form(...),
        session_uuid: str = Form(...),
        class_offering_id: int = Form(...),  # (从 classroom 变量中获取)
        user: dict = Depends(get_current_user),
        deep_thinking: bool = Form(False)
):
    """
    (V4.3 流式修改)
    处理 AI 聊天消息 (核心路由)
    接收文本和文件，流式调用 AI 助教，异步保存记录，返回流式响应
    """
    user_pk, user_role = _get_user_pk_role(user)

    # 1. 验证会话所有权并获取会话 DB ID
    with get_db_connection() as conn:
        session = conn.execute(
            """
            SELECT id, context_prompt
            FROM ai_chat_sessions
            WHERE session_uuid = ?
              AND user_pk = ?
              AND user_role = ?
              AND class_offering_id = ?
            """,
            (session_uuid, user_pk, user_role, class_offering_id)
        ).fetchone()
        if not session:
            raise HTTPException(status_code=403, detail="会话不存在或无权访问")
        session_db_id = session['id']

    # 2. 获取缓存的用户背景
    user_context_prompt = session['context_prompt']

    # 容错处理：如果缓存为空（例如这是老会话），则现场生成一次
    if not user_context_prompt:
        print(f"[WARN] Session {session_uuid} 没有缓存背景，正在重新生成...")
        try:
            user_context_prompt = format_system_prompt(user_pk, user_role, class_offering_id)
        except Exception as e:
            print(f"[ERROR] 现场生成 context_prompt 失败: {e}")
            user_context_prompt = f"无法加载用户 {user_pk} 的背景信息。"

    # 3. 处理上传的文件 -> 转换为 Base64
    base64_urls = []
    user_attachments = []
    model_capability: Literal["standard", "thinking", "vision"] = "standard"

    if files:
        model_capability = "vision"  # 只要有文件就切换到 vision
        for file in files:
            try:
                b64_url = await _upload_file_to_base64(file)
                base64_urls.append(b64_url)
                user_attachments.append({"type": "image", "name": file.filename})
            except HTTPException as e:
                # 如果只是某个文件失败，可以跳过
                print(f"文件 {file.filename} 处理失败: {e.detail}")
            except Exception as e:
                print(f"文件 {file.filename} 处理失败: {e}")
    elif deep_thinking:
        model_capability = "thinking"
    attachments_json = json.dumps(user_attachments) if user_attachments else None

    # 4. 保存用户的消息到数据库
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_chat_messages (session_id, role, message, attachments_json)
            VALUES (?, 'user', ?, ?)
            """,
            (session_db_id, message, attachments_json)
        )
        conn.commit()

    # 5. 加载 RAG 材料和 System Prompt
    with get_db_connection() as conn:
        config = conn.execute(
            "SELECT system_prompt, syllabus FROM ai_class_configs WHERE class_offering_id = ?",
            (class_offering_id,)
        ).fetchone()

    # 6. 加载聊天记录 (用于发送给 AI)
    history_cursor = conn.execute(
        """
        SELECT role, message
        FROM ai_chat_messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
        """,
        (session_db_id,)
    )

    # 7. 构建 AI 需要的 messages 列表
    # (V4.3 修改: 我们需要发送给 AI 的是除最后一条外的所有消息,
    # 因为最后一条(用户的)消息会通过 new_message 字段发送)
    ai_history_for_call = []
    all_messages = history_cursor.fetchall()

    # 提取除最后一条（我们刚插入的）之外的所有消息
    for row in all_messages[:-1]:
        ai_history_for_call.append({"role": row['role'], "content": row['message']})

    # 8. 构建最终的 System Prompt (RAG + 配置)
    teacher_base_prompt = config['system_prompt'] if config and config['system_prompt'] else "你是一个课堂AI助手。"
    rag_syllabus = config['syllabus'] if config and config['syllabus'] else "（无课程大纲信息）"

    final_system_prompt = f"""
    {teacher_base_prompt}

    --- 课程大纲/知识点 (RAG) ---
    {rag_syllabus}
    ---------------------------
    """

    # 9. [!! 核心修改 1 !!]
    # 如果历史记录为空 (这是会话的第一条消息)，则注入用户背景
    if not ai_history_for_call and user_context_prompt:
        print(f"[CHAT] 注入用户背景 {user_role} {user_pk} 到会话 {session_uuid}")
        ai_history_for_call.insert(0, {
            "role": "system",
            "content": f"--- 提问者背景信息 ---\n{user_context_prompt}\n--- (背景信息结束) ---"
        })

    # 10. 准备发送给 ai_assistant 的数据
    chat_payload = {
        "system_prompt": final_system_prompt,  # 简短的 (RAG + 教师指令)
        "messages": ai_history_for_call,  # 历史记录 (不含最新)
        "new_message": message,  # 用户的最新消息
        "base64_urls": base64_urls,
        "model_capability": model_capability,
        # (user_id 和 user_role 已被移除, V3.3.3 版 ai_assistant 不再需要它们)
    }

    # 11. [!!! 核心修改 2: 创建流式生成器 !!!]
    async def stream_and_save_generator():
        """
        这个内部生成器负责:
        1. 流式调用 ai_assistant
        2. 将 AI 响应(chunk) yield 给前端
        3. 在流结束后，将完整响应保存到数据库
        4. 触发画像更新
        """
        full_response_text = ""
        thinking_content = ""
        final_answer = ""
        is_thinking = False

        try:
            # 11.1. 流式调用 ai_assistant
            async with ai_client.stream(
                    "POST",
                    "/api/ai/chat-stream",  # [!!] 调用新的流式端点
                    json=chat_payload,
                    timeout=180.0
            ) as response:

                # 检查 HTTP 级别的错误
                if not response.is_success:
                    # 读取错误详情
                    error_detail = await response.aread()
                    error_msg = f"AI 助手服务连接失败 (状态码 {response.status_code}): {error_detail.decode('utf-8', errors='ignore')}"
                    print(f"[ERROR] {error_msg}")
                    yield error_msg  # 将错误信息流式传输给前端
                    full_response_text = error_msg  # (确保下面保存的是错误信息)

                else:
                    # 11.2. 迭代 stream, 转发 chunk
                    async for chunk in response.aiter_text():
                        full_response_text += chunk

                        # 实时解析思考过程
                        if "【思考过程开始】" in chunk:
                            is_thinking = True
                            # 移除标记，只保留内容
                            chunk = chunk.replace("【思考过程开始】", "")
                        elif "【思考过程结束】" in chunk:
                            is_thinking = False
                            chunk = chunk.replace("【思考过程结束】", "")

                        if is_thinking:
                            thinking_content += chunk
                        else:
                            final_answer += chunk

                        yield chunk  # 保持原始流式传输

        except httpx.ConnectError:
            error_msg = "无法连接到 AI 助教服务。"
            print(f"[ERROR] {error_msg}")
            yield error_msg
            full_response_text = error_msg
        except Exception as e:
            error_msg = f"AI 流式传输中发生未知错误: {e}"
            print(f"[ERROR] {error_msg}")
            yield error_msg
            full_response_text = error_msg

        # 11.3. [!!! 核心修改: 流结束后保存 !!!]

        # 确保 full_response_text 不为空, 避免存入空数据
        if not full_response_text or full_response_text.isspace():
            print("[WARN] AI 返回了空响应，将保存占位符。")
            full_response_text = "（AI 没有返回有效内容）"
            # (如果流中没有 yield 任何东西, 我们在这里 yield 一次)
            # (但通常错误处理已经 yield 过了, 所以这里只用于DB保存)

        try:
            with get_db_connection() as conn:
                # 如果有思考过程，存储为 JSON 格式；否则存储为纯文本
                if thinking_content.strip():
                    message_data = {
                        "thinking": thinking_content.strip(),
                        "answer": final_answer.strip()
                    }
                    stored_message = json.dumps(message_data, ensure_ascii=False)
                else:
                    stored_message = final_answer.strip()

                conn.execute(
                    """
                    INSERT INTO ai_chat_messages (session_id, role, message)
                    VALUES (?, 'assistant', ?)
                    """,
                    (session_db_id, stored_message)
                )
                conn.commit()
            print(f"[CHAT] 成功保存流式响应 (Session: {session_db_id}, Length: {len(full_response_text)})")
        except Exception as e:
            print(f"[ERROR] 保存 AI 流式响应失败: {e}")
            # (此时流已结束，无法再通知前端)

        # 11.4. [!!! 核心修改: 触发画像更新 !!!]
        try:
            with get_db_connection() as conn:
                user_msg_count = conn.execute(
                    "SELECT COUNT(*) FROM ai_chat_messages WHERE session_id = ? AND role = 'user'",
                    (session_db_id,)
                ).fetchone()[0]

            # 每 5 条用户消息触发一次
            if user_msg_count > 0 and user_msg_count % 5 == 0:
                print(f"[CHAT] 触发画像更新 (第 {user_msg_count} 条消息)")
                asyncio.create_task(update_user_profile(user_pk, user_role, session_db_id))

        except Exception as e:
            print(f"[ERROR] 检查或触发画像更新失败: {e}")

    # 12. [!!! 核心修改: 返回 StreamingResponse !!!]
    return StreamingResponse(stream_and_save_generator(), media_type="text/plain; charset=utf-8")


# ============================
# AI试卷生成API
# ============================

def get_course_context_for_offering(class_offering_id: int, teacher_id: int) -> Dict[str, Any]:
    """获取课堂的课程上下文信息（大纲、简介等）"""
    with get_db_connection() as conn:
        # 验证教师是否有权访问此课堂 - 先不使用LEFT JOIN
        offering = conn.execute(
            """SELECT co.id, co.course_id, c.name as course_name, c.description as course_description,
                      cl.name as class_name
               FROM class_offerings co
               JOIN courses c ON co.course_id = c.id
               JOIN classes cl ON co.class_id = cl.id
               WHERE co.id = ? AND co.teacher_id = ?""",
            (class_offering_id, teacher_id)
        ).fetchone()

        if not offering:
            raise HTTPException(status_code=404, detail="课堂不存在或无权访问")

        # 转换为字典
        offering_dict = dict(offering)

        # 单独获取AI配置（如果存在）
        syllabus = ""
        system_prompt = ""
        ai_config = conn.execute(
            "SELECT syllabus, system_prompt FROM ai_class_configs WHERE class_offering_id = ?",
            (class_offering_id,)
        ).fetchone()
        if ai_config:
            ai_dict = dict(ai_config)
            syllabus = ai_dict.get('syllabus') or ""
            system_prompt = ai_dict.get('system_prompt') or ""

        # 获取课程文件信息（如果有）
        materials = conn.execute(
            """SELECT file_name as title, description, stored_path as file_path
               FROM course_files
               WHERE course_id = ?
               ORDER BY uploaded_at DESC
               LIMIT 10""",
            (offering_dict['course_id'],)
        ).fetchall()

        return {
            "offering_id": offering_dict['id'],
            "course_name": offering_dict['course_name'],
            "course_description": offering_dict.get('course_description') or "",
            "class_name": offering_dict['class_name'],
            "syllabus": syllabus,
            "system_prompt": system_prompt,
            "materials": [dict(row) for row in materials]
        }


async def generate_exam_questions_async(task_id: str, prompt: str, teacher_id: int, class_offering_id: Optional[int]):
    """异步生成试卷题目（调用高级模型）"""
    paper_id = None
    try:
        # 首先检查任务是否已被取消
        async with _exam_gen_tasks_lock:
            if task_id not in _exam_gen_tasks:
                print(f"[WARN] 任务 {task_id} 不存在，跳过生成")
                return
            if _exam_gen_tasks[task_id]['status'] == ExamGenTaskStatus.CANCELLED:
                print(f"[INFO] 任务 {task_id} 已被取消，跳过生成")
                return
            _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.RUNNING
            _exam_gen_tasks[task_id]['started_at'] = datetime.now().isoformat()
            paper_id = _exam_gen_tasks[task_id].get('paper_id')

        # 更新数据库中的AI生成状态为running
        if paper_id:
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE exam_papers SET ai_gen_status = ?, updated_at = ? WHERE id = ?",
                        ('running', datetime.now().isoformat(), paper_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[WARN] 更新数据库状态失败: {e}")

        # 准备调用AI助手的payload
        payload = {
            "prompt": prompt,
            "model_type": "thinking",  # 使用高级模型
            "task_type": "exam_generation",
            "teacher_id": teacher_id,
            "class_offering_id": class_offering_id
        }

        print(f"[AI_GEN] 开始调用AI生成试卷 (Task: {task_id}, Paper: {paper_id})")

        # 调用AI助手服务（设置较长超时）
        response = await ai_client.post("/api/ai/generate-exam", json=payload, timeout=300.0)
        response.raise_for_status()
        result = response.json()

        # 再次检查任务是否已被取消
        async with _exam_gen_tasks_lock:
            if task_id not in _exam_gen_tasks:
                print(f"[WARN] 任务 {task_id} 在生成过程中被移除")
                return
            if _exam_gen_tasks[task_id]['status'] == ExamGenTaskStatus.CANCELLED:
                print(f"[INFO] 任务 {task_id} 在生成过程中被取消")
                return

        if result.get("status") == "success":
            exam_data = result.get("exam_data", {})
            # 验证返回的数据结构
            if not isinstance(exam_data, dict):
                raise ValueError("AI返回的数据格式不正确")

            async with _exam_gen_tasks_lock:
                _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.COMPLETED
                _exam_gen_tasks[task_id]['result'] = exam_data
                _exam_gen_tasks[task_id]['completed_at'] = datetime.now().isoformat()

            # 更新数据库（保持 status='generating'，让前端轮询能检测到完成状态）
            if paper_id:
                try:
                    questions_json = json.dumps(exam_data, ensure_ascii=False)
                    description = exam_data.get('description', '') or f"AI生成的试卷"
                    with get_db_connection() as conn:
                        conn.execute(
                            """UPDATE exam_papers
                               SET questions_json = ?, description = ?,
                                   ai_gen_status = 'completed', updated_at = ?
                               WHERE id = ?""",
                            (questions_json, description, datetime.now().isoformat(), paper_id)
                        )
                        conn.commit()
                except Exception as e:
                    print(f"[WARN] 更新数据库失败: {e}")

            print(f"[AI_GEN] 试卷生成成功 (Task: {task_id}, Paper: {paper_id})")
        else:
            error_msg = result.get("detail", "AI生成失败")
            async with _exam_gen_tasks_lock:
                _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.FAILED
                _exam_gen_tasks[task_id]['error'] = error_msg
                _exam_gen_tasks[task_id]['completed_at'] = datetime.now().isoformat()

            # 更新数据库为失败状态
            if paper_id:
                try:
                    with get_db_connection() as conn:
                        conn.execute(
                            """UPDATE exam_papers
                               SET ai_gen_status = 'failed', ai_gen_error = ?, updated_at = ?
                               WHERE id = ?""",
                            (error_msg, datetime.now().isoformat(), paper_id)
                        )
                        conn.commit()
                except Exception as e:
                    print(f"[WARN] 更新数据库失败状态失败: {e}")

            print(f"[AI_GEN] AI返回失败 (Task: {task_id}): {error_msg}")

    except httpx.TimeoutException:
        async with _exam_gen_tasks_lock:
            if task_id in _exam_gen_tasks and _exam_gen_tasks[task_id]['status'] != ExamGenTaskStatus.CANCELLED:
                _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.FAILED
                _exam_gen_tasks[task_id]['error'] = "AI生成超时（可能模型处理时间过长，请稍后重试）"
                _exam_gen_tasks[task_id]['completed_at'] = datetime.now().isoformat()
                paper_id = _exam_gen_tasks[task_id].get('paper_id')

        if paper_id:
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        """UPDATE exam_papers
                           SET ai_gen_status = 'failed', ai_gen_error = 'AI生成超时（可能模型处理时间过长，请稍后重试）', updated_at = ?
                           WHERE id = ?""",
                        (datetime.now().isoformat(), paper_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[WARN] 更新数据库超时状态失败: {e}")

        print(f"[AI_GEN] 生成超时 (Task: {task_id})")
    except httpx.ConnectError:
        async with _exam_gen_tasks_lock:
            if task_id in _exam_gen_tasks and _exam_gen_tasks[task_id]['status'] != ExamGenTaskStatus.CANCELLED:
                _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.FAILED
                _exam_gen_tasks[task_id]['error'] = "AI助手服务未运行，请先启动 ai_assistant.py。"
                _exam_gen_tasks[task_id]['completed_at'] = datetime.now().isoformat()
                paper_id = _exam_gen_tasks[task_id].get('paper_id')

        if paper_id:
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        """UPDATE exam_papers
                           SET ai_gen_status = 'failed', ai_gen_error = 'AI助手服务未运行，请先启动 ai_assistant.py。', updated_at = ?
                           WHERE id = ?""",
                        (datetime.now().isoformat(), paper_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[WARN] 更新数据库连接失败状态失败: {e}")

        print(f"[AI_GEN] 连接AI服务失败 (Task: {task_id})")
    except Exception as e:
        print(f"[AI_GEN] 生成异常 (Task: {task_id}): {e}")
        traceback.print_exc()
        async with _exam_gen_tasks_lock:
            if task_id in _exam_gen_tasks and _exam_gen_tasks[task_id]['status'] != ExamGenTaskStatus.CANCELLED:
                _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.FAILED
                _exam_gen_tasks[task_id]['error'] = f"AI生成过程中发生错误: {str(e)}"
                _exam_gen_tasks[task_id]['completed_at'] = datetime.now().isoformat()
                paper_id = _exam_gen_tasks[task_id].get('paper_id')

        if paper_id:
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        """UPDATE exam_papers
                           SET ai_gen_status = 'failed', ai_gen_error = ?, updated_at = ?
                           WHERE id = ?""",
                        (f"AI生成过程中发生错误: {str(e)}", datetime.now().isoformat(), paper_id)
                    )
                    conn.commit()
            except Exception as db_e:
                print(f"[WARN] 更新数据库异常状态失败: {db_e}")


@router.post("/ai/exam/suggest-topics", response_class=JSONResponse)
async def ai_suggest_exam_topics(request: Request, user: dict = Depends(get_current_teacher)):
    """获取出题范围推荐（调用普通AI）"""
    try:
        data = await request.json()
        class_offering_id = data.get('class_offering_id')

        if not class_offering_id:
            raise HTTPException(status_code=400, detail="请指定课堂ID")

        try:
            class_offering_id = int(class_offering_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="无效的课堂ID格式")

        # 获取课程上下文
        context = get_course_context_for_offering(class_offering_id, user['id'])

        # 构建提示词
        prompt = f"""
请根据以下课程信息，推荐适合出题的知识点范围：

课程名称：{context['course_name']}
课程描述：{context['course_description']}
班级：{context['class_name']}
教学大纲：{context['syllabus'][:500]}...

请列出3-5个主要的出题范围，每个范围包含：
1. 知识点主题
2. 建议的题目类型（单选、多选、填空、问答）
3. 难度分布建议
4. 简要说明为什么这个范围适合出题

请用清晰的结构化格式返回。
"""

        # 调用AI助手（使用标准模型）
        response = await ai_client.post("/api/ai/chat", json={
            "system_prompt": "你是一个教学专家，擅长分析课程内容并推荐合适的出题范围。",
            "messages": [],
            "new_message": prompt,
            "model_capability": "standard"
        }, timeout=60.0)

        response.raise_for_status()
        result = response.json()

        if result.get("status") == "success":
            return {
                "status": "success",
                "topics": result.get("response_text", ""),
                "course_context": {
                    "course_name": context['course_name'],
                    "class_name": context['class_name'],
                    "syllabus_preview": context['syllabus'][:300] + "..." if len(context['syllabus']) > 300 else context['syllabus']
                }
            }
        else:
            raise HTTPException(status_code=500, detail=f"AI推荐失败: {result.get('detail')}")

    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="AI助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI服务响应超时，请稍后重试。")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="请求数据格式错误")
    except Exception as e:
        print(f"[ERROR] 获取出题范围推荐失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取出题范围推荐失败: {str(e)}")


@router.post("/ai/exam/generate", response_class=JSONResponse)
async def ai_generate_exam(request: Request, background_tasks: BackgroundTasks, user: dict = Depends(get_current_teacher)):
    """启动AI生成试卷任务（调用高级模型，异步）"""
    try:
        data = await request.json()

        # 验证必填字段
        required_fields = ['scope', 'title']
        for field in required_fields:
            if field not in data or data.get(field) is None:
                raise HTTPException(status_code=400, detail=f"缺少必填字段: {field}")

        # 验证试卷标题
        title = data['title'].strip() if isinstance(data['title'], str) else ''
        if not title:
            raise HTTPException(status_code=400, detail="试卷标题不能为空")
        if len(title) > 200:
            raise HTTPException(status_code=400, detail="试卷标题不能超过200个字符")

        # 出题范围必填
        scope = data['scope'].strip() if isinstance(data['scope'], str) else ''
        if not scope:
            raise HTTPException(status_code=400, detail="出题范围不能为空")
        if len(scope) < 10:
            raise HTTPException(status_code=400, detail="出题范围描述太短，请提供更详细的内容（至少10个字符）")
        if len(scope) > 5000:
            raise HTTPException(status_code=400, detail="出题范围描述过长，请控制在5000个字符以内")

        # 验证难度
        difficulty = data.get('difficulty', 'medium')
        if difficulty not in ['easy', 'medium', 'hard']:
            raise HTTPException(status_code=400, detail="难度必须是: easy, medium, hard")

        # 验证总题数
        total_questions = data.get('total_questions', 10)
        try:
            total_questions = int(total_questions)
            if total_questions < 1 or total_questions > 100:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="总题数必须是1-100之间的整数")

        # 验证题型分布
        question_types = data.get('question_types', {})
        valid_types = ['radio', 'checkbox', 'text', 'textarea']
        for qtype, count in question_types.items():
            if qtype not in valid_types:
                raise HTTPException(status_code=400, detail=f"无效的题型: {qtype}")
            try:
                count = int(count)
                if count < 0 or count > 100:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"题型 {qtype} 的数量必须是0-100之间的整数")

        # 验证并处理课堂ID
        class_offering_id = data.get('class_offering_id')
        if class_offering_id:
            try:
                class_offering_id = int(class_offering_id)
                # 验证教师是否有权访问此课堂
                with get_db_connection() as conn:
                    offering = conn.execute(
                        "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                        (class_offering_id, user['id'])
                    ).fetchone()
                    if not offering:
                        raise HTTPException(status_code=403, detail="无权访问此课堂或课堂不存在")
            except ValueError:
                raise HTTPException(status_code=400, detail="无效的课堂ID")

        # 创建试卷ID和任务ID
        paper_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        # 先在数据库中创建试卷记录，状态为generating
        now = datetime.now().isoformat()
        empty_questions = json.dumps({"pages": []}, ensure_ascii=False)
        exam_config = json.dumps({
            "scope": scope,
            "difficulty": difficulty,
            "total_questions": total_questions,
            "question_types": question_types,
            "class_offering_id": class_offering_id
        }, ensure_ascii=False)

        with get_db_connection() as conn:
            conn.execute(
                """INSERT INTO exam_papers
                   (id, teacher_id, title, description, questions_json, exam_config_json, status, ai_gen_task_id, ai_gen_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (paper_id, user['id'], title, f"AI正在生成中，出题范围：{scope[:100]}...",
                 empty_questions, exam_config, 'generating', task_id, 'pending', now, now)
            )
            conn.commit()

        # 初始化任务状态
        async with _exam_gen_tasks_lock:
            _exam_gen_tasks[task_id] = {
                'id': task_id,
                'paper_id': paper_id,
                'teacher_id': user['id'],
                'status': ExamGenTaskStatus.PENDING,
                'created_at': now,
                'title': title,
                'scope': scope,
                'class_offering_id': class_offering_id,
                'question_types': question_types,
                'difficulty': difficulty,
                'total_questions': total_questions,
                'result': None,
                'error': None,
                'started_at': None,
                'completed_at': None
            }

        # 构建生成提示词
        prompt_parts = [
            f"请生成一份试卷，标题：{title}",
            f"出题范围：{scope}",
            f"难度：{difficulty}",
            f"总题数：{total_questions}"
        ]

        # 添加题型分布
        if question_types:
            type_desc = []
            for qtype, count in question_types.items():
                if int(count) > 0:
                    type_labels = {'radio': '单选题', 'checkbox': '多选题', 'text': '填空题', 'textarea': '问答题'}
                    type_desc.append(f"{type_labels.get(qtype, qtype)}: {count}题")
            if type_desc:
                prompt_parts.append(f"题型分布：{', '.join(type_desc)}")

        # 添加课程上下文（如果有课堂）
        if class_offering_id:
            try:
                context = get_course_context_for_offering(int(class_offering_id), user['id'])
                prompt_parts.append(f"\n课程背景信息：")
                prompt_parts.append(f"课程名称：{context['course_name']}")
                if context['course_description']:
                    prompt_parts.append(f"课程描述：{context['course_description'][:200]}...")
                if context['syllabus']:
                    prompt_parts.append(f"教学大纲要点：{context['syllabus'][:300]}...")
            except Exception as e:
                print(f"[WARN] 获取课程上下文失败: {e}")
                pass  # 忽略上下文获取失败

        prompt = "\n".join(prompt_parts)
        prompt += "\n\n请生成完整的试卷题目，具体要求如下："
        prompt += "\n1. 题目类型说明：radio=单选题，checkbox=多选题，text=填空题，textarea=问答题"
        prompt += "\n2. 每道题必须包含：id（唯一标识，如q1,q2）、type（题型）、text（题目内容）"
        prompt += "\n3. 选择题必须提供options数组（至少2个选项），并指定answer（单选题为单个选项字母如'A'，多选题为数组如['A','B']）"
        prompt += "\n4. 填空题和问答题可以提供placeholder作为提示文本，answer为字符串答案"
        prompt += "\n5. 每道题必须包含explanation（解析），说明为什么答案正确或其他选项为什么错误"
        prompt += "\n6. 试卷可以分多个部分（pages），每个部分有name和questions数组"
        prompt += "\n7. 根据难度要求调整题目难度：简单=基础知识点，中等=需要一定思考，困难=综合应用或分析"
        prompt += "\n8. 确保题目覆盖出题范围的所有主要知识点"
        prompt += "\n9. 返回格式必须为JSON，包含pages数组，每个page对象包含name和questions数组"
        prompt += "\n10. 不要包含任何额外的解释或代码块标记，只返回JSON数据"

        # 在后台启动生成任务
        background_tasks.add_task(generate_exam_questions_async, task_id, prompt, user['id'], class_offering_id)

        return {
            "status": "success",
            "task_id": task_id,
            "paper_id": paper_id,
            "message": "试卷生成任务已启动，这可能需要几分钟时间。",
            "estimated_time": "约5分钟"
        }

    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="请求数据格式错误")
    except Exception as e:
        print(f"[ERROR] 启动生成任务失败: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"启动生成任务失败: {str(e)}")


@router.get("/ai/exam/task/{task_id}/status", response_class=JSONResponse)
async def get_exam_gen_task_status(task_id: str, user: dict = Depends(get_current_teacher)):
    """获取试卷生成任务状态"""
    # 验证任务ID格式
    try:
        # 验证UUID格式
        uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的任务ID格式")

    async with _exam_gen_tasks_lock:
        task = _exam_gen_tasks.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task['teacher_id'] != user['id']:
        raise HTTPException(status_code=403, detail="无权访问此任务")

    # 清理返回数据，移除敏感信息
    result = {
        'id': task['id'],
        'status': task['status'],
        'title': task['title'],
        'created_at': task['created_at'],
        'started_at': task.get('started_at'),
        'completed_at': task.get('completed_at'),
        'error': task.get('error')
    }

    # 如果任务完成，包含结果
    if task['status'] == ExamGenTaskStatus.COMPLETED and task.get('result'):
        result['exam_data'] = task['result']

    return {"status": "success", "task": result}


@router.post("/ai/exam/task/{task_id}/cancel", response_class=JSONResponse)
async def cancel_exam_gen_task(task_id: str, user: dict = Depends(get_current_teacher)):
    """取消试卷生成任务"""
    # 验证任务ID格式
    try:
        uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的任务ID格式")

    async with _exam_gen_tasks_lock:
        task = _exam_gen_tasks.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task['teacher_id'] != user['id']:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    if task['status'] == ExamGenTaskStatus.COMPLETED:
        return {"status": "success", "message": "任务已完成，无法取消"}
    if task['status'] == ExamGenTaskStatus.FAILED:
        return {"status": "success", "message": "任务已失败，无法取消"}
    if task['status'] == ExamGenTaskStatus.CANCELLED:
        return {"status": "success", "message": "任务已取消"}

    async with _exam_gen_tasks_lock:
        _exam_gen_tasks[task_id]['status'] = ExamGenTaskStatus.CANCELLED
        _exam_gen_tasks[task_id]['completed_at'] = datetime.now().isoformat()
        _exam_gen_tasks[task_id]['error'] = "任务已被用户取消"

    # 删除数据库中的空试卷（取消后无有用内容）
    paper_id = task.get('paper_id')
    if paper_id:
        try:
            with get_db_connection() as conn:
                conn.execute(
                    "DELETE FROM exam_papers WHERE id = ? AND teacher_id = ?",
                    (paper_id, user['id'])
                )
                conn.commit()
        except Exception as e:
            print(f"[WARN] 删除取消的试卷失败: {e}")

    return {"status": "success", "message": "任务已取消", "paper_id": paper_id}


@router.get("/ai/exam/paper/{paper_id}/status", response_class=JSONResponse)
async def get_exam_paper_gen_status(paper_id: str, user: dict = Depends(get_current_teacher)):
    """获取试卷的AI生成状态（从数据库查询）"""
    try:
        uuid.UUID(paper_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的试卷ID格式")

    with get_db_connection() as conn:
        paper = conn.execute(
            "SELECT * FROM exam_papers WHERE id = ? AND teacher_id = ?",
            (paper_id, user['id'])
        ).fetchone()

        if not paper:
            raise HTTPException(status_code=404, detail="试卷不存在或无权访问")

        paper_dict = dict(paper)

        # 如果状态是generating/pending/running，同时检查内存中的任务状态
        ai_gen_status = paper_dict.get('ai_gen_status')
        task_info = None

        if ai_gen_status in ['pending', 'running'] and paper_dict.get('ai_gen_task_id'):
            task_id = paper_dict['ai_gen_task_id']
            async with _exam_gen_tasks_lock:
                if task_id in _exam_gen_tasks:
                    task = _exam_gen_tasks[task_id]
                    task_info = {
                        'status': task['status'],
                        'created_at': task['created_at'],
                        'started_at': task.get('started_at'),
                        'completed_at': task.get('completed_at'),
                        'error': task.get('error')
                    }

        return {
            "status": "success",
            "paper": {
                "id": paper_dict['id'],
                "title": paper_dict['title'],
                "status": paper_dict['status'],
                "ai_gen_status": ai_gen_status,
                "ai_gen_error": paper_dict.get('ai_gen_error'),
                "created_at": paper_dict['created_at'],
                "updated_at": paper_dict['updated_at'],
                "task_info": task_info
            }
        }


@router.get("/ai/exam/papers/generating", response_class=JSONResponse)
async def get_generating_exam_papers(user: dict = Depends(get_current_teacher)):
    """获取当前教师所有正在生成中的试卷"""
    with get_db_connection() as conn:
        papers = conn.execute(
            """SELECT * FROM exam_papers
               WHERE teacher_id = ? AND status = 'generating'
               ORDER BY created_at DESC""",
            (user['id'],)
        ).fetchall()

        result = []
        for paper in papers:
            paper_dict = dict(paper)
            result.append({
                "id": paper_dict['id'],
                "title": paper_dict['title'],
                "ai_gen_status": paper_dict.get('ai_gen_status'),
                "ai_gen_error": paper_dict.get('ai_gen_error'),
                "created_at": paper_dict['created_at'],
                "updated_at": paper_dict['updated_at']
            })

        return {"status": "success", "papers": result}

