from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..database import get_db_connection
from ..dependencies import get_client_ip, get_current_user
from ..services import (
    signature_image_service,
    signature_point_service,
    signature_service,
    signature_workflow_service,
)


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
    identity_category: str = "",
    function_point_key: str = "",
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
                identity_category=identity_category,
                function_point_key=function_point_key,
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
    subject_id: int | None = Form(None),
    scope_level: str = Form(""),
    identity_category: str = Form(""),
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
                subject_id=subject_id,
                scope_level=scope_level,
                identity_category=identity_category,
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
            row, actor = signature_service.get_signature_row_for_actor(
                conn,
                user,
                signature_id,
                require_use=bool(int(download or 0) == 1),
            )
            file_path = signature_service.resolve_signature_file_path(row)
            if not file_path:
                raise HTTPException(status_code=404, detail="签名图片文件不存在。")
            # 浏览场景（卡片/详情/认领审批）只对可直接使用者出原图；
            # 其他有查看权的人拿到带“仅供预览”水印的降清图，防止截图滥用。
            if int(download or 0) != 1 and not signature_service.can_use_signature(actor, row, conn):
                try:
                    preview = signature_image_service.ensure_preview(row["file_hash"], file_path)
                except signature_image_service.SignatureImageError:
                    raise HTTPException(status_code=404, detail="签名图片文件不存在。")
                response = FileResponse(
                    preview,
                    media_type="image/png",
                    content_disposition_type="inline",
                )
                response.headers["Cache-Control"] = "private, max-age=300"
                return response
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
            result = signature_workflow_service.authorize_and_consume_signature_use(
                conn,
                user,
                signature_id,
                function_point_key=str(payload.get("function_point_key") or ""),
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


@router.post("/{signature_id:int}/requests", response_class=JSONResponse)
async def api_create_signature_access_request(
    signature_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    try:
        with get_db_connection() as conn:
            raw_points = payload.get("function_point_keys")
            result = signature_workflow_service.create_access_request(
                conn,
                user,
                signature_id,
                note=str(payload.get("note") or ""),
                function_point_keys=[str(item) for item in raw_points] if isinstance(raw_points, list) else [],
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/requests", response_class=JSONResponse)
async def api_list_signature_access_requests(
    direction: str = "incoming",
    status: str = "",
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_workflow_service.list_access_requests(
                conn,
                user,
                direction=direction,
                status=status,
            )
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/{signature_id:int}/claim", response_class=JSONResponse)
async def api_claim_signature(
    signature_id: int,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            result = signature_workflow_service.claim_signature(conn, user, signature_id)
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/claim-candidates", response_class=JSONResponse)
async def api_signature_claim_candidates(
    q: str = "",
    limit: int = 200,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_service.list_claim_candidates(conn, user, q=q, limit=limit)
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/{signature_id:int}/claim-requests", response_class=JSONResponse)
async def api_create_signature_claim_request(
    signature_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    try:
        with get_db_connection() as conn:
            result = signature_workflow_service.create_claim_request(
                conn,
                user,
                signature_id,
                note=str(payload.get("note") or ""),
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/{signature_id:int}/unbind", response_class=JSONResponse)
async def api_unbind_signature(
    signature_id: int,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            item = signature_service.unbind_signature(conn, user, signature_id)
            conn.commit()
        return {"status": "success", "signature": item}
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/requests/batch-review", response_class=JSONResponse)
async def api_batch_review_signature_requests(
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    raw_ids = payload.get("request_ids")
    action = str(payload.get("action") or "")
    try:
        with get_db_connection() as conn:
            result = signature_workflow_service.batch_review_access_requests(
                conn,
                user,
                [item for item in raw_ids] if isinstance(raw_ids, list) else [],
                action=action,
                note=str(payload.get("note") or ""),
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/{signature_id:int}/refs", response_class=JSONResponse)
async def api_signature_refs(
    signature_id: int,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_service.get_signature_refs(conn, user, signature_id)
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/{signature_id:int}/image", response_class=JSONResponse)
async def api_replace_signature_image(
    signature_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            item = await signature_service.replace_signature_image(conn, user, signature_id, file)
            conn.commit()
        return {"status": "success", "signature": item}
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/usage-logs", response_class=JSONResponse)
async def api_list_signature_usage_logs(
    limit: int = 100,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_workflow_service.list_signature_usage_about_actor(
                conn,
                user,
                limit=limit,
            )
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/requests/{request_id:int}/approve", response_class=JSONResponse)
async def api_approve_signature_access_request(
    request_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    try:
        with get_db_connection() as conn:
            result = signature_workflow_service.review_access_request(
                conn,
                user,
                request_id,
                action="approve",
                note=str(payload.get("note") or ""),
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/requests/{request_id:int}/reject", response_class=JSONResponse)
async def api_reject_signature_access_request(
    request_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    try:
        with get_db_connection() as conn:
            result = signature_workflow_service.review_access_request(
                conn,
                user,
                request_id,
                action="reject",
                note=str(payload.get("note") or ""),
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/points/{function_point_key}/state", response_class=JSONResponse)
async def api_signature_point_state(
    function_point_key: str,
    material_type: str,
    material_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_point_service.get_point_state(
                conn,
                user,
                function_point_key=function_point_key,
                material_type=material_type,
                material_id=material_id,
            )
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/points/{function_point_key}/flows", response_class=JSONResponse)
async def api_create_signature_point_flow(
    function_point_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    raw_ids = payload.get("signature_ids")
    try:
        with get_db_connection() as conn:
            result = signature_point_service.create_point_flow(
                conn,
                user,
                function_point_key=function_point_key,
                material_type=str(payload.get("material_type") or ""),
                material_id=str(payload.get("material_id") or ""),
                signature_ids=list(raw_ids) if isinstance(raw_ids, list) else [],
                note=str(payload.get("note") or ""),
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/point-flows/{flow_id:int}/end", response_class=JSONResponse)
async def api_end_signature_point_flow(
    flow_id: int,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            result = signature_point_service.end_point_flow(conn, user, flow_id)
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/requests/{request_id:int}/cancel", response_class=JSONResponse)
async def api_cancel_signature_access_request(
    request_id: int,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            result = signature_workflow_service.cancel_access_request(conn, user, request_id)
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/function-points", response_class=JSONResponse)
async def api_signature_function_points(user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            signature_service.build_signature_actor(conn, user)
            return {"items": signature_workflow_service.list_function_points(conn)}
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
