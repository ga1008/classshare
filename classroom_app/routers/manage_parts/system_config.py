from .common import *
from ...services.background_task_ledger_service import build_background_task_ledger_snapshot


router = APIRouter()


@router.get("/system/background-tasks", response_class=JSONResponse)
async def api_get_background_tasks(user: dict = Depends(get_current_teacher)):
    """Return the unified background task ledger for super-admin diagnostics."""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        snapshot = build_background_task_ledger_snapshot(conn)
    return {"status": "success", **snapshot}


@router.get("/system/password-resets/{request_id}", response_class=JSONResponse)
async def api_get_password_reset_request_detail(
    request_id: int,
    user: dict = Depends(get_current_teacher),
):
    """查看单个找回密码申请详情及学生历史登录信息。"""
    with get_db_connection() as conn:
        request_row = conn.execute(
            """
            SELECT r.*,
                   s.name AS student_name,
                   s.student_id_number,
                   s.password_reset_required,
                   s.password_updated_at,
                   CASE WHEN s.hashed_password IS NULL OR s.hashed_password = '' THEN 0 ELSE 1 END AS has_password,
                   c.name AS current_class_name,
                   reviewer.name AS reviewer_name
            FROM student_password_reset_requests r
            JOIN students s ON s.id = r.student_id
            JOIN classes c ON c.id = r.class_id
            LEFT JOIN teachers reviewer ON reviewer.id = r.reviewed_by_teacher_id
            WHERE r.id = ?
              AND (
                    ? = 1
                    OR r.teacher_id = ?
                    OR c.created_by_teacher_id = ?
                    OR EXISTS (
                        SELECT 1 FROM class_offerings o
                        WHERE o.class_id = r.class_id
                          AND o.teacher_id = ?
                    )
              )
            """,
            (request_id, 1 if is_super_admin_teacher(conn, user["id"]) else 0, user["id"], user["id"], user["id"]),
        ).fetchone()

        if not request_row:
            raise HTTPException(status_code=404, detail="找回密码申请不存在。")

        login_history = list_student_login_history(conn, request_row["student_id"], limit=20)
        security_summary = build_student_security_summary(conn, request_row["student_id"])

    return {
        "status": "success",
        "request": dict(request_row),
        "login_history": login_history,
        "security_summary": security_summary,
    }


