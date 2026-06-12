from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *

from ...db.connection import execute_insert_returning_id, get_configured_db_engine
from ...services.material_mastery_check_service import build_material_mastery_check_payload


router = APIRouter()


def _insert_material_ai_import_record(
    conn,
    *,
    teacher_id: int,
    parent_material_id: int | None,
    document_group: str,
    document_type: str,
    document_type_label: str,
    source_file_name: str,
    source_file_hash: str,
    source_file_size: int,
    source_mime_type: str,
    metadata_json: str,
    now: str,
    engine: str | None = None,
):
    db_engine = (engine or get_configured_db_engine()).strip().lower()
    if db_engine not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported material AI import database engine: {db_engine}")
    insert_sql = """
        INSERT INTO material_ai_import_records
        (teacher_id, package_material_id, source_material_id, parsed_material_id,
         parent_material_id, document_group, document_type, document_type_label,
         parse_status, parse_mode, extraction_method, source_file_name,
         source_file_hash, source_file_size, source_mime_type, metadata_json, content_markdown,
         parsed_payload_json, export_payload_json, warnings_json, content_quality_status,
         content_quality_json, error_message, created_at, updated_at, completed_at)
        VALUES (?, NULL, NULL, NULL, ?, ?, ?, ?, 'queued', 'ai', '', ?, ?, ?, ?, ?, '',
                NULL, NULL, '[]', 'unchecked', '{}', '', ?, ?, NULL)
    """
    params = (
        int(teacher_id),
        parent_material_id,
        document_group,
        document_type,
        document_type_label,
        source_file_name,
        source_file_hash,
        int(source_file_size),
        source_mime_type,
        metadata_json,
        now,
        now,
    )
    if db_engine == "postgres":
        cursor = conn.execute(f"{insert_sql} RETURNING *", params)
        return cursor.fetchone()
    record_id = execute_insert_returning_id(conn, insert_sql, params, engine=db_engine)
    return conn.execute(
        "SELECT * FROM material_ai_import_records WHERE id = ?",
        (record_id,),
    ).fetchone()


@router.get(
    "/api/materials/ai-generation/candidates",
    response_class=JSONResponse,
    response_model=MaterialAiGenerationCandidatesResponse,
    response_model_exclude_unset=True,
)
async def list_material_ai_generation_candidates(
    query: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(get_current_teacher),
):
    normalized_query = _normalize_material_keyword(query)
    with get_db_connection() as conn:
        rows = _list_material_rows_for_parent(
            conn,
            int(user["id"]),
            None,
            keyword=normalized_query,
            sort_by="updated_at",
            sort_order="desc",
        )[: int(limit)]
        items = [
            _decorate_learning_document_item(item)
            for item in _serialize_material_items(conn, rows, user=user)
        ]
    return {"status": "success", "items": items}


@router.get(
    "/api/materials/ai-generation/assignments",
    response_class=JSONResponse,
    response_model=MaterialAiGenerationCandidatesResponse,
    response_model_exclude_unset=True,
)
async def list_material_ai_generation_assignment_candidates(
    query: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(get_current_teacher),
):
    normalized_query = _normalize_material_keyword(query)
    conditions = ["(o.teacher_id = ? OR co.created_by_teacher_id = ?)"]
    params: list[object] = [int(user["id"]), int(user["id"])]
    if normalized_query:
        keyword_pattern = f"%{normalized_query}%"
        conditions.append(
            """
            (
                a.title LIKE ? COLLATE NOCASE
                OR a.requirements_md LIKE ? COLLATE NOCASE
                OR co.name LIKE ? COLLATE NOCASE
                OR cl.name LIKE ? COLLATE NOCASE
            )
            """
        )
        params.extend([keyword_pattern, keyword_pattern, keyword_pattern, keyword_pattern])
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT a.id, a.title, a.status, a.created_at, a.requirements_md,
                   a.exam_paper_id, a.class_offering_id,
                   co.name AS course_name,
                   cl.name AS class_name,
                   o.semester,
                   ep.questions_json AS exam_questions_json
            FROM assignments a
            JOIN courses co ON co.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            LEFT JOIN classes cl ON cl.id = o.class_id
            LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
            WHERE {' AND '.join(conditions)}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            params + [int(limit)],
        ).fetchall()
    items = []
    for row in rows:
        row_dict = dict(row)
        questions = _iter_exam_question_texts(row_dict.get("exam_questions_json")) if row_dict.get("exam_questions_json") else []
        if not questions:
            questions = _extract_assignment_question_lines(row_dict.get("requirements_md") or "")
        excerpt = "；".join(questions[:2])
        items.append(
            {
                "id": int(row_dict["id"]),
                "title": row_dict.get("title") or "",
                "status": row_dict.get("status") or "",
                "course_name": row_dict.get("course_name") or "",
                "class_name": row_dict.get("class_name") or "",
                "semester": row_dict.get("semester") or "",
                "created_at": row_dict.get("created_at") or "",
                "question_count": len(questions),
                "question_excerpt": excerpt[:220],
            }
        )
    return {"status": "success", "items": items}


