from datetime import datetime
import sqlite3
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Form, HTTPException, Depends, status, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from typing import Optional, List
from pathlib import Path
import pandas as pd

from ..core import templates, COURSE_INFO
# 修复：移除不再需要的 TEACHER_PASS, SHARE_DIR, ROSTER_DIR
from ..config import TEACHER_USER, MAX_SUBMISSION_FILE_COUNT, MAX_UPLOAD_SIZE_MB
from ..dependencies import (
    get_current_user, get_current_user_optional, get_current_teacher, get_current_student,
    create_access_token, get_password_hash, verify_password,
    human_readable_size, get_client_ip  # human_readable_size 仍被 classroom_main 使用
)
# 修复：移除，V4.0 roster_handler 不再有 parse_excel_to_students
# from ..services.roster_handler import parse_excel_to_students
from ..database import get_db_connection
from ..dependencies import build_login_url, sanitize_next_path
from ..dependencies import infer_required_role_from_path, get_role_label
from ..dependencies import apply_access_token_cookie, clear_access_token_cookie, invalidate_session_for_user
from ..services.behavior_tracking_service import record_behavior_event
from ..services.discussion_mood_service import maybe_schedule_discussion_mood_refresh
from ..services.submission_assets import decode_allowed_file_types_json, summarize_allowed_file_types
from ..services.dashboard_service import build_dashboard_context
from ..services.classroom_page_service import build_classroom_page_context
from ..services.assignment_lifecycle_service import (
    assignment_accepts_submissions,
    close_overdue_assignments,
    enrich_assignment_runtime_view,
    refresh_assignment_runtime_status,
)
from ..services.student_auth_service import (
    PASSWORD_POLICY_HINT,
    build_password_setup_token,
    build_student_security_summary,
    can_student_use_identity_login,
    create_password_reset_request,
    decode_password_setup_token,
    get_student_auth_record_by_identity,
    get_student_auth_record_by_pk,
    get_student_auth_record_for_password_login,
    mark_latest_approved_reset_request_completed,
    record_student_login,
    validate_student_password,
)
from ..services.submission_preview_service import ensure_submission_access, serialize_submission_file_row

router = APIRouter()


def _build_login_page_context(request: Request, next_url: Optional[str]) -> dict:
    safe_next = sanitize_next_path(next_url, fallback="/dashboard")
    return {
        "request": request,
        "next_url": safe_next,
        "teacher_entry_url": build_login_url("/teacher/login", safe_next),
        "student_entry_url": build_login_url("/student/login", safe_next),
        "password_policy_hint": PASSWORD_POLICY_HINT,
    }


def _enrich_assignment_upload_config(assignment: dict) -> dict:
    allowed_file_types = decode_allowed_file_types_json(assignment.get("allowed_file_types_json"))
    assignment["allowed_file_types"] = allowed_file_types
    assignment["allowed_file_types_label"] = summarize_allowed_file_types(allowed_file_types)
    return enrich_assignment_runtime_view(assignment)


def _serialize_submission_file_rows(rows) -> list[dict]:
    files = []
    for row in rows:
        item = serialize_submission_file_row(row)
        item["relative_path"] = item.get("relative_path") or item.get("original_filename")
        files.append(item)
    return files


def _build_student_login_token(student_row, client_ip: str) -> tuple[str, dict]:
    student_data = dict(student_row)
    login_time = datetime.now().isoformat()
    token_data = {
        "id": student_data["id"],
        "student_id_number": student_data["student_id_number"],
        "name": student_data["name"],
        "role": "student",
        "login_time": login_time,
    }
    access_token = create_access_token(token_data, client_ip)
    return access_token, token_data


def _build_student_login_json_response(
    *,
    student_row,
    client_ip: str,
    safe_next: str,
    login_count: int,
) -> JSONResponse:
    access_token, _ = _build_student_login_token(student_row, client_ip)
    response = JSONResponse({
        "status": "success",
        "message": "登录成功。",
        "redirect_to": safe_next,
        "login_count": login_count,
    })
    apply_access_token_cookie(response, access_token)
    return response


