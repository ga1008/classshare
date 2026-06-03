from .common import *


router = APIRouter()

@router.get("/teacher-onboarding/state", response_class=JSONResponse)
async def api_get_teacher_onboarding_state(user: dict = Depends(get_current_teacher)):
    teacher_id = int(user["id"])
    with get_db_connection() as conn:
        return build_teacher_onboarding_payload(conn, teacher_id)


@router.post("/teacher-onboarding/dismiss", response_class=JSONResponse)
async def api_dismiss_teacher_onboarding(request: Request, user: dict = Depends(get_current_teacher)):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    teacher_id = int(user["id"])
    reason = str(payload.get("reason") or "manual_exit")
    with get_db_connection() as conn:
        mark_teacher_onboarding_dismissed(conn, teacher_id, reason)
        conn.commit()
        result = build_teacher_onboarding_payload(conn, teacher_id)

    result["message"] = "新手引导状态已更新。"
    return result


@router.post("/teacher-onboarding/classes/create", response_class=JSONResponse)
async def api_create_onboarding_class(request: Request, user: dict = Depends(get_current_teacher)):
    data = await _parse_json_request(request)
    class_name = str(data.get("name") or data.get("class_name") or "").strip()
    description = str(data.get("description") or "").strip()
    department = normalize_department(data.get("department")) or infer_department_from_text(class_name, description)

    if not class_name:
        raise HTTPException(400, "请填写班级名称")
    if not department:
        raise HTTPException(400, "请填写或选择班级所属系别")

    with get_db_connection() as conn:
        try:
            org_scope = apply_teacher_scope_to_org(
                conn,
                user["id"],
                college=data.get("college") or "",
                department=department,
            )
            cursor = conn.execute(
                """
                INSERT INTO classes (
                    name, department, description, created_by_teacher_id,
                    school_code, school_name, college
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    class_name,
                    department,
                    description,
                    user["id"],
                    org_scope["school_code"],
                    org_scope["school_name"],
                    org_scope["college"],
                ),
            )
            class_id = int(cursor.lastrowid)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(400, f"创建班级失败：{class_name} 已存在或数据不完整") from exc

    return {
        "status": "success",
        "message": f"班级“{class_name}”已创建",
        "class": {
            "id": class_id,
            "name": class_name,
            "department": department,
            "description": description,
            "student_count": 0,
            "related_course_ids": [],
        },
    }


@router.post("/teacher-onboarding/course-description", response_class=JSONResponse)
async def api_generate_onboarding_course_description(request: Request, user: dict = Depends(get_current_teacher)):
    data = await _parse_json_request(request)
    course_name = str(data.get("course_name") or data.get("name") or "").strip()
    department = normalize_department(data.get("department"))
    textbook_id = _parse_optional_int(data.get("textbook_id"))

    if not course_name:
        raise HTTPException(400, "请先填写课程名称")

    textbook = None
    if textbook_id:
        with get_db_connection() as conn:
            textbook_row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=user["id"])
            textbook = serialize_textbook_row(textbook_row)

    fallback = build_default_course_description(
        course_name=course_name,
        department=department,
        textbook=textbook,
    )
    textbook_hint = ""
    if textbook:
        textbook_hint = build_textbook_prompt_context(textbook)

    prompt = (
        "你是一名高校课程简介撰写助手。请使用简体中文，为教师生成一段可以直接放入课程信息的课程简介。"
        "要求：160-260 字，清晰说明课程定位、学习目标、实践方式和适用专业；不要输出标题或项目符号。"
    )
    user_message = (
        f"课程名称：{course_name}\n"
        f"系别：{department or '未填写'}\n"
        f"教材信息：\n{textbook_hint or '未选择教材'}\n"
        f"本地草稿：{fallback}"
    )
    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": prompt,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "standard",
                "task_type": "fast_text_response",
                "web_search_enabled": False,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        ai_data = response.json()
        generated = str(ai_data.get("response_text") or "").strip()
        if ai_data.get("status") == "success" and generated:
            return {
                "status": "success",
                "message": "AI 已生成课程简介草稿",
                "description": generated[:1600],
                "fallback": False,
            }
    except Exception:
        pass

    return {
        "status": "success",
        "message": "AI 暂时不可用，已使用本地课程简介草稿",
        "description": fallback,
        "fallback": True,
    }


@router.post("/teacher-onboarding/complete", response_class=JSONResponse)
async def api_complete_teacher_onboarding(request: Request, user: dict = Depends(get_current_teacher)):
    data = await _parse_json_request(request)
    teacher_id = int(user["id"])
    course_data = data.get("course") if isinstance(data.get("course"), dict) else {}
    ai_data = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    schedule_data = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}

    course_data = {
        **course_data,
        "course_id": course_data.get("course_id") or course_data.get("id") or data.get("course_id"),
    }
    try:
        course_payload = _prepare_course_payload(course_data, require_lessons=True)
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc

    semester_id = _parse_optional_int(data.get("semester_id"))
    class_id = _parse_optional_int(data.get("class_id"))
    textbook_id = _parse_optional_int(data.get("textbook_id"))
    if not semester_id or not class_id or not textbook_id:
        raise HTTPException(400, "请完整选择学期、教材和班级")

    selected_material_ids = _normalize_material_id_list(data.get("material_ids"))
    home_learning_material_id = _parse_optional_int(data.get("home_learning_material_id"))

    with get_db_connection() as conn:
        try:
            lesson_material_ids = [
                lesson.get("learning_material_id")
                for lesson in course_payload["lessons"]
                if lesson.get("learning_material_id")
            ]
            lesson_material_map = get_learning_material_brief_map(
                conn,
                lesson_material_ids,
                teacher_id=teacher_id,
                markdown_only=True,
            )
            if len(lesson_material_map) != len({int(item) for item in lesson_material_ids}):
                raise HTTPException(400, "绑定到具体课次的材料需要是可作为课堂文档的 Markdown 文件")

            selected_materials_to_validate = [
                material_id
                for material_id in [*selected_material_ids, home_learning_material_id]
                if material_id
            ]
            selected_material_map = get_learning_material_brief_map(
                conn,
                selected_materials_to_validate,
                teacher_id=teacher_id,
                markdown_only=False,
            )
            if len(selected_material_map) != len({int(item) for item in selected_materials_to_validate}):
                raise HTTPException(400, "所选教学材料不存在或无权访问")
            material_map = {**selected_material_map, **lesson_material_map}
            markdown_home_map = get_learning_material_brief_map(
                conn,
                [*lesson_material_ids, *selected_material_ids, home_learning_material_id],
                teacher_id=teacher_id,
                markdown_only=True,
            )
            if home_learning_material_id and home_learning_material_id not in markdown_home_map:
                home_learning_material_id = None

            if course_payload["course_id"]:
                _ensure_teacher_can_manage_course(
                    conn,
                    course_id=course_payload["course_id"],
                    teacher_id=teacher_id,
                )
                conn.execute(
                    """
                    UPDATE courses
                    SET name = ?, description = ?, sect_name = ?, department = ?, credits = ?, total_hours = ?
                    WHERE id = ?
                    """,
                    (
                        course_payload["name"],
                        course_payload["description"],
                        course_payload["sect_name"],
                        course_payload["department"],
                        course_payload["credits"],
                        course_payload["total_hours"],
                        course_payload["course_id"],
                    ),
                )
                course_id = int(course_payload["course_id"])
                course_action = "更新"
            else:
                org_scope = apply_teacher_scope_to_org(
                    conn,
                    teacher_id,
                    department=course_payload["department"],
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
                        course_payload["name"],
                        course_payload["description"],
                        course_payload["sect_name"],
                        course_payload["department"],
                        course_payload["credits"],
                        course_payload["total_hours"],
                        teacher_id,
                        org_scope["school_code"],
                        org_scope["school_name"],
                        org_scope["college"],
                    ),
                )
                course_id = int(cursor.lastrowid)
                course_action = "创建"

            replace_course_lessons(conn, course_id=course_id, lessons=course_payload["lessons"])

            offering_payload = _prepare_offering_payload(
                conn,
                teacher_id=teacher_id,
                data={
                    "class_id": class_id,
                    "course_id": course_id,
                    "semester_id": semester_id,
                    "textbook_id": textbook_id,
                    "first_class_date": schedule_data.get("first_class_date") or data.get("first_class_date"),
                    "weekly_schedule": schedule_data.get("weekly_schedule") or data.get("weekly_schedule") or [],
                },
                require_schedule=True,
                allow_missing_lessons=False,
            )
            semester_name = str(offering_payload["semester_row"]["name"] or "").strip()
            existing_offering = conn.execute(
                """
                SELECT id
                FROM class_offerings
                WHERE class_id = ?
                  AND course_id = ?
                  AND teacher_id = ?
                  AND (
                        semester_id = ?
                        OR (
                            semester_id IS NULL
                            AND COALESCE(semester, '') = ?
                        )
                  )
                LIMIT 1
                """,
                (class_id, course_id, teacher_id, semester_id, semester_name),
            ).fetchone()

            if existing_offering:
                offering_id = int(existing_offering["id"])
                conn.execute(
                    """
                    UPDATE class_offerings
                    SET semester = ?,
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
                        semester_name,
                        semester_id,
                        textbook_id,
                        offering_payload["plan"]["schedule_info"],
                        offering_payload["first_class_date"].isoformat() if offering_payload["first_class_date"] else "",
                        offering_payload["weekly_schedule_json"],
                        offering_payload["schedule_source"],
                        offering_payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "开课向导使用教务实际排课生成时间轴。"
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                        offering_id,
                        teacher_id,
                    ),
                )
                offering_action = "更新"
            else:
                cursor = conn.execute(
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
                        class_id,
                        course_id,
                        teacher_id,
                        semester_name,
                        semester_id,
                        textbook_id,
                        offering_payload["plan"]["schedule_info"],
                        offering_payload["first_class_date"].isoformat() if offering_payload["first_class_date"] else "",
                        offering_payload["weekly_schedule_json"],
                        offering_payload["schedule_source"],
                        offering_payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "开课向导使用教务实际排课生成时间轴。"
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                    ),
                )
                offering_id = int(cursor.lastrowid)
                offering_action = "开设"

            replace_offering_sessions(
                conn,
                offering_id=offering_id,
                sessions=offering_payload["plan"]["sessions"],
            )

            plan_material_ids = [
                session.get("learning_material_id")
                for session in offering_payload["plan"]["sessions"]
                if session.get("learning_material_id")
            ]
            all_assignment_ids = _normalize_material_id_list(
                [*plan_material_ids, *selected_material_ids, home_learning_material_id]
            )
            if all_assignment_ids:
                sync_classroom_learning_material_assignments(
                    conn,
                    class_offering_id=offering_id,
                    teacher_id=teacher_id,
                    material_ids=all_assignment_ids,
                )

            if not home_learning_material_id:
                for material_id in all_assignment_ids:
                    if material_id in markdown_home_map:
                        home_learning_material_id = material_id
                        break
            if home_learning_material_id:
                conn.execute(
                    """
                    UPDATE class_offerings
                    SET home_learning_material_id = ?
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (home_learning_material_id, offering_id, teacher_id),
                )

            textbook_row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=teacher_id)
            class_row = _ensure_teacher_can_use_class(conn, class_id=class_id, teacher_id=teacher_id)
            default_ai = build_default_ai_config(
                teacher_name=str(user.get("name") or "老师"),
                course_name=course_payload["name"],
                class_name=str(class_row["name"] or ""),
                semester_name=semester_name,
                department=course_payload["department"],
                textbook_title=str(textbook_row["title"] or ""),
                course_description=course_payload["description"],
                material_names=[item.get("name", "") for item in material_map.values()],
            )
            system_prompt = str(ai_data.get("system_prompt") or default_ai["system_prompt"]).strip()
            syllabus = str(ai_data.get("syllabus") or default_ai["syllabus"]).strip()
            conn.execute(
                """
                INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus)
                VALUES (?, ?, ?)
                ON CONFLICT(class_offering_id) DO UPDATE SET
                    system_prompt = excluded.system_prompt,
                    syllabus = excluded.syllabus,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (offering_id, system_prompt, syllabus),
            )

            mark_teacher_onboarding_dismissed(conn, teacher_id, "completed")
            conn.commit()
        except CoursePlanningError as exc:
            conn.rollback()
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(400, f"保存失败，该课堂可能已经存在：{exc}") from exc
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(500, f"开课失败: {exc}") from exc

    return {
        "status": "success",
        "message": f"课程已{course_action}，课堂已{offering_action}，并生成 {offering_payload['plan']['session_count']} 次课。",
        "course_id": course_id,
        "class_offering_id": offering_id,
        "classroom_url": f"/classroom/{offering_id}",
        "preview": offering_payload["plan"],
    }


__all__ = [name for name in globals() if not name.startswith("__")]
