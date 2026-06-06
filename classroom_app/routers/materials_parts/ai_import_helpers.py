from .common import *
from .generation_helpers import *
from ...db.connection import get_configured_db_engine


def _material_ai_import_status_message(row: dict, *, queue_position: int | None = None) -> str:
    status = str(row.get("parse_status") or "queued").strip().lower()
    source_name = row.get("source_file_name") or "材料文件"
    if status == "queued":
        if queue_position and queue_position > 1:
            return f"《{source_name}》已进入 AI 解析队列，当前约第 {queue_position} 位。"
        return f"《{source_name}》已进入 AI 解析队列，系统会按顺序处理。"
    if status == "running":
        return f"AI 正在解析《{source_name}》，会先校验乱码和结构，再生成可保存的材料内容。"
    if status == "completed":
        return f"《{source_name}》解析完成，已生成材料包和结构化内容。"

    error_message = str(row.get("error_message") or "").strip()
    if error_message:
        return error_message
    if status == "ai_failed":
        return "AI 服务未能返回有效识别结果，请稍后重试或换用更清晰的 PDF/Word 文件。"
    if status == "quality_failed":
        return "解析内容疑似乱码或质量不足，系统已阻止保存无效正文。"
    if status == "unsupported":
        return "当前文档格式暂不支持自动解析，请先转换为 docx、xlsx 或 PDF 后重试。"
    return "解析未完成，请稍后重试。"


def _material_ai_import_queue_position(conn, record_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS queue_position
        FROM material_ai_import_records
        WHERE parse_status = 'queued'
          AND id <= ?
        """,
        (int(record_id),),
    ).fetchone()
    if not row:
        return None
    return int(row["queue_position"] or 0) or None


def _serialize_material_ai_import_task(conn, row, user: dict) -> dict:
    item = dict(row)
    status = str(item.get("parse_status") or "queued").strip().lower()
    record_id = int(item.get("id") or 0)
    queue_position = _material_ai_import_queue_position(conn, record_id) if status == "queued" else None

    package_id = int(item.get("package_material_id") or 0) or None
    source_id = int(item.get("source_material_id") or 0) or None
    parsed_id = int(item.get("parsed_material_id") or 0) or None

    package_item = _fetch_material_response_item(conn, package_id, user) if package_id else None
    source_item = _fetch_material_response_item(conn, source_id, user) if source_id else None
    parsed_item = _fetch_material_response_item(conn, parsed_id, user) if parsed_id else None

    return {
        "id": record_id,
        "teacher_id": int(item.get("teacher_id") or 0),
        "parent_material_id": int(item.get("parent_material_id") or 0) or None,
        "package_material_id": package_id,
        "source_material_id": source_id,
        "parsed_material_id": parsed_id,
        "document_group": item.get("document_group") or "",
        "document_type": item.get("document_type") or "",
        "document_type_label": item.get("document_type_label") or "",
        "parse_status": status,
        "status": status,
        "status_label": MATERIAL_AI_IMPORT_STATUS_LABELS.get(status, "处理中"),
        "is_active": status in MATERIAL_AI_IMPORT_ACTIVE_STATUSES,
        "is_terminal": status in MATERIAL_AI_IMPORT_FINAL_STATUSES,
        "parse_mode": item.get("parse_mode") or "ai",
        "extraction_method": item.get("extraction_method") or "",
        "source_file_name": item.get("source_file_name") or "",
        "source_file_size": int(item.get("source_file_size") or 0),
        "source_mime_type": item.get("source_mime_type") or "",
        "content_quality_status": item.get("content_quality_status") or "unchecked",
        "error_message": item.get("error_message") or "",
        "message": _material_ai_import_status_message(item, queue_position=queue_position),
        "queue_position": queue_position,
        "created_at": item.get("created_at") or "",
        "started_at": item.get("started_at") or "",
        "updated_at": item.get("updated_at") or "",
        "completed_at": item.get("completed_at") or "",
        "failed_at": item.get("failed_at") or "",
        "package_item": package_item,
        "source_item": source_item,
        "parsed_item": parsed_item,
    }


def _ensure_material_ai_import_workers() -> asyncio.Queue[int]:
    global _material_ai_import_queue, _material_ai_import_worker_tasks
    if _material_ai_import_queue is None:
        _material_ai_import_queue = asyncio.Queue(maxsize=MATERIAL_AI_IMPORT_QUEUE_MAX_PENDING)

    live_tasks = [task for task in _material_ai_import_worker_tasks if not task.done()]
    _material_ai_import_worker_tasks = live_tasks
    while len(_material_ai_import_worker_tasks) < MATERIAL_AI_IMPORT_WORKER_COUNT:
        worker_no = len(_material_ai_import_worker_tasks) + 1
        _material_ai_import_worker_tasks.append(asyncio.create_task(_material_ai_import_worker_loop(worker_no)))
    return _material_ai_import_queue


def _enqueue_material_ai_import_task(record_id: int) -> bool:
    record_id = int(record_id)
    if record_id <= 0:
        return False
    if record_id in _material_ai_import_enqueued_ids:
        return True

    queue = _ensure_material_ai_import_workers()
    try:
        queue.put_nowait(record_id)
    except asyncio.QueueFull:
        return False
    _material_ai_import_enqueued_ids.add(record_id)
    return True


def _recover_stale_material_ai_import_tasks(conn) -> int:
    cutoff = (datetime.now() - timedelta(minutes=MATERIAL_AI_IMPORT_STALE_MINUTES)).isoformat()
    now = datetime.now().isoformat()
    cursor = conn.execute(
        """
        UPDATE material_ai_import_records
        SET parse_status = 'queued',
            started_at = NULL,
            error_message = CASE
                WHEN TRIM(COALESCE(error_message, '')) = '' THEN '上次解析进程中断，系统已重新排队。'
                ELSE error_message
            END,
            updated_at = ?
        WHERE parse_status = 'running'
          AND COALESCE(started_at, updated_at, created_at) < ?
        """,
        (now, cutoff),
    )
    return int(cursor.rowcount or 0)


def _classify_material_ai_import_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HTTPException):
        status_code = int(exc.status_code or 500)
        message = str(exc.detail or "").strip() or "解析失败"
    else:
        status_code = 500
        message = str(exc).strip() or "解析失败"

    lowered = message.lower()
    if status_code in {400, 415} and ("不受支持" in message or "格式" in message or "unsupported" in lowered):
        return "unsupported", message
    if "乱码" in message or "质量校验" in message or "质量不足" in message or "quality" in lowered or "garbled" in lowered:
        return "quality_failed", message
    if "可解析内容" in message or "无法从该材料中抽取" in message:
        return "quality_failed", message
    if "AI 未返回" in message or "AI 服务" in message or "AI 助手" in message:
        return "ai_failed", message
    if status_code in {429, 502, 503, 504}:
        return "ai_failed", message
    return "failed", message


def _mark_material_ai_import_failed(record_id: int, status: str, message: str) -> None:
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = ?,
                error_message = ?,
                updated_at = ?,
                completed_at = COALESCE(completed_at, ?),
                failed_at = COALESCE(failed_at, ?)
            WHERE id = ?
              AND parse_status IN ('queued', 'running')
            """,
            (status, message[:500], now, now, now, int(record_id)),
        )
        conn.commit()


