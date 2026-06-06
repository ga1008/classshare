"""Tests for the SQLite -> PostgreSQL performance-index port."""

from __future__ import annotations

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


def test_ensure_uses_savepoints_and_tolerates_failures():
    log: list[str] = []

    class FakeConn:
        def execute(self, sql, params=None):
            log.append(sql)
            # Simulate one index failing to force the rollback-to-savepoint path.
            if "CREATE INDEX" in sql and "idx_fail" in sql:
                raise RuntimeError("boom")

    # Monkeypatch the harvested set to a tiny deterministic list.
    import classroom_app.db.postgres_indexes as mod

    original = mod.collect_postgres_index_statements
    mod.collect_postgres_index_statements = lambda: [
        "CREATE INDEX IF NOT EXISTS idx_ok ON t (a)",
        "CREATE INDEX IF NOT EXISTS idx_fail ON t (b)",
    ]
    try:
        report = ensure_postgres_performance_indexes(FakeConn())
    finally:
        mod.collect_postgres_index_statements = original

    assert report["created"] == 1
    assert report["failed"] == 1
    assert report["total"] == 2
    assert any("ROLLBACK TO SAVEPOINT" in entry for entry in log)
    assert any("RELEASE SAVEPOINT" in entry for entry in log)
