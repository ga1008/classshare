from datetime import datetime
import json
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Form, HTTPException, Depends, status, UploadFile, File, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from typing import Optional, List, Any
from pathlib import Path
import pandas as pd

from ..core import templates, COURSE_INFO
# 修复：移除不再需要的 TEACHER_PASS, SHARE_DIR, ROSTER_DIR
from ..config import (
    INITIAL_SUPER_ADMIN_EMAIL,
    INITIAL_SUPER_ADMIN_NAME,
    MAX_SUBMISSION_FILE_COUNT,
    MAX_UPLOAD_SIZE_MB,
    MAX_SUBMISSION_PER_FILE_MB,
    MAX_SUBMISSION_TOTAL_MB,
)
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
from ..services.ai_grading_attachments import AI_GRADING_UPLOAD_EXTENSIONS, AI_GRADING_SUPPORTED_TYPES_LABEL
from ..services.dashboard_service import build_dashboard_context
from ..services.exam_json_service import strip_exam_scoring_for_student
from ..services.classroom_page_service import build_classroom_page_context
from ..services.assignment_lifecycle_service import (
    assignment_accepts_submissions,
    close_overdue_assignments,
    enrich_assignment_runtime_view,
    refresh_assignment_runtime_status,
    submission_effective_status,
    submission_is_returned,
    submission_resubmission_accepts,
    submission_resubmission_state,
)
from ..services.academic_service import (
    build_semester_calendar_payload,
    build_semester_defaults,
    choose_default_semester_id,
    china_today,
    load_teacher_semester_rows,
    parse_date_input,
    serialize_semester_row,
    serialize_textbook_row,
)
from ..services.academic_course_sync_service import (
    build_academic_course_metadata,
    summarize_academic_course_sync_item,
)
from ..services.course_planning_service import (
    decorate_offering_sessions,
    load_course_lessons_by_course_id,
    serialize_course_row,
)
from ..services.department_service import collect_department_options, normalize_department
from ..services.organization_scope_service import load_teacher_org_scope, organization_label
from ..services.materials_service import attach_home_learning_material_briefs, attach_learning_material_briefs
from ..services.learning_progress_service import (
    build_class_learning_overview,
    build_student_global_cultivation_profile,
    get_learning_level,
    get_learning_stage_options,
    is_personal_stage_exam_assignment,
    is_personal_stage_exam_paper,
    personal_stage_assignment_filter_sql,
    public_level_payload,
    serialize_student_learning_progress,
    student_can_access_assignment,
)
from ..services.message_center_service import (
    create_password_reset_request_notification,
    is_super_admin_teacher,
)
from ..services.blog_news_crawler_service import load_blog_news_crawler_dashboard
from ..services.agent_key_service import build_agent_key_dashboard
from ..services.session_material_generation_service import attach_generation_tasks
from ..services.student_insight_service import build_teacher_student_insight
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
from ..services.student_lifecycle_service import (
    STUDENT_STATUS_ACTIVE,
    normalize_student_enrollment_status,
    student_enrollment_status_label,
)
from ..services.submission_preview_service import ensure_submission_access, serialize_submission_file_row
from ..services.teacher_account_service import (
    TEACHER_PASSWORD_HINT,
    build_teacher_account_summary,
    list_teacher_accounts,
)
from ..services.academic_integration_service import (
    list_academic_system_profiles,
    list_teacher_academic_credentials,
)
from ..services.smart_classroom_integration_service import (
    list_smart_classroom_profiles,
    list_teacher_smart_classroom_credentials,
)
from ..services.signature_service import build_signature_dashboard_context
from ..services.organization_management_service import list_organization_tree
from ..services.smart_attendance_entry_service import (
    maybe_enqueue_teacher_daily_checkin_sync,
    maybe_send_student_attendance_alert,
    run_teacher_daily_checkin_sync_task,
)
from ..services.academic_classroom_sync_service import (
    count_teacher_teaching_places,
    load_teacher_teaching_place_dashboard,
    load_teacher_teaching_places,
)

router = APIRouter()


def _truthy_config(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "允许", "是"}


