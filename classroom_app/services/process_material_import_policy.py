from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException


PROCESS_DOCUMENT_IMPORT_ALLOWED_EXTENSIONS: tuple[str, ...] = (
    ".doc",
    ".docx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".md",
    ".txt",
)
PROCESS_DOCUMENT_IMPORT_ACCEPT = ",".join(PROCESS_DOCUMENT_IMPORT_ALLOWED_EXTENSIONS)
PROCESS_DOCUMENT_IMPORT_FORMAT_LABEL = "Word/PDF、Markdown/TXT 或常见图片"
PROCESS_DOCUMENT_IMPORT_MAX_FILES = 8
PROCESS_DOCUMENT_IMPORT_MAX_BYTES = 30 * 1024 * 1024


def validate_process_document_import_file_count(files: Any) -> int:
    count = len(files or [])
    if count <= 0:
        raise HTTPException(400, "请至少选择一个文件")
    if count > PROCESS_DOCUMENT_IMPORT_MAX_FILES:
        raise HTTPException(400, f"最多一次导入 {PROCESS_DOCUMENT_IMPORT_MAX_FILES} 个文件")
    return count


def normalize_process_import_filename(filename: Any, *, fallback: str = "file") -> str:
    normalized = os.path.basename(str(filename or "").strip())
    return normalized or fallback


def validate_process_document_import_filename(filename: Any, *, document_label: str = "过程材料") -> None:
    name = normalize_process_import_filename(filename)
    ext = os.path.splitext(name)[1].lower()
    if ext in PROCESS_DOCUMENT_IMPORT_ALLOWED_EXTENSIONS:
        return
    ext_label = ext or "无扩展名"
    raise HTTPException(
        415,
        f"{document_label}导入暂不支持 {ext_label} 文件，请上传{PROCESS_DOCUMENT_IMPORT_FORMAT_LABEL}。",
    )


def validate_process_document_import_file_bytes(data: bytes | bytearray, *, filename: Any) -> None:
    name = normalize_process_import_filename(filename)
    size = len(data or b"")
    if size <= 0:
        raise HTTPException(400, f"《{name}》是空文件，请重新选择。")
    if size > PROCESS_DOCUMENT_IMPORT_MAX_BYTES:
        raise HTTPException(400, f"《{name}》超过 30MB 单文件上限，请压缩或拆分后再导入。")
