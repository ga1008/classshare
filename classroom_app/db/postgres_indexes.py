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
_IDENTIFIER = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_INDEX_TARGET_RE = re.compile(
    rf'^\s*CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+({_IDENTIFIER})\s+ON\s+'
    rf'(?:(?P<schema>{_IDENTIFIER})\s*\.\s*)?(?P<table>{_IDENTIFIER})\s*\(',
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
    from .schema_ai_jobs import ensure_ai_job_schema

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
    ensure_ai_job_schema(conn, engine="sqlite")
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


def _identifier(value: str) -> str:
    return value[1:-1].replace('""', '"') if value.startswith('"') else value.lower()


def _index_target(statement: str) -> tuple[str, str, str]:
    match = _INDEX_TARGET_RE.match(statement)
    if match is None:
        raise ValueError("Unsupported harvested index declaration")
    return (_identifier(match.group(1)), _identifier(match.group("schema") or "public"),
            _identifier(match.group("table")))


def _catalog_indexes(conn: Any, names: list[str]) -> dict[str, dict[str, Any]]:
    if not names:
        return {}
    # Relation names, including non-index collisions, are scoped to public.
    # Reading pg_catalog does not acquire CREATE INDEX's ShareLock on business
    # tables. Literal escaping also supports raw psycopg and the SQLite facade.
    literals = ",".join("'" + name.replace("'", "''") + "'" for name in sorted(set(names)))
    columns = ("index_name", "schema_name", "relkind", "table_schema", "table_name",
               "valid", "ready", "unique", "primary", "method")
    rows = conn.execute(
        "SELECT c.relname AS index_name,n.nspname AS schema_name,c.relkind,"
        "tn.nspname AS table_schema,t.relname AS table_name,i.indisvalid AS valid,"
        "i.indisready AS ready,i.indisunique AS \"unique\",i.indisprimary AS \"primary\",am.amname AS method "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid "
        "LEFT JOIN pg_catalog.pg_class t ON t.oid=i.indrelid "
        "LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=t.relnamespace "
        "LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam "
        "WHERE n.nspname='public' AND c.relname IN (" + literals + ")"
    ).fetchall()
    result = {}
    for row in rows:
        value = ({name: row[name] for name in columns} if hasattr(row, "keys")
                 else dict(zip(columns, row)))
        result[str(value["index_name"])] = value
    return result


def _matches_index(value: dict[str, Any], target: tuple[str, str, str]) -> bool:
    name, schema, table = target
    return (schema == "public" and value.get("index_name") == name
            and value.get("schema_name") == schema and value.get("table_schema") == schema
            and value.get("table_name") == table and value.get("relkind") in {"i", "I"}
            and value.get("valid") is True and value.get("ready") is True
            and value.get("unique") is False and value.get("primary") is False
            and value.get("method") == "btree")


def ensure_postgres_performance_indexes(conn: Any) -> dict[str, Any]:
    """Create the harvested performance indexes on PostgreSQL.

    Each statement runs inside its own SAVEPOINT so a single failure (for
    example an index referencing a column that does not exist on PostgreSQL, or
    a transient lock timeout) never aborts the whole batch. ``IF NOT EXISTS``
    is retained for the missing-index race. Existing valid indexes are resolved
    through one catalog read: even a no-op CREATE INDEX takes a table ShareLock,
    which can deadlock with ordinary multi-table writes during worker restart.
    """
    if str(os.getenv("LANSHARE_DISABLE_PG_AUTO_INDEX", "")).strip().lower() in {"1", "true", "yes"}:
        return {"created": 0, "existing": 0, "failed": 0, "total": 0, "disabled": True}

    try:
        statements = collect_postgres_index_statements()
    except Exception as exc:  # pragma: no cover - defensive
        return {"created": 0, "existing": 0, "failed": 1, "total": 0, "error": str(exc)}

    targets = []
    for statement in statements:
        try:
            targets.append(_index_target(statement))
        except ValueError:
            targets.append(None)
    known = _catalog_indexes(conn, [target[0] for target in targets if target])
    created = existing = failed = 0
    failures: list[str] = []
    for statement, target in zip(statements, targets):
        if target and target[0] in known and _matches_index(known[target[0]], target):
            existing += 1
            continue
        if target is None or target[1] != "public" or target[0] in known:
            failed += 1
            if len(failures) < 20:
                failures.append(f"{statement[:80]}... -> invalid, incompatible or colliding index declaration")
            continue
        try:
            conn.execute("SAVEPOINT lanshare_idx")
            guards = conn.execute("SELECT current_setting('statement_timeout') AS statement_timeout, "
                                  "current_setting('lock_timeout') AS lock_timeout").fetchone()
            previous_statement, previous_lock = ((guards["statement_timeout"], guards["lock_timeout"])
                                                if hasattr(guards, "keys") else guards)
            conn.execute("SET LOCAL statement_timeout = '60s'")
            conn.execute("SET LOCAL lock_timeout = '15s'")
            conn.execute(statement)
            actual = _catalog_indexes(conn, [target[0]])
            if target[0] not in actual or not _matches_index(actual[target[0]], target):
                raise ValueError("Created index is invalid or belongs to another relation")
            # The caller retains transaction ownership and its original guards.
            conn.execute("SELECT set_config('statement_timeout', '" + str(previous_statement).replace("'", "''")
                         + "', true), set_config('lock_timeout', '" + str(previous_lock).replace("'", "''") + "', true)")
            conn.execute("RELEASE SAVEPOINT lanshare_idx")
            known.update(actual)
            created += 1
        except Exception as exc:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT lanshare_idx")
                conn.execute("RELEASE SAVEPOINT lanshare_idx")
            except Exception:
                pass
            failed += 1
            if len(failures) < 20:
                failures.append(f"{statement[:80]}... -> {exc}")

    report: dict[str, Any] = {
        "created": created,
        "existing": existing,
        "failed": failed,
        "total": len(statements),
    }
    if failures:
        report["failure_samples"] = failures
    return report
