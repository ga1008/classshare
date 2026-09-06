import asyncio
import errno
import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import aiofiles
from fastapi import HTTPException, UploadFile

from ..config import GLOBAL_FILES_DIR, GLOBAL_FILES_LEGACY_DIRS, FILE_CHUNK_SIZE, CHUNK_UPLOAD_TIMEOUT_HOURS
from ..storage_paths import unique_paths


file_locks: Dict[str, asyncio.Lock] = {}

HASH_CHUNK_SIZE = 1024 * 1024
WRITE_CHUNK_SIZE = 1024 * 1024


def _normalize_file_hash(file_hash: str) -> str:
    return str(file_hash or "").strip().lower()


def _valid_file_hash(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def global_file_candidates(file_hash: str) -> tuple[Path, ...]:
    normalized_hash = _normalize_file_hash(file_hash)
    if not _valid_file_hash(normalized_hash):
        return ()
    candidates: list[Path] = []
    for root in unique_paths((GLOBAL_FILES_DIR, *GLOBAL_FILES_LEGACY_DIRS)):
        candidates.append(_build_sharded_path(root, normalized_hash))
        candidates.append(root / normalized_hash)
    return unique_paths(candidates)


def resolve_global_file_path(file_hash: str) -> Path | None:
    for candidate in global_file_candidates(file_hash):
        if candidate.is_file():
            return candidate
    return None


def global_file_write_path(file_hash: str) -> Path:
    normalized = _normalize_file_hash(file_hash)
    if not _valid_file_hash(normalized):
        raise ValueError("文件标识无效，请重新上传。")
    return _build_sharded_path(GLOBAL_FILES_DIR, normalized)


def store_file_object_globally(file_object) -> dict:
    """Store a bounded, verified seekable stream without exposing a partial hash file."""
    file_object.seek(0)
    digest, size = hashlib.sha256(), 0
    while chunk := file_object.read(HASH_CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
    file_hash = digest.hexdigest()
    existing = resolve_global_file_path(file_hash)
    if existing:
        return {"hash": file_hash, "size": size, "path": str(existing)}
    target = global_file_write_path(file_hash)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-", delete=False) as output:
            temporary_name = output.name
            file_object.seek(0)
            shutil.copyfileobj(file_object, output, WRITE_CHUNK_SIZE)
        # Immutable, content-addressed publication: a competing complete upload
        # may already have won. Linking never replaces a file being downloaded
        # (which is denied by Windows mandatory sharing locks).
        try:
            os.link(temporary_name, target)
        except FileExistsError:
            pass
        except OSError as exc:
            if exc.errno not in {errno.ENOSYS, errno.ENOTSUP, errno.EXDEV} and getattr(exc, "winerror", None) not in {1, 50}:
                raise
            # Some mounted filesystems do not support hard links. Same-directory
            # replacement still publishes only the completed temporary file.
            try:
                os.replace(temporary_name, target)
            except PermissionError:
                if not target.is_file() or target.stat().st_size != size:
                    raise
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"hash": file_hash, "size": size, "path": str(target)}


def _build_sharded_path(root: Path, file_hash: str) -> Path:
    if len(file_hash) >= 4:
        return root / file_hash[:2] / file_hash[2:4] / file_hash
    return root / file_hash


async def calculate_file_hash(file: UploadFile) -> str:
    sha256_hash = hashlib.sha256()
    while chunk := await file.read(HASH_CHUNK_SIZE):
        sha256_hash.update(chunk)
    await file.seek(0)
    return sha256_hash.hexdigest()


async def save_file_globally(file: UploadFile) -> Optional[Dict]:
    """Publish complete bytes atomically before a caller binds their DB reference."""
    try:
        return await asyncio.to_thread(store_file_object_globally, file.file)
    except Exception as e:
        print(f"[ERROR] save global file failed: {e}")
        return None


def cleanup_stale_uploads():
    from ..database import get_db_connection

    cutoff = (datetime.now() - timedelta(hours=CHUNK_UPLOAD_TIMEOUT_HOURS)).isoformat()
    try:
        with get_db_connection() as conn:
            stale = conn.execute(
                "SELECT upload_id, temp_dir FROM chunked_uploads WHERE status = 'uploading' AND created_at < ?",
                (cutoff,),
            ).fetchall()
            for row in stale:
                try:
                    shutil.rmtree(row["temp_dir"], ignore_errors=True)
                except Exception:
                    pass
                conn.execute(
                    "UPDATE chunked_uploads SET status = 'expired' WHERE upload_id = ?",
                    (row["upload_id"],),
                )
            conn.commit()
            if stale:
                print(f"[CLEANUP] expired chunked uploads: {len(stale)}")
    except Exception as e:
        print(f"[ERROR] cleanup stale uploads failed: {e}")


def lock_global_file_references(conn, hashes, *, require_exists: bool = True) -> tuple[str, ...]:
    """Serialize blob binding and collection until the caller's transaction ends.

    Upload bytes before opening the transaction, then call this immediately
    before adding a reference. If collection won that race, reject the binding
    so the upload can be retried without admitting a dangling reference.
    """
    from ..db.connection import get_configured_db_engine

    normalized = tuple(sorted({_normalize_file_hash(value) for value in hashes if value}))
    if any(not _valid_file_hash(value) for value in normalized):
        raise ValueError("文件标识无效，请重新上传。")
    if get_configured_db_engine() == "postgres":
        for value in normalized:
            lock_key = int.from_bytes(hashlib.sha256(("lanshare:blob:" + value).encode("ascii")).digest()[:8], "big", signed=True)
            conn.execute("SELECT pg_advisory_xact_lock(?)", (lock_key,))
    elif normalized and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if require_exists and any(resolve_global_file_path(value) is None for value in normalized):
        raise ValueError("文件已不可用，请重新上传后保存。")
    return normalized


def bind_global_file_references(conn, hashes) -> tuple[str, ...]:
    """HTTP command boundary: bind a complete, sorted batch or report a conflict.

    A transaction that binds several blobs must supply its entire hash set in
    one call before its first reference write. Callers keep the same connection
    until commit; never acquire these locks in per-upload or per-row loops.
    """
    try:
        return lock_global_file_references(conn, hashes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def count_global_file_references(conn, file_hash: str) -> int:
    """Count live references, including immutable résumé/投递 snapshots.

    Deletions are infrequent, so consult the schema instead of caching a
    pre-migration table list in a long-lived worker. Missing optional tables
    must not abort a PostgreSQL transaction during a rolling upgrade.
    """
    from ..db.connection import get_configured_db_engine

    file_hash = _normalize_file_hash(file_hash)
    if not _valid_file_hash(file_hash):
        raise ValueError("文件标识无效，请重新上传。")
    snapshots = (
        ("resume_versions", "snapshot_json", True),
        ("resume_candidates", "payload_json", True),
        ("resume_applications", "resume_snapshot_json", True),
    )
    if get_configured_db_engine() == "postgres":
        available = {(str(row["table_name"]), str(row["column_name"])) for row in conn.execute(
            f"SELECT table_name,column_name FROM information_schema.columns "
            f"WHERE table_schema=current_schema()",
        ).fetchall()}
    else:
        existing = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        available = {(table, str(row[1])) for table in existing
                     for row in conn.execute('PRAGMA table_info("' + table.replace('"', '""') + '")').fetchall()}
    # The global store is also used by avatars, classroom/private attachments,
    # emoji, blog assets, feedback and collaboration. Discover every declared
    # file-hash column so new structured references cannot silently be omitted.
    references = [(table, column, False) for table, column in sorted(available)
                  if column == "file_hash" or column.endswith("_file_hash")]
    references.extend(snapshots)
    queries, params = [], []
    for table, column, snapshot in references:
        if (table, column) not in available:
            continue
        # A hash embedded in a snapshot remains a reference even after the
        # original profile item was deleted. False positives retain a blob;
        # they cannot make a valid student artifact disappear.
        quoted_table, quoted_column = table.replace('"', '""'), column.replace('"', '""')
        queries.append(f'SELECT COUNT(*) AS n FROM "{quoted_table}" WHERE "{quoted_column}" {"LIKE" if snapshot else "="} ?')
        params.append(f"%{file_hash}%" if snapshot else file_hash)
    if not queries:
        return 0
    row = conn.execute("SELECT SUM(n) FROM (" + " UNION ALL ".join(queries) + ") AS refs", params).fetchone()
    return int(row[0] or 0)


async def delete_global_file(file_hash: str, *, conn=None) -> bool:
    """Detach operations retain shared bytes until an audited collector exists.

    Structured counts cannot prove that rich text, Git snapshots or an upload
    awaiting commit no longer references a blob. A borrowed transaction may
    also roll back after an irreversible unlink. Returning False preserves the
    existing `removed_file_count` contract without claiming collection.

    See docs/global-file-reference-protocol.md for the collector prerequisites.
    Do not add an environment bypass or perform unlink inside caller transactions.
    """
    return False


async def get_file_lock(file_hash: str) -> asyncio.Lock:
    if file_hash not in file_locks:
        file_locks[file_hash] = asyncio.Lock()
    return file_locks[file_hash]


async def stream_file(file_path: Path):
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(FILE_CHUNK_SIZE):
            yield chunk
