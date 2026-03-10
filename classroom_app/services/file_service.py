import hashlib
import shutil
import aiofiles
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict
from fastapi import UploadFile
from ..config import GLOBAL_FILES_DIR, FILE_CHUNK_SIZE, CHUNK_UPLOAD_TIMEOUT_HOURS

# 文件下载锁
file_locks: Dict[str, asyncio.Lock] = {}

# 哈希和写入使用更大的块大小以提高效率
HASH_CHUNK_SIZE = 1024 * 1024  # 1MB
WRITE_CHUNK_SIZE = 1024 * 1024  # 1MB


async def calculate_file_hash(file: UploadFile) -> str:
    """计算文件的 SHA256 哈希值"""
    sha256_hash = hashlib.sha256()

    while chunk := await file.read(HASH_CHUNK_SIZE):
        sha256_hash.update(chunk)

    await file.seek(0)  # 重置文件指针
    return sha256_hash.hexdigest()


async def save_file_globally(file: UploadFile) -> Optional[Dict]:
    """
    全局保存文件,返回文件信息。
    如果文件已存在,返回现有文件信息。
    """
    try:
        # 确保目录存在
        GLOBAL_FILES_DIR.mkdir(parents=True, exist_ok=True)

        # 计算文件哈希
        file_hash = await calculate_file_hash(file)
        file_path = GLOBAL_FILES_DIR / file_hash

        # 如果文件已存在,直接返回信息
        if file_path.exists():
            return {
                "hash": file_hash,
                "path": str(file_path),
                "size": file_path.stat().st_size
            }

        # 保存新文件
        async with aiofiles.open(file_path, 'wb') as out_file:
            while chunk := await file.read(WRITE_CHUNK_SIZE):
                await out_file.write(chunk)

        return {
            "hash": file_hash,
            "path": str(file_path),
            "size": file_path.stat().st_size
        }

    except Exception as e:
        print(f"[ERROR] 保存文件失败: {e}")
        return None


def cleanup_stale_uploads():
    """清理超时的未完成分块上传临时文件"""
    from ..database import get_db_connection

    cutoff = (datetime.now() - timedelta(hours=CHUNK_UPLOAD_TIMEOUT_HOURS)).isoformat()
    try:
        with get_db_connection() as conn:
            stale = conn.execute(
                "SELECT upload_id, temp_dir FROM chunked_uploads WHERE status = 'uploading' AND created_at < ?",
                (cutoff,)
            ).fetchall()
            for row in stale:
                try:
                    shutil.rmtree(row['temp_dir'], ignore_errors=True)
                except Exception:
                    pass
                conn.execute(
                    "UPDATE chunked_uploads SET status = 'expired' WHERE upload_id = ?",
                    (row['upload_id'],)
                )
            conn.commit()
            if stale:
                print(f"[CLEANUP] 清理了 {len(stale)} 个超时的分块上传。")
    except Exception as e:
        print(f"[ERROR] 清理超时上传失败: {e}")

async def delete_global_file(file_hash: str) -> bool:
    """删除全局文件,返回是否成功删除"""
    file_path = GLOBAL_FILES_DIR / file_hash
    try:
        if file_path.exists():
            file_path.unlink()
        return True
    except Exception as e:
        print(f"[ERROR] 删除全局文件失败 ({file_path}): {e}")
        return False

async def get_file_lock(file_hash: str) -> asyncio.Lock:
    """获取文件的锁,如果不存在则创建"""
    if file_hash not in file_locks:
        file_locks[file_hash] = asyncio.Lock()
    return file_locks[file_hash]

async def stream_file(file_path: Path):
    """以流式方式读取文件"""
    async with aiofiles.open(file_path, 'rb') as f:
        while chunk := await f.read(FILE_CHUNK_SIZE):
            yield chunk