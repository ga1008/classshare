import base64
import json
import uuid
import asyncio
from pathlib import Path
from typing import List, Literal

from fastapi.responses import StreamingResponse

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pandas.core.missing import find_valid_index

from ..config import MAX_UPLOAD_SIZE_MB, MAX_UPLOAD_SIZE_BYTES
from ..core import ai_client
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user

router = APIRouter(prefix="/api")


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
    """向 AI 助手服务提交一个异步批改任务"""
    with get_db_connection() as conn:
        submission = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if not submission: raise HTTPException(status_code=404, detail="Submission not found")
        if submission['status'] == 'grading': return {"status": "already_grading"}
        assignment = conn.execute("SELECT rubric_md FROM assignments WHERE id = ?",
                                  (submission['assignment_id'],)).fetchone()
        files_cursor = conn.execute("SELECT stored_path FROM submission_files WHERE submission_id = ?",
                                    (submission_id,))
        file_paths = [row['stored_path'] for row in files_cursor]

    if not file_paths:
        raise HTTPException(status_code=400, detail="该学生未提交任何文件，AI无法批改。")

    job_data = {
        "submission_id": submission_id,
        "rubric_md": assignment['rubric_md'],
        "file_paths": [str(Path(p).resolve()) for p in file_paths]  # 确保发送绝对路径
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

