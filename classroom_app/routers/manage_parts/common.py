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

from ...config import ROSTER_DIR, TEXTBOOK_ATTACHMENT_DIR, TEXTBOOK_ATTACHMENT_LEGACY_DIRS
from ...core import ai_client
from ...database import get_db_connection
from ...db.connection import execute_insert_returning_id
from ...dependencies import get_current_teacher, invalidate_session_for_user
from ...services.academic_service import (
    build_classroom_ai_context,
    build_textbook_prompt_context,
    compute_semester_week_count,
    infer_semester_name,
    parse_date_input,
    parse_json_list_field,
    serialize_textbook_row,
)
from ...services.course_planning_service import (
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
from ...services.file_handler import save_upload_file
from ...services.file_service import save_file_globally
from ...services.department_service import infer_department_from_text, normalize_department
from ...services.organization_scope_service import (
    apply_teacher_scope_to_org,
    is_same_department,
    load_teacher_org_scope,
)
from ...services.resource_access_service import (
    teacher_can_manage_class,
    teacher_can_manage_class_offering,
    teacher_can_manage_course,
    teacher_can_manage_semester,
    teacher_can_manage_student,
    teacher_can_manage_textbook,
    teacher_can_use_class,
    teacher_can_use_course,
    teacher_can_use_semester,
    teacher_can_use_textbook,
)
from ...services.organization_management_service import (
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
from ...services.learning_progress_service import normalize_course_sect_name
from ...services.materials_service import (
    attach_learning_material_briefs,
    get_learning_material_brief_map,
    sync_classroom_learning_material_assignments,
)
from ...services.message_center_service import (
    is_super_admin_teacher,
    mark_password_reset_request_notification_read,
)
from ...services.teacher_onboarding_service import (
    build_default_ai_config,
    build_default_course_description,
    build_teacher_onboarding_payload,
    mark_teacher_onboarding_dismissed,
)
from ...services.blog_news_crawler_service import (
    cancel_pending_blog_news_crawler_runs,
    enqueue_blog_news_crawler_run,
    load_blog_news_crawler_dashboard,
    update_blog_news_crawler_config,
)
from ...services.agent_key_service import (
    build_agent_key_dashboard,
    create_agent_api_key,
    delete_agent_api_key,
    fetch_agent_runtime_usage,
    set_active_agent_api_key,
    test_saved_agent_api_key,
)
from ...services.roster_handler import parse_excel_to_students
from ...services.student_auth_service import build_student_security_summary, list_student_login_history
from ...services.student_lifecycle_service import (
    STUDENT_STATUS_ACTIVE,
    STUDENT_STATUS_SUSPENDED,
    normalize_student_enrollment_status,
    student_enrollment_status_label,
)
from ...services.student_support_service import (
    MAX_SHARED_NOTE_LENGTH,
    normalize_shared_teacher_note,
    save_shared_student_teacher_note,
    teacher_can_access_student,
)
from ...services.submission_file_alignment import run_full_alignment
from ...services.teacher_account_service import (
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
from ...services.academic_integration_service import (
    build_saved_credential_verification_payload,
    delete_teacher_academic_credential,
    get_teacher_academic_credential,
    list_teacher_academic_credentials,
    save_verified_academic_credential,
    update_academic_credential_verification_status,
    verify_academic_credential,
)
from ...services.academic_calendar_sync_service import (
    mark_semester_calendar_sync_queued,
    prepare_current_semester_from_academic_system,
    sync_semester_calendar_background,
)
from ...services.academic_auto_sync_service import (
    build_academic_sync_capabilities,
    sync_teacher_academic_data_after_credential_verified,
    sync_teacher_dashboard_reminders,
)
from ...services.exam_reminder_service import (
    cancel_exam_email_reminder,
    get_exam_email_reminder_state,
    schedule_exam_email_reminder,
)
from ...services.academic_classroom_sync_service import (
    count_teacher_teaching_places,
    load_free_classroom_options_from_academic_system,
    load_teacher_teaching_place_dashboard,
    load_teacher_teaching_places,
    query_free_classrooms_from_academic_system,
    sync_teaching_places_from_academic_system,
)
from ...services.academic_course_sync_service import sync_current_teacher_courses_from_academic_system
from ...services.academic_exam_roster_sync_service import (
    build_exam_roster_signature_workbook,
    load_classroom_exam_roster_status,
    sync_classroom_exam_roster_from_academic_system,
)
from ...services.academic_course_exam_sync_service import (
    load_classroom_course_exam_status,
    sync_classroom_course_exams_from_academic_system,
    sync_current_teacher_course_exams_from_academic_system,
)
from ...services.academic_invigilation_sync_service import sync_current_teacher_invigilations_from_academic_system
from ...services.academic_roster_sync_service import sync_current_teacher_rosters_from_academic_system
from ...services.smart_classroom_checkin_sync_service import (
    build_smart_classroom_sync_capabilities,
    sync_teacher_smart_classroom_checkins,
    sync_teacher_smart_classroom_data_after_credential_verified,
)
from ...services.smart_classroom_integration_service import (
    build_saved_smart_classroom_verification_payload,
    delete_teacher_smart_classroom_credential,
    get_teacher_smart_classroom_credential,
    list_teacher_smart_classroom_credentials,
    save_verified_smart_classroom_credential,
    update_smart_classroom_credential_verification_status,
    verify_smart_classroom_credential,
)
from ...services.gongwen_integration_service import (
    build_saved_gongwen_verification_payload,
    delete_teacher_gongwen_credential,
    get_teacher_gongwen_credential,
    list_teacher_gongwen_credentials,
    save_verified_gongwen_credential,
    update_gongwen_credential_verification_status,
    verify_gongwen_credential,
)
from ...services.gongwen_document_sync_service import (
    build_gongwen_sync_capabilities,
    count_teacher_gongwen_documents,
    get_gongwen_document_content,
    list_teacher_gongwen_categories,
    list_teacher_gongwen_documents,
    search_gongwen_documents,
    sync_current_teacher_gongwen_documents,
    sync_teacher_gongwen_data_after_credential_verified,
)
from ...services.integration_request_probe_service import probe_integration_request
from ...storage_paths import resolve_migrated_file_path
from ...time_utils import local_iso



def _form_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}










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
    if not row or not teacher_can_use_semester(conn, teacher_id, row):
        raise HTTPException(404, "学期不存在或不属于当前教师所在学校")
    return row


def _ensure_teacher_can_manage_semester(conn, *, semester_id: int, teacher_id: int):
    row = _ensure_teacher_can_use_semester(conn, semester_id=semester_id, teacher_id=teacher_id)
    if not teacher_can_manage_semester(conn, teacher_id, row):
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
    if not teacher_can_use_course(conn, teacher_id, row):
        raise HTTPException(404, "课程不存在或不属于当前教师所在系别")
    return row


def _ensure_teacher_can_manage_course(conn, *, course_id: int, teacher_id: int):
    row = _ensure_teacher_can_use_course(conn, course_id=course_id, teacher_id=teacher_id)
    if not teacher_can_manage_course(conn, teacher_id, row):
        raise HTTPException(403, "该课程为系内共享课程，仅创建者或超管可编辑")
    return row


def _ensure_teacher_can_use_textbook(conn, *, textbook_id: int, teacher_id: int):
    row = conn.execute(
        "SELECT * FROM textbooks WHERE id = ? LIMIT 1",
        (int(textbook_id),),
    ).fetchone()
    if not row or not teacher_can_use_textbook(conn, teacher_id, row):
        raise HTTPException(404, "Textbook not found")
    return row


def _ensure_teacher_can_manage_textbook(conn, *, textbook_id: int, teacher_id: int):
    row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=teacher_id)
    if not teacher_can_manage_textbook(conn, teacher_id, row):
        raise HTTPException(403, "Only the textbook owner or a super admin can edit this textbook")
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
        WHERE o.id = ?
        LIMIT 1
        """,
        (offering_id,),
    ).fetchone()
    if not offering or not teacher_can_manage_class_offering(conn, teacher_id, offering):
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
    class_row = _ensure_teacher_can_use_class(conn, class_id=class_id, teacher_id=teacher_id)
    course_row = _ensure_teacher_can_use_course(conn, course_id=course_id, teacher_id=teacher_id)
    semester_row = _ensure_teacher_can_use_semester(conn, semester_id=semester_id, teacher_id=teacher_id)
    textbook_row = _ensure_teacher_can_use_textbook(conn, textbook_id=textbook_id, teacher_id=teacher_id)
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
        WHERE id = ?
        LIMIT 1
        """,
        (int(class_id),),
    ).fetchone()
    if not class_row or not teacher_can_manage_class(conn, teacher_id, class_row):
        raise HTTPException(status_code=404, detail="班级不存在或无权操作")
    return class_row


