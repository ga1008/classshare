from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..database import get_db_connection
from ..dependencies import get_client_ip, get_current_user
from ..services import signature_service


router = APIRouter(prefix="/api/signatures")


def _raise_signature_error(exc: signature_service.SignatureServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@router.get("", response_class=JSONResponse)
@router.get("/list", response_class=JSONResponse)
async def api_list_signatures(
    q: str = "",
    school_code: str = "",
    owner_role: str = "",
    subject_role: str = "",
    scope: str = "",
    limit: int = 200,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_service.list_signatures(
                conn,
                user,
                search=q,
                school_code=school_code,
                owner_role=owner_role,
                subject_role=subject_role,
                scope=scope,
                limit=limit,
            )
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/schools", response_class=JSONResponse)
async def api_signature_school_options(
    q: str = "",
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_service.list_signature_school_options(conn, user, q=q)
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/teachers", response_class=JSONResponse)
async def api_signature_teacher_options(
    q: str = "",
    school_code: str = "",
    limit: int = 60,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_service.list_signature_teacher_options(
                conn,
                user,
                q=q,
                school_code=school_code,
                limit=limit,
            )
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/upload", response_class=JSONResponse)
async def api_upload_signature(
    file: UploadFile = File(...),
    name: str = Form(""),
    subject_role: str = Form(""),
    subject_name: str = Form(""),
    scope_level: str = Form(""),
    description: str = Form(""),
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            item = await signature_service.create_signature_from_upload(
                conn,
                user,
                file,
                name=name,
                subject_role=subject_role,
                subject_name=subject_name,
                scope_level=scope_level,
                description=description,
            )
            conn.commit()
        return {"status": "success", "signature": item}
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.patch("/{signature_id:int}", response_class=JSONResponse)
async def api_update_signature(signature_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_body(request)
    try:
        with get_db_connection() as conn:
            item = signature_service.update_signature_metadata(conn, user, signature_id, payload)
            conn.commit()
        return {"status": "success", "signature": item}
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/{signature_id:int}/image")
@router.get("/image/{signature_id:int}")
async def api_signature_image(
    signature_id: int,
    request: Request,
    download: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            row, _actor = signature_service.get_signature_row_for_actor(conn, user, signature_id)
            file_path = signature_service.resolve_signature_file_path(row)
            if not file_path:
                raise HTTPException(status_code=404, detail="签名图片文件不存在。")
            if int(download or 0) == 1:
                signature_service.record_signature_usage(
                    conn,
                    user,
                    signature_id,
                    action="download",
                    context_type="signature_library",
                    ip=get_client_ip(request),
                    user_agent=request.headers.get("user-agent", ""),
                )
                conn.commit()
            filename = _safe_download_name(row["name"], row["file_ext"])
            response = FileResponse(
                Path(file_path),
                media_type=row["mime_type"] or "application/octet-stream",
                filename=filename,
                content_disposition_type="attachment" if int(download or 0) == 1 else "inline",
            )
            response.headers["Cache-Control"] = "private, max-age=300"
            return response
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/{signature_id:int}/use", response_class=JSONResponse)
async def api_record_signature_use(
    signature_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    try:
        with get_db_connection() as conn:
            result = signature_service.record_signature_usage(
                conn,
                user,
                signature_id,
                action=str(payload.get("action") or "use"),
                context_type=str(payload.get("context_type") or ""),
                context_id=str(payload.get("context_id") or ""),
                context_label=str(payload.get("context_label") or ""),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                ip=get_client_ip(request),
                user_agent=request.headers.get("user-agent", ""),
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.delete("/{signature_id:int}", response_class=JSONResponse)
async def api_delete_signature(signature_id: int, user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            result = signature_service.delete_signature(conn, user, signature_id)
            conn.commit()
        return {"status": "success", **result}
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/delete", response_class=JSONResponse)
async def api_delete_signature_compat(request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_body(request)
    try:
        signature_id = int(payload.get("id") or payload.get("signature_id") or 0)
    except (TypeError, ValueError):
        signature_id = 0
    if signature_id <= 0:
        raise HTTPException(status_code=400, detail="缺少签名 ID。")
    return await api_delete_signature(signature_id, user)


def _safe_download_name(name: Any, ext: Any) -> str:
    safe_name = "".join(ch for ch in str(name or "signature") if ch not in '\\/:*?"<>|').strip()
    safe_ext = str(ext or ".png").strip()
    if safe_ext and not safe_ext.startswith("."):
        safe_ext = f".{safe_ext}"
    return f"{safe_name or 'signature'}{safe_ext or '.png'}"
