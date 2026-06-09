from .common import *


router = APIRouter()


@router.get("/system/academic-credentials", response_class=JSONResponse)
async def api_list_academic_credentials(user: dict = Depends(get_current_teacher)):
    """列出当前教师自己的教务系统对接凭据。"""
    with get_db_connection() as conn:
        credentials = list_teacher_academic_credentials(conn, int(user["id"]))
    return {"status": "success", "credentials": credentials}


@router.get("/system/academic-sync-capabilities", response_class=JSONResponse)
async def api_list_academic_sync_capabilities(user: dict = Depends(get_current_teacher)):
    """Return syncable academic-system features and their latest local sync state."""
    with get_db_connection() as conn:
        capabilities = build_academic_sync_capabilities(conn, int(user["id"]))
    return {"status": "success", "capabilities": capabilities}


@router.post("/system/academic-sync", response_class=JSONResponse)
async def api_sync_academic_data(user: dict = Depends(get_current_teacher)):
    """Manually rerun the saved academic-system sync chain."""
    auto_sync = await sync_teacher_academic_data_after_credential_verified(int(user["id"]))
    return {
        "status": auto_sync.get("status") or "unknown",
        "message": auto_sync.get("message") or "教务系统同步已完成。",
        "auto_sync": auto_sync,
    }


@router.post("/system/integration-request-probe", response_class=JSONResponse)
async def api_probe_integration_request(request: Request, user: dict = Depends(get_current_teacher)):
    """Run a bounded read-only-style request probe with the teacher's saved integration credential."""
    payload = await _parse_json_request(request)
    try:
        return await probe_integration_request(int(user["id"]), payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"对接系统请求失败：{str(exc)[:180]}") from exc


@router.get("/system/exam-reminders/email", response_class=JSONResponse)
async def api_get_exam_email_reminder(event_id: int, user: dict = Depends(get_current_teacher)):
    """Return whether the teacher already has an email reminder for an event."""
    state = get_exam_email_reminder_state(teacher_id=int(user["id"]), calendar_event_id=int(event_id))
    return {"status": "success", **state}


@router.post("/system/exam-reminders/email", response_class=JSONResponse)
async def api_set_exam_email_reminder(request: Request, user: dict = Depends(get_current_teacher)):
    """Schedule a one-shot email reminder fired before an invigilation/exam starts."""
    payload = await _parse_json_request(request)
    try:
        event_id = int(payload.get("event_id") or 0)
        lead_value = int(payload.get("lead_value") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="请求参数无效。")
    lead_unit = str(payload.get("lead_unit") or "").strip().lower()
    if not event_id:
        raise HTTPException(status_code=400, detail="缺少提醒对应的安排标识。")
    try:
        result = schedule_exam_email_reminder(
            teacher_id=int(user["id"]),
            calendar_event_id=event_id,
            lead_value=lead_value,
            lead_unit=lead_unit,
        )
    except ValueError as exc:
        # No email configured / no recipient — actionable client message.
        return {"status": "email_not_configured", "message": str(exc)}
    status_code = 200 if result.get("status") == "success" else 400
    if result.get("status") in {"not_found"}:
        status_code = 404
    return JSONResponse(result, status_code=status_code)


@router.delete("/system/exam-reminders/email", response_class=JSONResponse)
async def api_cancel_exam_email_reminder(event_id: int, user: dict = Depends(get_current_teacher)):
    """Cancel a previously scheduled email reminder for an event."""
    result = cancel_exam_email_reminder(teacher_id=int(user["id"]), calendar_event_id=int(event_id))
    return {"status": "success", **result}


@router.post("/system/academic-reminders/sync-current", response_class=JSONResponse)
async def api_sync_academic_dashboard_reminders(user: dict = Depends(get_current_teacher)):
    """Resync the academic feeds behind the teacher dashboard reminder widget."""
    result = await sync_teacher_dashboard_reminders(int(user["id"]))
    return {
        "status": result.get("status") or "unknown",
        "message": result.get("message") or "教务提醒刷新已完成。",
        "result": result,
    }


@router.post("/system/academic-invigilations/sync-current", response_class=JSONResponse)
async def api_sync_academic_invigilations(user: dict = Depends(get_current_teacher)):
    """Manually sync current-term invigilation assignments into teacher calendar events."""
    result = await sync_current_teacher_invigilations_from_academic_system(int(user["id"]))
    return {
        "status": result.get("status") or "unknown",
        "message": result.get("message") or "监考安排同步已完成。",
        "result": result,
    }