def _ensure_teacher_can_use_class(conn, *, class_id: int, teacher_id: int):
    class_row = conn.execute(
        """
        SELECT *
        FROM classes
        WHERE id = ?
        LIMIT 1
        """,
        (int(class_id),),
    ).fetchone()
    if not class_row or not teacher_can_use_class(conn, teacher_id, class_row):
        raise HTTPException(status_code=404, detail="Class not found or not visible")
    return class_row


def _ensure_teacher_owned_student(conn, *, student_id: int, teacher_id: int):
    student_row = conn.execute(
        """
        SELECT s.*, c.name AS class_name, c.created_by_teacher_id
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (int(student_id),),
    ).fetchone()
    if not student_row or not teacher_can_manage_student(conn, teacher_id, student_row):
        raise HTTPException(status_code=404, detail="学生不存在或无权操作")
    return student_row




# --- 班级管理 ---






























# (新增) 删除班级


# --- 课程管理 ---








# (新增) 删除课程




# --- 学期与教材管理 ---














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




# --- 班级课堂 (关联) ---






# (新增) 删除课堂


# --- 课堂 AI 配置 ---


# (新增) 获取 AI 配置 (用于前端加载)


# --- 课堂 AI 智能生成 ---


# --- 课堂列表 API (试卷分配用) ---




































def _require_current_super_admin(conn, user: dict, detail: str = "只有当前超管教师可以执行该系统操作。") -> None:
    if not is_super_admin_teacher(conn, user["id"]):
        raise HTTPException(status_code=403, detail=detail)


__all__ = [name for name in globals() if not name.startswith("__")]
