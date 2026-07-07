"""Teacher-facing pages for the 教师评学表 (过程材料 → 教师评学表).

List page + editor + HTML preview, mirroring the assessment-plan pages. The offering
list is grouped by semester so the create/generate modals can default to the current
学年学期's taught classes and still let the teacher pick an earlier semester.
"""

from .common import *

from ...services.class_label_service import build_academic_class_label
from ...services import teacher_evaluation_service as te


router = APIRouter()


def _list_teacher_offerings(conn, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT o.id,
               c.name AS class_name,
               c.academic_class_name AS academic_class_name,
               c.academic_major AS class_academic_major,
               c.major AS class_major,
               c.department AS class_department,
               c.description AS description,
               c.academic_metadata_json AS academic_metadata_json,
               co.name AS course_name,
               co.department AS course_department,
               o.academic_teaching_class_name AS academic_teaching_class_name,
               t.department AS teacher_department,
               COALESCE(NULLIF(sem.name, ''), NULLIF(o.semester, ''), '') AS semester_label,
               sem.start_date AS semester_start_date
        FROM class_offerings o
        JOIN classes c ON o.class_id = c.id
        JOIN courses co ON o.course_id = co.id
        LEFT JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        WHERE o.teacher_id = ?
        ORDER BY sem.start_date DESC, co.name, c.name
        """,
        (int(teacher_id),),
    ).fetchall()
    offerings = []
    for row in rows:
        item = dict(row)
        item["display_class_name"] = (
            build_academic_class_label(item)
            or item.get("academic_class_name")
            or item.get("class_name")
            or item.get("academic_teaching_class_name")
            or ""
        )
        offerings.append(item)
    return offerings


@router.get("/manage/teaching/teacher-evaluations", response_class=HTMLResponse)
@router.get("/manage/teacher-evaluations", response_class=HTMLResponse)
async def manage_teacher_evaluations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师评学表库管理页面（过程材料 → 教师评学表）。"""
    with get_db_connection() as conn:
        evaluations = te.list_evaluations(conn, teacher=user)
        offerings = _list_teacher_offerings(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/teacher_evaluations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教师评学表",
            active_page="teacher_evaluations",
            extra={
                "teacher_evaluations": evaluations,
                "teacher_evaluation_offerings": offerings,
                "teacher_evaluation_scope_options": te.scope_options(),
            },
        ),
    )


def _load_evaluation_for_viewer(conn, evaluation_id: str, user: dict) -> dict:
    evaluation = te.get_evaluation(conn, evaluation_id)
    if not evaluation:
        raise HTTPException(404, "教师评学表不存在")
    teacher_id = int(user["id"])
    is_owner = int(evaluation.get("teacher_id") or 0) == teacher_id
    is_super = is_super_admin_teacher(conn, teacher_id)
    if not is_owner and not is_super:
        viewer = te.teacher_scope(conn, teacher_id)
        if not te.can_view_evaluation(evaluation, viewer, is_super_admin=is_super):
            raise HTTPException(403, "无权查看该教师评学表")
    evaluation["is_owned"] = is_owner
    evaluation["can_manage"] = is_owner or is_super
    return evaluation


@router.get("/teacher-evaluation/{evaluation_id}/edit", response_class=HTMLResponse)
async def teacher_evaluation_editor_page(request: Request, evaluation_id: str, user: dict = Depends(get_current_teacher)):
    """教师评学表编辑器（基础信息 + 10 项评价打分 + 综合评价 + 学习情况分析 + 实时预览）。"""
    with get_db_connection() as conn:
        evaluation = _load_evaluation_for_viewer(conn, evaluation_id, user)
        if not evaluation.get("can_manage"):
            raise HTTPException(403, "无权编辑该教师评学表，请先继承为自己的副本。")
        offerings = _list_teacher_offerings(conn, int(user["id"]))
    return templates.TemplateResponse(
        request,
        "teacher_evaluation_editor.html",
        {
            "request": request,
            "user_info": user,
            "evaluation": evaluation,
            "offerings": offerings,
            "scope_options": te.scope_options(),
        },
    )


@router.get("/teacher-evaluation/{evaluation_id}/preview", response_class=HTMLResponse)
async def teacher_evaluation_preview_page(request: Request, evaluation_id: str, user: dict = Depends(get_current_teacher)):
    """教师评学表 HTML 预览（与导出 Word 同版式，可用于查看效果/截图）。"""
    with get_db_connection() as conn:
        evaluation = _load_evaluation_for_viewer(conn, evaluation_id, user)
        html = te.render_preview_html(evaluation)
    return HTMLResponse(html)
