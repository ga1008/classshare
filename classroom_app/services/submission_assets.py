from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

import aiofiles
from fastapi import HTTPException, UploadFile


TEXT_FILE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".log",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".tex",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

TEXT_LIKE_MIME_PREFIXES = ("text/",)
TEXT_LIKE_MIME_TYPES = {
    "application/javascript",
    "application/json",
    "application/sql",
    "application/typescript",
    "application/xml",
    "image/svg+xml",
}
EXAM_DRAWING_PREFIX = "exam_drawings/"
EXAM_DRAWING_EXTENSIONS = {".png"}
EXAM_DRAWING_MIME_TYPES = {"image/png", "image/x-png"}


@dataclass(slots=True)
class PreparedUploadEntry:
    file: UploadFile
    relative_path: str
    content_type: str
    size_bytes: int


@dataclass(slots=True)
class StoredSubmissionFile:
    original_filename: str
    relative_path: str
    stored_path: str
    mime_type: str
    file_size: int
    file_ext: str
    file_hash: str


@dataclass(slots=True)
class SubmissionStorageResult:
    stored_files: list[StoredSubmissionFile] = field(default_factory=list)
    dropped_files: list[dict[str, Any]] = field(default_factory=list)


def encode_allowed_file_types_json(raw_value: Any) -> str | None:
    normalized = normalize_allowed_file_types(raw_value)
    return json.dumps(normalized, ensure_ascii=False) if normalized else None


