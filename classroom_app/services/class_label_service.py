"""Shared helpers for user-facing administrative class labels.

Class offering integrations often carry both an academic/admin class name
(``软工2401班``) and a teaching-class code/name (for example ``计算机网络-0002``).
Document-generation surfaces should prefer the administrative class label and
only fall back to teaching-class text when no better signal exists.
"""

from __future__ import annotations

import re
from typing import Any


DEPARTMENT_SHORT_NAMES = {
    "软件工程": "软工",
    "网络工程": "网工",
    "计算机科学与技术": "计科",
    "计算机科学": "计科",
    "人工智能": "人工",
    "数据科学与大数据技术": "大数据",
    "大数据": "大数据",
    "数字媒体技术": "数媒",
    "数字媒体": "数媒",
    "信息管理与信息系统": "信管",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def department_short_name(*values: Any) -> str:
    compact_values = [re.sub(r"[\s（）()系部学院]+", "", _text(value)) for value in values if _text(value)]
    for compact in compact_values:
        for keyword, short in DEPARTMENT_SHORT_NAMES.items():
            if keyword in compact:
                return short
    compact = " ".join(compact_values)
    match = re.search(r"([\u4e00-\u9fff]{2,8})(?:工程|科学|技术|智能|媒体|管理)", compact)
    if match:
        return match.group(1)[:2]
    return ""


def _strip_course_prefix(text: str, course_name: str) -> str:
    compact_course = course_name.replace(" ", "")
    compact_text = text.replace(" ", "")
    if not compact_course or compact_course not in compact_text:
        return text
    pattern = r"\s*".join(re.escape(ch) for ch in course_name if not ch.isspace())
    stripped = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
    return stripped.strip(" \t\r\n·、,，-—－_：:")


def _ensure_class_suffix(label: str) -> str:
    compact = re.sub(r"\s+", "", _text(label))
    if re.fullmatch(r"[\u4e00-\u9fff]{1,8}\d{2,4}(?:[、/]\d{2,4})*", compact):
        return f"{compact}班"
    return label


def _looks_like_course_teaching_class(value: str, course_name: str) -> bool:
    text = _text(value)
    if not text:
        return True
    stripped = _strip_course_prefix(text, course_name)
    if stripped != text:
        text = stripped
    if "班" in text:
        return False
    if course_name and course_name.replace(" ", "") in text.replace(" ", ""):
        return True
    return bool(re.search(r"[-_]\d{3,}$", text))


def normalize_class_fragment(value: Any, *, department_short: str = "", course_name: str = "") -> str:
    text = _text(value)
    if not text:
        return ""
    text = _strip_course_prefix(text, course_name)
    if not text or _looks_like_course_teaching_class(text, course_name):
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(",", "、").replace("，", "、")

    for keyword, short in DEPARTMENT_SHORT_NAMES.items():
        if text.startswith(keyword):
            text = f"{short}{text[len(keyword):]}"
            department_short = department_short or short
            break

    for suffix in ("软件工程系", "网络工程系", "计算机科学与技术系", "人工智能系"):
        text = text.replace(suffix, department_short or "")

    if department_short and text.startswith(department_short):
        return _ensure_class_suffix(re.sub(rf"^{re.escape(department_short)}\s+", department_short, text))

    number_match = re.search(r"(\d{2,4}(?:\s*[、/]\s*\d{2,4})*\s*班)", text)
    if department_short and number_match:
        return f"{department_short}{number_match.group(1).replace(' ', '')}"
    numeric_only = re.fullmatch(r"(?:[\u4e00-\u9fff]{0,8})?(\d{2,4}(?:\s*[、/]\s*\d{2,4})*)", text)
    if department_short and numeric_only:
        return f"{department_short}{numeric_only.group(1).replace(' ', '')}班"
    return text


def is_upgrade_program(row: dict[str, Any], *extra_values: Any) -> bool:
    sources = [*extra_values]
    sources.extend(row.get(key) for key in ("class_name", "academic_class_name", "academic_major", "major", "description"))
    raw_meta = _text(row.get("academic_metadata_json"))
    if raw_meta:
        sources.append(raw_meta)
    return "专升本" in " ".join(_text(value) for value in sources if value is not None)


def build_academic_class_label(row: dict[str, Any]) -> str:
    """Return a compact admin-class label such as ``软工2401班（专升本）``."""
    course_name = _text(row.get("course_name"))
    department_short = department_short_name(
        row.get("class_department"),
        row.get("class_academic_major"),
        row.get("class_major"),
        row.get("course_department"),
        row.get("teacher_department"),
    )
    candidates = [
        row.get("academic_class_name"),
        row.get("class_name"),
        row.get("academic_teaching_class_name"),
    ]
    label = ""
    for candidate in candidates:
        label = normalize_class_fragment(candidate, department_short=department_short, course_name=course_name)
        if label:
            break
    if not label and department_short:
        label = department_short
    if department_short and label and not label.startswith(department_short) and re.search(r"\d{2,4}", label):
        label = f"{department_short}{label}"
    if is_upgrade_program(row, label) and "专升本" not in label:
        label = f"{label}（专升本）" if label else "（专升本）"
    return label
