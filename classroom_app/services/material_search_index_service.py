"""Material knowledge index: keeps ``material_search_chunks`` in step with
``course_materials`` so the AI (and the global search box) can answer
"这个知识点在哪个文档的哪一页".

Who writes the index
--------------------
- The unified scheduler runs ``material_search_index`` every
  ``INDEX_SWEEP_SECONDS`` (``ensure_material_search_index_task`` arms it at
  app startup). Each run indexes up to ``INDEX_BATCH_SIZE`` stale files within
  a byte budget and purges rows whose material was deleted, then stops. Big
  libraries converge over a few runs without ever blocking a request.
- A stale file = no document row, or the row's ``file_hash`` / ``updated_at``
  / ``extractor_version`` differs from the live material. Unsupported / empty /
  failed outcomes are recorded too, so the same file is not re-read on every
  sweep until it actually changes.
- ``request_material_reindex`` (super admin, or a tool) forgets the recorded
  outcome so the next sweep re-reads everything (or the given ids).

Every write is one material per transaction; a crash mid-batch loses nothing
already committed. Readers live in ``material_knowledge_search_service``.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Iterable

from ..database import get_db_connection
from .material_search_schema import ensure_material_search_schema
from .material_text_extraction_service import (
    EXTRACTOR_VERSION,
    STATUS_FAILED,
    STATUS_READY,
    MaterialExtraction,
    extract_material_chunks,
)

TASK_KIND_MATERIAL_SEARCH_INDEX = "material_search_index"
INDEX_SWEEP_DEDUPE_KEY = "material-search:index-sweep"


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


INDEX_SWEEP_SECONDS = _env_int("LANSHARE_MATERIAL_INDEX_SWEEP_SECONDS", 120)
INDEX_BATCH_SIZE = _env_int("LANSHARE_MATERIAL_INDEX_BATCH_SIZE", 24)
INDEX_BATCH_BYTES = _env_int("LANSHARE_MATERIAL_INDEX_BATCH_MB", 96) * 1024 * 1024

_STALE_WHERE = """
    m.node_type = 'file'
    AND m.material_path NOT LIKE '%/.git/%'
    AND (
        d.material_id IS NULL
        OR COALESCE(d.file_hash, '') <> COALESCE(m.file_hash, '')
        OR COALESCE(d.source_updated_at, '') <> COALESCE(m.updated_at, '')
        OR COALESCE(d.extractor_version, 0) <> ?
    )
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# single material
# --------------------------------------------------------------------------
def index_material(conn: Any, material_row: Any) -> dict[str, Any]:
    """Re-extract one file and replace its chunks. Does not commit."""
    ensure_material_search_schema(conn)
    material = dict(material_row)
    material_id = int(material["id"])
    extraction: MaterialExtraction = extract_material_chunks(conn, material)
    conn.execute("DELETE FROM material_search_chunks WHERE material_id = ?", (material_id,))
    conn.execute("DELETE FROM material_search_documents WHERE material_id = ?", (material_id,))
    for chunk in extraction.chunks:
        conn.execute(
            """
            INSERT INTO material_search_chunks
                (material_id, ordinal, locator_kind, locator_label, locator_url, page_number, anchor, heading, text, text_lower)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                material_id,
                int(chunk.ordinal),
                chunk.locator_kind[:40],
                chunk.locator_label[:200],
                chunk.locator_url[:600],
                int(chunk.page_number or 0),
                chunk.anchor[:200],
                chunk.heading[:200],
                chunk.text,
                chunk.text.lower(),
            ),
        )
    conn.execute(
        """
        INSERT INTO material_search_documents
            (material_id, teacher_id, root_id, file_hash, source_updated_at, extractor_version, status,
             locator_kind, kind, chunk_count, page_count, char_count, error_text, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            int(material.get("teacher_id") or 0),
            material.get("root_id"),
            str(material.get("file_hash") or ""),
            str(material.get("updated_at") or ""),
            EXTRACTOR_VERSION,
            extraction.status,
            extraction.locator_kind,
            extraction.kind,
            len(extraction.chunks),
            int(extraction.page_count or 0),
            extraction.char_count,
            str(extraction.error or "")[:500],
            _now_iso(),
        ),
    )
    return {
        "material_id": material_id,
        "status": extraction.status,
        "kind": extraction.kind,
        "chunk_count": len(extraction.chunks),
        "page_count": extraction.page_count,
        "error": extraction.error,
    }


