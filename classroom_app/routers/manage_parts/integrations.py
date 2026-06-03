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
