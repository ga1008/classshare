"""Opt-in intake races in an exclusively created, physically verified PG database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_behavior_inbox import ensure_behavior_inbox_schema
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.services import behavior_inbox_service as inbox
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires an explicitly owned offline PostgreSQL cluster")
class BehaviorIntakePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_intake_" + uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def connect(self, connection_class=LanSharePostgresConnection):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        conn = connection_class(raw)
        conn.execute("SET lock_timeout='2s'")
        conn.execute("SET statement_timeout='5s'")
        raw.commit()
        return conn

    def setUp(self):
        self.enterContext(patch.object(inbox, "get_configured_db_engine", return_value="postgres"))
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"))
        self.enterContext(patch.object(inbox, "BEHAVIOR_WRITE_QUEUE_SIZE", 1))
        self.pipeline = inbox.BehaviorWritePipeline(lambda *_args, **_kwargs: {})
        self.enterContext(patch.object(self.pipeline, "start", return_value=None))
        with self.connect() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute("CREATE TABLE class_offerings(id BIGINT PRIMARY KEY)")
            conn.execute("CREATE TABLE students(id BIGINT PRIMARY KEY)")
            conn.execute("INSERT INTO class_offerings VALUES(11)")
            conn.execute("INSERT INTO students VALUES(7),(8)")
            conn.execute("CREATE TABLE business_counter(id BIGINT PRIMARY KEY,value INTEGER,dirty_version INTEGER)")
            conn.execute("INSERT INTO business_counter VALUES(1,0,0)")
            ensure_behavior_inbox_schema(conn, engine="postgres")
            conn.commit()

    def request(self, *, batch=None, actor=7, text="Synthetic intake"):
        return inbox.BehaviorWriteRequest(
            class_offering_id=11, user_pk=actor, user_role="student", display_name="Synthetic",
            page_key="classroom", events=[{"action_type": "page_action", "summary_text": text, "payload": {}}],
            **({"batch_id": batch} if batch is not None else {}),
        )

    def persist(self, conn, request):
        payload_hash = hashlib.sha256(json.dumps({"events": request.events, "page_key": request.page_key},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([request.user_role, request.user_pk, request.class_offering_id, request.batch_id]).encode()).hexdigest()
        return self.pipeline._persist_in_connection(conn, request, key, payload_hash, datetime.now().isoformat())

    def race_after_initial_miss(self, first, second=None, *, advance=None, delete_actor=None):
        entering, detail = Event(), {}
        class Probe(LanSharePostgresConnection):
            def execute(inner, sql, params=None):
                compact = " ".join(sql.split())
                if ((compact == "SELECT pg_advisory_xact_lock(?, ?)" and tuple(params or ()) == inbox.CAPACITY_LOCK)
                        or (delete_actor is not None and compact == "SELECT id FROM students WHERE id = ? FOR UPDATE")):
                    detail["pid"] = inner.raw_connection.info.backend_pid
                    entering.set()
                return super().execute(sql, params)
        def contender():
            with self.connect(Probe) as conn:
                try:
                    result = (inbox.prepare_behavior_deletion(conn, user_role="student", user_pk=delete_actor)
                              if delete_actor is not None else self.persist(conn, second))
                    conn.commit()
                    return result
                except (inbox.BehaviorBatchConflict, inbox.BehaviorDeletionPending, DatabaseBusyError) as exc:
                    conn.rollback()
                    return exc
        with self.connect() as held, ThreadPoolExecutor(max_workers=1) as workers:
            self.persist(held, first)
            held_pid = held.raw_connection.info.backend_pid
            future = workers.submit(contender)
            try:
                self.assertTrue(entering.wait(2), "Contender did not reach the real capacity lock")
                blocked = False
                until = time.monotonic()+1
                with self.connect() as observer:
                    while time.monotonic()<until:
                        blocked = observer.execute("SELECT ?=ANY(pg_blocking_pids(?)) AS blocked",
                                                   (held_pid, detail["pid"])).fetchone()["blocked"]
                        if blocked:
                            break
                        time.sleep(.005)
                self.assertTrue(blocked, "Test must observe actual PG advisory blocking")
                if advance:
                    advance()
                held.commit()
                return future.result(timeout=4)
            finally:
                held.rollback()

    def test_lock_wait_rechecks_fresh_snapshot_existing_hash_and_expiry_before_full_capacity(self):
        now = datetime(2026, 11, 9, 9)
        clock = {"now": now}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]
        batch = "b1_"+str(int((now-timedelta(days=7)).timestamp()*1000))+"_"+uuid4().hex
        with patch.object(inbox, "datetime", Clock):
            result = self.race_after_initial_miss(self.request(batch=batch), self.request(batch=batch),
                advance=lambda: clock.update(now=now+timedelta(milliseconds=1)))
        self.assertEqual("durable_queued", result["write_mode"])
        # The existing row still wins when full and expired; conflicting input
        # remains a conflict, rather than being disguised as capacity pressure.
        with self.connect() as conn:
            with self.assertRaises(inbox.BehaviorBatchConflict) as raised:
                self.persist(conn, self.request(batch=batch, text="Different immutable body"))
            self.assertEqual("batch_payload_conflict", raised.exception.error_code)
            conn.rollback()
        with self.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])
            conn.execute("DELETE FROM behavior_write_inbox")
            conn.commit()
        # Both contenders initially miss. The second caller's different body
        # must remain a hash conflict after its lock wait, even when its age
        # check has expired and the first caller occupied the last slot.
        clock["now"] = now
        second_batch = "b1_"+str(int((now-timedelta(days=7)).timestamp()*1000))+"_"+uuid4().hex
        with patch.object(inbox, "datetime", Clock):
            conflict = self.race_after_initial_miss(self.request(batch=second_batch),
                self.request(batch=second_batch, text="Racing different immutable body"),
                advance=lambda: clock.update(now=now+timedelta(milliseconds=1)))
        self.assertIsInstance(conflict, inbox.BehaviorBatchConflict)
        self.assertEqual("batch_payload_conflict", conflict.error_code)
        with self.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])

    def test_last_slot_is_atomic_and_quarantined_receipts_still_occupy_capacity(self):
        result = self.race_after_initial_miss(self.request(), self.request(actor=8))
        self.assertIsInstance(result, DatabaseBusyError)
        with self.connect() as conn:
            conn.execute("UPDATE behavior_write_inbox SET state='quarantined',last_error='synthetic' WHERE user_pk=7")
            conn.commit()
            with self.assertRaises(DatabaseBusyError):
                self.persist(conn, self.request(actor=8))
            conn.rollback()
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])
            with self.assertRaises(inbox.BehaviorDeletionPending):
                inbox.prepare_behavior_deletion(conn, user_role="student", user_pk=7)
            conn.rollback()
            conn.execute("DELETE FROM behavior_write_inbox")
            conn.commit()
        # An uncommitted intake's parent KEY SHARE must fence actual deletion;
        # after commit the deletion guard must see, and retain, accepted work.
        blocked_delete = self.race_after_initial_miss(self.request(actor=8), delete_actor=8)
        self.assertIsInstance(blocked_delete, inbox.BehaviorDeletionPending)

    def test_existing_states_and_invalid_dates_preserve_receipt_priority_and_one_post_lock_statement(self):
        request = self.request(batch="opaque-internal-receipt")
        statements = []
        class Probe(LanSharePostgresConnection):
            def execute(inner, sql, params=None):
                statements.append(" ".join(sql.split()))
                return super().execute(sql, params)
        with self.connect(Probe) as conn:
            self.persist(conn, request)
            lock_index = statements.index("SELECT pg_advisory_xact_lock(?, ?)")
            self.assertEqual(1, len(statements[lock_index+1:]), "Old three-round-trip intake must fail this performance contract")
            conn.commit()
            for state in ("pending", "applied", "quarantined", "discarded"):
                conn.execute("UPDATE behavior_write_inbox SET state=?,result_json=?", (state, '{"retired":true}'))
                conn.commit()
                self.assertEqual("synced" if state=="applied" else "durable_queued" if state=="pending" else state,
                                 self.persist(conn, request)["write_mode"])
                conn.rollback()
            for batch, code in (("b1_invalid_stamp", "batch_timestamp_invalid"),
                                ("b1_1000000000000_expired", "batch_expired"),
                                ("b1_9999999999999_future", "batch_expired")):
                with self.assertRaises(inbox.BehaviorBatchConflict) as raised:
                    self.persist(conn, self.request(batch=batch))
                self.assertEqual(code, raised.exception.error_code)
                conn.rollback()
            # Retired receipt replay precedes even removed-parent validation.
            conn.execute("DELETE FROM class_offerings")
            conn.execute("DELETE FROM students")
            conn.commit()
            self.assertEqual("discarded", self.persist(conn, request)["write_mode"])

    def test_caller_savepoint_outer_rollback_and_unknown_commit_retry_keep_one_receipt(self):
        request = self.request()
        with self.connect() as conn:
            conn.execute("UPDATE business_counter SET value=1,dirty_version=1")
            result = self.pipeline.submit(request, conn=conn)
            self.assertEqual("transaction_pending", result["write_mode"])
            self.assertEqual(0, result["durable_event_count"])
            conn.rollback()
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])
            self.assertEqual(0, conn.execute("SELECT value FROM business_counter").fetchone()["value"])
            self.persist(conn, self.request())
            conn.commit()
            conn.execute("UPDATE business_counter SET value=2,dirty_version=2")
            with self.assertRaises(DatabaseBusyError):
                self.pipeline.submit(request, conn=conn)
            self.assertEqual("2s", conn.execute("SELECT current_setting('lock_timeout') AS value").fetchone()["value"])
            conn.execute("UPDATE business_counter SET value=3")
            conn.commit()
            self.assertEqual(3, conn.execute("SELECT value FROM business_counter").fetchone()["value"])
            conn.execute("DELETE FROM behavior_write_inbox")
            conn.commit()
        lost = {"once": True}
        class CommitOnceLost(LanSharePostgresConnection):
            def commit(inner):
                super().commit()
                if lost["once"]:
                    lost["once"] = False
                    raise RuntimeError("Synthetic lost commit acknowledgement")
        with patch.object(inbox, "get_db_connection", side_effect=lambda: self.connect(CommitOnceLost)):
            result = self.pipeline.submit(request)
        self.assertEqual("durable_queued", result["write_mode"])
        self.assertEqual(1, self.pipeline.stats()["unknown_commit_count"])
        with self.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])


if __name__ == "__main__":
    unittest.main()
