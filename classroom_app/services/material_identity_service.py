"""过程材料的**业务身份**命名：学年学期 + 课程 + 班级。

生成出来的材料如果只叫"AI生成-考核登分表-动态web程序设计"，教师在材料库里
根本分不清是哪个班、哪个学期的——同一门课往往有多个平行教学班，跨学期还会
反复生成。这里集中提供命名与摘要，避免每个文档类型各写一套。

两种形态：

* :func:`build_final_material_package_name` —— 材料库里的**文件夹名**，
  ``AI生成-机试（作品设计）考核登分表-动态web程序设计-软工2401班-2025-2026学年第二学期``。
  缺失的段直接省略，不塞"未命名"占位，保证名字干净。
* :func:`build_final_material_export_filename` —— **导出文件名**，沿用
  ``ordinary_grade_record_service`` 已有的公文风格
  ``2025-2026-2《动态web程序设计》机试（作品设计）考核登分表-软工2401班.xlsx``；
  归档要求字段齐全，所以这里反过来用占位符补齐缺失项。

学年学期一律走 ``semester_identity_service`` 解析，别在这里重新发明正则。
"""

from __future__ import annotations

import re
from typing import Any

from .semester_identity_service import parse_semester_identity


# Windows/Excel 都拒绝的字符，落到文件名和材料名里都会出问题。
_ILLEGAL_NAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

PLACEHOLDER_PERIOD = "未设学年学期"
PLACEHOLDER_COURSE = "未命名课程"
PLACEHOLDER_CLASS = "未命名班级"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sanitize(value: str, *, limit: int = 120) -> str:
    cleaned = _ILLEGAL_NAME_CHARS.sub("-", _text(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned[:limit]


def _identity_from_fields(values: dict[str, Any]):
    """把学年与学期拼成一段文本再解析。

    `parse_semester_identity` 需要年份与学期出现在**同一个字符串**里
    （``parse_semester_identity('2025-2026', '第二学期')`` 返回 None），
    而库里这两个值是分开存的，所以必须先拼接。
    """
    year_text = _text(values.get("academic_year"))
    term_text = _text(values.get("semester"))
    period_text = _text(values.get("period"))
    candidates = [
        f"{year_text}{term_text}",
        period_text,
        f"{year_text}{period_text}",
        year_text,
        term_text,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        identity = parse_semester_identity(candidate)
        if identity is not None:
            return identity
    return None


def semester_term_number(fields: dict[str, Any] | None) -> str:
    """学期序号（``"1"`` / ``"2"``）；解析不出来返回空串。"""
    identity = _identity_from_fields(fields if isinstance(fields, dict) else {})
    if identity is None:
        return ""
    return str(getattr(identity, "term", "") or "")


def academic_year_range(fields: dict[str, Any] | None) -> str:
    """学年区间（``2025-2026``）；解析不出来返回空串。"""
    values = fields if isinstance(fields, dict) else {}
    identity = _identity_from_fields(values)
    if identity is not None:
        start = getattr(identity, "start_year", None)
        if start:
            return f"{int(start)}-{int(start) + 1}"
    for key in ("academic_year", "period", "semester"):
        raw = _text(values.get(key))
        match = re.search(r"(20\d{2})\D+(20\d{2})", raw)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        single = re.search(r"(20\d{2})", raw)
        if single:
            year = int(single.group(1))
            return f"{year}-{year + 1}"
    return ""


def period_label(fields: dict[str, Any] | None, *, style: str = "full") -> str:
    """学年学期标签。

    ``style='full'``    -> ``2025-2026学年第二学期``（给人看）
    ``style='compact'`` -> ``2025-2026-2``（给公文文件名用）

    解析不出学期序号时只返回学年部分，宁可短也不要写错。
    """
    values = fields if isinstance(fields, dict) else {}
    year = academic_year_range(values)
    term = semester_term_number(values)
    if not year:
        return ""
    if not term:
        return year if style == "compact" else f"{year}学年"
    if style == "compact":
        return f"{year}-{term}"
    return f"{year}学年第{'一' if term == '1' else '二'}学期"


def context_summary(fields: dict[str, Any] | None, *, separator: str = " · ") -> str:
    """给 UI 用的一行业务上下文：``2025-2026学年第二学期 · 动态web程序设计 · 软工2401班``。"""
    values = fields if isinstance(fields, dict) else {}
    parts = [
        period_label(values),
        _text(values.get("course_name")),
        _text(values.get("class_name")),
    ]
    return separator.join(part for part in parts if part)


def build_final_material_package_name(
    *,
    document_type_label: str,
    fields: dict[str, Any] | None,
    prefix: str = "AI生成",
) -> str:
    """材料库文件夹名：类型 + 课程 + 班级 + 学年学期，缺失段省略。"""
    values = fields if isinstance(fields, dict) else {}
    segments = [
        _text(prefix),
        _text(document_type_label) or "期末材料",
        _text(values.get("course_name")),
        _text(values.get("class_name")),
        period_label(values),
    ]
    name = "-".join(_sanitize(segment, limit=60) for segment in segments if _text(segment))
    return _sanitize(name, limit=180) or "AI生成-期末材料"


def build_final_material_export_filename(
    *,
    document_type_label: str,
    fields: dict[str, Any] | None,
    suffix: str = ".xlsx",
    sequence: int | None = None,
) -> str:
    """导出文件名（公文风格）：``[序号. ]{学年-学期}《课程》{类型}-{班级}{后缀}``。

    归档场景要求字段齐全，缺失项用占位符补上，方便教师一眼看出哪份没填。
    """
    values = fields if isinstance(fields, dict) else {}
    period = period_label(values, style="compact") or PLACEHOLDER_PERIOD
    course = _sanitize(_text(values.get("course_name"))) or PLACEHOLDER_COURSE
    class_name = _sanitize(_text(values.get("class_name"))) or PLACEHOLDER_CLASS
    label = _sanitize(_text(document_type_label)) or "过程材料"

    normalized_suffix = _text(suffix).lower() or ".xlsx"
    if not normalized_suffix.startswith("."):
        normalized_suffix = f".{normalized_suffix}"

    prefix = f"{int(sequence)}. " if sequence is not None else ""
    stem = f"{prefix}{period}《{course}》{label}-{class_name}"
    return f"{_sanitize(stem, limit=180)}{normalized_suffix}"