def _claim_material_ai_import_record(
    conn,
    record_id: int,
    *,
    engine: str | None = None,
) -> dict | None:
    db_engine = (engine or get_configured_db_engine()).strip().lower()
    if db_engine not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported material AI import database engine: {db_engine}")
    now = datetime.now().isoformat()
    if db_engine == "postgres":
        cursor = conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = 'running',
                started_at = COALESCE(started_at, ?),
                updated_at = ?,
                error_message = ''
            WHERE id IN (
                SELECT id
                FROM material_ai_import_records
                WHERE id = ?
                  AND parse_status = 'queued'
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (now, now, int(record_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        conn.commit()
        return dict(row)

    cursor = conn.execute(
        """
        UPDATE material_ai_import_records
        SET parse_status = 'running',
            started_at = COALESCE(started_at, ?),
            updated_at = ?,
            error_message = ''
        WHERE id = ?
          AND parse_status = 'queued'
        """,
        (now, now, int(record_id)),
    )
    if int(cursor.rowcount or 0) <= 0:
        return None
    row = conn.execute(
        "SELECT * FROM material_ai_import_records WHERE id = ?",
        (int(record_id),),
    ).fetchone()
    conn.commit()
    return dict(row) if row is not None else None


async def _material_ai_import_worker_loop(worker_no: int) -> None:
    while True:
        queue = _ensure_material_ai_import_workers()
        record_id = await queue.get()
        _material_ai_import_enqueued_ids.discard(int(record_id))
        try:
            await _run_material_ai_import_record(int(record_id))
        except Exception as exc:  # pragma: no cover - worker must not die on one bad record
            status, message = _classify_material_ai_import_error(exc)
            _mark_material_ai_import_failed(int(record_id), status, message)
            print(f"[MATERIAL_AI_IMPORT] worker {worker_no} failed record {record_id}: {message}")
        finally:
            queue.task_done()


async def _run_material_ai_import_record(record_id: int) -> None:
    record_id = int(record_id)
    with get_db_connection() as conn:
        record = _claim_material_ai_import_record(conn, record_id)
        if not record:
            return

    try:
        file_hash = str(record.get("source_file_hash") or "").strip()
        if not file_hash:
            metadata = _parse_json_object(record.get("metadata_json"))
            file_hash = str(metadata.get("source_file_hash") or metadata.get("file_hash") or "").strip()
        stored_path = resolve_global_file_path(file_hash)
        if not stored_path:
            raise HTTPException(410, "源文件缓存已不存在，无法继续解析，请重新上传。")

        parse_result = await parse_material_document(
            file_path=stored_path,
            original_name=record.get("source_file_name") or stored_path.name,
            document_group=record.get("document_group") or "",
            document_type=record.get("document_type") or "",
            ai_chat=_call_ai_chat,
        )
        await _persist_material_ai_import_success(record_id, record, parse_result)
    except Exception as exc:
        status, message = _classify_material_ai_import_error(exc)
        _mark_material_ai_import_failed(record_id, status, message)
        print(f"[MATERIAL_AI_IMPORT] failed record {record_id}: {message}")


async def _persist_material_ai_import_success(record_id: int, record: dict, parse_result) -> None:
    teacher_id = int(record.get("teacher_id") or 0)
    parent_id = int(record.get("parent_material_id") or 0) or None
    user = {"id": teacher_id, "role": "teacher"}
    original_name = record.get("source_file_name") or "material"
    source_file_hash = str(record.get("source_file_hash") or "").strip()
    source_file_size = int(record.get("source_file_size") or 0)
    source_mime_type = str(record.get("source_mime_type") or "").strip()

    readme_content = build_import_readme(result=parse_result, original_name=original_name)
    readme_bytes = readme_content.encode("utf-8")
    readme_hash = hashlib.sha256(readme_bytes).hexdigest()
    await _write_material_file(readme_hash, readme_bytes)

    source_path = resolve_global_file_path(source_file_hash)
    if source_path and source_file_size <= 0:
        source_file_size = source_path.stat().st_size

    file_profile = infer_material_profile(original_name, source_mime_type or None)
    readme_profile = infer_material_profile("readme.md", "text/markdown")
    parse_payload = _build_material_ai_parse_payload(parse_result)
    parse_payload_json = json.dumps(parse_payload, ensure_ascii=False)
    metadata_json = json.dumps(parse_result.metadata, ensure_ascii=False)
    export_payload_json = json.dumps(parse_result.export_payload, ensure_ascii=False)
    warnings_json = json.dumps(parse_result.warnings, ensure_ascii=False)
    content_quality_json = json.dumps(parse_result.content_quality, ensure_ascii=False)

    with get_db_connection() as conn:
        current = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (int(record_id),),
        ).fetchone()
        if not current:
            return
        if str(current["parse_status"] or "").lower() not in MATERIAL_AI_IMPORT_ACTIVE_STATUSES:
            return

        base_parent = None
        base_prefix = ""
        inherited_root_id = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, teacher_id)
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能导入到文件夹中")
            base_prefix = str(base_parent["material_path"])
            inherited_root_id = int(base_parent["root_id"])

        owner_scope = load_teacher_org_scope(conn, teacher_id)
        now = datetime.now().isoformat()
        package_base_name = f"AI解析-{Path(original_name).stem or parse_result.document_type_label}"
        package_name = make_unique_material_name(conn, teacher_id, parent_id, package_base_name)
        package_path = normalize_material_path(f"{base_prefix}/{package_name}" if base_prefix else package_name)

        package_id, package_root_id = _insert_material_folder_row(
            conn,
            user=user,
            name=package_name,
            material_path=package_path,
            parent_id=base_parent["id"] if base_parent else None,
            inherited_root_id=inherited_root_id,
            owner_scope=owner_scope,
            now=now,
        )

        source_name = original_name
        if source_name.strip().lower() == "readme.md":
            source_name = "source-readme.md"
        material_source_path = normalize_material_path(f"{package_path}/{source_name}")
        source_id = _insert_material_file_row(
            conn,
            user=user,
            name=source_name,
            material_path=material_source_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=file_profile,
            file_hash=source_file_hash,
            file_size=source_file_size,
            owner_scope=owner_scope,
            now=now,
        )

        parsed_name = "readme.md"
        parsed_path = normalize_material_path(f"{package_path}/{parsed_name}")
        parsed_id = _insert_material_file_row(
            conn,
            user=user,
            name=parsed_name,
            material_path=parsed_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=readme_profile,
            file_hash=readme_hash,
            file_size=len(readme_bytes),
            owner_scope=owner_scope,
            now=now,
            ai_parse_status="completed",
            ai_parse_result_json=parse_payload_json,
        )

        conn.execute(
            """
            UPDATE material_ai_import_records
            SET package_material_id = ?,
                source_material_id = ?,
                parsed_material_id = ?,
                document_group = ?,
                document_type = ?,
                document_type_label = ?,
                parse_status = 'completed',
                parse_mode = ?,
                extraction_method = ?,
                metadata_json = ?,
                content_markdown = ?,
                parsed_payload_json = ?,
                export_payload_json = ?,
                warnings_json = ?,
                content_quality_status = ?,
                content_quality_json = ?,
                error_message = '',
                updated_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                package_id,
                source_id,
                parsed_id,
                parse_result.document_group,
                parse_result.document_type,
                parse_result.document_type_label,
                "ai" if parse_result.ai_used else "local_fallback",
                parse_result.extraction_method,
                metadata_json,
                parse_result.content_markdown,
                parse_payload_json,
                export_payload_json,
                warnings_json,
                parse_result.content_quality.get("status", "ok"),
                content_quality_json,
                now,
                now,
                int(record_id),
            ),
        )
        refresh_root_git_metadata(conn, package_root_id)
        conn.commit()


def _build_ai_import_payload_from_record(row) -> dict:
    payload = _parse_json_object(row["parsed_payload_json"])
    if payload:
        return payload
    return {
        "metadata": _parse_json_object(row["metadata_json"]),
        "content_markdown": row["content_markdown"] or "",
        "tables": [],
        "warnings": _parse_json_array(row["warnings_json"]),
        "export_payload": _parse_json_object(row["export_payload_json"]),
        "document_group": row["document_group"],
        "document_type": row["document_type"],
        "document_type_label": row["document_type_label"],
        "extraction_method": row["extraction_method"],
    }


def _find_material_ai_import_record(conn, material_id: int, teacher_id: int, *, completed_only: bool = False):
    status_clause = "AND parse_status = 'completed'" if completed_only else ""
    return conn.execute(
        f"""
        SELECT *
        FROM material_ai_import_records
        WHERE teacher_id = ?
          AND (
                parsed_material_id = ?
                OR package_material_id = ?
                OR source_material_id = ?
          )
          {status_clause}
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), int(material_id), int(material_id), int(material_id)),
    ).fetchone()