@router.post("/system/super-admin", response_class=JSONResponse)
async def api_update_super_admin_teacher(
    teacher_id: int = Form(...),
    user: dict = Depends(get_current_teacher),
):
    """兼容旧入口：为教师授予超管权限。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user, "只有当前超管教师可以调整超管身份。")
        try:
            teacher = grant_teacher_super_admin(conn, teacher_id=teacher_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()

    return {
        "status": "success",
        "message": f"已授予 {teacher['name']} 超管权限。",
        "teacher": teacher,
    }


@router.get("/system/organizations/tree", response_class=JSONResponse)
async def api_list_organization_tree(
    q: str = "",
    include_inactive: int = 0,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        return {
            "status": "success",
            **list_organization_tree(
                conn,
                query=q,
                include_inactive=bool(int(include_inactive or 0)),
            ),
        }


@router.get("/system/organizations/schools", response_class=JSONResponse)
async def api_list_organization_school_options(
    q: str = "",
    include_inactive: int = 0,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        return {
            "status": "success",
            "items": list_school_options(
                conn,
                query=q,
                include_inactive=bool(int(include_inactive or 0)),
            ),
        }


@router.post("/system/organizations/schools", response_class=JSONResponse)
async def api_create_organization_school(request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = create_school(
                conn,
                school_code=str(payload.get("school_code") or ""),
                school_name=str(payload.get("school_name") or ""),
                display_order=int(payload.get("display_order") or 0),
                actor_teacher_id=int(user["id"]),
            )
        except (OrganizationManagementError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "学校已保存。", "item": item}


@router.patch("/system/organizations/schools/{school_id:int}", response_class=JSONResponse)
async def api_update_organization_school(
    school_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = update_school(
                conn,
                school_id=school_id,
                school_name=str(payload.get("school_name") or ""),
                display_order=int(payload.get("display_order") or 0),
                is_active=_form_bool(payload.get("is_active", "1")),
                actor_teacher_id=int(user["id"]),
            )
        except (OrganizationManagementError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "学校已更新。", "item": item}


@router.delete("/system/organizations/schools/{school_id:int}", response_class=JSONResponse)
async def api_delete_organization_school(school_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = delete_school(conn, school_id=school_id, actor_teacher_id=int(user["id"]))
        except OrganizationManagementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "学校已停用，历史资源不会被删除。", "item": item}


@router.post("/system/organizations/colleges", response_class=JSONResponse)
async def api_create_organization_college(request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = create_college(
                conn,
                school_code=str(payload.get("school_code") or ""),
                college_name=str(payload.get("college_name") or ""),
                display_order=int(payload.get("display_order") or 0),
                actor_teacher_id=int(user["id"]),
            )
        except (OrganizationManagementError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "学院已保存。", "item": item}


@router.patch("/system/organizations/colleges/{college_id:int}", response_class=JSONResponse)
async def api_update_organization_college(
    college_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = update_college(
                conn,
                college_id=college_id,
                college_name=str(payload.get("college_name") or ""),
                display_order=int(payload.get("display_order") or 0),
                is_active=_form_bool(payload.get("is_active", "1")),
                actor_teacher_id=int(user["id"]),
            )
        except (OrganizationManagementError, ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "学院已更新。", "item": item}


@router.delete("/system/organizations/colleges/{college_id:int}", response_class=JSONResponse)
async def api_delete_organization_college(college_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = delete_college(conn, college_id=college_id, actor_teacher_id=int(user["id"]))
        except OrganizationManagementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "学院已停用，历史资源不会被删除。", "item": item}


@router.post("/system/organizations/departments", response_class=JSONResponse)
async def api_create_organization_department(request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = create_department(
                conn,
                school_code=str(payload.get("school_code") or ""),
                college_name=str(payload.get("college_name") or ""),
                department_name=str(payload.get("department_name") or ""),
                display_order=int(payload.get("display_order") or 0),
                actor_teacher_id=int(user["id"]),
            )
        except (OrganizationManagementError, ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "系部已保存。", "item": item}


@router.patch("/system/organizations/departments/{department_id:int}", response_class=JSONResponse)
async def api_update_organization_department(
    department_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = update_department(
                conn,
                department_id=department_id,
                department_name=str(payload.get("department_name") or ""),
                display_order=int(payload.get("display_order") or 0),
                is_active=_form_bool(payload.get("is_active", "1")),
                actor_teacher_id=int(user["id"]),
            )
        except (OrganizationManagementError, ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "系部已更新。", "item": item}


@router.delete("/system/organizations/departments/{department_id:int}", response_class=JSONResponse)
async def api_delete_organization_department(department_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            item = delete_department(conn, department_id=department_id, actor_teacher_id=int(user["id"]))
        except OrganizationManagementError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "系部已停用，历史资源不会被删除。", "item": item}


@router.get("/system/agent-keys/status", response_class=JSONResponse)
async def api_get_agent_key_dashboard(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        dashboard = build_agent_key_dashboard(conn)
    return {"status": "success", "dashboard": dashboard}


@router.post("/system/agent-keys", response_class=JSONResponse)
async def api_create_agent_key(request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _parse_json_request(request)
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            result = await create_agent_api_key(conn, payload, teacher_id=int(user["id"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()

    status_value = "success" if result.get("saved") else "warning"
    return {
        "status": status_value,
        "message": result.get("message") or ("Agent API Key 已保存。" if result.get("saved") else "Agent API Key 测试失败，未保存。"),
        **result,
    }


@router.post("/system/agent-keys/{key_id}/test", response_class=JSONResponse)
async def api_test_agent_key(key_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            result = await test_saved_agent_api_key(conn, key_id, teacher_id=int(user["id"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()

    test_status = (result.get("test_result") or {}).get("status")
    return {
        "status": "success" if test_status == "valid" else "warning",
        "message": result.get("message") or "测试完成。",
        **result,
    }


@router.post("/system/agent-keys/{key_id}/activate", response_class=JSONResponse)
async def api_activate_agent_key(key_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            result = set_active_agent_api_key(conn, key_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", **result}


@router.delete("/system/agent-keys/{key_id}", response_class=JSONResponse)
async def api_delete_agent_key(key_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            result = delete_agent_api_key(conn, key_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", **result}


@router.post("/system/agent-keys/usage/refresh", response_class=JSONResponse)
async def api_refresh_agent_runtime_usage(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        usage = await fetch_agent_runtime_usage(conn, teacher_id=int(user["id"]))
        conn.commit()
        dashboard = build_agent_key_dashboard(conn)
    return {"status": usage.get("status") or "success", "usage": usage, "dashboard": dashboard}


@router.post("/system/teachers", response_class=JSONResponse)
async def api_create_teacher_account(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    is_super_admin: str = Form(default=""),
    school_code: str = Form(default=""),
    school_name: str = Form(default=""),
    college: str = Form(default=""),
    department: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """新增教师账号，仅超管可用。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            teacher = create_teacher_account(
                conn,
                actor_teacher_id=int(user["id"]),
                name=name,
                email=email,
                password=password,
                is_super_admin=_form_bool(is_super_admin),
                school_code=school_code,
                school_name=school_name,
                college=college,
                department=department,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "教师账号已创建。", "teacher": teacher}


