from .common import *
from ...services.base_resource_modes_service import build_course_delete_blockers, raise_if_delete_blocked


router = APIRouter()

@router.post("/courses/save", response_class=JSONResponse)
async def api_save_course(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    try:
        payload = _prepare_course_payload(data, require_lessons=True)
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        try:
            selected_material_ids = [
                lesson.get("learning_material_id")
                for lesson in payload["lessons"]
                if lesson.get("learning_material_id")
            ]
            material_map = get_learning_material_brief_map(
                conn,
                selected_material_ids,
                teacher_id=int(user["id"]),
                markdown_only=True,
            )
            if len(material_map) != len({int(item) for item in selected_material_ids}):
                raise HTTPException(400, "课程中选择的课堂材料不存在、无权访问，或不是 Markdown 文档")

            if payload["course_id"]:
                _ensure_teacher_can_manage_course(
                    conn,
                    course_id=payload["course_id"],
                    teacher_id=user["id"],
                )
                conn.execute(
                    """
                    UPDATE courses
                    SET name = ?, description = ?, sect_name = ?, department = ?, credits = ?, total_hours = ?
                    WHERE id = ?
                    """,
                    (
                        payload["name"],
                        payload["description"],
                        payload["sect_name"],
                        payload["department"],
                        payload["credits"],
                        payload["total_hours"],
                        payload["course_id"],
                    ),
                )
                course_id = int(payload["course_id"])
                action_text = "更新"
            else:
                org_scope = apply_teacher_scope_to_org(
                    conn,
                    user["id"],
                    department=payload["department"],
                )
                cursor = conn.execute(
                    """
                    INSERT INTO courses (
                        name, description, sect_name, department, credits, total_hours,
                        created_by_teacher_id, school_code, school_name, college
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["name"],
                        payload["description"],
                        payload["sect_name"],
                        payload["department"],
                        payload["credits"],
                        payload["total_hours"],
                        user["id"],
                        org_scope["school_code"],
                        org_scope["school_name"],
                        org_scope["college"],
                    ),
                )
                course_id = int(cursor.lastrowid)
                action_text = "创建"

            replace_course_lessons(conn, course_id=course_id, lessons=payload["lessons"])
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(400, f"保存课程失败：{exc}") from exc

    return {
        "status": "success",
        "message": f"课程“{payload['name']}”已{action_text}",
        "course_id": course_id,
    }


@router.post("/courses/sync-current-academic", response_class=JSONResponse)
async def api_sync_current_courses_from_academic_system(
    user: dict = Depends(get_current_teacher),
):
    result = await sync_current_teacher_courses_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步课程。")
    return result


@router.post("/courses/ai-generate-lessons", response_class=JSONResponse)
async def api_ai_generate_course_lessons(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    course_name = str(data.get("name") or "").strip()
    course_description = str(data.get("description") or "").strip()
    textbook_id = _parse_optional_int(data.get("textbook_id"))
    total_hours = normalize_total_hours(data.get("total_hours"))
    per_session_sections = _parse_optional_int(data.get("per_session_sections"))

    if not course_name:
        raise HTTPException(400, "请先填写课程名称")
    if not textbook_id:
        raise HTTPException(400, "请先选择教材")
    if total_hours <= 0:
        raise HTTPException(400, "请先填写课程总学时")
    if not per_session_sections or per_session_sections <= 0:
        raise HTTPException(400, "请先填写每次课的小节数")
    if total_hours % per_session_sections != 0:
        raise HTTPException(400, "课程总学时必须能被每次课的小节数整除")

    session_count = total_hours // per_session_sections
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=user["id"])
        textbook = serialize_textbook_row(textbook_row)

    textbook_context = build_textbook_prompt_context(textbook)
    system_prompt = (
        "你是一名高校课程设计专家。请根据课程名称、课程简介和教材内容，"
        "为教师拆分出可直接落地的课堂设置。输出必须是合法 JSON 对象，"
        "不要输出 Markdown 代码块。"
    )
    user_message = (
        f"课程名称：{course_name}\n"
        f"课程简介：{course_description or '未补充'}\n"
        f"教材信息：\n{textbook_context}\n\n"
        f"请把课程拆成 {session_count} 次课，每次课固定 {per_session_sections} 小节。\n"
        "输出 JSON 对象，格式如下：\n"
        "{\n"
        '  "lessons": [\n'
        '    {"title": "第1次课标题", "content": "本次课内容概述，尽量具体到知识点、实验或案例。", "section_count": 2}\n'
        "  ]\n"
        "}\n\n"
        "要求：\n"
        "1. lessons 数量必须严格等于指定的课次数。\n"
        "2. 每一项都要贴合教材目录，内容循序渐进，避免重复。\n"
        "3. title 简洁明确，content 重点说明本次课讲什么、做什么。\n"
        "4. section_count 统一填写为指定的小节数。\n"
        "5. 不要输出额外解释文字。"
    )

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "thinking",
                "task_type": "deep_text_reasoning",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")

    if data.get("status") != "success":
        raise HTTPException(500, f"AI 返回异常: {data.get('detail', '未知错误')}")

    response_text = str(data.get("response_text") or "").strip()
    if not response_text:
        raise HTTPException(500, "AI 未返回有效内容")

    try:
        parsed = _parse_ai_json(response_text)
        generated_lessons = normalize_course_lessons(parsed.get("lessons"), require_items=True)
    except (json.JSONDecodeError, CoursePlanningError) as exc:
        raise HTTPException(500, f"AI 返回格式不正确：{exc}") from exc

    if len(generated_lessons) != session_count:
        raise HTTPException(
            500,
            f"AI 返回了 {len(generated_lessons)} 条课堂设置，预期应为 {session_count} 条，请重试。",
        )

    for item in generated_lessons:
        item["section_count"] = per_session_sections
        item["source_type"] = "ai"

    return {
        "status": "success",
        "message": f"已根据教材拆分出 {session_count} 次课，可继续手动调整。",
        "session_count": session_count,
        "total_hours": total_hours,
        "lessons": generated_lessons,
    }


@router.post("/courses/create", response_class=JSONResponse)
async def api_create_course(
        request: Request,
        name: str = Form(...),  # 改为必填
        description: str = Form(default=""),  # 明确指定默认值
        sect_name: str = Form(default=""),
        department: str = Form(default=""),
        credits: float = Form(default=0.0),  # 明确指定默认值
        user: dict = Depends(get_current_teacher)
):
    try:
        # 添加参数验证
        if not name or len(name.strip()) == 0:
            raise HTTPException(400, "课程名称不能为空")

        normalized_department = normalize_department(department) or infer_department_from_text(name, description)
        with get_db_connection() as conn:
            org_scope = apply_teacher_scope_to_org(
                conn,
                user["id"],
                department=normalized_department,
            )
            conn.execute(
                """
                INSERT INTO courses (
                    name, description, sect_name, department, credits, created_by_teacher_id,
                    school_code, school_name, college
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    description,
                    normalize_course_sect_name(sect_name, course_name=name),
                    normalized_department,
                    credits,
                    user['id'],
                    org_scope["school_code"],
                    org_scope["school_name"],
                    org_scope["college"],
                )
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建课程失败，可能名称已存在。")
    except Exception as e:
        print(f"创建课程错误: {str(e)}")  # 添加错误日志
        raise HTTPException(500, f"创建课程失败: {str(e)}")

    return {"status": "success", "message": f"课程 '{name}' 创建成功。"}


@router.delete("/courses/{course_id}", response_class=JSONResponse)
async def api_delete_course(course_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个课程 (及其所有文件和课堂关联)"""
    try:
        with get_db_connection() as conn:
            course_row = _ensure_teacher_can_manage_course(
                conn,
                course_id=course_id,
                teacher_id=user["id"],
            )
            raise_if_delete_blocked(
                f"课程“{course_row['name']}”",
                build_course_delete_blockers(conn, int(course_id)),
            )

            conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))

            # TODO: 还应按引用计数清理未被其他课程复用的哈希文件。

            conn.commit()

    except HTTPException:
        raise
    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "课程删除成功。"}


@router.post("/courses/{course_id}/files/upload", response_class=JSONResponse)
async def api_upload_course_file(
        course_id: int,
        file: UploadFile = File(...),
        is_public: bool = Form(True),
        is_teacher_resource: bool = Form(False),
        user: dict = Depends(get_current_teacher)
):
    """上传课程资源文件"""
    # 检查教师是否拥有此课程
    with get_db_connection() as conn:
        course = conn.execute("SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
                              (course_id, user['id'])).fetchone()
    if not course:
        raise HTTPException(403, "无权操作此课程")

    file_info = await save_file_globally(file)
    original_filename = "".join(
        c for c in str(file.filename or "upload") if c.isalnum() or c in (".", "_", "-")
    ).strip() or "upload"
    if not file_info:
        raise HTTPException(500, "保存文件到服务器失败")

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO course_files
                (course_id, file_name, file_hash, file_size, is_public, is_teacher_resource, uploaded_by_teacher_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course_id,
                original_filename,
                file_info["hash"],
                file_info["size"],
                is_public,
                is_teacher_resource,
                user["id"],
            )
        )
        conn.commit()

    return {"status": "success", "message": f"文件 '{original_filename}' 上传成功。"}


__all__ = [name for name in globals() if not name.startswith("__")]
