from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *
from ...services.learning_progress_service import get_material_mastery_check_context


router = APIRouter()


@router.get("/api/materials/ai-import-records/{record_id}/export", response_class=FileResponse)
async def export_ai_import_record(
    record_id: int,
    format: str = Query(default=""),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE id = ? AND teacher_id = ?
            """,
            (record_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到可导出的解析记录")
        payload = _build_ai_import_payload_from_record(row)
        fallback_filename = row["source_file_name"] or f"材料解析-{record_id}"

    artifact = build_material_export_artifact(
        payload,
        fallback_filename=fallback_filename,
        requested_format=format,
    )
    suffix = Path(artifact.filename).suffix or ".docx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(artifact.content)
        temp_path = temp_file.name
    return FileResponse(
        temp_path,
        media_type=artifact.media_type,
        filename=artifact.filename,
        background=BackgroundTask(_cleanup_temp_file, temp_path),
    )


@router.get("/api/materials/{material_id}/ai-import/export", response_class=FileResponse)
async def export_ai_import_material(
    material_id: int,
    format: str = Query(default=""),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE teacher_id = ?
              AND (
                    parsed_material_id = ?
                    OR package_material_id = ?
                    OR source_material_id = ?
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (user["id"], material["id"], material["id"], material["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "该材料没有关联的 AI 解析导出记录")
        record_id = int(row["id"])
    return await export_ai_import_record(record_id=record_id, format=format, user=user)


@router.get("/materials/view/{material_id}", response_class=HTMLResponse)
async def material_viewer_page(
    request: Request,
    material_id: int,
    variant: str = Query(default="original"),
    class_offering_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        allowed_rows = _resolve_allowed_scope_rows(conn, material, user)
        preview_variant = "optimized" if variant == "optimized" and material["ai_optimized_markdown"] else "original"
        can_edit_source = user["role"] == "teacher" and is_editable_material(material)

        mastery_check = None
        if user["role"] == "student" and class_offering_id:
            mastery_check = get_material_mastery_check_context(
                conn,
                class_offering_id=int(class_offering_id),
                student_id=int(user["id"]),
                material_id=int(material_id),
            )

        preview_payload = serialize_material_row(
            material,
            {
                "download_url": f"/materials/download/{material_id}",
                "raw_url": f"/materials/raw/{material_id}",
                "viewer_url": f"/materials/view/{material_id}",
                "content_url": f"/api/materials/{material_id}/content" if can_edit_source else "",
                "preview_variant": preview_variant,
                "path_index": allowed_rows,
                "class_offering_id": class_offering_id,
                "session_id": session_id,
                "is_image": material["preview_type"] == "image",
                "is_markdown": material["preview_type"] == "markdown",
                "is_text": material["preview_type"] in {"markdown", "text"},
                "can_edit_source": can_edit_source,
                "optimized_available": bool(material["ai_optimized_markdown"]),
                "ai_parse_result": json.loads(material["ai_parse_result_json"]) if material["ai_parse_result_json"] else None,
                "mastery_check": mastery_check,
            },
        )
        preview_payload = _decorate_material_download_policy(preview_payload)

    if material["preview_type"] in {"markdown", "text"}:
        preview_payload["content"], preview_payload["content_encoding"] = await _load_material_text_content(
            material,
            prefer_optimized=preview_variant == "optimized",
        )
    else:
        preview_payload["content"] = None
        preview_payload["content_encoding"] = None

    return templates.TemplateResponse(
        request,
        "material_viewer.html",
        {
            "request": request,
            "user_info": user,
            "material": preview_payload,
            "learning_context": {
                "class_offering_id": class_offering_id,
                "session_id": session_id,
            },
        },
    )


@router.get("/materials/raw/{material_id}", response_class=FileResponse)
async def get_material_raw(material_id: int, user: dict = Depends(get_current_user)):
    raw_preview_only = False
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
    raw_preview_only = material["preview_type"] == "image"
    if material["node_type"] != "file":
        raise HTTPException(400, "文件夹不能直接预览")
    if not raw_preview_only:
        raise HTTPException(400, "仅图片材料支持原始内容访问")
    file_path = _load_material_storage_path(material)
    return FileResponse(file_path, media_type=material["mime_type"] or "application/octet-stream")


@router.get("/materials/download/{material_id}", response_class=FileResponse)
async def download_material(material_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
    ensure_download_allowed(material["file_size"], resource_label="课堂材料")
    if material["node_type"] != "file":
        raise HTTPException(400, "文件夹请使用批量下载")
    file_path = _load_material_storage_path(material)
    return FileResponse(
        file_path,
        media_type=material["mime_type"] or "application/octet-stream",
        filename=material["name"],
    )


@router.post("/api/materials/download", response_class=FileResponse)
async def batch_download_materials(payload: MaterialBatchDownloadRequest, user: dict = Depends(get_current_user)):
    if not payload.material_ids:
        raise HTTPException(400, "请选择要下载的材料")

    with get_db_connection() as conn:
        unique_ids = []
        seen_ids = set()
        for material_id in payload.material_ids:
            if material_id in seen_ids:
                continue
            seen_ids.add(material_id)
            unique_ids.append(material_id)

        selected_rows = []
        for material_id in unique_ids:
            selected_rows.append(dict(ensure_user_material_access(conn, int(material_id), user)))

        archive_source_size = _estimate_material_archive_size(conn, selected_rows)
        ensure_download_allowed(archive_source_size, resource_label="所选课堂材料压缩包")
        temp_path = _create_material_zip(conn, selected_rows)

    archive_title = f"course-materials-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=archive_title,
        background=BackgroundTask(_cleanup_temp_file, temp_path),
    )
