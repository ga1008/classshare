from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .errors import DatabaseConfigurationError


SUPPORTED_SQL_ENGINES = {"sqlite", "postgres"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SqlStatement:
    sql: str
    returns_id: bool = False
    id_strategy: str = ""


def normalize_engine(engine: str) -> str:
    normalized = str(engine or "sqlite").strip().lower()
    if normalized not in SUPPORTED_SQL_ENGINES:
        raise DatabaseConfigurationError(
            f"Unsupported SQL engine '{normalized}'. Supported values: sqlite, postgres."
        )
    return normalized


def quote_identifier(identifier: str) -> str:
    parts = [part.strip() for part in str(identifier).split(".")]
    if not parts or any(not _IDENTIFIER_RE.fullmatch(part) for part in parts):
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return ".".join(f'"{part}"' for part in parts)


def quote_identifiers(identifiers: Iterable[str]) -> str:
    return ", ".join(quote_identifier(identifier) for identifier in identifiers)


def placeholder(engine: str, index: int) -> str:
    engine = normalize_engine(engine)
    if index < 1:
        raise ValueError("placeholder index must be 1 or greater")
    return "?" if engine == "sqlite" else f"${index}"


def placeholders(engine: str, count: int, *, start: int = 1) -> str:
    if count < 1:
        raise ValueError("placeholder count must be 1 or greater")
    return ", ".join(placeholder(engine, index) for index in range(start, start + count))


def current_timestamp_sql(engine: str) -> str:
    engine = normalize_engine(engine)
    return "CURRENT_TIMESTAMP" if engine == "sqlite" else "now()"


def limit_offset_clause(engine: str, *, limit_index: int, offset_index: int | None = None) -> str:
    clause = f"LIMIT {placeholder(engine, limit_index)}"
    if offset_index is not None:
        clause += f" OFFSET {placeholder(engine, offset_index)}"
    return clause


def insert_returning_id_sql(
    engine: str,
    table: str,
    columns: Sequence[str],
    *,
    id_column: str = "id",
) -> SqlStatement:
    engine = normalize_engine(engine)
    if not columns:
        raise ValueError("insert_returning_id_sql requires at least one column")
    column_sql = quote_identifiers(columns)
    sql = (
        f"INSERT INTO {quote_identifier(table)} ({column_sql}) "
        f"VALUES ({placeholders(engine, len(columns))})"
    )
    if engine == "postgres":
        return SqlStatement(sql=f"{sql} RETURNING {quote_identifier(id_column)}", returns_id=True, id_strategy="returning")
    return SqlStatement(sql=sql, returns_id=True, id_strategy="cursor.lastrowid")


def insert_ignore_sql(
    engine: str,
    table: str,
    columns: Sequence[str],
    *,
    conflict_columns: Sequence[str] = (),
) -> SqlStatement:
    engine = normalize_engine(engine)
    if not columns:
        raise ValueError("insert_ignore_sql requires at least one column")
    if engine == "postgres" and not conflict_columns:
        raise ValueError("PostgreSQL insert-ignore requires explicit conflict columns")
    column_sql = quote_identifiers(columns)
    value_sql = placeholders(engine, len(columns))
    if engine == "sqlite":
        sql = f"INSERT OR IGNORE INTO {quote_identifier(table)} ({column_sql}) VALUES ({value_sql})"
    else:
        conflict_sql = quote_identifiers(conflict_columns)
        sql = (
            f"INSERT INTO {quote_identifier(table)} ({column_sql}) VALUES ({value_sql}) "
            f"ON CONFLICT ({conflict_sql}) DO NOTHING"
        )
    return SqlStatement(sql=sql)


def insert_update_on_conflict_sql(
    engine: str,
    table: str,
    columns: Sequence[str],
    *,
    conflict_columns: Sequence[str],
    update_columns: Sequence[str],
) -> SqlStatement:
    engine = normalize_engine(engine)
    if not columns:
        raise ValueError("insert_update_on_conflict_sql requires at least one column")
    if not conflict_columns:
        raise ValueError("conflict_columns must not be empty")
    if not update_columns:
        raise ValueError("update_columns must not be empty")
    column_sql = quote_identifiers(columns)
    value_sql = placeholders(engine, len(columns))
    if engine == "sqlite":
        assignment_sql = ", ".join(
            f"{quote_identifier(column)} = excluded.{quote_identifier(column)}"
            for column in update_columns
        )
        conflict_sql = quote_identifiers(conflict_columns)
        sql = (
            f"INSERT INTO {quote_identifier(table)} ({column_sql}) VALUES ({value_sql}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {assignment_sql}"
        )
    else:
        assignment_sql = ", ".join(
            f"{quote_identifier(column)} = excluded.{quote_identifier(column)}"
            for column in update_columns
        )
        conflict_sql = quote_identifiers(conflict_columns)
        sql = (
            f"INSERT INTO {quote_identifier(table)} ({column_sql}) VALUES ({value_sql}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {assignment_sql}"
        )
    return SqlStatement(sql=sql)


def for_update_skip_locked_clause(engine: str) -> str:
    engine = normalize_engine(engine)
    return "" if engine == "sqlite" else "FOR UPDATE SKIP LOCKED"


def _validate_order_direction(direction: str) -> str:
    normalized = str(direction or "ASC").strip().upper()
    if normalized not in {"ASC", "DESC"}:
        raise ValueError(f"Unsupported order direction: {direction!r}")
    return normalized


def sql_string_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def postgres_claim_jobs_sql(
    table: str,
    *,
    claim_status: str,
    eligible_where_sql: str,
    id_column: str = "id",
    status_column: str = "status",
    worker_column: str | None = None,
    worker_placeholder_index: int | None = None,
    locked_at_column: str | None = None,
    started_at_column: str | None = None,
    updated_at_column: str | None = None,
    order_columns: Sequence[tuple[str, str]] = (("created_at", "ASC"), ("id", "ASC")),
    limit_placeholder_index: int = 1,
) -> SqlStatement:
    if not str(eligible_where_sql or "").strip():
        raise ValueError("eligible_where_sql must not be empty")
    assignments = [f"{quote_identifier(status_column)} = {sql_string_literal(claim_status)}"]
    if worker_column:
        if worker_placeholder_index is None:
            raise ValueError("worker_placeholder_index is required when worker_column is provided")
        assignments.append(f"{quote_identifier(worker_column)} = {placeholder('postgres', worker_placeholder_index)}")
    if locked_at_column:
        assignments.append(f"{quote_identifier(locked_at_column)} = now()")
    if started_at_column:
        assignments.append(f"{quote_identifier(started_at_column)} = COALESCE({quote_identifier(started_at_column)}, now())")
    if updated_at_column:
        assignments.append(f"{quote_identifier(updated_at_column)} = now()")
    order_sql = ", ".join(
        f"{quote_identifier(column)} {_validate_order_direction(direction)}"
        for column, direction in order_columns
    )
    sql = f"""
UPDATE {quote_identifier(table)}
SET {", ".join(assignments)}
WHERE {quote_identifier(id_column)} IN (
    SELECT {quote_identifier(id_column)}
    FROM {quote_identifier(table)}
    WHERE {eligible_where_sql.strip()}
    ORDER BY {order_sql}
    LIMIT {placeholder("postgres", limit_placeholder_index)}
    FOR UPDATE SKIP LOCKED
)
RETURNING *
""".strip()
    return SqlStatement(sql=sql)


def postgres_singleton_status_index_sql(
    table: str,
    *,
    status_column: str = "status",
    singleton_status: str = "running",
    index_name: str | None = None,
) -> SqlStatement:
    safe_index_name = index_name or f"idx_{table}_{singleton_status}_singleton"
    sql = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {quote_identifier(safe_index_name)} "
        f"ON {quote_identifier(table)} ((1)) "
        f"WHERE {quote_identifier(status_column)} = {sql_string_literal(singleton_status)}"
    )
    return SqlStatement(sql=sql)
