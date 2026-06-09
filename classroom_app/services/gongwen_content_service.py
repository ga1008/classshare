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
EXCEL_EXTS = {".xlsx", ".xls", ".csv"}
DISPLAY_TEXT_LIMIT = 120_000
PARSED_TEXT_LIMIT = 240_000
MAX_TABLE_ROWS = 300
MAX_TABLE_COLS = 30
AI_OCR_MAX_IMAGES = 8
AI_OCR_TIMEOUT_SECONDS = 150.0
# Drop extractor warnings that are noise for the reader (we use the text/tables).
_SUPPRESSED_WARNINGS = ("已将 Office 文档渲染为页面图片用于视觉兜底",)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _filename_from_url(url: str, fallback: str) -> str:
    name = str(url or "").split("?")[0].rstrip("/").split("/")[-1]
    return name or fallback


def _clean_warnings(warnings) -> list[str]:
    return [w for w in (warnings or []) if not any(s in str(w) for s in _SUPPRESSED_WARNINGS)]


def _run_extractor(path: Path, name: str):
    """Run the shared extractor; never raise."""
    try:
        from .material_ai_import_service import extract_material_content

        return extract_material_content(path, name)
    except Exception as exc:  # noqa: BLE001 — extraction must never break the reader

        class _Empty:
            text = ""
            warnings = [f"在线解析失败：{str(exc)[:140]}"]
            images: list[dict[str, str]] = []
            quality: dict[str, Any] = {}

        return _Empty()


def _parse_excel(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Parse a workbook into structured sheets for a real HTML table render."""
    sheets: list[dict[str, Any]] = []
    text_lines: list[str] = []
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets[:6]:
            rows: list[list[str]] = []
            for raw in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in raw][:MAX_TABLE_COLS]
                if any(cells):
                    rows.append(cells)
                if len(rows) >= MAX_TABLE_ROWS:
                    break
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                sheets.append({"sheet": str(ws.title), "rows": rows})
                text_lines.append(f"## 工作表：{ws.title}")
                text_lines.extend("\t".join(r).strip() for r in rows)
        wb.close()
    except Exception as exc:  # noqa: BLE001
        return [], ""
    return sheets, "\n".join(text_lines).strip()


async def _ocr_images_with_ai(images: list[dict[str, str]], hint: str) -> str:
    """Transcribe rendered page images with the multimodal model (scanned docs)."""
    usable = [img for img in (images or []) if str(img.get("data_url") or "").startswith("data:")][:AI_OCR_MAX_IMAGES]
    if not usable:
        return ""
    from ..core import ai_client

    payload = {
        "system_prompt": (
            "你是公文 OCR 与版式还原助手。请把图片中的公文内容按阅读顺序完整转写为整洁文本："
            "保留标题、文号、条款编号与层级；表格请用 Markdown 表格还原。只输出转写内容本身，不要解释。"
        ),
        "messages": [],
        "new_message": f"请完整转写这些公文页面图片中的全部文字内容。{hint}",
        "base64_urls": [],
        "image_inputs": [{"url": img["data_url"], "label": img.get("filename", "")} for img in usable],
        "file_texts": [],
        "model_capability": "vision",
        "task_type": "vision",
        "response_format": "text",
        "task_priority": "interactive",
        "task_label": "gongwen_ocr",
    }
    try:
        import httpx

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=AI_OCR_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return ""
    return str((data or {}).get("response_text") or "").strip()


def _text_is_weak(text: str, *, pages: int = 1) -> bool:
    stripped = (text or "").strip()
    return len(stripped) < max(40, pages * 10)


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
        "tables": [],
        "warnings": [],
        "view_url": f"{view_url}&inline=1",
        "download_url": view_url,
        "truncated": False,
        "ai_used": False,
    }

    cache = await ensure_local_attachment(teacher_scope, document_id, which, is_super_admin=is_super_admin)
    if cache.get("status") != "local":
        part["kind"] = "pdf" if ext in PDF_EXTS else ("image" if ext in IMAGE_EXTS else "unsupported")
        part["warnings"].append("未能获取本地副本，暂不能在线解析，可点击下载查看。")
        return part

    path = Path(cache["local_path"])

    if ext in IMAGE_EXTS:
        part["kind"] = "image"
        return part

    if ext in EXCEL_EXTS:
        sheets, text = await asyncio.to_thread(_parse_excel, path)
        if sheets:
            part["kind"] = "table"
            part["tables"] = sheets
            part["text"] = text[:DISPLAY_TEXT_LIMIT]
            return part
        # fall through to generic extractor if openpyxl produced nothing

    extraction = await asyncio.to_thread(_run_extractor, path, name)
    text = str(getattr(extraction, "text", "") or "")
    images = list(getattr(extraction, "images", []) or [])
    part["warnings"].extend(_clean_warnings(getattr(extraction, "warnings", [])))

    pages = max(1, len(images)) if ext in PDF_EXTS else 1
    # Escalate to multimodal OCR when the text layer is weak (e.g. scanned PDFs).
    if _text_is_weak(text, pages=pages) and images:
        ocr_text = await _ocr_images_with_ai(images, f"文件名：{name}")
        if len(ocr_text.strip()) > len(text.strip()):
            text = ocr_text
            part["ai_used"] = True
            part["warnings"].append("正文文字较少，已用多模态模型识别页面图片补全内容。")

    if len(text) > DISPLAY_TEXT_LIMIT:
        text = text[:DISPLAY_TEXT_LIMIT]
        part["truncated"] = True
    part["text"] = text

    if ext in PDF_EXTS:
        part["kind"] = "pdf"
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

    any_text = any(p.get("kind") in {"text", "pdf", "table"} and p.get("text", "").strip() for p in parts)
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
