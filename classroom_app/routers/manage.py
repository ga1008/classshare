import json
import os
import sqlite3
import tempfile
import traceback
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import aiofiles
import httpx
from fastapi import APIRouter, Request, Form, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse

from ..config import ROSTER_DIR, TEXTBOOK_ATTACHMENT_DIR, TEXTBOOK_ATTACHMENT_LEGACY_DIRS
from ..core import ai_client
from ..database import get_db_connection
from ..dependencies import get_current_teacher, invalidate_session_for_user
from ..services.academic_service import (
    build_classroom_ai_context,
    build_textbook_prompt_context,
    compute_semester_week_count,
    infer_semester_name,
    parse_date_input,
    parse_json_list_field,
    serialize_textbook_row,
)
from ..services.course_planning_service import (
    CoursePlanningError,
    SCHEDULE_SOURCE_ACADEMIC_SYNC,
    SCHEDULE_SOURCE_FIXED_CYCLE,
    build_academic_offering_session_plan,
    build_offering_session_plan,
    build_schedule_info_text,
    load_course_lessons_by_course_id,
    normalize_course_lessons,
    normalize_total_hours,
    normalize_weekly_schedule,
    replace_course_lessons,
    replace_offering_sessions,
    serialize_course_row,
    select_academic_teaching_class_for_offering,
)
from ..services.file_handler import save_upload_file
from ..services.file_service import save_file_globally
from ..services.department_service import infer_department_from_text, normalize_department
from ..services.organization_scope_service import (
    apply_teacher_scope_to_org,
    is_same_department,
    is_same_school,
    load_teacher_org_scope,
)
from ..services.organization_management_service import (
    OrganizationManagementError,
    create_college,
    create_department,
    create_school,
    delete_college,
    delete_department,
    delete_school,
    list_organization_tree,
    list_school_options,
    update_college,
    update_department,
    update_school,
)
from ..services.learning_progress_service import normalize_course_sect_name
from ..services.materials_service import (
    attach_learning_material_briefs,
    get_learning_material_brief_map,
    sync_classroom_learning_material_assignments,
)
from ..services.message_center_service import (
    is_super_admin_teacher,
    mark_password_reset_request_notification_read,
)
from ..services.teacher_onboarding_service import (
    build_default_ai_config,
    build_default_course_description,
    build_teacher_onboarding_payload,
    mark_teacher_onboarding_dismissed,
)
from ..services.blog_news_crawler_service import (
    cancel_pending_blog_news_crawler_runs,
    enqueue_blog_news_crawler_run,
    load_blog_news_crawler_dashboard,
    update_blog_news_crawler_config,
)
from ..services.agent_key_service import (
    build_agent_key_dashboard,
    create_agent_api_key,
    delete_agent_api_key,
    fetch_agent_runtime_usage,
    set_active_agent_api_key,
    test_saved_agent_api_key,
)
from ..services.roster_handler import parse_excel_to_students
from ..services.student_auth_service import build_student_security_summary, list_student_login_history
from ..services.student_lifecycle_service import (
    STUDENT_STATUS_ACTIVE,
    STUDENT_STATUS_SUSPENDED,
    normalize_student_enrollment_status,
    student_enrollment_status_label,
)
from ..services.student_support_service import (
    MAX_SHARED_NOTE_LENGTH,
    normalize_shared_teacher_note,
    save_shared_student_teacher_note,
    teacher_can_access_student,
)
from ..services.submission_file_alignment import run_full_alignment
from ..services.teacher_account_service import (
    TEACHER_PASSWORD_HINT,
    create_teacher_account,
    deactivate_teacher_account,
    deactivate_teacher_membership,
    get_teacher_account,
    grant_teacher_super_admin,
    reset_teacher_password,
    revoke_teacher_super_admin,
    set_teacher_primary_membership,
    update_teacher_account,
    upsert_teacher_membership,
)
from ..services.academic_integration_service import (
    build_saved_credential_verification_payload,
    delete_teacher_academic_credential,
    get_teacher_academic_credential,
    list_teacher_academic_credentials,
    save_verified_academic_credential,
    update_academic_credential_verification_status,
    verify_academic_credential,
)
from ..services.academic_calendar_sync_service import (
    mark_semester_calendar_sync_queued,
    prepare_current_semester_from_academic_system,
    sync_semester_calendar_background,
)
from ..services.academic_auto_sync_service import (
    build_academic_sync_capabilities,
    sync_teacher_academic_data_after_credential_verified,
)
from ..services.academic_classroom_sync_service import (
    count_teacher_teaching_places,
    load_free_classroom_options_from_academic_system,
    load_teacher_teaching_place_dashboard,
    load_teacher_teaching_places,
    query_free_classrooms_from_academic_system,
    sync_teaching_places_from_academic_system,
)
from ..services.academic_course_sync_service import sync_current_teacher_courses_from_academic_system
from ..services.academic_exam_roster_sync_service import (
    build_exam_roster_signature_workbook,
    load_classroom_exam_roster_status,
    sync_classroom_exam_roster_from_academic_system,
)
from ..services.academic_invigilation_sync_service import sync_current_teacher_invigilations_from_academic_system
from ..services.academic_roster_sync_service import sync_current_teacher_rosters_from_academic_system
from ..services.smart_classroom_checkin_sync_service import (
    build_smart_classroom_sync_capabilities,
    sync_teacher_smart_classroom_checkins,
    sync_teacher_smart_classroom_data_after_credential_verified,
)
from ..services.smart_classroom_integration_service import (
    build_saved_smart_classroom_verification_payload,
    delete_teacher_smart_classroom_credential,
    get_teacher_smart_classroom_credential,
    list_teacher_smart_classroom_credentials,
    save_verified_smart_classroom_credential,
    update_smart_classroom_credential_verification_status,
    verify_smart_classroom_credential,
)
from ..services.integration_request_probe_service import probe_integration_request
from ..storage_paths import resolve_migrated_file_path
from ..time_utils import local_iso

router = APIRouter(prefix="/api/manage", dependencies=[Depends(get_current_teacher)])


def _form_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@router.get("/teacher-onboarding/state", response_class=JSONResponse)
async def api_get_teacher_onboarding_state(user: dict = Depends(get_current_teacher)):
    teacher_id = int(user["id"])
    with get_db_connection() as conn:
        return build_teacher_onboarding_payload(conn, teacher_id)


@router.post("/teacher-onboarding/dismiss", response_class=JSONResponse)
async def api_dismiss_teacher_onboarding(request: Request, user: dict = Depends(get_current_teacher)):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    teacher_id = int(user["id"])
    reason = str(payload.get("reason") or "manual_exit")
    with get_db_connection() as conn:
        mark_teacher_onboarding_dismissed(conn, teacher_id, reason)
        conn.commit()
        result = build_teacher_onboarding_payload(conn, teacher_id)

    result["message"] = "新手引导状态已更新。"
    return result


@router.post("/teacher-onboarding/classes/create", response_class=JSONResponse)
async def api_create_onboarding_class(request: Request, user: dict = Depends(get_current_teacher)):
    data = await _parse_json_request(request)
    class_name = str(data.get("name") or data.get("class_name") or "").strip()
    description = str(data.get("description") or "").strip()
    department = normalize_department(data.get("department")) or infer_department_from_text(class_name, description)

    if not class_name:
        raise HTTPException(400, "请填写班级名称")
    if not department:
        raise HTTPException(400, "请填写或选择班级所属系别")

    with get_db_connection() as conn:
        try:
            org_scope = apply_teacher_scope_to_org(
                conn,
                user["id"],
                college=data.get("college") or "",
                department=department,
            )
            cursor = conn.execute(
                """
                INSERT INTO classes (
                    name, department, description, created_by_teacher_id,
                    school_code, school_name, college
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    class_name,
                    department,
                    description,
                    user["id"],
                    org_scope["school_code"],
                    org_scope["school_name"],
                    org_scope["college"],
                ),
            )
            class_id = int(cursor.lastrowid)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(400, f"创建班级失败：{class_name} 已存在或数据不完整") from exc

    return {
        "status": "success",
        "message": f"班级“{class_name}”已创建",
        "class": {
            "id": class_id,
            "name": class_name,
            "department": department,
            "description": description,
            "student_count": 0,
            "related_course_ids": [],
        },
    }


@router.post("/teacher-onboarding/course-description", response_class=JSONResponse)
async def api_generate_onboarding_course_description(request: Request, user: dict = Depends(get_current_teacher)):
    data = await _parse_json_request(request)
    course_name = str(data.get("course_name") or data.get("name") or "").strip()
    department = normalize_department(data.get("department"))
    textbook_id = _parse_optional_int(data.get("textbook_id"))

    if not course_name:
        raise HTTPException(400, "请先填写课程名称")

    textbook = None
    if textbook_id:
        with get_db_connection() as conn:
            textbook_row = _ensure_teacher_owned_record(
                conn,
                table="textbooks",
                record_id=textbook_id,
                teacher_id=user["id"],
                owner_column="teacher_id",
            )
            textbook = serialize_textbook_row(textbook_row)

    fallback = build_default_course_description(
        course_name=course_name,
        department=department,
        textbook=textbook,
    )
    textbook_hint = ""
    if textbook:
        textbook_hint = build_textbook_prompt_context(textbook)

    prompt = (
        "你是一名高校课程简介撰写助手。请使用简体中文，为教师生成一段可以直接放入课程信息的课程简介。"
        "要求：160-260 字，清晰说明课程定位、学习目标、实践方式和适用专业；不要输出标题或项目符号。"
    )
    user_message = (
        f"课程名称：{course_name}\n"
        f"系别：{department or '未填写'}\n"
        f"教材信息：\n{textbook_hint or '未选择教材'}\n"
        f"本地草稿：{fallback}"
    )
    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": prompt,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "standard",
                "task_type": "fast_text_response",
                "web_search_enabled": False,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        ai_data = response.json()
        generated = str(ai_data.get("response_text") or "").strip()
        if ai_data.get("status") == "success" and generated:
            return {
                "status": "success",
                "message": "AI 已生成课程简介草稿",
                "description": generated[:1600],
                "fallback": False,
            }
    except Exception:
        pass

    return {
        "status": "success",
        "message": "AI 暂时不可用，已使用本地课程简介草稿",
        "description": fallback,
        "fallback": True,
    }


def _ensure_teacher_owned_record(
    conn,
    *,
    table: str,
    record_id: int,
    teacher_id: int,
    owner_column: str,
):
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND {owner_column} = ?",
        (record_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "目标记录不存在或无权操作")
    return row


def _ensure_teacher_can_use_semester(conn, *, semester_id: int, teacher_id: int):
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE id = ?
        LIMIT 1
        """,
        (int(semester_id),),
    ).fetchone()
    if not row or not is_same_school(row, load_teacher_org_scope(conn, teacher_id)):
        raise HTTPException(404, "学期不存在或不属于当前教师所在学校")
    return row


def _ensure_teacher_can_manage_semester(conn, *, semester_id: int, teacher_id: int):
    row = _ensure_teacher_can_use_semester(conn, semester_id=semester_id, teacher_id=teacher_id)
    if int(row["teacher_id"]) != int(teacher_id) and not is_super_admin_teacher(conn, teacher_id):
        raise HTTPException(403, "该学期由同校其他教师维护，仅可复用，不能编辑或删除")
    return row


