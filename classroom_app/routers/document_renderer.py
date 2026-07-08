from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from ..dependencies import get_current_user
from ..services.document_render_service import (
    PNG_MEDIA_TYPE,
    DocumentRenderError,
    DocumentRenderNotFound,
    document_render_service,
    verify_render_token,
)


router = APIRouter(prefix="/api/document-renderer")


def _require_token(key: str, token: str | None, user: dict) -> None:
    if not verify_render_token(key, token, user=user):
        raise HTTPException(status_code=403, detail="预览凭证无效，请刷新预览页面。")


def _map_render_error(exc: DocumentRenderError) -> HTTPException:
    if isinstance(exc, DocumentRenderNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/jobs/{key}/pages/{page_number}", response_class=FileResponse)
async def get_rendered_document_page(
    key: str,
    page_number: int,
    size: str = Query(default="medium"),
    token: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _require_token(key, token, user)
    if size not in {"medium", "large"}:
        raise HTTPException(status_code=400, detail="预览图片尺寸参数无效。")
    try:
        image_path = document_render_service.get_page_image_path(key, page_number, size=size)
    except DocumentRenderError as exc:
        raise _map_render_error(exc) from exc
    return FileResponse(
        image_path,
        media_type=PNG_MEDIA_TYPE,
        headers={
            "Cache-Control": "private, max-age=900",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/jobs/{key}/download", response_class=FileResponse)
async def download_rendered_document(
    key: str,
    token: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _require_token(key, token, user)
    try:
        document_path, filename, media_type = document_render_service.get_download_path(key)
    except DocumentRenderError as exc:
        raise _map_render_error(exc) from exc
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return FileResponse(
        document_path,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=900",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/jobs/{key}/metadata", response_class=JSONResponse)
async def get_rendered_document_metadata(
    key: str,
    token: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _require_token(key, token, user)
    try:
        job = document_render_service.get_job(key)
    except DocumentRenderError as exc:
        raise _map_render_error(exc) from exc
    large_pages = sum(1 for path in job.root.glob("page-*.large.png") if path.is_file())
    return JSONResponse(
        {
            "key": job.key,
            "filename": job.filename,
            "media_type": job.media_type,
            "source_format": job.manifest.get("source_format") or "",
            "page_count": job.page_count,
            "large_pages_cached": large_pages,
            "created_at": job.manifest.get("created_at"),
            "updated_at": job.manifest.get("updated_at"),
            "last_access_at": job.manifest.get("last_access_at"),
        },
        headers={"Cache-Control": "private, no-store"},
    )
