"""Private image originals for account-owned assistant history, never static files."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import uuid

from fastapi import HTTPException
from PIL import Image

from ..config import DATA_DIR

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_USER_BYTES = 100 * 1024 * 1024
MAX_USER_FILES = 1024
IMAGE_FORMATS = {
    'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'),
    'WEBP': ('webp', 'image/webp'), 'GIF': ('gif', 'image/gif'),
    'BMP': ('bmp', 'image/bmp'), 'TIFF': ('tif', 'image/tiff'),
}
OPAQUE_NAME = re.compile(r'[0-9a-f]{64}\.(?:jpg|png|webp|gif|bmp|tif)\Z')
MIME_BY_EXT = {ext: mime for ext, mime in IMAGE_FORMATS.values()}


def _paths(user, session_uuid):
    role, pk = str(user.get('role') or ''), int(user.get('id') or 0)
    try:
        if role not in {'teacher', 'student'} or pk <= 0 or str(uuid.UUID(session_uuid)) != session_uuid:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, '图片不存在') from None
    base = Path(DATA_DIR).resolve()
    path = base
    for part in ('private', 'ai_workspace', f'{role}-{pk}', session_uuid):
        path = path / part
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise HTTPException(404, '图片不存在')
        if not path.resolve().is_relative_to(base):
            raise HTTPException(404, '图片不存在')
    return path.parent, path


def resolve_image(user, session_uuid, opaque_name):
    """Caller must check owned_session before resolving the opaque file name."""
    if not OPAQUE_NAME.fullmatch(str(opaque_name or '')):
        raise HTTPException(404, '图片不存在')
    _, folder = _paths(user, session_uuid)
    target = folder / opaque_name
    if target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()) or not target.is_file():
        raise HTTPException(404, '图片不存在')
    return target, MIME_BY_EXT[target.suffix[1:]]


def _usage(folder):
    """Bounded scan of this actor's private quota only; no startup/global scan."""
    total, count = 0, 0
    if not folder.exists():
        return total, count
    with os.scandir(folder) as sessions:
        for directory_index, session in enumerate(sessions):
            if directory_index >= MAX_USER_FILES:
                raise HTTPException(413, 'AI 图片存储已达到上限，当前消息尚未发送。请在对话记录中删除不再需要的对话以释放空间，或移除本次图片。')
            session_path = Path(session.path)
            if (session.is_symlink() or (hasattr(session_path, 'is_junction') and session_path.is_junction())
                    or not session_path.resolve().is_relative_to(folder.resolve()) or not session.is_dir(follow_symlinks=False)):
                raise HTTPException(503, 'AI 图片存储暂不可用，请稍后重试。')
            with os.scandir(session.path) as images:
                for entry in images:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        raise HTTPException(503, 'AI 图片存储暂不可用，请稍后重试。')
                    count += 1
                    total += entry.stat(follow_symlinks=False).st_size
                    if count > MAX_USER_FILES or total > MAX_USER_BYTES:
                        raise HTTPException(413, 'AI 图片存储已达到 100 MiB 上限，当前消息尚未发送。请在对话记录中删除不再需要的对话以释放空间，或移除本次图片。')
    return total, count


def remove_created(paths):
    # Exact files created by this transaction; never recursively remove a folder.
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # An orphan remains quota-accounted; do not hide the original failure.


def referenced_session_images(user, session_uuid, attachments):
    """Resolve only exact private URLs recorded by this session; never glob files."""
    _, folder = _paths(user, session_uuid)
    prefix = f'/api/ai/workspace/attachments/{session_uuid}/'
    paths = set()
    for item in attachments:
        if not isinstance(item, dict) or item.get('type') != 'image':
            continue
        url = str(item.get('previewUrl') or '')
        if not url.startswith(prefix):
            continue
        name = url[len(prefix):]
        if not OPAQUE_NAME.fullmatch(name):
            continue
        path = folder / name
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise HTTPException(503, '图片存储状态异常，对话尚未删除，请稍后重试。')
        if path.exists() and not path.is_file():
            raise HTTPException(503, '图片存储状态异常，对话尚未删除，请稍后重试。')
        paths.add(path)
    return paths


def delete_session_images(paths):
    # The caller retains DB history on failure, so an interrupted exact-file
    # cleanup can be retried without losing references to the remaining files.
    try:
        for path in paths:
            path.unlink(missing_ok=True)
    except OSError:
        raise HTTPException(503, '部分图片未能清理，对话记录已保留，请重试删除。') from None


def remove_empty_session_folder(user, session_uuid):
    _, folder = _paths(user, session_uuid)
    try:
        folder.rmdir()
    except OSError:
        pass  # Unknown files and the owner's other sessions are never removed.


def save_request_images(user, session_uuid, request_id, attachments, images):
    """Run under begin_request's account row lock, after replay/owner checks.

    Returns enriched metadata and newly created paths to remove on DB rollback.
    Previously saved retry files are reused and never part of rollback cleanup.
    """
    metadata = [dict(item) for item in attachments]
    if not images:
        return metadata, []
    if len(images) > 8:
        raise HTTPException(413, '每条消息最多保存 8 张图片。')
    owner_folder, folder = _paths(user, session_uuid)
    planned = {}
    seen = set()
    for image in images:
        index, contents = image['attachment_index'], image['contents']
        if index in seen or not 0 <= index < len(metadata) or metadata[index].get('type') != 'image':
            raise HTTPException(400, '图片附件信息无效。')
        seen.add(index)
        if not contents or len(contents) > MAX_IMAGE_BYTES:
            raise HTTPException(413, '单张图片不能超过 10 MiB，当前消息尚未发送。')
        try:
            with Image.open(io.BytesIO(contents)) as source:
                ext, _ = IMAGE_FORMATS[source.format]
                source.verify()
        except Exception:
            raise HTTPException(400, '图片附件无法读取，请重新上传。') from None
        digest = hashlib.sha256(f'{session_uuid}\0{request_id}\0{index}\0'.encode() + contents).hexdigest()
        name = f'{digest}.{ext}'
        target = folder / name
        if target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()):
            raise HTTPException(404, '图片不存在')
        planned[target] = contents
        metadata[index]['previewUrl'] = f'/api/ai/workspace/attachments/{session_uuid}/{name}'
    used, count = _usage(owner_folder)
    new = {path: contents for path, contents in planned.items() if not path.exists()}
    if used + sum(len(contents) for contents in new.values()) > MAX_USER_BYTES or count + len(new) > MAX_USER_FILES:
        raise HTTPException(413, 'AI 图片存储已达到 100 MiB 上限，当前消息尚未发送。请在对话记录中删除不再需要的对话以释放空间，或移除本次图片。')
    created = []
    try:
        # These private subdirectories are not mounted by StaticFiles.
        base = Path(DATA_DIR).resolve()
        for part in folder.relative_to(base).parts:
            base = base / part
            base.mkdir(mode=0o700, exist_ok=True)
        for target, contents in new.items():
            temporary = folder / f'.{uuid.uuid4().hex}.tmp'
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(contents)
                os.replace(temporary, target)
                created.append(target)
            finally:
                temporary.unlink(missing_ok=True)
        return metadata, created
    except Exception:
        remove_created(created)
        raise