def _ensure_teacher_can_use_course(conn, *, course_id: int, teacher_id: int):
    row = conn.execute(
        """
        SELECT *
        FROM courses
        WHERE id = ?
        LIMIT 1
        """,
        (int(course_id),),
    ).fetchone()
    if not row:
        raise HTTPException(404, "课程不存在")
    if int(row["created_by_teacher_id"]) == int(teacher_id):
        return row
    if not is_same_department(row, load_teacher_org_scope(conn, teacher_id)):
        raise HTTPException(404, "课程不存在或不属于当前教师所在系别")
    return row


def _ensure_teacher_can_manage_course(conn, *, course_id: int, teacher_id: int):
    row = _ensure_teacher_can_use_course(conn, course_id=course_id, teacher_id=teacher_id)
    if int(row["created_by_teacher_id"]) != int(teacher_id) and not is_super_admin_teacher(conn, teacher_id):
        raise HTTPException(403, "该课程为系内共享课程，仅创建者或超管可编辑")
    return row


def _ensure_teacher_owned_offering(conn, offering_id: int, teacher_id: int):
    offering = conn.execute(
        """
        SELECT o.*,
               COALESCE(s.name, o.semester) AS semester_name,
               tb.title AS textbook_title
        FROM class_offerings o
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
        WHERE o.id = ? AND o.teacher_id = ?
        LIMIT 1
        """,
        (offering_id, teacher_id),
    ).fetchone()
    if not offering:
        raise HTTPException(404, "课堂不存在或无权操作")
    return offering


def _validate_teacher_owned_selection(
    conn,
    *,
    teacher_id: int,
    class_id: int,
    course_id: int,
    semester_id: int,
    textbook_id: int,
) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row]:
    class_row = _ensure_teacher_owned_record(
        conn,
        table="classes",
        record_id=class_id,
        teacher_id=teacher_id,
        owner_column="created_by_teacher_id",
    )
    course_row = _ensure_teacher_can_use_course(conn, course_id=course_id, teacher_id=teacher_id)
    semester_row = _ensure_teacher_can_use_semester(conn, semester_id=semester_id, teacher_id=teacher_id)
    textbook_row = _ensure_teacher_owned_record(
        conn,
        table="textbooks",
        record_id=textbook_id,
        teacher_id=teacher_id,
        owner_column="teacher_id",
    )
    return class_row, course_row, semester_row, textbook_row


def _remove_file_if_exists(path_value: str | None) -> None:
    normalized_path = str(path_value or "").strip()
    if not normalized_path:
        return

    try:
        file_path = resolve_migrated_file_path(
            normalized_path,
            active_root=TEXTBOOK_ATTACHMENT_DIR,
            legacy_roots=TEXTBOOK_ATTACHMENT_LEGACY_DIRS,
            markers=("storage/textbook_attachments", "files/textbook_attachments", "textbook_attachments"),
        ) or Path(normalized_path)
        if file_path.exists():
            file_path.unlink()
    except Exception:
        pass


def _parse_optional_int(raw_value: Any) -> int | None:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except (TypeError, ValueError) as exc:
        raise CoursePlanningError("ID 参数格式不正确") from exc


def _parse_nonnegative_float(raw_value: Any, *, field_name: str, default: float = 0.0) -> float:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return float(default)
    try:
        value = float(normalized)
    except (TypeError, ValueError) as exc:
        raise CoursePlanningError(f"{field_name}格式不正确") from exc
    if value < 0:
        raise CoursePlanningError(f"{field_name}不能小于 0")
    if value > 100:
        raise CoursePlanningError(f"{field_name}不能大于 100")
    return value


async def _parse_json_request(request: Request, *, error_message: str = "请求数据格式错误") -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, error_message) from exc

    if not isinstance(payload, dict):
        raise HTTPException(400, error_message)
    return payload


def _prepare_course_payload(
    data: dict[str, Any],
    *,
    require_lessons: bool,
) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    sect_name = normalize_course_sect_name(data.get("sect_name"), course_name=name)
    department = normalize_department(data.get("department")) or infer_department_from_text(name, description)
    course_id = _parse_optional_int(data.get("course_id"))
    credits = _parse_nonnegative_float(data.get("credits"), field_name="学分", default=0.0)
    total_hours = normalize_total_hours(data.get("total_hours"))
    lessons = normalize_course_lessons(
        data.get("lessons", data.get("lessons_json")),
        require_items=require_lessons,
    )

    planned_section_count = sum(int(item.get("section_count") or 0) for item in lessons)
    if lessons and total_hours <= 0:
        total_hours = planned_section_count
    if lessons and total_hours > 0 and planned_section_count != total_hours:
        raise CoursePlanningError(
            f"课堂设置合计 {planned_section_count} 小节，与课程总学时 {total_hours} 不一致，请先调整。"
        )
    if not name:
        raise CoursePlanningError("课程名称不能为空")

    return {
        "course_id": course_id,
        "name": name,
        "description": description,
        "sect_name": sect_name,
        "department": department,
        "credits": credits,
        "total_hours": total_hours,
        "lessons": lessons,
        "planned_section_count": planned_section_count,
    }


def _prepare_offering_payload(
    conn,
    *,
    teacher_id: int,
    data: dict[str, Any],
    require_schedule: bool,
    allow_missing_lessons: bool,
) -> dict[str, Any]:
    offering_id = _parse_optional_int(data.get("offering_id"))
    class_id = _parse_optional_int(data.get("class_id"))
    course_id = _parse_optional_int(data.get("course_id"))
    semester_id = _parse_optional_int(data.get("semester_id"))
    textbook_id = _parse_optional_int(data.get("textbook_id"))

    if not class_id or not course_id or not semester_id or not textbook_id:
        raise CoursePlanningError("请完整选择学期、班级、课程和教材")

    class_row, course_row, semester_row, textbook_row = _validate_teacher_owned_selection(
        conn,
        teacher_id=teacher_id,
        class_id=class_id,
        course_id=course_id,
        semester_id=semester_id,
        textbook_id=textbook_id,
    )

    course_lessons = load_course_lessons_by_course_id(conn, [course_id]).get(course_id, [])
    course_lessons = attach_learning_material_briefs(
        conn,
        course_lessons,
        teacher_id=teacher_id,
        markdown_only=True,
    )
    if not course_lessons and not allow_missing_lessons:
        raise CoursePlanningError("所选课程还没有配置课堂设置，请先到课程管理页补齐课堂内容")

    semester_start_date = parse_date_input(semester_row["start_date"], "学期开始日期")
    semester_end_date = parse_date_input(semester_row["end_date"], "学期结束日期")
    requested_schedule_source = str(data.get("schedule_source") or "").strip()
    if requested_schedule_source not in {SCHEDULE_SOURCE_ACADEMIC_SYNC, SCHEDULE_SOURCE_FIXED_CYCLE}:
        requested_schedule_source = ""
    preferred_teaching_class_name = str(data.get("academic_teaching_class_name") or "").strip()
    academic_teaching_class_name, academic_occurrences, academic_warnings, academic_class_options = (
        select_academic_teaching_class_for_offering(
            conn,
            teacher_id=teacher_id,
            semester_id=semester_id,
            course_id=course_id,
            class_row=class_row,
            preferred_teaching_class_name=preferred_teaching_class_name,
        )
    )
    use_academic_schedule = requested_schedule_source == SCHEDULE_SOURCE_ACADEMIC_SYNC or (
        not requested_schedule_source and bool(academic_class_options)
    )

    first_class_date_value = parse_date_input(data.get("first_class_date"), "第一次上课日期")
    raw_weekly_schedule = data.get("weekly_schedule", data.get("weekly_schedule_json", "[]"))
    raw_weekly_schedule_has_value = str(raw_weekly_schedule).strip() not in ("", "[]")
    if use_academic_schedule:
        if require_schedule and not academic_occurrences:
            raise CoursePlanningError("；".join(academic_warnings) if academic_warnings else "未找到可用的教务实际排课。")
        weekly_schedule = normalize_weekly_schedule(
            raw_weekly_schedule,
            first_class_date=first_class_date_value,
            require_items=False,
        ) if raw_weekly_schedule_has_value else []
    else:
        if require_schedule and not first_class_date_value:
            raise CoursePlanningError("请先填写第一次上课日期")
        weekly_schedule = normalize_weekly_schedule(
            raw_weekly_schedule,
            first_class_date=first_class_date_value,
            require_items=require_schedule,
        ) if (require_schedule or raw_weekly_schedule_has_value) else []

    if use_academic_schedule and academic_occurrences:
        plan = build_academic_offering_session_plan(
            course_lessons=course_lessons,
            academic_occurrences=academic_occurrences,
            semester_start_date=semester_start_date,
            course_name=str(course_row["name"] or ""),
            teaching_class_name=academic_teaching_class_name,
        )
        first_class_date_value = parse_date_input(plan.get("first_class_date"), "第一次上课日期")
        weekly_schedule = []
    elif course_lessons and first_class_date_value and weekly_schedule:
        plan = build_offering_session_plan(
            course_lessons=course_lessons,
            first_class_date=first_class_date_value,
            weekly_schedule=weekly_schedule,
            semester_start_date=semester_start_date,
            semester_end_date=semester_end_date,
        )
    else:
        warnings = []
        if not course_lessons:
            warnings.append("所选课程暂未配置课堂设置，当前只能保存基础绑定信息。")
        plan = {
            "sessions": [],
            "session_count": 0,
            "warnings": warnings,
            "schedule_info": build_schedule_info_text(
                first_class_date=first_class_date_value,
                weekly_schedule=weekly_schedule,
                session_count=0,
                end_date=None,
            ),
            "weekly_schedule_summary": "",
            "first_class_date": first_class_date_value.isoformat() if first_class_date_value else "",
            "schedule_source": SCHEDULE_SOURCE_ACADEMIC_SYNC if use_academic_schedule else SCHEDULE_SOURCE_FIXED_CYCLE,
            "schedule_source_label": "教务实际排课" if use_academic_schedule else "固定周循环",
            "academic_teaching_class_name": academic_teaching_class_name,
            "academic_teaching_class_options": academic_class_options,
        }
    plan["academic_teaching_class_options"] = academic_class_options

    return {
        "offering_id": offering_id,
        "class_id": class_id,
        "course_id": course_id,
        "semester_id": semester_id,
        "textbook_id": textbook_id,
        "class_row": class_row,
        "course_row": course_row,
        "semester_row": semester_row,
        "textbook_row": textbook_row,
        "first_class_date": first_class_date_value,
        "weekly_schedule": weekly_schedule,
        "weekly_schedule_json": json.dumps(weekly_schedule, ensure_ascii=False),
        "schedule_source": SCHEDULE_SOURCE_ACADEMIC_SYNC if use_academic_schedule else SCHEDULE_SOURCE_FIXED_CYCLE,
        "academic_teaching_class_name": academic_teaching_class_name,
        "academic_teaching_class_options": academic_class_options,
        "course_lessons": course_lessons,
        "plan": plan,
    }


def _normalize_material_id_list(raw_value: Any) -> list[int]:
    if not isinstance(raw_value, list):
        return []
    normalized: list[int] = []
    for item in raw_value:
        try:
            material_id = int(item)
        except (TypeError, ValueError):
            continue
        if material_id > 0 and material_id not in normalized:
            normalized.append(material_id)
    return normalized


def _clean_form_text(value: Any, *, limit: int | None = None) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return text[:limit].strip()
    return text


def _ensure_teacher_owned_class(conn, *, class_id: int, teacher_id: int):
    class_row = conn.execute(
        """
        SELECT *
        FROM classes
        WHERE id = ? AND created_by_teacher_id = ?
        LIMIT 1
        """,
        (int(class_id), int(teacher_id)),
    ).fetchone()
    if not class_row:
        raise HTTPException(status_code=404, detail="班级不存在或无权操作")
    return class_row


