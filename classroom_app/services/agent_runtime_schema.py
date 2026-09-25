"""Runtime tables for the Agents-SDK Agent runtime (engine-aware, idempotent).

- ``agent_queue_controls``: global queue switches owned by super admins
  (currently ``queue_paused``). Key/value so new switches need no DDL.
- ``agent_run_states``: one row per task that has started at least once. It
  holds the model conversation needed to *resume* a parked task (after a
  question, or after a pause), the pending question, and per-task safety
  counters used by the danger guard.

Kept out of ``classroom_app/db`` on purpose (see AI/Agent standard §7): these
are runtime-owned tables, created lazily with ``CREATE TABLE IF NOT EXISTS``.

``ensure_agent_runtime_schema`` never commits and never trusts a process-wide
"ready" flag: it runs inside the caller's transaction (so it cannot release a
row lock mid-operation) and stays correct when the database is swapped, e.g.
by tests or a restore. Both statements are no-ops once the tables exist.
"""
from __future__ import annotations

from typing import Any

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent_queue_controls (
        control_key TEXT PRIMARY KEY,
        control_value TEXT NOT NULL DEFAULT '',
        updated_by INTEGER,
        updated_by_name TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_run_states (
        task_id INTEGER PRIMARY KEY,
        history_json TEXT NOT NULL DEFAULT '[]',
        pending_question_json TEXT NOT NULL DEFAULT '',
        answer_json TEXT NOT NULL DEFAULT '',
        pause_requested INTEGER NOT NULL DEFAULT 0,
        pause_requested_by TEXT NOT NULL DEFAULT '',
        segments INTEGER NOT NULL DEFAULT 0,
        destructive_count INTEGER NOT NULL DEFAULT 0,
        questions_asked INTEGER NOT NULL DEFAULT 0,
        usage_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT ''
    )
    """,
)


def ensure_agent_runtime_schema(conn: Any) -> None:
    for statement in _DDL:
        conn.execute(statement)
