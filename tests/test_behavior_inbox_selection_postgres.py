"""Opt-in recorder regressions on a physically verified synthetic cluster."""
from contextlib import contextmanager
import os
from pathlib import Path
import unittest
from uuid import uuid4

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row, sqlite_sql_to_psycopg
from classroom_app.db.schema_behavior_inbox import ensure_behavior_inbox_schema
from classroom_app.services.behavior_inbox_service import BehaviorWritePipeline
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class BehaviorInboxSelectionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_recorder_" + uuid4().hex[:16]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port,
                                    database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    @contextmanager
    def connection(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        conn = LanSharePostgresConnection(raw)
        try:
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def setUp(self):
        self.pipeline = BehaviorWritePipeline(lambda *_args, **_kwargs: {})
        with self.connection() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            ensure_behavior_inbox_schema(conn, engine="postgres")
            conn.commit()

    def insert(self, conn, key, actor, *, classroom=1, retry=None):
        conn.execute("""INSERT INTO behavior_write_inbox
            (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,
             display_name,events_json,received_at,retry_at)
            VALUES(?,?,?,?,?,'student','Synthetic','[]','2026-01-01T00:00:00',?)""",
            (key, key, "hash", classroom, actor, retry))

    def select(self, conn):
        return self.pipeline._select_next_receipt(conn, postgres=True)

    def test_locked_head_is_skipped_without_overtaking_or_locking_other_heads(self):
        with self.connection() as setup:
            self.insert(setup, "a", 10)
            self.insert(setup, "b", 10, classroom=2)
            self.insert(setup, "c", 20)
            self.insert(setup, "d", 30)
            setup.commit()
        with self.connection() as locked, self.connection() as worker, self.connection() as observer:
            locked.execute("SELECT batch_key FROM behavior_write_inbox WHERE batch_key='a' FOR UPDATE")
            self.assertEqual("c", self.select(worker)["batch_key"])
            # Only the selected real receipt is locked; ranking does not lock
            # later work or all other actor heads.
            self.assertEqual(2, len(observer.execute("SELECT batch_key FROM behavior_write_inbox "
                "WHERE batch_key IN ('b','d') FOR UPDATE NOWAIT").fetchall()))
            worker.rollback()
            locked.rollback()
            self.assertEqual("a", self.select(worker)["batch_key"])

    def test_future_retry_cross_classroom_tie_and_rollback_visibility(self):
        with self.connection() as conn:
            self.insert(conn, "a", 10, retry="9999-01-01T00:00:00")
            self.insert(conn, "b", 10, classroom=2)
            self.insert(conn, "c", 20)
            conn.commit()
            self.assertEqual("c", self.select(conn)["batch_key"])
            conn.execute("UPDATE behavior_write_inbox SET retry_at=NULL WHERE batch_key='a'")
            self.assertEqual("a", self.select(conn)["batch_key"])
            conn.execute("UPDATE behavior_write_inbox SET state='applied' WHERE batch_key='a'")
            self.assertEqual("b", self.select(conn)["batch_key"])
            conn.rollback()
            self.assertEqual("c", self.select(conn)["batch_key"])

    def test_stale_statistics_do_not_rescan_pending_backlog_per_candidate(self):
        # Reproduce the actual distribution: ANALYZE an idle retained inbox,
        # then receive three batches per actor before statistics catch up.
        with self.connection() as conn:
            conn.execute("ALTER TABLE behavior_write_inbox SET (autovacuum_enabled=false)")
            conn.execute("""INSERT INTO behavior_write_inbox
                (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,
                 display_name,events_json,received_at,state,result_json)
                SELECT md5('old'||g::text),g::text,'hash',1,1+mod(g,200),'student',
                    'Synthetic','[]','2026-01-01T00:00:00','applied',repeat('x',600)
                FROM generate_series(1,50000) g""")
            conn.commit()
            conn._raw_connection.autocommit = True
            conn.execute("VACUUM (ANALYZE) behavior_write_inbox")
            conn._raw_connection.autocommit = False
            conn.execute("""INSERT INTO behavior_write_inbox
                (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,
                 display_name,events_json,received_at)
                SELECT md5('new'||g::text),g::text,'hash',1,1+mod((g-1)/3,200),'student',
                    'Synthetic','[]','2026-01-01T01:00:00'
                FROM generate_series(1,600) g""")
            conn.commit()

            class Capture:
                def execute(self, statement, params):
                    self.statement, self.params = statement, params
                    return conn.execute(statement, params)

            capture = Capture()
            self.pipeline._select_next_receipt(capture, postgres=True)
            statement = sqlite_sql_to_psycopg(capture.statement).replace("%s", "$1")
            conn.execute("PREPARE recorder_next(text) AS " + statement)
            for mode in ("force_custom_plan", "force_generic_plan", "auto"):
                with self.subTest(plan_cache_mode=mode):
                    conn.execute("SET plan_cache_mode=" + mode)
                    for _ in range(12):
                        self.assertIsNotNone(conn.execute("EXECUTE recorder_next('2026-01-02T00:00:00')").fetchone())
                    plan = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
                        "EXECUTE recorder_next('2026-01-02T00:00:00')").fetchone()[0][0]["Plan"]

                    def visits(node):
                        return node.get("Actual Rows", 0) * node.get("Actual Loops", 0) + sum(
                            visits(child) for child in node.get("Plans", []))

                    # This is a work bound, not a hardware-dependent timing
                    # assertion or a requirement for particular index names.
                    self.assertLess(visits(plan), 10000, plan)
                    self.assertLess(plan["Shared Hit Blocks"] + plan["Shared Read Blocks"], 10000, plan)


if __name__ == "__main__":
    unittest.main()