def _ensure_teacher_owned_student(conn, *, student_id: int, teacher_id: int):
    student_row = conn.execute(
        """
        SELECT s.*, c.name AS class_name, c.created_by_teacher_id
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
          AND c.created_by_teacher_id = ?
        LIMIT 1
        """,
        (int(student_id), int(teacher_id)),
    ).fetchone()
    if not student_row:
        raise HTTPException(status_code=404, detail="学生不存在或无权操作")
    return student_row


@router.post("/teacher-onboarding/complete", response_class=JSONResponse)
async def api_complete_teacher_onboarding(request: Request, user: dict = Depends(get_current_teacher)):
    data = await _parse_json_request(request)
    teacher_id = int(user["id"])
    course_data = data.get("course") if isinstance(data.get("course"), dict) else {}
    ai_data = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    schedule_data = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}

    course_data = {
        **course_data,
        "course_id": course_data.get("course_id") or course_data.get("id") or data.get("course_id"),
    }
    try:
        course_payload = _prepare_course_payload(course_data, require_lessons=True)
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc

    semester_id = _parse_optional_int(data.get("semester_id"))
    class_id = _parse_optional_int(data.get("class_id"))
    textbook_id = _parse_optional_int(data.get("textbook_id"))
    if not semester_id or not class_id or not textbook_id:
        raise HTTPException(400, "请完整选择学期、教材和班级")

    selected_material_ids = _normalize_material_id_list(data.get("material_ids"))
    home_learning_material_id = _parse_optional_int(data.get("home_learning_material_id"))

    with get_db_connection() as conn:
        try:
            lesson_material_ids = [
                lesson.get("learning_material_id")
                for lesson in course_payload["lessons"]
                if lesson.get("learning_material_id")
            ]
            lesson_material_map = get_learning_material_brief_map(
                conn,
                lesson_material_ids,
                teacher_id=teacher_id,
                markdown_only=True,
            )
            if len(lesson_material_map) != len({int(item) for item in lesson_material_ids}):
                raise HTTPException(400, "绑定到具体课次的材料需要是可作为课堂文档的 Markdown 文件")

            selected_materials_to_validate = [
                material_id
                for material_id in [*selected_material_ids, home_learning_material_id]
                if material_id
            ]
            selected_material_map = get_learning_material_brief_map(
                conn,
                selected_materials_to_validate,
                teacher_id=teacher_id,
                markdown_only=False,
            )
            if len(selected_material_map) != len({int(item) for item in selected_materials_to_validate}):
                raise HTTPException(400, "所选教学材料不存在或无权访问")
            material_map = {**selected_material_map, **lesson_material_map}
            markdown_home_map = get_learning_material_brief_map(
                conn,
                [*lesson_material_ids, *selected_material_ids, home_learning_material_id],
                teacher_id=teacher_id,
                markdown_only=True,
            )
            if home_learning_material_id and home_learning_material_id not in markdown_home_map:
                home_learning_material_id = None

            if course_payload["course_id"]:
                _ensure_teacher_can_manage_course(
                    conn,
                    course_id=course_payload["course_id"],
                    teacher_id=teacher_id,
                )
                conn.execute(
                    """
                    UPDATE courses
                    SET name = ?, description = ?, sect_name = ?, department = ?, credits = ?, total_hours = ?
                    WHERE id = ?
                    """,
                    (
                        course_payload["name"],
                        course_payload["description"],
                        course_payload["sect_name"],
                        course_payload["department"],
                        course_payload["credits"],
                        course_payload["total_hours"],
                        course_payload["course_id"],
                    ),
                )
                course_id = int(course_payload["course_id"])
                course_action = "更新"
            else:
                org_scope = apply_teacher_scope_to_org(
                    conn,
                    teacher_id,
                    department=course_payload["department"],
                )
                cursor = conn.execute(
                    """
                    INSERT INTO courses (
                        name, description, sect_name, department, credits, total_hours,
                        created_by_teacher_id, school_code, school_name, college
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        course_payload["name"],
                        course_payload["description"],
                        course_payload["sect_name"],
                        course_payload["department"],
                        course_payload["credits"],
                        course_payload["total_hours"],
                        teacher_id,
                        org_scope["school_code"],
                        org_scope["school_name"],
                        org_scope["college"],
                    ),
                )
                course_id = int(cursor.lastrowid)
                course_action = "创建"

            replace_course_lessons(conn, course_id=course_id, lessons=course_payload["lessons"])

            offering_payload = _prepare_offering_payload(
                conn,
                teacher_id=teacher_id,
                data={
                    "class_id": class_id,
                    "course_id": course_id,
                    "semester_id": semester_id,
                    "textbook_id": textbook_id,
                    "first_class_date": schedule_data.get("first_class_date") or data.get("first_class_date"),
                    "weekly_schedule": schedule_data.get("weekly_schedule") or data.get("weekly_schedule") or [],
                },
                require_schedule=True,
                allow_missing_lessons=False,
            )
            semester_name = str(offering_payload["semester_row"]["name"] or "").strip()
            existing_offering = conn.execute(
                """
                SELECT id
                FROM class_offerings
                WHERE class_id = ?
                  AND course_id = ?
                  AND teacher_id = ?
                  AND (
                        semester_id = ?
                        OR (
                            semester_id IS NULL
                            AND COALESCE(semester, '') = ?
                        )
                  )
                LIMIT 1
                """,
                (class_id, course_id, teacher_id, semester_id, semester_name),
            ).fetchone()

            if existing_offering:
                offering_id = int(existing_offering["id"])
                conn.execute(
                    """
                    UPDATE class_offerings
                    SET semester = ?,
                        semester_id = ?,
                        textbook_id = ?,
                        schedule_info = ?,
                        first_class_date = ?,
                        weekly_schedule_json = ?,
                        schedule_source = ?,
                        academic_teaching_class_name = ?,
                        academic_schedule_sync_at = ?,
                        academic_schedule_sync_message = ?
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (
                        semester_name,
                        semester_id,
                        textbook_id,
                        offering_payload["plan"]["schedule_info"],
                        offering_payload["first_class_date"].isoformat() if offering_payload["first_class_date"] else "",
                        offering_payload["weekly_schedule_json"],
                        offering_payload["schedule_source"],
                        offering_payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "开课向导使用教务实际排课生成时间轴。"
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                        offering_id,
                        teacher_id,
                    ),
                )
                offering_action = "更新"
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO class_offerings (
                        class_id,
                        course_id,
                        teacher_id,
                        semester,
                        semester_id,
                        textbook_id,
                        schedule_info,
                        first_class_date,
                        weekly_schedule_json,
                        schedule_source,
                        academic_teaching_class_name,
                        academic_schedule_sync_at,
                        academic_schedule_sync_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        class_id,
                        course_id,
                        teacher_id,
                        semester_name,
                        semester_id,
                        textbook_id,
                        offering_payload["plan"]["schedule_info"],
                        offering_payload["first_class_date"].isoformat() if offering_payload["first_class_date"] else "",
                        offering_payload["weekly_schedule_json"],
                        offering_payload["schedule_source"],
                        offering_payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "开课向导使用教务实际排课生成时间轴。"
                        if offering_payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                    ),
                )
                offering_id = int(cursor.lastrowid)
                offering_action = "开设"

            replace_offering_sessions(
                conn,
                offering_id=offering_id,
                sessions=offering_payload["plan"]["sessions"],
            )

            plan_material_ids = [
                session.get("learning_material_id")
                for session in offering_payload["plan"]["sessions"]
                if session.get("learning_material_id")
            ]
            all_assignment_ids = _normalize_material_id_list(
                [*plan_material_ids, *selected_material_ids, home_learning_material_id]
            )
            if all_assignment_ids:
                sync_classroom_learning_material_assignments(
                    conn,
                    class_offering_id=offering_id,
                    teacher_id=teacher_id,
                    material_ids=all_assignment_ids,
                )

            if not home_learning_material_id:
                for material_id in all_assignment_ids:
                    if material_id in markdown_home_map:
                        home_learning_material_id = material_id
                        break
            if home_learning_material_id:
                conn.execute(
                    """
                    UPDATE class_offerings
                    SET home_learning_material_id = ?
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (home_learning_material_id, offering_id, teacher_id),
                )

            textbook_row = _ensure_teacher_owned_record(
                conn,
                table="textbooks",
                record_id=textbook_id,
                teacher_id=teacher_id,
                owner_column="teacher_id",
            )
            class_row = _ensure_teacher_owned_record(
                conn,
                table="classes",
                record_id=class_id,
                teacher_id=teacher_id,
                owner_column="created_by_teacher_id",
            )
            default_ai = build_default_ai_config(
                teacher_name=str(user.get("name") or "老师"),
                course_name=course_payload["name"],
                class_name=str(class_row["name"] or ""),
                semester_name=semester_name,
                department=course_payload["department"],
                textbook_title=str(textbook_row["title"] or ""),
                course_description=course_payload["description"],
                material_names=[item.get("name", "") for item in material_map.values()],
            )
            system_prompt = str(ai_data.get("system_prompt") or default_ai["system_prompt"]).strip()
            syllabus = str(ai_data.get("syllabus") or default_ai["syllabus"]).strip()
            conn.execute(
                """
                INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus)
                VALUES (?, ?, ?)
                ON CONFLICT(class_offering_id) DO UPDATE SET
                    system_prompt = excluded.system_prompt,
                    syllabus = excluded.syllabus,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (offering_id, system_prompt, syllabus),
            )

            mark_teacher_onboarding_dismissed(conn, teacher_id, "completed")
            conn.commit()
        except CoursePlanningError as exc:
            conn.rollback()
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(400, f"保存失败，该课堂可能已经存在：{exc}") from exc
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(500, f"开课失败: {exc}") from exc

    return {
        "status": "success",
        "message": f"课程已{course_action}，课堂已{offering_action}，并生成 {offering_payload['plan']['session_count']} 次课。",
        "course_id": course_id,
        "class_offering_id": offering_id,
        "classroom_url": f"/classroom/{offering_id}",
        "preview": offering_payload["plan"],
    }


