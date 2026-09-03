import mimetypes

from urllib.parse import quote

from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *
from ...services.learning_progress_service import get_material_mastery_check_context
from ...services.html_package_service import find_html_package_root, lesson_number_from_entry_name
from ...services.material_render_service import attach_render_metadata, resolve_render_file, resolve_render_target
from ...services.document_render_service import DocumentRenderError, document_render_service


router = APIRouter()

_DYNAMIC_DOCUMENT_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _load_ai_import_record_preview_payload(conn, record_id: int, user: dict):
    row = conn.execute(
        """
        SELECT *
        FROM material_ai_import_records
        WHERE id = ?
        """,
        (record_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "未找到可预览的解析记录")
    material_ids = [
        row["package_material_id"],
        row["parsed_material_id"],
        row["source_material_id"],
        row["parent_material_id"],
    ]
    has_access = False
    for material_id in material_ids:
        if not material_id:
            continue
        try:
            ensure_user_material_access(conn, int(material_id), user)
            has_access = True
            break
        except HTTPException:
            continue
    if not has_access:
        raise HTTPException(404, "未找到可预览的解析记录")
    payload = _build_ai_import_payload_from_record(row, conn)
    fallback_filename = row["source_file_name"] or f"材料解析-{record_id}"
    return row, payload, fallback_filename


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
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到可导出的解析记录")
        material_ids = [
            row["package_material_id"],
            row["parsed_material_id"],
            row["source_material_id"],
            row["parent_material_id"],
        ]
        has_access = False
        for material_id in material_ids:
            if not material_id:
                continue
            try:
                ensure_user_material_access(conn, int(material_id), user)
                has_access = True
                break
            except HTTPException:
                continue
        if not has_access:
            raise HTTPException(404, "未找到可导出的解析记录")
        payload = _build_ai_import_payload_from_record(row, conn)
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
        headers=_DYNAMIC_DOCUMENT_HEADERS,
        background=BackgroundTask(_cleanup_temp_file, temp_path),
    )


