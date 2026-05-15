from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Iterable, Sequence

from .submission_assets import TEXT_FILE_EXTENSIONS


VOLCENGINE_FILE_API_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".icns",
    ".ico",
    ".jpeg",
    ".jpg",
    ".jp2",
    ".png",
    ".sgi",
    ".tiff",
    ".webp",
}
VOLCENGINE_FILE_API_IMAGE_MIME_TYPES = {
    "image/bmp",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/icns",
    "image/jp2",
    "image/jpeg",
    "image/png",
    "image/sgi",
    "image/tiff",
    "image/webp",
    "image/x-icon",
}
AI_NATIVE_DOCUMENT_EXTENSIONS = {".pdf"}
AI_NATIVE_DOCUMENT_MIME_TYPES = {"application/pdf"}
AI_EXTRACTABLE_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
AI_EXTRACTABLE_DOCUMENT_MIME_TYPES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
AI_TEXT_LIKE_MIME_PREFIXES = ("text/",)
AI_TEXT_LIKE_MIME_TYPES = {
    "application/javascript",
    "application/json",
    "application/sql",
    "application/typescript",
    "application/xml",
    "image/svg+xml",
}
AI_UNSUPPORTED_ARCHIVE_EXTENSIONS = {
    ".7z",
    ".br",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
    ".zipx",
    ".zst",
}

AI_GRADING_UPLOAD_EXTENSIONS = sorted(
    VOLCENGINE_FILE_API_IMAGE_EXTENSIONS
    | AI_NATIVE_DOCUMENT_EXTENSIONS
    | AI_EXTRACTABLE_DOCUMENT_EXTENSIONS
    | TEXT_FILE_EXTENSIONS
    | AI_UNSUPPORTED_ARCHIVE_EXTENSIONS
)

AI_GRADING_SUPPORTED_TYPES_LABEL = (
    "图片（jpg/jpeg/png/gif/webp/bmp/tiff/ico/icns/sgi/jp2/heic/heif）、"
    "PDF、Word/Excel/PPT（系统先提取文本和内嵌图片）、文本/代码文件；"
    "zip/rar/7z/tar 等压缩包和无法解析的二进制文件仅传入文件属性信息"
)


def normalize_file_ext(*values: Any) -> str:
    for value in values:
        ext = str(value or "").strip().lower()
        if not ext:
            continue
        if "/" in ext or "\\" in ext:
            ext = Path(ext.replace("\\", "/")).suffix.lower()
        elif not ext.startswith(".") and "." in ext:
            ext = Path(ext).suffix.lower()
        elif ext and not ext.startswith("."):
            ext = f".{ext}"
        if ext:
            return ext
    return ""


def guess_mime_type(filename: str = "", mime_type: str | None = None) -> str:
    explicit = str(mime_type or "").strip().lower()
    if explicit and explicit != "application/octet-stream":
        return explicit
    guessed = mimetypes.guess_type(filename or "")[0]
    return str(guessed or explicit or "application/octet-stream").lower()


def classify_ai_grading_attachment(file_info: dict[str, Any]) -> dict[str, Any]:
    display_name = str(
        file_info.get("display_name")
        or file_info.get("relative_path")
        or file_info.get("original_filename")
        or file_info.get("stored_path")
        or "附件"
    )
    ext = normalize_file_ext(
        file_info.get("ext"),
        file_info.get("file_ext"),
        file_info.get("relative_path"),
        file_info.get("original_filename"),
        file_info.get("stored_path"),
    )
    mime_type = guess_mime_type(display_name, file_info.get("mime_type"))

    if ext in VOLCENGINE_FILE_API_IMAGE_EXTENSIONS or mime_type in VOLCENGINE_FILE_API_IMAGE_MIME_TYPES:
        return {
            "category": "image",
            "category_label": "图片",
            "supported": True,
            "ext": ext,
            "mime_type": mime_type,
            "display_name": display_name,
            "reason": "",
        }
    if ext in AI_NATIVE_DOCUMENT_EXTENSIONS or mime_type in AI_NATIVE_DOCUMENT_MIME_TYPES:
        return {
            "category": "pdf",
            "category_label": "PDF",
            "supported": True,
            "ext": ext or ".pdf",
            "mime_type": mime_type,
            "display_name": display_name,
            "reason": "",
        }
    if ext in AI_EXTRACTABLE_DOCUMENT_EXTENSIONS or mime_type in AI_EXTRACTABLE_DOCUMENT_MIME_TYPES:
        return {
            "category": "document",
            "category_label": "文档",
            "supported": True,
            "ext": ext,
            "mime_type": mime_type,
            "display_name": display_name,
            "reason": "系统会先提取文本和内嵌图片再提交给 AI。",
        }
    if ext in TEXT_FILE_EXTENSIONS or mime_type.startswith(AI_TEXT_LIKE_MIME_PREFIXES) or mime_type in AI_TEXT_LIKE_MIME_TYPES:
        return {
            "category": "text",
            "category_label": "文本",
            "supported": True,
            "ext": ext,
            "mime_type": mime_type,
            "display_name": display_name,
            "reason": "系统会以文本内容提交给 AI。",
        }
    if ext in AI_UNSUPPORTED_ARCHIVE_EXTENSIONS:
        reason = "压缩包不会传入文件内容；AI 批改只会收到文件名、大小和时间等属性。"
    else:
        reason = "该文件类型无法直接转换为模型可理解的图片、PDF或文本；AI 批改只会收到文件属性。"
    return {
        "category": "metadata_only",
        "category_label": "仅属性",
        "supported": True,
        "metadata_only": True,
        "ext": ext,
        "mime_type": mime_type,
        "display_name": display_name,
        "reason": reason,
    }


def find_unsupported_ai_grading_attachments(files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        profile
        for profile in (classify_ai_grading_attachment(file_info) for file_info in files)
        if not profile["supported"]
    ]


def ensure_ai_grading_attachments_supported(files: Sequence[dict[str, Any]]) -> None:
    unsupported = find_unsupported_ai_grading_attachments(files)
    if not unsupported:
        return
    names = "、".join(
        f"{item['display_name']}（{item.get('ext') or item.get('mime_type') or '未知类型'}）"
        for item in unsupported[:8]
    )
    if len(unsupported) > 8:
        names += f" 等 {len(unsupported)} 个文件"
    raise ValueError(
        f"AI 批改前检查未通过：以下附件不是当前批改链路能提交给 AI 理解的类型：{names}。"
        f"当前支持：{AI_GRADING_SUPPORTED_TYPES_LABEL}。"
    )


def build_attachment_type_summary(files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for file_info in files:
        profile = classify_ai_grading_attachment(file_info)
        label = profile["ext"] or profile["mime_type"] or "无后缀"
        key = (profile["category"], label)
        item = groups.setdefault(
            key,
            {
                "label": label,
                "count": 0,
                "category": profile["category"],
                "category_label": profile["category_label"],
                "supported": profile["supported"],
                "reason": profile["reason"],
            },
        )
        item["count"] += 1
        if not profile["supported"]:
            item["supported"] = False
            item["reason"] = profile["reason"]
    return sorted(
        groups.values(),
        key=lambda item: (
            0 if not item["supported"] else 1,
            str(item["category"]),
            str(item["label"]),
        ),
    )
