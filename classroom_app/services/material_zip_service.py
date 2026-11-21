"""Bounded ZIP reader shared by course materials and private project topics."""

import io
import re
import zipfile
from pathlib import Path

from fastapi import HTTPException


# zip 自动解压护栏：压缩包 ≤200MB，解压后总量 ≤500MB、单文件 ≤100MB、条目 ≤3000。
ZIP_UPLOAD_MAX_COMPRESSED_BYTES = 200 * 1024 * 1024
ZIP_UPLOAD_MAX_TOTAL_BYTES = 500 * 1024 * 1024
ZIP_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024
ZIP_UPLOAD_MAX_ENTRY_COUNT = 3000
_ZIP_IGNORED_SEGMENTS = {"__macosx", ".ds_store", ".git", "thumbs.db"}


def _decode_zip_member_name(info: zipfile.ZipInfo) -> str:
    """zip 条目名编码兜底：无 UTF-8 标记时按 GBK 还原中文文件名。"""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def extract_zip_upload_entries(
    zip_name: str, payload: bytes, *,
    max_compressed: int = ZIP_UPLOAD_MAX_COMPRESSED_BYTES,
    max_total: int = ZIP_UPLOAD_MAX_TOTAL_BYTES,
    max_file: int = ZIP_UPLOAD_MAX_FILE_BYTES,
    max_entries: int = ZIP_UPLOAD_MAX_ENTRY_COUNT,
    wrap: bool = True,
) -> list[dict]:
    """把上传的 zip 解压成上传条目列表（内存解压 + 路径/体积护栏）。

    返回 [{"relative_path": str, "payload": bytes, "content_type": None}]。
    """
    if len(payload) > max_compressed:
        raise HTTPException(400, f"压缩包 {zip_name} 超过 {max_compressed // (1024 * 1024)}MB 限制")

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise HTTPException(400, f"压缩包 {zip_name} 无法解析，请确认是有效的 .zip 文件")

    stem = Path(zip_name).stem.strip() or "archive"
    raw_entries: list[tuple[str, zipfile.ZipInfo]] = []
    total_bytes = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member_name = _decode_zip_member_name(info).replace("\\", "/")
            if not wrap and (member_name.startswith("/") or info.external_attr >> 28 == 10):
                raise HTTPException(400, f"压缩包 {zip_name} 中的路径或文件类型不受支持")
            member_name = member_name.strip("/")
            if not member_name:
                continue
            segments = [seg for seg in member_name.split("/") if seg and seg != "."]
            if not segments:
                continue
            if any(seg == ".." for seg in segments):
                raise HTTPException(400, f"压缩包 {zip_name} 含有非法路径（..）")
            if any(seg.lower() in _ZIP_IGNORED_SEGMENTS for seg in segments):
                continue
            if re.match(r"^[a-zA-Z]:", segments[0]):
                raise HTTPException(400, f"压缩包 {zip_name} 含有非法路径（盘符）")
            if info.file_size > max_file:
                raise HTTPException(400, f"压缩包内文件 {member_name} 超过单文件大小限制")
            total_bytes += int(info.file_size)
            if total_bytes > max_total:
                raise HTTPException(400, f"压缩包 {zip_name} 解压后总体积超过限制")
            raw_entries.append(("/".join(segments), info))
            if len(raw_entries) > max_entries:
                raise HTTPException(400, f"压缩包 {zip_name} 文件数量超过 {max_entries} 限制")

        if not raw_entries:
            raise HTTPException(400, f"压缩包 {zip_name} 中没有可导入的文件")

        # 若 zip 内已有唯一顶层目录则直接使用；否则用压缩包名做顶层目录。
        top_levels = {path.split("/", 1)[0] for path, _info in raw_entries}
        wrap_with_stem = wrap and len(top_levels) != 1

        entries: list[dict] = []
        for member_path, info in raw_entries:
            relative_path = f"{stem}/{member_path}" if wrap_with_stem else member_path
            with archive.open(info) as handle:
                data = handle.read(max_file + 1)
            if len(data) > max_file:
                raise HTTPException(400, f"压缩包内文件 {member_path} 超过单文件大小限制")
            entries.append({"relative_path": relative_path, "payload": data, "content_type": None})
    return entries