def _perform_student_password_login(
    conn,
    *,
    identifier: str,
    password: str,
    client_ip: str,
    user_agent: Optional[str],
):
    try:
        student_row, identifier_type = get_student_auth_record_for_password_login(conn, identifier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not student_row:
        raise HTTPException(status_code=400, detail="登录失败：账号或密码错误。")
    if student_row["password_reset_required"]:
        raise HTTPException(
            status_code=409,
            detail="教师已通过找回密码申请，请使用姓名和学号重新设置密码。",
        )
    if not student_row["hashed_password"]:
        raise HTTPException(
            status_code=400,
            detail="该账号尚未设置密码，请使用姓名和学号完成首次登录。",
        )
    if not verify_password(password, student_row["hashed_password"]):
        raise HTTPException(status_code=400, detail="登录失败：账号或密码错误。")

    login_count = record_student_login(
        conn,
        student_row=student_row,
        login_method="password",
        identifier_type=identifier_type,
        identifier_value=identifier,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return student_row, login_count


# ============================
# 1. 根目录和登录/注册
# ============================

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
    return templates.TemplateResponse(request, "teacher_register_v4.html", {"request": request})


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
    """V4.0: 教师注册"""
    hashed_password = get_password_hash(password)
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                (name.strip(), email.strip(), hashed_password)
            )
            conn.commit()
    except sqlite3.IntegrityError:  # 邮箱已存在
        return templates.TemplateResponse(request, "status.html",
                                          {"request": request, "success": False, "message": "注册失败：该邮箱已被使用。",
                                           "back_url": "/teacher/register"})

    return templates.TemplateResponse(request, "status.html",
                                      {"request": request, "success": True, "message": "注册成功！请登录。",
                                       "back_url": "/teacher/login"})


@router.post("/teacher/login")
def handle_teacher_login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """V4.0: 教师登录 - 验证数据库"""
    from ..dependencies import get_client_ip
    client_ip = get_client_ip(request)
    safe_next = sanitize_next_path(next, fallback="/dashboard")

    with get_db_connection() as conn:
        teacher = conn.execute("SELECT * FROM teachers WHERE email = ?", (email,)).fetchone()

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
    from ..dependencies import get_active_user_from_request

    # 获取当前用户并使其会话失效
    user = get_active_user_from_request(request)
    if user and user.get('id'):
        invalidate_session_for_user(str(user['id']), user.get('role'))
        print(f"[SESSION] 用户 {user.get('name')} 主动注销")

    response = RedirectResponse(url="/student/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_access_token_cookie(response)
    return response


# ============================
# 2. 仪表盘 (V4.0 新)
# ============================

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    filter: Optional[str] = None,
    q: Optional[str] = None,
    search: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """V4.0: 仪表盘，显示用户所有相关的 "班级课堂" """
    with get_db_connection() as conn:
        dashboard_context = build_dashboard_context(
            conn,
            user,
            initial_filter=filter,
            initial_search=q if q is not None else search,
        )

    current_search = str(dashboard_context.get("dashboard_initial_search") or "")
    for item in dashboard_context.get("dashboard_filters", []):
        params: dict[str, str] = {}
        filter_value = str(item.get("value") or "all")
        if filter_value and filter_value != "all":
            params["filter"] = filter_value
        if current_search:
            params["q"] = current_search
        item["href"] = "/dashboard" if not params else f"/dashboard?{urlencode(params)}"

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "user_info": user,
            **dashboard_context,
        },
    )


# ============================
# 3. 课堂主界面 (V4.0 新)
# ============================

