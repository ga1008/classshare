from collections.abc import Mapping
from datetime import datetime
import json
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Form, HTTPException, Depends, status, UploadFile, File, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from typing import Optional, List, Any
from pathlib import Path
import pandas as pd

from ...core import templates, COURSE_INFO
# 修复：移除不再需要的 TEACHER_PASS, SHARE_DIR, ROSTER_DIR
from ...config import (
    AI_GRADING_STALE_MINUTES,
    INITIAL_SUPER_ADMIN_EMAIL,
    INITIAL_SUPER_ADMIN_NAME,
    MAX_SUBMISSION_FILE_COUNT,
    MAX_UPLOAD_SIZE_MB,
    MAX_SUBMISSION_PER_FILE_MB,
    MAX_SUBMISSION_TOTAL_MB,
)
from ...dependencies import (
    get_current_user, get_current_user_optional, get_current_teacher, get_current_student,
    create_access_token, get_password_hash, verify_password,
    human_readable_size, get_client_ip  # human_readable_size 仍被 classroom_main 使用
)
# 修复：移除，V4.0 roster_handler 不再有 parse_excel_to_students
# from ...services.roster_handler import parse_excel_to_students
from ...database import get_db_connection
from ...dependencies import build_login_url, sanitize_next_path
from ...dependencies import infer_required_role_from_path, get_role_label
from ...dependencies import apply_access_token_cookie, clear_access_token_cookie, invalidate_session_for_user
from ...services.behavior_tracking_service import record_behavior_event
from ...services.discussion_mood_service import schedule_discussion_mood_refresh_soon
from ...services.submission_assets import decode_allowed_file_types_json, summarize_allowed_file_types
from ...services.ai_grading_attachments import AI_GRADING_UPLOAD_EXTENSIONS, AI_GRADING_SUPPORTED_TYPES_LABEL
from ...services.dashboard_service import build_dashboard_context
from ...services.exam_json_service import strip_exam_scoring_for_student
from ...services.classroom_page_service import build_classroom_page_context
from ...services.academic_course_exam_sync_service import (
    load_classroom_course_exam_status_for_user,
    merge_course_exams_into_teaching_plan,
)
from ...services.assignment_lifecycle_service import (
    assignment_accepts_submissions,
    close_overdue_assignments,
    enrich_assignment_runtime_view,
    refresh_assignment_runtime_status,
    submission_effective_status,
    submission_is_returned,
    submission_resubmission_accepts,
    submission_resubmission_state,
)
from ...services.academic_service import (
    build_semester_calendar_payload,
    build_semester_defaults,
    choose_default_semester_id,
    china_today,
    load_teacher_semester_rows,
    parse_date_input,
    serialize_semester_row,
    serialize_textbook_row,
)
from ...services.academic_course_sync_service import (
    build_academic_course_metadata,
    summarize_academic_course_sync_item,
)
from ...services.course_planning_service import (
    decorate_offering_sessions,
    load_course_lessons_by_course_id,
    serialize_course_row,
)
from ...services.department_service import collect_department_options, normalize_department
from ...services.organization_scope_service import load_teacher_org_memberships, load_teacher_org_scope, normalize_school_code, organization_label
from ...services.materials_service import attach_home_learning_material_briefs, attach_learning_material_briefs
from ...services.learning_progress_service import (
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
from ...services.message_center_service import (
    create_password_reset_request_notification,
    is_super_admin_teacher,
)
from ...services.blog_news_crawler_service import load_blog_news_crawler_dashboard
from ...services.agent_key_service import build_agent_key_dashboard
from ...services.session_material_generation_service import attach_generation_tasks
from ...services.student_insight_service import build_teacher_student_insight
from ...services.student_auth_service import (
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
from ...services.student_lifecycle_service import (
    STUDENT_STATUS_ACTIVE,
    normalize_student_enrollment_status,
    student_enrollment_status_label,
)
from ...services.ai_grading_service import expire_stale_ai_grading_submissions
from ...services.submission_preview_service import ensure_submission_access, serialize_submission_file_row
from ...services.teacher_account_service import (
    TEACHER_PASSWORD_HINT,
    build_teacher_account_summary,
    list_teacher_accounts,
)
from ...services.wrong_question_summary_service import (
    build_assignment_wrong_question_summary,
    reorganize_assignment_wrong_summary_ai,
)
from ...services.academic_integration_service import (
    list_academic_system_profiles,
    list_teacher_academic_credentials,
)
from ...services.smart_classroom_integration_service import (
    list_smart_classroom_profiles,
    list_teacher_smart_classroom_credentials,
)
from ...services.gongwen_integration_service import (
    list_gongwen_system_profiles,
    list_teacher_gongwen_credentials,
)
from ...services.gongwen_document_sync_service import (
    count_visible_gongwen_documents,
    list_visible_gongwen_categories,
    list_visible_gongwen_documents,
)
from ...services.signature_service import build_signature_dashboard_context
from ...services.organization_management_service import list_organization_tree
from ...services.resource_access_service import (
    SCOPE_DEPARTMENT,
    SCOPE_PRIVATE,
    SCOPE_SCHOOL,
    normalize_scope_level,
    teacher_can_manage_assignment,
    teacher_can_manage_exam_paper,
    teacher_can_use_class,
    teacher_can_use_course,
    teacher_can_use_exam_paper,
    teacher_can_use_textbook,
)
from ...services.smart_attendance_entry_service import (
    maybe_enqueue_teacher_daily_checkin_sync,
    maybe_send_student_attendance_alert,
    run_teacher_daily_checkin_sync_task,
)
from ...services.academic_classroom_sync_service import (
    count_teacher_teaching_places,
    load_teacher_teaching_place_dashboard,
    load_teacher_teaching_places,
)



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


def _row_first_value(row: Any, default: Any = 0) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        for key in ("row_count", "count", "total", "cnt"):
            if key in row:
                return row[key]
        return next(iter(row.values()), default)
    keys = getattr(row, "keys", None)
    if callable(keys):
        row_keys = list(keys())
        for key in ("row_count", "count", "total", "cnt"):
            if key in row_keys:
                return row[key]
        if row_keys:
            return row[row_keys[0]]
    try:
        return row[0]
    except (IndexError, KeyError, TypeError):
        return default


def _query_count(conn, sql: str, params: tuple[Any, ...] = ()) -> int:
    return _safe_int(_row_first_value(conn.execute(sql, params).fetchone()))


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


def _expire_stale_ai_grading_for_assignments(conn, assignment_ids: list[Any] | tuple[Any, ...] | set[Any]) -> int:
    try:
        reclaimed_count = expire_stale_ai_grading_submissions(
            conn,
            stale_minutes=AI_GRADING_STALE_MINUTES,
            assignment_ids=assignment_ids,
        )
        if reclaimed_count:
            conn.commit()
            print(f"[AI_GRADING] reclaimed {reclaimed_count} stale grading submission(s) before teacher stats")
        return int(reclaimed_count or 0)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[AI_GRADING] stale grading reclaim before teacher stats failed: {exc}")
        return 0


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
        total_students = _safe_int(_row_first_value(count_row))

    assignment_ids = [_safe_int(item.get("id")) for item in assignments if item.get("id") is not None]
    assignment_ids = [assignment_id for assignment_id in assignment_ids if assignment_id > 0]
    if not assignment_ids:
        return

    _expire_stale_ai_grading_for_assignments(conn, assignment_ids)

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





























# ============================
# 2. 仪表盘 (V4.0 新)
# ============================



# ============================
# 3. 课堂主界面 (V4.0 新)
# ============================


# ============================
# 5. 作业详情页 (V4.0)
# ============================









# ============================
# V4.1: 新的管理中心路由
# ============================










def _load_teacher_textbook_rows(conn, teacher_id: int):
    current_teacher_is_super_admin = is_super_admin_teacher(conn, teacher_id)
    rows = conn.execute(
        """
        SELECT tb.id,
               tb.teacher_id,
               tb.title,
               tb.authors_json,
               tb.publisher,
               tb.publication_date,
               tb.introduction,
               tb.catalog_text,
               tb.attachment_name,
               tb.attachment_path,
               tb.attachment_size,
               tb.attachment_mime_type,
               tb.tags_json,
               tb.owner_role,
               tb.owner_user_pk,
               tb.scope_level,
               tb.school_code,
               tb.school_name,
               tb.college,
               tb.department,
               tb.published_at,
               tb.archived_at,
               tb.deleted_at,
               t.name AS owner_teacher_name,
               tb.created_at,
               tb.updated_at
        FROM textbooks tb
        LEFT JOIN teachers t ON t.id = tb.teacher_id
        WHERE ? = 1
           OR tb.teacher_id = ?
           OR COALESCE(tb.scope_level, 'private') != 'private'
        ORDER BY tb.updated_at DESC, tb.id DESC
        """,
        (1 if current_teacher_is_super_admin else 0, int(teacher_id)),
    ).fetchall()
    return [row for row in rows if teacher_can_use_textbook(conn, int(teacher_id), row)]


def _teacher_school_codes(conn, teacher_id: int) -> list[str]:
    codes: list[str] = []
    for scope in load_teacher_org_memberships(conn, int(teacher_id)):
        code = normalize_school_code(scope.get("school_code"))
        if code and code not in codes:
            codes.append(code)
    return codes


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
        WHERE s.class_id IN ({placeholders})
        ORDER BY
            s.class_id,
            CASE COALESCE(s.enrollment_status, 'active') WHEN 'active' THEN 0 ELSE 1 END,
            s.student_id_number,
            s.id
        """,
        normalized_ids,
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
               SUM(CASE WHEN COALESCE(is_non_periodic, 0) <> 0 THEN 1 ELSE 0 END) AS non_periodic_count,
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
               c.owner_role,
               c.owner_user_pk,
               c.scope_level,
               c.updated_at,
               c.archived_at,
               c.deleted_at,
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
        WHERE ? = 1
           OR c.created_by_teacher_id = ?
           OR COALESCE(c.scope_level, 'school') != 'private'
        ORDER BY
            CASE WHEN c.created_by_teacher_id = ? THEN 0 ELSE 1 END,
            COALESCE(c.updated_at, c.created_at) DESC,
            c.name
        """,
        (
            teacher_id,
            1 if current_teacher_is_super_admin else 0,
            teacher_id,
            teacher_id,
        ),
    ).fetchall()
    rows = [row for row in rows if teacher_can_use_course(conn, int(teacher_id), row)]
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
               SUM(CASE WHEN COALESCE(os.is_non_periodic, 0) <> 0 THEN 1 ELSE 0 END) AS non_periodic_session_count,
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
                 s.start_date,
                 o.created_at,
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
        "courses": _query_count(conn,
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "course_lessons": _query_count(conn,
            """
            SELECT COUNT(*)
            FROM course_lessons lessons
            JOIN courses c ON c.id = lessons.course_id
            WHERE c.created_by_teacher_id = ?
            """,
            (teacher_id,),
        ),
        "textbooks": _query_count(conn,
            "SELECT COUNT(*) FROM textbooks WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "materials": _query_count(conn,
            "SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND name != '.git'",
            (teacher_id,),
        ),
        "classes": _query_count(conn,
            "SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "offerings": _query_count(conn,
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "ai_configs": _query_count(conn,
            """
            SELECT COUNT(*)
            FROM ai_class_configs cfg
            JOIN class_offerings o ON o.id = cfg.class_offering_id
            WHERE o.teacher_id = ?
            """,
            (teacher_id,),
        ),
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
        "classes": _query_count(conn,
            "SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "courses": _query_count(conn,
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "textbooks": _query_count(conn,
            "SELECT COUNT(*) FROM textbooks WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "exams": _query_count(conn,
            "SELECT COUNT(*) FROM exam_papers WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "materials": _query_count(conn,
            "SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND name != '.git'",
            (teacher_id,),
        ),
        "signatures": int((build_signature_dashboard_context(
            conn,
            {"id": teacher_id, "role": "teacher"},
        ).get("signature_stats") or {}).get("visible_total") or 0),
        "semesters": len(semester_rows),
        "current_semesters": sum(1 for item in semester_rows if item.get("is_current")),
        "offerings": _query_count(conn,
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "ai_configs": _query_count(conn,
            """
            SELECT COUNT(*)
            FROM ai_class_configs cfg
            JOIN class_offerings o ON o.id = cfg.class_offering_id
            WHERE o.teacher_id = ?
            """,
            (teacher_id,),
        ),
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


































# ============================
# V4.5: 试卷库管理路由
# ============================

EXAM_OPEN_SCOPES = {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL}
EXAM_SCOPE_LABELS = {
    SCOPE_PRIVATE: "私有",
    SCOPE_DEPARTMENT: "本系部开放",
    SCOPE_SCHOOL: "全校开放",
}


def _normalize_exam_open_scope(value: Any, default: str = SCOPE_DEPARTMENT) -> str:
    scope = normalize_scope_level(value, default=default)
    return scope if scope in EXAM_OPEN_SCOPES else default


def _exam_scope_label(scope_level: Any) -> str:
    return EXAM_SCOPE_LABELS.get(_normalize_exam_open_scope(scope_level, default=SCOPE_PRIVATE), "私有")


__all__ = [name for name in globals() if not name.startswith("__")]
