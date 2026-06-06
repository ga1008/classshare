from .common import *


router = APIRouter()

@router.post("/class_offerings/preview", response_class=JSONResponse)
async def api_preview_class_offering(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    try:
        with get_db_connection() as conn:
            payload = _prepare_offering_payload(
                conn,
                teacher_id=int(user["id"]),
                data=data,
                require_schedule=True,
                allow_missing_lessons=True,
            )
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc

    plan = payload["plan"]
    planned_section_count = sum(int(item.get("section_count") or 0) for item in payload["course_lessons"])

    return {
        "status": "success",
        "preview": plan,
        "class_name": str(payload["class_row"]["name"] or ""),
        "course_name": str(payload["course_row"]["name"] or ""),
        "semester_name": str(payload["semester_row"]["name"] or ""),
        "textbook_title": str(payload["textbook_row"]["title"] or ""),
        "course_lesson_count": len(payload["course_lessons"]),
        "planned_section_count": planned_section_count,
        "course_total_hours": int(payload["course_row"]["total_hours"] or 0),
        "schedule_source": payload["schedule_source"],
        "academic_teaching_class_name": payload["academic_teaching_class_name"],
        "academic_teaching_class_options": payload["academic_teaching_class_options"],
    }


@router.post("/class_offerings/save", response_class=JSONResponse)
async def api_save_class_offering(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    try:
        with get_db_connection() as conn:
            payload = _prepare_offering_payload(
                conn,
                teacher_id=int(user["id"]),
                data=data,
                require_schedule=True,
                allow_missing_lessons=False,
            )

            if payload["offering_id"]:
                _ensure_teacher_owned_offering(conn, payload["offering_id"], user["id"])
                conn.execute(
                    """
                    UPDATE class_offerings
                    SET class_id = ?,
                        course_id = ?,
                        semester = ?,
                        semester_id = ?,
                        textbook_id = ?,
                        schedule_info = ?,
                        first_class_date = ?,
                        weekly_schedule_json = ?,
                        schedule_source = ?,
                        academic_teaching_class_name = ?,
                        academic_schedule_sync_at = ?,
                        academic_schedule_sync_message = ?
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (
                        payload["class_id"],
                        payload["course_id"],
                        str(payload["semester_row"]["name"] or "").strip(),
                        payload["semester_id"],
                        payload["textbook_id"],
                        payload["plan"]["schedule_info"],
                        payload["first_class_date"].isoformat() if payload["first_class_date"] else "",
                        payload["weekly_schedule_json"],
                        payload["schedule_source"],
                        payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "保存课堂时使用教务实际排课生成时间轴。"
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                        payload["offering_id"],
                        user["id"],
                    ),
                )
                offering_id = int(payload["offering_id"])
                action_text = "更新"
            else:
                offering_id = execute_insert_returning_id(
                    conn,
                    """
                    INSERT INTO class_offerings (
                        class_id,
                        course_id,
                        teacher_id,
                        semester,
                        semester_id,
                        textbook_id,
                        schedule_info,
                        first_class_date,
                        weekly_schedule_json,
                        schedule_source,
                        academic_teaching_class_name,
                        academic_schedule_sync_at,
                        academic_schedule_sync_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["class_id"],
                        payload["course_id"],
                        user["id"],
                        str(payload["semester_row"]["name"] or "").strip(),
                        payload["semester_id"],
                        payload["textbook_id"],
                        payload["plan"]["schedule_info"],
                        payload["first_class_date"].isoformat() if payload["first_class_date"] else "",
                        payload["weekly_schedule_json"],
                        payload["schedule_source"],
                        payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "保存课堂时使用教务实际排课生成时间轴。"
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                    ),
                )
                action_text = "开设"

            replace_offering_sessions(
                conn,
                offering_id=offering_id,
                sessions=payload["plan"]["sessions"],
            )
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=offering_id,
                teacher_id=int(user["id"]),
                material_ids=[
                    session.get("learning_material_id")
                    for session in payload["plan"]["sessions"]
                    if session.get("learning_material_id")
                ],
            )
            conn.commit()
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError:
        raise HTTPException(400, "保存失败，该班级课程在当前学期可能已存在。")
    except Exception as exc:
        raise HTTPException(500, f"数据库错误: {exc}")

    return {
        "status": "success",
        "message": (
            f"课堂已{action_text}，并生成 {payload['plan']['session_count']} 次课的时间安排。"
        ),
        "offering_id": offering_id,
        "preview": payload["plan"],
    }


@router.post("/class_offerings/create", response_class=JSONResponse)
async def api_create_class_offering(
        request: Request,
        class_id: int = Form(...),
        course_id: int = Form(...),
        semester_id: int = Form(...),
        textbook_id: int = Form(...),
        user: dict = Depends(get_current_teacher)
):
    try:
        with get_db_connection() as conn:
            _, _, semester_row, textbook_row = _validate_teacher_owned_selection(
                conn,
                teacher_id=user["id"],
                class_id=class_id,
                course_id=course_id,
                semester_id=semester_id,
                textbook_id=textbook_id,
            )
            offering_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO class_offerings (
                    class_id,
                    course_id,
                    teacher_id,
                    semester,
                    semester_id,
                    textbook_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_id,
                    course_id,
                    user["id"],
                    str(semester_row["name"] or "").strip(),
                    semester_id,
                    textbook_id,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建失败，该班级课程在当前学期可能已存在。")
    except Exception as e:
        raise HTTPException(500, f"数据库错误: {e}")

    return {
        "status": "success",
        "message": f"课堂已开设，并绑定学期“{semester_row['name']}”和教材“{textbook_row['title']}”",
        "class_offering_id": offering_id,
    }


@router.delete("/class_offerings/{offering_id}", response_class=JSONResponse)
async def api_delete_class_offering(offering_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个课堂 (及其AI配置和聊天记录)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                (offering_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该课堂或课堂不存在")

            # 删除 (依赖于 ON DELETE CASCADE)
            # 1. 删除 chat_logs (通过外键)
            # 2. 删除 ai_class_configs (通过外键)
            # 3. 删除 class_offering
            conn.execute("DELETE FROM class_offerings WHERE id = ?", (offering_id,))
            conn.commit()

    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "课堂关联删除成功。"}


@router.post("/ai/configure", response_class=JSONResponse)
async def api_configure_ai_offering(
        class_offering_id: int = Form(...),
        system_prompt: str = Form(""),
        syllabus: str = Form(""),
        textbook_id: str = Form(default=""),
        user: dict = Depends(get_current_teacher)
):
    """
    创建或更新一个特定课堂的 AI 配置，并同步更新教材绑定
    """
    conn = get_db_connection()
    try:
        _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])

        textbook_id_value = int(str(textbook_id).strip()) if str(textbook_id).strip() else None
        bound_textbook_id = None
        if textbook_id_value:
            textbook_row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id_value, teacher_id=user["id"])
            bound_textbook_id = int(textbook_row["id"])

        conn.execute(
            """
            UPDATE class_offerings
            SET textbook_id = ?
            WHERE id = ? AND teacher_id = ?
            """,
            (bound_textbook_id, class_offering_id, user["id"]),
        )

        conn.execute(
            """
            INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus)
            VALUES (?, ?, ?)
            ON CONFLICT(class_offering_id) DO UPDATE SET
                system_prompt = excluded.system_prompt,
                syllabus = excluded.syllabus,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                class_offering_id,
                str(system_prompt or "").strip(),
                str(syllabus or "").strip(),
            ),
        )

        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"配置保存失败: {e}")
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "AI 配置保存成功！",
        "class_offering_id": class_offering_id,
        "textbook_id": bound_textbook_id,
    }


@router.get("/ai/config/{class_offering_id}", response_class=JSONResponse)
async def api_get_ai_config(class_offering_id: int, user: dict = Depends(get_current_teacher)):
    """获取一个特定课堂的 AI 配置"""
    conn = get_db_connection()
    try:
        offering = _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])
        config_row = conn.execute(
            "SELECT system_prompt, syllabus FROM ai_class_configs WHERE class_offering_id = ?",
            (class_offering_id,),
        ).fetchone()
        classroom_context = build_classroom_ai_context(conn, class_offering_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    config = dict(config_row) if config_row else {"system_prompt": "", "syllabus": ""}
    return {
        **config,
        "textbook_id": int(offering["textbook_id"]) if offering["textbook_id"] else None,
        "semester_name": str(offering["semester_name"] or ""),
        "textbook": classroom_context.get("textbook") or None,
        "classroom_summary": classroom_context.get("classroom_summary") or "",
        "textbook_summary": classroom_context.get("textbook_summary") or "",
        "recent_material_names": classroom_context.get("recent_material_names") or [],
        "recent_assignment_titles": classroom_context.get("recent_assignment_titles") or [],
    }


@router.post("/ai/ai-generate", response_class=JSONResponse)
async def api_ai_generate_config(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """调用思考模型 AI，根据课堂和教材信息生成系统提示词和课程大纲。"""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "请求数据格式错误")

    class_offering_id = data.get("class_offering_id")
    textbook_id = data.get("textbook_id")

    if not class_offering_id:
        raise HTTPException(400, "请先选择一个课堂")
    try:
        class_offering_id = int(class_offering_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "无效的课堂 ID")

    if not textbook_id:
        raise HTTPException(400, "请先选择一本教材，AI 生成需要教材信息作为知识依据")
    try:
        textbook_id = int(textbook_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "无效的教材 ID")

    # 获取课堂和教材上下文
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])
        _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=user["id"])
        classroom_context = build_classroom_ai_context(conn, class_offering_id)

    if not classroom_context:
        raise HTTPException(404, "课堂信息不存在")

    classroom_summary = classroom_context.get("classroom_summary") or ""
    textbook_summary = classroom_context.get("textbook_summary") or ""
    textbook = classroom_context.get("textbook") or {}
    recent_materials = classroom_context.get("recent_material_names") or []
    recent_assignments = classroom_context.get("recent_assignment_titles") or []

    teacher_name = classroom_context.get("teacher_name") or "任课教师"
    course_name = classroom_context.get("course_name") or "课程"
    class_name = classroom_context.get("class_name") or "班级"
    semester_name = classroom_context.get("semester_name") or ""
    class_student_count = classroom_context.get("class_student_count") or 0
    course_credits = classroom_context.get("course_credits")
    course_description = classroom_context.get("course_description") or ""

    # 构建发送给 AI 的提示词
    system_prompt_for_ai = (
        "你是一名高校课堂 AI 助教配置专家。根据教师提供的课堂信息和教材信息，"
        "为其生成课堂 AI 助教的「系统提示词」和「课程大纲 / 知识依据」。\n\n"
        "你的输出必须是合法的 JSON 对象，包含两个键：\n"
        "- \"system_prompt\"：课堂 AI 助教的系统提示词（字符串）\n"
        "- \"syllabus\"：课程大纲 / 知识依据（字符串）\n\n"
        "只输出 JSON 对象，不要输出任何额外的解释或 Markdown 代码块标记。"
    )

    user_message_parts = [
        f"请为以下课堂生成 AI 助教配置：\n",
        f"--- 课堂基本信息 ---",
        f"课程名称：{course_name}",
        f"授课班级：{class_name}",
        f"任课教师：{teacher_name}",
    ]
    if semester_name:
        user_message_parts.append(f"所属学期：{semester_name}")
    if class_student_count:
        user_message_parts.append(f"班级人数：{int(class_student_count)} 人")
    if course_credits is not None:
        user_message_parts.append(f"课程学分：{course_credits}")
    if course_description:
        user_message_parts.append(f"课程简介：{course_description.strip()[:800]}")

    user_message_parts.append(f"\n--- 教材信息 ---\n{textbook_summary}")

    if recent_materials:
        user_message_parts.append(f"\n--- 最近课堂材料 ---\n{'、'.join(recent_materials)}")
    if recent_assignments:
        user_message_parts.append(f"\n--- 最近课堂任务 ---\n{'、'.join(recent_assignments)}")

    user_message_parts.append(f"""
--- 生成要求 ---

一、system_prompt（系统提示词）要求：
这是给课堂 AI 助教看的提示词，让助教 AI 在回复学生时表现得活泼、可爱、热情且专业。
具体要求：
1. 赋予 AI 助教一个亲和力十足的角色设定，名字可以用"小X助手"之类的可爱称呼，语气自然轻松。
2. 使用简体中文回复，表达风格要生动活泼，适当使用鼓励性语言和表情符号（如"太棒了！""加油~""没问题，我来帮你~"等）。
3. 回答专业问题时必须严谨准确，不能为了活泼而牺牲专业性。
4. 面向学生时：优先讲思路、举例子、拆步骤，不直接代写作业或泄露考试答案；当学生遇到困难时先共情鼓励，再引导解决。
5. 面向教师时：帮助备课、设计活动、梳理知识点、优化教学表达，语气可以更专业但依然亲和。
6. 明确使用边界：超出课程范围的问题要温和说明边界并给出查证方向；教材/材料/大纲不一致时先指出差异。
7. 引用教材章节、知识点名称使建议可落地，让回答有根有据。
8. 学生焦虑或挫败时，先用短句共情，再给可执行的小步建议。
9. 任课教师在生成提示词时，请在提示词中体现教师姓名：{teacher_name}。
10. 提示词要详细完整，确保 AI 助教能够理解自己的角色定位、行为准则和教学目标。建议 300-600 字。

二、syllabus（课程大纲 / 知识依据）要求：
这是给助教 AI 看的知识参考，让 AI 全面了解课堂信息以便更好辅助教学。
具体要求：
1. 侧重点在课堂知识范围和核心知识点梳理上，这是最重要的部分。
2. 基于教材目录和教材简介，梳理出课程的章节结构、核心知识点和学习要点。
3. 包含课堂的基本信息：课程名称、班级、学期、教师、学生人数等。
4. 如果教材有目录信息，请按章节结构化整理出知识点概要。
5. 包含课程目标、考核方式的建议模板（供教师后续修改）。
6. 包含 AI 回答约束：哪些可以直接回答、哪些需引导回教材、哪些必须提醒教师确认。
7. 内容要全面详实，建议 500-1000 字。
""")

    user_message = "\n".join(user_message_parts)

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt_for_ai,
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
    except json.JSONDecodeError:
        raise HTTPException(500, "AI 返回的内容格式不正确，请重试")

    generated_system_prompt = str(parsed.get("system_prompt") or "").strip()
    generated_syllabus = str(parsed.get("syllabus") or "").strip()

    if not generated_system_prompt and not generated_syllabus:
        raise HTTPException(500, "AI 生成的内容为空，请重试")

    return {
        "status": "success",
        "system_prompt": generated_system_prompt,
        "syllabus": generated_syllabus,
    }


@router.get("/offerings/list", response_class=JSONResponse)
async def api_list_offerings(user: dict = Depends(get_current_teacher)):
    """获取当前教师的课堂列表（用于试卷分配）"""
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            """
               SELECT o.id,
                      COALESCE(s.name, o.semester) AS semester,
                      c.name AS class_name,
                      co.name AS course_name,
                      tb.title AS textbook_title
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               LEFT JOIN academic_semesters s ON s.id = o.semester_id
               LEFT JOIN textbooks tb ON tb.id = o.textbook_id
               WHERE o.teacher_id = ?
               ORDER BY COALESCE(s.start_date, o.created_at) DESC, co.name, c.name
            """,
            (user['id'],)
        )
        offerings = [dict(row) for row in cursor]
        conn.close()
        return {"status": "success", "offerings": offerings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


__all__ = [name for name in globals() if not name.startswith("__")]