@router.get("/classroom/{class_offering_id}", response_class=HTMLResponse)
async def classroom_main(request: Request, class_offering_id: int, user: dict = Depends(get_current_user)):
    """V4.0: 替换旧的 /app，这是特定班级课堂的主界面"""
    student_security_summary = None
    classroom_page = None
    with get_db_connection() as conn:
        offering = conn.execute(
            """SELECT o.*,
                      c.name as course_name,
                      c.description as course_description,
                      c.credits as course_credits,
                      cl.name as class_name,
                      cl.description as class_description,
                      t.name as teacher_name,
                      (SELECT COUNT(*) FROM students s WHERE s.class_id = o.class_id) as class_student_count
               FROM class_offerings o
                        JOIN courses c ON o.course_id = c.id
                        JOIN classes cl ON o.class_id = cl.id
                        JOIN teachers t ON o.teacher_id = t.id
               WHERE o.id = ?""",
            (class_offering_id,)
        ).fetchone()

        if not offering: raise HTTPException(404, "未找到此课堂")

        offering_data = dict(offering)
        course_id = offering_data['course_id']

        if user['role'] == 'student':
            student_class = conn.execute("SELECT class_id FROM students WHERE id = ?", (user['id'],)).fetchone()
            if not student_class or student_class['class_id'] != offering_data['class_id']:
                raise HTTPException(403, "您未加入此课堂")
            student_security_summary = build_student_security_summary(conn, int(user['id']))
        elif user['role'] == 'teacher':
            if offering_data['teacher_id'] != user['id']:
                raise HTTPException(403, "您不是此课堂的教师")

        if user['role'] == 'teacher':
            files_cursor = conn.execute(
                "SELECT * FROM course_files WHERE course_id = ?",
                (course_id,)
            )
        else:
            files_cursor = conn.execute(
                "SELECT * FROM course_files WHERE course_id = ? AND is_public = TRUE AND is_teacher_resource = FALSE",
                (course_id,)
            )

        def format_size(size_bytes: int) -> str:
            """辅助函数：将字节大小转换为人类可读格式"""
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size_bytes < 1024:
                    return f"{size_bytes:.2f} {unit}"
                size_bytes /= 1024
            return f"{size_bytes:.2f} PB"

        # 修复：从 V3.2 复制，但 V4.0 还不支持显示大小
        files_info = [{"id": row['id'], "name": row['file_name'], "size": format_size(row['file_size'])} for row in files_cursor]

        close_overdue_assignments(conn)
        assignments_cursor = conn.execute(
            """
            SELECT *
            FROM assignments
            WHERE course_id = ? AND class_offering_id = ?
            ORDER BY created_at DESC
            """,
            (course_id, class_offering_id)
        )
        assignments = []
        for row in assignments_cursor:
            assignment = _enrich_assignment_upload_config(dict(row))
            if user['role'] == 'student':
                if assignment['status'] == 'new': continue
                submission = conn.execute(
                    "SELECT status, score FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
                    (assignment['id'], user['id'])
                ).fetchone()
                if submission:
                    assignment['submission_status'] = submission['status']
                    assignment['submission_score'] = submission['score']
                else:
                    assignment['submission_status'] = 'unsubmitted'
            assignments.append(assignment)

        classroom_page = build_classroom_page_context(
            conn=conn,
            user=user,
            classroom=offering_data,
            assignments=assignments,
            shared_files=files_info,
        )

    try:
        record_behavior_event(
            class_offering_id=class_offering_id,
            user_pk=int(user["id"]),
            user_role=str(user["role"]),
            display_name=str(user.get("name") or user.get("username") or user["id"]),
            action_type="page_view",
            session_started_at=str(user.get("login_time") or "").strip() or None,
            summary_text=f"进入课堂页面：{offering_data.get('course_name') or class_offering_id}",
            payload={
                "page": "classroom_main",
                "class_name": offering_data.get("class_name"),
                "course_name": offering_data.get("course_name"),
            },
            page_key="classroom_discussion",
        )
    except Exception as exc:
        print(f"[BEHAVIOR] 记录课堂页面访问失败: {exc}")

    try:
        await maybe_schedule_discussion_mood_refresh(
            class_offering_id,
            reason="page_view",
        )
    except Exception as exc:
        print(f"[DISCUSSION_MOOD] 课堂页面预热失败: {exc}")

    return templates.TemplateResponse(request, "classroom_main_v4.html", {
        "request": request,
        "user_info": user,
        "classroom": offering_data,
        "classroom_page": classroom_page,
        "shared_files": files_info,
        "assignments": assignments,
        "student_security_summary": student_security_summary,
    })

# ============================
# 5. 作业详情页 (V4.0)
# ============================

