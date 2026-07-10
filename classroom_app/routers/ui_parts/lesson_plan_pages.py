from .common import *

from ...services import lesson_plan_service as lp
from ...services.academic_class_mapping_service import resolve_offering_display_class_name
from ...services.lesson_plan_recovery_service import expire_stale_lesson_plan_tasks
from ...services.lesson_plan_render_service import render_preview_html


router = APIRouter()


def _list_teacher_offerings(conn, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id, c.name AS class_name, c.academic_class_name AS academic_class_name,
               o.academic_teaching_class_name AS academic_teaching_class_name,
               co.name AS course_name, '' AS academic_course_code,
               COALESCE(NULLIF(c.academic_class_name, ''), c.name, NULLIF(o.academic_teaching_class_name, '')) AS display_class_name,
               COALESCE(NULLIF(sem.name, ''), NULLIF(o.semester, ''), '') AS semester_label,
               tb.title AS textbook_title, tb.publisher AS textbook_publisher,
               o.schedule_source AS schedule_source,
               (SELECT COUNT(*) FROM class_offering_sessions s WHERE s.class_offering_id = o.id) AS session_count
        FROM class_offerings o
        JOIN classes c ON o.class_id = c.id
        JOIN courses co ON o.course_id = co.id
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
        WHERE o.teacher_id = ?
        ORDER BY co.name, c.name
        """,
        (int(teacher_id),),
    ).fetchall()
    offerings = []
    for row in rows:
        item = dict(row)
        item["display_class_name"] = resolve_offering_display_class_name(
            conn,
            teacher_id=int(teacher_id),
            row=item,
        )
        offerings.append(item)
    return offerings


@router.get("/manage/teaching/lesson-plans", response_class=HTMLResponse)
@router.get("/manage/lesson-plans", response_class=HTMLResponse)
async def manage_lesson_plans_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教案库管理页面（内容资产 → 教案）。"""
    with get_db_connection() as conn:
        try:
            expire_stale_lesson_plan_tasks(conn, teacher_id=int(user["id"]))
            conn.commit()
        except Exception as exc:  # pragma: no cover - best-effort recovery
            print(f"[LESSON_PLAN] stale task recovery skipped: {exc}")
        plans = lp.list_lesson_plans(conn, teacher=user)
        offerings = _list_teacher_offerings(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/lesson_plans.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教案管理",
            active_page="lesson_plans",
            extra={
                "lesson_plans": plans,
                "lesson_plan_offerings": offerings,
                "lesson_plan_scope_options": lp.scope_options(),
            },
        ),
    )


def _load_plan_for_viewer(conn, plan_id: str, user: dict) -> dict:
    plan = lp.get_lesson_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "教案不存在")
    teacher_id = int(user["id"])
    is_owner = int(plan.get("teacher_id") or 0) == teacher_id
    is_super = is_super_admin_teacher(conn, teacher_id)
    if not is_owner and not is_super:
        viewer = lp.teacher_scope(conn, teacher_id)
        if not lp.can_view_plan(plan, viewer, is_super_admin=is_super):
            raise HTTPException(403, "无权查看该教案")
    plan["is_owned"] = is_owner
    plan["can_manage"] = is_owner or is_super
    return plan


@router.get("/lesson-plan/{plan_id}/edit", response_class=HTMLResponse)
async def lesson_plan_editor_page(request: Request, plan_id: str, user: dict = Depends(get_current_teacher)):
    """教案编辑器页面（封面 + 逐课次编辑 + 实时预览）。"""
    with get_db_connection() as conn:
        plan = _load_plan_for_viewer(conn, plan_id, user)
        if not plan.get("can_manage"):
            raise HTTPException(403, "无权编辑该教案，请先继承为自己的教案。")
        card = lp.serialize_card(plan)
    return templates.TemplateResponse(
        request,
        "lesson_plan_editor.html",
        {
            "request": request,
            "user_info": user,
            "plan": plan,
            "plan_card": card,
            "scope_options": lp.scope_options(),
        },
    )


@router.get("/lesson-plan/{plan_id}/preview", response_class=HTMLResponse)
async def lesson_plan_preview_page(request: Request, plan_id: str, user: dict = Depends(get_current_teacher)):
    """教案 HTML 预览（与导出 Word 同版式，可用于查看效果/截图）。"""
    with get_db_connection() as conn:
        plan = _load_plan_for_viewer(conn, plan_id, user)
    return HTMLResponse(render_preview_html(plan, user=user))
