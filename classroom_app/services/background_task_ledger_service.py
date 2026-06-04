from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable

from ..config import (
    AGENT_TASK_MAX_RUNTIME_SECONDS,
    AI_GRADING_STALE_MINUTES,
    EMAIL_WORKER_HEARTBEAT_TIMEOUT_SECONDS,
)
from ..database import get_db_connection
from .background_task_registry_service import BACKGROUND_TASK_DEFINITIONS, BackgroundTaskDefinition
from .behavior_tracking_service import get_behavior_write_pipeline_stats


MATERIAL_AI_IMPORT_STALE_MINUTES_FALLBACK = 45
SESSION_MATERIAL_GENERATION_STALE_MINUTES = 90
PRIVATE_MESSAGE_AI_REPLY_STALE_MINUTES = 15
BLOG_NEWS_CRAWLER_HEARTBEAT_STALE_SECONDS = 300
MAX_ERROR_CHARS = 260
MAX_WORKER_IDS = 6

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|bearer|cookie|password|passwd|secret|token)\b\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{8,})\b"),
)


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _cutoff(minutes: int | float = 0, seconds: int | float = 0) -> str:
    return (_now() - timedelta(minutes=float(minutes), seconds=float(seconds))).isoformat(timespec="seconds")


def _sanitize_text(value: Any, *, limit: int = MAX_ERROR_CHARS) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if not text:
        return ""
    text = _SECRET_PATTERNS[0].sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _SECRET_PATTERNS[1].sub("[REDACTED]", text)
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _safe_worker_id(value: Any) -> str:
    text = _sanitize_text(value, limit=64)
    if not text:
        return ""
    return re.sub(r"[^A-Za-z0-9_.:@/\-]+", "_", text)[:64]


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(_row_dict(row).get("name") or row[1]) == column_name for row in rows)


def _count_status(conn: sqlite3.Connection, table_name: str, status_column: str, statuses: tuple[str, ...]) -> int:
    if not statuses:
        return 0
    placeholders = ",".join("?" for _ in statuses)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table_name} WHERE {status_column} IN ({placeholders})",
        statuses,
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _oldest_time(
    conn: sqlite3.Connection,
    table_name: str,
    status_column: str,
    statuses: tuple[str, ...],
    time_column: str,
) -> str:
    if not statuses:
        return ""
    placeholders = ",".join("?" for _ in statuses)
    row = conn.execute(
        f"""
        SELECT MIN({time_column})
        FROM {table_name}
        WHERE {status_column} IN ({placeholders})
        """,
        statuses,
    ).fetchone()
    return str((row[0] if row else "") or "")