@router.get("/assignment/{assignment_id}", response_class=HTMLResponse)
async def assignment_detail_page(request: Request, assignment_id: str, user: dict = Depends(get_current_user)):
    """V4.0: 作业详情页 (学生/教师均可访问)"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment_row = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment_row:
            raise HTTPException(404, "Assignment not found")
        assignment_row = refresh_assignment_runtime_status(conn, assignment_row)
        assignment = _enrich_assignment_upload_config(dict(assignment_row))

        # 如果是试卷型作业且用户是学生 → 重定向到考试页面
        if assignment.get('exam_paper_id') and user['role'] == 'student':
            return RedirectResponse(url=f"/exam/take/{assignment_id}")

        if user['role'] == 'teacher':
            if assignment.get("class_offering_id"):
                try:
                    record_behavior_event(
                        class_offering_id=int(assignment["class_offering_id"]),
                        user_pk=int(user["id"]),
                        user_role=str(user["role"]),
                        display_name=str(user.get("name") or user.get("username") or user["id"]),
                        action_type="page_view",
                        session_started_at=str(user.get("login_time") or "").strip() or None,
                        summary_text=f"查看作业详情：{assignment.get('title') or assignment_id}",
                        payload={"page": "assignment_detail", "assignment_id": assignment_id},
                        page_key="assignment_detail",
                    )
                except Exception as exc:
                    print(f"[BEHAVIOR] 记录教师作业页访问失败: {exc}")
            return templates.TemplateResponse(request, "assignment_detail_teacher.html", {
                "request": request, "user_info": user, "assignment": assignment
            })

        if assignment['status'] == 'new':
            return templates.TemplateResponse(
                request,
                "status.html",
                {
                    "request": request,
                    "success": False,
                    "message": "该作业尚未发布",
                    "back_url": "/dashboard",
                },
            )

        submission_row = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, user['id'])
        ).fetchone()
        submission = dict(submission_row) if submission_row else None
        submission_files = []
        if submission:
            files_cursor = conn.execute(
                "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
                (submission['id'],)
            )
            submission_files = _serialize_submission_file_rows(files_cursor)

    can_withdraw_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and assignment_accepts_submissions(assignment)
    )

    if assignment.get("class_offering_id"):
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role=str(user["role"]),
                display_name=str(user.get("name") or user.get("username") or user["id"]),
                action_type="page_view",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"查看作业详情：{assignment.get('title') or assignment_id}",
                payload={
                    "page": "assignment_detail",
                    "assignment_id": assignment_id,
                    "has_submission": bool(submission),
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录学生作业页访问失败: {exc}")

    return templates.TemplateResponse(request, "assignment_detail_student.html", {
        "request": request, "user_info": user, "assignment": assignment,
        "submission": submission, "submission_files": submission_files,
        "can_withdraw_submission": can_withdraw_submission,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
    })


# ============================
# V4.1: 新的管理中心路由
# ============================

@router.get("/manage", response_class=RedirectResponse)
async def redirect_to_manage_classes(user: dict = Depends(get_current_teacher)):
    """管理中心根目录，重定向到班级管理页"""
    return RedirectResponse(url="/manage/classes", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/manage/classes", response_class=HTMLResponse)
async def get_manage_classes_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示班级管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_classes_cursor = conn.execute(
            """
            SELECT c.id, c.name, COUNT(s.id) as student_count
            FROM classes c
            LEFT JOIN students s ON c.id = s.class_id
            WHERE c.created_by_teacher_id = ?
            GROUP BY c.id, c.name
            ORDER BY c.name
            """,
            (user['id'],)
        )
        my_classes = my_classes_cursor.fetchall()

    return templates.TemplateResponse(request, "manage/classes.html", {
        "request": request,
        "user_info": user,
        "my_classes": my_classes,
        "page_title": "班级管理",
        "active_page": "classes"  # 用于侧边栏高亮
    })


@router.get("/manage/courses", response_class=HTMLResponse)
async def get_manage_courses_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示课程管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_courses_cursor = conn.execute(
            "SELECT id, name, credits FROM courses WHERE created_by_teacher_id = ? ORDER BY name", (user['id'],)
        )
        my_courses = my_courses_cursor.fetchall()

    return templates.TemplateResponse(request, "manage/courses.html", {
        "request": request,
        "user_info": user,
        "my_courses": my_courses,
        "page_title": "课程管理",
        "active_page": "courses"
    })


@router.get("/manage/offerings", response_class=HTMLResponse)
async def get_manage_offerings_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示开设课堂页面 (列表和新建)"""
    conn = get_db_connection()
    try:
        my_classes = conn.execute(
            "SELECT id, name FROM classes WHERE created_by_teacher_id = ?", (user['id'],)
        ).fetchall()
        my_courses = conn.execute(
            "SELECT id, name FROM courses WHERE created_by_teacher_id = ?", (user['id'],)
        ).fetchall()
        my_offerings = conn.execute(
            """
            SELECT o.id, c.name as class_name, co.name as course_name, o.semester
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            WHERE o.teacher_id = ?
            ORDER BY co.name, c.name
            """, (user['id'],)
        ).fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(request, "manage/offerings.html", {
        "request": request,
        "user_info": user,
        "my_classes": my_classes,
        "my_courses": my_courses,
        "my_offerings": my_offerings,
        "page_title": "开设课堂",
        "active_page": "offerings"
    })


