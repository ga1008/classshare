from __future__ import annotations

import mimetypes
from pathlib import Path

import aiofiles
from fastapi import HTTPException


TEXT_CONTENT_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
)

TEXT_PREVIEW_TYPES = {"markdown", "text"}
SUPPORTED_PREVIEW_TYPES = TEXT_PREVIEW_TYPES | {"image"}

TEXTUAL_MIME_PREFIXES = ("text/",)
TEXTUAL_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "application/javascript",
    "application/x-javascript",
    "application/xml",
    "application/x-sh",
    "application/x-yaml",
    "application/yaml",
}
TEXTUAL_EXTENSIONS = {
    "bat",
    "c",
    "cc",
    "cfg",
    "conf",
    "cpp",
    "cs",
    "css",
    "csv",
    "dockerfile",
    "env",
    "gitignore",
    "go",
    "gradle",
    "h",
    "hpp",
    "htm",
    "html",
    "ini",
    "java",
    "js",
    "json",
    "jsx",
    "kt",
    "kts",
    "less",
    "log",
    "md",
    "markdown",
    "mjs",
    "php",
    "properties",
    "ps1",
    "py",
    "rb",
    "rs",
    "scss",
    "sh",
    "sql",
    "svg",
    "svelte",
    "text",
    "toml",
    "ts",
    "tsx",
    "tsv",
    "txt",
    "vue",
    "xml",
    "yaml",
    "yml",
}
TEXTUAL_BASENAME_HINTS = {
    ".dockerignore",
    ".env",
    ".gitignore",
    "cmakelists.txt",
    "dockerfile",
    "license",
    "makefile",
    "notice",
    "procfile",
    "readme",
    "requirements.txt",
}

FILE_PREVIEW_TYPE_REGISTRY = {
    "md": {
        "mime_type": "text/markdown",
        "preview_type": "markdown",
        "type_label": "Markdown",
        "ai_capability": "markdown",
    },
    "markdown": {
        "mime_type": "text/markdown",
        "preview_type": "markdown",
        "type_label": "Markdown",
        "ai_capability": "markdown",
    },
    "pdf": {
        "mime_type": "application/pdf",
        "preview_type": "pdf",
        "type_label": "PDF",
        "ai_capability": "document",
    },
    "doc": {
        "mime_type": "application/msword",
        "preview_type": "document",
        "type_label": "Word",
        "ai_capability": "document",
    },
    "docx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "preview_type": "document",
        "type_label": "Word",
        "ai_capability": "document",
    },
    "xls": {
        "mime_type": "application/vnd.ms-excel",
        "preview_type": "spreadsheet",
        "type_label": "Excel",
        "ai_capability": "spreadsheet",
    },
    "xlsx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "preview_type": "spreadsheet",
        "type_label": "Excel",
        "ai_capability": "spreadsheet",
    },
    "ppt": {
        "mime_type": "application/vnd.ms-powerpoint",
        "preview_type": "presentation",
        "type_label": "PPT",
        "ai_capability": "presentation",
    },
    "pptx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "preview_type": "presentation",
        "type_label": "PPT",
        "ai_capability": "presentation",
    },
    "txt": {
        "mime_type": "text/plain",
        "preview_type": "text",
        "type_label": "文本",
        "ai_capability": "text",
    },
    "png": {
        "mime_type": "image/png",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "jpg": {
        "mime_type": "image/jpeg",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "jpeg": {
        "mime_type": "image/jpeg",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "gif": {
        "mime_type": "image/gif",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "svg": {
        "mime_type": "image/svg+xml",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
}


def is_text_preview_type(preview_type: str | None) -> bool:
    return str(preview_type or "").lower() in TEXT_PREVIEW_TYPES


def is_preview_supported(preview_type: str | None) -> bool:
    return str(preview_type or "").lower() in SUPPORTED_PREVIEW_TYPES


def is_editable_preview_type(preview_type: str | None) -> bool:
    return str(preview_type or "").lower() in TEXT_PREVIEW_TYPES


def _is_textual_file(file_name: str, mime_type: str | None = None) -> bool:
    normalized_name = str(file_name or "").strip()
    lower_name = normalized_name.lower()
    extension = ""
    if "." in normalized_name:
        extension = normalized_name.rsplit(".", 1)[-1].lower()

    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime.startswith(TEXTUAL_MIME_PREFIXES) or normalized_mime in TEXTUAL_MIME_TYPES:
        return True
    if extension in TEXTUAL_EXTENSIONS:
        return True
    return lower_name in TEXTUAL_BASENAME_HINTS


def _infer_text_type_label(file_name: str, extension: str) -> str:
    if extension in {"md", "markdown"}:
        return "Markdown"
    if extension == "txt":
        return "文本"
    if extension:
        return extension.upper()

    normalized_name = str(file_name or "").strip()
    return normalized_name or "文本"


def infer_file_preview_profile(file_name: str, content_type: str | None = None) -> dict:
    extension = ""
    if "." in file_name:
        extension = file_name.rsplit(".", 1)[-1].lower()

    profile = FILE_PREVIEW_TYPE_REGISTRY.get(extension, {}).copy()
    guessed_mime = (
        content_type
        or profile.get("mime_type")
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )
    if not profile and _is_textual_file(file_name, guessed_mime):
        profile = {
            "mime_type": guessed_mime if guessed_mime != "application/octet-stream" else "text/plain",
            "preview_type": "text",
            "type_label": _infer_text_type_label(file_name, extension),
            "ai_capability": "text",
        }
        guessed_mime = profile["mime_type"]

    preview_type = profile.get("preview_type", "binary")
    return {
        "file_ext": extension,
        "mime_type": guessed_mime,
        "preview_type": preview_type,
        "type_label": profile.get("type_label", extension.upper() if extension else "文件"),
        "ai_capability": profile.get("ai_capability", "none"),
        "preview_supported": is_preview_supported(preview_type),
        "is_markdown": preview_type == "markdown",
        "is_text": is_text_preview_type(preview_type),
        "is_image": preview_type == "image",
        "editable": is_editable_preview_type(preview_type),
    }


def decode_text_bytes(
    raw_bytes: bytes,
    *,
    binary_error_message: str = "当前文件不是可预览的文本文件",
    encoding_error_message: str = "当前文本文件编码暂不支持在线预览",
) -> tuple[str, str]:
    if b"\x00" in raw_bytes:
        raise HTTPException(400, binary_error_message)

    for encoding in TEXT_CONTENT_ENCODINGS:
        try:
            return raw_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    raise HTTPException(400, encoding_error_message)


async def load_text_content(
    file_path: Path,
    *,
    binary_error_message: str = "当前文件不是可预览的文本文件",
    encoding_error_message: str = "当前文本文件编码暂不支持在线预览",
) -> tuple[str, str]:
    async with aiofiles.open(file_path, "rb") as handle:
        raw_bytes = await handle.read()
    return decode_text_bytes(
        raw_bytes,
        binary_error_message=binary_error_message,
        encoding_error_message=encoding_error_message,
    )