# --- 班级管理 ---
@router.post("/classes/create", response_class=JSONResponse)
async def api_create_class(request: Request, class_name: str = Form(), file: UploadFile = File(...),
                           department: str = Form(default=""),
                           school_name: str = Form(default=""),
                           college: str = Form(default=""),
                           user: dict = Depends(get_current_teacher)):
    """从Excel文件创建班级和学生"""

    # 1. 保存 Excel 文件
    temp_excel_path = ROSTER_DIR / f"temp_{uuid.uuid4()}_{file.filename}"
    try:
        async with aiofiles.open(temp_excel_path, 'wb') as out_file:
            while content := await file.read(1024 * 1024): await out_file.write(content)
    except Exception as e:
        raise HTTPException(500, f"保存文件失败: {e}")

    # 2. 解析 Excel
    students_data = parse_excel_to_students(temp_excel_path)
    if students_data is None:
        if temp_excel_path.exists():
            temp_excel_path.unlink()  # 清理临时文件
        raise HTTPException(400, "解析Excel失败，请检查文件格式和列名（需包含'姓名'和'学号'）。")
    missing_email_count = sum(1 for item in students_data if not str(item.get("email") or "").strip())

    # 3. 存入数据库 (使用事务)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 创建班级
        normalized_department = normalize_department(department) or infer_department_from_text(class_name)
        org_scope = apply_teacher_scope_to_org(
            conn,
            user["id"],
            school_name=school_name,
            college=college,
            department=normalized_department,
        )
        cursor.execute(
            """
            INSERT INTO classes (
                name, department, created_by_teacher_id,
                school_code, school_name, college
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                class_name,
                normalized_department,
                user['id'],
                org_scope["school_code"],
                org_scope["school_name"],
                org_scope["college"],
            ),
        )
        class_id = cursor.lastrowid

        # 批量插入学生
        students_to_insert = [
            (
                s['student_id_number'],
                s['name'],
                class_id,
                s.get('gender'),
                s.get('email'),
                s.get('phone'),
                org_scope["school_code"],
                org_scope["school_name"],
                org_scope["college"],
                org_scope["department"],
            )
            for s in students_data
        ]
        cursor.executemany(
            """
            INSERT INTO students (
                student_id_number, name, class_id, gender, email, phone,
                school_code, school_name, college, department
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            students_to_insert
        )
        conn.commit()

    except sqlite3.IntegrityError as e:
        # 显示更详细的错误信息并打印堆栈跟踪
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(400, f"创建失败：{e}。可能是班级名称或学号已存在。")
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"数据库操作失败: {e}")
    finally:
        conn.close()
        if temp_excel_path.exists():
            temp_excel_path.unlink()  # 清理临时文件

    message = f"成功创建班级 '{class_name}' 并导入 {len(students_data)} 名学生。"
    if missing_email_count:
        message += f" 其中 {missing_email_count} 名学生缺少邮箱，后续只能收到站内通知，可提醒学生在个人中心补充。"
    return {"status": "success", "message": message, "missing_email_count": missing_email_count}


@router.post("/classes/sync-current-academic", response_class=JSONResponse)
async def api_sync_current_classes_from_academic_system(
    user: dict = Depends(get_current_teacher),
):
    result = await sync_current_teacher_rosters_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步班级和学生名单。")
    return result