def _build_ai_import_preview(record, *, content_limit: int = 8000) -> dict:
    payload = _build_ai_import_payload_from_record(record)
    export_payload = _parse_json_object(payload.get("export_payload")) or _parse_json_object(record["export_payload_json"])
    fields = _parse_json_object(export_payload.get("fields"))
    structured = _parse_json_object(export_payload.get("structured"))
    content_markdown = str(payload.get("content_markdown") or record["content_markdown"] or "")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else _parse_json_array(record["warnings_json"])
    return {
        "id": int(record["id"]),
        "document_group": record["document_group"] or "",
        "document_type": record["document_type"] or "",
        "document_type_label": record["document_type_label"] or "",
        "parse_mode": record["parse_mode"] or "",
        "extraction_method": record["extraction_method"] or "",
        "updated_at": record["updated_at"] or "",
        "completed_at": record["completed_at"] or "",
        "metadata": _parse_json_object(payload.get("metadata")) or _parse_json_object(record["metadata_json"]),
        "fields": fields,
        "structured": structured,
        "tables": payload.get("tables") if isinstance(payload.get("tables"), list) else [],
        "warnings": warnings,
        "content_markdown": content_markdown[:content_limit],
        "content_truncated": len(content_markdown) > content_limit,
        "export_url": f"/api/materials/ai-import-records/{int(record['id'])}/export?format=docx",
        "export_pdf_url": f"/api/materials/ai-import-records/{int(record['id'])}/export?format=pdf" if record["document_type"] == "exam_paper" else "",
    }


__all__ = [name for name in globals() if not name.startswith("__")]
