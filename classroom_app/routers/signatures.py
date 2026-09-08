from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from ..database import get_db_connection
from ..dependencies import get_client_ip, get_current_user
from ..services import (
    signature_image_service,
    signature_point_service,
    signature_service,
    signature_workflow_service,
)


router = APIRouter(prefix="/api/signatures")


@router.get("/notification-readiness")
async def api_signature_notification_readiness(user: dict = Depends(get_current_user)):
    from ..services import email_notification_service as email

    with get_db_connection() as conn:
        recipient = email._load_recipient_email(conn, role=user["role"], user_pk=user["id"])
        sender = email._resolve_sender_teacher_id(conn, {"category": "signature_workflow", "recipient_role": user["role"], "recipient_user_pk": user["id"]})
        configured = bool(sender and email._load_default_email_config(conn, sender))
    reason = "邮件提醒已就绪，发送仍遵循账号的通知偏好。" if recipient and configured else "请在个人资料填写收件邮箱后接收邮件提醒。" if not recipient else "邮件通道尚未配置；可配置默认发信邮箱，或由平台设置统一发信账号。"
    return {"email_available": bool(recipient and configured), "email_reason": reason}


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
    school_code: str = Form(""),
    college: str = Form(""),
    department: str = Form(""),
    identity_category: str = Form(""),
    signature_kind: str = Form(""),
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
                school_code=school_code,
                college=college,
                department=department,
                identity_category=identity_category,
                signature_kind=signature_kind,
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
            is_admin_viewer = False
            row, actor = signature_service.get_signature_row_for_actor(
                conn,
                user,
                signature_id,
                require_use=False,
            )
            is_admin_viewer = bool(actor.get("is_super_admin"))
            if int(download or 0) == 1 and not signature_service.can_use_signature(actor, row, conn):
                raise HTTPException(status_code=403, detail="当前账号无权下载此签名。")
            file_path = signature_service.resolve_signature_file_path(row)
            if not file_path:
                raise HTTPException(status_code=404, detail="签名图片文件不存在。")
            # 浏览场景（卡片/详情/认领审批）只对可直接使用者出原图；其他有
            # 查看权的人拿到带“仅供预览”水印的降清图。超管审核需要原图，
            # 始终放行（替代所有人审批的兜底职责）。
            if (
                int(download or 0) != 1
                and not is_admin_viewer
                and not signature_service.can_use_signature(actor, row, conn)
            ):
                try:
                    preview = signature_image_service.ensure_preview(row["file_hash"], file_path)
                except signature_image_service.SignatureImageError:
                    raise HTTPException(status_code=404, detail="签名图片文件不存在。")
                response = FileResponse(
                    preview,
                    media_type="image/png",
                    content_disposition_type="inline",
                )
                response.headers["Cache-Control"] = "private, no-store"
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
            response.headers["Cache-Control"] = "private, no-store"
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
    q: str = "", document_type: str = "", requester_role: str = "",
    identity: str = "", organization: str = "", request_kind: str = "", batch_id: str = "",
    offset: int = 0, limit: int = 50,
    user: dict = Depends(get_current_user),
):
    try:
        with get_db_connection() as conn:
            return signature_workflow_service.list_access_requests(
                conn,
                user,
                direction=direction,
                status=status, search=q, document_type=document_type, requester_role=requester_role,
                identity=identity, organization=organization, request_kind=request_kind, batch_id=batch_id,
                offset=offset, limit=limit,
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


@router.post("/{signature_id:int}/merge", response_class=JSONResponse)
async def api_merge_signatures(
    signature_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_body(request)
    raw_ids = payload.get("duplicate_ids")
    try:
        with get_db_connection() as conn:
            result = signature_service.merge_duplicate_signatures(
                conn,
                user,
                signature_id,
                [item for item in raw_ids] if isinstance(raw_ids, list) else [],
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
        def review_batch():
            with get_db_connection() as conn:
                result = signature_workflow_service.batch_review_access_requests(
                    conn, user, list(raw_ids) if isinstance(raw_ids, list) else [],
                    action=action, note=str(payload.get("note") or ""),
                )
                conn.commit()
                return result
        return await run_in_threadpool(review_batch)
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
                expected_snapshot_id=payload.get("expected_snapshot_id"),
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
    q: str = "",
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
                search=q[:80],
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
        from ..services.material_signature_service import prepare_snapshot

        snapshot = await run_in_threadpool(prepare_snapshot, user, {
            "material_type": str(payload.get("material_type") or ""),
            "material_id": str(payload.get("material_id") or ""),
        })
        if payload.get("expected_revision") and str(payload["expected_revision"]) != snapshot["material_revision"]:
            raise signature_service.SignatureServiceError(409, "材料内容已更新，请重新打开材料后申请。")
        with get_db_connection() as conn:
            result = signature_point_service.create_point_flow(
                conn,
                user,
                function_point_key=function_point_key,
                material_type=str(payload.get("material_type") or ""),
                material_id=str(payload.get("material_id") or ""),
                signature_ids=list(raw_ids) if isinstance(raw_ids, list) else [],
                note=str(payload.get("note") or ""),
                snapshot=snapshot,
                auto_apply=payload.get("auto_apply") is True,
                opinion_mode="stamp" if payload.get("opinion_mode") == "stamp" else "keep",
            )
            conn.commit()
        return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.get("/requests/{request_id:int}", response_class=JSONResponse)
async def api_signature_request_detail(request_id: int, user: dict = Depends(get_current_user)):
    from ..services.material_signature_service import authorized_request

    try:
        with get_db_connection() as conn:
            item = authorized_request(conn, user, request_id)
        return {"status": "success", "request": item,
                "preview_url": f"/api/signatures/requests/{request_id}/preview" if item.get("snapshot_id") else "",
                "preview_notice": "" if item.get("snapshot_id") else "历史申请未保存申请时文档，请申请人选择材料重新提交。"}
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/requests/{request_id:int}/document-review")
async def api_signature_document_review(request_id: int, request: Request, user: dict = Depends(get_current_user)):
    from ..services.material_signature_service import review_document_requests

    payload = await _json_body(request)
    def review():
        with get_db_connection() as conn:
            result = review_document_requests(conn, user, request_id, action=str(payload.get("action") or ""),
                note=str(payload.get("note") or ""), expected_snapshot_id=payload.get("expected_snapshot_id"))
            conn.commit()
            return result
    try:
        return await run_in_threadpool(review)
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


def _render_signature_request_preview(request_id: int, user: dict) -> str:
    from ..services.material_signature_service import authorized_request, artifact_path, json_object
    from ..services.document_render_service import document_render_service

    with get_db_connection() as conn:
        item = authorized_request(conn, user, request_id)
        snapshot = conn.execute("SELECT * FROM signature_material_snapshots WHERE id = ?", (item.get("snapshot_id", ""),)).fetchone()
        if not snapshot:
            raise signature_service.SignatureServiceError(409, "历史申请没有冻结文档，请申请人基于当前材料重新申请。")
        artifact = json_object(snapshot["artifact_json"])
    original = artifact
    artifact = artifact.get("pdf_artifact") or artifact
    job = document_render_service.render_artifact(
        artifact_path(artifact).read_bytes(), filename=artifact["filename"], media_type=artifact["media_type"],
        source_format=artifact["suffix"].lstrip("."),
    )
    if not original.get("pdf_artifact"):
        from ..services.material_signature_service import store_artifact, dumps

        pdf_path = job.root / (job.manifest.get("pdf_file") or "document.pdf")
        if pdf_path.is_file():
            original["pdf_artifact"] = store_artifact(pdf_path.read_bytes(), filename="申请时材料.pdf", media_type="application/pdf")
            with get_db_connection() as conn:
                conn.execute("UPDATE signature_material_snapshots SET artifact_json = ? WHERE id = ?", (dumps(original), snapshot["id"]))
                conn.commit()
    return document_render_service.render_preview_html(job, title=snapshot["title"], user=user,
        eyebrow="签名审批 · 申请时文档", download_label="下载申请时文档", signature_request_id=request_id)


@router.get("/requests/{request_id:int}/preview", response_class=HTMLResponse)
async def api_signature_request_preview(request_id: int, user: dict = Depends(get_current_user)):
    from ..services.document_render_service import document_render_service, DocumentRenderError

    try:
        content = await run_in_threadpool(_render_signature_request_preview, request_id, user)
        return HTMLResponse(content, headers={"Cache-Control": "private, no-store"})
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)
    except (DocumentRenderError, RuntimeError) as exc:
        return HTMLResponse(document_render_service.render_error_html(title="申请文档预览", message=str(exc)), status_code=503,
                            headers={"Cache-Control": "private, no-store"})


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


def _material_call(user, operation, *args):
    from ..services import material_batch_service

    try:
        with get_db_connection() as conn:
            result = getattr(material_batch_service, operation)(conn, user, *args)
            conn.commit()
            return result
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/materials/selection")
async def api_material_selection(request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_body(request)
    return await run_in_threadpool(_material_call, user, "selection_context", payload.get("documents"))


@router.post("/materials/applications")
async def api_material_application(request: Request, user: dict = Depends(get_current_user)):
    return await run_in_threadpool(_material_call, user, "create_application", await _json_body(request))


@router.get("/materials/applications")
async def api_material_application_history(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        batches = conn.execute("SELECT id, status, results_json, error_message, created_at FROM signature_application_batches WHERE requester_role = ? AND requester_id = ? ORDER BY created_at DESC, id DESC LIMIT 50", (user["role"], user["id"])).fetchall()
        flows = conn.execute("SELECT id, application_batch_id, material_label, function_point_key, status, apply_status, apply_error FROM signature_point_flows WHERE requester_role = ? AND requester_id = ? ORDER BY created_at DESC, id DESC LIMIT 100", (user["role"], user["id"])).fetchall()
    import json

    return {"batches": [{**dict(batch), "results": json.loads(batch["results_json"] or "[]")} for batch in batches], "flows": [dict(flow) for flow in flows]}


@router.get("/materials/applications/{batch_id}")
async def api_material_application_status(batch_id: str, user: dict = Depends(get_current_user)):
    return await run_in_threadpool(_material_call, user, "job_status", batch_id, "application")


@router.post("/materials/bundles/preflight")
async def api_material_bundle_preflight(request: Request, user: dict = Depends(get_current_user)):
    return await run_in_threadpool(_material_call, user, "bundle_preflight", await _json_body(request))


@router.post("/materials/bundles/{bundle_id}/submit")
async def api_material_bundle_submit(bundle_id: str, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_body(request)
    return await run_in_threadpool(_material_call, user, "submit_bundle", bundle_id, payload.get("allow_incomplete") is True)


@router.get("/materials/bundles/{bundle_id}")
async def api_material_bundle_status(bundle_id: str, user: dict = Depends(get_current_user)):
    return await run_in_threadpool(_material_call, user, "job_status", bundle_id, "bundle")


@router.get("/materials/bundles/{bundle_id}/download")
async def api_material_bundle_download(bundle_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime
    from ..services.material_batch_service import job_status
    from ..services.material_signature_service import artifact_path, json_object, load_material

    try:
        with get_db_connection() as conn:
            job_status(conn, user, bundle_id, "bundle")
            row = conn.execute("SELECT * FROM material_export_bundles WHERE id = ?", (bundle_id,)).fetchone()
            if datetime.fromisoformat(str(row["expires_at"])) < datetime.now():
                raise signature_service.SignatureServiceError(410, "下载已过期，请重新打包。")
            if row["status"] != "ready":
                raise signature_service.SignatureServiceError(409, "压缩包尚未准备完成。")
            for doc in json_object(row["plan_json"])["documents"]:
                load_material(conn, user, doc, write=False)
            artifact = json_object(row["artifact_json"])
        return FileResponse(artifact_path(artifact), filename=artifact["filename"], media_type="application/zip", headers={"Cache-Control": "private, no-store"})
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)


@router.post("/flows/{flow_id:int}/apply")
async def api_material_apply_flow(flow_id: int, user: dict = Depends(get_current_user)):
    from ..services.material_signature_apply_service import apply_flow

    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT requester_role, requester_id FROM signature_point_flows WHERE id = ?", (flow_id,)).fetchone()
            if not row or (row["requester_role"], row["requester_id"]) != (user["role"], user["id"]):
                raise signature_service.SignatureServiceError(404, "申请流程不存在或无权操作。")
        return await apply_flow(flow_id)
    except signature_service.SignatureServiceError as exc:
        _raise_signature_error(exc)