def _latest_error(
    conn: sqlite3.Connection,
    table_name: str,
    *,
    error_column: str,
    time_column: str,
    status_column: str | None = None,
    statuses: tuple[str, ...] = (),
) -> tuple[str, str]:
    where = f"{error_column} IS NOT NULL AND TRIM({error_column}) <> ''"
    params: list[Any] = []
    if status_column and statuses:
        where += f" AND {status_column} IN ({','.join('?' for _ in statuses)})"
        params.extend(statuses)
    row = conn.execute(
        f"""
        SELECT {error_column} AS error_message, {time_column} AS error_at
        FROM {table_name}
        WHERE {where}
        ORDER BY {time_column} DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    data = _row_dict(row)
    return str(data.get("error_at") or ""), _sanitize_text(data.get("error_message"))


def _base_item(definition: BackgroundTaskDefinition) -> dict[str, Any]:
    return {
        **definition.to_dict(),
        "queue_depth": 0,
        "running_count": 0,
        "failed_count": 0,
        "stale_count": 0,
        "active_worker_count": 0,
        "worker_ids": [],
        "last_heartbeat_at": "",
        "last_error_at": "",
        "last_error": "",
        "oldest_queued_at": "",
        "status": "ok",
        "notes": "",
    }


def _missing_source_item(definition: BackgroundTaskDefinition, source_table: str) -> dict[str, Any]:
    item = _base_item(definition)
    item["status"] = "missing_source"
    item["notes"] = f"source table not found: {source_table}"
    return item


def _material_import_stale_minutes() -> int:
    try:
        from ..routers.materials_parts.common import MATERIAL_AI_IMPORT_STALE_MINUTES

        return int(MATERIAL_AI_IMPORT_STALE_MINUTES)
    except Exception:
        return MATERIAL_AI_IMPORT_STALE_MINUTES_FALLBACK


def _material_import_runtime_snapshot() -> dict[str, Any]:
    try:
        from ..routers.materials_parts import common as materials_common

        queue = getattr(materials_common, "_material_ai_import_queue", None)
        worker_tasks = list(getattr(materials_common, "_material_ai_import_worker_tasks", []) or [])
        worker_ids = [f"material-import-{index + 1}" for index, task in enumerate(worker_tasks) if not task.done()]
        return {
            "queue_size": int(queue.qsize()) if queue is not None else 0,
            "active_worker_count": len(worker_ids),
            "worker_ids": worker_ids[:MAX_WORKER_IDS],
        }
    except Exception:
        return {"queue_size": 0, "active_worker_count": 0, "worker_ids": []}


def _build_ai_grading_item(conn: sqlite3.Connection, definition: BackgroundTaskDefinition) -> dict[str, Any]:
    if not _table_exists(conn, "submissions"):
        return _missing_source_item(definition, "submissions")
    item = _base_item(definition)
    started_column = "grading_started_at" if _column_exists(conn, "submissions", "grading_started_at") else "submitted_at"
    item["running_count"] = _count_status(conn, "submissions", "status", ("grading",))
    item["failed_count"] = _count_status(conn, "submissions", "status", ("grading_failed",))
    item["stale_count"] = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM submissions
            WHERE status = 'grading'
              AND COALESCE({started_column}, submitted_at, '') <> ''
              AND COALESCE({started_column}, submitted_at) < ?
            """,
            (_cutoff(minutes=AI_GRADING_STALE_MINUTES),),
        ).fetchone()[0]
        or 0
    )
    item["oldest_queued_at"] = _oldest_time(conn, "submissions", "status", ("grading",), started_column)
    if _column_exists(conn, "submissions", "feedback_md"):
        item["last_error_at"], item["last_error"] = _latest_error(
            conn,
            "submissions",
            error_column="feedback_md",
            time_column="updated_at" if _column_exists(conn, "submissions", "updated_at") else started_column,
            status_column="status",
            statuses=("grading_failed",),
        )
    return item


def _build_material_ai_import_item(conn: sqlite3.Connection, definition: BackgroundTaskDefinition) -> dict[str, Any]:
    if not _table_exists(conn, "material_ai_import_records"):
        return _missing_source_item(definition, "material_ai_import_records")
    item = _base_item(definition)
    item["queue_depth"] = _count_status(conn, "material_ai_import_records", "parse_status", ("queued",))
    item["running_count"] = _count_status(conn, "material_ai_import_records", "parse_status", ("running",))
    item["failed_count"] = _count_status(
        conn,
        "material_ai_import_records",
        "parse_status",
        ("failed", "ai_failed", "quality_failed", "unsupported"),
    )
    item["stale_count"] = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM material_ai_import_records
            WHERE parse_status = 'running'
              AND COALESCE(updated_at, started_at, created_at, '') <> ''
              AND COALESCE(updated_at, started_at, created_at) < ?
            """,
            (_cutoff(minutes=_material_import_stale_minutes()),),
        ).fetchone()[0]
        or 0
    )
    item["oldest_queued_at"] = _oldest_time(conn, "material_ai_import_records", "parse_status", ("queued",), "created_at")
    item["last_error_at"], item["last_error"] = _latest_error(
        conn,
        "material_ai_import_records",
        error_column="error_message",
        time_column="updated_at",
        status_column="parse_status",
        statuses=("failed", "ai_failed", "quality_failed", "unsupported"),
    )
    runtime = _material_import_runtime_snapshot()
    item["active_worker_count"] = int(runtime.get("active_worker_count") or 0)
    item["worker_ids"] = list(runtime.get("worker_ids") or [])
    if runtime.get("queue_size"):
        item["runtime_queue_depth"] = int(runtime["queue_size"])
    return item


def _build_session_material_generation_item(
    conn: sqlite3.Connection,
    definition: BackgroundTaskDefinition,
) -> dict[str, Any]:
    if not _table_exists(conn, "session_material_generation_tasks"):
        return _missing_source_item(definition, "session_material_generation_tasks")
    item = _base_item(definition)
    item["queue_depth"] = _count_status(conn, "session_material_generation_tasks", "status", ("queued",))
    item["running_count"] = _count_status(conn, "session_material_generation_tasks", "status", ("running",))
    item["failed_count"] = _count_status(conn, "session_material_generation_tasks", "status", ("failed",))
    item["stale_count"] = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM session_material_generation_tasks
            WHERE status = 'running'
              AND COALESCE(updated_at, started_at, created_at, '') <> ''
              AND COALESCE(updated_at, started_at, created_at) < ?
            """,
            (_cutoff(minutes=SESSION_MATERIAL_GENERATION_STALE_MINUTES),),
        ).fetchone()[0]
        or 0
    )
    item["oldest_queued_at"] = _oldest_time(
        conn,
        "session_material_generation_tasks",
        "status",
        ("queued",),
        "created_at",
    )
    item["last_error_at"], item["last_error"] = _latest_error(
        conn,
        "session_material_generation_tasks",
        error_column="error_message",
        time_column="updated_at",
        status_column="status",
        statuses=("failed",),
    )
    return item


