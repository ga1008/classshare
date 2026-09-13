"""管理中心 · 我的域页面路由（由 manage_pages.py 按域拆出，函数体未改）。"""
from .common import *
from datetime import timedelta
from ...db.connection import get_configured_db_engine
from ...dependencies import require_teacher_domain
from ...services.ai_usage_budget_service import build_ai_usage_dashboard
from ...services.offering_hub_service import build_offering_hub_context
from ...services.profile_service import build_profile_page_context
from .manage_pages_shared import _table_has_column, _password_reset_login_summary_sql, _academic_event_label


router = APIRouter()


@router.get("/manage/me/credentials", response_class=HTMLResponse)
async def manage_me_credentials_page(request: Request, user: dict = Depends(require_teacher_domain("me"))):
    teacher_id = int(user["id"])
    with get_db_connection() as conn:
        credential_groups = [
            {
                "key": "academic",
                "label": "教务系统",
                "description": "课表、考试、监考和名册同步使用的个人账号。",
                "items": list_teacher_academic_credentials(conn, teacher_id),
                "href": canonical_manage_href("system_academic_integrations"),
            },
            {
                "key": "smart",
                "label": "智慧课堂",
                "description": "点名、签到和课堂考勤同步使用的个人账号。",
                "items": list_teacher_smart_classroom_credentials(conn, teacher_id),
                "href": canonical_manage_href("system_smart_classroom_integrations"),
            },
            {
                "key": "gongwen",
                "label": "校园公文通",
                "description": "公文同步和关注命中使用的个人统一认证账号。",
                "items": list_teacher_gongwen_credentials(conn, teacher_id),
                "href": canonical_manage_href("system_gongwen_integrations"),
            },
        ]

    return templates.TemplateResponse(
        request,
        "manage/me_credentials.html",
        _build_manage_template_context(
            request,
            user,
            page_title="我的对接凭据",
            active_page="teacher_credentials",
            extra={
                "credential_groups": credential_groups,
            },
        ),
    )


@router.get("/manage/me/signature-workflows", response_class=HTMLResponse)
async def get_manage_signature_workflows_page(request: Request, user: dict = Depends(get_current_teacher)):
    return templates.TemplateResponse(request, "manage/signature_workflows.html",
        _build_manage_template_context(request, user, page_title="签名审批与使用", active_page="signature_workflows"))


@router.get("/manage/me/signatures", response_class=HTMLResponse)
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


@router.get("/manage/me/password-resets", response_class=HTMLResponse)
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
            WHERE ? = 1
               OR teacher_id = ?
               OR class_id IN (
                  SELECT id FROM classes WHERE created_by_teacher_id = ?
               )
               OR class_id IN (
                  SELECT class_id FROM class_offerings WHERE teacher_id = ?
               )
            """,
            (
                1 if is_super_admin_teacher(conn, user["id"]) else 0,
                user["id"],
                user["id"],
                user["id"],
            ),
        ).fetchone()

        login_summary = conn.execute(
            _password_reset_login_summary_sql(),
            (1 if is_super_admin_teacher(conn, user["id"]) else 0, user["id"], user["id"]),
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
            WHERE ? = 1
               OR r.teacher_id = ?
               OR c.created_by_teacher_id = ?
               OR EXISTS (
                    SELECT 1 FROM class_offerings o
                    WHERE o.class_id = r.class_id
                      AND o.teacher_id = ?
               )
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
            (
                1 if is_super_admin_teacher(conn, user["id"]) else 0,
                user["id"],
                user["id"],
                user["id"],
            ),
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