@router.post("/system/teachers/{teacher_id}", response_class=JSONResponse)
async def api_update_teacher_account(
    teacher_id: int,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(default=""),
    wechat: str = Form(default=""),
    qq: str = Form(default=""),
    homepage_url: str = Form(default=""),
    description: str = Form(default=""),
    school_code: str = Form(default=""),
    school_name: str = Form(default=""),
    college: str = Form(default=""),
    department: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """修改教师账号资料，仅超管可用。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            teacher = update_teacher_account(
                conn,
                teacher_id=teacher_id,
                name=name,
                email=email,
                phone=phone,
                wechat=wechat,
                qq=qq,
                homepage_url=homepage_url,
                description=description,
                school_code=school_code,
                school_name=school_name,
                college=college,
                department=department,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "教师资料已更新。", "teacher": teacher}


@router.post("/system/teachers/{teacher_id}/memberships", response_class=JSONResponse)
async def api_upsert_teacher_membership(
    teacher_id: int,
    school_code: str = Form(default=""),
    school_name: str = Form(default=""),
    college: str = Form(default=""),
    department: str = Form(default=""),
    is_primary: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """为教师新增或恢复一个学校任教归属；同一学校只保留一个系部归属。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            membership = upsert_teacher_membership(
                conn,
                teacher_id=teacher_id,
                school_code=school_code,
                school_name=school_name,
                college=college,
                department=department,
                is_primary=_form_bool(is_primary),
                actor_teacher_id=int(user["id"]),
            )
            teacher = get_teacher_account(conn, teacher_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "任教归属已保存。", "membership": membership, "teacher": teacher}


@router.post("/system/teachers/{teacher_id}/memberships/{membership_id}/primary", response_class=JSONResponse)
async def api_set_teacher_primary_membership(
    teacher_id: int,
    membership_id: int,
    user: dict = Depends(get_current_teacher),
):
    """设置教师默认任教归属，并同步教师主档案上的组织字段。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            membership = set_teacher_primary_membership(
                conn,
                teacher_id=teacher_id,
                membership_id=membership_id,
            )
            teacher = get_teacher_account(conn, teacher_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "默认任教归属已更新。", "membership": membership, "teacher": teacher}


@router.delete("/system/teachers/{teacher_id}/memberships/{membership_id}", response_class=JSONResponse)
async def api_deactivate_teacher_membership(
    teacher_id: int,
    membership_id: int,
    user: dict = Depends(get_current_teacher),
):
    """停用教师的一个任教归属；至少保留一个启用归属。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            membership = deactivate_teacher_membership(
                conn,
                teacher_id=teacher_id,
                membership_id=membership_id,
                actor_teacher_id=int(user["id"]),
            )
            teacher = get_teacher_account(conn, teacher_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": "任教归属已停用。", "membership": membership, "teacher": teacher}


@router.post("/system/teachers/{teacher_id}/reset-password", response_class=JSONResponse)
async def api_reset_teacher_account_password(
    teacher_id: int,
    password: str = Form(...),
    user: dict = Depends(get_current_teacher),
):
    """重置教师账号密码，仅超管可用。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            teacher = reset_teacher_password(conn, teacher_id=teacher_id, password=password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()

    invalidate_session_for_user(str(teacher_id), "teacher")
    return {
        "status": "success",
        "message": f"已重置 {teacher['name']} 的密码，并清理其当前登录会话。",
        "teacher": teacher,
        "password_hint": TEACHER_PASSWORD_HINT,
    }


@router.post("/system/teachers/{teacher_id}/super-admin/grant", response_class=JSONResponse)
async def api_grant_teacher_account_super_admin(
    teacher_id: int,
    user: dict = Depends(get_current_teacher),
):
    """授予教师超管权限，仅超管可用。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            teacher = grant_teacher_super_admin(conn, teacher_id=teacher_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"status": "success", "message": f"已授予 {teacher['name']} 超管权限。", "teacher": teacher}


@router.post("/system/teachers/{teacher_id}/super-admin/revoke", response_class=JSONResponse)
async def api_revoke_teacher_account_super_admin(
    teacher_id: int,
    user: dict = Depends(get_current_teacher),
):
    """撤销教师超管权限，仅超管可用。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            teacher = revoke_teacher_super_admin(conn, teacher_id=teacher_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()

    if int(teacher_id) == int(user["id"]):
        invalidate_session_for_user(str(teacher_id), "teacher")
    return {"status": "success", "message": f"已撤销 {teacher['name']} 的超管权限。", "teacher": teacher}


@router.delete("/system/teachers/{teacher_id}", response_class=JSONResponse)
async def api_delete_teacher_account(
    teacher_id: int,
    user: dict = Depends(get_current_teacher),
):
    """删除教师账号：停用登录，保留历史教学数据。"""
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        try:
            teacher = deactivate_teacher_account(
                conn,
                teacher_id=teacher_id,
                actor_teacher_id=int(user["id"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()

    invalidate_session_for_user(str(teacher_id), "teacher")
    return {
        "status": "success",
        "message": f"已删除 {teacher['name']} 的登录账号，历史教学数据已保留。",
        "teacher": teacher,
    }


@router.get("/system/blog-crawler/status", response_class=JSONResponse)
async def api_get_blog_news_crawler_status(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        dashboard = load_blog_news_crawler_dashboard(conn)
        dashboard["current_teacher_is_super_admin"] = is_super_admin_teacher(conn, user["id"])
    return {"status": "success", "dashboard": dashboard}


@router.post("/system/blog-crawler/config", response_class=JSONResponse)
async def api_update_blog_news_crawler_config(request: Request, user: dict = Depends(get_current_teacher)):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="请求数据格式不正确。")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求数据格式不正确。")

    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        config = update_blog_news_crawler_config(conn, payload, teacher_id=user["id"])
        conn.commit()
    return {"status": "success", "message": "AI 博客管家设置已保存。", "config": config}


@router.post("/system/blog-crawler/run", response_class=JSONResponse)
async def api_enqueue_blog_news_crawler_run(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        run = enqueue_blog_news_crawler_run(conn, trigger_source="manual")
        conn.commit()
    return {"status": "success", "message": "已加入执行队列。", "run": run}


@router.post("/system/blog-crawler/cancel-pending", response_class=JSONResponse)
async def api_cancel_blog_news_crawler_pending_runs(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_current_super_admin(conn, user)
        count = cancel_pending_blog_news_crawler_runs(conn)
        conn.commit()
    return {"status": "success", "message": f"已取消 {count} 个待执行任务。", "cancelled_count": count}


@router.post("/system/password-resets/{request_id}/approve", response_class=JSONResponse)
async def api_approve_password_reset_request(
    request_id: int,
    review_note: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """教师通过学生找回密码申请。"""
    with get_db_connection() as conn:
        request_row = conn.execute(
            """
            SELECT r.id, r.student_id, r.teacher_id, r.status
            FROM student_password_reset_requests r
            JOIN classes c ON c.id = r.class_id
            WHERE r.id = ?
              AND (
                    ? = 1
                    OR r.teacher_id = ?
                    OR c.created_by_teacher_id = ?
                    OR EXISTS (
                        SELECT 1 FROM class_offerings o
                        WHERE o.class_id = r.class_id
                          AND o.teacher_id = ?
                    )
              )
            """,
            (request_id, 1 if is_super_admin_teacher(conn, user["id"]) else 0, user["id"], user["id"], user["id"]),
        ).fetchone()
        if not request_row:
            raise HTTPException(status_code=404, detail="找回密码申请不存在。")
        if request_row["status"] != "pending":
            raise HTTPException(status_code=400, detail="该申请当前不能再执行通过操作。")

        reviewed_at = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE student_password_reset_requests
            SET status = 'approved', reviewed_at = ?, reviewed_by_teacher_id = ?, review_note = ?
            WHERE id = ?
            """,
            (reviewed_at, user["id"], review_note.strip(), request_id),
        )
        conn.execute(
            """
            UPDATE students
            SET password_reset_required = 1
            WHERE id = ?
            """,
            (request_row["student_id"],),
        )
        mark_password_reset_request_notification_read(conn, request_id, user["id"])
        invalidate_session_for_user(str(request_row["student_id"]), "student", conn=conn)
        conn.commit()

    return {
        "status": "success",
        "message": "已通过该申请，学生可重新使用姓名和学号登录并设置新密码。",
    }


@router.post("/system/password-resets/{request_id}/reject", response_class=JSONResponse)
async def api_reject_password_reset_request(
    request_id: int,
    review_note: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """教师拒绝学生找回密码申请。"""
    with get_db_connection() as conn:
        request_row = conn.execute(
            """
            SELECT r.id, r.status
            FROM student_password_reset_requests r
            JOIN classes c ON c.id = r.class_id
            WHERE r.id = ?
              AND (
                    ? = 1
                    OR r.teacher_id = ?
                    OR c.created_by_teacher_id = ?
                    OR EXISTS (
                        SELECT 1 FROM class_offerings o
                        WHERE o.class_id = r.class_id
                          AND o.teacher_id = ?
                    )
              )
            """,
            (request_id, 1 if is_super_admin_teacher(conn, user["id"]) else 0, user["id"], user["id"], user["id"]),
        ).fetchone()
        if not request_row:
            raise HTTPException(status_code=404, detail="找回密码申请不存在。")
        if request_row["status"] != "pending":
            raise HTTPException(status_code=400, detail="该申请当前不能再执行拒绝操作。")

        conn.execute(
            """
            UPDATE student_password_reset_requests
            SET status = 'rejected', reviewed_at = ?, reviewed_by_teacher_id = ?, review_note = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), user["id"], review_note.strip(), request_id),
        )
        mark_password_reset_request_notification_read(conn, request_id, user["id"])
        conn.commit()

    return {"status": "success", "message": "已拒绝该找回密码申请。"}


@router.post("/system/repair-submission-files", response_class=JSONResponse)
async def api_repair_submission_files(user: dict = Depends(get_current_teacher)):
    """Repair stale stored_path entries and recover orphaned submission files.

    This is an administrative action that:
    1. Fixes stored_path values that point to wrong drives / directories
    2. Discovers files on disk with no DB record and reconstructs entries
    """
    try:
        with get_db_connection() as conn:
            _require_current_super_admin(conn, user)
            report = run_full_alignment(conn)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, f"修复失败: {exc}")

    return {
        "status": "success",
        "message": (
            f"路径修复: {report['stale_path_repair']['paths_repaired']} 条已修复, "
            f"{report['stale_path_repair']['paths_still_missing']} 条仍缺失; "
            f"孤立文件恢复: {report['orphan_recovery']['orphan_files_recovered']} 个文件已恢复, "
            f"{report['orphan_recovery']['orphan_submissions_created']} 条提交记录已重建"
        ),
        "report": report,
    }
