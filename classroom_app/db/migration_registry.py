from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .sql import insert_ignore_sql, normalize_engine


SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
BASELINE_VERSION = "0001_sqlite_v4_baseline"


@dataclass(frozen=True)
class MigrationDefinition:
    version: str
    name: str
    db_engine: str
    description: str
    checksum: str


def migration_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def schema_migrations_table_sql(engine: str) -> str:
    engine = normalize_engine(engine)
    if engine == "sqlite":
        success_type = "INTEGER NOT NULL CHECK (success IN (0, 1))"
        timestamp_type = "TEXT NOT NULL"
    else:
        success_type = "boolean NOT NULL"
        timestamp_type = "timestamptz NOT NULL DEFAULT now()"
    return f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at {timestamp_type},
    duration_ms INTEGER NOT NULL DEFAULT 0,
    db_engine TEXT NOT NULL,
    success {success_type},
    error TEXT,
    PRIMARY KEY (version, db_engine)
)
""".strip()


def insert_migration_record_sql(engine: str):
    return insert_ignore_sql(
        engine,
        SCHEMA_MIGRATIONS_TABLE,
        (
            "version",
            "name",
            "checksum",
            "applied_at",
            "duration_ms",
            "db_engine",
            "success",
            "error",
        ),
        conflict_columns=("version", "db_engine"),
    )


def baseline_migration_for_schema(schema_sql: str, *, db_engine: str = "sqlite") -> MigrationDefinition:
    engine = normalize_engine(db_engine)
    return MigrationDefinition(
        version=BASELINE_VERSION,
        name="LanShare SQLite V4 baseline schema",
        db_engine=engine,
        description="Read-only baseline captured from the current LanShare SQLite schema.",
        checksum=migration_checksum(schema_sql),
    )
