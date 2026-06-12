"""Port the SQLite performance indexes onto PostgreSQL.

The SQLite schema (``schema_*.py``) defines ~240 indexes, but the PostgreSQL
bring-up only *validates* the schema and never created the matching
performance indexes. On a busy deployment (target: 200 concurrent users) the
missing indexes turn hot lookups into sequential scans, which is the dominant
PostgreSQL performance regression after the SQLite -> PostgreSQL migration.

Rather than hand-maintaining a second copy of every index, we harvest the
canonical index DDL from an in-memory SQLite build of the real schema and adapt
each statement to PostgreSQL. Only non-unique (performance) indexes are ported;
unique constraints are managed separately by ``ensure_postgres_runtime_constraints``
so we never risk failing a unique build against live duplicate data.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

_COLLATE_NOCASE_RE = re.compile(r"\s+COLLATE\s+NOCASE", re.IGNORECASE)
_CLOSING_WHERE_RE = re.compile(r"\)\s*WHERE\b", re.IGNORECASE)
_CREATE_INDEX_PREFIX_RE = re.compile(
    r"^\s*CREATE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS)",
    re.IGNORECASE,
)


def _build_in_memory_sqlite_schema() -> sqlite3.Connection:
    # Imported lazily so importing this module never drags in the SQLite schema
    # builders unless we actually harvest.
    from .schema_assignments import ensure_assignment_schema
    from .schema_classroom_activity import ensure_classroom_activity_schema
    from .schema_cultivation_progress import ensure_cultivation_progress_schema
    from .schema_foundation import ensure_foundation_schema
    from .schema_learning_blog import ensure_learning_blog_signature_schema
    from .schema_materials_integrations import ensure_materials_integrations_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for builder in (
        ensure_foundation_schema,
        ensure_assignment_schema,
        ensure_classroom_activity_schema,
        ensure_materials_integrations_schema,
        ensure_learning_blog_signature_schema,
    ):
        builder(conn)
    ensure_cultivation_progress_schema(conn, engine="sqlite")
    return conn


def adapt_index_ddl_for_postgres(sql: str) -> str:
    """Convert a SQLite ``CREATE INDEX`` statement to a PostgreSQL-safe one."""
    statement = str(sql).strip().rstrip(";")
    statement = _COLLATE_NOCASE_RE.sub("", statement)
    statement = _CLOSING_WHERE_RE.sub(") WHERE", statement)
    statement = _CREATE_INDEX_PREFIX_RE.sub("CREATE INDEX IF NOT EXISTS ", statement)
    return statement


def collect_postgres_index_statements() -> list[str]:
    """Harvest non-unique index DDL from the canonical SQLite schema."""
    conn = _build_in_memory_sqlite_schema()
    try:
        rows = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index'
              AND sql IS NOT NULL
              AND sql NOT LIKE 'CREATE UNIQUE%'
            ORDER BY tbl_name, name
            """
        ).fetchall()
    finally:
        conn.close()

    statements: list[str] = []
    seen: set[str] = set()
    for row in rows:
        adapted = adapt_index_ddl_for_postgres(row[0])
        if adapted and adapted not in seen:
            seen.add(adapted)
            statements.append(adapted)
    return statements


def ensure_postgres_performance_indexes(conn: Any) -> dict[str, Any]:
    """Create the harvested performance indexes on PostgreSQL.

    Each statement runs inside its own SAVEPOINT so a single failure (for
    example an index referencing a column that does not exist on PostgreSQL, or
    a transient lock timeout) never aborts the whole batch. ``IF NOT EXISTS``
    keeps the operation idempotent and cheap on every subsequent startup.
    """
    if str(os.getenv("LANSHARE_DISABLE_PG_AUTO_INDEX", "")).strip().lower() in {"1", "true", "yes"}:
        return {"created": 0, "failed": 0, "total": 0, "disabled": True}

    try:
        statements = collect_postgres_index_statements()
    except Exception as exc:  # pragma: no cover - defensive
        return {"created": 0, "skipped": 0, "failed": 0, "total": 0, "error": str(exc)}

    # Index builds can outlast the default per-statement timeout, and we never
    # want them to wait forever on a lock during a live deploy.
    try:
        conn.execute("SET statement_timeout = 0")
        conn.execute("SET lock_timeout = '15s'")
    except Exception:
        pass

    created = failed = 0
    failures: list[str] = []
    for statement in statements:
        try:
            conn.execute("SAVEPOINT lanshare_idx")
            conn.execute(statement)
            conn.execute("RELEASE SAVEPOINT lanshare_idx")
            created += 1
        except Exception as exc:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT lanshare_idx")
            except Exception:
                pass
            failed += 1
            if len(failures) < 20:
                failures.append(f"{statement[:80]}... -> {exc}")

    # Restore the normal session guards for subsequent queries on this conn.
    try:
        conn.execute("RESET statement_timeout")
        conn.execute("RESET lock_timeout")
    except Exception:
        pass

    report: dict[str, Any] = {
        "created": created,
        "failed": failed,
        "total": len(statements),
    }
    if failures:
        report["failure_samples"] = failures
    return report
