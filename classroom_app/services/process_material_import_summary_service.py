"""Compact import-source summaries for teacher process-material cards."""

from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_list(value: Any, *, limit: int = 10) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text:
            result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _warnings_from_error(error: Any) -> list[str]:
    text = _text(error)
    if not text:
        return []
    return [
        part.strip()[:240]
        for part in re.split(r"[；;\n]+", text)
        if part and part.strip()
    ][:10]


def _merge_warnings(*groups: list[str], limit: int = 10) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            text = _text(item)[:240]
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= limit:
                return result
    return result


def _source_file_label(source_files: list[str]) -> str:
    if not source_files:
        return ""
    first = source_files[0]
    if len(source_files) == 1:
        return first
    return f"{first} 等 {len(source_files)} 个文件"


def _source_file_title(source_files: list[str]) -> str:
    if not source_files:
        return ""
    return "、".join(source_files)


def _failed_action_label(source_type: str, row: dict[str, Any]) -> str:
    if source_type == "classroom" and row.get("class_offering_id"):
        return "一键重试"
    if source_type == "import":
        return "重新上传文件"
    return "请处理失败记录"


def _process_verb(source_type: str) -> str:
    return "解析" if source_type == "import" else "生成"


def _busy_quality_label(source_type: str) -> str:
    return f"{_process_verb(source_type)}中"


def _failed_quality_label(source_type: str) -> str:
    return f"{_process_verb(source_type)}失败"


def _busy_action_label(source_type: str) -> str:
    return f"{_process_verb(source_type)}完成后可核对并导出"


def build_process_import_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Return the small import provenance object consumed by list cards.

    The heavy parsed payload stays in each document's own JSON columns. Cards only
    need enough to answer three teacher questions: what source produced this,
    does the result need review, and what should I do next?
    """

    preview = row.get("import_preview")
    preview = preview if isinstance(preview, dict) else {}
    source_type = _text(row.get("source_type")).lower()
    is_import = source_type == "import"

    source_files = _string_list(preview.get("source_files"), limit=8)
    warnings = _string_list(preview.get("warnings"), limit=10)
    ai_status = _text(row.get("ai_gen_status")).lower()
    status = _text(row.get("status")).lower()
    error_warnings = _warnings_from_error(row.get("ai_gen_error"))
    is_busy = status in {"parsing", "generating"} or ai_status in {"pending", "running"}
    is_failed = status == "failed" or ai_status == "failed"
    if ai_status == "completed_with_fallback" and not warnings:
        warnings = error_warnings
    if not is_import and not source_files and not warnings and not error_warnings and not is_busy and not is_failed:
        return {}

    if is_busy:
        quality_key = "in_progress"
        quality_label = _busy_quality_label(source_type)
        action_label = _busy_action_label(source_type)
    elif is_failed:
        quality_key = "failed"
        quality_label = _failed_quality_label(source_type)
        action_label = _failed_action_label(source_type, row)
        warnings = _merge_warnings(error_warnings, warnings)
    elif warnings:
        quality_key = "needs_review"
        quality_label = f"需核对 {len(warnings)} 项"
        action_label = "建议先编辑核对"
    else:
        quality_key = "ready"
        quality_label = "可预览导出"
        action_label = "可直接预览导出"

    visible_warnings = warnings[:3]
    source_heading = "导入来源" if is_import else "生成结果"
    fallback_source_label = "导入文件" if is_import else "生成结果"
    return {
        "visible": True,
        "source_heading": source_heading,
        "source_files": source_files,
        "source_file_label": _source_file_label(source_files) or fallback_source_label,
        "source_file_title": _source_file_title(source_files) or fallback_source_label,
        "warning_count": len(warnings),
        "warnings": visible_warnings,
        "all_warnings": warnings,
        "more_warning_count": max(0, len(warnings) - len(visible_warnings)),
        "quality_key": quality_key,
        "quality_label": quality_label,
        "action_label": action_label,
    }
