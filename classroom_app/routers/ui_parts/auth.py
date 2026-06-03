from .common import *


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def root(request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    """根目录，根据登录状态重定向到仪表盘或学生登录页"""
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/student/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/student/login", response_class=HTMLResponse)
async def student_login_page(request: Request, next: Optional[str] = None):
    # V4.0 不再需要 class_name 和 course_name
    return templates.TemplateResponse(
        request,
        "student_login_v4.html",
        _build_login_page_context(request, next),
    )


@router.get("/teacher/login", response_class=HTMLResponse)
async def teacher_login_page(request: Request, next: Optional[str] = None):
    return templates.TemplateResponse(
        request,
        "teacher_login_v4.html",
        _build_login_page_context(request, next),
    )


@router.get("/teacher/register", response_class=HTMLResponse)
async def teacher_register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "request": request,
            "success": False,
            "message": "教师账号已改为由超管教师统一创建，请联系系统超管开通账号。",
            "back_url": "/teacher/login",
        },
        status_code=status.HTTP_403_FORBIDDEN,
    )


@router.get("/auth/forbidden", response_class=HTMLResponse)
async def permission_warning_page(
    request: Request,
    next: Optional[str] = None,
    required_role: Optional[str] = None,
    user: Optional[dict] = Depends(get_current_user_optional),
):
    safe_next = sanitize_next_path(next, fallback="/dashboard")
    effective_required_role = required_role or infer_required_role_from_path(safe_next.split("?", 1)[0])
    if not user:
        login_path = "/teacher/login" if effective_required_role == "teacher" else "/student/login"
        response = RedirectResponse(
            url=build_login_url(login_path, safe_next),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        clear_access_token_cookie(response)
        return response

    user_hint = user
    current_role = user_hint.get("role")

    return templates.TemplateResponse(request, "permission_denied.html", {
        "request": request,
        "next_url": safe_next,
        "current_user": user_hint,
        "current_role_label": get_role_label(current_role),
        "required_role": effective_required_role,
        "required_role_label": get_role_label(effective_required_role) if effective_required_role else "",
        "teacher_login_url": build_login_url("/teacher/login", safe_next),
        "student_login_url": build_login_url("/student/login", safe_next),
        "dashboard_url": "/dashboard" if user_hint else "/",
        "show_teacher_login": True,
        "permission_message": "当前账号已登录，但没有访问该页面或资源的权限。",
    })


@router.post("/api/student/login/password", response_class=JSONResponse)
def api_student_password_login(
    request: Request,
    identifier: str = Form(),
    password: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """学生密码登录（姓名或学号 + 密码）。"""
    safe_next = sanitize_next_path(next, fallback="/dashboard")
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    with get_db_connection() as conn:
        student_row, login_count = _perform_student_password_login(
            conn,
            identifier=identifier.strip(),
            password=password,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        conn.commit()

    return _build_student_login_json_response(
        student_row=student_row,
        client_ip=client_ip,
        safe_next=safe_next,
        login_count=login_count,
    )


@router.post("/api/student/login/identity", response_class=JSONResponse)
def api_student_identity_login(
    request: Request,
    name: str = Form(),
    student_id_number: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """学生首次登录/找回密码后重设密码前的身份核验。"""
    safe_next = sanitize_next_path(next, fallback="/dashboard")

    with get_db_connection() as conn:
        student_row = get_student_auth_record_by_identity(conn, name, student_id_number)
        if not student_row:
            raise HTTPException(status_code=400, detail="登录失败：姓名或学号错误。")
        _ensure_student_can_login(student_row)
        if not can_student_use_identity_login(student_row):
            raise HTTPException(status_code=409, detail="该账号已设置密码，请使用密码登录。")

        flow_type = "first_login"
        approved_request = None
        if student_row["password_reset_required"]:
            flow_type = "password_reset"
            approved_request = conn.execute(
                """
                SELECT id
                FROM student_password_reset_requests
                WHERE student_id = ? AND status = 'approved'
                ORDER BY reviewed_at DESC, id DESC
                LIMIT 1
                """,
                (student_row["id"],),
            ).fetchone()

        setup_token = build_password_setup_token(
            student_id=student_row["id"],
            next_path=safe_next,
            flow_type=flow_type,
            reset_request_id=approved_request["id"] if approved_request else None,
        )

    return {
        "status": "success",
        "message": "身份核验通过，请先设置登录密码。",
        "setup_token": setup_token,
        "flow_type": flow_type,
        "password_policy_hint": PASSWORD_POLICY_HINT,
        "student": {
            "name": student_row["name"],
            "student_id_number": student_row["student_id_number"],
            "class_name": student_row["class_name"],
        },
    }


@router.post("/api/student/password/setup", response_class=JSONResponse)
def api_student_password_setup(
    request: Request,
    setup_token: str = Form(),
    password: str = Form(),
    confirm_password: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """完成学生首次设密或重置后设密，并自动登录。"""
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致。")

    password_error = validate_student_password(password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    token_payload = decode_password_setup_token(setup_token)
    if not token_payload:
        raise HTTPException(status_code=400, detail="设密凭证已失效，请重新进行身份验证。")
    if not token_payload.get("student_id"):
        raise HTTPException(status_code=400, detail="设密凭证无效，请重新进行身份验证。")

    safe_next = sanitize_next_path(next or token_payload.get("next"), fallback="/dashboard")
    flow_type = str(token_payload.get("flow_type") or "first_login")
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    with get_db_connection() as conn:
        student_row = get_student_auth_record_by_pk(conn, int(token_payload["student_id"]))
        if not student_row:
            raise HTTPException(status_code=404, detail="学生账号不存在。")
        _ensure_student_can_login(student_row)

        if flow_type == "password_reset":
            if not student_row["password_reset_required"]:
                raise HTTPException(status_code=400, detail="当前账号无需重置密码，请直接使用密码登录。")
        elif student_row["hashed_password"] and not student_row["password_reset_required"]:
            raise HTTPException(status_code=400, detail="该账号已设置密码，请直接使用密码登录。")

        conn.execute(
            """
            UPDATE students
            SET hashed_password = ?, password_reset_required = 0, password_updated_at = ?
            WHERE id = ?
            """,
            (get_password_hash(password), datetime.now().isoformat(), student_row["id"]),
        )

        if flow_type == "password_reset":
            mark_latest_approved_reset_request_completed(
                conn,
                student_id=student_row["id"],
                approved_request_id=token_payload.get("reset_request_id"),
            )

        login_count = record_student_login(
            conn,
            student_row=student_row,
            login_method="password_reset_setup" if flow_type == "password_reset" else "first_time_setup",
            identifier_type="name_and_student_id_number",
            identifier_value=f"{student_row['name']} / {student_row['student_id_number']}",
            client_ip=client_ip,
            user_agent=user_agent,
        )
        conn.commit()

    return _build_student_login_json_response(
        student_row=student_row,
        client_ip=client_ip,
        safe_next=safe_next,
        login_count=login_count,
    )


@router.post("/api/student/password/forgot", response_class=JSONResponse)
def api_student_password_forgot(
    request: Request,
    name: str = Form(),
    student_id_number: str = Form(),
    class_name: str = Form(),
):
    """学生提交忘记密码申请，等待教师审核。"""
    with get_db_connection() as conn:
        student_row = conn.execute(
            """
            SELECT s.*, c.name AS class_name, c.created_by_teacher_id
            FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE s.name = ? AND s.student_id_number = ? AND c.name = ?
            """,
            (name.strip(), student_id_number.strip(), class_name.strip()),
        ).fetchone()

        if not student_row:
            raise HTTPException(status_code=400, detail="提交失败：姓名、学号和班级名称不匹配。")
        _ensure_student_can_login(student_row)
        if not student_row["hashed_password"] and not student_row["password_reset_required"]:
            raise HTTPException(
                status_code=400,
                detail="该账号尚未设置密码，无需找回，请直接使用姓名和学号登录。",
            )

        try:
            request_id = create_password_reset_request(
                conn,
                student_row=student_row,
                requester_ip=get_client_ip(request),
                requester_user_agent=request.headers.get("user-agent", ""),
            )
            create_password_reset_request_notification(conn, request_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        conn.commit()

    return {
        "status": "success",
        "message": "找回密码申请已提交，请等待教师审核。",
        "request_id": request_id,
    }


@router.post("/api/student/password/change", response_class=JSONResponse)
def api_student_password_change(
    current_password: str = Form(),
    new_password: str = Form(),
    confirm_password: str = Form(),
    user: dict = Depends(get_current_student),
):
    """学生登录后主动修改密码。"""
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致。")
    if current_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同。")

    password_error = validate_student_password(new_password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    with get_db_connection() as conn:
        student_row = get_student_auth_record_by_pk(conn, int(user["id"]))
        if not student_row:
            raise HTTPException(status_code=404, detail="学生账号不存在。")
        if student_row["password_reset_required"]:
            raise HTTPException(status_code=400, detail="当前账号正处于重置流程，请重新登录后设置密码。")
        if not student_row["hashed_password"] or not verify_password(current_password, student_row["hashed_password"]):
            raise HTTPException(status_code=400, detail="当前密码错误。")

        conn.execute(
            """
            UPDATE students
            SET hashed_password = ?, password_updated_at = ?, password_reset_required = 0
            WHERE id = ?
            """,
            (get_password_hash(new_password), datetime.now().isoformat(), student_row["id"]),
        )
        conn.commit()

    return {"status": "success", "message": "密码修改成功。"}


@router.post("/student/login")
def handle_student_login(
    request: Request,
    identifier: Optional[str] = Form(default=None),
    password: Optional[str] = Form(default=None),
    name: Optional[str] = Form(default=None),
    student_id_number: Optional[str] = Form(default=None),
    next: Optional[str] = Form(default=None),
):
    """兼容表单提交流程，优先支持密码登录。"""
    safe_next = sanitize_next_path(next, fallback="/dashboard")

    if identifier and password:
        client_ip = get_client_ip(request)
        with get_db_connection() as conn:
            student_row, _ = _perform_student_password_login(
                conn,
                identifier=identifier.strip(),
                password=password,
                client_ip=client_ip,
                user_agent=request.headers.get("user-agent", ""),
            )
            conn.commit()

        access_token, _ = _build_student_login_token(student_row, client_ip)
        response = RedirectResponse(url=safe_next, status_code=status.HTTP_303_SEE_OTHER)
        apply_access_token_cookie(response, access_token)
        response.set_cookie("cultivation_reveal", "1", max_age=60, httponly=False, samesite="lax")
        return response

    if name and student_id_number:
        return templates.TemplateResponse(
            request,
            "status.html",
            {
                "request": request,
                "success": False,
                "message": "首次登录需要先完成密码设置，请返回登录页后按页面提示操作。",
                "back_url": build_login_url("/student/login", safe_next),
            },
        )

    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "request": request,
            "success": False,
            "message": "登录失败：请填写完整的登录信息。",
            "back_url": build_login_url("/student/login", safe_next),
        },
    )


@router.post("/teacher/register")
def handle_teacher_register(request: Request, name: str = Form(), email: str = Form(), password: str = Form()):
    """教师账号只能由超管在管理中心创建。"""
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "request": request,
            "success": False,
            "message": "教师账号只能由超管教师创建，请联系系统超管开通账号。",
            "back_url": "/teacher/login",
        },
        status_code=status.HTTP_403_FORBIDDEN,
    )


@router.post("/teacher/login")
def handle_teacher_login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """V4.0: 教师登录 - 验证数据库"""
    from ...dependencies import get_client_ip
    client_ip = get_client_ip(request)
    safe_next = sanitize_next_path(next, fallback="/dashboard")

    with get_db_connection() as conn:
        teacher = conn.execute(
            """
            SELECT *
            FROM teachers
            WHERE lower(email) = ?
              AND COALESCE(is_active, 1) = 1
            LIMIT 1
            """,
            (email.strip().lower(),),
        ).fetchone()

    # 修复：使用 verify_password 验证
    if not teacher or not verify_password(password, teacher['hashed_password']):
        return templates.TemplateResponse(request, "status.html",
                                          {"request": request, "success": False, "message": "登录失败：邮箱或密码错误。",
                                           "back_url": build_login_url("/teacher/login", safe_next)})

    teacher_data = dict(teacher)

    token_data = {
        "id": teacher_data['id'],  # 数据库主键 PK
        "name": teacher_data['name'],
        "email": teacher_data['email'],
        "role": "teacher",
        "login_time": datetime.now().isoformat()
    }

    access_token = create_access_token(token_data, client_ip)

    response = RedirectResponse(
        url=safe_next,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    apply_access_token_cookie(response, access_token)
    return response


@router.get("/logout")
def logout(request: Request):
    from ...dependencies import get_active_user_from_request

    # 获取当前用户并使其会话失效
    user = get_active_user_from_request(request)
    if user and user.get('id'):
        invalidate_session_for_user(str(user['id']), user.get('role'))
        print(f"[SESSION] 用户 {user.get('name')} 主动注销")

    response = RedirectResponse(url="/student/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_access_token_cookie(response)
    return response
