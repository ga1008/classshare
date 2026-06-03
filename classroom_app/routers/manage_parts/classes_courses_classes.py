from .common import *


router = APIRouter()

@router.post("/classes/create", response_class=JSONResponse)
async def api_create_class(request: Request, class_name: str = Form(), file: UploadFile = File(...),
                           department: str = Form(default=""),
                           school_name: str = Form(default=""),
                           college: str = Form(default=""),
                           user: dict = Depends(get_current_teacher)):
    """从Excel文件创建班级和学生"""

    # 1. 保存 Excel 文件
    temp_excel_path = ROSTER_DIR / f"temp_{uuid.uuid4()}_{file.filename}"
    try:
        async with aiofiles.open(temp_excel_path, 'wb') as out_file:
            while content := await file.read(1024 * 1024): await out_file.write(content)
    except Exception as e:
        raise HTTPException(500, f"保存文件失败: {e}")

    # 2. 解析 Excel
    students_data = parse_excel_to_students(temp_excel_path)
    if students_data is None:
        if temp_excel_path.exists():
            temp_excel_path.unlink()  # 清理临时文件
        raise HTTPException(400, "解析Excel失败，请检查文件格式和列名（需包含'姓名'和'学号'）。")
    missing_email_count = sum(1 for item in students_data if not str(item.get("email") or "").strip())

    # 3. 存入数据库 (使用事务)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 创建班级
        normalized_department = normalize_department(department) or infer_department_from_text(class_name)
        org_scope = apply_teacher_scope_to_org(
            conn,
            user["id"],
            school_name=school_name,
            college=college,
            department=normalized_department,
        )
        cursor.execute(
            """
            INSERT INTO classes (
                name, department, created_by_teacher_id,
                school_code, school_name, college
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                class_name,
                normalized_department,
                user['id'],
                org_scope["school_code"],
                org_scope["school_name"],
                org_scope["college"],
            ),
        )
        class_id = cursor.lastrowid

        # 批量插入学生
        students_to_insert = [
            (
                s['student_id_number'],
                s['name'],
                class_id,
                s.get('gender'),
                s.get('email'),
                s.get('phone'),
                org_scope["school_code"],
                org_scope["school_name"],
                org_scope["college"],
                org_scope["department"],
            )
            for s in students_data
        ]
        cursor.executemany(
            """
            INSERT INTO students (
                student_id_number, name, class_id, gender, email, phone,
                school_code, school_name, college, department
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            students_to_insert
        )
        conn.commit()

    except sqlite3.IntegrityError as e:
        # 显示更详细的错误信息并打印堆栈跟踪
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(400, f"创建失败：{e}。可能是班级名称或学号已存在。")
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"数据库操作失败: {e}")
    finally:
        conn.close()
        if temp_excel_path.exists():
            temp_excel_path.unlink()  # 清理临时文件

    message = f"成功创建班级 '{class_name}' 并导入 {len(students_data)} 名学生。"
    if missing_email_count:
        message += f" 其中 {missing_email_count} 名学生缺少邮箱，后续只能收到站内通知，可提醒学生在个人中心补充。"
    return {"status": "success", "message": message, "missing_email_count": missing_email_count}


@router.post("/classes/sync-current-academic", response_class=JSONResponse)
async def api_sync_current_classes_from_academic_system(
    user: dict = Depends(get_current_teacher),
):
    result = await sync_current_teacher_rosters_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步班级和学生名单。")
    return result