def _load_json_object(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iter_exam_questions(paper_data: dict[str, Any]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for page in paper_data.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        for question in page.get("questions", []) or []:
            if isinstance(question, dict):
                questions.append(question)
    return questions


def _exam_allows_student_ai(paper_data: dict[str, Any], exam_config: dict[str, Any]) -> bool:
    if _truthy_config(
        exam_config.get("allow_student_ai")
        or exam_config.get("student_ai_enabled")
        or exam_config.get("allow_ai")
    ):
        return True
    return any(
        _truthy_config(
            question.get("allow_ai")
            or question.get("allow_student_ai")
            or question.get("ai_allowed")
        )
        for question in _iter_exam_questions(paper_data)
    )


def _build_exam_ai_context(assignment: dict[str, Any], paper: dict[str, Any], paper_data: dict[str, Any]) -> str:
    lines = [
        "【当前考试上下文】",
        f"- 作业/考试标题：{assignment.get('title') or paper.get('title') or assignment.get('id')}",
        f"- 试卷标题：{paper.get('title') or ''}",
    ]
    if paper.get("description"):
        lines.append(f"- 试卷说明：{str(paper.get('description'))[:800]}")
    allowed_question_ids = []
    question_lines = []
    for index, question in enumerate(_iter_exam_questions(paper_data), start=1):
        allow_ai = _truthy_config(
            question.get("allow_ai")
            or question.get("allow_student_ai")
            or question.get("ai_allowed")
        )
        if allow_ai:
            allowed_question_ids.append(str(question.get("id") or index))
        text = re.sub(r"\s+", " ", str(question.get("text") or "")).strip()
        attachment = question.get("attachment_requirements") if isinstance(question.get("attachment_requirements"), dict) else {}
        attachment_note = ""
        if attachment:
            min_count = attachment.get("min_count") or attachment.get("min")
            try:
                min_count_num = int(min_count or 0)
            except (TypeError, ValueError):
                min_count_num = 0
            required = _truthy_config(attachment.get("required")) or min_count_num > 0
            if required or attachment.get("description"):
                attachment_note = f"；附件要求：{attachment.get('description') or ('至少' + str(min_count_num or 1) + '个附件')}"
        question_lines.append(
            f"{index}. [{question.get('id') or index}] {question.get('type') or 'question'}：{text[:320]}{attachment_note}"
        )
    if allowed_question_ids:
        lines.append(f"- 教师允许使用课堂 AI 的题目：{', '.join(allowed_question_ids)}")
    else:
        lines.append("- 教师允许使用课堂 AI：整卷允许")
    lines.append("【试卷题目摘要】")
    lines.extend(question_lines[:80])
    lines.append("请只围绕当前课堂、试卷和学生提问提供启发式帮助，不要直接替学生完成整份答案。")
    return "\n".join(lines)


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
    stage_key = assignment.get("learning_stage_key")
    stage_level = get_learning_level(stage_key) if stage_key else None
    assignment["learning_stage"] = public_level_payload(stage_level) if stage_level else None
    return enrich_assignment_runtime_view(assignment)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _plain_feedback_preview(markdown: Any, limit: int = 96) -> str:
    text = str(markdown or "")
    if not text.strip():
        return ""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_~|`]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _attach_teacher_assignment_card_metrics(
    conn,
    assignments: list[dict[str, Any]],
    classroom: dict[str, Any],
) -> None:
    if not assignments:
        return

    total_students = _safe_int(classroom.get("class_student_count"))
    if total_students <= 0 and classroom.get("class_id"):
        count_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM students
            WHERE class_id = ?
              AND COALESCE(enrollment_status, 'active') = 'active'
            """,
            (classroom.get("class_id"),),
        ).fetchone()
        total_students = _safe_int(count_row[0] if count_row else 0)

    assignment_ids = [_safe_int(item.get("id")) for item in assignments if item.get("id") is not None]
    assignment_ids = [assignment_id for assignment_id in assignment_ids if assignment_id > 0]
    if not assignment_ids:
        return

    placeholders = ",".join("?" for _ in assignment_ids)
    rows = conn.execute(
        f"""
        SELECT assignment_id,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_absence_score, 0) = 0
                    AND status != 'unsubmitted'
                   THEN student_pk_id END) AS submitted_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_absence_score, 0) = 0
                    AND COALESCE(resubmission_allowed, 0) = 0
                    AND status = 'submitted'
                   THEN student_pk_id END) AS pending_grade_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_absence_score, 0) = 0
                    AND COALESCE(resubmission_allowed, 0) = 0
                    AND status = 'grading'
                   THEN student_pk_id END) AS grading_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_absence_score, 0) = 0
                    AND status = 'graded'
                   THEN student_pk_id END) AS graded_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_absence_score, 0) = 0
                    AND COALESCE(resubmission_allowed, 0) = 1
                   THEN student_pk_id END) AS returned_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_absence_score, 0) = 1
                   THEN student_pk_id END) AS absence_zero_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(is_late_submission, 0) = 1
                   THEN student_pk_id END) AS late_submission_count
        FROM submissions
        WHERE assignment_id IN ({placeholders})
        GROUP BY assignment_id
        """,
        tuple(assignment_ids),
    ).fetchall()
    metrics_by_assignment = {int(row["assignment_id"]): dict(row) for row in rows}

    for assignment in assignments:
        row = metrics_by_assignment.get(_safe_int(assignment.get("id")), {})
        submitted_count = _safe_int(row.get("submitted_count"))
        pending_grade_count = _safe_int(row.get("pending_grade_count"))
        grading_count = _safe_int(row.get("grading_count"))
        graded_count = _safe_int(row.get("graded_count"))
        returned_count = _safe_int(row.get("returned_count"))
        absence_zero_count = _safe_int(row.get("absence_zero_count"))
        late_submission_count = _safe_int(row.get("late_submission_count"))
        unsubmitted_count = max(0, total_students - submitted_count - absence_zero_count)
        review_queue_count = pending_grade_count
        assignment["teacher_submission_metrics"] = {
            "total_students": total_students,
            "submitted_count": submitted_count,
            "pending_grade_count": pending_grade_count,
            "grading_count": grading_count,
            "graded_count": graded_count,
            "returned_count": returned_count,
            "absence_zero_count": absence_zero_count,
            "late_submission_count": late_submission_count,
            "unsubmitted_count": unsubmitted_count,
            "review_queue_count": review_queue_count,
            "review_activity_count": pending_grade_count + grading_count,
            "submission_percent": round(submitted_count * 100 / total_students) if total_students else 0,
            "needs_attention": review_queue_count > 0 or returned_count > 0,
        }


def _assignment_back_url(assignment: dict) -> str:
    class_offering_id = assignment.get("class_offering_id")
    if class_offering_id:
        return f"/classroom/{class_offering_id}"
    return "/dashboard"


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


def _ensure_student_can_login(student_row) -> None:
    if not student_row:
        return
    normalized_status = normalize_student_enrollment_status(
        student_row["enrollment_status"] if "enrollment_status" in student_row.keys() else STUDENT_STATUS_ACTIVE
    )
    if normalized_status != STUDENT_STATUS_ACTIVE:
        raise HTTPException(
            status_code=403,
            detail=f"该学生已设置为{student_enrollment_status_label(normalized_status)}，暂不纳入课堂学习。",
        )


def _build_student_login_json_response(
    *,
    student_row,
    client_ip: str,
    safe_next: str,
    login_count: int,
) -> JSONResponse:
    _ensure_student_can_login(student_row)
    access_token, _ = _build_student_login_token(student_row, client_ip)
    cultivation_profile = None
    try:
        with get_db_connection() as profile_conn:
            cultivation_profile = build_student_global_cultivation_profile(
                profile_conn,
                int(student_row["id"]),
            )
            profile_conn.commit()
    except Exception as exc:
        print(f"[LEARNING_PROGRESS] 登录境界信息加载失败: {exc}")
    response = JSONResponse({
        "status": "success",
        "message": "登录成功。",
        "redirect_to": safe_next,
        "login_count": login_count,
        "cultivation_profile": cultivation_profile,
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
    _ensure_student_can_login(student_row)
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
    from ..dependencies import get_client_ip
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
async def classroom_main(
    request: Request,
    class_offering_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """V4.0: 替换旧的 /app，这是特定班级课堂的主界面"""
    student_security_summary = None
    classroom_page = None
    teacher_daily_sync_task_id = None
    with get_db_connection() as conn:
        offering = conn.execute(
            """SELECT o.*,
                      COALESCE(s.name, o.semester) as semester_display,
                      c.name as course_name,
                      c.description as course_description,
                      c.credits as course_credits,
                      cl.name as class_name,
                      cl.description as class_description,
                      t.name as teacher_name,
                      tb.title as textbook_title,
                      (
                          SELECT COUNT(*)
                          FROM students s
                          WHERE s.class_id = o.class_id
                            AND COALESCE(s.enrollment_status, 'active') = 'active'
                      ) as class_student_count
               FROM class_offerings o
                        JOIN courses c ON o.course_id = c.id
                        JOIN classes cl ON o.class_id = cl.id
                        JOIN teachers t ON o.teacher_id = t.id
                        LEFT JOIN academic_semesters s ON s.id = o.semester_id
                        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
               WHERE o.id = ?""",
            (class_offering_id,)
        ).fetchone()

        if not offering: raise HTTPException(404, "未找到此课堂")

        offering_data = dict(offering)
        offering_data["semester"] = offering_data.get("semester_display") or offering_data.get("semester")
        offering_data = attach_home_learning_material_briefs(
            conn,
            [offering_data],
            teacher_id=int(offering_data["teacher_id"]),
            markdown_only=True,
        )[0]
        course_id = offering_data['course_id']

        if user['role'] == 'student':
            student_class = conn.execute(
                """
                SELECT class_id, COALESCE(enrollment_status, 'active') AS enrollment_status
                FROM students
                WHERE id = ?
                """,
                (user['id'],),
            ).fetchone()
            if (
                not student_class
                or student_class['class_id'] != offering_data['class_id']
                or normalize_student_enrollment_status(student_class["enrollment_status"]) != STUDENT_STATUS_ACTIVE
            ):
                raise HTTPException(403, "您未加入此课堂")
            student_security_summary = build_student_security_summary(conn, int(user['id']))
            try:
                maybe_send_student_attendance_alert(
                    conn,
                    class_offering_id=int(class_offering_id),
                    student_id=int(user["id"]),
                )
            except Exception as exc:
                print(f"[SMART_ATTENDANCE] 学生考勤提醒创建失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass
        elif user['role'] == 'teacher':
            if offering_data['teacher_id'] != user['id']:
                raise HTTPException(403, "您不是此课堂的教师")
            try:
                teacher_daily_sync_task_id = maybe_enqueue_teacher_daily_checkin_sync(
                    conn,
                    class_offering_id=int(class_offering_id),
                    teacher_id=int(user["id"]),
                )
            except Exception as exc:
                print(f"[SMART_ATTENDANCE] 教师每日后台同步任务入队失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass

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
        teacher_assignment_filter = (
            f"AND {personal_stage_assignment_filter_sql('assignments')}"
            if user["role"] == "teacher"
            else ""
        )
        assignments_cursor = conn.execute(
            f"""
            SELECT *
            FROM assignments
            WHERE course_id = ? AND class_offering_id = ?
            {teacher_assignment_filter}
            ORDER BY created_at DESC
            """,
            (course_id, class_offering_id)
        )
        assignments = []
        for row in assignments_cursor:
            assignment = _enrich_assignment_upload_config(dict(row))
            if user['role'] == 'student':
                if not student_can_access_assignment(conn, assignment["id"], int(user["id"])):
                    continue
                if assignment['status'] == 'new': continue
                submission = conn.execute(
                    """
                    SELECT id, status, score, feedback_md, resubmission_allowed, resubmission_due_at
                    FROM submissions
                    WHERE assignment_id = ? AND student_pk_id = ?
                    """,
                    (assignment['id'], user['id'])
                ).fetchone()
                if submission:
                    submission_dict = dict(submission)
                    can_resubmit = submission_resubmission_accepts(submission_dict)
                    assignment['can_resubmit_submission'] = can_resubmit
                    assignment['resubmission_state'] = submission_resubmission_state(submission_dict)
                    assignment['resubmission_due_at'] = submission_dict.get('resubmission_due_at')
                    assignment['submission_status'] = submission_effective_status(submission_dict)
                    assignment['submission_score'] = submission['score']
                    assignment['submission_id'] = submission['id']
                    assignment['submission_feedback_md'] = submission['feedback_md']
                    assignment['submission_feedback_preview'] = _plain_feedback_preview(submission['feedback_md'])
                else:
                    assignment['submission_status'] = 'unsubmitted'
                    assignment['can_resubmit_submission'] = False
                    assignment['resubmission_state'] = 'none'
                    assignment['submission_id'] = None
                    assignment['submission_feedback_md'] = None
                    assignment['submission_feedback_preview'] = ""
            assignments.append(assignment)

        if user['role'] == 'teacher':
            _attach_teacher_assignment_card_metrics(conn, assignments, offering_data)

        session_rows = conn.execute(
            """
            SELECT id,
                   course_lesson_id,
                   order_index,
                   title,
                   content,
                   section_count,
                   slot_section_count,
                   session_date,
                   weekday,
                   week_index,
                   learning_material_id,
                   schedule_source,
                   academic_occurrence_id,
                   academic_sync_item_id,
                   academic_course_code,
                   academic_teaching_class_name,
                   academic_weeks_text,
                   academic_section_text,
                   academic_time_text,
                   academic_campus,
                   academic_location,
                   academic_classroom_id,
                   academic_classroom_code,
                   academic_classroom_type,
                   schedule_status,
                   is_non_periodic,
                   schedule_note,
                   schedule_metadata_json
            FROM class_offering_sessions
            WHERE class_offering_id = ?
            ORDER BY order_index, session_date
            """,
            (class_offering_id,),
        ).fetchall()
        session_items = attach_learning_material_briefs(
            conn,
            [dict(row) for row in session_rows],
            teacher_id=int(offering_data["teacher_id"]),
            markdown_only=True,
        )
        attach_generation_tasks(
            conn,
            session_items,
            teacher_id=int(offering_data["teacher_id"]),
        )
        teaching_plan = decorate_offering_sessions(
            session_items,
            home_material=offering_data.get("home_learning_material"),
            include_home_placeholder=user["role"] == "teacher",
        )
        if teaching_plan.get("schedule_summary") and not offering_data.get("schedule_info"):
            offering_data["schedule_info"] = teaching_plan["schedule_summary"]

        classroom_page = build_classroom_page_context(
            conn=conn,
            user=user,
            classroom=offering_data,
            assignments=assignments,
            shared_files=files_info,
        )
        classroom_page["teaching_plan"] = teaching_plan
        if user["role"] == "student":
            try:
                classroom_page["learning_progress"] = serialize_student_learning_progress(
                    conn,
                    class_offering_id,
                    int(user["id"]),
                )
            except Exception as exc:
                print(f"[LEARNING_PROGRESS] 学生修为信息加载失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                classroom_page["learning_progress"] = None
        else:
            try:
                classroom_page["learning_overview"] = build_class_learning_overview(
                    conn,
                    class_offering_id,
                )
            except Exception as exc:
                print(f"[LEARNING_PROGRESS] 课堂修为概览加载失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                classroom_page["learning_overview"] = None

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

    if teacher_daily_sync_task_id:
        background_tasks.add_task(
            run_teacher_daily_checkin_sync_task,
            int(teacher_daily_sync_task_id),
            teacher_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
        )

    return templates.TemplateResponse(request, "classroom_main_v4.html", {
        "request": request,
        "user_info": user,
        "classroom": offering_data,
        "classroom_page": classroom_page,
        "shared_files": files_info,
        "assignments": assignments,
        "student_security_summary": student_security_summary,
        "learning_stage_options": get_learning_stage_options(),
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
        assignment_back_url = _assignment_back_url(assignment)
        if user["role"] == "student" and not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")
        if user["role"] == "teacher" and is_personal_stage_exam_assignment(conn, assignment_id):
            return templates.TemplateResponse(
                request,
                "status.html",
                {
                    "request": request,
                    "success": False,
                    "message": "学生个人试炼不进入教师作业与考试明细，请在班级修行统计中查看汇总情况。",
                    "back_url": assignment_back_url,
                },
                status_code=404,
            )

        # 如果是试卷型作业且用户是学生 → 重定向到考试页面
        if assignment.get('exam_paper_id') and user['role'] == 'student':
            return RedirectResponse(url=f"/exam/take/{assignment_id}")

        if user['role'] == 'teacher':
            access_row = conn.execute(
                """
                SELECT 1
                FROM assignments a
                JOIN courses c ON c.id = a.course_id
                LEFT JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE a.id = ?
                  AND (c.created_by_teacher_id = ? OR o.teacher_id = ?)
                LIMIT 1
                """,
                (assignment_id, user["id"], user["id"]),
            ).fetchone()
            if not access_row:
                raise HTTPException(403, "无权查看该作业")
            exam_questions = None
            exam_paper_preview = None
            if assignment.get("exam_paper_id"):
                paper_row = conn.execute(
                    "SELECT title, description, questions_json FROM exam_papers WHERE id = ?",
                    (assignment["exam_paper_id"],),
                ).fetchone()
                if paper_row:
                    exam_paper_preview = {
                        "title": paper_row["title"],
                        "description": paper_row["description"] or "",
                    }
                    try:
                        exam_questions = json.loads(paper_row["questions_json"] or "{}")
                    except json.JSONDecodeError:
                        exam_questions = {"pages": []}
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
                "request": request,
                "user_info": user,
                "assignment": assignment,
                "assignment_back_url": assignment_back_url,
                "exam_questions": exam_questions,
                "exam_paper_preview": exam_paper_preview,
                "learning_stage_options": get_learning_stage_options(),
                "max_upload_mb": MAX_UPLOAD_SIZE_MB,
                "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
                "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
                "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
            })

        if assignment['status'] == 'new':
            return templates.TemplateResponse(
                request,
                "status.html",
                {
                    "request": request,
                    "success": False,
                    "message": "该作业尚未发布",
                    "back_url": assignment_back_url,
                },
            )

        submission_row = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, user['id'])
        ).fetchone()
        submission = dict(submission_row) if submission_row else None
        if submission and int(submission.get("is_absence_score") or 0):
            submission = None
        submission_files = []
        if submission:
            files_cursor = conn.execute(
                "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
                (submission['id'],)
            )
            submission_files = _serialize_submission_file_rows(files_cursor)

    submission_returned = bool(submission and submission_is_returned(submission))
    resubmission_state = submission_resubmission_state(submission) if submission else "none"
    can_resubmit_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and resubmission_state == "open"
    )
    can_withdraw_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and assignment_accepts_submissions(assignment)
        and not submission_returned
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
        "assignment_back_url": assignment_back_url,
        "submission": submission, "submission_files": submission_files,
        "can_withdraw_submission": can_withdraw_submission,
        "can_resubmit_submission": can_resubmit_submission,
        "submission_returned": submission_returned,
        "resubmission_state": resubmission_state,
        "resubmission_due_at": submission.get("resubmission_due_at") if submission else None,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
        "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
        "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
    })


# ============================
# V4.1: 新的管理中心路由
# ============================

@router.get("/manage", response_class=HTMLResponse)
async def manage_workflow_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        workflow_snapshot = _build_classroom_opening_workflow_snapshot(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/workflow.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教学流程工作台",
            active_page="workflow",
            extra={
                "workflow_snapshot": workflow_snapshot,
            },
        ),
    )


@router.get("/manage/classes", response_class=HTMLResponse)
async def get_manage_classes_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示班级管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_classes_cursor = conn.execute(
            """
            SELECT c.id,
                   c.name,
                   c.department,
                   c.description,
                   c.academic_source,
                   c.academic_class_code,
                   c.academic_class_name,
                   c.academic_college,
                   c.academic_grade,
                   c.academic_major,
                   c.school_code,
                   c.school_name,
                   c.college,
                   c.academic_sync_at,
                   c.academic_sync_message,
                   c.created_at,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(s.enrollment_status, 'active') = 'active'
                       THEN s.id END
                   ) AS student_count,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(s.enrollment_status, 'active') = 'suspended'
                       THEN s.id END
                   ) AS suspended_student_count,
                   COUNT(DISTINCT s.id) AS total_student_count,
                   SUM(
                       CASE
                           WHEN s.id IS NOT NULL
                             AND COALESCE(s.enrollment_status, 'active') = 'active'
                             AND (s.email IS NULL OR TRIM(s.email) = '')
                            THEN 1 ELSE 0
                       END
                    ) AS missing_email_count,
                    COUNT(DISTINCT CASE
                       WHEN s.academic_source = 'gxufl_jwxt'
                       THEN s.id END
                    ) AS academic_synced_student_count,
                    COUNT(DISTINCT o.id) AS offering_count,
                    MAX(
                        CASE
                            WHEN COALESCE(s.enrollment_status, 'active') = 'active'
                            THEN s.created_at
                        END
                    ) AS latest_student_created_at,
                    MAX(s.academic_sync_at) AS latest_student_academic_sync_at
             FROM classes c
             LEFT JOIN students s ON c.id = s.class_id
            LEFT JOIN class_offerings o
                   ON o.class_id = c.id
                  AND o.teacher_id = c.created_by_teacher_id
            WHERE c.created_by_teacher_id = ?
             GROUP BY c.id, c.name, c.department, c.description,
                      c.academic_source, c.academic_class_code, c.academic_class_name,
                      c.academic_college, c.academic_grade, c.academic_major,
                      c.school_code, c.school_name, c.college,
                      c.academic_sync_at, c.academic_sync_message, c.created_at
             ORDER BY COALESCE(NULLIF(TRIM(c.department), ''), '未分类'), c.name
            """,
            (user["id"],),
        )
        my_classes = [dict(row) for row in my_classes_cursor.fetchall()]
        students_by_class = _load_teacher_class_student_rows(
            conn,
            int(user["id"]),
            [int(item["id"]) for item in my_classes],
        )
        for class_item in my_classes:
            class_item["student_count"] = int(class_item.get("student_count") or 0)
            class_item["suspended_student_count"] = int(class_item.get("suspended_student_count") or 0)
            class_item["total_student_count"] = int(class_item.get("total_student_count") or 0)
            class_item["missing_email_count"] = int(class_item.get("missing_email_count") or 0)
            class_item["academic_synced_student_count"] = int(class_item.get("academic_synced_student_count") or 0)
            class_item["offering_count"] = int(class_item.get("offering_count") or 0)
            class_item["department_label"] = str(class_item.get("department") or "").strip() or "未分类"
            class_item["organization_label"] = organization_label(
                {
                    "school_code": class_item.get("school_code"),
                    "school_name": class_item.get("school_name"),
                    "college": class_item.get("college") or class_item.get("academic_college"),
                    "department": class_item.get("department"),
                }
            )
            class_item["is_academic_synced"] = str(class_item.get("academic_source") or "").strip() == "gxufl_jwxt"
            class_item["latest_academic_sync_at"] = (
                class_item.get("latest_student_academic_sync_at")
                or class_item.get("academic_sync_at")
                or ""
            )
            class_item["email_coverage_percent"] = (
                round(
                    (class_item["student_count"] - class_item["missing_email_count"])
                    / class_item["student_count"]
                    * 100
                )
                if class_item["student_count"]
                else 0
            )
            class_item["students"] = students_by_class.get(int(class_item["id"]), [])
            class_item["active_students"] = [
                student
                for student in class_item["students"]
                if student.get("enrollment_status") == STUDENT_STATUS_ACTIVE
            ]

    missing_email_total = sum(int(item.get("missing_email_count") or 0) for item in my_classes)
    active_class_count = sum(1 for item in my_classes if int(item.get("offering_count") or 0) > 0)
    class_stats = {
        "class_count": len(my_classes),
        "student_count": sum(int(item.get("student_count") or 0) for item in my_classes),
        "suspended_student_count": sum(int(item.get("suspended_student_count") or 0) for item in my_classes),
        "largest_class_size": max((int(item.get("student_count") or 0) for item in my_classes), default=0),
        "missing_email_count": missing_email_total,
        "active_class_count": active_class_count,
        "department_count": len({item.get("department_label") for item in my_classes if item.get("department_label")}),
        "academic_synced_class_count": sum(1 for item in my_classes if item.get("is_academic_synced")),
        "academic_synced_student_count": sum(int(item.get("academic_synced_student_count") or 0) for item in my_classes),
    }

    return templates.TemplateResponse(
        request,
        "manage/classes.html",
        _build_manage_template_context(
            request,
            user,
            page_title="班级管理",
            active_page="classes",
            extra={
                "my_classes": my_classes,
                "class_stats": class_stats,
                "department_options": collect_department_options(
                    (item.get("department") for item in my_classes),
                ),
            },
        ),
    )


@router.get("/manage/students/{student_id}", response_class=HTMLResponse)
async def get_manage_student_detail_page(
    request: Request,
    student_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        insight = build_teacher_student_insight(conn, int(user["id"]), int(student_id))
        if not insight:
            raise HTTPException(status_code=404, detail="学生不存在或无权查看")
        conn.commit()

    student = insight.get("student") or {}
    return templates.TemplateResponse(
        request,
        "manage/student_detail.html",
        _build_manage_template_context(
            request,
            user,
            page_title=f"{student.get('name') or '学生'} · 学生洞察",
            active_page="classes",
            extra={
                "insight": insight,
            },
        ),
    )


@router.get("/manage/classrooms", response_class=HTMLResponse)
async def get_manage_classrooms_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教学场地与空闲教室查询页面。"""
    initial_page_size = 10
    with get_db_connection() as conn:
        teaching_place_count = count_teacher_teaching_places(conn, int(user["id"]))
        teaching_places = load_teacher_teaching_places(conn, int(user["id"]), limit=initial_page_size)
        classroom_dashboard = load_teacher_teaching_place_dashboard(conn, int(user["id"]))
        semester_options = [
            serialize_semester_row(row)
            for row in load_teacher_semester_rows(conn, int(user["id"]))
        ]

    return templates.TemplateResponse(
        request,
        "manage/classrooms.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教室管理",
            active_page="classrooms",
            extra={
                "teaching_places": teaching_places,
                "teaching_place_pagination": {
                    "page": 1,
                    "page_size": initial_page_size,
                    "total_count": teaching_place_count,
                    "total_page": max(1, (teaching_place_count + initial_page_size - 1) // initial_page_size),
                },
                "classroom_dashboard": classroom_dashboard,
                "semester_options": semester_options,
                "default_semester_id": choose_default_semester_id(semester_options),
            },
        ),
    )


@router.get("/manage/courses", response_class=HTMLResponse)
async def get_manage_courses_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示课程管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_courses = _load_teacher_course_rows(conn, int(user["id"]))
        semesters = load_teacher_semester_rows(conn, int(user["id"]))
        _decorate_course_grouping_context(my_courses, semesters)
        textbooks = [
            {
                "id": item["id"],
                "title": item["title"],
                "author_display": item["author_display"],
                "publisher": item["publisher"],
                "publication_year": item["publication_year"],
            }
            for item in (serialize_textbook_row(row) for row in _load_teacher_textbook_rows(conn, int(user["id"])))
        ]

    course_stats = {
        "course_count": len(my_courses),
        "active_course_count": sum(1 for item in my_courses if item.get("is_in_use")),
        "academic_synced_course_count": sum(1 for item in my_courses if item.get("academic_is_synced")),
        "lesson_count": sum(int(item.get("lesson_count") or 0) for item in my_courses),
        "total_hours": sum(int(item.get("total_hours") or 0) for item in my_courses),
    }

    return templates.TemplateResponse(
        request,
        "manage/courses.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课程管理",
            active_page="courses",
            extra={
                "my_courses": my_courses,
                "courses_json": my_courses,
                "textbooks_json": textbooks,
                "course_stats": course_stats,
                "semester_calendar": build_semester_calendar_payload(semesters),
                "department_options": collect_department_options(
                    (item.get("department") for item in my_courses),
                ),
            },
        ),
    )

def _load_teacher_textbook_rows(conn, teacher_id: int):
    return conn.execute(
        """
        SELECT id,
               title,
               authors_json,
               publisher,
               publication_date,
               introduction,
               catalog_text,
               attachment_name,
               attachment_path,
               attachment_size,
               attachment_mime_type,
               tags_json,
               created_at,
               updated_at
        FROM textbooks
        WHERE teacher_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (teacher_id,),
    ).fetchall()


def _safe_parse_json_list(raw_value):
    if raw_value in (None, ""):
        return []
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _load_teacher_class_student_rows(conn, teacher_id: int, class_ids: list[int]) -> dict[int, list[dict]]:
    normalized_ids = sorted({int(item) for item in class_ids if int(item or 0) > 0})
    if not normalized_ids:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT s.id,
               s.class_id,
               s.name,
               s.nickname,
               s.student_id_number,
               s.email,
               s.phone,
                s.academic_source,
                s.academic_student_id,
                s.academic_class_code,
                s.academic_class_name,
                s.academic_college,
                s.academic_grade,
                s.academic_major,
                s.academic_school_status,
                s.academic_student_flags,
                s.academic_sync_at,
                s.academic_sync_message,
                COALESCE(s.enrollment_status, 'active') AS enrollment_status,
                s.enrollment_status_updated_at,
                s.enrollment_note,
               s.created_at
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE c.created_by_teacher_id = ?
          AND s.class_id IN ({placeholders})
        ORDER BY
            s.class_id,
            CASE COALESCE(s.enrollment_status, 'active') WHEN 'active' THEN 0 ELSE 1 END,
            s.student_id_number,
            s.id
        """,
        [int(teacher_id), *normalized_ids],
    ).fetchall()
    grouped: dict[int, list[dict]] = {class_id: [] for class_id in normalized_ids}
    for row in rows:
        item = dict(row)
        item["enrollment_status"] = normalize_student_enrollment_status(item.get("enrollment_status"))
        item["enrollment_status_label"] = student_enrollment_status_label(item["enrollment_status"])
        item["is_active"] = item["enrollment_status"] == STUDENT_STATUS_ACTIVE
        item["display_name"] = item.get("nickname") or item.get("name") or "学生"
        item["has_email"] = bool(str(item.get("email") or "").strip())
        item["is_academic_synced"] = str(item.get("academic_source") or "").strip() == "gxufl_jwxt"
        grouped.setdefault(int(item["class_id"]), []).append(item)
    return grouped


def _load_teacher_academic_course_items(conn, teacher_id: int, course_ids: list[int]) -> dict[int, list[dict]]:
    normalized_ids = [int(course_id) for course_id in course_ids if course_id]
    if not normalized_ids:
        return {}

    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM teacher_academic_course_sync_items
        WHERE teacher_id = ?
          AND course_id IN ({placeholders})
        ORDER BY
            COALESCE(semester_id, 0) DESC,
            CASE WHEN weekday IS NULL THEN 1 ELSE 0 END,
            weekday,
            section_text,
            id
        """,
        [int(teacher_id), *normalized_ids],
    ).fetchall()

    grouped: dict[int, list[dict]] = {course_id: [] for course_id in normalized_ids}
    for row in rows:
        item = summarize_academic_course_sync_item(row)
        if item.get("course_id"):
            grouped.setdefault(int(item["course_id"]), []).append(item)
    return grouped


def _load_teacher_academic_course_occurrence_summaries(
    conn,
    teacher_id: int,
    course_ids: list[int],
) -> dict[int, list[dict]]:
    normalized_ids = [int(course_id) for course_id in course_ids if course_id]
    if not normalized_ids:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT course_id,
               semester_id,
               teaching_class_name,
               class_composition,
               COUNT(*) AS session_count,
               MIN(session_date) AS first_session_date,
               MAX(session_date) AS last_session_date,
               SUM(CASE WHEN is_non_periodic THEN 1 ELSE 0 END) AS non_periodic_count,
               GROUP_CONCAT(DISTINCT location) AS locations
        FROM teacher_academic_course_session_occurrences
        WHERE teacher_id = ?
          AND course_id IN ({placeholders})
        GROUP BY course_id, semester_id, teaching_class_name, class_composition
        ORDER BY COALESCE(semester_id, 0) DESC, teaching_class_name
        """,
        [int(teacher_id), *normalized_ids],
    ).fetchall()
    grouped: dict[int, list[dict]] = {course_id: [] for course_id in normalized_ids}
    for row in rows:
        course_id = int(row["course_id"])
        locations = [
            value.strip()
            for value in str(row["locations"] or "").split(",")
            if value.strip()
        ]
        grouped.setdefault(course_id, []).append(
            {
                "course_id": course_id,
                "semester_id": int(row["semester_id"]) if row["semester_id"] else None,
                "teaching_class_name": str(row["teaching_class_name"] or "").strip(),
                "class_composition": str(row["class_composition"] or "").strip(),
                "session_count": int(row["session_count"] or 0),
                "first_session_date": str(row["first_session_date"] or ""),
                "last_session_date": str(row["last_session_date"] or ""),
                "non_periodic_count": int(row["non_periodic_count"] or 0),
                "locations": locations[:6],
            }
        )
    return grouped


def _load_teacher_course_rows(conn, teacher_id: int):
    teacher_scope = load_teacher_org_scope(conn, teacher_id)
    teacher_department = normalize_department(teacher_scope.get("department"))
    teacher_school_code = teacher_scope["school_code"]
    current_teacher_is_super_admin = is_super_admin_teacher(conn, teacher_id)
    rows = conn.execute(
        """
        SELECT c.id,
               c.name,
               c.department,
               c.description,
               c.sect_name,
               c.credits,
               c.total_hours,
               c.created_at,
               c.created_by_teacher_id,
               c.school_code,
               c.school_name,
               c.college,
               c.academic_source,
               c.academic_course_code,
               c.academic_sync_at,
               c.academic_sync_message,
               c.academic_metadata_json,
               t.name AS owner_teacher_name,
               COALESCE(o.offering_count, 0) AS offering_count
        FROM courses c
        LEFT JOIN teachers t ON t.id = c.created_by_teacher_id
        LEFT JOIN (
            SELECT course_id, COUNT(DISTINCT id) AS offering_count
            FROM class_offerings
            WHERE teacher_id = ?
            GROUP BY course_id
        ) o
            ON o.course_id = c.id
        WHERE c.created_by_teacher_id = ?
           OR (
                lower(TRIM(COALESCE(c.school_code, ?))) = lower(TRIM(?))
                AND ? != ''
                AND lower(TRIM(COALESCE(c.department, ''))) = lower(TRIM(?))
           )
        ORDER BY
            CASE WHEN c.created_by_teacher_id = ? THEN 0 ELSE 1 END,
            c.created_at DESC,
            c.name
        """,
        (
            teacher_id,
            teacher_id,
            teacher_school_code,
            teacher_school_code,
            teacher_department,
            teacher_department,
            teacher_id,
        ),
    ).fetchall()
    course_ids = [int(row["id"]) for row in rows]
    lessons_by_course = load_course_lessons_by_course_id(conn, course_ids)
    academic_items_by_course = _load_teacher_academic_course_items(conn, teacher_id, course_ids)
    academic_occurrences_by_course = _load_teacher_academic_course_occurrence_summaries(conn, teacher_id, course_ids)
    for course_id, lesson_items in lessons_by_course.items():
        lessons_by_course[course_id] = attach_learning_material_briefs(
            conn,
            lesson_items,
            teacher_id=teacher_id,
            markdown_only=True,
        )

    result = []
    for row in rows:
        course_id = int(row["id"])
        is_owned = int(row["created_by_teacher_id"] or 0) == int(teacher_id)
        item = serialize_course_row(
            row,
            lessons=lessons_by_course.get(course_id, []),
            offering_count=int(row["offering_count"] or 0),
        )
        item["is_owned"] = is_owned
        item["can_manage"] = is_owned or current_teacher_is_super_admin
        item["is_shared_course"] = not is_owned
        item["owner_teacher_name"] = str(row["owner_teacher_name"] or "").strip()
        item["school_code"] = str(row["school_code"] or "").strip()
        item["school_name"] = str(row["school_name"] or "").strip()
        item["college"] = str(row["college"] or "").strip()
        item["organization_label"] = organization_label(
            {
                "school_code": item["school_code"],
                "school_name": item["school_name"],
                "college": item["college"],
                "department": item["department"],
            }
        )
        sync_items = academic_items_by_course.get(course_id, [])
        occurrence_items = academic_occurrences_by_course.get(course_id, [])
        metadata = build_academic_course_metadata(item.get("academic_metadata_json"))
        item["academic_metadata"] = metadata
        item["academic_source"] = str(item.get("academic_source") or "")
        item["academic_course_code"] = str(item.get("academic_course_code") or "")
        item["academic_sync_at"] = str(item.get("academic_sync_at") or "")
        item["academic_sync_message"] = str(item.get("academic_sync_message") or "")
        item["academic_schedule_items"] = sync_items
        item["academic_schedule_preview"] = sync_items[:3]
        item["academic_schedule_count"] = len(sync_items) or int(metadata.get("schedule_item_count") or 0)
        item["academic_occurrence_classes"] = occurrence_items
        item["academic_occurrence_count"] = sum(int(entry.get("session_count") or 0) for entry in occurrence_items)
        item["academic_occurrence_preview"] = occurrence_items[:4]
        item["academic_is_synced"] = bool(item["academic_source"] or item["academic_course_code"] or sync_items)
        item["academic_follow_up_items"] = metadata.get("follow_up_items") if isinstance(metadata.get("follow_up_items"), list) else []
        item["academic_follow_up_hint"] = (
            "已同步教务课表，请继续补充教材、课堂设置和本平台班级绑定。"
            if item["academic_is_synced"]
            else ""
        )
        academic_search = " ".join(
            filter(
                None,
                [
                    item["academic_course_code"],
                    item["academic_sync_message"],
                    " ".join(str(sync_item.get("teaching_class_name") or "") for sync_item in sync_items),
                    " ".join(str(sync_item.get("time_text") or "") for sync_item in sync_items),
                    " ".join(str(sync_item.get("location") or "") for sync_item in sync_items),
                    " ".join(str(sync_item.get("classroom_type") or "") for sync_item in sync_items),
                    " ".join(str(sync_item.get("weeks_text") or "") for sync_item in sync_items),
                ],
            )
        )
        item["search_blob"] = " ".join(
            part
            for part in (
                item.get("search_blob"),
                academic_search,
                item.get("owner_teacher_name"),
                item.get("organization_label"),
            )
            if part
        ).lower()
        result.append(item)
    return result


def _decorate_course_grouping_context(courses: list[dict], semesters: list[dict]) -> None:
    semester_windows: list[tuple[Any, Any, dict]] = []
    for raw_semester in semesters:
        try:
            semester = serialize_semester_row(raw_semester)
            start_date = parse_date_input(semester.get("start_date"))
            end_date = parse_date_input(semester.get("end_date"))
        except Exception:
            continue
        if not start_date or not end_date:
            continue
        semester_windows.append((start_date, end_date, semester))

    semester_windows.sort(key=lambda item: (item[0], item[1], int(item[2].get("id") or 0)), reverse=True)

    for course in courses:
        department_label = str(course.get("department") or "").strip()
        course["department_group_label"] = department_label or "未指定系别"
        course["department_group_key"] = (
            f"department:{department_label.casefold()}" if department_label else "department:__unset__"
        )
        course["department_group_order"] = 1 if not department_label else 0
        course["department_group_meta"] = "课程尚未绑定系别" if not department_label else "绑定系别"

        try:
            created_date = parse_date_input(course.get("created_at"))
        except Exception:
            created_date = None
        course["created_date_label"] = created_date.isoformat() if created_date else "创建时间未知"

        matched_semester = None
        if created_date:
            for start_date, end_date, semester in semester_windows:
                if start_date <= created_date <= end_date:
                    matched_semester = (start_date, end_date, semester)
                    break

        if matched_semester:
            start_date, _end_date, semester = matched_semester
            semester_id = int(semester.get("id") or 0)
            semester_label = str(semester.get("name") or "").strip() or "未命名学期"
            course["created_semester_group_key"] = f"semester:{semester_id}" if semester_id else (
                f"semester:{semester_label.casefold()}"
            )
            course["created_semester_group_label"] = semester_label
            course["created_semester_group_meta"] = semester.get("display_range") or course["created_date_label"]
            course["created_semester_group_order"] = -start_date.toordinal()
        elif created_date:
            course["created_semester_group_key"] = "semester:__unmatched__"
            course["created_semester_group_label"] = "未匹配学期日历"
            course["created_semester_group_meta"] = f"创建于 {course['created_date_label']}"
            course["created_semester_group_order"] = 999_998
        else:
            course["created_semester_group_key"] = "semester:__unknown__"
            course["created_semester_group_label"] = "创建时间未知"
            course["created_semester_group_meta"] = "缺少创建日期"
            course["created_semester_group_order"] = 999_999


def _load_teacher_offering_rows(conn, teacher_id: int):
    rows = conn.execute(
        """
        SELECT o.id,
               o.class_id,
               o.course_id,
               o.semester_id,
               o.textbook_id,
               o.first_class_date,
               o.weekly_schedule_json,
               o.schedule_info,
               o.schedule_source,
               o.academic_teaching_class_name,
               o.academic_schedule_sync_at,
               o.academic_schedule_sync_message,
               COALESCE(s.name, o.semester) AS semester,
               c.name AS class_name,
               c.department AS class_department,
               co.name AS course_name,
               co.department AS course_department,
               co.sect_name AS course_sect_name,
               co.description,
               co.credits,
               tb.title AS textbook_title,
               COUNT(DISTINCT os.id) AS scheduled_session_count,
               SUM(CASE WHEN os.schedule_source = 'academic_sync' THEN 1 ELSE 0 END) AS academic_session_count,
               SUM(CASE WHEN os.is_non_periodic THEN 1 ELSE 0 END) AS non_periodic_session_count,
               MIN(os.session_date) AS scheduled_start_date,
               MAX(os.session_date) AS scheduled_end_date
        FROM class_offerings o
        JOIN classes c ON o.class_id = c.id
        JOIN courses co ON o.course_id = co.id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
        LEFT JOIN class_offering_sessions os ON os.class_offering_id = o.id
        WHERE o.teacher_id = ?
        GROUP BY o.id,
                 o.class_id,
                 o.course_id,
                 o.semester_id,
                 o.textbook_id,
                 o.first_class_date,
                 o.weekly_schedule_json,
                 o.schedule_info,
                 o.schedule_source,
                 o.academic_teaching_class_name,
                 o.academic_schedule_sync_at,
                 o.academic_schedule_sync_message,
                 s.name,
                 c.name,
                 c.department,
                 co.name,
                 co.department,
                 co.sect_name,
                 co.description,
                 co.credits,
                 tb.title
        ORDER BY COALESCE(s.start_date, o.first_class_date, o.created_at) DESC, co.name, c.name
        """,
        (teacher_id,),
    ).fetchall()
    offerings = []
    for row in rows:
        item = dict(row)
        item["weekly_schedule"] = _safe_parse_json_list(item.get("weekly_schedule_json"))
        item["scheduled_session_count"] = int(item.get("scheduled_session_count") or 0)
        item["academic_session_count"] = int(item.get("academic_session_count") or 0)
        item["non_periodic_session_count"] = int(item.get("non_periodic_session_count") or 0)
        item["schedule_source"] = str(item.get("schedule_source") or "fixed_cycle")
        item["schedule_source_label"] = "教务实际排课" if item["schedule_source"] == "academic_sync" else "固定周循环"
        item["academic_teaching_class_name"] = str(item.get("academic_teaching_class_name") or "")
        item["academic_schedule_sync_at"] = str(item.get("academic_schedule_sync_at") or "")
        item["academic_schedule_sync_message"] = str(item.get("academic_schedule_sync_message") or "")
        offerings.append(item)
    return offerings


def _is_embedded_manage_request(request: Request) -> bool:
    return str(request.query_params.get("embed") or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_manage_template_context(
    request: Request,
    user: dict,
    *,
    page_title: str,
    active_page: str,
    extra: dict | None = None,
) -> dict:
    current_teacher_is_super_admin = False
    if user.get("role") == "teacher":
        try:
            with get_db_connection() as conn:
                current_teacher_is_super_admin = is_super_admin_teacher(conn, user.get("id"))
        except Exception as exc:
            print(f"[MANAGE] 超管状态读取失败: {exc}")
    context = {
        "request": request,
        "user_info": user,
        "page_title": page_title,
        "active_page": active_page,
        "embedded_mode": _is_embedded_manage_request(request),
        "current_teacher_is_super_admin": current_teacher_is_super_admin,
    }
    if extra:
        context.update(extra)
    return context


def _build_manage_view_url(path: str, **params) -> str:
    query = {}
    for key, value in params.items():
        if value in (None, "", False):
            continue
        query[key] = value
    if not query:
        return path
    return f"{path}?{urlencode(query)}"


def _ensure_manage_super_admin(conn, user: dict) -> None:
    if not is_super_admin_teacher(conn, user.get("id")):
        raise HTTPException(status_code=403, detail="只有超管教师可以访问该系统管理页面。")


def _build_classroom_opening_workflow_snapshot(conn, teacher_id: int) -> dict:
    semester_rows = [serialize_semester_row(row) for row in load_teacher_semester_rows(conn, teacher_id)]
    counts = {
        "semesters": len(semester_rows),
        "current_semesters": sum(1 for item in semester_rows if item.get("is_current")),
        "courses": int(conn.execute(
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "course_lessons": int(conn.execute(
            """
            SELECT COUNT(*)
            FROM course_lessons lessons
            JOIN courses c ON c.id = lessons.course_id
            WHERE c.created_by_teacher_id = ?
            """,
            (teacher_id,),
        ).fetchone()[0] or 0),
        "textbooks": int(conn.execute(
            "SELECT COUNT(*) FROM textbooks WHERE teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "materials": int(conn.execute(
            "SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND name != '.git'",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "classes": int(conn.execute(
            "SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "offerings": int(conn.execute(
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "ai_configs": int(conn.execute(
            """
            SELECT COUNT(*)
            FROM ai_class_configs cfg
            JOIN class_offerings o ON o.id = cfg.class_offering_id
            WHERE o.teacher_id = ?
            """,
            (teacher_id,),
        ).fetchone()[0] or 0),
    }

    def status_for(ready: bool, partial: bool = False, optional: bool = False) -> str:
        if ready:
            return "complete"
        if partial:
            return "in_progress"
        if optional:
            return "optional"
        return "pending"

    def step(
        step_id: str,
        index: int,
        title: str,
        eyebrow: str,
        description: str,
        summary: str,
        advice: str,
        *,
        count: int = 0,
        ready: bool = False,
        partial: bool = False,
        optional: bool = False,
        badge_text: str = "",
        meta_label: str = "",
        checklist: list[dict] | None = None,
    ) -> dict:
        status = status_for(ready, partial, optional)
        status_label_map = {
            "complete": "已就绪",
            "in_progress": "可继续",
            "optional": "可跳过",
            "pending": "待处理",
        }
        return {
            "id": step_id,
            "title": title,
            "order_label": f"第 {index} 步",
            "eyebrow": eyebrow,
            "description": description,
            "summary": summary,
            "advice": advice,
            "status": status,
            "status_label": status_label_map[status],
            "count": count,
            "badge_count": count,
            "badge_text": badge_text or f"已有 {count} 项",
            "meta_label": meta_label or status_label_map[status],
            "checklist": checklist or [],
        }

    steps = [
        step(
            "semester",
            1,
            "选择学期",
            "先确定时间范围",
            "课堂从学期开始，先确认本次课程属于哪个学期，后续排课、周次和课堂时间轴才不会错位。",
            f"当前已有 {counts['semesters']} 个学期，其中 {counts['current_semesters']} 个覆盖今天。",
            "如果还没有本学期，先新建学期；如果已有，开课向导会直接让老师单击选择。",
            count=counts["semesters"],
            ready=counts["semesters"] > 0,
            partial=counts["current_semesters"] > 0,
            badge_text=f"{counts['semesters']} 个学期",
            meta_label="学期列表",
            checklist=[
                {
                    "title": "学期可选",
                    "ready": counts["semesters"] > 0,
                    "status": f"{counts['semesters']} 个",
                    "description": "开课必须绑定一个学期，用于计算周次与课堂日期。",
                },
                {
                    "title": "当前学期",
                    "ready": counts["current_semesters"] > 0,
                    "status": f"{counts['current_semesters']} 个",
                    "description": "覆盖今天的学期会更适合作为默认推荐。",
                },
            ],
        ),
        step(
            "course",
            2,
            "确认课程",
            "课程名称与系别",
            "输入课程名称后，系统会推荐相似旧课程；老师可绑定旧课程，也可把同名课程作为新课程创建。",
            f"当前已有 {counts['courses']} 门课程模板。",
            "课程名称可以先简单确认，课程简介、学时、学分和课次会在后面的细节步骤继续补齐。",
            count=counts["courses"],
            ready=counts["courses"] > 0,
            badge_text=f"{counts['courses']} 门课程",
            meta_label="相似课程",
            checklist=[
                {
                    "title": "课程模板",
                    "ready": counts["courses"] > 0,
                    "status": f"{counts['courses']} 门",
                    "description": "已有模板会在向导里作为可绑定对象出现。",
                },
                {
                    "title": "课次基础",
                    "ready": counts["course_lessons"] > 0,
                    "status": f"{counts['course_lessons']} 条",
                    "description": "旧课程若已有课次，开课后课堂时间轴会更完整。",
                },
            ],
        ),
        step(
            "textbook",
            3,
            "选择教材",
            "建立知识依据",
            "教材是课程简介、课次生成和 AI 助教上下文的重要来源，向导会优先推荐与课程相关的教材。",
            f"当前已有 {counts['textbooks']} 本教材。",
            "没有合适教材时可在向导里新建；新增后会自动出现在候选列表顶部并被选中。",
            count=counts["textbooks"],
            ready=counts["textbooks"] > 0,
            badge_text=f"{counts['textbooks']} 本教材",
            meta_label="教材候选",
            checklist=[
                {
                    "title": "教材库",
                    "ready": counts["textbooks"] > 0,
                    "status": f"{counts['textbooks']} 本",
                    "description": "教材越完整，AI 生成课程简介和课堂设置越稳定。",
                },
            ],
        ),
        step(
            "materials",
            4,
            "导入材料",
            "文件夹与文档",
            "课程材料通常以文件夹保存，向导会按目录树展示，可选择根目录、文件夹或具体文件。",
            f"当前已有 {counts['materials']} 个材料节点；此步骤允许跳过。",
            "材料可以先跳过，之后再用管理中心导入文件夹，或用深度思考 AI 辅助生成与绑定。",
            count=counts["materials"],
            ready=counts["materials"] > 0,
            optional=counts["materials"] == 0,
            badge_text=f"{counts['materials']} 个材料",
            meta_label="可跳过",
            checklist=[
                {
                    "title": "材料库",
                    "ready": counts["materials"] > 0,
                    "status": f"{counts['materials']} 个",
                    "description": "有根目录时向导默认只展示根目录，避免文件列表过长。",
                },
                {
                    "title": "跳过策略",
                    "ready": True,
                    "status": "允许",
                    "description": "没有材料也可以继续开课，后续仍可补充。",
                },
            ],
        ),
        step(
            "class",
            5,
            "选择班级",
            "系别与学生名单",
            "班级按系别归属管理，向导会把与课程系别关联的班级推荐到前面。",
            f"当前已有 {counts['classes']} 个班级。",
            "如果班级还没录入，可先在向导里新建班级，再继续完成课堂开设。",
            count=counts["classes"],
            ready=counts["classes"] > 0,
            badge_text=f"{counts['classes']} 个班级",
            meta_label="班级候选",
            checklist=[
                {
                    "title": "班级可选",
                    "ready": counts["classes"] > 0,
                    "status": f"{counts['classes']} 个",
                    "description": "课程与班级同属系别时，后续统计和推荐更准确。",
                },
            ],
        ),
        step(
            "details",
            6,
            "补充细节",
            "学分、学时与课次",
            "系统会根据学时推算学分，并用课程名称与教材辅助生成简介、课次和材料绑定建议。",
            f"当前已有 {counts['course_lessons']} 条课程课次设置。",
            "这一页适合把自动带入的内容检查一遍，尤其是学时合计和课次小节数是否一致。",
            count=counts["course_lessons"],
            ready=counts["course_lessons"] > 0,
            partial=counts["courses"] > 0,
            badge_text=f"{counts['course_lessons']} 条课次",
            meta_label="课堂设置",
            checklist=[
                {
                    "title": "学时与学分",
                    "ready": counts["courses"] > 0,
                    "status": "自动计算",
                    "description": "系统按 8 学时 0.5 学分的规则推算，可由老师调整。",
                },
                {
                    "title": "课次设置",
                    "ready": counts["course_lessons"] > 0,
                    "status": f"{counts['course_lessons']} 条",
                    "description": "课次合计小节数需要与总学时一致。",
                },
            ],
        ),
        step(
            "ai",
            7,
            "配置 AI 助教",
            "生成课堂助手",
            "根据学期、课程、教材、材料和班级，生成课堂 AI 助教的上下文与提示词。",
            f"当前已有 {counts['ai_configs']} 个课堂完成 AI 配置。",
            "建议先完成课堂创建，再进入 AI 配置；已有内容越完整，助手越容易给出贴合课程的回答。",
            count=counts["ai_configs"],
            ready=counts["offerings"] > 0 and counts["ai_configs"] >= counts["offerings"],
            partial=counts["ai_configs"] > 0,
            badge_text=f"{counts['ai_configs']} 个配置",
            meta_label="AI 助教",
            checklist=[
                {
                    "title": "已开课堂",
                    "ready": counts["offerings"] > 0,
                    "status": f"{counts['offerings']} 个",
                    "description": "AI 助教配置依赖具体课堂。",
                },
                {
                    "title": "AI 配置",
                    "ready": counts["ai_configs"] > 0,
                    "status": f"{counts['ai_configs']} 个",
                    "description": "每个课堂都可以保留自己的助手设置。",
                },
            ],
        ),
        step(
            "success",
            8,
            "完成开课",
            "进入课堂继续使用",
            "完成向导后会创建课堂，并提供进入课堂页的按钮。",
            f"当前已开设 {counts['offerings']} 个课堂。",
            "创建成功后建议直接进入课堂页，检查时间轴、材料入口和 AI 助手是否符合预期。",
            count=counts["offerings"],
            ready=counts["offerings"] > 0,
            badge_text=f"{counts['offerings']} 个课堂",
            meta_label="开课结果",
            checklist=[
                {
                    "title": "课堂数量",
                    "ready": counts["offerings"] > 0,
                    "status": f"{counts['offerings']} 个",
                    "description": "已有课堂可在下方列表继续编辑或进入。",
                },
            ],
        ),
    ]

    if counts["semesters"] == 0:
        recommended_stage = "semester"
    elif counts["courses"] == 0:
        recommended_stage = "course"
    elif counts["textbooks"] == 0:
        recommended_stage = "textbook"
    elif counts["classes"] == 0:
        recommended_stage = "class"
    elif counts["offerings"] == 0:
        recommended_stage = "details"
    elif counts["ai_configs"] < counts["offerings"]:
        recommended_stage = "ai"
    else:
        recommended_stage = "success"

    stage_views = {
        "semester": {"href": "/manage/semesters", "embed_url": _build_manage_view_url("/manage/semesters", embed=1)},
        "course": {"href": "/manage/courses", "embed_url": _build_manage_view_url("/manage/courses", embed=1)},
        "textbook": {"href": "/manage/textbooks", "embed_url": _build_manage_view_url("/manage/textbooks", embed=1)},
        "materials": {"href": "/manage/materials", "embed_url": _build_manage_view_url("/manage/materials", embed=1)},
        "class": {"href": "/manage/classes", "embed_url": _build_manage_view_url("/manage/classes", embed=1)},
        "details": {"href": "/manage/courses", "embed_url": _build_manage_view_url("/manage/courses", embed=1)},
        "ai": {"href": "/manage/ai", "embed_url": _build_manage_view_url("/manage/ai", embed=1)},
        "success": {"href": "/manage/offerings", "embed_url": _build_manage_view_url("/manage/offerings", embed=1)},
    }

    return {
        "counts": counts,
        "prep_resources": [],
        "steps": steps,
        "recommended_stage": recommended_stage,
        "recommended_prep": "",
        "stage_views": stage_views,
    }


def _build_manage_workflow_snapshot(conn, teacher_id: int) -> dict:
    semester_rows = [serialize_semester_row(row) for row in load_teacher_semester_rows(conn, teacher_id)]

    counts = {
        "classes": int(conn.execute(
            "SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "courses": int(conn.execute(
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "textbooks": int(conn.execute(
            "SELECT COUNT(*) FROM textbooks WHERE teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "exams": int(conn.execute(
            "SELECT COUNT(*) FROM exam_papers WHERE teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "materials": int(conn.execute(
            "SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND name != '.git'",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "signatures": int((build_signature_dashboard_context(
            conn,
            {"id": teacher_id, "role": "teacher"},
        ).get("signature_stats") or {}).get("visible_total") or 0),
        "semesters": len(semester_rows),
        "current_semesters": sum(1 for item in semester_rows if item.get("is_current")),
        "offerings": int(conn.execute(
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0] or 0),
        "ai_configs": int(conn.execute(
            """
            SELECT COUNT(*)
            FROM ai_class_configs cfg
            JOIN class_offerings o ON o.id = cfg.class_offering_id
            WHERE o.teacher_id = ?
            """,
            (teacher_id,),
        ).fetchone()[0] or 0),
    }

    resource_definitions = [
        {
            "id": "classes",
            "title": "班级",
            "description": "先准备好班级和学生名单，开课时才能直接绑定到课堂。",
            "count_key": "classes",
            "href": "/manage/classes",
        },
        {
            "id": "courses",
            "title": "课程",
            "description": "课程模板决定后续课堂的教学结构、学时和内容映射。",
            "count_key": "courses",
            "href": "/manage/courses",
        },
        {
            "id": "textbooks",
            "title": "教材",
            "description": "教材用于开课绑定，也会作为 AI 助教的重要知识依据。",
            "count_key": "textbooks",
            "href": "/manage/textbooks",
        },
        {
            "id": "exams",
            "title": "试卷",
            "description": "试卷和考试资源可跨学期复用，适合在准备阶段统一整理。",
            "count_key": "exams",
            "href": "/manage/exams",
        },
        {
            "id": "materials",
            "title": "材料",
            "description": "课堂材料和文档目录建议提前维护，便于课程与课堂持续复用。",
            "count_key": "materials",
            "href": "/manage/materials",
        },
        {
            "id": "signatures",
            "title": "签名",
            "description": "维护教师、学生与平台导入的电子签名，后续导出和审批可直接调用。",
            "count_key": "signatures",
            "href": "/manage/signatures",
        },
    ]

    prep_resources = []
    for item in resource_definitions:
        count = counts[item["count_key"]]
        prep_resources.append({
            **item,
            "count": count,
            "ready": count > 0,
            "status_label": "已准备" if count > 0 else "待准备",
            "embed_url": _build_manage_view_url(item["href"], embed=1),
        })

    prep_ready_count = sum(1 for item in prep_resources if item["ready"])
    prep_inventory_count = sum(int(item["count"]) for item in prep_resources)

    def resolve_step_status(*, completed: bool, partial: bool) -> str:
        if completed:
            return "complete"
        if partial:
            return "in_progress"
        return "pending"

    steps = [
        {
            "id": "preparation",
            "title": "流程前准备",
            "eyebrow": "准备基础资源",
            "description": "先整理班级、课程、教材、试卷和材料，再进入学期与课堂流程会更顺畅。",
            "summary": f"已准备 {prep_ready_count}/{len(prep_resources)} 项基础资源",
            "status": resolve_step_status(
                completed=prep_ready_count == len(prep_resources),
                partial=prep_ready_count > 0,
            ),
            "count": prep_ready_count,
            "badge_count": prep_inventory_count,
            "badge_text": f"已有 {prep_inventory_count} 份",
            "total": len(prep_resources),
        },
        {
            "id": "semester",
            "title": "确认学期",
            "eyebrow": "统一学期范围",
            "description": "先定义学期区间和周次规则，后续开设课堂时才能共享一致的时间基准。",
            "summary": f"已创建 {counts['semesters']} 个学期",
            "status": resolve_step_status(
                completed=counts["semesters"] > 0,
                partial=counts["current_semesters"] > 0,
            ),
            "count": counts["semesters"],
            "badge_count": counts["semesters"],
            "badge_text": f"已有 {counts['semesters']} 份",
        },
        {
            "id": "offerings",
            "title": "开设课堂",
            "eyebrow": "绑定学期与教学资源",
            "description": "将学期、班级、课程和教材组合为具体课堂，并生成课堂排期与内容映射。",
            "summary": f"已开设 {counts['offerings']} 个课堂",
            "status": resolve_step_status(
                completed=counts["offerings"] > 0,
                partial=counts["offerings"] > 0,
            ),
            "count": counts["offerings"],
            "badge_count": counts["offerings"],
            "badge_text": f"已有 {counts['offerings']} 份",
        },
        {
            "id": "ai",
            "title": "配置 AI 助教",
            "eyebrow": "完善课堂支持",
            "description": "为具体课堂绑定教材、提示词和知识依据，让 AI 助教具备可落地的教学语境。",
            "summary": f"已完成 {counts['ai_configs']} 个课堂 AI 配置",
            "status": resolve_step_status(
                completed=counts["ai_configs"] > 0 and counts["ai_configs"] >= counts["offerings"] > 0,
                partial=counts["ai_configs"] > 0,
            ),
            "count": counts["ai_configs"],
            "badge_count": counts["ai_configs"],
            "badge_text": f"已有 {counts['ai_configs']} 份",
        },
    ]

    if prep_ready_count < len(prep_resources):
        recommended_stage = "preparation"
    elif counts["semesters"] == 0:
        recommended_stage = "semester"
    elif counts["offerings"] == 0:
        recommended_stage = "offerings"
    else:
        recommended_stage = "ai"

    recommended_prep = next((item["id"] for item in prep_resources if not item["ready"]), prep_resources[0]["id"])

    stage_views = {
        "semester": {
            "href": "/manage/semesters",
            "embed_url": _build_manage_view_url("/manage/semesters", embed=1),
        },
        "offerings": {
            "href": "/manage/offerings",
            "embed_url": _build_manage_view_url("/manage/offerings", embed=1),
        },
        "ai": {
            "href": "/manage/ai",
            "embed_url": _build_manage_view_url("/manage/ai", embed=1),
        },
    }

    return {
        "counts": counts,
        "prep_resources": prep_resources,
        "steps": steps,
        "recommended_stage": recommended_stage,
        "recommended_prep": recommended_prep,
        "stage_views": stage_views,
    }


@router.get("/manage/semesters", response_class=HTMLResponse)
async def get_manage_semesters_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        semester_calendar = build_semester_calendar_payload(
            load_teacher_semester_rows(conn, int(user["id"])),
        )

    current_date = china_today()
    semesters = semester_calendar["semesters"]

    return templates.TemplateResponse(
        request,
        "manage/semesters.html",
        _build_manage_template_context(
            request,
            user,
            page_title="学期管理",
            active_page="semesters",
            extra={
                "semesters": semesters,
                "semester_calendar": semester_calendar,
                "semester_defaults": build_semester_defaults(current_date),
            },
        ),
    )


@router.get("/manage/textbooks", response_class=HTMLResponse)
async def get_manage_textbooks_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        textbooks = [
            serialize_textbook_row(row)
            for row in _load_teacher_textbook_rows(conn, int(user["id"]))
        ]

    return templates.TemplateResponse(
        request,
        "manage/textbooks.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教材管理",
            active_page="textbooks",
            extra={
                "textbooks": textbooks,
                "textbooks_json": textbooks,
            },
        ),
    )


@router.get("/manage/signatures", response_class=HTMLResponse)
async def get_manage_signatures_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        signature_context = build_signature_dashboard_context(conn, user)

    return templates.TemplateResponse(
        request,
        "manage/signatures.html",
        _build_manage_template_context(
            request,
            user,
            page_title="电子签名",
            active_page="signatures",
            extra=signature_context,
        ),
    )


@router.get("/manage/offerings", response_class=HTMLResponse)
async def get_manage_offerings_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        my_classes = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, department FROM classes WHERE created_by_teacher_id = ? ORDER BY name",
                (user["id"],),
            ).fetchall()
        ]
        my_courses = _load_teacher_course_rows(conn, int(user["id"]))
        semester_rows = load_teacher_semester_rows(conn, int(user["id"]))
        textbook_rows = _load_teacher_textbook_rows(conn, int(user["id"]))
        my_semesters = [serialize_semester_row(row) for row in semester_rows]
        my_textbooks = [
            {
                "id": item["id"],
                "title": item["title"],
                "author_display": item["author_display"],
                "publication_year": item["publication_year"],
                "publisher": item["publisher"],
            }
            for item in (serialize_textbook_row(row) for row in textbook_rows)
        ]
        my_offerings = _load_teacher_offering_rows(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/offerings.html",
        _build_manage_template_context(
            request,
            user,
            page_title="开设课堂",
            active_page="offerings",
            extra={
                "my_classes": my_classes,
                "my_courses": my_courses,
                "my_semesters": my_semesters,
                "my_textbooks": my_textbooks,
                "my_offerings": my_offerings,
                "default_semester_id": choose_default_semester_id(my_semesters),
                "department_options": collect_department_options(
                    (item.get("department") for item in my_classes),
                    (item.get("department") for item in my_courses),
                ),
            },
        ),
    )


@router.get("/manage/ai", response_class=HTMLResponse)
async def get_manage_ai_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        my_offerings = _load_teacher_offering_rows(conn, int(user["id"]))
        my_textbooks = [
            serialize_textbook_row(row)
            for row in _load_teacher_textbook_rows(conn, int(user["id"]))
        ]

    return templates.TemplateResponse(
        request,
        "manage/ai.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课堂 AI 助教",
            active_page="ai",
            extra={
                "my_offerings": my_offerings,
                "my_textbooks": my_textbooks,
            },
        ),
    )


@router.get("/manage/system", response_class=HTMLResponse)
async def get_manage_system_redirect(request: Request, user: dict = Depends(get_current_teacher)):
    """重定向旧的系统管理页面到当前教师可访问的系统页。"""
    with get_db_connection() as conn:
        if is_super_admin_teacher(conn, user["id"]):
            return RedirectResponse(url="/manage/system/users", status_code=302)
    return RedirectResponse(url="/manage/system/password-resets", status_code=302)


@router.get("/manage/system/academic-integrations", response_class=HTMLResponse)
async def get_manage_system_academic_integrations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师个人教务系统账号与适配器管理页面。"""
    profiles = list_academic_system_profiles()
    with get_db_connection() as conn:
        credentials = list_teacher_academic_credentials(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/system/academic_integrations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教务系统对接",
            active_page="system_academic_integrations",
            extra={
                "academic_profiles": profiles,
                "academic_credentials": credentials,
            },
        ),
    )


@router.get("/manage/system/smart-classroom-integrations", response_class=HTMLResponse)
async def get_manage_system_smart_classroom_integrations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师个人智慧课堂账号与点名同步管理页面。"""
    profiles = list_smart_classroom_profiles()
    with get_db_connection() as conn:
        credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/system/smart_classroom_integrations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="智慧课堂对接",
            active_page="system_smart_classroom_integrations",
            extra={
                "smart_classroom_profiles": profiles,
                "smart_classroom_credentials": credentials,
            },
        ),
    )


@router.get("/manage/system/users", response_class=HTMLResponse)
async def get_manage_system_users_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师账号与超管授权管理页面。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        teacher_accounts = list_teacher_accounts(conn)
        teacher_account_summary = build_teacher_account_summary(conn)

    return templates.TemplateResponse(
        request,
        "manage/system/users.html",
        _build_manage_template_context(
            request,
            user,
            page_title="用户管理",
            active_page="system_users",
            extra={
                "teacher_accounts": teacher_accounts,
                "teacher_account_summary": teacher_account_summary,
                "teacher_password_hint": TEACHER_PASSWORD_HINT,
                "initial_super_admin_email": INITIAL_SUPER_ADMIN_EMAIL,
                "initial_super_admin_name": INITIAL_SUPER_ADMIN_NAME,
            },
        ),
    )


@router.get("/manage/system/super-admin", response_class=HTMLResponse)
async def get_manage_system_super_admin_page(request: Request, user: dict = Depends(get_current_teacher)):
    """兼容旧超管设置入口，统一进入用户管理页。"""
    return RedirectResponse(url="/manage/system/users", status_code=302)


@router.get("/manage/system/organizations", response_class=HTMLResponse)
async def get_manage_system_organizations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """学校、学院、系部组织目录管理页面。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        organization_payload = list_organization_tree(conn)
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])

    return templates.TemplateResponse(
        request,
        "manage/system/organizations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="学校组织",
            active_page="system_organizations",
            extra={
                "organization_payload": organization_payload,
                "current_teacher_is_super_admin": current_teacher_is_super_admin,
            },
        ),
    )


@router.get("/manage/system/feedback", response_class=HTMLResponse)
async def get_manage_system_feedback_page(request: Request, user: dict = Depends(get_current_teacher)):
    """问题反馈查看页面，仅超管教师可查看完整内容。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])

        feedback_items = []
        feedback_attachments = {}
        if current_teacher_is_super_admin:
            feedback_items = conn.execute(
                """
                SELECT f.id, f.user_id, f.user_role, f.user_name, f.feedback_type,
                       f.section, f.title, f.description, f.page_url, f.status,
                       f.created_at, f.updated_at,
                       COUNT(a.id) AS attachment_count
                FROM app_feedback f
                LEFT JOIN app_feedback_attachments a ON a.feedback_id = f.id
                GROUP BY f.id
                ORDER BY f.created_at DESC, f.id DESC
                LIMIT 120
                """
            ).fetchall()
            feedback_ids = [int(row["id"]) for row in feedback_items]
            if feedback_ids:
                placeholders = ",".join("?" for _ in feedback_ids)
                attachment_rows = conn.execute(
                    f"""
                    SELECT id, feedback_id, file_hash, original_filename, file_size, mime_type, created_at
                    FROM app_feedback_attachments
                    WHERE feedback_id IN ({placeholders})
                    ORDER BY feedback_id DESC, id ASC
                    """,
                    tuple(feedback_ids),
                ).fetchall()
                for attachment in attachment_rows:
                    feedback_attachments.setdefault(int(attachment["feedback_id"]), []).append(dict(attachment))

    return templates.TemplateResponse(
        request,
        "manage/system/feedback.html",
        _build_manage_template_context(
            request,
            user,
            page_title="问题反馈",
            active_page="system_feedback",
            extra={
                "current_teacher_is_super_admin": current_teacher_is_super_admin,
                "feedback_items": feedback_items,
                "feedback_attachments": feedback_attachments,
            },
        ),
    )


@router.get("/manage/system/diagnostics", response_class=HTMLResponse)
async def get_manage_system_diagnostics_page(request: Request, user: dict = Depends(get_current_teacher)):
    """压测与诊断页面，展示后端健康状态、运行时指标和压测工具。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
    return templates.TemplateResponse(
        request,
        "manage/system/diagnostics.html",
        _build_manage_template_context(
            request,
            user,
            page_title="压测与诊断",
            active_page="system_diagnostics",
        ),
    )


@router.get("/manage/system/agent-keys", response_class=HTMLResponse)
async def get_manage_system_agent_keys_page(request: Request, user: dict = Depends(get_current_teacher)):
    """Agent runtime API key management page."""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        dashboard = build_agent_key_dashboard(conn)

    return templates.TemplateResponse(
        request,
        "manage/system/agent_keys.html",
        _build_manage_template_context(
            request,
            user,
            page_title="Agent Key 管理",
            active_page="system_agent_keys",
            extra={
                "agent_key_dashboard": dashboard,
            },
        ),
    )


@router.get("/manage/system/blog-crawler", response_class=HTMLResponse)
async def get_manage_system_blog_crawler_page(request: Request, user: dict = Depends(get_current_teacher)):
    """AI blog news crawler management page."""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        dashboard = load_blog_news_crawler_dashboard(conn)
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])

    return templates.TemplateResponse(
        request,
        "manage/system/blog_crawler.html",
        _build_manage_template_context(
            request,
            user,
            page_title="AI博客管家",
            active_page="system_blog_crawler",
            extra={
                "crawler_dashboard": dashboard,
                "current_teacher_is_super_admin": current_teacher_is_super_admin,
            },
        ),
    )


@router.get("/manage/system/password-resets", response_class=HTMLResponse)
async def get_manage_system_password_resets_page(request: Request, user: dict = Depends(get_current_teacher)):
    """学生找回密码申请审核页面。"""
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
              AND class_id IN (
                  SELECT id FROM classes WHERE created_by_teacher_id = ?
              )
            """,
            (user["id"], user["id"]),
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
              AND c.created_by_teacher_id = ?
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
            (user["id"], user["id"]),
        ).fetchall()

    return templates.TemplateResponse(
        request,
        "manage/system/password_resets.html",
        _build_manage_template_context(
            request,
            user,
            page_title="找回密码申请",
            active_page="system_password_resets",
            extra={
                "system_summary": dict(system_summary) if system_summary else {},
                "login_summary": dict(login_summary) if login_summary else {},
                "reset_requests": reset_requests,
            },
        ),
    )


# ============================
# V4.5: 试卷库管理路由
# ============================

@router.get("/manage/exams", response_class=HTMLResponse)
async def manage_exams_page(request: Request, user: dict = Depends(get_current_teacher)):
    """试卷库管理页面"""
    def _extract_exam_metrics(question_data: Any) -> dict[str, Any]:
        pages = question_data.get("pages", []) if isinstance(question_data, dict) else []
        if not isinstance(pages, list):
            pages = []

        type_counts: dict[str, int] = {}
        question_count = 0
        total_points = 0.0

        for page in pages:
            questions = page.get("questions", []) if isinstance(page, dict) else []
            if not isinstance(questions, list):
                continue
            for question in questions:
                if not isinstance(question, dict):
                    continue
                question_count += 1
                qtype = str(question.get("type") or "").strip()
                if qtype:
                    type_counts[qtype] = type_counts.get(qtype, 0) + 1

                point_value = question.get("points") if question.get("points") is not None else question.get("score")
                if point_value is None:
                    point_value = question.get("max_score")
                if point_value is None and isinstance(question.get("grading"), dict):
                    point_value = question["grading"].get("points")
                try:
                    total_points += float(point_value or 0)
                except (TypeError, ValueError):
                    pass

        question_types = set(type_counts)
        objective_types = {"radio", "checkbox"}
        subjective_types = {"text", "textarea"}
        if question_count == 0:
            profile = "empty"
        elif question_types and question_types <= objective_types:
            profile = "objective"
        elif question_types and question_types <= subjective_types:
            profile = "subjective"
        else:
            profile = "mixed"

        return {
            "page_count": len(pages),
            "question_count": question_count,
            "total_points": round(total_points, 1),
            "question_type_counts": type_counts,
            "question_profile": profile,
            "question_types": sorted(question_types),
        }

    def _resolve_exam_source(paper: dict[str, Any]) -> str:
        if paper.get("ai_gen_task_id") or paper.get("ai_gen_status"):
            return "ai"
        return "manual"

    with get_db_connection() as conn:
        # 兼容旧版本：已完成但仍停留在 generating 的试卷应进入可用状态。
        conn.execute(
            """UPDATE exam_papers SET status = 'ready', updated_at = ?
               WHERE teacher_id = ? AND status = 'generating' AND ai_gen_status = 'completed'""",
            (datetime.now().isoformat(), user['id'])
        )
        conn.commit()

        papers_cursor = conn.execute(
            """SELECT ep.*,
                      (SELECT COUNT(*)
                       FROM assignments a
                       WHERE a.exam_paper_id = ep.id
                         AND NOT EXISTS (
                             SELECT 1 FROM learning_stage_exam_attempts lsea
                             WHERE lsea.assignment_id = a.id
                         )) as assigned_count
               FROM exam_papers ep
               WHERE ep.teacher_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM learning_stage_exam_attempts lsea
                     WHERE lsea.exam_paper_id = ep.id
                 )
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
            # 解析 tags_json
            if paper.get('tags_json'):
                try:
                    paper['tags_json'] = json.loads(paper['tags_json'])
                except (json.JSONDecodeError, TypeError):
                    paper['tags_json'] = []
            else:
                paper['tags_json'] = []
            metrics = _extract_exam_metrics(paper.get('questions_json'))
            paper.update(metrics)
            paper['source_type'] = _resolve_exam_source(paper)
            papers.append(paper)

    return templates.TemplateResponse(
        request,
        "manage/exams.html",
        _build_manage_template_context(
            request,
            user,
            page_title="试卷管理",
            active_page="exams",
            extra={
                "papers": papers,
                "learning_stage_options": get_learning_stage_options(),
            },
        ),
    )


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
        if is_personal_stage_exam_paper(conn, exam_id):
            raise HTTPException(404, "学生个人试炼不进入教师试卷库")

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
        conn.commit()

    can_manage_submission_files = bool(
        user.get("role") == "teacher"
        and submission.get("status") != "grading"
        and (
            submission.get("status") != "graded"
            or int(submission.get("resubmission_allowed") or 0)
        )
    )
    attachment_locked_reason = ""
    if user.get("role") == "teacher" and submission.get("status") == "grading":
        attachment_locked_reason = "AI 正在批改中，附件暂不可修改。"
    elif (
        user.get("role") == "teacher"
        and submission.get("status") == "graded"
        and not int(submission.get("resubmission_allowed") or 0)
    ):
        attachment_locked_reason = "已批改成功的提交需要先撤回，才能修改附件。"

    return templates.TemplateResponse(request, "submission_detail.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "submission": submission,
        "submission_files": submission_files,
        "exam_questions": exam_questions,
        "can_manage_submission_files": can_manage_submission_files,
        "attachment_locked_reason": attachment_locked_reason,
        "ai_grading_upload_extensions": AI_GRADING_UPLOAD_EXTENSIONS,
        "ai_grading_supported_types_label": AI_GRADING_SUPPORTED_TYPES_LABEL,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
        "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
        "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
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
        assignment_back_url = _assignment_back_url(assignment)
        if user["role"] == "student" and not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")

        if not assignment.get('exam_paper_id'):
            # 不是试卷型作业，跳转到普通作业页
            return RedirectResponse(url=f"/assignment/{assignment_id}")

        if user['role'] == 'student' and assignment['status'] == 'new':
            return templates.TemplateResponse(request, "status.html",
                {"request": request, "success": False, "message": "该考试尚未发布", "back_url": assignment_back_url})

        paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (assignment['exam_paper_id'],)).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")
        paper_dict = dict(paper)
        paper_data = _load_json_object(paper_dict.get("questions_json"))
        exam_config = _load_json_object(paper_dict.get("exam_config_json"))
        exam_ai_allowed = (
            user["role"] == "student"
            and bool(assignment.get("class_offering_id"))
            and _exam_allows_student_ai(paper_data, exam_config)
        )
        exam_ai_context = _build_exam_ai_context(assignment, paper_dict, paper_data) if exam_ai_allowed else ""
        if user["role"] == "student":
            paper_dict["questions_json"] = json.dumps(strip_exam_scoring_for_student(paper_data), ensure_ascii=False)

        # 检查学生是否已提交
        submission = None
        submission_files = []
        if user['role'] == 'student':
            submission_row = conn.execute(
                "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
                (assignment_id, user['id'])
            ).fetchone()
            submission = dict(submission_row) if submission_row else None
            if submission and int(submission.get("is_absence_score") or 0):
                submission = None
            if submission:
                files_cursor = conn.execute(
                    "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
                    (submission['id'],)
                )
                submission_files = _serialize_submission_file_rows(files_cursor)
        conn.commit()

    submission_returned = bool(submission and submission_is_returned(submission))
    resubmission_state = submission_resubmission_state(submission) if submission else "none"
    can_resubmit_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and resubmission_state == "open"
    )
    can_withdraw_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and assignment_accepts_submissions(assignment)
        and not submission_returned
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
        "assignment_back_url": assignment_back_url,
        "paper": paper_dict,
        "submission": submission,
        "submission_files": submission_files,
        "exam_ai_allowed": exam_ai_allowed,
        "exam_ai_context": exam_ai_context,
        "can_withdraw_submission": can_withdraw_submission,
        "can_resubmit_submission": can_resubmit_submission,
        "submission_returned": submission_returned,
        "resubmission_state": resubmission_state,
        "resubmission_due_at": submission.get("resubmission_due_at") if submission else None,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
        "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
        "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
    })
