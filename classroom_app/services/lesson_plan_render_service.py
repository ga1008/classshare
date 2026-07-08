"""Preview shell for lesson-plan exports.

The preview renders page images from the same DOCX artifact used by downloads,
so browser preview and exported Word layout share one source of truth.
"""

from __future__ import annotations

from typing import Any

from .document_render_service import DocumentRenderError, document_render_service
from .lesson_plan_docx_service import build_lesson_plan_docx


def render_plan_html(plan: dict[str, Any]) -> str:
    """A standalone preview page backed by rendered final DOCX page images."""
    title_text = (plan.get("cover") or {}).get("course_name") or plan.get("title") or "教案预览"
    base_title = str(plan.get("title") or title_text or "教案").replace("/", "_").replace("\\", "_")
    try:
        docx_bytes = build_lesson_plan_docx(plan)
        job = document_render_service.render_artifact(
            docx_bytes,
            filename=f"{base_title}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            source_format="docx",
        )
    except (RuntimeError, DocumentRenderError) as exc:
        return document_render_service.render_error_html(title=title_text, message=str(exc))
    return document_render_service.render_preview_html(
        job,
        title=title_text,
        eyebrow="教案 · 导出一致预览",
        download_label="下载 Word",
    )