@router.get("/classrooms/teaching-places", response_class=JSONResponse)
async def api_list_academic_teaching_places(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    try:
        page_size = int(request.query_params.get("page_size") or request.query_params.get("limit") or 10)
    except (TypeError, ValueError):
        page_size = 10
    try:
        page = int(request.query_params.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    page_size = max(1, min(page_size, 120))
    page = max(1, page)
    filters = {
        "search": str(request.query_params.get("q") or "").strip(),
        "campus_id": str(request.query_params.get("campus_id") or "").strip(),
        "building_id": str(request.query_params.get("building_id") or "").strip(),
        "room_type_id": str(request.query_params.get("room_type_id") or "").strip(),
        "availability": str(request.query_params.get("availability") or "").strip(),
        "include_stale": str(request.query_params.get("include_stale") or "").lower() in {"1", "true", "yes"},
    }
    with get_db_connection() as conn:
        total_count = count_teacher_teaching_places(conn, int(user["id"]), **filters)
        total_page = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_page)
        places = load_teacher_teaching_places(
            conn,
            int(user["id"]),
            **filters,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        dashboard = load_teacher_teaching_place_dashboard(conn, int(user["id"]))
    return {
        "status": "success",
        "items": places,
        "count": len(places),
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_page": total_page,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_page": total_page,
        },
        "dashboard": dashboard,
    }


@router.post("/classrooms/sync-academic", response_class=JSONResponse)
async def api_sync_academic_teaching_places(user: dict = Depends(get_current_teacher)):
    result = await sync_teaching_places_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步教学场地。")
    return result


@router.get("/classrooms/free-options", response_class=JSONResponse)
async def api_load_free_classroom_options(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    result = await load_free_classroom_options_from_academic_system(
        int(user["id"]),
        xnm=str(request.query_params.get("xnm") or "").strip(),
        xqm=str(request.query_params.get("xqm") or "").strip(),
        semester_id=str(request.query_params.get("semester_id") or "").strip(),
        xqh_id=str(request.query_params.get("xqh_id") or "1").strip() or "1",
    )
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能读取教务系统教室选项。")
    return result


@router.post("/classrooms/free-query", response_class=JSONResponse)
async def api_query_free_classrooms(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = await query_free_classrooms_from_academic_system(int(user["id"]), payload)
    status = result.get("status")
    if status == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if status == "invalid":
        raise HTTPException(400, result.get("message") or "请补全空闲教室查询条件。")
    if status != "success":
        raise HTTPException(502, result.get("message") or "未能实时查询教务系统空闲教室。")
    return result


@router.get("/classrooms/{class_offering_id}/exam-roster", response_class=JSONResponse)
async def api_get_classroom_exam_roster(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    result = load_classroom_exam_roster_status(int(user["id"]), int(class_offering_id))
    if result.get("status") == "not_found":
        raise HTTPException(404, result.get("message") or "课堂不存在或无权访问。")
    return result


@router.post("/classrooms/{class_offering_id}/exam-roster/sync", response_class=JSONResponse)
async def api_sync_classroom_exam_roster(
    class_offering_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = await sync_classroom_exam_roster_from_academic_system(
        int(user["id"]),
        int(class_offering_id),
        exam_course_key=str(payload.get("exam_course_key") or "").strip(),
    )
    status = result.get("status")
    if status == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if status in {"not_found", "no_semester"}:
        raise HTTPException(400 if status == "no_semester" else 404, result.get("message") or "无法同步考试名单。")
    if status == "needs_confirmation":
        return result
    if status != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步考试名单。")
    return result


@router.post("/classrooms/{class_offering_id}/exam-roster/export")
async def api_export_classroom_exam_roster(
    class_offering_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, int(user["id"]))
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        export_result = build_exam_roster_signature_workbook(
            int(user["id"]),
            int(class_offering_id),
            export_payload=payload,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(
        export_result["path"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=export_result["filename"],
        headers={"Cache-Control": "no-store"},
    )


@router.post("/classes/{class_id}/students", response_class=JSONResponse)
async def api_create_class_student(
    class_id: int,
    name: str = Form(...),
    student_id_number: str = Form(...),
    gender: str = Form(default=""),
    email: str = Form(default=""),
    phone: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """向已有班级追加单个学生，适用于插班等日常维护。"""
    cleaned_name = _clean_form_text(name, limit=80)
    cleaned_student_id = _clean_form_text(student_id_number, limit=80)
    cleaned_gender = _clean_form_text(gender, limit=20)
    cleaned_email = _clean_form_text(email, limit=160)
    cleaned_phone = _clean_form_text(phone, limit=80)

    if not cleaned_name:
        raise HTTPException(status_code=400, detail="请填写学生姓名")
    if not cleaned_student_id:
        raise HTTPException(status_code=400, detail="请填写学生学号")

    with get_db_connection() as conn:
        class_row = _ensure_teacher_owned_class(conn, class_id=class_id, teacher_id=user["id"])
        class_scope = apply_teacher_scope_to_org(
            conn,
            user["id"],
            school_code=class_row["school_code"] if "school_code" in class_row.keys() else "",
            school_name=class_row["school_name"] if "school_name" in class_row.keys() else "",
            college=class_row["college"] if "college" in class_row.keys() else "",
            department=class_row["department"] if "department" in class_row.keys() else "",
        )
        try:
            cursor = conn.execute(
                """
                INSERT INTO students (
                    student_id_number, name, class_id, gender, email, phone,
                    enrollment_status, enrollment_status_updated_at,
                    school_code, school_name, college, department
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_student_id,
                    cleaned_name,
                    int(class_id),
                    cleaned_gender,
                    cleaned_email,
                    cleaned_phone,
                    local_iso(),
                    class_scope["school_code"],
                    class_scope["school_name"],
                    class_scope["college"],
                    class_scope["department"],
                ),
            )
            student_id = int(cursor.lastrowid)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(
                status_code=400,
                detail="新增失败：该学号已经存在，请先确认学生是否已在其它班级名单中。",
            ) from exc

    return {
        "status": "success",
        "message": f"已将 {cleaned_name} 加入班级。",
        "student": {
            "id": student_id,
            "name": cleaned_name,
            "student_id_number": cleaned_student_id,
            "gender": cleaned_gender,
            "email": cleaned_email,
            "phone": cleaned_phone,
            "enrollment_status": STUDENT_STATUS_ACTIVE,
            "enrollment_status_label": student_enrollment_status_label(STUDENT_STATUS_ACTIVE),
        },
    }


@router.post("/students/{student_id}/status", response_class=JSONResponse)
async def api_update_class_student_status(
    student_id: int,
    enrollment_status: str = Form(...),
    enrollment_note: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """切换学生学籍状态；休学学生保留数据但不再纳入课堂管理统计。"""
    raw_status = str(enrollment_status or "").strip().lower()
    if raw_status not in {"active", "suspended", "在读", "休学"}:
        raise HTTPException(status_code=400, detail="学生状态参数不正确")

    normalized_status = normalize_student_enrollment_status(enrollment_status)
    note = _clean_form_text(enrollment_note, limit=500)

    with get_db_connection() as conn:
        student_row = _ensure_teacher_owned_student(conn, student_id=student_id, teacher_id=user["id"])
        conn.execute(
            """
            UPDATE students
            SET enrollment_status = ?,
                enrollment_status_updated_at = ?,
                enrollment_note = ?
            WHERE id = ?
            """,
            (normalized_status, local_iso(), note, int(student_id)),
        )
        conn.commit()

    if normalized_status != STUDENT_STATUS_ACTIVE:
        invalidate_session_for_user(str(student_id), "student")

    student_name = str(student_row["name"] or "学生")
    return {
        "status": "success",
        "message": f"{student_name} 已设置为{student_enrollment_status_label(normalized_status)}。",
        "student": {
            "id": int(student_id),
            "enrollment_status": normalized_status,
            "enrollment_status_label": student_enrollment_status_label(normalized_status),
            "enrollment_note": note,
        },
    }


@router.put("/students/{student_id}/support-note", response_class=JSONResponse)
async def api_update_student_support_note(
    request: Request,
    student_id: int,
    user: dict = Depends(get_current_teacher),
):
    """保存教师共享补充说明；同一学生的任课教师可共同查看和维护。"""
    data = await _parse_json_request(request)
    note_text = normalize_shared_teacher_note(data.get("note_text"))

    with get_db_connection() as conn:
        if not teacher_can_access_student(conn, teacher_id=int(user["id"]), student_id=int(student_id)):
            raise HTTPException(status_code=404, detail="学生不存在或无权操作")
        note = save_shared_student_teacher_note(
            conn,
            student_id=int(student_id),
            teacher_id=int(user["id"]),
            note_text=note_text,
            now_text=local_iso(),
        )
        conn.commit()

    message = "教师共享说明已保存。" if note_text else "教师共享说明已清空。"
    return {
        "status": "success",
        "message": message,
        "note": note,
        "limit": MAX_SHARED_NOTE_LENGTH,
    }


@router.delete("/students/{student_id}", response_class=JSONResponse)
async def api_delete_class_student(student_id: int, user: dict = Depends(get_current_teacher)):
    """从班级名册中删除单个学生及其关联课堂数据。"""
    with get_db_connection() as conn:
        student_row = _ensure_teacher_owned_student(conn, student_id=student_id, teacher_id=user["id"])
        student_name = str(student_row["name"] or "学生")
        try:
            conn.execute("DELETE FROM students WHERE id = ?", (int(student_id),))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"删除失败: {exc}") from exc

    invalidate_session_for_user(str(student_id), "student")
    return {"status": "success", "message": f"已删除学生 {student_name}。"}


# (新增) 删除班级
@router.delete("/classes/{class_id}", response_class=JSONResponse)
async def api_delete_class(class_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个班级 (及其所有学生和课堂关联)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM classes WHERE id = ? AND created_by_teacher_id = ?",
                (class_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该班级或班级不存在")

            # 删除 (依赖于 database.py 中设置的 PRAGMA foreign_keys = ON 和 ON DELETE CASCADE)
            # 1. 删除 students (通过外键)
            # 2. 删除 class_offerings (通过外键)
            # 3. 删除 class
            conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
            conn.commit()

    except HTTPException:
        raise
    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "班级删除成功。"}


# --- 课程管理 ---
@router.post("/courses/save", response_class=JSONResponse)
async def api_save_course(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    try:
        payload = _prepare_course_payload(data, require_lessons=True)
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        try:
            selected_material_ids = [
                lesson.get("learning_material_id")
                for lesson in payload["lessons"]
                if lesson.get("learning_material_id")
            ]
            material_map = get_learning_material_brief_map(
                conn,
                selected_material_ids,
                teacher_id=int(user["id"]),
                markdown_only=True,
            )
            if len(material_map) != len({int(item) for item in selected_material_ids}):
                raise HTTPException(400, "课程中选择的课堂材料不存在、无权访问，或不是 Markdown 文档")

            if payload["course_id"]:
                _ensure_teacher_can_manage_course(
                    conn,
                    course_id=payload["course_id"],
                    teacher_id=user["id"],
                )
                conn.execute(
                    """
                    UPDATE courses
                    SET name = ?, description = ?, sect_name = ?, department = ?, credits = ?, total_hours = ?
                    WHERE id = ?
                    """,
                    (
                        payload["name"],
                        payload["description"],
                        payload["sect_name"],
                        payload["department"],
                        payload["credits"],
                        payload["total_hours"],
                        payload["course_id"],
                    ),
                )
                course_id = int(payload["course_id"])
                action_text = "更新"
            else:
                org_scope = apply_teacher_scope_to_org(
                    conn,
                    user["id"],
                    department=payload["department"],
                )
                cursor = conn.execute(
                    """
                    INSERT INTO courses (
                        name, description, sect_name, department, credits, total_hours,
                        created_by_teacher_id, school_code, school_name, college
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["name"],
                        payload["description"],
                        payload["sect_name"],
                        payload["department"],
                        payload["credits"],
                        payload["total_hours"],
                        user["id"],
                        org_scope["school_code"],
                        org_scope["school_name"],
                        org_scope["college"],
                    ),
                )
                course_id = int(cursor.lastrowid)
                action_text = "创建"

            replace_course_lessons(conn, course_id=course_id, lessons=payload["lessons"])
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(400, f"保存课程失败：{exc}") from exc

    return {
        "status": "success",
        "message": f"课程“{payload['name']}”已{action_text}",
        "course_id": course_id,
    }


@router.post("/courses/sync-current-academic", response_class=JSONResponse)
async def api_sync_current_courses_from_academic_system(
    user: dict = Depends(get_current_teacher),
):
    result = await sync_current_teacher_courses_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        raise HTTPException(502, result.get("message") or "未能从教务系统同步课程。")
    return result


@router.post("/courses/ai-generate-lessons", response_class=JSONResponse)
async def api_ai_generate_course_lessons(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    course_name = str(data.get("name") or "").strip()
    course_description = str(data.get("description") or "").strip()
    textbook_id = _parse_optional_int(data.get("textbook_id"))
    total_hours = normalize_total_hours(data.get("total_hours"))
    per_session_sections = _parse_optional_int(data.get("per_session_sections"))

    if not course_name:
        raise HTTPException(400, "请先填写课程名称")
    if not textbook_id:
        raise HTTPException(400, "请先选择教材")
    if total_hours <= 0:
        raise HTTPException(400, "请先填写课程总学时")
    if not per_session_sections or per_session_sections <= 0:
        raise HTTPException(400, "请先填写每次课的小节数")
    if total_hours % per_session_sections != 0:
        raise HTTPException(400, "课程总学时必须能被每次课的小节数整除")

    session_count = total_hours // per_session_sections
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )
        textbook = serialize_textbook_row(textbook_row)

    textbook_context = build_textbook_prompt_context(textbook)
    system_prompt = (
        "你是一名高校课程设计专家。请根据课程名称、课程简介和教材内容，"
        "为教师拆分出可直接落地的课堂设置。输出必须是合法 JSON 对象，"
        "不要输出 Markdown 代码块。"
    )
    user_message = (
        f"课程名称：{course_name}\n"
        f"课程简介：{course_description or '未补充'}\n"
        f"教材信息：\n{textbook_context}\n\n"
        f"请把课程拆成 {session_count} 次课，每次课固定 {per_session_sections} 小节。\n"
        "输出 JSON 对象，格式如下：\n"
        "{\n"
        '  "lessons": [\n'
        '    {"title": "第1次课标题", "content": "本次课内容概述，尽量具体到知识点、实验或案例。", "section_count": 2}\n'
        "  ]\n"
        "}\n\n"
        "要求：\n"
        "1. lessons 数量必须严格等于指定的课次数。\n"
        "2. 每一项都要贴合教材目录，内容循序渐进，避免重复。\n"
        "3. title 简洁明确，content 重点说明本次课讲什么、做什么。\n"
        "4. section_count 统一填写为指定的小节数。\n"
        "5. 不要输出额外解释文字。"
    )

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "thinking",
                "task_type": "deep_text_reasoning",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")

    if data.get("status") != "success":
        raise HTTPException(500, f"AI 返回异常: {data.get('detail', '未知错误')}")

    response_text = str(data.get("response_text") or "").strip()
    if not response_text:
        raise HTTPException(500, "AI 未返回有效内容")

    try:
        parsed = _parse_ai_json(response_text)
        generated_lessons = normalize_course_lessons(parsed.get("lessons"), require_items=True)
    except (json.JSONDecodeError, CoursePlanningError) as exc:
        raise HTTPException(500, f"AI 返回格式不正确：{exc}") from exc

    if len(generated_lessons) != session_count:
        raise HTTPException(
            500,
            f"AI 返回了 {len(generated_lessons)} 条课堂设置，预期应为 {session_count} 条，请重试。",
        )

    for item in generated_lessons:
        item["section_count"] = per_session_sections
        item["source_type"] = "ai"

    return {
        "status": "success",
        "message": f"已根据教材拆分出 {session_count} 次课，可继续手动调整。",
        "session_count": session_count,
        "total_hours": total_hours,
        "lessons": generated_lessons,
    }


@router.post("/courses/create", response_class=JSONResponse)
async def api_create_course(
        request: Request,
        name: str = Form(...),  # 改为必填
        description: str = Form(default=""),  # 明确指定默认值
        sect_name: str = Form(default=""),
        department: str = Form(default=""),
        credits: float = Form(default=0.0),  # 明确指定默认值
        user: dict = Depends(get_current_teacher)
):
    try:
        # 添加参数验证
        if not name or len(name.strip()) == 0:
            raise HTTPException(400, "课程名称不能为空")

        normalized_department = normalize_department(department) or infer_department_from_text(name, description)
        with get_db_connection() as conn:
            org_scope = apply_teacher_scope_to_org(
                conn,
                user["id"],
                department=normalized_department,
            )
            conn.execute(
                """
                INSERT INTO courses (
                    name, description, sect_name, department, credits, created_by_teacher_id,
                    school_code, school_name, college
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    description,
                    normalize_course_sect_name(sect_name, course_name=name),
                    normalized_department,
                    credits,
                    user['id'],
                    org_scope["school_code"],
                    org_scope["school_name"],
                    org_scope["college"],
                )
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建课程失败，可能名称已存在。")
    except Exception as e:
        print(f"创建课程错误: {str(e)}")  # 添加错误日志
        raise HTTPException(500, f"创建课程失败: {str(e)}")

    return {"status": "success", "message": f"课程 '{name}' 创建成功。"}


# (新增) 删除课程
@router.delete("/courses/{course_id}", response_class=JSONResponse)
async def api_delete_course(course_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个课程 (及其所有文件和课堂关联)"""
    try:
        with get_db_connection() as conn:
            course_row = _ensure_teacher_can_manage_course(
                conn,
                course_id=course_id,
                teacher_id=user["id"],
            )
            linked_count_row = conn.execute(
                "SELECT COUNT(*) AS count FROM class_offerings WHERE course_id = ?",
                (course_id,),
            ).fetchone()
            linked_count = int((linked_count_row["count"] if linked_count_row else 0) or 0)
            if linked_count > 0:
                raise HTTPException(
                    400,
                    f"课程“{course_row['name']}”已被 {linked_count} 个课堂使用，请先调整课堂绑定后再删除",
                )

            conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))

            # TODO: 还应按引用计数清理未被其他课程复用的哈希文件。

            conn.commit()

    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "课程删除成功。"}


@router.post("/courses/{course_id}/files/upload", response_class=JSONResponse)
async def api_upload_course_file(
        course_id: int,
        file: UploadFile = File(...),
        is_public: bool = Form(True),
        is_teacher_resource: bool = Form(False),
        user: dict = Depends(get_current_teacher)
):
    """上传课程资源文件"""
    # 检查教师是否拥有此课程
    with get_db_connection() as conn:
        course = conn.execute("SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
                              (course_id, user['id'])).fetchone()
    if not course:
        raise HTTPException(403, "无权操作此课程")

    file_info = await save_file_globally(file)
    original_filename = "".join(
        c for c in str(file.filename or "upload") if c.isalnum() or c in (".", "_", "-")
    ).strip() or "upload"
    if not file_info:
        raise HTTPException(500, "保存文件到服务器失败")

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO course_files
                (course_id, file_name, file_hash, file_size, is_public, is_teacher_resource, uploaded_by_teacher_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course_id,
                original_filename,
                file_info["hash"],
                file_info["size"],
                is_public,
                is_teacher_resource,
                user["id"],
            )
        )
        conn.commit()

    return {"status": "success", "message": f"文件 '{original_filename}' 上传成功。"}


# --- 学期与教材管理 ---
@router.post("/semesters/save", response_class=JSONResponse)
async def api_save_semester(
    background_tasks: BackgroundTasks,
    semester_id: str = Form(default=""),
    name: str = Form(default=""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    user: dict = Depends(get_current_teacher),
):
    semester_id_value = int(str(semester_id).strip()) if str(semester_id).strip() else None
    try:
        start_date_value = parse_date_input(start_date, "学期开始时间")
        end_date_value = parse_date_input(end_date, "学期结束时间")
        if not start_date_value or not end_date_value:
            raise HTTPException(400, "请完整填写学期起止日期")
        week_count = compute_semester_week_count(start_date_value, end_date_value)
        semester_name = str(name or "").strip() or infer_semester_name(start_date_value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        try:
            saved_semester_id: int | None = semester_id_value
            teacher_scope = load_teacher_org_scope(conn, int(user["id"]))
            should_sync_calendar = False
            if semester_id_value:
                _ensure_teacher_can_manage_semester(
                    conn,
                    semester_id=semester_id_value,
                    teacher_id=user["id"],
                )
                conn.execute(
                    """
                    UPDATE academic_semesters
                    SET name = ?,
                        start_date = ?,
                        end_date = ?,
                        week_count = ?,
                        school_code = ?,
                        school_name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        week_count,
                        teacher_scope["school_code"],
                        teacher_scope["school_name"],
                        semester_id_value,
                    ),
                )
                action_text = "更新"
                should_sync_calendar = True
            else:
                existing_row = conn.execute(
                    """
                    SELECT id, teacher_id, calendar_sync_status
                    FROM academic_semesters
                    WHERE lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
                      AND (
                          lower(TRIM(name)) = lower(TRIM(?))
                          OR (start_date = ? AND end_date = ?)
                      )
                    ORDER BY
                        CASE WHEN lower(TRIM(name)) = lower(TRIM(?)) THEN 0 ELSE 1 END,
                        updated_at DESC,
                        id DESC
                    LIMIT 1
                    """,
                    (
                        teacher_scope["school_code"],
                        teacher_scope["school_code"],
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        semester_name,
                    ),
                ).fetchone()
                if existing_row:
                    saved_semester_id = int(existing_row["id"])
                    action_text = "复用"
                else:
                    cursor = conn.execute(
                    """
                    INSERT INTO academic_semesters (
                        teacher_id, school_code, school_name, name, start_date, end_date, week_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        teacher_scope["school_code"],
                        teacher_scope["school_name"],
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        week_count,
                    ),
                    )
                    saved_semester_id = int(cursor.lastrowid)
                    action_text = "创建"
                    should_sync_calendar = True
            if saved_semester_id and should_sync_calendar:
                mark_semester_calendar_sync_queued(
                    conn,
                    teacher_id=int(user["id"]),
                    semester_id=int(saved_semester_id),
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, f"保存失败，学期名称“{semester_name}”已存在") from exc

    if saved_semester_id and should_sync_calendar:
        background_tasks.add_task(
            sync_semester_calendar_background,
            int(user["id"]),
            int(saved_semester_id),
        )

    return {
        "status": "success",
        "message": (
            f"学期已{action_text}：{semester_name}，校历同步已开始。"
            if should_sync_calendar
            else f"已复用同校学期：{semester_name}。"
        ),
        "semester_id": saved_semester_id,
        "calendar_sync_status": "pending" if should_sync_calendar else "",
    }


@router.post("/semesters/{semester_id}/calendar/sync", response_class=JSONResponse)
async def api_sync_semester_calendar(
    semester_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_can_manage_semester(
            conn,
            semester_id=semester_id,
            teacher_id=user["id"],
        )
        mark_semester_calendar_sync_queued(
            conn,
            teacher_id=int(user["id"]),
            semester_id=int(semester_id),
        )
        conn.commit()

    background_tasks.add_task(sync_semester_calendar_background, int(user["id"]), int(semester_id))
    return {
        "status": "success",
        "message": "校历同步已开始，系统会自动拉取教务系统并核对广西节假日/补课日期。",
        "semester_id": int(semester_id),
    }


@router.post("/semesters/calendar/sync-current", response_class=JSONResponse)
async def api_sync_current_semester_from_academic_system(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_teacher),
):
    result = await prepare_current_semester_from_academic_system(int(user["id"]))
    if result.get("status") == "missing_credential":
        raise HTTPException(400, result.get("message") or "请先配置教务系统账号。")
    if result.get("status") != "success":
        detail = result.get("message") or "未能从教务系统同步当前学期。"
        source_message = str(result.get("source_message") or "").strip()
        if source_message:
            detail = f"{detail}（{source_message}）"
        raise HTTPException(502, detail)

    semester_id = int(result["semester_id"])
    if result.get("should_sync_calendar", True):
        background_tasks.add_task(sync_semester_calendar_background, int(user["id"]), semester_id)
    return {
        "status": "success",
        "message": result.get("message") or "已从教务系统同步本学期，校历处理已开始。",
        "semester_id": semester_id,
        "action": result.get("action") or "",
        "calendar_sync_status": "pending" if result.get("should_sync_calendar", True) else "",
    }


@router.delete("/semesters/{semester_id}", response_class=JSONResponse)
async def api_delete_semester(semester_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        semester_row = _ensure_teacher_can_manage_semester(
            conn,
            semester_id=semester_id,
            teacher_id=user["id"],
        )
        offering_count = conn.execute(
            "SELECT COUNT(*) AS count FROM class_offerings WHERE semester_id = ?",
            (semester_id,),
        ).fetchone()
        linked_count = int((offering_count["count"] if offering_count else 0) or 0)
        if linked_count > 0:
            raise HTTPException(
                400,
                f"该学期已被 {linked_count} 个课堂使用，请先调整课堂的学期绑定后再删除",
            )

        conn.execute(
            "DELETE FROM academic_semesters WHERE id = ?",
            (semester_id,),
        )
        conn.commit()

    return {
        "status": "success",
        "message": f"学期“{semester_row['name']}”已删除",
    }


@router.post("/textbooks/save", response_class=JSONResponse)
async def api_save_textbook(
    textbook_id: str = Form(default=""),
    title: str = Form(...),
    authors_json: str = Form(default="[]"),
    publisher: str = Form(default=""),
    publication_date: str = Form(default=""),
    introduction: str = Form(default=""),
    catalog_text: str = Form(default=""),
    tags_json: str = Form(default="[]"),
    remove_attachment: bool = Form(default=False),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    normalized_title = str(title or "").strip()
    textbook_id_value = int(str(textbook_id).strip()) if str(textbook_id).strip() else None
    if not normalized_title:
        raise HTTPException(400, "教材名称不能为空")

    try:
        authors = parse_json_list_field(
            authors_json,
            field_name="作者",
            max_items=12,
            max_length=30,
        )
        tags = parse_json_list_field(
            tags_json,
            field_name="标签",
            max_items=20,
            max_length=12,
        )
        publication_date_value = parse_date_input(publication_date, "出版日期")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    attachment_info = None
    old_attachment_path = ""
    old_attachment_name = ""
    old_attachment_size = 0
    old_attachment_mime_type = ""

    if attachment and str(attachment.filename or "").strip():
        upload_dir = TEXTBOOK_ATTACHMENT_DIR / str(user["id"])
        attachment_info = await save_upload_file(upload_dir, attachment)
        if not attachment_info:
            raise HTTPException(500, "教材附件保存失败")

    with get_db_connection() as conn:
        try:
            if textbook_id_value:
                existing_row = _ensure_teacher_owned_record(
                    conn,
                    table="textbooks",
                    record_id=textbook_id_value,
                    teacher_id=user["id"],
                    owner_column="teacher_id",
                )
                old_attachment_path = str(existing_row["attachment_path"] or "")
                old_attachment_name = str(existing_row["attachment_name"] or "")
                old_attachment_size = int(existing_row["attachment_size"] or 0)
                old_attachment_mime_type = str(existing_row["attachment_mime_type"] or "")

                attachment_name = old_attachment_name
                attachment_path = old_attachment_path
                attachment_size = old_attachment_size
                attachment_mime_type = old_attachment_mime_type

                if remove_attachment and not attachment_info:
                    attachment_name = ""
                    attachment_path = ""
                    attachment_size = 0
                    attachment_mime_type = ""

                if attachment_info:
                    attachment_name = str(attachment_info["original_filename"] or "")
                    attachment_path = str(attachment_info["stored_path"] or "")
                    attachment_size = int(Path(attachment_path).stat().st_size) if attachment_path else 0
                    attachment_mime_type = str(attachment.content_type or "")

                conn.execute(
                    """
                    UPDATE textbooks
                    SET title = ?,
                        authors_json = ?,
                        publisher = ?,
                        publication_date = ?,
                        introduction = ?,
                        catalog_text = ?,
                        attachment_name = ?,
                        attachment_path = ?,
                        attachment_size = ?,
                        attachment_mime_type = ?,
                        tags_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (
                        normalized_title,
                        json.dumps(authors, ensure_ascii=False),
                        str(publisher or "").strip(),
                        publication_date_value.isoformat() if publication_date_value else "",
                        str(introduction or "").strip(),
                        str(catalog_text or "").strip(),
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        json.dumps(tags, ensure_ascii=False),
                        textbook_id_value,
                        user["id"],
                    ),
                )
                persisted_textbook_id = textbook_id_value
                action_text = "更新"
            else:
                attachment_name = str(attachment_info["original_filename"] or "") if attachment_info else ""
                attachment_path = str(attachment_info["stored_path"] or "") if attachment_info else ""
                attachment_size = int(Path(attachment_path).stat().st_size) if attachment_path else 0
                attachment_mime_type = str(attachment.content_type or "") if attachment_info else ""
                cursor = conn.execute(
                    """
                    INSERT INTO textbooks (
                        teacher_id,
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
                        tags_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        normalized_title,
                        json.dumps(authors, ensure_ascii=False),
                        str(publisher or "").strip(),
                        publication_date_value.isoformat() if publication_date_value else "",
                        str(introduction or "").strip(),
                        str(catalog_text or "").strip(),
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        json.dumps(tags, ensure_ascii=False),
                    ),
                )
                persisted_textbook_id = int(cursor.lastrowid)
                action_text = "创建"

            conn.commit()
        except Exception:
            if attachment_info:
                _remove_file_if_exists(attachment_info.get("stored_path"))
            raise

    if attachment_info and old_attachment_path and old_attachment_path != attachment_info.get("stored_path"):
        _remove_file_if_exists(old_attachment_path)
    elif remove_attachment and old_attachment_path and not attachment_info:
        _remove_file_if_exists(old_attachment_path)

    return {
        "status": "success",
        "message": f"教材已{action_text}：{normalized_title}",
        "textbook_id": persisted_textbook_id,
    }


@router.delete("/textbooks/{textbook_id}", response_class=JSONResponse)
async def api_delete_textbook(textbook_id: int, user: dict = Depends(get_current_teacher)):
    attachment_path = ""
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )
        offering_count = conn.execute(
            "SELECT COUNT(*) AS count FROM class_offerings WHERE textbook_id = ? AND teacher_id = ?",
            (textbook_id, user["id"]),
        ).fetchone()
        linked_count = int((offering_count["count"] if offering_count else 0) or 0)
        if linked_count > 0:
            raise HTTPException(
                400,
                f"该教材已被 {linked_count} 个课堂绑定，请先调整课堂教材后再删除",
            )

        attachment_path = str(textbook_row["attachment_path"] or "")
        conn.execute(
            "DELETE FROM textbooks WHERE id = ? AND teacher_id = ?",
            (textbook_id, user["id"]),
        )
        conn.commit()

    _remove_file_if_exists(attachment_path)
    return {
        "status": "success",
        "message": f"教材“{textbook_row['title']}”已删除",
    }


@router.get("/textbooks/{textbook_id}/attachment")
async def api_download_textbook_attachment(textbook_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )

    attachment_path = str(textbook_row["attachment_path"] or "").strip()
    if not attachment_path:
        raise HTTPException(404, "该教材没有附件")

    file_path = resolve_migrated_file_path(
        attachment_path,
        active_root=TEXTBOOK_ATTACHMENT_DIR,
        legacy_roots=TEXTBOOK_ATTACHMENT_LEGACY_DIRS,
        markers=("storage/textbook_attachments", "files/textbook_attachments", "textbook_attachments"),
    )
    if not file_path:
        raise HTTPException(404, "教材附件不存在或已丢失")

    media_type = str(textbook_row["attachment_mime_type"] or "").strip() or None
    return FileResponse(
        path=file_path,
        filename=str(textbook_row["attachment_name"] or file_path.name),
        media_type=media_type,
    )


def _strip_code_fence(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _parse_ai_json(raw_text: str) -> dict:
    cleaned = _strip_code_fence(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise


@router.post("/textbooks/ai-format-intro-catalog", response_class=JSONResponse)
async def api_ai_format_textbook_intro_catalog(
    title: str = Form(default=""),
    publisher: str = Form(default=""),
    authors_json: str = Form(default="[]"),
    publication_date: str = Form(default=""),
    tags_json: str = Form(default="[]"),
    raw_introduction: str = Form(default=""),
    raw_catalog: str = Form(default=""),
    custom_requirements: str = Form(default=""),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    """Call AI (thinking model) to format and organize textbook introduction and catalog."""
    has_intro = bool(str(raw_introduction or "").strip())
    has_catalog = bool(str(raw_catalog or "").strip())
    if not has_intro and not has_catalog:
        raise HTTPException(400, "请至少填写教材简介或教材目录")

    # Parse basic info for context
    normalized_title = str(title or "").strip() or "未命名教材"
    try:
        authors = parse_json_list_field(
            authors_json, field_name="作者", max_items=12, max_length=30,
        )
        tags = parse_json_list_field(
            tags_json, field_name="标签", max_items=20, max_length=12,
        )
    except ValueError:
        authors = []
        tags = []

    publication_year = ""
    if publication_date:
        try:
            publication_year = str(parse_date_input(publication_date).year)
        except Exception:
            pass

    # Extract attachment text if provided
    attachment_text = ""
    if attachment and str(attachment.filename or "").strip():
        try:
            contents = await attachment.read()
            if contents:
                filename = str(attachment.filename or "").strip()
                ext = Path(filename).suffix.lower()
                text_exts = {
                    ".txt", ".md", ".py", ".js", ".ts", ".html", ".htm", ".css",
                    ".json", ".xml", ".yaml", ".yml", ".csv", ".log",
                }
                if ext in text_exts:
                    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
                        try:
                            attachment_text = contents.decode(enc)
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    if not attachment_text:
                        attachment_text = contents.decode("utf-8", errors="replace")
                elif ext in {".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt", ".pdf"}:
                    try:
                        from ai_assistant_doc_extract import extract_document_text
                        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                            tmp.write(contents)
                            tmp_path = tmp.name
                        try:
                            result = extract_document_text(Path(tmp_path), ext)
                            attachment_text = str(result.text or "") if result else ""
                        finally:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                    except Exception as exc:
                        print(f"[TEXTBOOK_AI] 附件文本提取失败 ({filename}): {exc}")
        except Exception as exc:
            print(f"[TEXTBOOK_AI] 附件读取失败: {exc}")

    # Build context block
    context_parts = [f"教材名称：{normalized_title}"]
    if publisher:
        context_parts.append(f"出版社：{publisher}")
    if authors:
        context_parts.append(f"作者：{'、'.join(authors)}")
    if publication_year:
        context_parts.append(f"出版年份：{publication_year}")
    if tags:
        context_parts.append(f"标签：{'、'.join(tags)}")
    context_block = "\n".join(context_parts)

    # Build content block
    content_parts = []
    if has_intro:
        content_parts.append(f"【原始简介】\n{raw_introduction.strip()}")
    if has_catalog:
        content_parts.append(f"【原始目录】\n{raw_catalog.strip()}")
    if attachment_text:
        content_parts.append(f"【附件内容】\n{attachment_text}")
    content_block = "\n\n".join(content_parts)

    system_prompt = (
        "你是一名高校教材内容整理助手，负责将教师提供的教材简介和目录重新规整化。"
        "你的输出必须是合法的 JSON 对象，包含两个键：\n"
        "- \"introduction\"：教材简介文本（字符串）\n"
        "- \"catalog_text\"：教材目录文本（字符串）\n\n"
        "工作要求：\n"
        "1. 目录整理：\n"
        "   - 必须完整保留原始目录中的所有章节和小节，不得遗漏任何一个条目。\n"
        "   - 如果原始目录格式混乱（如编号不一致、层级不清），请统一为「第X章」+「X.X 小节」的清晰层级格式。\n"
        "   - 保持原始缩进或用编号体现层级关系，使目录结构一目了然。\n"
        "   - 如果原始目录有重复或明显错误的编号，请自动修正。\n"
        "2. 简介改写：\n"
        "   - 将简介改写为适合学生阅读的课程导引式文本，语气亲切自然。\n"
        "   - 概括教材的核心内容、适用对象和学习目标。\n"
        "   - 突出本教材的关键知识点和教学特色，方便后续课堂AI助手理解本门课的基本要点。\n"
        "   - 如果原始简介信息不足，可以结合目录内容进行合理的补充概括，但不要编造不存在的内容。\n"
        "3. 如果提供了附件内容，请结合附件中的信息来完善简介和目录。\n"
        "4. 只输出 JSON 对象，不要输出任何额外的解释或 Markdown 代码块标记。"
    )

    user_message = f"以下是教材的基本信息及需要整理的内容：\n\n{context_block}\n\n{content_block}"
    if custom_requirements and custom_requirements.strip():
        user_message += f"\n\n【教师的自定义要求】\n{custom_requirements.strip()}"

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "thinking",
                "task_type": "deep_text_reasoning",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")

    if data.get("status") != "success":
        raise HTTPException(500, f"AI 返回异常: {data.get('detail', '未知错误')}")

    response_text = str(data.get("response_text") or "").strip()
    if not response_text:
        raise HTTPException(500, "AI 未返回有效内容")

    try:
        parsed = _parse_ai_json(response_text)
    except json.JSONDecodeError:
        raise HTTPException(500, "AI 返回的内容格式不正确，请重试")

    formatted_intro = str(parsed.get("introduction") or "").strip()
    formatted_catalog = str(parsed.get("catalog_text") or "").strip()

    if not formatted_intro and not formatted_catalog:
        raise HTTPException(500, "AI 返回的内容为空，请重试")

    return {
        "status": "success",
        "introduction": formatted_intro,
        "catalog_text": formatted_catalog,
    }


# --- 班级课堂 (关联) ---
@router.post("/class_offerings/preview", response_class=JSONResponse)
async def api_preview_class_offering(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    try:
        with get_db_connection() as conn:
            payload = _prepare_offering_payload(
                conn,
                teacher_id=int(user["id"]),
                data=data,
                require_schedule=True,
                allow_missing_lessons=True,
            )
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc

    plan = payload["plan"]
    planned_section_count = sum(int(item.get("section_count") or 0) for item in payload["course_lessons"])

    return {
        "status": "success",
        "preview": plan,
        "class_name": str(payload["class_row"]["name"] or ""),
        "course_name": str(payload["course_row"]["name"] or ""),
        "semester_name": str(payload["semester_row"]["name"] or ""),
        "textbook_title": str(payload["textbook_row"]["title"] or ""),
        "course_lesson_count": len(payload["course_lessons"]),
        "planned_section_count": planned_section_count,
        "course_total_hours": int(payload["course_row"]["total_hours"] or 0),
        "schedule_source": payload["schedule_source"],
        "academic_teaching_class_name": payload["academic_teaching_class_name"],
        "academic_teaching_class_options": payload["academic_teaching_class_options"],
    }


@router.post("/class_offerings/save", response_class=JSONResponse)
async def api_save_class_offering(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await _parse_json_request(request)

    try:
        with get_db_connection() as conn:
            payload = _prepare_offering_payload(
                conn,
                teacher_id=int(user["id"]),
                data=data,
                require_schedule=True,
                allow_missing_lessons=False,
            )

            if payload["offering_id"]:
                _ensure_teacher_owned_offering(conn, payload["offering_id"], user["id"])
                conn.execute(
                    """
                    UPDATE class_offerings
                    SET class_id = ?,
                        course_id = ?,
                        semester = ?,
                        semester_id = ?,
                        textbook_id = ?,
                        schedule_info = ?,
                        first_class_date = ?,
                        weekly_schedule_json = ?,
                        schedule_source = ?,
                        academic_teaching_class_name = ?,
                        academic_schedule_sync_at = ?,
                        academic_schedule_sync_message = ?
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (
                        payload["class_id"],
                        payload["course_id"],
                        str(payload["semester_row"]["name"] or "").strip(),
                        payload["semester_id"],
                        payload["textbook_id"],
                        payload["plan"]["schedule_info"],
                        payload["first_class_date"].isoformat() if payload["first_class_date"] else "",
                        payload["weekly_schedule_json"],
                        payload["schedule_source"],
                        payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "保存课堂时使用教务实际排课生成时间轴。"
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                        payload["offering_id"],
                        user["id"],
                    ),
                )
                offering_id = int(payload["offering_id"])
                action_text = "更新"
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO class_offerings (
                        class_id,
                        course_id,
                        teacher_id,
                        semester,
                        semester_id,
                        textbook_id,
                        schedule_info,
                        first_class_date,
                        weekly_schedule_json,
                        schedule_source,
                        academic_teaching_class_name,
                        academic_schedule_sync_at,
                        academic_schedule_sync_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["class_id"],
                        payload["course_id"],
                        user["id"],
                        str(payload["semester_row"]["name"] or "").strip(),
                        payload["semester_id"],
                        payload["textbook_id"],
                        payload["plan"]["schedule_info"],
                        payload["first_class_date"].isoformat() if payload["first_class_date"] else "",
                        payload["weekly_schedule_json"],
                        payload["schedule_source"],
                        payload["academic_teaching_class_name"],
                        datetime.now().isoformat(timespec="seconds")
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else None,
                        "保存课堂时使用教务实际排课生成时间轴。"
                        if payload["schedule_source"] == SCHEDULE_SOURCE_ACADEMIC_SYNC
                        else "",
                    ),
                )
                offering_id = int(cursor.lastrowid)
                action_text = "开设"

            replace_offering_sessions(
                conn,
                offering_id=offering_id,
                sessions=payload["plan"]["sessions"],
            )
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=offering_id,
                teacher_id=int(user["id"]),
                material_ids=[
                    session.get("learning_material_id")
                    for session in payload["plan"]["sessions"]
                    if session.get("learning_material_id")
                ],
            )
            conn.commit()
    except CoursePlanningError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError:
        raise HTTPException(400, "保存失败，该班级课程在当前学期可能已存在。")
    except Exception as exc:
        raise HTTPException(500, f"数据库错误: {exc}")

    return {
        "status": "success",
        "message": (
            f"课堂已{action_text}，并生成 {payload['plan']['session_count']} 次课的时间安排。"
        ),
        "offering_id": offering_id,
        "preview": payload["plan"],
    }


@router.post("/class_offerings/create", response_class=JSONResponse)
async def api_create_class_offering(
        request: Request,
        class_id: int = Form(...),
        course_id: int = Form(...),
        semester_id: int = Form(...),
        textbook_id: int = Form(...),
        user: dict = Depends(get_current_teacher)
):
    try:
        with get_db_connection() as conn:
            _, _, semester_row, textbook_row = _validate_teacher_owned_selection(
                conn,
                teacher_id=user["id"],
                class_id=class_id,
                course_id=course_id,
                semester_id=semester_id,
                textbook_id=textbook_id,
            )
            conn.execute(
                """
                INSERT INTO class_offerings (
                    class_id,
                    course_id,
                    teacher_id,
                    semester,
                    semester_id,
                    textbook_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_id,
                    course_id,
                    user["id"],
                    str(semester_row["name"] or "").strip(),
                    semester_id,
                    textbook_id,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建失败，该班级课程在当前学期可能已存在。")
    except Exception as e:
        raise HTTPException(500, f"数据库错误: {e}")

    return {
        "status": "success",
        "message": f"课堂已开设，并绑定学期“{semester_row['name']}”和教材“{textbook_row['title']}”",
    }


# (新增) 删除课堂
@router.delete("/class_offerings/{offering_id}", response_class=JSONResponse)
async def api_delete_class_offering(offering_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个课堂 (及其AI配置和聊天记录)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                (offering_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该课堂或课堂不存在")

            # 删除 (依赖于 ON DELETE CASCADE)
            # 1. 删除 chat_logs (通过外键)
            # 2. 删除 ai_class_configs (通过外键)
            # 3. 删除 class_offering
            conn.execute("DELETE FROM class_offerings WHERE id = ?", (offering_id,))
            conn.commit()

    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "课堂关联删除成功。"}


# --- 课堂 AI 配置 ---
@router.post("/ai/configure", response_class=JSONResponse)
async def api_configure_ai_offering(
        class_offering_id: int = Form(...),
        system_prompt: str = Form(""),
        syllabus: str = Form(""),
        textbook_id: str = Form(default=""),
        user: dict = Depends(get_current_teacher)
):
    """
    创建或更新一个特定课堂的 AI 配置，并同步更新教材绑定
    """
    conn = get_db_connection()
    try:
        _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])

        textbook_id_value = int(str(textbook_id).strip()) if str(textbook_id).strip() else None
        bound_textbook_id = None
        if textbook_id_value:
            textbook_row = _ensure_teacher_owned_record(
                conn,
                table="textbooks",
                record_id=textbook_id_value,
                teacher_id=user["id"],
                owner_column="teacher_id",
            )
            bound_textbook_id = int(textbook_row["id"])

        conn.execute(
            """
            UPDATE class_offerings
            SET textbook_id = ?
            WHERE id = ? AND teacher_id = ?
            """,
            (bound_textbook_id, class_offering_id, user["id"]),
        )

        conn.execute(
            """
            INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus)
            VALUES (?, ?, ?)
            ON CONFLICT(class_offering_id) DO UPDATE SET
                system_prompt = excluded.system_prompt,
                syllabus = excluded.syllabus,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                class_offering_id,
                str(system_prompt or "").strip(),
                str(syllabus or "").strip(),
            ),
        )

        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"配置保存失败: {e}")
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "AI 配置保存成功！",
        "class_offering_id": class_offering_id,
        "textbook_id": bound_textbook_id,
    }


# (新增) 获取 AI 配置 (用于前端加载)
@router.get("/ai/config/{class_offering_id}", response_class=JSONResponse)
async def api_get_ai_config(class_offering_id: int, user: dict = Depends(get_current_teacher)):
    """获取一个特定课堂的 AI 配置"""
    conn = get_db_connection()
    try:
        offering = _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])
        config_row = conn.execute(
            "SELECT system_prompt, syllabus FROM ai_class_configs WHERE class_offering_id = ?",
            (class_offering_id,),
        ).fetchone()
        classroom_context = build_classroom_ai_context(conn, class_offering_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    config = dict(config_row) if config_row else {"system_prompt": "", "syllabus": ""}
    return {
        **config,
        "textbook_id": int(offering["textbook_id"]) if offering["textbook_id"] else None,
        "semester_name": str(offering["semester_name"] or ""),
        "textbook": classroom_context.get("textbook") or None,
        "classroom_summary": classroom_context.get("classroom_summary") or "",
        "textbook_summary": classroom_context.get("textbook_summary") or "",
        "recent_material_names": classroom_context.get("recent_material_names") or [],
        "recent_assignment_titles": classroom_context.get("recent_assignment_titles") or [],
    }


# --- 课堂 AI 智能生成 ---
@router.post("/ai/ai-generate", response_class=JSONResponse)
async def api_ai_generate_config(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """调用思考模型 AI，根据课堂和教材信息生成系统提示词和课程大纲。"""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "请求数据格式错误")

    class_offering_id = data.get("class_offering_id")
    textbook_id = data.get("textbook_id")

    if not class_offering_id:
        raise HTTPException(400, "请先选择一个课堂")
    try:
        class_offering_id = int(class_offering_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "无效的课堂 ID")

    if not textbook_id:
        raise HTTPException(400, "请先选择一本教材，AI 生成需要教材信息作为知识依据")
    try:
        textbook_id = int(textbook_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "无效的教材 ID")

    # 获取课堂和教材上下文
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])
        _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )
        classroom_context = build_classroom_ai_context(conn, class_offering_id)

    if not classroom_context:
        raise HTTPException(404, "课堂信息不存在")

    classroom_summary = classroom_context.get("classroom_summary") or ""
    textbook_summary = classroom_context.get("textbook_summary") or ""
    textbook = classroom_context.get("textbook") or {}
    recent_materials = classroom_context.get("recent_material_names") or []
    recent_assignments = classroom_context.get("recent_assignment_titles") or []

    teacher_name = classroom_context.get("teacher_name") or "任课教师"
    course_name = classroom_context.get("course_name") or "课程"
    class_name = classroom_context.get("class_name") or "班级"
    semester_name = classroom_context.get("semester_name") or ""
    class_student_count = classroom_context.get("class_student_count") or 0
    course_credits = classroom_context.get("course_credits")
    course_description = classroom_context.get("course_description") or ""

    # 构建发送给 AI 的提示词
    system_prompt_for_ai = (
        "你是一名高校课堂 AI 助教配置专家。根据教师提供的课堂信息和教材信息，"
        "为其生成课堂 AI 助教的「系统提示词」和「课程大纲 / 知识依据」。\n\n"
        "你的输出必须是合法的 JSON 对象，包含两个键：\n"
        "- \"system_prompt\"：课堂 AI 助教的系统提示词（字符串）\n"
        "- \"syllabus\"：课程大纲 / 知识依据（字符串）\n\n"
        "只输出 JSON 对象，不要输出任何额外的解释或 Markdown 代码块标记。"
    )

    user_message_parts = [
        f"请为以下课堂生成 AI 助教配置：\n",
        f"--- 课堂基本信息 ---",
        f"课程名称：{course_name}",
        f"授课班级：{class_name}",
        f"任课教师：{teacher_name}",
    ]
    if semester_name:
        user_message_parts.append(f"所属学期：{semester_name}")
    if class_student_count:
        user_message_parts.append(f"班级人数：{int(class_student_count)} 人")
    if course_credits is not None:
        user_message_parts.append(f"课程学分：{course_credits}")
    if course_description:
        user_message_parts.append(f"课程简介：{course_description.strip()[:800]}")

    user_message_parts.append(f"\n--- 教材信息 ---\n{textbook_summary}")

    if recent_materials:
        user_message_parts.append(f"\n--- 最近课堂材料 ---\n{'、'.join(recent_materials)}")
    if recent_assignments:
        user_message_parts.append(f"\n--- 最近课堂任务 ---\n{'、'.join(recent_assignments)}")

    user_message_parts.append(f"""
--- 生成要求 ---

一、system_prompt（系统提示词）要求：
这是给课堂 AI 助教看的提示词，让助教 AI 在回复学生时表现得活泼、可爱、热情且专业。
具体要求：
1. 赋予 AI 助教一个亲和力十足的角色设定，名字可以用"小X助手"之类的可爱称呼，语气自然轻松。
2. 使用简体中文回复，表达风格要生动活泼，适当使用鼓励性语言和表情符号（如"太棒了！""加油~""没问题，我来帮你~"等）。
3. 回答专业问题时必须严谨准确，不能为了活泼而牺牲专业性。
4. 面向学生时：优先讲思路、举例子、拆步骤，不直接代写作业或泄露考试答案；当学生遇到困难时先共情鼓励，再引导解决。
5. 面向教师时：帮助备课、设计活动、梳理知识点、优化教学表达，语气可以更专业但依然亲和。
6. 明确使用边界：超出课程范围的问题要温和说明边界并给出查证方向；教材/材料/大纲不一致时先指出差异。
7. 引用教材章节、知识点名称使建议可落地，让回答有根有据。
8. 学生焦虑或挫败时，先用短句共情，再给可执行的小步建议。
9. 任课教师在生成提示词时，请在提示词中体现教师姓名：{teacher_name}。
10. 提示词要详细完整，确保 AI 助教能够理解自己的角色定位、行为准则和教学目标。建议 300-600 字。

二、syllabus（课程大纲 / 知识依据）要求：
这是给助教 AI 看的知识参考，让 AI 全面了解课堂信息以便更好辅助教学。
具体要求：
1. 侧重点在课堂知识范围和核心知识点梳理上，这是最重要的部分。
2. 基于教材目录和教材简介，梳理出课程的章节结构、核心知识点和学习要点。
3. 包含课堂的基本信息：课程名称、班级、学期、教师、学生人数等。
4. 如果教材有目录信息，请按章节结构化整理出知识点概要。
5. 包含课程目标、考核方式的建议模板（供教师后续修改）。
6. 包含 AI 回答约束：哪些可以直接回答、哪些需引导回教材、哪些必须提醒教师确认。
7. 内容要全面详实，建议 500-1000 字。
""")

    user_message = "\n".join(user_message_parts)

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt_for_ai,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "thinking",
                "task_type": "deep_text_reasoning",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")

    if data.get("status") != "success":
        raise HTTPException(500, f"AI 返回异常: {data.get('detail', '未知错误')}")

    response_text = str(data.get("response_text") or "").strip()
    if not response_text:
        raise HTTPException(500, "AI 未返回有效内容")

    try:
        parsed = _parse_ai_json(response_text)
    except json.JSONDecodeError:
        raise HTTPException(500, "AI 返回的内容格式不正确，请重试")

    generated_system_prompt = str(parsed.get("system_prompt") or "").strip()
    generated_syllabus = str(parsed.get("syllabus") or "").strip()

    if not generated_system_prompt and not generated_syllabus:
        raise HTTPException(500, "AI 生成的内容为空，请重试")

    return {
        "status": "success",
        "system_prompt": generated_system_prompt,
        "syllabus": generated_syllabus,
    }


# --- 课堂列表 API (试卷分配用) ---
@router.get("/offerings/list", response_class=JSONResponse)
async def api_list_offerings(user: dict = Depends(get_current_teacher)):
    """获取当前教师的课堂列表（用于试卷分配）"""
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            """
               SELECT o.id,
                      COALESCE(s.name, o.semester) AS semester,
                      c.name AS class_name,
                      co.name AS course_name,
                      tb.title AS textbook_title
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               LEFT JOIN academic_semesters s ON s.id = o.semester_id
               LEFT JOIN textbooks tb ON tb.id = o.textbook_id
               WHERE o.teacher_id = ?
               ORDER BY COALESCE(s.start_date, o.created_at) DESC, co.name, c.name
            """,
            (user['id'],)
        )
        offerings = [dict(row) for row in cursor]
        conn.close()
        return {"status": "success", "offerings": offerings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
              AND r.teacher_id = ?
              AND c.created_by_teacher_id = ?
            """,
            (request_id, user["id"], user["id"]),
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


def _require_current_super_admin(conn, user: dict, detail: str = "只有当前超管教师可以执行该系统操作。") -> None:
    if not is_super_admin_teacher(conn, user["id"]):
        raise HTTPException(status_code=403, detail=detail)


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
              AND r.teacher_id = ?
              AND c.created_by_teacher_id = ?
            """,
            (request_id, user["id"], user["id"]),
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
              AND r.teacher_id = ?
              AND c.created_by_teacher_id = ?
            """,
            (request_id, user["id"], user["id"]),
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