def _build_private_message_ai_reply_item(conn: sqlite3.Connection, definition: BackgroundTaskDefinition) -> dict[str, Any]:
    if not _table_exists(conn, "private_message_ai_jobs"):
        return _missing_source_item(definition, "private_message_ai_jobs")
    item = _base_item(definition)
    item["queue_depth"] = _count_status(conn, "private_message_ai_jobs", "status", ("pending",))
    item["running_count"] = _count_status(conn, "private_message_ai_jobs", "status", ("running",))
    item["failed_count"] = _count_status(conn, "private_message_ai_jobs", "status", ("failed",))
    item["stale_count"] = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM private_message_ai_jobs
            WHERE status = 'running'
              AND COALESCE(updated_at, started_at, created_at, '') <> ''
              AND COALESCE(updated_at, started_at, created_at) < ?
            """,
            (_cutoff(minutes=PRIVATE_MESSAGE_AI_REPLY_STALE_MINUTES),),
        ).fetchone()[0]
        or 0
    )
    item["oldest_queued_at"] = _oldest_time(conn, "private_message_ai_jobs", "status", ("pending",), "created_at")
    item["last_error_at"], item["last_error"] = _latest_error(
        conn,
        "private_message_ai_jobs",
        error_column="error_message",
        time_column="updated_at",
        status_column="status",
        statuses=("failed",),
    )
    return item


def _build_email_outbox_item(conn: sqlite3.Connection, definition: BackgroundTaskDefinition) -> dict[str, Any]:
    if not _table_exists(conn, "email_outbox"):
        return _missing_source_item(definition, "email_outbox")
    item = _base_item(definition)
    item["queue_depth"] = _count_status(conn, "email_outbox", "status", ("queued",))
    item["running_count"] = _count_status(conn, "email_outbox", "status", ("sending",))
    item["failed_count"] = _count_status(conn, "email_outbox", "status", ("failed",))
    item["oldest_queued_at"] = _oldest_time(conn, "email_outbox", "status", ("queued",), "created_at")
    item["last_error_at"], item["last_error"] = _latest_error(
        conn,
        "email_outbox",
        error_column="last_error",
        time_column="updated_at",
        status_column="status",
        statuses=("failed", "queued", "sending"),
    )
    if _table_exists(conn, "email_worker_heartbeats"):
        heartbeat_cutoff = _cutoff(seconds=EMAIL_WORKER_HEARTBEAT_TIMEOUT_SECONDS)
        rows = conn.execute(
            """
            SELECT worker_id, updated_at, last_error
            FROM email_worker_heartbeats
            WHERE updated_at >= ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (heartbeat_cutoff, MAX_WORKER_IDS),
        ).fetchall()
        workers = [_safe_worker_id(_row_dict(row).get("worker_id")) for row in rows]
        item["worker_ids"] = [worker for worker in workers if worker]
        item["active_worker_count"] = len(item["worker_ids"])
        latest = conn.execute(
            "SELECT updated_at, last_error FROM email_worker_heartbeats ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        latest_data = _row_dict(latest)
        item["last_heartbeat_at"] = str(latest_data.get("updated_at") or "")
        heartbeat_error = _sanitize_text(latest_data.get("last_error"))
        if heartbeat_error and not item["last_error"]:
            item["last_error"] = heartbeat_error
            item["last_error_at"] = item["last_heartbeat_at"]
    return item


def _build_blog_news_crawler_item(conn: sqlite3.Connection, definition: BackgroundTaskDefinition) -> dict[str, Any]:
    if not _table_exists(conn, "blog_news_crawler_runs"):
        return _missing_source_item(definition, "blog_news_crawler_runs")
    item = _base_item(definition)
    item["queue_depth"] = _count_status(conn, "blog_news_crawler_runs", "status", ("pending",))
    item["running_count"] = _count_status(conn, "blog_news_crawler_runs", "status", ("running",))
    item["failed_count"] = _count_status(conn, "blog_news_crawler_runs", "status", ("failed",))
    item["stale_count"] = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM blog_news_crawler_runs
            WHERE status = 'running'
              AND COALESCE(updated_at, started_at, created_at, '') <> ''
              AND COALESCE(updated_at, started_at, created_at) < ?
            """,
            (_cutoff(seconds=BLOG_NEWS_CRAWLER_HEARTBEAT_STALE_SECONDS),),
        ).fetchone()[0]
        or 0
    )
    item["oldest_queued_at"] = _oldest_time(conn, "blog_news_crawler_runs", "status", ("pending",), "created_at")
    item["last_error_at"], item["last_error"] = _latest_error(
        conn,
        "blog_news_crawler_runs",
        error_column="error_message",
        time_column="updated_at",
        status_column="status",
        statuses=("failed", "running"),
    )
    if _table_exists(conn, "blog_news_crawler_config"):
        row = conn.execute(
            "SELECT worker_id, worker_status, last_heartbeat_at FROM blog_news_crawler_config WHERE id = 1"
        ).fetchone()
        data = _row_dict(row)
        item["last_heartbeat_at"] = str(data.get("last_heartbeat_at") or "")
        heartbeat_at = _parse_dt(item["last_heartbeat_at"])
        worker_id = _safe_worker_id(data.get("worker_id"))
        if heartbeat_at and (_now() - heartbeat_at).total_seconds() <= BLOG_NEWS_CRAWLER_HEARTBEAT_STALE_SECONDS:
            item["active_worker_count"] = 1 if worker_id else 0
            item["worker_ids"] = [worker_id] if worker_id else []
        status_text = _sanitize_text(data.get("worker_status"))
        if status_text.lower().startswith("error") and not item["last_error"]:
            item["last_error"] = status_text
            item["last_error_at"] = item["last_heartbeat_at"]
    return item


def _build_agent_task_item(conn: sqlite3.Connection, definition: BackgroundTaskDefinition) -> dict[str, Any]:
    if not _table_exists(conn, "agent_tasks"):
        return _missing_source_item(definition, "agent_tasks")
    item = _base_item(definition)
    item["queue_depth"] = _count_status(conn, "agent_tasks", "status", ("queued",))
    item["running_count"] = _count_status(conn, "agent_tasks", "status", ("running",))
    item["failed_count"] = _count_status(conn, "agent_tasks", "status", ("failed",))
    item["stale_count"] = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_tasks
            WHERE status = 'running'
              AND COALESCE(updated_at, started_at, created_at, '') <> ''
              AND COALESCE(updated_at, started_at, created_at) < ?
            """,
            (_cutoff(seconds=AGENT_TASK_MAX_RUNTIME_SECONDS),),
        ).fetchone()[0]
        or 0
    )
    item["oldest_queued_at"] = _oldest_time(conn, "agent_tasks", "status", ("queued",), "created_at")
    item["last_error_at"], item["last_error"] = _latest_error(
        conn,
        "agent_tasks",
        error_column="error_message",
        time_column="updated_at",
        status_column="status",
        statuses=("failed", "running"),
    )
    rows = conn.execute(
        """
        SELECT DISTINCT worker_id
        FROM agent_tasks
        WHERE status = 'running' AND COALESCE(worker_id, '') <> ''
        ORDER BY worker_id
        LIMIT ?
        """,
        (MAX_WORKER_IDS,),
    ).fetchall()
    workers = [_safe_worker_id(_row_dict(row).get("worker_id")) for row in rows]
    item["worker_ids"] = [worker for worker in workers if worker]
    item["active_worker_count"] = len(item["worker_ids"])
    latest_running = conn.execute(
        """
        SELECT updated_at
        FROM agent_tasks
        WHERE status = 'running' AND COALESCE(worker_id, '') <> ''
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    item["last_heartbeat_at"] = str(_row_dict(latest_running).get("updated_at") or "")
    return item


def _build_behavior_write_pipeline_item(
    definition: BackgroundTaskDefinition,
    behavior_stats_provider: Callable[[], dict[str, Any]] | None,
) -> dict[str, Any]:
    item = _base_item(definition)
    provider = behavior_stats_provider or get_behavior_write_pipeline_stats
    try:
        stats = provider()
    except Exception as exc:
        item["status"] = "error"
        item["last_error"] = _sanitize_text(exc)
        return item
    item["queue_depth"] = int(stats.get("queue_depth") or 0)
    item["active_worker_count"] = 1 if stats.get("alive") else 0
    item["worker_ids"] = ["behavior-write-pipeline"] if stats.get("alive") else []
    item["queue_capacity"] = int(stats.get("queue_capacity") or 0)
    item["dropped_count"] = int(stats.get("dropped_count") or 0) if "dropped_count" in stats else 0
    item["status"] = "ok" if stats.get("alive") else "worker_stopped"
    return item


_DB_BUILDERS: dict[str, Callable[[sqlite3.Connection, BackgroundTaskDefinition], dict[str, Any]]] = {
    "ai_grading": _build_ai_grading_item,
    "material_ai_import": _build_material_ai_import_item,
    "session_material_generation": _build_session_material_generation_item,
    "private_message_ai_reply": _build_private_message_ai_reply_item,
    "email_outbox": _build_email_outbox_item,
    "blog_news_crawler": _build_blog_news_crawler_item,
    "agent_task": _build_agent_task_item,
}


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("queue_depth", "running_count", "failed_count", "stale_count", "active_worker_count"):
        item[key] = int(item.get(key) or 0)
    item["worker_ids"] = [
        worker
        for worker in (_safe_worker_id(worker) for worker in list(item.get("worker_ids") or [])[:MAX_WORKER_IDS])
        if worker
    ]
    item["last_error"] = _sanitize_text(item.get("last_error"))
    return item


def _build_items(
    conn: sqlite3.Connection,
    *,
    behavior_stats_provider: Callable[[], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for definition in BACKGROUND_TASK_DEFINITIONS:
        try:
            if definition.task_type == "behavior_write_pipeline":
                item = _build_behavior_write_pipeline_item(definition, behavior_stats_provider)
            else:
                item = _DB_BUILDERS[definition.task_type](conn, definition)
        except Exception as exc:
            item = _base_item(definition)
            item["status"] = "error"
            item["last_error"] = _sanitize_text(exc)
        items.append(_normalize_item(item))
    return items


def _summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_type_count": len(items),
        "total_queue_depth": sum(int(item.get("queue_depth") or 0) for item in items),
        "total_running_count": sum(int(item.get("running_count") or 0) for item in items),
        "total_failed_count": sum(int(item.get("failed_count") or 0) for item in items),
        "total_stale_count": sum(int(item.get("stale_count") or 0) for item in items),
        "active_worker_count": sum(int(item.get("active_worker_count") or 0) for item in items),
        "problem_task_types": [
            item["task_type"]
            for item in items
            if int(item.get("failed_count") or 0) > 0
            or int(item.get("stale_count") or 0) > 0
            or item.get("status") in {"error", "worker_stopped"}
        ],
    }


def build_background_task_ledger_snapshot(
    conn: sqlite3.Connection | None = None,
    *,
    include_internal_details: bool = True,
    behavior_stats_provider: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    owns_connection = conn is None
    active_conn = conn or get_db_connection()
    try:
        items = _build_items(active_conn, behavior_stats_provider=behavior_stats_provider)
        snapshot = {
            "generated_at": _now_iso(),
            "summary": _summarize_items(items),
            "items": items,
        }
        if include_internal_details:
            snapshot["definitions"] = [definition.to_dict() for definition in BACKGROUND_TASK_DEFINITIONS]
        return snapshot
    finally:
        if owns_connection:
            active_conn.close()


def build_background_task_health_summary(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    try:
        snapshot = build_background_task_ledger_snapshot(
            conn,
            include_internal_details=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "generated_at": _now_iso(),
            "summary": {
                "task_type_count": 0,
                "total_queue_depth": 0,
                "total_running_count": 0,
                "total_failed_count": 0,
                "total_stale_count": 0,
                "active_worker_count": 0,
                "problem_task_types": ["background_task_ledger"],
            },
            "items": [],
            "last_error": _sanitize_text(exc),
        }

    summary = snapshot["summary"]
    return {
        "ok": not summary.get("problem_task_types"),
        "generated_at": snapshot["generated_at"],
        "summary": summary,
        "items": [
            {
                "task_type": item["task_type"],
                "display_name": item["display_name"],
                "queue_depth": item["queue_depth"],
                "running_count": item["running_count"],
                "failed_count": item["failed_count"],
                "stale_count": item["stale_count"],
                "active_worker_count": item["active_worker_count"],
                "status": item["status"],
            }
            for item in snapshot["items"]
        ],
    }