@router.get("/manage/ai", response_class=HTMLResponse)
async def get_manage_ai_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示课堂AI配置页面"""
    with get_db_connection() as conn:
        # 优化：查询时额外获取课程的 description 和 credits，用于前端预填充
        my_offerings = conn.execute(
            """
            SELECT o.id, c.name as class_name, 
                   co.name as course_name, co.description, co.credits
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            WHERE o.teacher_id = ?
            ORDER BY co.name, c.name
            """, (user['id'],)
        ).fetchall()

    return templates.TemplateResponse(request, "manage/ai.html", {
        "request": request,
        "user_info": user,
        "my_offerings": my_offerings,
        "page_title": "课堂AI配置",
        "active_page": "ai"
    })


@router.get("/manage/system", response_class=HTMLResponse)
async def get_manage_system_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示系统管理页面，用于审核学生找回密码申请。"""
    with get_db_connection() as conn:
        system_summary = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM student_password_reset_requests
            WHERE teacher_id = ?
            """,
            (user["id"],),
        ).fetchone()

        login_summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total_logins,
                SUM(CASE WHEN date(logged_at) = date('now', 'localtime') THEN 1 ELSE 0 END) AS today_logins
            FROM student_login_audit_logs logs
            JOIN students s ON s.id = logs.student_id
            JOIN classes c ON c.id = s.class_id
            WHERE c.created_by_teacher_id = ?
            """,
            (user["id"],),
        ).fetchone()

        reset_requests = conn.execute(
            """
            SELECT r.id, r.status, r.submitted_at, r.reviewed_at, r.completed_at,
                   s.name AS student_name,
                   s.student_id_number,
                   c.name AS class_name,
                   (
                       SELECT COUNT(*)
                       FROM student_login_audit_logs logs
                       WHERE logs.student_id = s.id
                   ) AS total_logins,
                   (
                       SELECT MAX(logged_at)
                       FROM student_login_audit_logs logs
                       WHERE logs.student_id = s.id
                   ) AS last_login_at
            FROM student_password_reset_requests r
            JOIN students s ON s.id = r.student_id
            JOIN classes c ON c.id = r.class_id
            WHERE r.teacher_id = ?
            ORDER BY
                CASE r.status
                    WHEN 'pending' THEN 0
                    WHEN 'approved' THEN 1
                    WHEN 'completed' THEN 2
                    ELSE 3
                END,
                r.submitted_at DESC,
                r.id DESC
            """,
            (user["id"],),
        ).fetchall()

    return templates.TemplateResponse(request, "manage/system.html", {
        "request": request,
        "user_info": user,
        "system_summary": dict(system_summary) if system_summary else {},
        "login_summary": dict(login_summary) if login_summary else {},
        "reset_requests": reset_requests,
        "page_title": "系统管理",
        "active_page": "system",
    })


# ============================
# V4.5: 试卷库管理路由
# ============================

@router.get("/manage/exams", response_class=HTMLResponse)
async def manage_exams_page(request: Request, user: dict = Depends(get_current_teacher)):
    """试卷库管理页面"""
    with get_db_connection() as conn:
        # 自动将已完成的AI生成试卷从 generating 转为 draft
        conn.execute(
            """UPDATE exam_papers SET status = 'draft', ai_gen_status = NULL, updated_at = ?
               WHERE teacher_id = ? AND status = 'generating' AND ai_gen_status = 'completed'""",
            (datetime.now().isoformat(), user['id'])
        )
        conn.commit()

        papers_cursor = conn.execute(
            """SELECT ep.*,
                      (SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ep.id) as assigned_count
               FROM exam_papers ep
               WHERE ep.teacher_id = ?
               ORDER BY ep.updated_at DESC""",
            (user['id'],)
        )
        papers = []
        for row in papers_cursor:
            paper = dict(row)
            # 解析 questions_json
            if paper.get('questions_json'):
                try:
                    paper['questions_json'] = json.loads(paper['questions_json'])
                except (json.JSONDecodeError, TypeError):
                    paper['questions_json'] = None
            papers.append(paper)

    return templates.TemplateResponse(request, "manage/exams.html", {
        "request": request,
        "user_info": user,
        "papers": papers,
        "page_title": "试卷库",
        "active_page": "exams"
    })