@router.get("/classrooms/teaching-places", response_class=JSONResponse)
async def api_list_academic_teaching_places(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    try:
        page_size = int(request.query_params.get("page_size") or request.query_params.get("limit") or 10)
    except (TypeError, ValueError):
        page_size = 10
    try:
        page = int(request.query_params.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    page_size = max(1, min(page_size, 120))
    page = max(1, page)
    filters = {
        "search": str(request.query_params.get("q") or "").strip(),
        "campus_id": str(request.query_params.get("campus_id") or "").strip(),
        "building_id": str(request.query_params.get("building_id") or "").strip(),
        "room_type_id": str(request.query_params.get("room_type_id") or "").strip(),
        "availability": str(request.query_params.get("availability") or "").strip(),
        "include_stale": str(request.query_params.get("include_stale") or "").lower() in {"1", "true", "yes"},
    }
    with get_db_connection() as conn:
        total_count = count_teacher_teaching_places(conn, int(user["id"]), **filters)
        total_page = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_page)
        places = load_teacher_teaching_places(
            conn,
            int(user["id"]),
            **filters,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        dashboard = load_teacher_teaching_place_dashboard(conn, int(user["id"]))
    return {
        "status": "success",
        "items": places,
        "count": len(places),
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_page": total_page,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_page": total_page,
        },
        "dashboard": dashboard,
    }


@router.post("/classrooms/sync-academic", response_class=JSONResponse)
async def api_sync_academic_teaching_places(user: dict = Depends(get_current_teacher)):
    result = await sync_teaching_places_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步教学场地。")
    return result


@router.get("/classrooms/free-options", response_class=JSONResponse)
async def api_load_free_classroom_options(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    result = await load_free_classroom_options_from_academic_system(
        int(user["id"]),
        xnm=str(request.query_params.get("xnm") or "").strip(),
        xqm=str(request.query_params.get("xqm") or "").strip(),
        semester_id=str(request.query_params.get("semester_id") or "").strip(),
        xqh_id=str(request.query_params.get("xqh_id") or "1").strip() or "1",
    )
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能读取教务系统教室选项。")
    return result


@router.post("/classrooms/free-query", response_class=JSONResponse)
async def api_query_free_classrooms(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = await query_free_classrooms_from_academic_system(int(user["id"]), payload)
    status = result.get("status")
    if status == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if status == "invalid":
        raise HTTPException(400, result.get("message") or "请补全空闲教室查询条件。")
    if status != "success":
        raise HTTPException(502, result.get("message") or "未能实时查询教务系统空闲教室。")
    return result


@router.get("/classrooms/{class_offering_id}/exam-roster", response_class=JSONResponse)
async def api_get_classroom_exam_roster(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    result = load_classroom_exam_roster_status(int(user["id"]), int(class_offering_id))
    if result.get("status") == "not_found":
        raise HTTPException(404, result.get("message") or "课堂不存在或无权访问。")
    return result


@router.post("/classrooms/{class_offering_id}/exam-roster/sync", response_class=JSONResponse)
async def api_sync_classroom_exam_roster(
    class_offering_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = await sync_classroom_exam_roster_from_academic_system(
        int(user["id"]),
        int(class_offering_id),
        exam_course_key=str(payload.get("exam_course_key") or "").strip(),
    )
    status = result.get("status")
    if status == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if status in {"not_found", "no_semester"}:
        raise HTTPException(400 if status == "no_semester" else 404, result.get("message") or "无法同步考试名单。")
    if status == "needs_confirmation":
        return result
    if status != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步考试名单。")
    return result


@router.get("/classrooms/{class_offering_id}/academic-exams", response_class=JSONResponse)
async def api_get_classroom_academic_exams(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    return load_classroom_course_exam_status(int(user["id"]), int(class_offering_id))


@router.post("/classrooms/{class_offering_id}/academic-exams/sync", response_class=JSONResponse)
async def api_sync_classroom_academic_exams(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    try:
        result = await sync_classroom_course_exams_from_academic_system(
            int(user["id"]),
            int(class_offering_id),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    status = result.get("status")
    if status == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if status == "no_current_semester":
        raise HTTPException(400, result.get("message") or "请先同步当前学期。")
    if status != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步任课考试。")
    return result


@router.post("/classrooms/{class_offering_id}/exam-roster/export")
async def api_export_classroom_exam_roster(
    class_offering_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        export_result = build_exam_roster_signature_workbook(
            int(user["id"]),
            int(class_offering_id),
            export_payload=payload,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(
        export_result["path"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=export_result["filename"],
        headers={"Cache-Control": "no-store"},
    )


@router.post("/classes/{class_id}/students", response_class=JSONResponse)
async def api_create_class_student(
    class_id: int,
    name: str = Form(...),
    student_id_number: str = Form(...),
    gender: str = Form(default=""),
    email: str = Form(default=""),
    phone: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """向已有班级追加单个学生，适用于插班等日常维护。"""
    cleaned_name = _clean_form_text(name, limit=80)
    cleaned_student_id = _clean_form_text(student_id_number, limit=80)
    cleaned_gender = _clean_form_text(gender, limit=20)
    cleaned_email = _clean_form_text(email, limit=160)
    cleaned_phone = _clean_form_text(phone, limit=80)

    if not cleaned_name:
        raise HTTPException(status_code=400, detail="请填写学生姓名")
    if not cleaned_student_id:
        raise HTTPException(status_code=400, detail="请填写学生学号")

    with get_db_connection() as conn:
        class_row = _ensure_teacher_owned_class(conn, class_id=class_id, teacher_id=user["id"])
        class_scope = apply_teacher_scope_to_org(
            conn,
            user["id"],
            school_code=class_row["school_code"] if "school_code" in class_row.keys() else "",
            school_name=class_row["school_name"] if "school_name" in class_row.keys() else "",
            college=class_row["college"] if "college" in class_row.keys() else "",
            department=class_row["department"] if "department" in class_row.keys() else "",
        )
        try:
            cursor = conn.execute(
                """
                INSERT INTO students (
                    student_id_number, name, class_id, gender, email, phone,
                    enrollment_status, enrollment_status_updated_at,
                    school_code, school_name, college, department
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_student_id,
                    cleaned_name,
                    int(class_id),
                    cleaned_gender,
                    cleaned_email,
                    cleaned_phone,
                    local_iso(),
                    class_scope["school_code"],
                    class_scope["school_name"],
                    class_scope["college"],
                    class_scope["department"],
                ),
            )
            student_id = int(cursor.lastrowid)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(
                status_code=400,
                detail="新增失败：该学号已经存在，请先确认学生是否已在其它班级名单中。",
            ) from exc

    return {
        "status": "success",
        "message": f"已将 {cleaned_name} 加入班级。",
        "student": {
            "id": student_id,
            "name": cleaned_name,
            "student_id_number": cleaned_student_id,
            "gender": cleaned_gender,
            "email": cleaned_email,
            "phone": cleaned_phone,
            "enrollment_status": STUDENT_STATUS_ACTIVE,
            "enrollment_status_label": student_enrollment_status_label(STUDENT_STATUS_ACTIVE),
        },
    }


@router.post("/students/{student_id}/status", response_class=JSONResponse)
async def api_update_class_student_status(
    student_id: int,
    enrollment_status: str = Form(...),
    enrollment_note: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """切换学生学籍状态；休学学生保留数据但不再纳入课堂管理统计。"""
    raw_status = str(enrollment_status or "").strip().lower()
    if raw_status not in {"active", "suspended", "在读", "休学"}:
        raise HTTPException(status_code=400, detail="学生状态参数不正确")

    normalized_status = normalize_student_enrollment_status(enrollment_status)
    note = _clean_form_text(enrollment_note, limit=500)

    with get_db_connection() as conn:
        student_row = _ensure_teacher_owned_student(conn, student_id=student_id, teacher_id=user["id"])
        conn.execute(
            """
            UPDATE students
            SET enrollment_status = ?,
                enrollment_status_updated_at = ?,
                enrollment_note = ?
            WHERE id = ?
            """,
            (normalized_status, local_iso(), note, int(student_id)),
        )
        conn.commit()

    if normalized_status != STUDENT_STATUS_ACTIVE:
        invalidate_session_for_user(str(student_id), "student")

    student_name = str(student_row["name"] or "学生")
    return {
        "status": "success",
        "message": f"{student_name} 已设置为{student_enrollment_status_label(normalized_status)}。",
        "student": {
            "id": int(student_id),
            "enrollment_status": normalized_status,
            "enrollment_status_label": student_enrollment_status_label(normalized_status),
            "enrollment_note": note,
        },
    }


@router.put("/students/{student_id}/support-note", response_class=JSONResponse)
async def api_update_student_support_note(
    request: Request,
    student_id: int,
    user: dict = Depends(get_current_teacher),
):
    """保存教师共享补充说明；同一学生的任课教师可共同查看和维护。"""
    data = await _parse_json_request(request)
    note_text = normalize_shared_teacher_note(data.get("note_text"))

    with get_db_connection() as conn:
        if not teacher_can_access_student(conn, teacher_id=int(user["id"]), student_id=int(student_id)):
            raise HTTPException(status_code=404, detail="学生不存在或无权操作")
        note = save_shared_student_teacher_note(
            conn,
            student_id=int(student_id),
            teacher_id=int(user["id"]),
            note_text=note_text,
            now_text=local_iso(),
        )
        conn.commit()

    message = "教师共享说明已保存。" if note_text else "教师共享说明已清空。"
    return {
        "status": "success",
        "message": message,
        "note": note,
        "limit": MAX_SHARED_NOTE_LENGTH,
    }


@router.delete("/students/{student_id}", response_class=JSONResponse)
async def api_delete_class_student(student_id: int, user: dict = Depends(get_current_teacher)):
    """从班级名册中删除单个学生及其关联课堂数据。"""
    with get_db_connection() as conn:
        student_row = _ensure_teacher_owned_student(conn, student_id=student_id, teacher_id=user["id"])
        student_name = str(student_row["name"] or "学生")
        try:
            conn.execute("DELETE FROM students WHERE id = ?", (int(student_id),))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"删除失败: {exc}") from exc

    invalidate_session_for_user(str(student_id), "student")
    return {"status": "success", "message": f"已删除学生 {student_name}。"}


@router.delete("/classes/{class_id}", response_class=JSONResponse)
async def api_delete_class(class_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个班级 (及其所有学生和课堂关联)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM classes WHERE id = ? AND (created_by_teacher_id = ? OR ? = 1)",
                (class_id, user['id'], 1 if is_super_admin_teacher(conn, user["id"]) else 0)
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该班级或班级不存在")

            # 删除 (依赖于 database.py 中设置的 PRAGMA foreign_keys = ON 和 ON DELETE CASCADE)
            # 1. 删除 students (通过外键)
            # 2. 删除 class_offerings (通过外键)
            # 3. 删除 class
            conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
            conn.commit()

    except HTTPException:
        raise
    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "班级删除成功。"}


__all__ = [name for name in globals() if not name.startswith("__")]
