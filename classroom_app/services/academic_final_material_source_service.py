"""Resolve archived academic originals for export without persisting file contents.

The parsed report is useful for editing and validation, but it cannot represent
the source's native table, typography or chart.  Export receives the verified
original separately; JSON metadata can never supply a trusted local path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .file_service import resolve_global_file_path


ACADEMIC_NATIVE_SOURCE_KEY = "_academic_native_source"
_EXAM_ANALYSIS_TYPE = "academic_exam_analysis"
_MAX_SOURCE_BYTES = 32 * 1024 * 1024


class NativeAcademicSourceError(RuntimeError):
    """The archived original cannot safely be used for a faithful export."""


@dataclass(frozen=True)
class NativeAcademicSource:
    content: bytes
    sha256: str
    source_format: str = "rtf"


def strip_academic_native_source(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop runtime-only values from JSON before displaying, saving or hydrating."""
    cleaned = dict(payload)
    cleaned.pop(ACADEMIC_NATIVE_SOURCE_KEY, None)
    export_payload = cleaned.get("export_payload")
    if isinstance(export_payload, dict):
        cleaned["export_payload"] = dict(export_payload)
        cleaned["export_payload"].pop(ACADEMIC_NATIVE_SOURCE_KEY, None)
    return cleaned


def _record_value(record: Any, key: str, default: Any = "") -> Any:
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def _source_hash(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate and not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise NativeAcademicSourceError("教务原件的文件标识无效，请重新同步试卷分析表后导出。")
    return candidate


def hydrate_academic_final_material_source(
    conn: Any, record: Any, payload: dict[str, Any]
) -> dict[str, Any]:
    """Attach one bounded, hash-verified RTF original to an authorized export.

    Call only after normal record/material access checks.  The record's archived
    hash remains usable if a source-material row was removed; if both hashes are
    present they must agree.  Missing originals never select a substitute form.
    """
    hydrated = strip_academic_native_source(payload)
    if str(_record_value(record, "document_type") or "") != _EXAM_ANALYSIS_TYPE:
        return hydrated

    archived_hash = _source_hash(_record_value(record, "source_file_hash"))
    material_hash = ""
    source_id = _record_value(record, "source_material_id", None)
    if source_id:
        material = conn.execute(
            "SELECT file_hash FROM course_materials WHERE id = ?",
            (int(source_id),),
        ).fetchone()
        if material is not None:
            material_hash = _source_hash(_record_value(material, "file_hash"))
    if archived_hash and material_hash and archived_hash != material_hash:
        raise NativeAcademicSourceError("教务原件与归档记录不一致，请重新同步试卷分析表后导出。")
    expected_hash = archived_hash or material_hash
    if not expected_hash:
        raise NativeAcademicSourceError("此试卷分析表缺少教务原件，请重新同步后导出，以保留原版格式。")

    source_path = resolve_global_file_path(expected_hash)
    if source_path is None:
        raise NativeAcademicSourceError("此试卷分析表的教务原件已不可用，请重新同步后导出，以保留原版格式。")
    try:
        with source_path.open("rb") as source_file:
            content = source_file.read(_MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise NativeAcademicSourceError("无法读取此试卷分析表的教务原件，请重新同步后导出。") from exc
    if len(content) > _MAX_SOURCE_BYTES:
        raise NativeAcademicSourceError("此试卷分析表的教务原件过大，无法安全导出，请检查原件后重新同步。")
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != expected_hash:
        raise NativeAcademicSourceError("此试卷分析表的教务原件校验失败，请重新同步后导出，以免使用损坏文件。")
    if not content.lstrip().startswith(b"{\\rtf"):
        raise NativeAcademicSourceError("此试卷分析表的教务原件格式不受支持，请重新从教务同步后导出。")

    hydrated[ACADEMIC_NATIVE_SOURCE_KEY] = NativeAcademicSource(
        content=content, sha256=actual_hash
    )
    return hydrated