def decode_allowed_file_types_json(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return normalize_allowed_file_types(raw_value)
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return normalize_allowed_file_types(stripped)
        return normalize_allowed_file_types(parsed)
    return normalize_allowed_file_types(raw_value)


def normalize_allowed_file_types(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        candidate_items = (
            raw_value.replace("\r", "\n")
            .replace(";", ",")
            .replace("，", ",")
            .replace("、", ",")
            .replace("\n", ",")
            .split(",")
        )
    elif isinstance(raw_value, (list, tuple, set)):
        candidate_items = list(raw_value)
    else:
        raise HTTPException(400, "Unsupported allowed file types payload")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidate_items:
        token = str(item or "").strip().lower()
        if not token:
            continue
        if token in {"*", "*/*", "all", "all files", "any"}:
            return []
        if "/" in token:
            normalized_token = token
        else:
            normalized_token = token if token.startswith(".") else f".{token.lstrip('.')}"
        if normalized_token in seen:
            continue
        seen.add(normalized_token)
        normalized.append(normalized_token)
    return normalized


def summarize_allowed_file_types(allowed_file_types: Sequence[str]) -> str:
    normalized = normalize_allowed_file_types(list(allowed_file_types))
    return ", ".join(normalized) if normalized else "all"


def normalize_submission_relative_path(raw_path: str, fallback_name: str = "upload.bin") -> str:
    normalized = (raw_path or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        normalized = fallback_name

    parts: list[str] = []
    for part in PurePosixPath(normalized).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise HTTPException(400, "Upload path cannot contain parent traversal")
        cleaned = _sanitize_path_segment(part)
        if cleaned:
            parts.append(cleaned)

    if not parts:
        parts = [_sanitize_path_segment(fallback_name) or "upload.bin"]
    return "/".join(parts)


def parse_submission_manifest(files: Sequence[UploadFile], manifest: str = "") -> list[PreparedUploadEntry]:
    if not files:
        return []

    try:
        manifest_items = json.loads(manifest) if manifest else []
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Upload manifest is invalid JSON") from exc

    if manifest_items and not isinstance(manifest_items, list):
        raise HTTPException(400, "Upload manifest must be a list")

    prepared_entries: list[PreparedUploadEntry] = []
    seen_paths: set[str] = set()

    for index, file in enumerate(files):
        manifest_item = manifest_items[index] if index < len(manifest_items) and isinstance(manifest_items[index], dict) else {}
        fallback_name = file.filename or f"file-{index + 1}"
        relative_path = normalize_submission_relative_path(
            manifest_item.get("relative_path") or fallback_name,
            fallback_name=fallback_name,
        )
        unique_relative_path = _deduplicate_relative_path(relative_path, seen_paths)
        content_type = str(
            manifest_item.get("content_type")
            or file.content_type
            or mimetypes.guess_type(unique_relative_path)[0]
            or "application/octet-stream"
        ).lower()
        size_bytes = measure_upload_file(file)
        prepared_entries.append(
            PreparedUploadEntry(
                file=file,
                relative_path=unique_relative_path,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )

    return prepared_entries


def measure_upload_file(file: UploadFile) -> int:
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    return int(file_size)


def answers_have_content(payload: Any) -> bool:
    if payload is None:
        return False
    if isinstance(payload, str):
        return bool(payload.strip())
    if isinstance(payload, dict):
        return any(answers_have_content(value) for value in payload.values())
    if isinstance(payload, list):
        return any(answers_have_content(item) for item in payload)
    return bool(str(payload).strip())


def is_allowed_submission_file(relative_path: str, content_type: str | None, allowed_file_types: Sequence[str]) -> bool:
    if is_exam_drawing_file(relative_path, content_type):
        return True

    normalized_types = normalize_allowed_file_types(list(allowed_file_types))
    if not normalized_types:
        return True

    normalized_path = str(relative_path or "").strip().lower()
    normalized_content_type = str(content_type or mimetypes.guess_type(normalized_path)[0] or "").lower()
    for token in normalized_types:
        if "/" in token:
            if token.endswith("/*"):
                prefix = token[:-1]
                if normalized_content_type.startswith(prefix):
                    return True
            elif normalized_content_type == token:
                return True
            continue
        if normalized_path.endswith(token):
            return True
    return False


def is_exam_drawing_file(relative_path: str, content_type: str | None = None) -> bool:
    normalized_path = str(relative_path or "").replace("\\", "/").strip().lower()
    if not normalized_path.startswith(EXAM_DRAWING_PREFIX):
        return False
    suffix = Path(normalized_path).suffix.lower()
    normalized_content_type = str(content_type or mimetypes.guess_type(normalized_path)[0] or "").lower()
    return suffix in EXAM_DRAWING_EXTENSIONS and (
        not normalized_content_type
        or normalized_content_type in EXAM_DRAWING_MIME_TYPES
        or normalized_content_type.startswith("image/")
    )


def is_text_like_file(relative_path: str, content_type: str | None = None) -> bool:
    normalized_path = str(relative_path or "").lower()
    suffix = Path(normalized_path).suffix.lower()
    if suffix in TEXT_FILE_EXTENSIONS:
        return True
    normalized_content_type = str(content_type or mimetypes.guess_type(normalized_path)[0] or "").lower()
    if normalized_content_type in TEXT_LIKE_MIME_TYPES:
        return True
    return any(normalized_content_type.startswith(prefix) for prefix in TEXT_LIKE_MIME_PREFIXES)


async def store_submission_files(
    submission_dir: Path,
    prepared_entries: Sequence[PreparedUploadEntry],
    allowed_file_types: Sequence[str],
) -> SubmissionStorageResult:
    result = SubmissionStorageResult()
    if not prepared_entries:
        return result

    submission_dir.mkdir(parents=True, exist_ok=True)
    normalized_allowed_types = normalize_allowed_file_types(list(allowed_file_types))

    for entry in prepared_entries:
        if not is_allowed_submission_file(entry.relative_path, entry.content_type, normalized_allowed_types):
            await entry.file.close()
            result.dropped_files.append(
                {
                    "relative_path": entry.relative_path,
                    "reason": "type_not_allowed",
                    "content_type": entry.content_type,
                }
            )
            continue

        target_path = _build_storage_path(submission_dir, entry.relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        hasher = hashlib.sha256()
        written_size = 0
        try:
            async with aiofiles.open(target_path, "wb") as output_file:
                while chunk := await entry.file.read(1024 * 1024):
                    written_size += len(chunk)
                    hasher.update(chunk)
                    await output_file.write(chunk)
        except Exception:
            if target_path.exists():
                target_path.unlink()
            raise
        finally:
            await entry.file.close()

        result.stored_files.append(
            StoredSubmissionFile(
                original_filename=Path(entry.relative_path).name,
                relative_path=entry.relative_path,
                stored_path=str(target_path),
                mime_type=entry.content_type,
                file_size=written_size,
                file_ext=Path(entry.relative_path).suffix.lower(),
                file_hash=hasher.hexdigest(),
            )
        )

    return result


def delete_storage_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _sanitize_path_segment(segment: str) -> str:
    value = str(segment or "").strip()
    if not value:
        return ""
    cleaned = "".join("_" if ch in {"/", "\\", "\x00"} else ch for ch in value)
    if cleaned in {".", ".."}:
        raise HTTPException(400, "Upload path segment is invalid")
    return cleaned


def _deduplicate_relative_path(relative_path: str, seen_paths: set[str]) -> str:
    normalized_key = relative_path.lower()
    if normalized_key not in seen_paths:
        seen_paths.add(normalized_key)
        return relative_path

    path_obj = PurePosixPath(relative_path)
    parent = "" if str(path_obj.parent) == "." else str(path_obj.parent)
    suffix = "".join(path_obj.suffixes)
    if suffix:
        stem = path_obj.name[: -len(suffix)]
    else:
        stem = path_obj.name

    for index in range(2, 10000):
        candidate_name = f"{stem} ({index}){suffix}"
        candidate_path = f"{parent}/{candidate_name}" if parent else candidate_name
        candidate_key = candidate_path.lower()
        if candidate_key in seen_paths:
            continue
        seen_paths.add(candidate_key)
        return candidate_path

    raise HTTPException(400, "Too many duplicated upload paths")


def _build_storage_path(root_dir: Path, relative_path: str) -> Path:
    return root_dir.joinpath(*PurePosixPath(relative_path).parts)
