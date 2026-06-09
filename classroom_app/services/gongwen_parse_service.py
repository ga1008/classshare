"""公文 parse pipeline: logic → fast-AI verify → multimodal fallback → AI structure.

Each document is ingested as metadata first (``parsed_status='pending'``) and then
parsed asynchronously by a scheduler worker (so the first full backfill of a large
inbox spreads its load and never blocks a request). Per document:

1. Download + keep the source files (正文 + 附件) for manual verification/download.
2. Logic-parse each file (PyMuPDF / python-docx / openpyxl / txt) — see
   ``gongwen_content_service.build_file_part`` (which also runs the fast-AI verify
   and escalates to multimodal OCR on garbled/scanned files).
3. Structure the aggregated text with a fast model into 正文标题 / 摘要 / 落款.
4. Store ``parsed_text`` (AI-ready), ``parsed_payload_json`` (parts), the structured
   fields, and ``parsed_status='done'`` (or ``'failed'`` with ``parse_error``).

All AI calls use **background priority** so they never starve interactive AI, and
the worker processes a small, paced batch per run — keeping load on both the
upstream 公文 server and this platform's AI queue gentle (req 6).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..database import get_db_connection
from ..db.schema_gongwen import ensure_gongwen_schema
from . import material_scope_service as ms
from .gongwen_content_service import (
    PARSED_TEXT_LIMIT,
    assemble_reader,
    build_file_part,
)

GONGWEN_PARSE_TASK_KIND = "gongwen_parse_pending"
GONGWEN_PARSE_INTERVAL_SECONDS = 150
PARSE_BATCH_SIZE = 6
PARSE_ITEM_DELAY_SECONDS = 1.0
PARSE_MAX_ATTEMPTS = 4
PARSE_STALE_MINUTES = 30
STRUCTURE_INPUT_LIMIT = 9000

# Internal worker has no requesting teacher — bypass visibility for parsing only.
_ADMIN_SCOPE: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def _structure_with_ai(text: str) -> dict[str, str]:
    """Extract 正文标题 / 摘要 / 落款 from the document text (fast model)."""
    head = (text or "").strip()
    if not head:
        return {}
    if len(head) > STRUCTURE_INPUT_LIMIT:
        head = head[: STRUCTURE_INPUT_LIMIT - 800] + "\n……\n" + head[-700:]
    payload = {
        "system_prompt": (
            "你是公文信息抽取助手。从给定公文文本中抽取要点，只输出 JSON："
            "{\"title\": \"正文标题（无则留空）\", \"summary\": \"120字以内中文摘要\", "
            "\"signature\": \"落款（发文单位与日期，无则留空）\"}。不要编造内容。"
        ),
        "messages": [],
        "new_message": head,
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "background",
        "task_label": "gongwen_parse_structure",
    }
    try:
        from ..core import ai_client
        import httpx  # noqa: F401

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    parsed = data.get("response_json") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        return {}
    return {
        "title": str(parsed.get("title") or "").strip()[:300],
        "summary": str(parsed.get("summary") or "").strip()[:600],
        "signature": str(parsed.get("signature") or "").strip()[:300],
    }


def _assemble_from_stored(data: dict[str, Any]) -> dict[str, Any]:
    try:
        cached = json.loads(data.get("parsed_payload_json") or "{}")
    except (TypeError, ValueError):
        cached = {}
    parts = cached.get("parts") if isinstance(cached, dict) else None
    return assemble_reader(data, parts if isinstance(parts, list) else [])


async def parse_document(document_id: int, *, force: bool = False) -> dict[str, Any] | None:
    """Run the full parse pipeline for one document, store results, set status."""
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        return None
    data = dict(row)

    if not force and str(data.get("parsed_status") or "") == "done":
        return _assemble_from_stored(data)

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE gongwen_documents SET parsed_status = 'parsing', parse_attempts = parse_attempts + 1, "
            "updated_at = ? WHERE id = ?",
            (_now_iso(), int(document_id)),
        )
        conn.commit()

    try:
        parts: list[dict[str, Any]] = []
        source_name, source_type = "", ""
        for which, url_col in (("primary", "file_url"), ("attachment", "attachment_url")):
            url = str(data.get(url_col) or "")
            if not url:
                continue
            part = await build_file_part(_ADMIN_SCOPE, int(document_id), which, url, is_super_admin=True)
            parts.append(part)
            if which == "primary":
                source_name = part.get("name") or ""
                source_type = part.get("ext") or ""

        text_chunks: list[str] = []
        if str(data.get("content_text") or "").strip():
            text_chunks.append(str(data["content_text"]).strip())
        for part in parts:
            if part.get("text", "").strip():
                text_chunks.append(f"【{part['label']}：{part['name']}】\n{part['text'].strip()}")
        parsed_text = "\n\n".join(text_chunks)[:PARSED_TEXT_LIMIT]

        struct = await _structure_with_ai(parsed_text) if parsed_text.strip() else {}
        parsed_title = struct.get("title") or str(data.get("title") or "")
        parsed_summary = struct.get("summary") or str(data.get("summary") or "")
        parsed_signature = struct.get("signature") or ""

        has_content = bool(parsed_text.strip()) or any(p.get("kind") == "image" for p in parts)
        status = "done" if (parts and has_content) else ("done" if not parts else "failed")
        error = "" if status == "done" else "未能从源文件解析出可读内容。"

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE gongwen_documents
                SET parsed_status = ?, parsed_text = ?, parsed_payload_json = ?,
                    parsed_title = ?, parsed_summary = ?, parsed_signature = ?,
                    source_file_name = CASE WHEN source_file_name = '' THEN ? ELSE source_file_name END,
                    source_file_type = CASE WHEN source_file_type = '' THEN ? ELSE source_file_type END,
                    parse_error = ?, parsed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status, parsed_text, json.dumps({"parts": parts}, ensure_ascii=False)[:600_000],
                    parsed_title, parsed_summary, parsed_signature,
                    source_name, source_type, error, _now_iso(), _now_iso(), int(document_id),
                ),
            )
            conn.commit()
            fresh = dict(conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone())
        return assemble_reader(fresh, parts)
    except Exception as exc:  # noqa: BLE001 — record failure, let the worker retry later
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE gongwen_documents SET parsed_status = 'failed', parse_error = ?, updated_at = ? WHERE id = ?",
                (str(exc)[:400], _now_iso(), int(document_id)),
            )
            conn.commit()
        return None


async def build_gongwen_document_reader(
    teacher_scope: dict[str, str],
    document_id: int,
    *,
    is_super_admin: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Reader entry: visibility-check, serve the stored parse, or parse on demand."""
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        return None
    data = dict(row)
    if not ms.can_view(data, teacher_scope, is_super_admin=is_super_admin):
        return None
    if not refresh and str(data.get("parsed_status") or "") == "done":
        return _assemble_from_stored(data)
    # Pending/parsing/failed, or a forced refresh → parse now (instant gratification;
    # the background worker would otherwise get to it eventually).
    return await parse_document(int(document_id), force=refresh)


