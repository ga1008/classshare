"""Tests for the SQLite -> PostgreSQL performance-index port."""

from __future__ import annotations

from unittest.mock import patch

from classroom_app.db import postgres_indexes as mod
from classroom_app.db.postgres_indexes import (
    adapt_index_ddl_for_postgres,
    collect_postgres_index_statements,
    ensure_postgres_performance_indexes,
)


def test_adapt_strips_collate_and_injects_if_not_exists():
    out = adapt_index_ddl_for_postgres(
        "CREATE INDEX idx_x ON t (a COLLATE NOCASE, b DESC)"
    )
    assert out == "CREATE INDEX IF NOT EXISTS idx_x ON t (a, b DESC)"
    assert "COLLATE" not in out.upper()


def test_adapt_fixes_partial_index_spacing():
    out = adapt_index_ddl_for_postgres("CREATE INDEX idx_x ON t (a)WHERE a = 1")
    assert out == "CREATE INDEX IF NOT EXISTS idx_x ON t (a) WHERE a = 1"


def test_adapt_is_idempotent_on_if_not_exists():
    src = "CREATE INDEX IF NOT EXISTS idx_x ON t (a)"
    assert adapt_index_ddl_for_postgres(src) == src


def test_collect_returns_postgres_safe_non_unique_indexes():
    statements = collect_postgres_index_statements()
    # The SQLite schema defines a large index set; ensure we harvested a lot.
    assert len(statements) > 150
    for sql in statements:
        assert "IF NOT EXISTS" in sql.upper()
        assert "COLLATE" not in sql.upper()
        assert not sql.upper().startswith("CREATE UNIQUE")
        # Partial-index keyword must be space-separated for PostgreSQL.
        assert ")WHERE" not in sql.replace(" ", " ")


def metadata(name="idx_ok", table="t", **changes):
    return {"index_name": name, "schema_name": "public", "relkind": "i", "table_schema": "public",
            "table_name": table, "valid": True, "ready": True, "unique": False, "primary": False,
            "method": "btree", **changes}


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class FakeConn:
    def __init__(self, indexes=(), *, race_table=None, tuple_rows=False):
        self.indexes = {row["index_name"]: row for row in indexes}
        self.log = []
        self.race_table = race_table
        self.tuple_rows = tuple_rows

    def execute(self, sql, params=None):
        self.log.append(sql)
        if "FROM pg_catalog.pg_class" in sql:
            rows = [row for name, row in self.indexes.items()
                    if "'" + name + "'" in sql and row["schema_name"] == "public"]
            return Cursor([tuple(row.values()) for row in rows] if self.tuple_rows else rows)
        if sql.startswith("SELECT current_setting"):
            return Cursor([{"statement_timeout": "9s", "lock_timeout": "2s"}])
        if sql.startswith("CREATE INDEX"):
            name, _, table = mod._index_target(sql)
            if name == "idx_fail":
                raise RuntimeError("synthetic create failure")
            self.indexes[name] = metadata(name, self.race_table or table)
        return Cursor()


def test_ensure_uses_savepoints_and_tolerates_failures():
    conn = FakeConn()
    with patch.object(mod, "collect_postgres_index_statements", return_value=[
        "CREATE INDEX IF NOT EXISTS idx_ok ON t (a)",
        "CREATE INDEX IF NOT EXISTS idx_fail ON t (b)",
    ]):
        report = ensure_postgres_performance_indexes(conn)
    assert report["created"] == 1
    assert report["existing"] == 0
    assert report["failed"] == 1
    assert report["total"] == 2
    assert any("ROLLBACK TO SAVEPOINT" in entry for entry in conn.log)
    assert any("RELEASE SAVEPOINT" in entry for entry in conn.log)
    assert "SET LOCAL statement_timeout = '60s'" in conn.log
    assert any("'9s'" in entry and "'2s'" in entry for entry in conn.log if "set_config" in entry)


def test_existing_valid_indexes_use_one_catalog_read_and_no_business_ddl():
    conn = FakeConn([metadata(), metadata("idx_two", "other")], tuple_rows=True)
    with patch.object(mod, "collect_postgres_index_statements", return_value=[
        "CREATE INDEX IF NOT EXISTS idx_ok ON t (a)",
        "CREATE INDEX IF NOT EXISTS idx_two ON other (b)",
    ]):
        report = ensure_postgres_performance_indexes(conn)
    assert report == {"created": 0, "existing": 2, "failed": 0, "total": 2}
    assert len(conn.log) == 1
    assert "FROM pg_catalog.pg_class" in conn.log[0]


def test_invalid_or_colliding_public_relation_is_reported_without_ddl():
    for changes in ({"valid": False}, {"ready": False}, {"table_name": "wrong"}, {"table_schema": "other"},
                    {"relkind": "r"}, {"unique": True}, {"primary": True}, {"method": "hash"}):
        conn = FakeConn([metadata(**changes)])
        with patch.object(mod, "collect_postgres_index_statements", return_value=["CREATE INDEX IF NOT EXISTS idx_ok ON t (a)"]):
            report = ensure_postgres_performance_indexes(conn)
        assert report["failed"] == 1 and report["existing"] == report["created"] == 0
        assert not any("CREATE INDEX" in entry for entry in conn.log)


def test_other_schema_name_does_not_block_public_creation():
    conn = FakeConn([metadata(schema_name="other", table_schema="other")])
    with patch.object(mod, "collect_postgres_index_statements", return_value=["CREATE INDEX IF NOT EXISTS idx_ok ON t (a)"]):
        report = ensure_postgres_performance_indexes(conn)
    assert report == {"created": 1, "existing": 0, "failed": 0, "total": 1}


def test_concurrent_name_collision_after_catalog_read_does_not_claim_creation_success():
    conn = FakeConn(race_table="wrong")
    with patch.object(mod, "collect_postgres_index_statements", return_value=["CREATE INDEX IF NOT EXISTS idx_ok ON t (a)"]):
        report = ensure_postgres_performance_indexes(conn)
    assert report["failed"] == 1 and report["created"] == report["existing"] == 0
    assert "ROLLBACK TO SAVEPOINT lanshare_idx" in conn.log


def test_harvest_failure_is_not_a_successful_empty_index_migration():
    with patch.object(mod, "collect_postgres_index_statements", side_effect=RuntimeError("synthetic harvest failure")):
        report = ensure_postgres_performance_indexes(FakeConn())
    assert report["failed"] == 1 and "error" in report
