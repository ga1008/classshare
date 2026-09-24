"""管理中心 · 教务域页面路由（由 manage_pages.py 按域拆出，函数体未改）。"""
from .common import *
from datetime import timedelta
from ...db.connection import get_configured_db_engine
from ...dependencies import require_teacher_domain
from ...services.ai_usage_budget_service import build_ai_usage_dashboard
from ...services.offering_hub_service import build_offering_hub_context
from ...services.profile_service import build_profile_page_context
from .manage_pages_shared import _table_has_column, _password_reset_login_summary_sql, _academic_event_label


router = APIRouter()


@router.get("/manage/academic", response_class=HTMLResponse)
async def manage_academic_overview_page(request: Request, week: str = "", user: dict = Depends(require_teacher_domain("academic"))):
    """教务域首页：我的教务日程（周视图 + 同步状态卡）。"""
    from ...services.academic_home_service import build_academic_home

    with get_db_connection() as conn:
        academic_home = build_academic_home(conn, user, week_anchor=week)

    return templates.TemplateResponse(
        request,
        "manage/academic_overview.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教务日程",
            active_page="academic_overview",
            extra={"academic_home": academic_home},
        ),
    )


@router.get("/manage/academic/classrooms", response_class=HTMLResponse)
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


@router.get("/manage/academic/integrations", response_class=HTMLResponse)
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


@router.get("/manage/academic/smart-classroom", response_class=HTMLResponse)
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


@router.get("/manage/academic/course-schedule", response_class=HTMLResponse)
async def get_manage_course_schedule_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师课时统计页面：同步智慧课堂课程表，按周 3D 展示并归集课时。"""
    from ...services.smart_classroom_schedule_sync_service import (
        build_teacher_course_schedule_overview,
    )

    with get_db_connection() as conn:
        overview = build_teacher_course_schedule_overview(conn, int(user["id"]))
        smart_credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))
        conn.commit()

    return templates.TemplateResponse(
        request,
        "manage/course_schedule.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课时统计",
            active_page="course_schedule",
            extra={
                "course_schedule_overview": overview,
                "has_smart_credential": bool(smart_credentials),
            },
        ),
    )


@router.get("/manage/academic/course-schedule/editor", response_class=HTMLResponse)
async def get_manage_course_schedule_editor_page(
    request: Request, year: str = "", term: str = "", user: dict = Depends(get_current_teacher),
):
    """课表编辑模式：整学期周列表 + 拖拽调课 + 课次属性 + 保存到教务草稿。"""
    from ...services.schedule_editor_service import build_editor_payload
    from ...services.smart_classroom_schedule_sync_service import (
        build_teacher_course_schedule_overview,
    )

    with get_db_connection() as conn:
        overview = build_teacher_course_schedule_overview(
            conn, int(user["id"]), year=str(year or "").strip(), term=str(term or "").strip(),
        )
        editor = build_editor_payload(conn, int(user["id"]), overview)
        conn.commit()

    return templates.TemplateResponse(
        request,
        "manage/course_schedule_editor.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课表编辑模式",
            active_page="course_schedule",
            extra={"schedule_editor_boot": editor},
        ),
    )


@router.get("/manage/academic/gongwen-sync", response_class=HTMLResponse)
async def get_manage_system_gongwen_integrations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师个人校园公文通账号与公文同步管理页面。"""
    profiles = list_gongwen_system_profiles()
    with get_db_connection() as conn:
        credentials = list_teacher_gongwen_credentials(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/system/gongwen_integrations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="校园公文通对接",
            active_page="system_gongwen_integrations",
            extra={
                "gongwen_profiles": profiles,
                "gongwen_credentials": credentials,
            },
        ),
    )


@router.get("/manage/academic/gongwen", response_class=HTMLResponse)
async def get_manage_gongwen_page(request: Request, user: dict = Depends(get_current_teacher)):
    """公文材料列表页（基础资源）。"""
    with get_db_connection() as conn:
        scope = load_teacher_org_scope(conn, int(user["id"]))
        is_admin = is_super_admin_teacher(conn, int(user["id"]))
        listing = list_visible_gongwen_documents(conn, scope, is_super_admin=is_admin, limit=20)
        summary = count_visible_gongwen_documents(conn, scope, is_super_admin=is_admin)
        facets = build_gongwen_facets(conn, scope, is_super_admin=is_admin)
        try:
            from ...services.gongwen_follow_service import count_follow_hits

            follow_stats = count_follow_hits(conn, int(user["id"]))
        except Exception:  # noqa: BLE001 — 关注统计异常不阻塞列表页
            follow_stats = {"total": 0, "unseen": 0}

    return templates.TemplateResponse(
        request,
        "manage/gongwen.html",
        _build_manage_template_context(
            request,
            user,
            page_title="公文材料",
            active_page="gongwen",
            extra={
                "gongwen_documents": listing["documents"],
                "gongwen_total": listing["total"],
                "gongwen_summary": summary,
                "gongwen_categories": facets["categories"],
                "gongwen_facets": facets,
                "gongwen_follow_stats": follow_stats,
            },
        ),
    )
