"""Best-effort savepoints (audit B6) and cross-engine unique-violation detection (audit B11)."""
from __future__ import annotations

import sqlite3
import unittest

from classroom_app.db.errors import is_unique_violation
from classroom_app.db.savepoint import best_effort


class BestEffortSavepointTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute("INSERT INTO t (id, v) VALUES (1, 'outer')")

    def tearDown(self):
        self.conn.close()

    def test_failed_hook_rolls_back_only_its_own_writes(self):
        with best_effort(self.conn, "hook", log_prefix="[TEST]"):
            self.conn.execute("INSERT INTO t (id, v) VALUES (2, 'hook')")
            raise RuntimeError("boom")
        self.conn.execute("INSERT INTO t (id, v) VALUES (3, 'after')")
        self.conn.execute("COMMIT")
        self.assertEqual([r[0] for r in self.conn.execute("SELECT id FROM t ORDER BY id")], [1, 3])

    def test_successful_hook_keeps_writes(self):
        with best_effort(self.conn, "hook"):
            self.conn.execute("INSERT INTO t (id, v) VALUES (2, 'hook')")
        self.conn.execute("COMMIT")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 2)

    def test_name_is_validated(self):
        with self.assertRaises(ValueError):
            with best_effort(self.conn, "bad name; DROP"):
                pass


class ProductionStyleConnectionTests(unittest.TestCase):
    """Default (legacy) isolation, as LanShareSQLiteConnection uses in the app."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_failure_after_dml_keeps_outer_transaction_uncommitted(self):
        self.conn.execute("INSERT INTO t (id) VALUES (1)")  # implicit BEGIN
        with best_effort(self.conn, "hook"):
            self.conn.execute("INSERT INTO t (id) VALUES (2)")
            raise RuntimeError("boom")
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 0)

    def test_without_open_transaction_no_savepoint_commit_happens(self):
        with best_effort(self.conn, "hook"):
            raise RuntimeError("boom")
        self.assertFalse(self.conn.in_transaction)

    def test_hook_that_commits_does_not_fail_the_caller(self):
        self.conn.execute("INSERT INTO t (id) VALUES (1)")
        with best_effort(self.conn, "hook"):
            self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)


class UniqueViolationTests(unittest.TestCase):
    def test_sqlite_unique_detected_but_not_other_integrity(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE u (k TEXT UNIQUE, v TEXT NOT NULL)")
        conn.execute("INSERT INTO u VALUES ('a', 'x')")
        with self.assertRaises(sqlite3.IntegrityError) as dup:
            conn.execute("INSERT INTO u VALUES ('a', 'y')")
        with self.assertRaises(sqlite3.IntegrityError) as notnull:
            conn.execute("INSERT INTO u VALUES ('b', NULL)")
        self.assertTrue(is_unique_violation(dup.exception))
        self.assertFalse(is_unique_violation(notnull.exception))

    def test_postgres_sqlstate_detected(self):
        class FakePgError(Exception):
            sqlstate = "23505"

        class OtherPgError(Exception):
            sqlstate = "23502"

        self.assertTrue(is_unique_violation(FakePgError("duplicate key")))
        self.assertFalse(is_unique_violation(OtherPgError("not null")))

    def test_retryable_conflicts(self):
        from classroom_app.db.errors import is_retryable_conflict

        class Serialization(Exception):
            sqlstate = "40001"

        self.assertTrue(is_retryable_conflict(Serialization("could not serialize")))
        self.assertTrue(is_retryable_conflict(sqlite3.OperationalError("database is locked")))
        self.assertFalse(is_retryable_conflict(sqlite3.OperationalError("no such table: x")))
        self.assertFalse(is_retryable_conflict(ValueError("boom")))


if __name__ == "__main__":
    unittest.main()
