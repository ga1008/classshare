import asyncio
import hashlib
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import aiofiles
from fastapi import UploadFile

from ..config import GLOBAL_FILES_DIR, GLOBAL_FILES_LEGACY_DIRS, FILE_CHUNK_SIZE, CHUNK_UPLOAD_TIMEOUT_HOURS
from ..storage_paths import unique_paths


file_locks: Dict[str, asyncio.Lock] = {}

HASH_CHUNK_SIZE = 1024 * 1024
WRITE_CHUNK_SIZE = 1024 * 1024


def _normalize_file_hash(file_hash: str) -> str:
    return str(file_hash or "").strip().lower()


def global_file_candidates(file_hash: str) -> tuple[Path, ...]:
    normalized_hash = _normalize_file_hash(file_hash)
    if not normalized_hash:
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
    return _build_sharded_path(GLOBAL_FILES_DIR, _normalize_file_hash(file_hash))


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
    """Store an upload by SHA-256 hash and return its storage metadata."""
    try:
        GLOBAL_FILES_DIR.mkdir(parents=True, exist_ok=True)

        file_hash = await calculate_file_hash(file)
        file_path = global_file_write_path(file_hash)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        existing_path = resolve_global_file_path(file_hash)
        if existing_path and existing_path != file_path and not file_path.exists():
            shutil.copy2(existing_path, file_path)

        if file_path.exists():
            return {
                "hash": file_hash,
                "path": str(file_path),
                "size": file_path.stat().st_size,
            }

        async with aiofiles.open(file_path, "wb") as out_file:
            while chunk := await file.read(WRITE_CHUNK_SIZE):
                await out_file.write(chunk)

        return {
            "hash": file_hash,
            "path": str(file_path),
            "size": file_path.stat().st_size,
        }

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


async def delete_global_file(file_hash: str) -> bool:
    try:
        for file_path in global_file_candidates(file_hash):
            if file_path.exists():
                file_path.unlink()
        return True
    except Exception as e:
        print(f"[ERROR] delete global file failed ({file_hash}): {e}")
        return False


async def get_file_lock(file_hash: str) -> asyncio.Lock:
    if file_hash not in file_locks:
        file_locks[file_hash] = asyncio.Lock()
    return file_locks[file_hash]


async def stream_file(file_path: Path):
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(FILE_CHUNK_SIZE):
            yield chunk
