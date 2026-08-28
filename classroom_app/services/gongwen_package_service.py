"""公文打包下载：正文 + 全部附件 → 一个 zip，并以可读名称重命名。

上游 CDN 的文件名是一串哈希（如 ``3aa58a8f….pdf``），单独下载后无法从
文件名判断内容。打包时统一改用「文号 标题」组织：

- zip 文件名 = ``{文号} {标题}.zip``；
- 正文文件 → ``{文号 标题}（正文）.{ext}``；
- 附件（含压缩包解压出的文件，本身已有真实中文名）→ ``附件/`` 目录，
  保留解压后的相对路径；未解压的单附件 → ``{文号 标题}（附件）.{ext}``；
- 附带 ``公文信息.txt``（文号/标题/发文单位/摘要/关键词等元数据）。

zip 写入临时文件（附件体积上限与解压流水线一致，最大可达约 200MB，
不能全放内存），由路由层在响应结束后清理。
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..database import get_db_connection
from ..db.schema_gongwen import ensure_gongwen_schema
from . import material_scope_service as ms
from .gongwen_archive_service import extracted_root_for
from .gongwen_document_sync_service import ensure_local_attachment

# Windows 非法文件名字符 + 控制字符；斜杠也除掉（名字不允许分层）。
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NAME_MAX_LEN = 120


def safe_download_name(text: str, fallback: str = "公文") -> str:
    """把任意标题/文号清洗成各系统都合法的文件名片段。"""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub(" ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:_NAME_MAX_LEN].strip() or fallback


def document_base_name(document: dict[str, Any]) -> str:
    """「文号 标题」——zip 名与文件重命名的公共前缀。"""
    sn = str(document.get("sn") or "").strip()
    title = str(document.get("title") or "").strip()
    return safe_download_name(f"{sn} {title}".strip(), fallback=f"公文{document.get('id', '')}")


def readable_file_name(document: dict[str, Any], which: str, original_name: str) -> str:
    """单文件下载的可读名：``{文号 标题}（正文/附件）.{ext}``。"""
    ext = Path(str(original_name or "")).suffix
    label = "正文" if which != "attachment" else "附件"
    return safe_download_name(f"{document_base_name(document)}（{label}）") + ext


def _unique_arcname(used: set[str], name: str) -> str:
    if name not in used:
        used.add(name)
        return name
    stem, ext = Path(name).stem, Path(name).suffix
    parent = str(Path(name).parent)
    prefix = "" if parent in ("", ".") else f"{parent}/"
    for i in range(2, 100):
        candidate = f"{prefix}{stem}({i}){ext}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    used.add(name)
    return name


def _info_text(document: dict[str, Any]) -> str:
    rows = [
        ("文号", document.get("sn")),
        ("标题", document.get("title")),
        ("发文单位", document.get("author")),
        ("发送人", document.get("sender_name")),
        ("分类", document.get("category_name")),
        ("发布时间", document.get("publish_time")),
        ("正文标题", document.get("parsed_title")),
        ("内容摘要", document.get("parsed_summary") or document.get("summary")),
        ("关键词", document.get("parsed_keywords") or document.get("keywords")),
        ("落款", document.get("parsed_signature")),
        ("正文原始文件名", document.get("source_file_name")),
        ("正文原始链接", document.get("file_url")),
        ("附件原始链接", document.get("attachment_url")),
    ]
    lines = [f"{label}：{str(value).strip()}" for label, value in rows if str(value or "").strip()]
    return "\n".join(lines) + "\n"


def _extracted_files(document: dict[str, Any], which: str) -> list[tuple[Path, str]]:
    """压缩附件解压后的 (文件, 包内相对路径)；解压文件本身已是真实中文名。"""
    base = extracted_root_for(document.get("attr_school_code"), document.get("remote_id")) / which
    if not base.is_dir():
        return []
    files: list[tuple[Path, str]] = []
    for item in sorted(base.rglob("*")):
        if item.is_file() and not item.is_symlink():
            files.append((item, item.relative_to(base).as_posix()))
    return files


async def build_gongwen_package(
    teacher_scope: dict[str, str],
    document_id: int,
    *,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """打包一份公文的全部可下载内容到临时 zip。

    返回 ``{"zip_path": str, "download_name": str, "entries": int}``；
    公文不可见抛 LookupError，没有任何可打包文件抛 ValueError。
    调用方（路由）负责在响应结束后删除临时文件。
    """
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute(
            "SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)
        ).fetchone()
    if row is None:
        raise LookupError("公文不存在或无权访问。")
    document = dict(row)
    if not ms.can_view(document, teacher_scope, is_super_admin=is_super_admin):
        raise LookupError("公文不存在或无权访问。")

    base_name = document_base_name(document)
    used_names: set[str] = set()
    entries = 0

    tmp = tempfile.NamedTemporaryFile(prefix="gongwen_pkg_", suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for which in ("primary", "attachment"):
                extracted = _extracted_files(document, which)
                if extracted:
                    # 压缩包附件已解压：直接收编解压文件（真实中文名），
                    # 不再回源重新下载原压缩包。
                    prefix = "正文" if which == "primary" else "附件"
                    for file_path, rel in extracted:
                        zf.write(file_path, _unique_arcname(used_names, f"{prefix}/{rel}"))
                        entries += 1
                    continue
                url_col = "file_url" if which == "primary" else "attachment_url"
                if not str(document.get(url_col) or "").strip():
                    continue
                cache = await ensure_local_attachment(
                    teacher_scope, int(document_id), which, is_super_admin=is_super_admin
                )
                if cache.get("status") != "local":
                    continue
                local = Path(cache["local_path"])
                zf.write(local, _unique_arcname(used_names, readable_file_name(document, which, local.name)))
                entries += 1

            if entries == 0:
                raise ValueError("该公文没有可打包下载的正文或附件（源文件可能尚未下载成功）。")

            zf.writestr(_unique_arcname(used_names, "公文信息.txt"), _info_text(document))
            content_html = str(document.get("content_html") or "").strip()
            if content_html:
                html = (
                    "<!doctype html><html><head><meta charset=\"utf-8\">"
                    f"<title>{base_name}</title></head><body>{content_html}</body></html>"
                )
                zf.writestr(_unique_arcname(used_names, "公文正文.html"), html)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "zip_path": str(tmp_path),
        "download_name": f"{base_name}.zip",
        "entries": entries,
    }
