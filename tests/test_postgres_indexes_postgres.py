"""Index startup versus real DML, only in a new database on an owned PG cluster."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import uuid

from classroom_app.db import postgres_indexes as indexes
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires explicitly owned offline PostgreSQL")
class PerformanceIndexPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_index_" + uuid.uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def connect(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(raw)

    def test_existing_indexes_do_not_lock_business_tables_while_dml_holds_opposite_order(self):
        statements = ["CREATE INDEX IF NOT EXISTS idx_owned_assignments ON assignments (value)",
                      "CREATE INDEX IF NOT EXISTS idx_owned_todos ON classroom_todos (value)"]
        with self.connect() as setup:
            setup.execute("CREATE TABLE assignments (id INTEGER PRIMARY KEY,value INTEGER)")
            setup.execute("CREATE TABLE classroom_todos (id INTEGER PRIMARY KEY,value INTEGER)")
            setup.execute("INSERT INTO assignments VALUES (1,10)")
            setup.execute("INSERT INTO classroom_todos VALUES (1,10)")
            for statement in statements:
                setup.execute(statement)
            setup.commit()
        completed, release = threading.Event(), threading.Event()
        state = {}

        def initialize():
            with self.connect() as conn:
                state["pid"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                with patch.object(indexes, "collect_postgres_index_statements", return_value=statements):
                    state["report"] = indexes.ensure_postgres_performance_indexes(conn)
                completed.set()
                if not release.wait(5):
                    raise RuntimeError("Synthetic initializer release timed out")
                conn.commit()

        with self.connect() as dml, ThreadPoolExecutor(max_workers=1) as pool:
            dml.execute("SET LOCAL lock_timeout='500ms'")
            dml.execute("UPDATE classroom_todos SET value=value+1 WHERE id=1")
            future = pool.submit(initialize)
            try:
                self.assertTrue(completed.wait(3), "Existing-index startup must finish while the DML transaction holds todo locks")
                self.assertEqual(state["report"], {"created": 0, "existing": 2, "failed": 0, "total": 2})
                with self.connect() as observer:
                    count = observer.execute("SELECT COUNT(*) AS n FROM pg_locks WHERE pid=? "
                        "AND relation IN ('public.assignments'::regclass,'public.classroom_todos'::regclass)",
                        (state["pid"],)).fetchone()["n"]
                    self.assertEqual(0, count, "Catalog-only startup must retain no business-table locks")
                # This is the opposite write order to the old index batch. It
                # must proceed even while the initializer transaction is open.
                dml.execute("UPDATE assignments SET value=value+1 WHERE id=1")
                dml.commit()
            finally:
                release.set()
            future.result(timeout=6)

    def test_missing_index_is_created_but_transaction_and_timeouts_belong_to_caller(self):
        with self.connect() as conn:
            conn.execute("CREATE TABLE missing_owned (value INTEGER)")
            conn.commit()
            conn.execute("SET statement_timeout='7s'")
            conn.execute("SET lock_timeout='3s'")
            with patch.object(indexes, "collect_postgres_index_statements", return_value=[
                "CREATE INDEX IF NOT EXISTS idx_missing_owned ON missing_owned (value)"
            ]):
                report = indexes.ensure_postgres_performance_indexes(conn)
            self.assertEqual(report, {"created": 1, "existing": 0, "failed": 0, "total": 1})
            guards = conn.execute("SELECT current_setting('statement_timeout') AS s,current_setting('lock_timeout') AS l").fetchone()
            self.assertEqual(("7s", "3s"), (guards["s"], guards["l"]))
            with self.connect() as observer:
                self.assertIsNone(observer.execute("SELECT to_regclass('public.idx_missing_owned') AS i").fetchone()["i"])
            conn.commit()
            self.assertIsNotNone(conn.execute("SELECT to_regclass('public.idx_missing_owned') AS i").fetchone()["i"])

    def test_public_name_collision_is_failure_without_replacing_existing_index(self):
        with self.connect() as conn:
            conn.execute("CREATE TABLE collision_left (value INTEGER)")
            conn.execute("CREATE TABLE collision_right (value INTEGER)")
            conn.execute("CREATE INDEX idx_owned_collision ON collision_left (value)")
            conn.commit()
            before = conn.execute("SELECT pg_get_indexdef('public.idx_owned_collision'::regclass) AS definition").fetchone()["definition"]
            with patch.object(indexes, "collect_postgres_index_statements", return_value=[
                "CREATE INDEX IF NOT EXISTS idx_owned_collision ON collision_right (value)"
            ]):
                report = indexes.ensure_postgres_performance_indexes(conn)
            self.assertEqual((0, 0, 1), (report["created"], report["existing"], report["failed"]))
            self.assertEqual(before, conn.execute("SELECT pg_get_indexdef('public.idx_owned_collision'::regclass) AS definition").fetchone()["definition"])

    def test_failed_concurrent_index_is_rejected_without_catalog_mutation(self):
        with self.connect() as conn:
            conn.execute("CREATE TABLE invalid_owned (value INTEGER)")
            conn.execute("INSERT INTO invalid_owned VALUES (0)")
            conn.commit()
            conn.raw_connection.autocommit = True
            # PostgreSQL itself leaves an invalid index after an interrupted
            # concurrent build; never write its system catalogs in a test.
            with self.assertRaises(Exception):
                conn.execute("CREATE INDEX CONCURRENTLY idx_invalid_owned ON invalid_owned ((10/value))")
            conn.raw_connection.autocommit = False
            before = conn.execute("SELECT indexrelid,indisvalid,indisready FROM pg_index "
                                  "WHERE indexrelid='public.idx_invalid_owned'::regclass").fetchone()
            self.assertFalse(before["indisvalid"])
            with patch.object(indexes, "collect_postgres_index_statements", return_value=[
                "CREATE INDEX IF NOT EXISTS idx_invalid_owned ON invalid_owned (value)"
            ]):
                report = indexes.ensure_postgres_performance_indexes(conn)
            self.assertEqual((0, 0, 1), (report["created"], report["existing"], report["failed"]))
            after = conn.execute("SELECT indexrelid,indisvalid,indisready FROM pg_index "
                                 "WHERE indexrelid='public.idx_invalid_owned'::regclass").fetchone()
            self.assertEqual(dict(before), dict(after))


if __name__ == "__main__":
    unittest.main()
