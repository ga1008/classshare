"""Parse + assemble a readable view of a 公文 (正文 + 附件) for the in-page reader.

Reuses the battle-tested ``extract_material_content`` (PDF via PyMuPDF, DOCX via
python-docx, XLS/XLSX via openpyxl/xlrd, TXT/CSV/MD, legacy DOC best-effort) to
turn each file into plain text. The extracted text is cached on the document
(``parsed_text`` = AI-ready plain text, ``parsed_payload_json`` = structured
parts) so re-opening is instant and a later AI step can read it directly.

Display kinds per part: ``html`` (公文正文 HTML), ``text`` (extracted text),
``pdf`` (inline <iframe> of the file endpoint + extracted text), ``image``
(inline <img>), ``unsupported`` (download only).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..database import get_db_connection
from ..db.schema_gongwen import ensure_gongwen_schema
from . import material_scope_service as ms
from .gongwen_document_sync_service import ensure_local_attachment, serialize_gongwen_document

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
PDF_EXTS = {".pdf"}
DISPLAY_TEXT_LIMIT = 120_000
PARSED_TEXT_LIMIT = 240_000


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _filename_from_url(url: str, fallback: str) -> str:
    name = str(url or "").split("?")[0].rstrip("/").split("/")[-1]
    return name or fallback


def _extract_text_safe(path: Path, name: str) -> tuple[str, list[str]]:
    """Run the shared extractor; never raise — degrade to a warning."""
    try:
        from .material_ai_import_service import extract_material_content

        extraction = extract_material_content(path, name)
        return str(extraction.text or ""), list(extraction.warnings or [])
    except Exception as exc:  # noqa: BLE001 — extraction must never break the reader
        return "", [f"在线解析失败：{str(exc)[:140]}"]


async def _parse_file_part(
    teacher_scope: dict[str, str],
    document_id: int,
    which: str,
    url: str,
    *,
    is_super_admin: bool,
) -> dict[str, Any]:
    name = _filename_from_url(url, f"{which}")
    ext = Path(name).suffix.lower()
    label = "正文文件" if which == "primary" else "附件"
    view_url = f"/api/manage/gongwen/documents/{document_id}/file?which={which}"
    part: dict[str, Any] = {
        "which": which,
        "name": name,
        "ext": ext.lstrip("."),
        "label": label,
        "kind": "unsupported",
        "text": "",
        "warnings": [],
        "view_url": view_url,
        "download_url": view_url,
        "truncated": False,
    }

    cache = await ensure_local_attachment(teacher_scope, document_id, which, is_super_admin=is_super_admin)
    if cache.get("status") != "local":
        # Could not cache a local copy; still let the user view/download the CDN file.
        part["kind"] = "pdf" if ext in PDF_EXTS else ("image" if ext in IMAGE_EXTS else "unsupported")
        part["warnings"].append("未能获取本地副本，暂不能在线解析，可点击下载查看。")
        return part

    path = Path(cache["local_path"])
    if ext in IMAGE_EXTS:
        part["kind"] = "image"
        return part

    text, warnings = await asyncio.to_thread(_extract_text_safe, path, name)
    part["warnings"].extend(warnings)
    if len(text) > DISPLAY_TEXT_LIMIT:
        text = text[:DISPLAY_TEXT_LIMIT]
        part["truncated"] = True
    part["text"] = text
    if ext in PDF_EXTS:
        part["kind"] = "pdf"  # inline iframe + extracted text
    elif text.strip():
        part["kind"] = "text"
    else:
        part["kind"] = "unsupported"
        if not part["warnings"]:
            part["warnings"].append("该文件未能解析出文本，可下载后查看。")
    return part


def _assemble(data: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
    meta = serialize_gongwen_document(data, include_content=True)
    meta["parts"] = parts
    meta["parsed_status"] = str(data.get("parsed_status") or "idle")
    meta["parsed_at"] = str(data.get("parsed_at") or "")
    return meta


async def build_gongwen_document_reader(
    teacher_scope: dict[str, str],
    document_id: int,
    *,
    is_super_admin: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Return the document metadata + content + parsed file parts for the reader."""
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        return None
    data = dict(row)
    if not ms.can_view(data, teacher_scope, is_super_admin=is_super_admin):
        return None

    # Serve cached parse unless a refresh was requested.
    if not refresh and str(data.get("parsed_status") or "") in {"done", "partial"}:
        try:
            cached = json.loads(data.get("parsed_payload_json") or "{}")
        except (TypeError, ValueError):
            cached = {}
        if isinstance(cached.get("parts"), list):
            return _assemble(data, cached["parts"])

    parts: list[dict[str, Any]] = []
    for which, url_col in (("primary", "file_url"), ("attachment", "attachment_url")):
        url = str(data.get(url_col) or "")
        if not url:
            continue
        parts.append(await _parse_file_part(teacher_scope, document_id, which, url, is_super_admin=is_super_admin))

    # AI-ready plain text = 正文 + every parsed file's text.
    text_chunks: list[str] = []
    if str(data.get("content_text") or "").strip():
        text_chunks.append(str(data["content_text"]).strip())
    for part in parts:
        if part.get("text", "").strip():
            text_chunks.append(f"【{part['label']}：{part['name']}】\n{part['text'].strip()}")
    parsed_text = "\n\n".join(text_chunks)[:PARSED_TEXT_LIMIT]

    any_text = any(p.get("kind") in {"text", "pdf"} and p.get("text", "").strip() for p in parts)
    status = "done" if (parts and (any_text or all(p.get("kind") == "image" for p in parts))) else ("partial" if parts else "done")

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE gongwen_documents SET parsed_status = ?, parsed_text = ?, parsed_payload_json = ?, parsed_at = ?, "
            "updated_at = ? WHERE id = ?",
            (status, parsed_text, json.dumps({"parts": parts}, ensure_ascii=False)[:600_000], _now_iso(), _now_iso(), int(document_id)),
        )
        conn.commit()
        fresh = dict(conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone())
    return _assemble(fresh, parts)


def get_gongwen_document_parsed_text(conn, teacher_scope: dict[str, str], document_id: int, *, is_super_admin: bool = False) -> str | None:
    """Reader-independent accessor for the cached AI-ready text (for reminders/AI)."""
    ensure_gongwen_schema(conn)
    row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        return None
    data = dict(row)
    if not ms.can_view(data, teacher_scope, is_super_admin=is_super_admin):
        return None
    return str(data.get("parsed_text") or data.get("content_text") or "")
