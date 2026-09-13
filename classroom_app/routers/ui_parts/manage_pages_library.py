"""管理中心 · 资源库域页面路由（由 manage_pages.py 按域拆出，函数体未改）。"""
from .common import *
from datetime import timedelta
from ...db.connection import get_configured_db_engine
from ...dependencies import require_teacher_domain
from ...services.ai_usage_budget_service import build_ai_usage_dashboard
from ...services.offering_hub_service import build_offering_hub_context
from ...services.profile_service import build_profile_page_context
from .manage_pages_shared import _table_has_column, _password_reset_login_summary_sql, _academic_event_label


router = APIRouter()


@router.get("/manage/library/courses", response_class=HTMLResponse)
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
        semester_calendar = build_semester_calendar_payload(semesters)

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
                "semester_calendar": semester_calendar,
                "academic_sync_semesters": build_academic_sync_semester_options(
                    semester_calendar.get("semesters") or []
                ),
                "academic_sync_default_semester_id": semester_calendar.get("default_semester_id"),
                "department_options": collect_department_options(
                    (item.get("department") for item in my_courses),
                ),
            },
        ),
    )


@router.get("/manage/library/textbooks", response_class=HTMLResponse)
async def get_manage_textbooks_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])
        textbooks = [
            serialize_textbook_row(row)
            for row in _load_teacher_textbook_rows(conn, int(user["id"]))
        ]
        for item in textbooks:
            item["is_owned"] = int(item.get("teacher_id") or 0) == int(user["id"])
            item["can_manage"] = item["is_owned"] or current_teacher_is_super_admin
            item["owner_teacher_name"] = str(item.get("owner_teacher_name") or "").strip()

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
