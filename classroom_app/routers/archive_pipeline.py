"""成绩与归档域首页：九步流水线总览（docs/manage-center-improvement-plan-2026-09-11.md §5.6）。

每一步的数量复用材料检索的分类检索器（material_hub_service），不新写 SQL；
单步失败只记录不拖垮整页（检索器本身已按分类独立降级）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import require_teacher_domain
from ..services.manage_nav_service import ARCHIVE_STEP_TOTAL, iter_archive_steps
from ..services.material_hub_service import search_material_hub
from .ui_parts.common import _build_manage_template_context

router = APIRouter()

# 步 key → 材料检索分类 key（两边名字略有差异）。
_STEP_CATEGORY = {
    "assessment_plans": "assessment_plans",
    "grading_rubrics": "grading_rubrics",
    "ordinary_grade_records": "ordinary_grade_records",
    "exam_grade_records": "exam_grade_records",
    "final_grade_transcripts": "final_grade_transcripts",
    "academic_grade_registers": "academic_grade_registers",
    "academic_exam_analyses": "academic_exam_analyses",
    "teacher_evaluations": "teacher_evaluations",
    "postclass_materials": "postclass",
}


def build_archive_pipeline(conn, user: dict) -> list[dict]:
    counts: dict[str, int] = {}
    try:
        result = search_material_hub(conn, user, query="", categories=list(_STEP_CATEGORY.values()), scope_filter="private")
        counts = {str(k): int(v or 0) for k, v in (result.get("counts") or {}).items()}
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[ARCHIVE_PIPELINE] hub counts failed: {exc}")
    pipeline = []
    for item in iter_archive_steps():
        count = counts.get(_STEP_CATEGORY.get(item.key, item.key), 0)
        pipeline.append({
            "key": item.key,
            "step": item.step,
            "label": item.label,
            "group": item.group,
            "href": item.href,
            "hint": item.nav_note or "",
            "count": count,
            "state": "done" if count else "todo",
        })
    return pipeline


@router.get("/manage/archive", response_class=HTMLResponse)
async def manage_archive_pipeline_page(request: Request, user: dict = Depends(require_teacher_domain("archive"))):
    with get_db_connection() as conn:
        pipeline = build_archive_pipeline(conn, user)
    done_steps = sum(1 for entry in pipeline if entry["count"])
    return templates.TemplateResponse(
        request,
        "manage/archive_pipeline.html",
        _build_manage_template_context(
            request,
            user,
            page_title="成绩与归档",
            active_page="archive_pipeline",
            extra={"pipeline": pipeline, "step_total": ARCHIVE_STEP_TOTAL, "done_steps": done_steps},
        ),
    )
