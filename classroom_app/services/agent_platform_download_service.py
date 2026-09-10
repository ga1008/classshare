"""Import authorized byte snapshots into a task, without model-visible binary data."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import threading
import uuid

from fastapi import HTTPException

from ..config import AGENT_TASK_WORKSPACE_ROOT
from .agent_bridge_service import allowed_file_roots
from .agent_continuation_service import resolve_continuation_task
from .agent_delegation_service import lock_task_authority, verify_task_delegation
from .agent_platform_multipart_service import _open_confined, _relative_path
from .agent_scoped_read_service import _platform_file, assert_scoped_file_current

MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_IMPORT_ENTRIES = 128
_DOWNLOAD_CAPACITY = threading.BoundedSemaphore(2)


@contextmanager
def source_snapshot(path):
    """Keep a bounded private copy while checking the opened source identity."""
    path = Path(path).absolute()
    selected = None
    for candidate in allowed_file_roots():
        try:
            relative = path.relative_to(candidate.absolute())
            if relative.parts and '..' not in relative.parts:
                selected = candidate.absolute(), relative
                break
        except ValueError:
            continue
    if selected is None:
        raise HTTPException(403, '文件不在平台允许的数据目录。')
    descriptor = None
    try:
        descriptor = _open_confined(*selected)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise HTTPException(403, '只能下载普通文件。')
        if before.st_size > MAX_DOWNLOAD_BYTES:
            raise HTTPException(413, '单次工作区导入上限为32MiB，请按平台原下载流程处理更大文件。')
        with os.fdopen(descriptor, 'rb') as source, tempfile.TemporaryFile() as snapshot:
            descriptor = None
            digest, size = hashlib.sha256(), 0
            while chunk := source.read(min(1024 * 1024, MAX_DOWNLOAD_BYTES - size + 1)):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise HTTPException(413, '文件超过本次导入大小限制。')
                digest.update(chunk)
                snapshot.write(chunk)
            after = os.fstat(source.fileno())
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if size != before.st_size or identity(before) != identity(after):
                raise HTTPException(409, '源文件仍在变化，请待写入完成后重试。')
            snapshot.seek(0)
            yield {'stream': snapshot, 'size': size, 'sha256': digest.hexdigest()}
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(403, '无法安全读取指定平台文件。') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _directory_descriptor(root):
    # Production DSH runs on Linux. Refuse a weaker write-path fallback on
    # development hosts without openat/nofollow; reading remains cross-platform.
    if os.name != 'posix' or os.open not in os.supports_dir_fd:
        raise HTTPException(503, '当前运行主机不支持安全的工作区文件导入。')
    if os.geteuid() == 10001:
        # tools.agent_dsh_launcher pins the untrusted runner to 10001:10001.
        raise HTTPException(503, '可信文件服务不能与任务执行器共用操作系统身份。')
    root = Path(root).absolute()
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        try:
            os.mkdir('inputs', mode=0o755, dir_fd=descriptor)
        except FileExistsError:
            pass
        inputs = os.open('inputs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            # The task owns its workspace and could precreate inputs. Reclaim
            # this reserved directory before publication: the runner must not
            # swap individual names between an inode check and cleanup/chmod.
            # Renaming the directory from its parent is harmless to the held fd
            # and is caught by the final confined pathname identity check.
            os.fchown(inputs, os.geteuid(), -1)
            os.fchmod(inputs, 0o755)
            return inputs
        except BaseException:
            os.close(inputs)
            raise
    finally:
        os.close(descriptor)


def _existing_import(directory, name, snapshot):
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != snapshot['size']:
            raise HTTPException(409, '工作区导入文件已被修改，请核对后在新任务中重新导入。')
        digest, size = hashlib.sha256(), 0
        while chunk := handle.read(min(1024 * 1024, snapshot['size'] - size + 1)):
            size += len(chunk)
            if size > snapshot['size']:
                raise HTTPException(409, '工作区导入文件仍在变化，请重新核对。')
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        if (size != snapshot['size'] or digest.hexdigest() != snapshot['sha256'] or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
            raise HTTPException(409, '工作区导入文件仍在变化，请重新核对。')
    return True


def _assert_import_path(root, relative, snapshot):
    with source_snapshot(Path(root) / relative) as actual:
        if actual['sha256'] != snapshot['sha256']:
            raise HTTPException(409, '工作区文件路径发生变化，请重新核对。')


def _assert_import_inode(root, relative, identity):
    descriptor = _open_confined(Path(root).absolute(), Path(relative))
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            raise HTTPException(409, '工作区文件路径发生变化，请重新核对。')
    finally:
        os.close(descriptor)


def _remove_own_import(directory, name, identity):
    if not name or identity is None:
        return
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def publish_snapshot(root, snapshot, filename, *, authorize):
    """Prepare inaccessible bytes, then authorize immediately before disclosure.

    The trusted host owns the temporary inode (0600); the DSH UID cannot read
    it. The caller's authorization locks remain held through the final chmod.
    Once authorized bytes become readable, a later revocation cannot undo that
    download, just as it cannot recall an ordinary browser download.
    """
    safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', Path(filename).name, flags=re.UNICODE)[-120:]
    safe_name = safe_name.strip('._') or 'file.bin'
    name = snapshot['sha256'] + '-' + safe_name
    relative = PurePosixPath('inputs') / name
    directory, temporary, created_identity = None, None, None
    completed = False
    try:
        directory = _directory_descriptor(root)
        import fcntl
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(429, '本任务已有文件正在导入，请稍后重试。') from None
        if _existing_import(directory, name, snapshot):
            _assert_import_path(root, relative, snapshot)
            authorize()
            return relative.as_posix()
        count, used = 0, 0
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                used += entry.stat(follow_symlinks=False).st_size
                if count >= MAX_IMPORT_ENTRIES or used + snapshot['size'] > MAX_IMPORT_BYTES:
                    raise HTTPException(413, '本任务 inputs 目录达到128个文件或64MiB上限，请使用新任务处理更多文件。')
        if snapshot['size'] > MAX_IMPORT_BYTES:
            raise HTTPException(413, '导入文件超过工作区输入容量。')
        temporary = '.import-' + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        with os.fdopen(descriptor, 'wb') as target:
            snapshot['stream'].seek(0)
            for chunk in iter(lambda: snapshot['stream'].read(1024 * 1024), b''):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
            staged = os.fstat(target.fileno())
            identity = (staged.st_dev, staged.st_ino)
            try:
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                created_identity = identity
            except FileExistsError:
                _existing_import(directory, name, snapshot)
                _assert_import_path(root, relative, snapshot)
                authorize()
                return relative.as_posix()
            _assert_import_path(root, relative, snapshot)
            authorize()
            _assert_import_inode(root, relative, identity)
            os.fchmod(target.fileno(), 0o444)
            completed = True
        return relative.as_posix()
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(403, '无法安全创建本任务输入文件。') from None
    finally:
        if directory is not None:
            if not completed:
                _remove_own_import(directory, name, created_identity)
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            os.close(directory)


def _lock_disclosure_authority(conn, grant):
    """Order disclosure against task/account/session/grant revocation.

    Resource policy is evaluated afresh by the caller after these locks. This
    does not claim to lock every domain's resource mutation. Persistent-source
    rows precede delegation rows, matching explicit persistent revocation.
    """
    from .account_credentials_service import lock_actor_authorization_transition
    lock_task_authority(conn, int(grant.task['id']))
    lock_actor_authorization_transition(conn, role=grant.actor.role, user_id=grant.actor.id)
    persistent = grant.delegation.get('persistent_authorization_id')
    if persistent:
        conn.execute('UPDATE agent_persistent_authorizations SET status=status WHERE id=?', (persistent,))
    conn.execute('UPDATE agent_task_delegations SET status=status WHERE id=?', (grant.delegation['id'],))
    if grant.delegation.get('source_session_hash'):
        conn.execute('UPDATE user_sessions SET session_id=session_id WHERE session_user_key=?', (grant.actor.key,))


def download_scoped_file(conn, token, *, path='', parent_task_id=None, revision=None, **selectors):
    grant = verify_task_delegation(conn, token, purpose='tools', required_scope='platform:read')
    selected = [(key, value) for key, value in selectors.items() if value is not None]
    if len(selected) + bool(path.strip()) != 1 or (selected and parent_task_id is not None):
        raise HTTPException(422, '请选择一个平台文件标识，或指定允许延续的任务文件。')
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision)):
        raise HTTPException(422, '文件版本必须是64位小写SHA256。')
    if selected:
        kind, file_id = selected[0]
        source = _platform_file(conn, grant.actor, kind, file_id)
    else:
        relative = _relative_path(path)
        previous = resolve_continuation_task(conn, grant, parent_task_id)
        source = {'path': AGENT_TASK_WORKSPACE_ROOT / 'tasks' / str(previous['id']) / relative,
                  'filename': relative.name, 'hash': '', 'binding': '', 'url': ''}
    if not _DOWNLOAD_CAPACITY.acquire(blocking=False):
        raise HTTPException(429, '文件导入繁忙，请稍后重试。')
    try:
        with source_snapshot(source['path']) as snapshot:
            if (source['hash'] and source['hash'] != snapshot['sha256']) or (revision and revision != snapshot['sha256']):
                raise HTTPException(409, '源文件内容与预期版本不一致。')
            fresh = verify_task_delegation(conn, token, purpose='tools', required_scope='platform:read')
            proof = {'_source_binding': source['binding'], 'sha256': snapshot['sha256']}
            assert_scoped_file_current(conn, fresh.actor, proof, **selectors)
            if not selected:
                resolve_continuation_task(conn, fresh, parent_task_id)
            root = AGENT_TASK_WORKSPACE_ROOT / 'tasks' / str(grant.task['id'])
            def authorize():
                _lock_disclosure_authority(conn, grant)
                fresh = verify_task_delegation(conn, token, purpose='tools', required_scope='platform:read')
                assert_scoped_file_current(conn, fresh.actor,
                    {'_source_binding': source['binding'], 'sha256': snapshot['sha256']}, **selectors)
                if not selected:
                    resolve_continuation_task(conn, fresh, parent_task_id)
            imported = publish_snapshot(root, snapshot, source['filename'], authorize=authorize)
            return {'status': 'success', 'path': imported, 'size': snapshot['size'],
                    'sha256': snapshot['sha256'], 'revision': snapshot['sha256'],
                    'filename': source['filename'], 'source_url': source['url'],
                    'input_file': True, 'next_step': '文件位于当前任务工作区；如需编辑请复制到输出文件，使用已安装的文档工具处理。'}
    finally:
        _DOWNLOAD_CAPACITY.release()