# --------------------------------------------------------------------------- #
# Background worker — drains the pending backlog in small paced batches.
# --------------------------------------------------------------------------- #


def _reset_stale_parsing(conn) -> None:
    cutoff = (datetime.now() - timedelta(minutes=PARSE_STALE_MINUTES)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE gongwen_documents SET parsed_status = 'pending' "
        "WHERE parsed_status = 'parsing' AND (updated_at IS NULL OR updated_at < ?)",
        (cutoff,),
    )


def _claim_pending_ids(conn, limit: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM gongwen_documents
        WHERE parsed_status = 'pending'
           OR (parsed_status = 'failed' AND parse_attempts < ?)
        ORDER BY (parsed_status = 'pending') DESC, publish_time DESC, id DESC
        LIMIT ?
        """,
        (PARSE_MAX_ATTEMPTS, int(limit)),
    ).fetchall()
    return [int(dict(r)["id"]) for r in rows]


async def parse_pending_batch(limit: int = PARSE_BATCH_SIZE) -> dict[str, Any]:
    """Parse up to ``limit`` pending documents, paced. Returns a small summary."""
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        _reset_stale_parsing(conn)
        conn.commit()
        ids = _claim_pending_ids(conn, limit)
        remaining = int((conn.execute("SELECT COUNT(*) AS c FROM gongwen_documents WHERE parsed_status = 'pending'").fetchone() or {"c": 0})["c"])
    done = 0
    for index, doc_id in enumerate(ids):
        try:
            result = await parse_document(doc_id)
            if result is not None:
                done += 1
        except Exception:  # noqa: BLE001
            pass
        if index < len(ids) - 1:
            await asyncio.sleep(PARSE_ITEM_DELAY_SECONDS)
    return {"claimed": len(ids), "parsed": done, "pending_remaining": max(0, remaining - done)}


async def handle_gongwen_parse_task(task: dict[str, Any]) -> str:
    result = await parse_pending_batch()
    return f"parsed={result['parsed']}/{result['claimed']} pending={result['pending_remaining']}"


def schedule_gongwen_parse_worker(conn) -> int:
    """Arm the recurring parse worker (idempotent, one global task)."""
    from .scheduled_task_service import schedule_task

    run_at = datetime.now() + timedelta(seconds=45)
    return schedule_task(
        conn,
        task_kind=GONGWEN_PARSE_TASK_KIND,
        run_at=run_at,
        payload={},
        dedupe_key="gongwen-parse-pending",
        recurrence_seconds=GONGWEN_PARSE_INTERVAL_SECONDS,
        owner_role="system",
        title="公文解析队列",
        replace=True,
    )


def count_pending_parses(conn) -> int:
    ensure_gongwen_schema(conn)
    row = conn.execute("SELECT COUNT(*) AS c FROM gongwen_documents WHERE parsed_status IN ('pending', 'parsing')").fetchone()
    return int(dict(row)["c"]) if row else 0