@router.post("/system/academic-course-exams/sync-current", response_class=JSONResponse)
async def api_sync_academic_course_exams(user: dict = Depends(get_current_teacher)):
    """Manually sync current-term course exam assignments into local classroom schedules."""
    result = await sync_current_teacher_course_exams_from_academic_system(int(user["id"]))
    return {
        "status": result.get("status") or "unknown",
        "message": result.get("message") or "任课考试安排同步已完成。",
        "result": result,
    }


@router.post("/system/academic-credentials", response_class=JSONResponse)
async def api_save_academic_credential(request: Request, user: dict = Depends(get_current_teacher)):
    """保存教务系统账号：先真实登录校验，成功后再加密落库。"""
    payload = await _parse_json_request(request)

    try:
        verification = await verify_academic_credential(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not verification.get("ok"):
        raise HTTPException(status_code=400, detail=verification.get("message") or "教务系统账号校验失败。")

    with get_db_connection() as conn:
        try:
            credential = save_verified_academic_credential(conn, int(user["id"]), payload, verification)
            credentials = list_teacher_academic_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    auto_sync = await sync_teacher_academic_data_after_credential_verified(int(user["id"]))

    return {
        "status": "success",
        "message": auto_sync.get("message") or "教务系统账号已验证并保存。",
        "verification": verification,
        "credential": credential,
        "credentials": credentials,
        "auto_sync": auto_sync,
    }


@router.post("/system/academic-credentials/{credential_id}/verify", response_class=JSONResponse)
async def api_verify_academic_credential(credential_id: int, user: dict = Depends(get_current_teacher)):
    """使用已保存的加密密码重新校验教务系统连接。"""
    with get_db_connection() as conn:
        try:
            row = get_teacher_academic_credential(conn, int(user["id"]), credential_id)
            payload = build_saved_credential_verification_payload(row)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        verification = await verify_academic_credential(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with get_db_connection() as conn:
        try:
            credential = update_academic_credential_verification_status(
                conn,
                int(user["id"]),
                credential_id,
                verification,
            )
            credentials = list_teacher_academic_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    auto_sync = None
    if verification.get("ok"):
        auto_sync = await sync_teacher_academic_data_after_credential_verified(int(user["id"]))

    return {
        "status": "success" if verification.get("ok") else "failed",
        "message": (
            auto_sync.get("message")
            if auto_sync
            else verification.get("message") or "教务系统连接校验完成。"
        ),
        "verification": verification,
        "credential": credential,
        "credentials": credentials,
        "auto_sync": auto_sync,
    }


@router.delete("/system/academic-credentials/{credential_id}", response_class=JSONResponse)
async def api_delete_academic_credential(credential_id: int, user: dict = Depends(get_current_teacher)):
    """删除当前教师自己的教务系统凭据。"""
    with get_db_connection() as conn:
        try:
            removed_count = delete_teacher_academic_credential(conn, int(user["id"]), credential_id)
            credentials = list_teacher_academic_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "教务系统对接已删除。",
        "removed_count": removed_count,
        "credentials": credentials,
    }


@router.get("/system/smart-classroom-credentials", response_class=JSONResponse)
async def api_list_smart_classroom_credentials(user: dict = Depends(get_current_teacher)):
    """List the current teacher's saved Smart Classroom access methods."""
    with get_db_connection() as conn:
        credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))
    return {"status": "success", "credentials": credentials}


@router.get("/system/smart-classroom-sync-capabilities", response_class=JSONResponse)
async def api_list_smart_classroom_sync_capabilities(user: dict = Depends(get_current_teacher)):
    """Return syncable Smart Classroom features and their latest local sync state."""
    with get_db_connection() as conn:
        capabilities = build_smart_classroom_sync_capabilities(conn, int(user["id"]))
    return {"status": "success", "capabilities": capabilities}


@router.post("/system/smart-classroom-sync", response_class=JSONResponse)
async def api_sync_smart_classroom_data(user: dict = Depends(get_current_teacher)):
    """Manually sync Smart Classroom check-in records."""
    result = await sync_teacher_smart_classroom_checkins(int(user["id"]))
    return {
        "status": result.get("status") or "unknown",
        "message": result.get("message") or "智慧课堂点名同步已完成。",
        "result": result,
        "auto_sync": {
            "status": result.get("status") or "unknown",
            "message": result.get("message") or "",
            "stages": [
                {
                    "key": "checkins",
                    "label": "点名记录",
                    "status": result.get("status") or "unknown",
                    "message": result.get("message") or "",
                    "counts": result.get("counts") or {},
                    "warnings": result.get("warnings") or [],
                }
            ],
        },
    }


