from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..dependencies import get_current_user
from ..services.document_render_service import (
    PNG_MEDIA_TYPE,
    DocumentRenderError,
    DocumentRenderNotFound,
    document_render_service,
    verify_render_token,
)


router = APIRouter(prefix="/api/document-renderer")


def _require_token(key: str, token: str | None) -> None:
    if not verify_render_token(key, token):
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
    _require_token(key, token)
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
    _require_token(key, token)
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

