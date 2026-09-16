"""管理中心 · 平台管理域页面路由（由 manage_pages.py 按域拆出，函数体未改）。"""
from .common import *
from datetime import timedelta
from ...db.connection import get_configured_db_engine
from ...dependencies import require_teacher_domain
from ...services.ai_usage_budget_service import build_ai_usage_dashboard
from ...services.offering_hub_service import build_offering_hub_context
from ...services.profile_service import build_profile_page_context
from ...services.feedback_conversation_service import list_feedback
from .manage_pages_shared import _table_has_column, _password_reset_login_summary_sql, _academic_event_label


router = APIRouter()


@router.get("/manage/system", response_class=HTMLResponse)
async def get_manage_system_redirect(request: Request, user: dict = Depends(get_current_teacher)):
    """重定向旧的系统管理页面到当前教师可访问的系统页。"""
    with get_db_connection() as conn:
        if is_super_admin_teacher(conn, user["id"]):
            return RedirectResponse(url="/manage/system/users", status_code=302)
    return RedirectResponse(url=canonical_manage_href("system_password_resets"), status_code=302)


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

        feedback_page = {"items": [], "has_more": False, "next_before_id": None}
        feedback_status = request.query_params.get("status", "all")
        if feedback_status not in {"open", "closed", "all"}:
            feedback_status = "all"
        if current_teacher_is_super_admin:
            feedback_page = list_feedback(conn, user, admin=True, status=feedback_status, limit=40)

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
                "feedback_page": feedback_page,
                "feedback_status": feedback_status,
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


@router.get("/manage/system/monitor", response_class=HTMLResponse)
async def get_manage_system_monitor_page(request: Request, user: dict = Depends(get_current_teacher)):
    """在线服务器监控大屏：资源、进程树、访问压力与 AI 解读。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
    return templates.TemplateResponse(
        request,
        "manage/system/monitor.html",
        _build_manage_template_context(
            request,
            user,
            page_title="监控大屏",
            active_page="system_monitor",
        ),
    )


@router.get("/manage/system/ai-usage", response_class=HTMLResponse)
async def get_manage_system_ai_usage_page(request: Request, user: dict = Depends(get_current_teacher)):
    """AI usage and budget dashboard for super-admins."""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        dashboard = build_ai_usage_dashboard(conn)

    return templates.TemplateResponse(
        request,
        "manage/system/ai_usage.html",
        _build_manage_template_context(
            request,
            user,
            page_title="AI 用量",
            active_page="system_ai_usage",
            extra={
                "ai_usage_dashboard": dashboard,
            },
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