@router.post("/system/smart-classroom-credentials", response_class=JSONResponse)
async def api_save_smart_classroom_credential(request: Request, user: dict = Depends(get_current_teacher)):
    """Verify and save a Smart Classroom credential for later sync jobs."""
    payload = await _parse_json_request(request)

    try:
        verification = await verify_smart_classroom_credential(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not verification.get("ok"):
        raise HTTPException(status_code=400, detail=verification.get("message") or "智慧课堂账号校验失败。")

    with get_db_connection() as conn:
        try:
            credential = save_verified_smart_classroom_credential(conn, int(user["id"]), payload, verification)
            credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    auto_sync = await sync_teacher_smart_classroom_data_after_credential_verified(int(user["id"]))

    return {
        "status": "success",
        "message": auto_sync.get("message") or "智慧课堂账号已验证并保存。",
        "verification": verification,
        "credential": credential,
        "credentials": credentials,
        "auto_sync": auto_sync,
    }


@router.post("/system/smart-classroom-credentials/{credential_id}/verify", response_class=JSONResponse)
async def api_verify_smart_classroom_credential(credential_id: int, user: dict = Depends(get_current_teacher)):
    """Re-verify a saved Smart Classroom credential."""
    with get_db_connection() as conn:
        try:
            row = get_teacher_smart_classroom_credential(conn, int(user["id"]), credential_id)
            payload = build_saved_smart_classroom_verification_payload(row)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        verification = await verify_smart_classroom_credential(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with get_db_connection() as conn:
        try:
            credential = update_smart_classroom_credential_verification_status(
                conn,
                int(user["id"]),
                credential_id,
                verification,
            )
            credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    auto_sync = None
    if verification.get("ok"):
        auto_sync = await sync_teacher_smart_classroom_data_after_credential_verified(int(user["id"]))

    return {
        "status": "success" if verification.get("ok") else "failed",
        "message": (
            auto_sync.get("message")
            if auto_sync
            else verification.get("message") or "智慧课堂连接校验完成。"
        ),
        "verification": verification,
        "credential": credential,
        "credentials": credentials,
        "auto_sync": auto_sync,
    }


@router.delete("/system/smart-classroom-credentials/{credential_id}", response_class=JSONResponse)
async def api_delete_smart_classroom_credential(credential_id: int, user: dict = Depends(get_current_teacher)):
    """Delete a saved Smart Classroom credential for the current teacher."""
    with get_db_connection() as conn:
        try:
            removed_count = delete_teacher_smart_classroom_credential(conn, int(user["id"]), credential_id)
            credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "智慧课堂对接已删除。",
        "removed_count": removed_count,
        "credentials": credentials,
    }


# --------------------------------------------------------------------------- #
# 校园公文通 (official-document) integration
# --------------------------------------------------------------------------- #


@router.get("/system/gongwen-credentials", response_class=JSONResponse)
async def api_list_gongwen_credentials(user: dict = Depends(get_current_teacher)):
    """List the current teacher's saved 校园公文通 credentials."""
    with get_db_connection() as conn:
        credentials = list_teacher_gongwen_credentials(conn, int(user["id"]))
    return {"status": "success", "credentials": credentials}


@router.get("/system/gongwen-sync-capabilities", response_class=JSONResponse)
async def api_list_gongwen_sync_capabilities(user: dict = Depends(get_current_teacher)):
    """Return the syncable 公文 features and their latest local sync state."""
    with get_db_connection() as conn:
        capabilities = build_gongwen_sync_capabilities(conn, int(user["id"]))
    return {"status": "success", "capabilities": capabilities}


@router.post("/system/gongwen-sync", response_class=JSONResponse)
async def api_sync_gongwen_documents(user: dict = Depends(get_current_teacher)):
    """Manually rerun the 公文 document sync with the saved credential."""
    auto_sync = await sync_teacher_gongwen_data_after_credential_verified(int(user["id"]))
    return {
        "status": auto_sync.get("status") or "unknown",
        "message": auto_sync.get("message") or "公文同步已完成。",
        "auto_sync": auto_sync,
    }


@router.post("/system/gongwen-credentials", response_class=JSONResponse)
async def api_save_gongwen_credential(request: Request, user: dict = Depends(get_current_teacher)):
    """Verify (login via unified-auth + captcha) and save a 公文 credential."""
    payload = await _parse_json_request(request)
    try:
        verification = await verify_gongwen_credential(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not verification.get("ok"):
        raise HTTPException(status_code=400, detail=verification.get("message") or "校园公文通账号校验失败。")

    with get_db_connection() as conn:
        try:
            credential = save_verified_gongwen_credential(conn, int(user["id"]), payload, verification)
            schedule_gongwen_auto_sync(conn, int(user["id"]))
            credentials = list_teacher_gongwen_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    auto_sync = await sync_teacher_gongwen_data_after_credential_verified(int(user["id"]))

    return {
        "status": "success",
        "message": auto_sync.get("message") or "校园公文通账号已验证并保存。",
        "verification": verification,
        "credential": credential,
        "credentials": credentials,
        "auto_sync": auto_sync,
    }


@router.post("/system/gongwen-credentials/{credential_id}/verify", response_class=JSONResponse)
async def api_verify_gongwen_credential(credential_id: int, user: dict = Depends(get_current_teacher)):
    """Re-verify a saved 公文 credential using the stored encrypted password."""
    with get_db_connection() as conn:
        try:
            row = get_teacher_gongwen_credential(conn, int(user["id"]), credential_id)
            payload = build_saved_gongwen_verification_payload(row)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        verification = await verify_gongwen_credential(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with get_db_connection() as conn:
        try:
            credential = update_gongwen_credential_verification_status(
                conn, int(user["id"]), credential_id, verification
            )
            if verification.get("ok"):
                schedule_gongwen_auto_sync(conn, int(user["id"]))
            credentials = list_teacher_gongwen_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    auto_sync = None
    if verification.get("ok"):
        auto_sync = await sync_teacher_gongwen_data_after_credential_verified(int(user["id"]))

    return {
        "status": "success" if verification.get("ok") else "failed",
        "message": (
            auto_sync.get("message")
            if auto_sync
            else verification.get("message") or "校园公文通连接校验完成。"
        ),
        "verification": verification,
        "credential": credential,
        "credentials": credentials,
        "auto_sync": auto_sync,
    }


@router.delete("/system/gongwen-credentials/{credential_id}", response_class=JSONResponse)
async def api_delete_gongwen_credential(credential_id: int, user: dict = Depends(get_current_teacher)):
    """Delete one of the current teacher's 公文 credentials."""
    with get_db_connection() as conn:
        try:
            removed_count = delete_teacher_gongwen_credential(conn, int(user["id"]), credential_id)
            # Stop the recurring sync once the teacher has no 公文 credential left.
            if not list_teacher_gongwen_credentials(conn, int(user["id"])):
                cancel_gongwen_auto_sync(conn, int(user["id"]))
            credentials = list_teacher_gongwen_credentials(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "校园公文通对接已删除。",
        "removed_count": removed_count,
        "credentials": credentials,
    }


def _gongwen_viewer(conn, user: dict) -> tuple[dict[str, str], bool]:
    """Resolve the requesting teacher's org scope + super-admin flag."""
    teacher_id = int(user["id"])
    return load_teacher_org_scope(conn, teacher_id), bool(is_super_admin_teacher(conn, teacher_id))


@router.get("/gongwen/documents", response_class=JSONResponse)
async def api_list_gongwen_documents(
    request: Request,
    keyword: str = "",
    category: str = "",
    author: str = "",
    sender: str = "",
    has_attachment: int = 0,
    unread: int = 0,
    favorite: int = 0,
    limit: int = 20,
    offset: int = 0,
    with_facets: int = 0,
    user: dict = Depends(get_current_teacher),
):
    """List campus-visible 公文 for the teacher (归属 + 开放范围 filtered)."""
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    with get_db_connection() as conn:
        scope, is_admin = _gongwen_viewer(conn, user)
        result = list_visible_gongwen_documents(
            conn,
            scope,
            is_super_admin=is_admin,
            keyword=keyword,
            category=category,
            author=author,
            sender=sender,
            has_attachment=bool(int(has_attachment or 0)),
            unread_only=bool(int(unread or 0)),
            favorite_only=bool(int(favorite or 0)),
            limit=limit,
            offset=offset,
        )
        summary = count_visible_gongwen_documents(conn, scope, is_super_admin=is_admin)
        facets = build_gongwen_facets(conn, scope, is_super_admin=is_admin) if int(with_facets or 0) else None
    payload = {
        "status": "success",
        "documents": result["documents"],
        "total": result["total"],
        "summary": summary,
    }
    if facets is not None:
        payload["facets"] = facets
        payload["categories"] = facets["categories"]
    return payload


@router.get("/gongwen/documents/{document_id}/reader", response_class=JSONResponse)
async def api_gongwen_document_reader(
    document_id: int,
    refresh: int = 0,
    user: dict = Depends(get_current_teacher),
):
    """Parsed, in-page reader view of a 公文 (正文 + 附件 文本/表格/PDF)."""
    with get_db_connection() as conn:
        scope, is_admin = _gongwen_viewer(conn, user)
    reader = await build_gongwen_document_reader(
        scope, int(document_id), is_super_admin=is_admin, refresh=bool(int(refresh or 0))
    )
    if reader is None:
        raise HTTPException(status_code=404, detail="公文不存在或无权访问。")
    return {"status": "success", "document": reader}


@router.get("/gongwen/documents/search", response_class=JSONResponse)
async def api_search_gongwen_documents(
    q: str = "",
    category: str = "",
    limit: int = 20,
    user: dict = Depends(get_current_teacher),
):
    """Keyword search interface across visible documents (reminders/retrieval)."""
    limit = max(1, min(int(limit or 20), 100))
    with get_db_connection() as conn:
        scope, is_admin = _gongwen_viewer(conn, user)
        documents = search_visible_gongwen_documents(conn, scope, q, is_super_admin=is_admin, category=category, limit=limit)
    return {"status": "success", "documents": documents}


@router.get("/gongwen/scope-options", response_class=JSONResponse)
async def api_gongwen_scope_options(user: dict = Depends(get_current_teacher)):
    """Org options (current teacher college/department) + openness levels per
    attribution level, for the 归属/开放 editor."""
    with get_db_connection() as conn:
        scope = load_teacher_org_scope(conn, int(user["id"]))
    return {
        "status": "success",
        "teacher_org": {
            "school_code": scope.get("school_code", ""),
            "school_name": scope.get("school_name", ""),
            "college": scope.get("college", ""),
            "department": scope.get("department", ""),
        },
        "openness_by_level": {
            "school": gongwen_openness_options("school"),
            "college": gongwen_openness_options("college"),
            "department": gongwen_openness_options("department"),
        },
    }


@router.get("/gongwen/documents/{document_id}", response_class=JSONResponse)
async def api_get_gongwen_document(document_id: int, user: dict = Depends(get_current_teacher)):
    """Return one visible document with full content — retrieval for reminders."""
    with get_db_connection() as conn:
        scope, is_admin = _gongwen_viewer(conn, user)
        document = get_visible_gongwen_document(conn, scope, document_id, is_super_admin=is_admin)
    if document is None:
        raise HTTPException(status_code=404, detail="公文不存在或无权访问。")
    return {"status": "success", "document": document}


@router.post("/gongwen/documents/{document_id}/scope", response_class=JSONResponse)
async def api_set_gongwen_document_scope(document_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    """Set a document's 归属 (学院/系部) and 开放范围 (campus-restricted)."""
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        scope, is_admin = _gongwen_viewer(conn, user)
        try:
            document = set_gongwen_document_scope(
                conn,
                scope,
                int(document_id),
                college=payload.get("college"),
                department=payload.get("department"),
                openness=payload.get("openness"),
                is_super_admin=is_admin,
            )
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "message": "已更新公文归属与开放范围。", "document": document}


@router.get("/gongwen/documents/{document_id}/file")
async def api_download_gongwen_document_file(
    document_id: int,
    which: str = "primary",
    user: dict = Depends(get_current_teacher),
):
    """Serve the document attachment: cache the public CDN file locally on first
    access, then stream it; fall back to a host-validated redirect on failure."""
    from fastapi.responses import RedirectResponse

    which = "attachment" if str(which) == "attachment" else "primary"
    with get_db_connection() as conn:
        scope, is_admin = _gongwen_viewer(conn, user)
    result = await ensure_local_attachment(scope, int(document_id), which, is_super_admin=is_admin)
    status = result.get("status")
    if status == "not_found":
        raise HTTPException(status_code=404, detail="公文不存在或无权访问。")
    if status == "no_file":
        raise HTTPException(status_code=404, detail="该公文暂无可下载的附件。")
    if status == "local":
        path = Path(result["local_path"])
        return FileResponse(str(path), filename=path.name)
    # status == "redirect": cache failed but the CDN file is public.
    return RedirectResponse(url=str(result.get("remote_url")), status_code=302)
