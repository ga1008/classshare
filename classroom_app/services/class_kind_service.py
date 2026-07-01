from __future__ import annotations

from typing import Any


CLASS_KIND_ADMINISTRATIVE = "administrative"
CLASS_KIND_CUSTOM = "custom"

CLASS_KIND_LABELS = {
    CLASS_KIND_ADMINISTRATIVE: "行政班",
    CLASS_KIND_CUSTOM: "自定义班级",
}

_SUPPORTED_CLASS_KINDS = set(CLASS_KIND_LABELS)


def normalize_class_kind(value: Any, *, default: str = CLASS_KIND_ADMINISTRATIVE) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _SUPPORTED_CLASS_KINDS:
        return normalized
    return default if default in _SUPPORTED_CLASS_KINDS else CLASS_KIND_ADMINISTRATIVE


def class_kind_label(value: Any) -> str:
    return CLASS_KIND_LABELS.get(normalize_class_kind(value), CLASS_KIND_LABELS[CLASS_KIND_ADMINISTRATIVE])


def is_custom_class_kind(value: Any) -> bool:
    return normalize_class_kind(value) == CLASS_KIND_CUSTOM
