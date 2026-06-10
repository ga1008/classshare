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
from .gongwen_archive_service import extracted_root_for
from .gongwen_content_service import (
    PARSED_TEXT_LIMIT,
    assemble_reader,
    build_archive_entry_parts,
    build_file_part,
)

GONGWEN_PARSE_TASK_KIND = "gongwen_parse_pending"
GONGWEN_PARSE_INTERVAL_SECONDS = 150
PARSE_BATCH_SIZE = 6
PARSE_ITEM_DELAY_SECONDS = 1.0
PARSE_MAX_ATTEMPTS = 4
PARSE_STALE_MINUTES = 30
STRUCTURE_INPUT_LIMIT = 9000
# Documents published in this year or later get the full AI pipeline (fast-AI
# verify + multimodal fallback + AI structure). Older docs (≤ AI_PARSE_MIN_YEAR-1,
# i.e. 2023 and earlier) only get simple metadata + logic extraction — no AI —
# since there are far too many old documents to be worth AI analysis.
AI_PARSE_MIN_YEAR = 2024

# Internal worker has no requesting teacher — bypass visibility for parsing only.
_ADMIN_SCOPE: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _doc_year(publish_time: Any) -> int | None:
    """Parse the leading year from a publish/create timestamp like '2026-06-08 …'."""
    text = str(publish_time or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def doc_uses_ai_parse(data: dict[str, Any]) -> bool:
    """AI parsing only for docs published in AI_PARSE_MIN_YEAR or later.

    Unknown date → treat as recent (parse fully) since such docs are rare."""
    year = _doc_year(data.get("publish_time") or data.get("remote_created_at"))
    return year is None or year >= AI_PARSE_MIN_YEAR


async def _structure_with_ai(text: str) -> dict[str, str]:
    """Extract 正文标题 / 摘要 / 关键词 / 落款 from the document text (fast model)."""
    head = (text or "").strip()
    if not head:
        return {}
    if len(head) > STRUCTURE_INPUT_LIMIT:
        head = head[: STRUCTURE_INPUT_LIMIT - 800] + "\n……\n" + head[-700:]
    payload = {
        "system_prompt": (
            "你是公文信息抽取助手。从给定公文文本中抽取要点，只输出 JSON："
            "{\"title\": \"正文标题（无则留空）\", \"summary\": \"120字以内中文摘要\", "
            "\"keywords\": \"3至6个中文关键词，用逗号分隔\", "
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
    keywords = parsed.get("keywords")
    if isinstance(keywords, list):
        keywords = "，".join(str(k).strip() for k in keywords if str(k).strip())
    return {
        "title": str(parsed.get("title") or "").strip()[:300],
        "summary": str(parsed.get("summary") or "").strip()[:600],
        "keywords": str(keywords or "").strip()[:300],
        "signature": str(parsed.get("signature") or "").strip()[:300],
    }


def _assemble_from_stored(data: dict[str, Any]) -> dict[str, Any]:
    try:
        cached = json.loads(data.get("parsed_payload_json") or "{}")
    except (TypeError, ValueError):
        cached = {}
    parts = cached.get("parts") if isinstance(cached, dict) else None
    return assemble_reader(data, parts if isinstance(parts, list) else [])


async def _expand_archive(
    data: dict[str, Any],
    document_id: int,
    which: str,
    archive_part: dict[str, Any],
    *,
    use_ai: bool,
) -> list[dict[str, Any]]:
    """压缩附件 → 解压为独立"附件"条目；确认完整后删除原包、清空本地路径。"""
    extract_root = extracted_root_for(data.get("attr_school_code"), data.get("remote_id"))
    entry_parts, complete = await build_archive_entry_parts(
        document_id, which, archive_part, Path(archive_part["local_path"]), extract_root, use_ai=use_ai
    )
    if not complete:
        return entry_parts
    column = "local_file_path" if which == "primary" else "local_attachment_path"
    try:
        Path(archive_part["local_path"]).unlink(missing_ok=True)
    except OSError:
        return entry_parts  # 删不掉就保留原包，路径仍有效
    archive_part["local_path"] = ""
    archive_part["note"] = "已自动解压为附件条目，原压缩包已清理。"
    with get_db_connection() as conn:
        conn.execute(
            f"UPDATE gongwen_documents SET {column} = '', updated_at = ? WHERE id = ?",
            (_now_iso(), int(document_id)),
        )
        conn.commit()
    return entry_parts


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

    use_ai = doc_uses_ai_parse(data)
    try:
        parts: list[dict[str, Any]] = []
        source_name, source_type = "", ""
        for which, url_col in (("primary", "file_url"), ("attachment", "attachment_url")):
            url = str(data.get(url_col) or "")
            if not url:
                continue
            part = await build_file_part(_ADMIN_SCOPE, int(document_id), which, url, is_super_admin=True, use_ai=use_ai)
            parts.append(part)
            if which == "primary":
                source_name = part.get("name") or ""
                source_type = part.get("ext") or ""
            if part.get("kind") == "archive" and part.get("local_path"):
                parts.extend(await _expand_archive(data, int(document_id), which, part, use_ai=use_ai))

        text_chunks: list[str] = []
        if str(data.get("content_text") or "").strip():
            text_chunks.append(str(data["content_text"]).strip())
        for part in parts:
            if part.get("text", "").strip():
                text_chunks.append(f"【{part['label']}：{part['name']}】\n{part['text'].strip()}")
        parsed_text = "\n\n".join(text_chunks)[:PARSED_TEXT_LIMIT]

        # Old docs: logic extraction only, no AI structuring.
        struct = await _structure_with_ai(parsed_text) if (use_ai and parsed_text.strip()) else {}
        parsed_title = struct.get("title") or str(data.get("title") or "")
        parsed_summary = struct.get("summary") or str(data.get("summary") or "")
        parsed_keywords = struct.get("keywords") or str(data.get("keywords") or "")
        parsed_signature = struct.get("signature") or ""

        has_content = bool(parsed_text.strip()) or any(p.get("kind") == "image" for p in parts)
        status = "done" if (parts and has_content) else ("done" if not parts else "failed")
        error = "" if status == "done" else "未能从源文件解析出可读内容。"

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE gongwen_documents
                SET parsed_status = ?, parsed_text = ?, parsed_payload_json = ?,
                    parsed_title = ?, parsed_summary = ?, parsed_keywords = ?, parsed_signature = ?,
                    source_file_name = CASE WHEN source_file_name = '' THEN ? ELSE source_file_name END,
                    source_file_type = CASE WHEN source_file_type = '' THEN ? ELSE source_file_type END,
                    parse_error = ?, parsed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status, parsed_text, json.dumps({"parts": parts}, ensure_ascii=False)[:600_000],
                    parsed_title, parsed_summary, parsed_keywords, parsed_signature,
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


def _rebuild_parsed_text(data: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    """content_text + 各 part 文本 → AI 可读全文（与 parse_document 的聚合一致）。"""
    chunks: list[str] = []
    if str(data.get("content_text") or "").strip():
        chunks.append(str(data["content_text"]).strip())
    for part in parts:
        text = str(part.get("text") or "").strip()
        if text:
            chunks.append(f"【{part.get('label') or '附件'}：{part.get('name') or ''}】\n{text}")
    return "\n\n".join(chunks)[:PARSED_TEXT_LIMIT]


def update_gongwen_part_text(
    conn,
    teacher_scope: dict[str, str],
    document_id: int,
    part_index: int,
    text: str,
    *,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """人工校正某个 part 的解析文本（全屏编辑器保存），并重建 parsed_text。

    仅本校区教师（或超管）可改 —— 与归属编辑同一规则。"""
    from .organization_scope_service import normalize_school_code

    ensure_gongwen_schema(conn)
    row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        raise ValueError("公文不存在。")
    data = dict(row)
    if not ms.can_view(data, teacher_scope, is_super_admin=is_super_admin):
        raise ValueError("公文不存在或无权访问。")
    if not is_super_admin and normalize_school_code(data.get("attr_school_code")) != normalize_school_code(
        teacher_scope.get("school_code")
    ):
        raise ValueError("只能编辑本校区公文的解析文本。")

    try:
        cached = json.loads(data.get("parsed_payload_json") or "{}")
    except (TypeError, ValueError):
        cached = {}
    parts = cached.get("parts") if isinstance(cached, dict) else None
    if not isinstance(parts, list) or not (0 <= int(part_index) < len(parts)):
        raise ValueError("该公文尚未完成解析，暂不能编辑解析文本。")

    part = dict(parts[int(part_index)])
    part["text"] = str(text or "")[:120_000]
    part["edited"] = True
    parts = [*parts[: int(part_index)], part, *parts[int(part_index) + 1:]]
    conn.execute(
        "UPDATE gongwen_documents SET parsed_payload_json = ?, parsed_text = ?, updated_at = ? WHERE id = ?",
        (
            json.dumps({"parts": parts}, ensure_ascii=False)[:600_000],
            _rebuild_parsed_text(data, parts),
            _now_iso(),
            int(document_id),
        ),
    )
    return {k: v for k, v in part.items() if k != "local_path"}


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
        WHERE parsed_status IN ('pending', 'idle')
           OR (parsed_status = 'failed' AND parse_attempts < ?)
        ORDER BY (parsed_status IN ('pending', 'idle')) DESC, publish_time DESC, id DESC
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
        remaining = int((conn.execute("SELECT COUNT(*) AS c FROM gongwen_documents WHERE parsed_status IN ('pending', 'idle')").fetchone() or {"c": 0})["c"])
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

    # 关注匹配 worker 紧随解析 worker：解析完成的公文随即匹配教师关注并提醒。
    from .gongwen_follow_service import schedule_gongwen_follow_worker

    schedule_gongwen_follow_worker(conn)

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
    row = conn.execute("SELECT COUNT(*) AS c FROM gongwen_documents WHERE parsed_status IN ('pending', 'idle', 'parsing')").fetchone()
    return int(dict(row)["c"]) if row else 0
