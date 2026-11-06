"""Physical schema/transaction tests; never connect to PostgreSQL or a model."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from classroom_app.db import schema_wanderers as schema
from classroom_app.db.postgres import LanSharePostgresConnection


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _CatalogConnection:
    """Record the actual adapter protocol without opening a driver/socket."""
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("WITH expected_tables"):
            return _Cursor(next(self.snapshots))
        return _Cursor([])

    def commit(self):
        raise AssertionError("schema preparation cannot commit its caller")

    def rollback(self):
        raise AssertionError("schema preparation cannot roll back its caller")


class WandererSchemaTransactionsTest(unittest.TestCase):
    def connect(self, path=":memory:"):
        conn = sqlite3.connect(path, timeout=2)
        self.addCleanup(conn.close)
        return conn

    def file_database(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="wanderer-schema-tx-"))
        path = Path(directory) / "owned.sqlite"
        conn = self.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO ledger VALUES (1,'before')")
        conn.commit()
        return path, conn

    def prepare(self, conn):
        schema.ensure_wanderer_schema(conn, engine="sqlite")
        conn.commit()

    @staticmethod
    def trace(conn):
        statements = []
        conn.set_trace_callback(statements.append)
        return statements

    @staticmethod
    def top_level(statements):
        return [sql for sql in statements if not sql.lstrip().startswith("--")]

    def test_complete_business_and_startup_checks_are_readonly_even_with_reset_flag(self):
        conn = self.connect()
        self.prepare(conn)
        for ready in (False, True):
            with self.subTest(ready=ready), patch.object(schema, "_SCHEMA_READY", ready):
                statements = self.trace(conn)
                schema.ensure_wanderer_schema(conn, engine="sqlite")
                schema.ensure_wanderer_schema(conn, engine="sqlite", allow_schema_changes=True)
                conn.set_trace_callback(None)
                self.assertEqual(len(self.top_level(statements)), 2)
                self.assertTrue(all(sql.startswith("WITH expected_tables") for sql in self.top_level(statements)))
                # Each check inspects each table once, not once per column.
                self.assertLessEqual(len(statements), 2 * (len(schema._TABLE_NAMES) + 1))
                self.assertEqual(schema._SCHEMA_READY, ready)

    def test_separate_memory_databases_are_never_skipped_by_a_process_flag(self):
        first, second = self.connect(), self.connect()
        with patch.object(schema, "_SCHEMA_READY", True):
            self.prepare(first)
            self.assertTrue(schema._missing_schema(second, "sqlite"))
            self.prepare(second)
        for conn in (first, second):
            self.assertFalse(schema._missing_schema(conn, "sqlite"))
            conn.execute("INSERT INTO wanderer_runtime_settings VALUES ('owned','one','now')")
        first.commit()
        second.rollback()
        self.assertEqual(first.execute("SELECT COUNT(*) FROM wanderer_runtime_settings").fetchone()[0], 1)
        self.assertEqual(second.execute("SELECT COUNT(*) FROM wanderer_runtime_settings").fetchone()[0], 0)

    def test_requirements_cover_every_declared_physical_column_and_index(self):
        conn = self.connect()
        self.prepare(conn)
        actual_columns = set()
        for table in schema._TABLE_NAMES:
            actual_columns.update((table, row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")'))
        self.assertEqual(set(schema._COLUMNS), actual_columns)
        actual_indexes = {(row[1], row[0]) for row in conn.execute(
            "SELECT name,tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
        )}
        self.assertEqual(set(schema._INDEX_NAMES), actual_indexes)
        self.assertLess(len(schema._PG_WITNESS), 4096)
        self.assertLess(len(schema._SQLITE_WITNESS_PARAMS), 999)
        self.assertEqual(len(schema._PG_WITNESS_PARAMS), 5)
        self.assertFalse(schema._missing_schema(conn, "sqlite"))

    def test_outer_business_transaction_and_schema_both_roll_back(self):
        _path, conn = self.file_database()
        conn.execute("UPDATE ledger SET value='uncommitted' WHERE id=1")
        schema.ensure_wanderer_schema(conn, engine="sqlite")
        self.assertTrue(conn.in_transaction)
        self.assertFalse(schema._missing_schema(conn, "sqlite"))
        conn.rollback()
        self.assertEqual(conn.execute("SELECT value FROM ledger").fetchone()[0], "before")
        self.assertTrue(schema._missing_schema(conn, "sqlite"))
        self.prepare(conn)

    def test_uncommitted_and_rolled_back_schema_never_makes_other_connection_ready(self):
        path, first = self.file_database()
        second = self.connect(path)
        schema.ensure_wanderer_schema(first, engine="sqlite")
        self.assertTrue(first.in_transaction)
        self.assertFalse(schema._missing_schema(first, "sqlite"))
        self.assertTrue(schema._missing_schema(second, "sqlite"))
        first.rollback()
        self.prepare(second)
        self.assertFalse(schema._missing_schema(first, "sqlite"))

    def test_failed_ddl_is_left_to_owner_rollback_and_next_connection_can_prepare(self):
        path, first = self.file_database()

        class FailAtSecondTable:
            @property
            def in_transaction(self):
                return first.in_transaction

            def execute(self, sql, params=()):
                if "CREATE TABLE IF NOT EXISTS wanderer_todos" in sql:
                    raise sqlite3.OperationalError("controlled schema write failure")
                return first.execute(sql, params)

        with self.assertRaisesRegex(sqlite3.OperationalError, "controlled schema write failure"):
            schema.ensure_wanderer_schema(FailAtSecondTable(), engine="sqlite")
        self.assertTrue(first.in_transaction)
        first.rollback()
        second = self.connect(path)
        self.assertTrue(schema._missing_schema(second, "sqlite"))
        self.prepare(second)
        self.assertFalse(schema._missing_schema(first, "sqlite"))

    def test_selective_old_schema_upgrade_preserves_rows_constraints_and_custom_indexes(self):
        conn = self.connect()
        self.prepare(conn)
        conn.execute("ALTER TABLE wanderer_action_log DROP COLUMN tick_id")
        conn.execute("DROP INDEX idx_wanderer_todos_due")
        conn.execute("CREATE UNIQUE INDEX custom_wanderer_setting_value ON wanderer_runtime_settings(value)")
        conn.execute("CREATE TABLE dependent (setting_key TEXT REFERENCES wanderer_runtime_settings(key))")
        conn.execute("INSERT INTO wanderer_runtime_settings VALUES ('one','preserved','now')")
        conn.execute("INSERT INTO dependent VALUES ('one')")
        conn.commit()
        definitions = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE name IN ('custom_wanderer_setting_value','dependent')"))
        before_fks = conn.execute("PRAGMA foreign_key_list(dependent)").fetchall()
        statements = self.trace(conn)
        schema.ensure_wanderer_schema(conn, engine="sqlite", allow_schema_changes=True)
        conn.commit()
        conn.set_trace_callback(None)
        ddl = [sql.strip() for sql in self.top_level(statements) if sql.lstrip().startswith(("ALTER", "CREATE", "DROP"))]
        self.assertEqual(ddl, ["ALTER TABLE wanderer_action_log ADD COLUMN tick_id TEXT NOT NULL DEFAULT ''",
                              "CREATE INDEX IF NOT EXISTS idx_wanderer_todos_due ON wanderer_todos (wanderer_id, status, remind_at)"])
        self.assertEqual(dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE name IN ('custom_wanderer_setting_value','dependent')")), definitions)
        self.assertEqual(conn.execute("PRAGMA foreign_key_list(dependent)").fetchall(), before_fks)
        self.assertEqual(conn.execute("SELECT * FROM wanderer_runtime_settings").fetchall(), [('one', 'preserved', 'now')])
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO wanderer_runtime_settings VALUES ('two','preserved','later')")

    def test_unknown_required_base_column_raises_without_attempting_destructive_repair(self):
        conn = self.connect()
        self.prepare(conn)
        conn.execute("ALTER TABLE wanderer_action_log DROP COLUMN detail_json")
        conn.commit()
        statements = self.trace(conn)
        with self.assertRaises(schema.WandererSchemaUpgradeRequired) as caught:
            schema.ensure_wanderer_schema(conn, engine="sqlite")
        self.assertIn(("column", "wanderer_action_log", "detail_json"), caught.exception.missing)
        self.assertFalse(any(sql.lstrip().startswith(("ALTER", "CREATE", "DROP")) for sql in self.top_level(statements)))
        conn.rollback()

    def test_complete_startup_check_finishes_while_another_business_writer_is_open(self):
        path, writer = self.file_database()
        self.prepare(writer)
        writer.execute("UPDATE ledger SET value='still-open' WHERE id=1")
        writer.execute("INSERT INTO wanderer_action_log (wanderer_id,action_kind,created_at) VALUES (1,'hold','now')")
        done = threading.Event()

        def startup():
            conn = sqlite3.connect(path, timeout=0.1)
            try:
                statements = self.trace(conn)
                schema.ensure_wanderer_schema(conn, engine="sqlite", allow_schema_changes=True)
                conn.commit()
                done.set()
                return statements, conn.execute("SELECT value FROM ledger").fetchone()[0]
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(startup)
            self.assertTrue(done.wait(2), "complete startup tried to acquire a writer/schema lock")
            self.assertTrue(writer.in_transaction)
            statements, visible_value = future.result(timeout=2)
        self.assertEqual(visible_value, "before")
        self.assertFalse(any(sql.lstrip().startswith(("ALTER", "CREATE", "DROP", "BEGIN IMMEDIATE")) for sql in self.top_level(statements)))
        writer.rollback()

    def test_complete_postgres_check_uses_one_small_query_and_bound_arrays_through_adapter(self):
        for allow in (False, True):
            raw = _CatalogConnection([[]])
            schema.ensure_wanderer_schema(LanSharePostgresConnection(raw), engine="postgres", allow_schema_changes=allow)
            self.assertEqual(len(raw.calls), 1)
            sql, params = raw.calls[0]
            self.assertEqual(sql.count("%s::text[]"), 5)
            self.assertEqual(len(params), 5)
            self.assertEqual(params[0], list(schema._TABLE_NAMES))
            self.assertIn("x.indrelid=t.oid", sql)
            self.assertIn("x.indisvalid AND x.indisready", sql)
            self.assertNotIn("SELECT * FROM pg_catalog", sql)

    def test_postgres_missing_schema_refuses_business_ddl_without_committing_or_poisoning_owner(self):
        missing = [("column", "wanderer_action_log", "tick_id")]
        raw = _CatalogConnection([missing])
        conn = LanSharePostgresConnection(raw)
        with self.assertRaises(schema.WandererSchemaUpgradeRequired) as caught:
            schema.ensure_wanderer_schema(conn, engine="postgres")
        self.assertEqual(caught.exception.missing, tuple(missing))
        self.assertEqual(len(raw.calls), 1)
        conn.execute("SELECT owner_business_still_usable")
        self.assertEqual(len(raw.calls), 2)

    def test_postgres_explicit_migration_rechecks_after_owner_lock_and_changes_only_missing_objects(self):
        missing = [("column", "wanderer_action_log", "tick_id"),
                   ("index", "wanderer_todos", "idx_wanderer_todos_due")]
        raw = _CatalogConnection([missing, missing, []])
        schema.ensure_wanderer_schema(LanSharePostgresConnection(raw), engine="postgres", allow_schema_changes=True)
        changes = [sql for sql, _ in raw.calls if sql.startswith(("ALTER", "CREATE"))]
        self.assertEqual(changes, ["ALTER TABLE wanderer_action_log ADD COLUMN IF NOT EXISTS tick_id TEXT NOT NULL DEFAULT ''",
                                  "CREATE INDEX IF NOT EXISTS idx_wanderer_todos_due ON wanderer_todos (wanderer_id, status, remind_at)"])
        self.assertIn("pg_advisory_xact_lock", raw.calls[1][0])
        # Another owner may have completed preparation while we awaited the lock.
        already_prepared = _CatalogConnection([missing, []])
        schema.ensure_wanderer_schema(LanSharePostgresConnection(already_prepared), engine="postgres", allow_schema_changes=True)
        self.assertEqual(len(already_prepared.calls), 3)
        self.assertFalse(any(sql.startswith(("ALTER", "CREATE")) for sql, _ in already_prepared.calls))