@router.get("/api/materials/ai-import-records/{record_id}/render-preview", response_class=HTMLResponse)
async def preview_ai_import_record_export(
    record_id: int,
    format: str = Query(default=""),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        row, payload, fallback_filename = _load_ai_import_record_preview_payload(conn, record_id, user)

    preferred_format = (
        "xlsx"
        if row["document_type"] in {"ordinary_grade_record", "exam_grade_record", "final_grade_transcript"}
        else "docx"
    )
    requested_format = (format or preferred_format).strip().lower()
    if requested_format == "pdf":
        requested_format = preferred_format
    artifact = build_material_export_artifact(
        payload,
        fallback_filename=fallback_filename,
        requested_format=requested_format,
    )
    source_format = (Path(artifact.filename).suffix or f".{requested_format}").lstrip(".").lower()
    title = row["document_type_label"] or Path(artifact.filename).stem or "材料导出预览"
    try:
        job = document_render_service.render_artifact(
            artifact.content,
            filename=artifact.filename,
            media_type=artifact.media_type,
            source_format=source_format,
        )
    except (RuntimeError, DocumentRenderError) as exc:
        return HTMLResponse(
            document_render_service.render_error_html(title=title, message=str(exc)),
            status_code=503,
        )
    return HTMLResponse(
        document_render_service.render_preview_html(
            job,
            title=title,
            user=user,
            eyebrow="期末材料 · 导出一致预览",
            download_label="下载文件",
        )
    )


@router.get("/api/materials/{material_id}/ai-import/export", response_class=FileResponse)
async def export_ai_import_material(
    material_id: int,
    format: str = Query(default=""),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE (
                    parsed_material_id = ?
                    OR package_material_id = ?
                    OR source_material_id = ?
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (material["id"], material["id"], material["id"]),
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
        ai_import_record = None
        if user["role"] == "teacher":
            ai_import_row = _find_material_ai_import_record(
                conn,
                material_id,
                material["teacher_id"],
                completed_only=True,
            )
            if ai_import_row:
                ai_import_record = _build_ai_import_record_detail_payload(ai_import_row)

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
                "ai_import_record": ai_import_record,
                "mastery_check": mastery_check,
            },
        )
        preview_payload = _decorate_material_download_policy(preview_payload)
        attach_render_metadata(conn, [preview_payload])

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


async def _serve_rendered_material(material_id: int, subpath: str, user: dict) -> FileResponse:
    with get_db_connection() as conn:
        node = ensure_user_material_access(conn, material_id, user)
        if not resolve_render_target(conn, node):
            raise HTTPException(400, "当前材料不支持直接渲染")
        target_row = resolve_render_file(conn, node, subpath)
        if not target_row:
            raise HTTPException(404, "未找到要渲染的文件")
        # 复用统一鉴权：确保子资源（css/js/图片等）同样在可访问范围内。
        ensure_user_material_access(conn, int(target_row["id"]), user)
        target = dict(target_row)

    file_path = _load_material_storage_path(target)
    media_type = str(target.get("mime_type") or "") or mimetypes.guess_type(target.get("name") or "")[0] or "application/octet-stream"
    # 直接渲染（inline，无 filename），并禁用 MIME 嗅探。
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/materials/render-view/{material_id}", response_class=HTMLResponse)
async def material_render_shell_page(
    request: Request,
    material_id: int,
    path: str = Query(default=""),
    class_offering_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    """HTML 材料的全屏渲染壳页：iframe 直渲 + 浮动工具栏（返回/白板）+ AI 助手。

    HTML 包课次入口通过 ``?path=lesson_N/lesson_N.html`` 定位；页面内的相对
    路径跳转与静态资源加载全部走 ``/materials/render/{id}/...`` 通道。
    """
    subpath = str(path or "").strip().strip("/")
    with get_db_connection() as conn:
        node = ensure_user_material_access(conn, material_id, user)
        if not resolve_render_target(conn, node):
            raise HTTPException(400, "当前材料不支持直接渲染")
        target_row = resolve_render_file(conn, node, subpath)
        if not target_row:
            raise HTTPException(404, "未找到要渲染的入口文件")
        ensure_user_material_access(conn, int(target_row["id"]), user)

        lesson_number = 0
        lesson_count = 0
        is_html_package = False
        package = find_html_package_root(conn, node if node["node_type"] == "folder" else target_row)
        if package:
            is_html_package = True
            lesson_count = len(package.get("lessons") or [])
            lesson_number = lesson_number_from_entry_name(str(target_row["name"] or ""))

    iframe_src = (
        f"/materials/render/{material_id}/{quote(subpath)}"
        if subpath
        else f"/materials/render/{material_id}/"
    )
    return templates.TemplateResponse(
        request,
        "material_render_shell.html",
        {
            "request": request,
            "user_info": user,
            "shell": {
                "node_id": int(material_id),
                "package_root_id": int(package["root_node_id"]) if package else int(material_id),
                "entry_material_id": int(target_row["id"]),
                "entry_name": str(target_row["name"] or ""),
                "material_name": str(node["name"] or ""),
                "material_path": str(node["material_path"] or ""),
                "iframe_src": iframe_src,
                "subpath": subpath,
                "lesson_number": lesson_number,
                "lesson_count": lesson_count,
                "is_html_package": is_html_package,
            },
            "learning_context": {
                "class_offering_id": class_offering_id,
                "session_id": session_id,
            },
        },
    )


@router.get("/materials/render/{material_id}", response_class=FileResponse)
async def render_material_entry(material_id: int, user: dict = Depends(get_current_user)):
    return await _serve_rendered_material(material_id, "", user)


@router.get("/materials/render/{material_id}/{subpath:path}", response_class=FileResponse)
async def render_material_asset(material_id: int, subpath: str = "", user: dict = Depends(get_current_user)):
    return await _serve_rendered_material(material_id, subpath, user)


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
