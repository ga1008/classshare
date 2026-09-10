"""Bounded form uploads from an actor's own task files, never arbitrary paths."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import stat
from urllib.parse import urlencode

from fastapi import HTTPException

from ..config import AGENT_TASK_WORKSPACE_ROOT, MAX_SUBMISSION_PER_FILE_BYTES
from .agent_continuation_service import resolve_continuation_task

MAX_FILES = 16
MAX_FORM_FILE_BYTES = 32 * 1024 * 1024
RESERVED_NAMES = {"bridge.md", "config.toml", "credentials.json", "docker.env"}


def _relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 1000 or "\\" in value:
        raise HTTPException(400, "附件必须使用任务内相对路径。")
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in {"", ".", ".."} or part.startswith(".") for part in value.split("/"))
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or c in ':"' for c in value)
            or path.name.casefold() in RESERVED_NAMES):
        raise HTTPException(403, "运行配置、凭据或越界路径不能作为附件。")
    return path


def _open_confined(root: Path, relative: PurePosixPath):
    """Open without reading, then prove confinement before any bytes leave disk."""
    root = root.absolute()
    target = root.joinpath(*relative.parts)
    if os.open in os.supports_dir_fd:
        descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in (*root.parts[1:], *relative.parts[:-1]):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            # A runner can create a FIFO; reject it after a nonblocking open.
            return os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        finally:
            os.close(descriptor)
    if os.name == "nt":
        # Windows development: validate the final kernel handle, not a path
        # checked before open. No file contents are read until this succeeds.
        import ctypes
        import msvcrt

        descriptor = os.open(target, os.O_RDONLY | os.O_BINARY)
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
            function.restype = ctypes.c_uint32
            length = function(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
            if not length or length >= len(buffer):
                raise OSError("Cannot verify upload handle")
            actual = buffer.value.removeprefix("\\\\?\\")
            if actual.casefold() != str(target).casefold():
                raise HTTPException(403, "附件路径发生重定向。")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    raise HTTPException(503, "当前主机不支持安全的任务附件读取。")


def _read_snapshot(root, relative, *, remaining):
    descriptor = None
    try:
        descriptor = _open_confined(root, relative)
        before = os.fstat(descriptor)
        bound = min(MAX_SUBMISSION_PER_FILE_BYTES, remaining)
        if not stat.S_ISREG(before.st_mode):
            raise HTTPException(403, "附件必须是普通文件。")
        if before.st_size > bound:
            raise HTTPException(413, "附件超出本次提交大小限制，可分批保存到作业草稿后统一提交。")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(bound + 1)
            after = os.fstat(handle.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise HTTPException(409, "附件仍在变化，请待文件写入完成后重试。")
        return data
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(403, "无法安全读取指定任务附件。") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def form_values(values):
    def scalar(value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if type(value) is bool:
            return "true" if value else "false"
        return "" if value is None else str(value)
    return [(key, scalar(value)) for key, value in sorted(values.items())]


def encode_form_upload(conn, grant, operation, normalized, file_refs):
    if file_refs is None:
        file_refs = []
    if (not isinstance(file_refs, list) or len(file_refs) > MAX_FILES
            or (file_refs and not operation.allows_files)):
        raise HTTPException(400, "该能力不支持这些附件；每次最多提交16个文件。")
    values = form_values(normalized["body"])
    if not file_refs:
        return urlencode(values).encode(), "application/x-www-form-urlencoded", normalized
    files, references, names = [], [], set()
    remaining = MAX_FORM_FILE_BYTES
    for ref in file_refs:
        if not isinstance(ref, dict) or set(ref) - {"path", "filename", "parent_task_id", "sha256"} or "path" not in ref:
            raise HTTPException(400, "附件引用格式无效。")
        relative = _relative_path(ref["path"])
        task = resolve_continuation_task(conn, grant, ref.get("parent_task_id"))
        name = str(_relative_path(ref.get("filename", relative.name)))
        if len(name) > 240 or name in names:
            raise HTTPException(400, "附件名称过长或重复。")
        names.add(name)
        root = AGENT_TASK_WORKSPACE_ROOT / "tasks" / str(task["id"])
        data = _read_snapshot(root, relative, remaining=remaining)
        digest = hashlib.sha256(data).hexdigest()
        if "sha256" in ref and ref["sha256"] != digest:
            raise HTTPException(409, "附件内容版本已变化。")
        remaining -= len(data)
        files.append((name, data))
        references.append({"path": relative.as_posix(), "filename": name, "task_id": int(task["id"]),
                           "sha256": digest, "size": len(data)})
    # Stable wire bytes are essential: replaying an operation must not get a
    # fresh random multipart boundary and fail its durable request hash check.
    fingerprint = json.dumps([values, [(item["filename"], item["sha256"]) for item in references]],
                             ensure_ascii=False, separators=(",", ":")).encode()
    boundary = "lanshare-" + hashlib.sha256(fingerprint).hexdigest()
    marker = boundary.encode()
    if any(marker in data for _, data in files) or any(boundary in value for _, value in values):
        raise HTTPException(400, "附件内容与表单分隔标识冲突。")
    parts = []
    for key, value in values:
        parts.append(b"--" + marker + b'\r\nContent-Disposition: form-data; name="' + key.encode() + b'"\r\n\r\n' + value.encode() + b"\r\n")
    for name, data in files:
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        parts.append(b"--" + marker + b'\r\nContent-Disposition: form-data; name="files"; filename="' + name.encode() +
                     b'"\r\nContent-Type: ' + content_type.encode() + b"\r\n\r\n" + data + b"\r\n")
    parts.append(b"--" + marker + b"--\r\n")
    return b"".join(parts), "multipart/form-data; boundary=" + boundary, {**normalized, "files": references}


def assignment_draft_response(payload):
    return (isinstance(payload, dict) and type(payload.get("exists")) is bool
            and type(payload.get("server_version")) is int and isinstance(payload.get("files"), list))