def index_material_by_id(conn: Any, material_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM course_materials WHERE id = ? LIMIT 1", (int(material_id),)).fetchone()
    if not row:
        return None
    return index_material(conn, row)


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------
def select_stale_material_ids(conn: Any, *, limit: int = INDEX_BATCH_SIZE) -> list[dict[str, Any]]:
    ensure_material_search_schema(conn)
    rows = conn.execute(
        f"""
        SELECT m.id, COALESCE(m.file_size, 0) AS file_size
        FROM course_materials m
        LEFT JOIN material_search_documents d ON d.material_id = m.id
        WHERE {_STALE_WHERE}
        ORDER BY m.updated_at DESC, m.id DESC
        LIMIT ?
        """,
        (EXTRACTOR_VERSION, int(limit)),
    ).fetchall()
    return [{"id": int(row["id"]), "file_size": int(row["file_size"] or 0)} for row in rows]


def count_stale_materials(conn: Any) -> int:
    ensure_material_search_schema(conn)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM course_materials m
        LEFT JOIN material_search_documents d ON d.material_id = m.id
        WHERE {_STALE_WHERE}
        """,
        (EXTRACTOR_VERSION,),
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def purge_orphan_index_rows(conn: Any) -> int:
    """Drop index rows whose material no longer exists. Does not commit."""
    ensure_material_search_schema(conn)
    cursor = conn.execute(
        "DELETE FROM material_search_chunks WHERE NOT EXISTS (SELECT 1 FROM course_materials m WHERE m.id = material_search_chunks.material_id)"
    )
    removed = int(cursor.rowcount or 0) if cursor is not None else 0
    conn.execute(
        "DELETE FROM material_search_documents WHERE NOT EXISTS (SELECT 1 FROM course_materials m WHERE m.id = material_search_documents.material_id)"
    )
    return removed


def reindex_stale_materials(*, limit: int = INDEX_BATCH_SIZE, byte_budget: int = INDEX_BATCH_BYTES) -> dict[str, Any]:
    """One sweep: index up to ``limit`` stale files (within ``byte_budget``), purge orphans."""
    summary: dict[str, Any] = {"indexed": 0, "ready": 0, "skipped": 0, "failed": 0, "purged": 0, "remaining": 0}
    with get_db_connection() as conn:
        candidates = select_stale_material_ids(conn, limit=limit)
        # Housekeeping only on quiet sweeps (nothing stale), so a catching-up index
        # never pays for a whole-table orphan scan on every run.
        if not candidates:
            try:
                summary["purged"] = purge_orphan_index_rows(conn)
                conn.commit()
            except Exception as exc:  # noqa: BLE001 — purge is housekeeping, never blocks indexing
                conn.rollback()
                print(f"[MATERIAL-INDEX] purge skipped: {exc}")
    consumed = 0
    for candidate in candidates:
        if summary["indexed"] and consumed + candidate["file_size"] > byte_budget:
            break
        consumed += candidate["file_size"]
        with get_db_connection() as conn:
            try:
                result = index_material_by_id(conn, candidate["id"])
                conn.commit()
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the sweep
                conn.rollback()
                print(f"[MATERIAL-INDEX] material {candidate['id']} failed: {exc}")
                summary["failed"] += 1
                _record_failure(candidate["id"], f"{type(exc).__name__}: {str(exc)[:200]}")
                continue
        if result is None:
            summary["skipped"] += 1
            continue
        summary["indexed"] += 1
        if result["status"] == STATUS_READY:
            summary["ready"] += 1
        elif result["status"] == STATUS_FAILED:
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
    with get_db_connection() as conn:
        summary["remaining"] = count_stale_materials(conn)
    return summary


def _record_failure(material_id: int, error: str) -> None:
    """Pin a failed outcome so a crashing file is not retried on every sweep."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, teacher_id, root_id, file_hash, updated_at FROM course_materials WHERE id = ? LIMIT 1",
                (int(material_id),),
            ).fetchone()
            if not row:
                return
            ensure_material_search_schema(conn)
            conn.execute("DELETE FROM material_search_documents WHERE material_id = ?", (int(material_id),))
            conn.execute(
                """
                INSERT INTO material_search_documents
                    (material_id, teacher_id, root_id, file_hash, source_updated_at, extractor_version, status, error_text, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["id"]),
                    int(row["teacher_id"] or 0),
                    row["root_id"],
                    str(row["file_hash"] or ""),
                    str(row["updated_at"] or ""),
                    EXTRACTOR_VERSION,
                    STATUS_FAILED,
                    error[:500],
                    _now_iso(),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[MATERIAL-INDEX] could not record failure for {material_id}: {exc}")


# --------------------------------------------------------------------------
# scheduler wiring
# --------------------------------------------------------------------------
def ensure_material_search_index_task(conn: Any) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=TASK_KIND_MATERIAL_SEARCH_INDEX,
        run_at=datetime.now() + timedelta(seconds=15),
        payload={},
        dedupe_key=INDEX_SWEEP_DEDUPE_KEY,
        recurrence_seconds=INDEX_SWEEP_SECONDS,
        title="Index course materials for knowledge search",
        priority=80,
        max_attempts=3,
        replace=True,
    )


def handle_material_search_index_task(task: dict[str, Any]) -> str:
    payload = task.get("payload") if isinstance(task, dict) else None
    limit = INDEX_BATCH_SIZE
    if isinstance(payload, dict):
        try:
            limit = max(1, min(200, int(payload.get("limit") or INDEX_BATCH_SIZE)))
        except (TypeError, ValueError):
            limit = INDEX_BATCH_SIZE
    summary = reindex_stale_materials(limit=limit)
    return (
        f"indexed={summary['indexed']} ready={summary['ready']} skipped={summary['skipped']} "
        f"failed={summary['failed']} purged={summary['purged']} remaining={summary['remaining']}"
    )


def request_material_reindex(conn: Any, material_ids: Iterable[int] | None = None) -> int:
    """Forget recorded outcomes so the next sweep re-reads the files. Does not commit."""
    ensure_material_search_schema(conn)
    if material_ids is None:
        cursor = conn.execute("DELETE FROM material_search_documents")
    else:
        ids = sorted({int(value) for value in material_ids if int(value) > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = conn.execute(f"DELETE FROM material_search_documents WHERE material_id IN ({placeholders})", ids)
    try:
        from .scheduled_task_service import schedule_task

        schedule_task(
            conn,
            task_kind=TASK_KIND_MATERIAL_SEARCH_INDEX,
            run_at=datetime.now(),
            payload={"limit": INDEX_BATCH_SIZE},
            dedupe_key=INDEX_SWEEP_DEDUPE_KEY,
            recurrence_seconds=INDEX_SWEEP_SECONDS,
            title="Index course materials for knowledge search",
            priority=80,
            max_attempts=3,
            replace=True,
        )
    except Exception as exc:  # noqa: BLE001 — the recurring sweep will pick it up anyway
        print(f"[MATERIAL-INDEX] immediate sweep not armed: {exc}")
    return int(cursor.rowcount or 0) if cursor is not None else 0


# --------------------------------------------------------------------------
# readers used by other services
# --------------------------------------------------------------------------
def index_status_snapshot(conn: Any) -> dict[str, Any]:
    ensure_material_search_schema(conn)
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n, COALESCE(SUM(chunk_count), 0) AS chunks FROM material_search_documents GROUP BY status"
    ).fetchall()
    by_status = {str(row["status"]): {"documents": int(row["n"] or 0), "chunks": int(row["chunks"] or 0)} for row in rows}
    latest = conn.execute("SELECT MAX(indexed_at) AS latest FROM material_search_documents").fetchone()
    total_files = conn.execute(
        "SELECT COUNT(*) AS n FROM course_materials m WHERE m.node_type = 'file' AND m.material_path NOT LIKE '%/.git/%'"
    ).fetchone()
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "sweep_seconds": INDEX_SWEEP_SECONDS,
        "batch_size": INDEX_BATCH_SIZE,
        "total_files": int(total_files["n"] or 0) if total_files else 0,
        "by_status": by_status,
        "indexed_documents": sum(item["documents"] for item in by_status.values()),
        "indexed_chunks": sum(item["chunks"] for item in by_status.values()),
        "stale": count_stale_materials(conn),
        "last_indexed_at": str(latest["latest"] or "") if latest else "",
    }


def load_indexed_material_text(conn: Any, material_id: int, *, max_chars: int = 8000) -> str:
    """Join a material's indexed chunks (with locator labels) for prompt injection."""
    ensure_material_search_schema(conn)
    rows = conn.execute(
        """
        SELECT locator_label, text FROM material_search_chunks
        WHERE material_id = ? ORDER BY ordinal ASC
        """,
        (int(material_id),),
    ).fetchall()
    parts: list[str] = []
    used = 0
    last_label = None
    for row in rows:
        label = str(row["locator_label"] or "")
        text = str(row["text"] or "")
        if not text:
            continue
        piece = text if label == last_label else f"【{label}】\n{text}"
        last_label = label
        if used + len(piece) > max_chars:
            remaining = max_chars - used
            if remaining > 80:
                parts.append(piece[:remaining].rstrip() + "\n[内容已截断]")
            break
        parts.append(piece)
        used += len(piece)
    return "\n\n".join(parts)