@router.post("/api/materials/ai-generate", response_class=JSONResponse)
async def ai_generate_material_from_context(
    prompt: str = Form(default=""),
    document_group: str = Form(default="teaching_material"),
    document_type: str = Form(default="teaching_document"),
    parent_id: int | None = Form(default=None),
    existing_material_ids: str = Form(default="[]"),
    assignment_ids: str = Form(default="[]"),
    new_files: list[UploadFile] | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    material_ids = _parse_material_ai_id_list(existing_material_ids)
    selected_assignment_ids = _parse_material_ai_id_list(assignment_ids)
    upload_files = list(new_files or [])
    total_attachments = len(material_ids) + len(selected_assignment_ids) + len(upload_files)
    if total_attachments > MATERIAL_AI_CONTEXT_MAX_ATTACHMENTS:
        raise HTTPException(400, f"关联附件最多支持 {MATERIAL_AI_CONTEXT_MAX_ATTACHMENTS} 份")
    if not prompt.strip() and total_attachments <= 0:
        raise HTTPException(400, "请填写提示语，或至少选择一份关联附件")

    material_context_specs: list[tuple[dict, list[dict]]] = []
    assignment_rows: list[dict] = []
    parent_context: dict[str, Any] | None = None
    with get_db_connection() as conn:
        if parent_id is not None:
            parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if parent["node_type"] != "folder":
                raise HTTPException(400, "只能生成到文件夹中")
            parent_context = {
                "id": int(parent["id"]),
                "name": parent["name"],
                "material_path": parent["material_path"],
            }
        for material_id in material_ids:
            material = dict(ensure_user_material_access(conn, material_id, user))
            material_context_specs.append((material, _collect_material_context_rows(conn, material)))

        if selected_assignment_ids:
            placeholders = ",".join("?" for _ in selected_assignment_ids)
            rows = conn.execute(
                f"""
                SELECT a.id, a.title, a.status, a.created_at, a.requirements_md,
                       a.exam_paper_id, a.class_offering_id,
                       co.name AS course_name,
                       cl.name AS class_name,
                       o.semester,
                       ep.questions_json AS exam_questions_json
                FROM assignments a
                JOIN courses co ON co.id = a.course_id
                LEFT JOIN class_offerings o ON o.id = a.class_offering_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
                WHERE a.id IN ({placeholders})
                  AND (o.teacher_id = ? OR co.created_by_teacher_id = ?)
                ORDER BY a.created_at DESC, a.id DESC
                """,
                selected_assignment_ids + [int(user["id"]), int(user["id"])],
            ).fetchall()
            found_ids = {int(row["id"]) for row in rows}
            missing_ids = [item_id for item_id in selected_assignment_ids if item_id not in found_ids]
            if missing_ids:
                raise HTTPException(403, "存在无权使用的作业题目")
            assignment_rows = [dict(row) for row in rows]

    attachments: list[dict[str, Any]] = []
    for file in upload_files:
        attachments.append(await _build_uploaded_context_attachment(file))
    for material, context_rows in material_context_specs:
        attachments.append(await _build_material_context_attachment(material, context_rows))
    for assignment_row in assignment_rows:
        attachments.append(_build_assignment_context_attachment(assignment_row))
    attachments = _limit_ai_context_attachments(attachments)

    if str(document_group or "").strip() == "final_material" and str(document_type or "").strip() in FINAL_MATERIAL_TYPES:
        return await _generate_final_material_from_manage_context(
            document_type=str(document_type or "").strip(),
            prompt=prompt,
            parent_id=parent_id,
            parent_context=parent_context,
            attachments=attachments,
            user=user,
        )

    file_texts = [
        {"name": item.get("title") or f"attachment-{index + 1}", "content": item.get("content") or ""}
        for index, item in enumerate(attachments)
        if str(item.get("content") or "").strip()
    ]
    raw_result = await _call_ai_chat(
        _build_ai_material_generation_system_prompt(),
        _build_ai_material_generation_user_prompt(
            prompt=prompt,
            parent_context=parent_context,
            attachments=attachments,
        ),
        capability="thinking",
        response_format="json",
        file_texts=file_texts,
        task_type="material_ai_generate",
        task_label="materials:ai-generate",
        timeout=300.0,
    )
    fallback_title = prompt.strip().splitlines()[0][:42] if prompt.strip() else "AI生成材料"
    parse_result = _build_generic_material_parse_result(
        raw_result=raw_result,
        fallback_title=fallback_title,
        attachments=attachments,
        ai_used=True,
    )
    markdown_content = build_import_readme(
        result=parse_result,
        original_name=f"{parse_result.metadata.get('title') or fallback_title}.md",
    )
    parse_payload_json = json.dumps(_build_material_ai_parse_payload(parse_result), ensure_ascii=False)
    material = await _create_generated_markdown_material(
        user=user,
        parent_id=parent_id,
        title=str(parse_result.metadata.get("title") or fallback_title),
        markdown_content=markdown_content,
        parse_payload_json=parse_payload_json,
        name_prefix="AI生成",
    )
    return {
        "status": "success",
        "message": "AI 已生成材料并保存到材料库",
        "material": material,
        "viewer_url": f"/materials/view/{material['id']}",
        "attachment_count": len(attachments),
    }


@router.post("/api/materials/ai-import", response_class=JSONResponse)
async def ai_import_material(
    file: UploadFile = File(...),
    document_group: str = Form(...),
    document_type: str = Form(...),
    parent_id: int | None = Form(default=None),
    user: dict = Depends(get_current_teacher),
):
    original_name = _normalize_uploaded_filename(file.filename)
    type_meta = resolve_material_ai_import_type(document_group, document_type)

    if parent_id is not None:
        with get_db_connection() as conn:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能导入到文件夹中")

    payload_bytes = await file.read()
    if not payload_bytes:
        raise HTTPException(400, "请选择非空材料文件")

    file_hash = hashlib.sha256(payload_bytes).hexdigest()
    stored_path = await _write_material_file(file_hash, payload_bytes)
    source_file_size = len(payload_bytes)
    source_mime_type = str(file.content_type or "").strip()
    initial_metadata = {
        "source_file_hash": file_hash,
        "source_file_size": source_file_size,
        "source_mime_type": source_mime_type,
        "source_filename": original_name,
        "document_group": type_meta["group_label"],
        "document_type": type_meta["label"],
        "parent_material_id": parent_id,
        "storage_path": str(stored_path),
    }

    with get_db_connection() as conn:
        _recover_stale_material_ai_import_tasks(conn)
        active_count = conn.execute(
            """
            SELECT COUNT(*) AS active_count
            FROM material_ai_import_records
            WHERE parse_status IN ('queued', 'running')
            """,
        ).fetchone()["active_count"]
        if int(active_count or 0) >= MATERIAL_AI_IMPORT_QUEUE_MAX_PENDING:
            raise HTTPException(429, "当前 AI 材料解析任务较多，请稍后再试。")

        base_parent = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能导入到文件夹中")
        now = datetime.now().isoformat()
        row = _insert_material_ai_import_record(
            conn,
            teacher_id=int(user["id"]),
            parent_material_id=base_parent["id"] if base_parent else None,
            document_group=type_meta["group_key"],
            document_type=type_meta["key"],
            document_type_label=type_meta["label"],
            source_file_name=original_name,
            source_file_hash=file_hash,
            source_file_size=source_file_size,
            source_mime_type=source_mime_type,
            metadata_json=json.dumps(initial_metadata, ensure_ascii=False),
            now=now,
        )
        import_record_id = int(row["id"])
        conn.commit()
        task = _serialize_material_ai_import_task(conn, row, user)

    if not _enqueue_material_ai_import_task(import_record_id):
        _mark_material_ai_import_failed(
            import_record_id,
            "failed",
            "当前 AI 材料解析队列已满，请稍后重新发起。",
        )
        raise HTTPException(429, "当前 AI 材料解析队列已满，请稍后重新发起。")

    return {
        "status": "queued",
        "message": f"《{original_name}》已加入 AI 解析队列，完成后会自动出现在当前材料列表。",
        "import_record_id": import_record_id,
        "task": task,
    }


@router.get(
    "/api/materials/ai-import-records/active",
    response_class=JSONResponse,
    response_model=MaterialAiImportActiveResponse,
    response_model_exclude_unset=True,
)
async def list_ai_import_records(
    parent_id: int | None = Query(default=None),
    recent_minutes: int = Query(default=MATERIAL_AI_IMPORT_RECENT_MINUTES, ge=1, le=1440),
    user: dict = Depends(get_current_teacher),
):
    cutoff = (datetime.now() - timedelta(minutes=max(1, recent_minutes))).isoformat()
    params: list[Any] = [user["id"]]
    parent_clause = "parent_material_id IS NULL"
    if parent_id is not None:
        with get_db_connection() as conn:
            parent_row = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if parent_row["node_type"] != "folder":
                raise HTTPException(400, "只能查看文件夹下的解析任务")
        parent_clause = "parent_material_id = ?"
        params.append(int(parent_id))

    params.append(cutoff)
    with get_db_connection() as conn:
        _recover_stale_material_ai_import_tasks(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM material_ai_import_records
            WHERE teacher_id = ?
              AND {parent_clause}
              AND (
                    parse_status IN ('queued', 'running')
                    OR updated_at >= ?
              )
            ORDER BY
                CASE WHEN parse_status IN ('queued', 'running') THEN 0 ELSE 1 END,
                updated_at DESC,
                id DESC
            LIMIT 20
            """,
            params,
        ).fetchall()
        conn.commit()
        tasks = [_serialize_material_ai_import_task(conn, row, user) for row in rows]

    for task in tasks:
        if task["parse_status"] == "queued":
            _enqueue_material_ai_import_task(int(task["id"]))

    return {
        "status": "success",
        "tasks": tasks,
        "poll_interval_ms": 3500,
    }


@router.get(
    "/api/materials/ai-import-records/{record_id}/status",
    response_class=JSONResponse,
    response_model=MaterialAiImportStatusResponse,
    response_model_exclude_unset=True,
)
async def get_ai_import_record_status(
    record_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _recover_stale_material_ai_import_tasks(conn)
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE id = ? AND teacher_id = ?
            """,
            (int(record_id), user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到该 AI 解析任务")
        conn.commit()
        task = _serialize_material_ai_import_task(conn, row, user)

    if task["parse_status"] == "queued":
        _enqueue_material_ai_import_task(int(task["id"]))

    return {
        "status": "success",
        "task": task,
    }


@router.get(
    "/api/materials/{material_id}/ai-import/preview",
    response_class=JSONResponse,
    response_model=MaterialAiImportPreviewResponse,
    response_model_exclude_unset=True,
)
async def preview_ai_import_material(
    material_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_teacher_material_owner(conn, material_id, user["id"])
        record = _find_material_ai_import_record(conn, material_id, user["id"], completed_only=True)
        if not record:
            raise HTTPException(404, "该材料没有可预览的期末材料解析结果")
        task = _serialize_material_ai_import_task(conn, record, user)
        preview = _build_ai_import_preview(record)
    return {
        "status": "success",
        "task": task,
        "preview": preview,
    }


@router.post("/api/materials/{material_id}/ai-import/optimize", response_class=JSONResponse)
async def optimize_ai_import_material(
    material_id: int,
    payload: MaterialAiImportOptimizeRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_teacher_material_owner(conn, material_id, user["id"])
        record = _find_material_ai_import_record(conn, material_id, user["id"], completed_only=True)
        if not record:
            raise HTTPException(404, "该材料没有可优化的 AI 解析结果")
        if str(record["document_type"] or "") not in FINAL_MATERIAL_TYPES:
            raise HTTPException(400, "当前仅支持对期末材料执行结构化优化")
        classroom_context: dict[str, Any] = {}
        if payload.class_offering_id:
            classroom_context = _load_final_material_classroom_context(conn, int(payload.class_offering_id), user)
        current_payload = _build_ai_import_payload_from_record(record)

    system_prompt = _build_final_material_ai_system_prompt(str(record["document_type"]))
    user_prompt = "\n\n".join(
        [
            "请优化这份已经解析入库的期末材料，修正字段缺漏、结构层次和导出字段，但不要删除原有关键内容。",
            f"教师优化要求：\n{payload.prompt.strip() or '请提升结构化完整性、导出可用性和表述规范性。'}",
            f"课堂关联信息：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2) if classroom_context else '未提供'}",
            f"当前材料 JSON：\n{json.dumps(current_payload, ensure_ascii=False, indent=2)[:30000]}",
        ]
    )
    raw_result = await _call_ai_chat(
        system_prompt,
        user_prompt,
        capability="thinking",
        response_format="json",
        task_type="material_final_optimize",
        task_label="materials:final-optimize",
        timeout=240.0,
    )
    extraction = MaterialExtraction(
        text=str(raw_result.get("content_markdown") or current_payload.get("content_markdown") or ""),
        method="ai_optimize",
        source_kind="ai_generated",
        warnings=[],
        quality={"usable": True},
    )
    type_meta = resolve_material_ai_import_type("final_material", str(record["document_type"]))
    parse_result = normalize_ai_parse_result(
        raw_result,
        original_name=record["source_file_name"] or type_meta["label"],
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=[],
        ai_used=True,
    )
    if classroom_context:
        parse_result.export_payload = normalize_final_material_payload(
            document_type=parse_result.document_type,
            metadata=parse_result.metadata,
            content_markdown=parse_result.content_markdown,
            tables=parse_result.tables,
            export_payload=parse_result.export_payload,
            classroom_context=classroom_context,
        )
        parse_result.metadata.update(parse_result.export_payload.get("fields") or {})
        parse_result.parsed_payload["metadata"] = parse_result.metadata
        parse_result.parsed_payload["export_payload"] = parse_result.export_payload

    task = await _persist_final_material_record_update(int(record["id"]), record, parse_result, user)
    with get_db_connection() as conn:
        refreshed_record = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ? AND teacher_id = ?",
            (int(record["id"]), user["id"]),
        ).fetchone()
        preview = _build_ai_import_preview(refreshed_record) if refreshed_record else None
    return {
        "status": "success",
        "message": "期末材料已优化并更新导出字段",
        "task": task,
        "preview": preview,
    }


@router.post("/api/materials/{material_id}/ai-parse", response_class=JSONResponse)
async def ai_parse_material(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if material["node_type"] != "file" or material["ai_capability"] != "markdown":
            raise HTTPException(400, "当前仅支持对 Markdown 材料执行 AI 解析")
        conn.execute(
            "UPDATE course_materials SET ai_parse_status = 'running', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), material_id),
        )
        conn.commit()

    try:
        markdown_content = await _load_material_markdown(material, prefer_optimized=False)
        system_prompt = (
            "你是一名教学材料分析助手。"
            "请严格输出 JSON，不要输出 Markdown 代码块。"
            "JSON 结构必须包含 summary, outline, keywords, teaching_value, cautions 字段。"
            "其中 outline 为数组，元素包含 level 和 title。"
        )
        user_prompt = (
            f"请解析下面这份 Markdown 课程材料《{material['name']}》，输出结构化教学摘要。\n\n"
            f"{markdown_content}"
        )
        response_text = await _call_ai_chat(system_prompt, user_prompt, capability="thinking")
        parsed_result = _parse_ai_json(response_text)
        now = datetime.now().isoformat()
        check_payload = build_material_mastery_check_payload(
            parsed_result,
            material_name=str(material["name"] or ""),
            generated_at=now,
        )
        check_status = "ready" if check_payload.get("status") == "ready" else "fallback"
        check_error = "" if check_status == "ready" else str(check_payload.get("reason") or "")

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE course_materials
                SET ai_parse_status = 'completed',
                    ai_parse_result_json = ?,
                    check_questions_json = ?,
                    check_questions_status = ?,
                    check_questions_error = ?,
                    check_questions_generated_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(parsed_result, ensure_ascii=False),
                    json.dumps(check_payload, ensure_ascii=False),
                    check_status,
                    check_error,
                    now,
                    now,
                    material_id,
                ),
            )
            conn.commit()

        return {
            "status": "success",
            "message": "AI 解析完成",
            "result": parsed_result,
        }
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_parse_status = 'failed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(500, f"AI 解析失败: {error_message}")


@router.post("/api/materials/{material_id}/ai-optimize", response_class=JSONResponse)
async def ai_optimize_material(material_id: int, user: dict = Depends(get_current_teacher)):
    return await _run_ai_material_rewrite(
        material_id=material_id,
        mode="optimize",
        prompt="",
        user=user,
    )