@router.get("/exam/{exam_id}/edit", response_class=HTMLResponse)
async def exam_editor_page(request: Request, exam_id: str, user: dict = Depends(get_current_teacher)):
    """试卷编辑器页面"""
    with get_db_connection() as conn:
        paper = conn.execute(
            "SELECT * FROM exam_papers WHERE id = ? AND teacher_id = ?",
            (exam_id, user['id'])
        ).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")

        # 获取教师所有课堂（用于分配）
        offerings = conn.execute(
            """SELECT o.id, c.name as class_name, co.name as course_name
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               WHERE o.teacher_id = ?
               ORDER BY co.name""",
            (user['id'],)
        ).fetchall()

    return templates.TemplateResponse(request, "exam_editor.html", {
        "request": request,
        "user_info": user,
        "paper": dict(paper),
        "offerings": [dict(row) for row in offerings]
    })


@router.get("/exam/new", response_class=HTMLResponse)
async def exam_new_page(request: Request, user: dict = Depends(get_current_teacher)):
    """新建试卷页面"""
    with get_db_connection() as conn:
        offerings = conn.execute(
            """SELECT o.id, c.name as class_name, co.name as course_name
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               WHERE o.teacher_id = ?
               ORDER BY co.name""",
            (user['id'],)
        ).fetchall()

    return templates.TemplateResponse(request, "exam_editor.html", {
        "request": request,
        "user_info": user,
        "paper": None,
        "offerings": [dict(row) for row in offerings]
    })


@router.get("/submission/{submission_id}", response_class=HTMLResponse)
async def submission_detail_page(request: Request, submission_id: int, user: dict = Depends(get_current_user)):
    """查看/批改提交详情页（教师+学生均可访问）"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        submission = ensure_submission_access(conn, submission_id, user)
        if submission is None:
            raise HTTPException(404, "提交记录不存在")
        submission = dict(submission)

        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (submission['assignment_id'],)).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        assignment = refresh_assignment_runtime_status(conn, assignment)
        assignment = _enrich_assignment_upload_config(dict(assignment))

        # 获取提交的附件
        files_cursor = conn.execute(
            "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
            (submission_id,)
        )
        submission_files = _serialize_submission_file_rows(files_cursor)

        # 如果是试卷型作业，获取题目信息
        exam_questions = None
        if assignment.get('exam_paper_id'):
            paper = conn.execute("SELECT questions_json FROM exam_papers WHERE id = ?",
                                 (assignment['exam_paper_id'],)).fetchone()
            if paper:
                exam_questions = json.loads(paper['questions_json'])

    return templates.TemplateResponse(request, "submission_detail.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "submission": submission,
        "submission_files": submission_files,
        "exam_questions": exam_questions,
    })


@router.get("/exam/take/{assignment_id}", response_class=HTMLResponse)
async def exam_take_page(request: Request, assignment_id: str, user: dict = Depends(get_current_user)):
    """学生考试界面"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        assignment = refresh_assignment_runtime_status(conn, assignment)
        assignment = _enrich_assignment_upload_config(dict(assignment))

        if not assignment.get('exam_paper_id'):
            # 不是试卷型作业，跳转到普通作业页
            return RedirectResponse(url=f"/assignment/{assignment_id}")

        if user['role'] == 'student' and assignment['status'] == 'new':
            return templates.TemplateResponse(request, "status.html",
                {"request": request, "success": False, "message": "该考试尚未发布", "back_url": "/dashboard"})

        paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (assignment['exam_paper_id'],)).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")

        # 检查学生是否已提交
        submission = None
        submission_files = []
        if user['role'] == 'student':
            submission_row = conn.execute(
                "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
                (assignment_id, user['id'])
            ).fetchone()
            submission = dict(submission_row) if submission_row else None
            if submission:
                files_cursor = conn.execute(
                    "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
                    (submission['id'],)
                )
                submission_files = _serialize_submission_file_rows(files_cursor)

    can_withdraw_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and assignment_accepts_submissions(assignment)
    )

    if assignment.get("class_offering_id"):
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role=str(user["role"]),
                display_name=str(user.get("name") or user.get("username") or user["id"]),
                action_type="page_view",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"进入考试页面：{assignment.get('title') or assignment_id}",
                payload={
                    "page": "exam_take",
                    "assignment_id": assignment_id,
                    "has_submission": bool(submission),
                },
                page_key="exam_take",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录考试页访问失败: {exc}")

    return templates.TemplateResponse(request, "exam_take.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "paper": dict(paper),
        "submission": submission,
        "submission_files": submission_files,
        "can_withdraw_submission": can_withdraw_submission,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
    })

