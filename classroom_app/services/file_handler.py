import asyncio
from pathlib import Path
import aiofiles
import uuid

from ..core import active_downloads
from ..config import TOTAL_UPLOAD_MBPS


async def throttled_file_iterator(file_path: Path, bw_limit_mbps: float):
    chunk_size = 1024 * 64
    rate_limit_bytes = bw_limit_mbps * 1024 * 1024
    delay = chunk_size / rate_limit_bytes if rate_limit_bytes > 0 else 0
    with open(file_path, mode="rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            yield chunk
        if delay > 0:
            await asyncio.sleep(delay)


async def save_upload_file(upload_dir: Path, file: "UploadFile") -> dict:
    """
    保存上传的文件并返回文件名和路径。
    """
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 清理文件名
    original_filename = "".join(c for c in file.filename if c.isalnum() or c in ('.', '_', '-')).strip()
    # 创建一个唯一的文件名
    stored_filename = f"{uuid.uuid4()}_{original_filename}"
    stored_path = upload_dir / stored_filename

    try:
        async with aiofiles.open(stored_path, 'wb') as out_file:
            while content := await file.read(1024 * 1024):  # 1MB chunks
                await out_file.write(content)
    except Exception as e:
        print(f"[ERROR] 保存文件失败: {e}")
        return None
    finally:
        await file.close()

    return {
        "original_filename": original_filename,
        "stored_path": str(stored_path)
    }

async def delete_file_safely(file_path: Path) -> bool:
    """安全地删除文件,返回是否成功删除"""
    try:
        if file_path.exists():
            file_path.unlink()
        return True
    except Exception as e:
        print(f"[ERROR] 删除文件失败 ({file_path}): {e}")
        return False
