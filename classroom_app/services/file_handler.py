import asyncio
from pathlib import Path
import aiofiles
import uuid

from ..core import active_downloads
from ..config import TOTAL_UPLOAD_MBPS


async def throttled_file_iterator(
    file_path: Path,
    bw_limit_mbps: float,
    *,
    start: int = 0,
    length: int | None = None,
    chunk_size: int = 1024 * 64,
):
    """Stream ``length`` bytes from ``start`` at about ``bw_limit_mbps`` MiB/s (0 = unthrottled).

    The pause now sits inside the loop, after every chunk; it used to run once
    after the whole file, so nothing was actually throttled.
    """
    rate_limit_bytes = float(bw_limit_mbps or 0) * 1024 * 1024
    remaining = None if length is None else max(0, int(length))
    async with aiofiles.open(file_path, mode="rb") as file:
        if start:
            await file.seek(int(start))
        while remaining is None or remaining > 0:
            size = chunk_size if remaining is None else min(chunk_size, remaining)
            chunk = await file.read(size)
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
            if rate_limit_bytes > 0:
                await asyncio.sleep(len(chunk) / rate_limit_bytes)


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
