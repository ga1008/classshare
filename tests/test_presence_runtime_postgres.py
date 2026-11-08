"""Real PG budgets and monotonic replay in an exclusively owned synthetic DB."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from classroom_app.db import postgres
from classroom_app.services import presence_service as presence
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class PresenceRuntimePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_presence_" + uuid4().hex[:12]
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

    def connect(self, connection_class=postgres.LanSharePostgresConnection, *, lock="5s", statement="2s"):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = postgres.sqlite_compatible_dict_row
        conn = connection_class(raw)
        conn.execute("SELECT set_config('lock_timeout', ?, false), set_config('statement_timeout', ?, false)",
                     (lock, statement))
        raw.commit()  # Fixture setup is not the synthetic unknown-commit trigger.
        return conn

    def setUp(self):
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"))
        with self.connect() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute("CREATE TABLE user_presence(identity TEXT PRIMARY KEY,last_seen_at TEXT NOT NULL)")

    def stamps(self):
        with self.connect() as conn:
            return {row["identity"]: row["last_seen_at"] for row in conn.execute(
                "SELECT identity,last_seen_at FROM user_presence ORDER BY identity").fetchall()}

    def test_multirow_monotonic_upsert_and_real_stricter_session_budgets(self):
        observed, commands, checkout = [], [], []
        class Probe(postgres.LanSharePostgresConnection):
            def execute(inner, sql, params=None):
                compact = " ".join(sql.split())
                commands.append(compact)
                result = super().execute(sql, params)
                if compact.startswith("SELECT set_config('lock_timeout', CASE"):
                    values = inner.raw_connection.execute(
                        "SELECT current_setting('lock_timeout') AS lock_timeout, "
                        "current_setting('statement_timeout') AS statement_timeout").fetchone()
                    observed.append((values["lock_timeout"], values["statement_timeout"]))
                return result
        def factory(**kwargs):
            checkout.append(kwargs)
            return self.connect(Probe, **settings)
        settings = {"lock": "5s", "statement": "2s"}
        with patch.object(postgres, "connect_postgres", side_effect=factory):
            self.assertTrue(presence._persist_last_seen_batch([
                ("student:1", "2026-11-09T09:02:00"), ("student:2", "2026-11-09T09:03:00")])["ok"])
            settings.update(lock="70ms", statement="90ms")
            self.assertTrue(presence._persist_last_seen_batch([
                ("student:1", "2026-11-09T09:01:00"), ("student:2", "2026-11-09T09:04:00"),
                ("student:3", "2026-11-09T09:05:00")])["ok"])
        self.assertEqual([("250ms", "1s"), ("70ms", "90ms")], observed)
        self.assertEqual([{"checkout_timeout": 1.0}] * 2, checkout)
        inserts = [sql for sql in commands if sql.startswith("INSERT INTO user_presence")]
        self.assertEqual(2, len(inserts), "Each batch is a single multi-row UPSERT")
        self.assertFalse(any(sql.startswith(("CREATE", "ALTER")) for sql in commands))
        self.assertEqual({"student:1": "2026-11-09T09:02:00", "student:2": "2026-11-09T09:04:00",
                          "student:3": "2026-11-09T09:05:00"}, self.stamps())

    def test_real_row_lock_timeout_rolls_back_whole_batch_then_original_stamps_retry(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO user_presence VALUES('free','2026-11-09T09:00:00'),"
                         "('locked','2026-11-09T09:00:00')")
        entered, detail = Event(), {}
        class Probe(postgres.LanSharePostgresConnection):
            def execute(inner, sql, params=None):
                if sql.startswith("INSERT INTO user_presence"):
                    detail["pid"] = inner.raw_connection.info.backend_pid
                    entered.set()
                return super().execute(sql, params)
        batch = [("free", "2026-11-09T09:01:00"), ("locked", "2026-11-09T09:02:00")]
        with self.connect() as held, ThreadPoolExecutor(max_workers=1) as workers, \
                patch.object(postgres, "connect_postgres", side_effect=lambda **_: self.connect(Probe)):
            held.execute("UPDATE user_presence SET last_seen_at=last_seen_at WHERE identity='locked'")
            held_pid = held.raw_connection.info.backend_pid
            future = workers.submit(presence._persist_last_seen_batch, batch)
            self.assertTrue(entered.wait(2))
            blocked = False
            with self.connect() as observer:
                until = time.monotonic() + .2
                while time.monotonic() < until:
                    blocked = observer.execute("SELECT ?=ANY(pg_blocking_pids(?)) AS blocked",
                                               (held_pid, detail["pid"])).fetchone()["blocked"]
                    if blocked:
                        break
                    time.sleep(.005)
            self.assertTrue(blocked, "Must observe a real row-lock wait, not a mocked timeout")
            outcome = future.result(timeout=2)
            self.assertFalse(outcome["ok"])
            self.assertFalse(outcome["unknown_commit"])
            self.assertEqual("LockNotAvailable", outcome["error"])
            self.assertEqual({"free": "2026-11-09T09:00:00", "locked": "2026-11-09T09:00:00"},
                             self.stamps(), "A preceding row in the same batch must roll back too")
            held.rollback()
            self.assertTrue(presence._persist_last_seen_batch(batch)["ok"])
        self.assertEqual(dict(batch), self.stamps())

    def test_actual_committed_unknown_ack_replays_monotonically_without_duplicate_rows(self):
        lost = {"once": True}
        class CommitOnceLost(postgres.LanSharePostgresConnection):
            def commit(inner):
                super().commit()
                if lost["once"]:
                    lost["once"] = False
                    raise RuntimeError("Synthetic lost commit acknowledgement")
        batch = [("student:1", "2026-11-09T09:02:00"), ("student:2", "2026-11-09T09:03:00")]
        with patch.object(postgres, "connect_postgres", side_effect=lambda **_: self.connect(CommitOnceLost)):
            outcome = presence._persist_last_seen_batch(batch)
            self.assertFalse(outcome["ok"])
            self.assertTrue(outcome["unknown_commit"])
            self.assertEqual(dict(batch), self.stamps(), "The commit really succeeded before the fake lost ACK")
            self.assertTrue(presence._persist_last_seen_batch(batch)["ok"])
            self.assertTrue(presence._persist_last_seen_batch([
                ("student:1", "2026-11-09T09:01:00"), ("student:2", "2026-11-09T09:02:00")])["ok"])
        self.assertEqual(dict(batch), self.stamps())


if __name__ == "__main__":
    unittest.main()
