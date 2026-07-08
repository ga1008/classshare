from .common import *

from ...services import assessment_plan_service as ap


router = APIRouter()


def _list_teacher_offerings(conn, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, c.name AS class_name, co.name AS course_name,
               COALESCE(NULLIF(o.academic_teaching_class_name, ''), c.academic_class_name, c.name) AS display_class_name,
               COALESCE(NULLIF(sem.name, ''), NULLIF(o.semester, ''), '') AS semester_label
        FROM class_offerings o
        JOIN classes c ON o.class_id = c.id
        JOIN courses co ON o.course_id = co.id
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        WHERE o.teacher_id = ?
        ORDER BY co.name, c.name
        """,
        (int(teacher_id),),
    ).fetchall()
    return [dict(row) for row in rows]


@router.get("/manage/teaching/assessment-plans", response_class=HTMLResponse)
@router.get("/manage/assessment-plans", response_class=HTMLResponse)
async def manage_assessment_plans_page(request: Request, user: dict = Depends(get_current_teacher)):
    """考核计划表库管理页面（过程材料 → 考核计划表）。"""
    with get_db_connection() as conn:
        plans = ap.list_assessment_plans(conn, teacher=user)
        offerings = _list_teacher_offerings(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/assessment_plans.html",
        _build_manage_template_context(
            request,
            user,
            page_title="考核计划表",
            active_page="assessment_plans",
            extra={
                "assessment_plans": plans,
                "assessment_plan_offerings": offerings,
                "assessment_plan_scope_options": ap.scope_options(),
            },
        ),
    )


def _load_plan_for_viewer(conn, plan_id: str, user: dict) -> dict:
    plan = ap.get_assessment_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "考核计划表不存在")
    teacher_id = int(user["id"])
    is_owner = int(plan.get("teacher_id") or 0) == teacher_id
    is_super = is_super_admin_teacher(conn, teacher_id)
    if not is_owner and not is_super:
        viewer = ap.teacher_scope(conn, teacher_id)
        if not ap.can_view_plan(plan, viewer, is_super_admin=is_super):
            raise HTTPException(403, "无权查看该考核计划表")
    plan["is_owned"] = is_owner
    plan["can_manage"] = is_owner or is_super
    return plan


@router.get("/assessment-plan/{plan_id}/edit", response_class=HTMLResponse)
async def assessment_plan_editor_page(request: Request, plan_id: str, user: dict = Depends(get_current_teacher)):
    """考核计划表编辑器（表单 + 考核项目表 + 签名 + 实时预览）。"""
    with get_db_connection() as conn:
        plan = _load_plan_for_viewer(conn, plan_id, user)
        if not plan.get("can_manage"):
            raise HTTPException(403, "无权编辑该考核计划表，请先继承为自己的副本。")
        offerings = _list_teacher_offerings(conn, int(user["id"]))
    return templates.TemplateResponse(
        request,
        "assessment_plan_editor.html",
        {
            "request": request,
            "user_info": user,
            "plan": plan,
            "offerings": offerings,
            "scope_options": ap.scope_options(),
        },
    )


@router.get("/assessment-plan/{plan_id}/preview", response_class=HTMLResponse)
async def assessment_plan_preview_page(request: Request, plan_id: str, user: dict = Depends(get_current_teacher)):
    """考核计划表 HTML 预览（与导出 Word 同版式，可用于查看效果/截图）。"""
    with get_db_connection() as conn:
        plan = _load_plan_for_viewer(conn, plan_id, user)
        html = ap.render_preview_html(conn, plan, user=user)
    return HTMLResponse(html)
