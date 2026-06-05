from .common import *

from ...services.base_resource_modes_service import (
    ensure_teacher_can_manage_class_attributes,
    ensure_teacher_can_manage_course_attributes,
    ensure_teacher_can_manage_textbook_attributes,
    ensure_teacher_can_view_class_attributes,
    ensure_teacher_can_view_course_attributes,
    ensure_teacher_can_view_textbook_attributes,
    serialize_class_attributes,
    serialize_course_attributes,
    serialize_course_content,
    serialize_textbook_attributes,
    serialize_textbook_content,
    update_class_attributes,
    update_course_attributes,
    update_textbook_attributes,
)


router = APIRouter()


def _teacher_teaches_class_content(conn, *, teacher_id: int, class_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM class_offerings
        WHERE teacher_id = ? AND class_id = ?
        LIMIT 1
        """,
        (int(teacher_id), int(class_id)),
    ).fetchone()
    return row is not None


def _ensure_teacher_can_view_class_content(conn, *, class_id: int, teacher_id: int):
    class_row = ensure_teacher_can_view_class_attributes(conn, class_id, teacher_id)
    if teacher_can_manage_class(conn, teacher_id, class_row) or _teacher_teaches_class_content(
        conn,
        teacher_id=int(teacher_id),
        class_id=int(class_id),
    ):
        return class_row
    raise HTTPException(403, "仅班级维护人、超管或实际任课教师可以查看学生名单")


@router.get("/classes/{class_id}/attributes", response_class=JSONResponse)
async def api_get_class_attributes(class_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        class_row = ensure_teacher_can_view_class_attributes(conn, class_id, int(user["id"]))
        attributes = serialize_class_attributes(conn, class_row, int(user["id"]))
    return {"status": "success", "resource_type": "class", "attributes": attributes}


@router.patch("/classes/{class_id}/attributes", response_class=JSONResponse)
async def api_update_class_attributes(
    class_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        class_row = ensure_teacher_can_manage_class_attributes(conn, class_id, int(user["id"]))
        update_class_attributes(conn, class_row=class_row, teacher_id=int(user["id"]), payload=payload)
        conn.commit()
        refreshed = ensure_teacher_can_view_class_attributes(conn, class_id, int(user["id"]))
        attributes = serialize_class_attributes(conn, refreshed, int(user["id"]))
    return {"status": "success", "message": "班级属性已保存", "attributes": attributes}


@router.get("/classes/{class_id}/students", response_class=JSONResponse)
async def api_get_class_students(class_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        class_row = _ensure_teacher_can_view_class_content(conn, class_id=class_id, teacher_id=int(user["id"]))
        rows = conn.execute(
            """
            SELECT s.id, s.student_id_number, s.name, s.gender, s.email, s.phone,
                   s.wechat, s.qq, s.homepage_url, s.nickname, s.description,
                   COALESCE(s.enrollment_status, 'active') AS enrollment_status,
                   s.enrollment_status_updated_at, s.enrollment_note,
                   s.academic_source, s.academic_student_id, s.academic_class_code,
                   s.academic_class_name, s.academic_college, s.academic_grade,
                   s.academic_major, s.academic_school_status, s.academic_sync_at,
                   s.school_code, s.school_name, s.college, s.department,
                   note.note_text AS shared_teacher_note
            FROM students s
            LEFT JOIN student_shared_teacher_notes note ON note.student_id = s.id
            WHERE s.class_id = ?
            ORDER BY s.student_id_number COLLATE NOCASE, s.name COLLATE NOCASE, s.id
            """,
            (int(class_id),),
        ).fetchall()
        can_manage = teacher_can_manage_class(conn, int(user["id"]), class_row)
    students = []
    for row in rows:
        item = dict(row)
        item["enrollment_status_label"] = student_enrollment_status_label(item["enrollment_status"])
        item["can_edit"] = bool(can_manage)
        students.append(item)
    return {
        "status": "success",
        "resource_type": "class",
        "class_id": int(class_id),
        "can_edit_content": bool(can_manage),
        "students": students,
    }


@router.patch("/students/{student_id}", response_class=JSONResponse)
async def api_update_student_profile(
    student_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    allowed_fields = {
        "student_id_number": 80,
        "name": 80,
        "gender": 20,
        "email": 160,
        "phone": 80,
        "wechat": 80,
        "qq": 80,
        "homepage_url": 300,
        "nickname": 80,
        "description": 500,
    }
    updates = {}
    for field, limit in allowed_fields.items():
        if field in payload:
            updates[field] = _clean_form_text(payload.get(field), limit=limit)
    if not updates:
        raise HTTPException(400, "没有可更新的学生字段")
    if "name" in updates and not updates["name"]:
        raise HTTPException(400, "学生姓名不能为空")
    if "student_id_number" in updates and not updates["student_id_number"]:
        raise HTTPException(400, "学生学号不能为空")

    with get_db_connection() as conn:
        student_row = _ensure_teacher_owned_student(conn, student_id=int(student_id), teacher_id=int(user["id"]))
        assignments = ", ".join(f"{field} = ?" for field in updates)
        conn.execute(
            f"UPDATE students SET {assignments} WHERE id = ?",
            (*updates.values(), int(student_id)),
        )
        conn.commit()
        refreshed = conn.execute("SELECT * FROM students WHERE id = ? LIMIT 1", (int(student_id),)).fetchone()
    return {
        "status": "success",
        "message": f"{student_row['name']} 的学生信息已保存",
        "student": dict(refreshed) if refreshed else {"id": int(student_id), **updates},
    }


@router.patch("/students/{student_id}/status", response_class=JSONResponse)
async def api_patch_class_student_status(
    student_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    raw_status = str(payload.get("enrollment_status") or "").strip().lower()
    if raw_status not in {"active", "suspended", "在读", "休学"}:
        raise HTTPException(status_code=400, detail="学生状态参数不正确")
    normalized_status = normalize_student_enrollment_status(raw_status)
    note = _clean_form_text(payload.get("enrollment_note"), limit=500)

    with get_db_connection() as conn:
        student_row = _ensure_teacher_owned_student(conn, student_id=student_id, teacher_id=int(user["id"]))
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

    return {
        "status": "success",
        "message": f"{student_row['name']} 已设置为{student_enrollment_status_label(normalized_status)}。",
        "student": {
            "id": int(student_id),
            "enrollment_status": normalized_status,
            "enrollment_status_label": student_enrollment_status_label(normalized_status),
            "enrollment_note": note,
        },
    }


@router.get("/courses/{course_id}/attributes", response_class=JSONResponse)
async def api_get_course_attributes(course_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        course_row = ensure_teacher_can_view_course_attributes(conn, course_id, int(user["id"]))
        attributes = serialize_course_attributes(conn, course_row, int(user["id"]))
    return {"status": "success", "resource_type": "course", "attributes": attributes}


@router.patch("/courses/{course_id}/attributes", response_class=JSONResponse)
async def api_update_course_attributes(
    course_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        course_row = ensure_teacher_can_manage_course_attributes(conn, course_id, int(user["id"]))
        update_course_attributes(conn, course_row=course_row, teacher_id=int(user["id"]), payload=payload)
        conn.commit()
        refreshed = ensure_teacher_can_view_course_attributes(conn, course_id, int(user["id"]))
        attributes = serialize_course_attributes(conn, refreshed, int(user["id"]))
    return {"status": "success", "message": "课程属性已保存", "attributes": attributes}


@router.get("/courses/{course_id}/content", response_class=JSONResponse)
async def api_get_course_content(course_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        course_row = ensure_teacher_can_view_course_attributes(conn, course_id, int(user["id"]))
        content = serialize_course_content(conn, course_row, int(user["id"]))
        content["lessons"] = attach_learning_material_briefs(
            conn,
            content["lessons"],
            teacher_id=int(user["id"]),
            markdown_only=True,
        )
    return {"status": "success", "resource_type": "course", "content": content}


@router.put("/courses/{course_id}/content", response_class=JSONResponse)
async def api_update_course_content(
    course_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        course_row = ensure_teacher_can_manage_course_attributes(conn, course_id, int(user["id"]))
        merged_payload = {
            "course_id": int(course_id),
            "name": course_row["name"],
            "description": course_row["description"],
            "sect_name": course_row["sect_name"],
            "department": course_row["department"],
            "credits": course_row["credits"],
            "total_hours": payload.get("total_hours", course_row["total_hours"]),
            "lessons": payload.get("lessons", payload.get("lessons_json")),
        }
        try:
            prepared = _prepare_course_payload(merged_payload, require_lessons=True)
        except CoursePlanningError as exc:
            raise HTTPException(400, str(exc)) from exc
        selected_material_ids = [
            lesson.get("learning_material_id")
            for lesson in prepared["lessons"]
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
        if int(course_row["total_hours"] or 0) != int(prepared["total_hours"] or 0):
            conn.execute(
                "UPDATE courses SET total_hours = ?, updated_at = ? WHERE id = ?",
                (int(prepared["total_hours"] or 0), local_iso(), int(course_id)),
            )
        replace_course_lessons(conn, course_id=int(course_id), lessons=prepared["lessons"])
        conn.commit()
        refreshed = ensure_teacher_can_view_course_attributes(conn, course_id, int(user["id"]))
        content = serialize_course_content(conn, refreshed, int(user["id"]))
    return {"status": "success", "message": "课程内容已保存", "content": content}


@router.get("/textbooks/{textbook_id}/attributes", response_class=JSONResponse)
async def api_get_textbook_attributes(textbook_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        textbook_row = ensure_teacher_can_view_textbook_attributes(conn, textbook_id, int(user["id"]))
        attributes = serialize_textbook_attributes(conn, textbook_row, int(user["id"]))
    return {"status": "success", "resource_type": "textbook", "attributes": attributes}


@router.patch("/textbooks/{textbook_id}/attributes", response_class=JSONResponse)
async def api_update_textbook_attributes(
    textbook_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        textbook_row = ensure_teacher_can_manage_textbook_attributes(conn, textbook_id, int(user["id"]))
        update_textbook_attributes(conn, textbook_row=textbook_row, teacher_id=int(user["id"]), payload=payload)
        conn.commit()
        refreshed = ensure_teacher_can_view_textbook_attributes(conn, textbook_id, int(user["id"]))
        attributes = serialize_textbook_attributes(conn, refreshed, int(user["id"]))
    return {"status": "success", "message": "教材属性已保存", "attributes": attributes}


@router.get("/textbooks/{textbook_id}/content", response_class=JSONResponse)
async def api_get_textbook_content(textbook_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        textbook_row = ensure_teacher_can_view_textbook_attributes(conn, textbook_id, int(user["id"]))
        content = serialize_textbook_content(textbook_row, int(user["id"]), conn)
    return {"status": "success", "resource_type": "textbook", "content": content}


@router.put("/textbooks/{textbook_id}/content", response_class=JSONResponse)
async def api_update_textbook_content(
    textbook_id: int,
    introduction: str = Form(default=""),
    catalog_text: str = Form(default=""),
    remove_attachment: bool = Form(default=False),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    attachment_info = None
    old_attachment_path = ""
    if attachment and str(attachment.filename or "").strip():
        upload_dir = TEXTBOOK_ATTACHMENT_DIR / str(user["id"])
        attachment_info = await save_upload_file(upload_dir, attachment)
        if not attachment_info:
            raise HTTPException(500, "教材附件保存失败")

    with get_db_connection() as conn:
        textbook_row = ensure_teacher_can_manage_textbook_attributes(conn, textbook_id, int(user["id"]))
        old_attachment_path = str(textbook_row["attachment_path"] or "")
        attachment_name = str(textbook_row["attachment_name"] or "")
        attachment_path = old_attachment_path
        attachment_size = int(textbook_row["attachment_size"] or 0)
        attachment_mime_type = str(textbook_row["attachment_mime_type"] or "")

        if remove_attachment and not attachment_info:
            attachment_name = ""
            attachment_path = ""
            attachment_size = 0
            attachment_mime_type = ""
        if attachment_info:
            attachment_name = str(attachment_info["original_filename"] or "")
            attachment_path = str(attachment_info["stored_path"] or "")
            attachment_size = int(Path(attachment_path).stat().st_size) if attachment_path else 0
            attachment_mime_type = str(attachment.content_type or "")

        try:
            conn.execute(
                """
                UPDATE textbooks
                SET introduction = ?,
                    catalog_text = ?,
                    attachment_name = ?,
                    attachment_path = ?,
                    attachment_size = ?,
                    attachment_mime_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(introduction or "").strip(),
                    str(catalog_text or "").strip(),
                    attachment_name,
                    attachment_path,
                    attachment_size,
                    attachment_mime_type,
                    int(textbook_id),
                ),
            )
            conn.commit()
        except Exception:
            if attachment_info:
                _remove_file_if_exists(attachment_info.get("stored_path"))
            raise
        refreshed = ensure_teacher_can_view_textbook_attributes(conn, textbook_id, int(user["id"]))
        content = serialize_textbook_content(refreshed, int(user["id"]), conn)

    if attachment_info and old_attachment_path and old_attachment_path != attachment_info.get("stored_path"):
        _remove_file_if_exists(old_attachment_path)
    elif remove_attachment and old_attachment_path and not attachment_info:
        _remove_file_if_exists(old_attachment_path)

    return {"status": "success", "message": "教材内容已保存", "content": content}


__all__ = [name for name in globals() if not name.startswith("__")]
