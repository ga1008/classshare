from __future__ import annotations

from fastapi import HTTPException

from ..config import (
    CLASSROOM_DOWNLOAD_LIMIT_ACTIVE,
    CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES,
    CLASSROOM_DOWNLOAD_MAX_SIZE_LABEL,
)


def format_download_size(size_bytes: int | None) -> str:
    normalized_size = int(size_bytes or 0)
    if normalized_size <= 0:
        return "0 B"

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(normalized_size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1

    precision = 0 if value >= 100 or unit_index == 0 else 2
    return f"{value:.{precision}f} {units[unit_index]}"


def build_download_policy(file_size_bytes: int | None, *, resource_label: str = "文件") -> dict:
    normalized_size = max(int(file_size_bytes or 0), 0)
    limit_enabled = bool(CLASSROOM_DOWNLOAD_LIMIT_ACTIVE and CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES)
    limit_bytes = int(CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES or 0)
    allowed = not limit_enabled or normalized_size <= limit_bytes
    blocked_reason = ""

    if not allowed:
        blocked_reason = (
            f"已限制下载：{resource_label}大小为 {format_download_size(normalized_size)}，"
            f"超过当前上限 {CLASSROOM_DOWNLOAD_MAX_SIZE_LABEL}。"
        )

    return {
        "download_limit_enabled": limit_enabled,
        "download_limit_bytes": limit_bytes if limit_enabled else None,
        "download_limit_label": CLASSROOM_DOWNLOAD_MAX_SIZE_LABEL if limit_enabled else "",
        "download_allowed": allowed,
        "download_blocked_reason": blocked_reason,
    }


def apply_download_policy(item: dict, file_size_key: str = "file_size", *, resource_label: str = "文件") -> dict:
    item.update(build_download_policy(item.get(file_size_key), resource_label=resource_label))
    return item


def ensure_download_allowed(file_size_bytes: int | None, *, resource_label: str = "文件") -> dict:
    policy = build_download_policy(file_size_bytes, resource_label=resource_label)
    if policy["download_allowed"]:
        return policy
    raise HTTPException(status_code=403, detail=policy["download_blocked_reason"])
