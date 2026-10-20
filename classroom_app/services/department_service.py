from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


DEPARTMENT_PRESETS = [
    "网络工程系",
    "软件工程系",
    "电子信息工程系",
    "计算机科学与技术系",
    "人工智能系",
    "大数据技术系",
]

_DEPARTMENT_ALIASES = {
    "网工": "网络工程系",
    "网络工程": "网络工程系",
    "软工": "软件工程系",
    "软件工程": "软件工程系",
    "电信": "电子信息工程系",
    "电子信息": "电子信息工程系",
    "计科": "计算机科学与技术系",
    "计算机科学与技术": "计算机科学与技术系",
    "人工智能": "人工智能系",
    "大数据": "大数据技术系",
}


def normalize_department(value: Any) -> str:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    if not text:
        return ""
    # A known abbreviation remains the same abbreviation with an explicit 系
    # suffix. Do not infer initials or partial matches for unknown departments.
    alias = text.removesuffix("系")
    if alias in _DEPARTMENT_ALIASES:
        return _DEPARTMENT_ALIASES[alias]
    for preset in DEPARTMENT_PRESETS:
        if text == preset or text == preset.removesuffix("系"):
            return preset
    return text if text.endswith("系") else f"{text}系"


def department_aliases(department: Any) -> list[str]:
    canonical = normalize_department(department)
    if not canonical:
        return []
    aliases = {canonical.removesuffix("系")}
    for alias, target in _DEPARTMENT_ALIASES.items():
        if target == canonical:
            aliases.update((alias, f"{alias}系"))
    return sorted(aliases - {canonical})


def infer_department_from_text(*parts: Any) -> str:
    blob = " ".join(str(part or "") for part in parts).replace(" ", "")
    if not blob:
        return ""
    for alias, department in _DEPARTMENT_ALIASES.items():
        if alias and alias in blob:
            return department
    for preset in DEPARTMENT_PRESETS:
        if preset in blob or preset.removesuffix("系") in blob:
            return preset
    return ""


def collect_department_options(*groups: Iterable[Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            department = normalize_department(value)
            if department and department not in seen:
                seen.add(department)
                ordered.append(department)
    for preset in DEPARTMENT_PRESETS:
        if preset not in seen:
            ordered.append(preset)
            seen.add(preset)
    return ordered
