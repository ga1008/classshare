from .common import *
from ...services.base_resource_modes_service import build_textbook_delete_blockers, raise_if_delete_blocked


router = APIRouter()


@router.post("/semesters/save", response_class=JSONResponse)
async def api_save_semester(
    background_tasks: BackgroundTasks,
    semester_id: str = Form(default=""),
    name: str = Form(default=""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    user: dict = Depends(get_current_teacher),
):
    semester_id_value = int(str(semester_id).strip()) if str(semester_id).strip() else None
    try:
        start_date_value = parse_date_input(start_date, "学期开始时间")
        end_date_value = parse_date_input(end_date, "学期结束时间")
        if not start_date_value or not end_date_value:
            raise HTTPException(400, "请完整填写学期起止日期")
        week_count = compute_semester_week_count(start_date_value, end_date_value)
        semester_name = str(name or "").strip() or infer_semester_name(start_date_value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        try:
            saved_semester_id: int | None = semester_id_value
            teacher_scope = load_teacher_org_scope(conn, int(user["id"]))
            should_sync_calendar = False
            if semester_id_value:
                _ensure_teacher_can_manage_semester(
                    conn,
                    semester_id=semester_id_value,
                    teacher_id=user["id"],
                )
                conn.execute(
                    """
                    UPDATE academic_semesters
                    SET name = ?,
                        start_date = ?,
                        end_date = ?,
                        week_count = ?,
                        school_code = ?,
                        school_name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        week_count,
                        teacher_scope["school_code"],
                        teacher_scope["school_name"],
                        semester_id_value,
                    ),
                )
                action_text = "更新"
                should_sync_calendar = True
            else:
                existing_row = conn.execute(
                    """
                    SELECT id, teacher_id, calendar_sync_status
                    FROM academic_semesters
                    WHERE lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
                      AND (
                          lower(TRIM(name)) = lower(TRIM(?))
                          OR (start_date = ? AND end_date = ?)
                      )
                    ORDER BY
                        CASE WHEN lower(TRIM(name)) = lower(TRIM(?)) THEN 0 ELSE 1 END,
                        updated_at DESC,
                        id DESC
                    LIMIT 1
                    """,
                    (
                        teacher_scope["school_code"],
                        teacher_scope["school_code"],
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        semester_name,
                    ),
                ).fetchone()
                if existing_row:
                    saved_semester_id = int(existing_row["id"])
                    action_text = "复用"
                else:
                    cursor = conn.execute(
                    """
                    INSERT INTO academic_semesters (
                        teacher_id, school_code, school_name, name, start_date, end_date, week_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        teacher_scope["school_code"],
                        teacher_scope["school_name"],
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        week_count,
                    ),
                    )
                    saved_semester_id = int(cursor.lastrowid)
                    action_text = "创建"
                    should_sync_calendar = True
            if saved_semester_id and should_sync_calendar:
                mark_semester_calendar_sync_queued(
                    conn,
                    teacher_id=int(user["id"]),
                    semester_id=int(saved_semester_id),
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, f"保存失败，学期名称“{semester_name}”已存在") from exc

    if saved_semester_id and should_sync_calendar:
        background_tasks.add_task(
            sync_semester_calendar_background,
            int(user["id"]),
            int(saved_semester_id),
        )

    return {
        "status": "success",
        "message": (
            f"学期已{action_text}：{semester_name}，校历同步已开始。"
            if should_sync_calendar
            else f"已复用同校学期：{semester_name}。"
        ),
        "semester_id": saved_semester_id,
        "calendar_sync_status": "pending" if should_sync_calendar else "",
    }


@router.post("/semesters/{semester_id}/calendar/sync", response_class=JSONResponse)
async def api_sync_semester_calendar(
    semester_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_can_manage_semester(
            conn,
            semester_id=semester_id,
            teacher_id=user["id"],
        )
        mark_semester_calendar_sync_queued(
            conn,
            teacher_id=int(user["id"]),
            semester_id=int(semester_id),
        )
        conn.commit()

    background_tasks.add_task(sync_semester_calendar_background, int(user["id"]), int(semester_id))
    return {
        "status": "success",
        "message": "校历同步已开始，系统会自动拉取教务系统并核对广西节假日/补课日期。",
        "semester_id": int(semester_id),
    }


@router.post("/semesters/calendar/sync-current", response_class=JSONResponse)
async def api_sync_current_semester_from_academic_system(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_teacher),
):
    result = await prepare_current_semester_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        detail = result.get("message") or "未能从教务系统同步当前学期。"
        source_message = str(result.get("source_message") or "").strip()
        if source_message:
            detail = f"{detail}（{source_message}）"
        raise HTTPException(502, detail)

    semester_id = int(result["semester_id"])
    if result.get("should_sync_calendar", True):
        background_tasks.add_task(sync_semester_calendar_background, int(user["id"]), semester_id)
    return {
        "status": "success",
        "message": result.get("message") or "已从教务系统同步本学期，校历处理已开始。",
        "semester_id": semester_id,
        "action": result.get("action") or "",
        "calendar_sync_status": "pending" if result.get("should_sync_calendar", True) else "",
    }


@router.delete("/semesters/{semester_id}", response_class=JSONResponse)
async def api_delete_semester(semester_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        semester_row = _ensure_teacher_can_manage_semester(
            conn,
            semester_id=semester_id,
            teacher_id=user["id"],
        )
        offering_count = conn.execute(
            "SELECT COUNT(*) AS count FROM class_offerings WHERE semester_id = ?",
            (semester_id,),
        ).fetchone()
        linked_count = int((offering_count["count"] if offering_count else 0) or 0)
        if linked_count > 0:
            raise HTTPException(
                400,
                f"该学期已被 {linked_count} 个课堂使用，请先调整课堂的学期绑定后再删除",
            )

        conn.execute(
            "DELETE FROM academic_semesters WHERE id = ?",
            (semester_id,),
        )
        conn.commit()

    return {
        "status": "success",
        "message": f"学期“{semester_row['name']}”已删除",
    }


@router.post("/textbooks/save", response_class=JSONResponse)
async def api_save_textbook(
    textbook_id: str = Form(default=""),
    title: str = Form(...),
    authors_json: str = Form(default="[]"),
    publisher: str = Form(default=""),
    publication_date: str = Form(default=""),
    introduction: str = Form(default=""),
    catalog_text: str = Form(default=""),
    tags_json: str = Form(default="[]"),
    remove_attachment: bool = Form(default=False),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    normalized_title = str(title or "").strip()
    textbook_id_value = int(str(textbook_id).strip()) if str(textbook_id).strip() else None
    if not normalized_title:
        raise HTTPException(400, "教材名称不能为空")

    try:
        authors = parse_json_list_field(
            authors_json,
            field_name="作者",
            max_items=12,
            max_length=30,
        )
        tags = parse_json_list_field(
            tags_json,
            field_name="标签",
            max_items=20,
            max_length=12,
        )
        publication_date_value = parse_date_input(publication_date, "出版日期")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    attachment_info = None
    old_attachment_path = ""
    old_attachment_name = ""
    old_attachment_size = 0
    old_attachment_mime_type = ""

    if attachment and str(attachment.filename or "").strip():
        upload_dir = TEXTBOOK_ATTACHMENT_DIR / str(user["id"])
        attachment_info = await save_upload_file(upload_dir, attachment)
        if not attachment_info:
            raise HTTPException(500, "教材附件保存失败")

    with get_db_connection() as conn:
        try:
            if textbook_id_value:
                existing_row = _ensure_teacher_can_manage_textbook(
                    conn,
                    textbook_id=textbook_id_value,
                    teacher_id=user["id"],
                )
                old_attachment_path = str(existing_row["attachment_path"] or "")
                old_attachment_name = str(existing_row["attachment_name"] or "")
                old_attachment_size = int(existing_row["attachment_size"] or 0)
                old_attachment_mime_type = str(existing_row["attachment_mime_type"] or "")

                attachment_name = old_attachment_name
                attachment_path = old_attachment_path
                attachment_size = old_attachment_size
                attachment_mime_type = old_attachment_mime_type

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

                conn.execute(
                    """
                    UPDATE textbooks
                    SET title = ?,
                        authors_json = ?,
                        publisher = ?,
                        publication_date = ?,
                        introduction = ?,
                        catalog_text = ?,
                        attachment_name = ?,
                        attachment_path = ?,
                        attachment_size = ?,
                        attachment_mime_type = ?,
                        tags_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        normalized_title,
                        json.dumps(authors, ensure_ascii=False),
                        str(publisher or "").strip(),
                        publication_date_value.isoformat() if publication_date_value else "",
                        str(introduction or "").strip(),
                        str(catalog_text or "").strip(),
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        json.dumps(tags, ensure_ascii=False),
                        textbook_id_value,
                    ),
                )
                persisted_textbook_id = textbook_id_value
                action_text = "更新"
            else:
                attachment_name = str(attachment_info["original_filename"] or "") if attachment_info else ""
                attachment_path = str(attachment_info["stored_path"] or "") if attachment_info else ""
                attachment_size = int(Path(attachment_path).stat().st_size) if attachment_path else 0
                attachment_mime_type = str(attachment.content_type or "") if attachment_info else ""
                cursor = conn.execute(
                    """
                    INSERT INTO textbooks (
                        teacher_id,
                        title,
                        authors_json,
                        publisher,
                        publication_date,
                        introduction,
                        catalog_text,
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        tags_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        normalized_title,
                        json.dumps(authors, ensure_ascii=False),
                        str(publisher or "").strip(),
                        publication_date_value.isoformat() if publication_date_value else "",
                        str(introduction or "").strip(),
                        str(catalog_text or "").strip(),
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        json.dumps(tags, ensure_ascii=False),
                    ),
                )
                persisted_textbook_id = int(cursor.lastrowid)
                action_text = "创建"

            conn.commit()
        except Exception:
            if attachment_info:
                _remove_file_if_exists(attachment_info.get("stored_path"))
            raise

    if attachment_info and old_attachment_path and old_attachment_path != attachment_info.get("stored_path"):
        _remove_file_if_exists(old_attachment_path)
    elif remove_attachment and old_attachment_path and not attachment_info:
        _remove_file_if_exists(old_attachment_path)

    return {
        "status": "success",
        "message": f"教材已{action_text}：{normalized_title}",
        "textbook_id": persisted_textbook_id,
    }


@router.delete("/textbooks/{textbook_id}", response_class=JSONResponse)
async def api_delete_textbook(textbook_id: int, user: dict = Depends(get_current_teacher)):
    attachment_path = ""
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_can_manage_textbook(conn, textbook_id=textbook_id, teacher_id=user["id"])
        raise_if_delete_blocked(
            f"教材“{textbook_row['title']}”",
            build_textbook_delete_blockers(conn, int(textbook_id)),
        )

        attachment_path = str(textbook_row["attachment_path"] or "")
        conn.execute(
            "DELETE FROM textbooks WHERE id = ?",
            (textbook_id,),
        )
        conn.commit()

    _remove_file_if_exists(attachment_path)
    return {
        "status": "success",
        "message": f"教材“{textbook_row['title']}”已删除",
    }


@router.get("/textbooks/{textbook_id}/attachment")
async def api_download_textbook_attachment(textbook_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=user["id"])

    attachment_path = str(textbook_row["attachment_path"] or "").strip()
    if not attachment_path:
        raise HTTPException(404, "该教材没有附件")

    file_path = resolve_migrated_file_path(
        attachment_path,
        active_root=TEXTBOOK_ATTACHMENT_DIR,
        legacy_roots=TEXTBOOK_ATTACHMENT_LEGACY_DIRS,
        markers=("storage/textbook_attachments", "files/textbook_attachments", "textbook_attachments"),
    )
    if not file_path:
        raise HTTPException(404, "教材附件不存在或已丢失")

    media_type = str(textbook_row["attachment_mime_type"] or "").strip() or None
    return FileResponse(
        path=file_path,
        filename=str(textbook_row["attachment_name"] or file_path.name),
        media_type=media_type,
    )


@router.post("/textbooks/ai-format-intro-catalog", response_class=JSONResponse)
async def api_ai_format_textbook_intro_catalog(
    title: str = Form(default=""),
    publisher: str = Form(default=""),
    authors_json: str = Form(default="[]"),
    publication_date: str = Form(default=""),
    tags_json: str = Form(default="[]"),
    raw_introduction: str = Form(default=""),
    raw_catalog: str = Form(default=""),
    custom_requirements: str = Form(default=""),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    """Call AI (thinking model) to format and organize textbook introduction and catalog."""
    has_intro = bool(str(raw_introduction or "").strip())
    has_catalog = bool(str(raw_catalog or "").strip())
    if not has_intro and not has_catalog:
        raise HTTPException(400, "请至少填写教材简介或教材目录")

    # Parse basic info for context
    normalized_title = str(title or "").strip() or "未命名教材"
    try:
        authors = parse_json_list_field(
            authors_json, field_name="作者", max_items=12, max_length=30,
        )
        tags = parse_json_list_field(
            tags_json, field_name="标签", max_items=20, max_length=12,
        )
    except ValueError:
        authors = []
        tags = []

    publication_year = ""
    if publication_date:
        try:
            publication_year = str(parse_date_input(publication_date).year)
        except Exception:
            pass

    # Extract attachment text if provided
    attachment_text = ""
    if attachment and str(attachment.filename or "").strip():
        try:
            contents = await attachment.read()
            if contents:
                filename = str(attachment.filename or "").strip()
                ext = Path(filename).suffix.lower()
                text_exts = {
                    ".txt", ".md", ".py", ".js", ".ts", ".html", ".htm", ".css",
                    ".json", ".xml", ".yaml", ".yml", ".csv", ".log",
                }
                if ext in text_exts:
                    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
                        try:
                            attachment_text = contents.decode(enc)
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    if not attachment_text:
                        attachment_text = contents.decode("utf-8", errors="replace")
                elif ext in {".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt", ".pdf"}:
                    try:
                        from ai_assistant_doc_extract import extract_document_text
                        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                            tmp.write(contents)
                            tmp_path = tmp.name
                        try:
                            result = extract_document_text(Path(tmp_path), ext)
                            attachment_text = str(result.text or "") if result else ""
                        finally:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                    except Exception as exc:
                        print(f"[TEXTBOOK_AI] 附件文本提取失败 ({filename}): {exc}")
        except Exception as exc:
            print(f"[TEXTBOOK_AI] 附件读取失败: {exc}")

    # Build context block
    context_parts = [f"教材名称：{normalized_title}"]
    if publisher:
        context_parts.append(f"出版社：{publisher}")
    if authors:
        context_parts.append(f"作者：{'、'.join(authors)}")
    if publication_year:
        context_parts.append(f"出版年份：{publication_year}")
    if tags:
        context_parts.append(f"标签：{'、'.join(tags)}")
    context_block = "\n".join(context_parts)

    # Build content block
    content_parts = []
    if has_intro:
        content_parts.append(f"【原始简介】\n{raw_introduction.strip()}")
    if has_catalog:
        content_parts.append(f"【原始目录】\n{raw_catalog.strip()}")
    if attachment_text:
        content_parts.append(f"【附件内容】\n{attachment_text}")
    content_block = "\n\n".join(content_parts)

    system_prompt = (
        "你是一名高校教材内容整理助手，负责将教师提供的教材简介和目录重新规整化。"
        "你的输出必须是合法的 JSON 对象，包含两个键：\n"
        "- \"introduction\"：教材简介文本（字符串）\n"
        "- \"catalog_text\"：教材目录文本（字符串）\n\n"
        "工作要求：\n"
        "1. 目录整理：\n"
        "   - 必须完整保留原始目录中的所有章节和小节，不得遗漏任何一个条目。\n"
        "   - 如果原始目录格式混乱（如编号不一致、层级不清），请统一为「第X章」+「X.X 小节」的清晰层级格式。\n"
        "   - 保持原始缩进或用编号体现层级关系，使目录结构一目了然。\n"
        "   - 如果原始目录有重复或明显错误的编号，请自动修正。\n"
        "2. 简介改写：\n"
        "   - 将简介改写为适合学生阅读的课程导引式文本，语气亲切自然。\n"
        "   - 概括教材的核心内容、适用对象和学习目标。\n"
        "   - 突出本教材的关键知识点和教学特色，方便后续课堂AI助手理解本门课的基本要点。\n"
        "   - 如果原始简介信息不足，可以结合目录内容进行合理的补充概括，但不要编造不存在的内容。\n"
        "3. 如果提供了附件内容，请结合附件中的信息来完善简介和目录。\n"
        "4. 只输出 JSON 对象，不要输出任何额外的解释或 Markdown 代码块标记。"
    )

    user_message = f"以下是教材的基本信息及需要整理的内容：\n\n{context_block}\n\n{content_block}"
    if custom_requirements and custom_requirements.strip():
        user_message += f"\n\n【教师的自定义要求】\n{custom_requirements.strip()}"

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
    except json.JSONDecodeError:
        raise HTTPException(500, "AI 返回的内容格式不正确，请重试")

    formatted_intro = str(parsed.get("introduction") or "").strip()
    formatted_catalog = str(parsed.get("catalog_text") or "").strip()

    if not formatted_intro and not formatted_catalog:
        raise HTTPException(500, "AI 返回的内容为空，请重试")

    return {
        "status": "success",
        "introduction": formatted_intro,
        "catalog_text": formatted_catalog,
    }
