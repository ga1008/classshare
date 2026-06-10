"""压缩附件（zip / rar / 7z）的安全解压与解压文件定位。

公文的正文/附件偶尔是压缩包。解析入库时自动解压到
``data/gongwen_attachments/<school>/extracted/<remote_id>/<which>/``，
每个解压出的文件作为该公文的"附件"条目继续走解析流水线；确认解压
完整后由解析流水线删除原压缩包（见 ``gongwen_parse_service``）。

安全约束：
- zip 用标准库解压，逐项过滤路径（拒绝绝对路径 / ``..`` / 盘符，即 zip-slip）；
  无 UTF-8 标记的条目名按 GBK 兜底解码（国内压缩包常见编码）。
- rar / 7z 交给 bsdtar（libarchive，Docker 镜像内置 ``libarchive-tools``；
  Windows 自带 ``tar`` 即 bsdtar），解压后再统一校验：删符号链接、
  越界文件，超出数量/体积上限判为不完整（不删原包）。
- 对外提供 ``resolve_extracted_file``：按 entry 相对路径取文件，
  resolve 后必须仍在该公文的解压目录内才返回。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from .gongwen_document_sync_service import GONGWEN_ATTACHMENT_DIR
from .organization_scope_service import normalize_school_code

ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
MAX_ARCHIVE_ENTRIES = 80
MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
EXTRACT_TIMEOUT_SECONDS = 180
_SKIP_NAME_PARTS = {"__MACOSX", ".DS_Store", "Thumbs.db"}
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def is_archive_ext(ext: str) -> bool:
    return ("." + str(ext or "").lstrip(".").lower()) in ARCHIVE_EXTS


def extracted_root_for(school_code: Any, remote_id: Any) -> Path:
    """每个公文一个解压根目录（按校区 + remote_id，均做安全字符过滤）。"""
    safe_remote = re.sub(r"[^0-9A-Za-z_-]", "_", str(remote_id or "")) or "unknown"
    return GONGWEN_ATTACHMENT_DIR / normalize_school_code(school_code) / "extracted" / safe_remote


def resolve_extracted_file(document: dict[str, Any], entry: str) -> Path | None:
    """按 entry 相对路径定位解压文件；任何越界/异常路径返回 None。"""
    raw = str(entry or "").strip()
    if not raw or raw.startswith(("/", "\\")) or ":" in raw or ".." in raw.replace("\\", "/").split("/"):
        return None
    base = extracted_root_for(document.get("attr_school_code"), document.get("remote_id")).resolve()
    try:
        candidate = (base / raw).resolve()
        candidate.relative_to(base)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:  # entry name declared UTF-8
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _safe_rel_path(name: str) -> Path | None:
    """Normalize an archive entry name into a safe relative path (or reject)."""
    pieces: list[str] = []
    for raw in str(name or "").replace("\\", "/").split("/"):
        piece = raw.strip()
        if piece in ("", "."):
            continue
        if piece == ".." or piece.endswith(":") or piece in _SKIP_NAME_PARTS or piece.startswith("._"):
            return None
        pieces.append(_ILLEGAL_NAME_CHARS.sub("_", piece)[:180])
    return Path(*pieces) if pieces else None


def _reset_dir(dest_dir: Path) -> None:
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)


def _extract_zip(path: Path, dest_dir: Path) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    complete = True
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            warnings.append(f"压缩包内文件过多（{len(infos)} 个），仅解压前 {MAX_ARCHIVE_ENTRIES} 个。")
            infos = infos[:MAX_ARCHIVE_ENTRIES]
            complete = False
        if sum(i.file_size for i in infos) > MAX_TOTAL_UNCOMPRESSED_BYTES:
            return False, ["压缩包解压后体积过大，已跳过自动解压，可下载后自行查看。"]
        for info in infos:
            rel = _safe_rel_path(_decode_zip_name(info))
            if rel is None:
                continue  # junk entries (__MACOSX 等) silently skipped
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 256 * 1024)
    return complete, warnings


def _extract_with_bsdtar(path: Path, dest_dir: Path) -> tuple[bool, list[str]]:
    tool = shutil.which("bsdtar") or shutil.which("tar")
    if not tool:
        return False, ["服务器缺少解压工具，暂不能自动解压该格式，可下载后自行查看。"]
    try:
        proc = subprocess.run(
            [tool, "-x", "-f", str(path), "-C", str(dest_dir)],
            capture_output=True, text=True, timeout=EXTRACT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, [f"解压超时或失败：{str(exc)[:120]}"]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:140]
        return False, [f"解压失败（可能是不支持的压缩格式版本）：{detail}"]
    return True, []


def _validate_extracted(dest_dir: Path, complete: bool, warnings: list[str]) -> dict[str, Any]:
    """统一后校验：去符号链接/越界文件，限数量与体积，列出有效文件。"""
    base = dest_dir.resolve()
    files: list[Path] = []
    total_bytes = 0
    for item in sorted(dest_dir.rglob("*")):
        if item.is_symlink():
            item.unlink(missing_ok=True)
            continue
        if not item.is_file():
            continue
        try:
            item.resolve().relative_to(base)
        except (OSError, ValueError):
            item.unlink(missing_ok=True)
            continue
        files.append(item)
    if len(files) > MAX_ARCHIVE_ENTRIES:
        warnings.append(f"压缩包内文件过多，仅保留前 {MAX_ARCHIVE_ENTRIES} 个。")
        for extra in files[MAX_ARCHIVE_ENTRIES:]:
            extra.unlink(missing_ok=True)
        files = files[:MAX_ARCHIVE_ENTRIES]
        complete = False
    total_bytes = sum(f.stat().st_size for f in files)
    if total_bytes > MAX_TOTAL_UNCOMPRESSED_BYTES:
        shutil.rmtree(dest_dir, ignore_errors=True)
        return {"files": [], "complete": False, "warnings": [*warnings, "压缩包解压后体积过大，已放弃自动解压。"]}
    if not files:
        complete = False
        if not warnings:
            warnings.append("压缩包为空或未能解压出有效文件。")
    return {"files": files, "complete": complete, "warnings": warnings}


def extract_archive(path: Path, dest_dir: Path) -> dict[str, Any]:
    """解压到 dest_dir（先清空）。返回 {files, complete, warnings}；不抛异常。

    ``complete=True`` 表示压缩包内容已完整落盘——此时调用方才可以安全删除原包。
    """
    try:
        _reset_dir(dest_dir)
        if path.suffix.lower() == ".zip":
            complete, warnings = _extract_zip(path, dest_dir)
        else:
            complete, warnings = _extract_with_bsdtar(path, dest_dir)
        return _validate_extracted(dest_dir, complete, warnings)
    except Exception as exc:  # noqa: BLE001 — 解压失败不能影响解析主流程
        return {"files": [], "complete": False, "warnings": [f"解压失败：{str(exc)[:140]}"]}
