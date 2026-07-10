"""Preview shell for lesson-plan exports.

The preview renders page images from the same DOCX artifact used by downloads,
so browser preview and exported Word layout share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .document_render_service import DocumentRenderError, document_render_service
from .lesson_plan_docx_service import build_lesson_plan_docx, convert_docx_to_pdf, convert_docx_to_png


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"
PNG_MEDIA_TYPE = "image/png"
SUPPORTED_EXPORT_FORMATS = {"docx", "pdf", "png"}


@dataclass(frozen=True)
class LessonPlanExportArtifact:
    content: bytes
    filename: str
    media_type: str
    source_format: str


def _safe_export_filename(value: Any, fallback: str = "教案") -> str:
    raw = str(value or "").strip() or fallback
    raw = raw.replace("/", "_").replace("\\", "_").replace("\x00", "")
    return raw[:120] or fallback


def export_plan_artifact(plan: dict[str, Any], *, requested_format: str = "docx") -> LessonPlanExportArtifact:
    """Build the final lesson-plan export artifact from the canonical DOCX."""
    normalized_format = str(requested_format or "docx").strip().lower().lstrip(".")
    if normalized_format not in SUPPORTED_EXPORT_FORMATS:
        raise ValueError("教案当前支持导出 Word(.docx)、PDF 和 PNG")

    base_title = _safe_export_filename(plan.get("title") or (plan.get("cover") or {}).get("course_name"), "教案")
    docx_bytes = build_lesson_plan_docx(plan)
    if normalized_format == "docx":
        return LessonPlanExportArtifact(
            content=docx_bytes,
            filename=f"{base_title}.docx",
            media_type=DOCX_MEDIA_TYPE,
            source_format="docx",
        )
    if normalized_format == "pdf":
        return LessonPlanExportArtifact(
            content=convert_docx_to_pdf(docx_bytes, base_name=base_title),
            filename=f"{base_title}.pdf",
            media_type=PDF_MEDIA_TYPE,
            source_format="pdf",
        )
    return LessonPlanExportArtifact(
        content=convert_docx_to_png(docx_bytes, base_name=base_title),
        filename=f"{base_title}.png",
        media_type=PNG_MEDIA_TYPE,
        source_format="png",
    )


def render_preview_html(plan: dict[str, Any], *, user: dict[str, Any]) -> str:
    """A standalone preview page backed by rendered final DOCX page images."""
    title_text = (plan.get("cover") or {}).get("course_name") or plan.get("title") or "教案预览"
    try:
        artifact = export_plan_artifact(plan, requested_format="docx")
        job = document_render_service.render_artifact(
            artifact.content,
            filename=artifact.filename,
            media_type=artifact.media_type,
            source_format=artifact.source_format,
        )
    except (RuntimeError, DocumentRenderError) as exc:
        return document_render_service.render_error_html(title=title_text, message=str(exc))
    return document_render_service.render_preview_html(
        job,
        title=title_text,
        user=user,
        eyebrow="教案 · 导出一致预览",
        download_label="下载 Word",
    )


def render_plan_html(plan: dict[str, Any], *, user: dict[str, Any]) -> str:
    return render_preview_html(plan, user=user)
