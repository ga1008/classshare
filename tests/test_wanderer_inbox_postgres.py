"""Native lock, lease, quota and memory races on an explicitly owned empty PG database."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db import schema_wanderers
from classroom_app.services import wanderer_inbox_service as inbox
from classroom_app.services.wanderer_ai_service import _reserve_call
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set, daily_ai_calls
from classroom_app.services.wanderer_memory_service import append_turn, invalidate_thread_cache, load_thread
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires owned offline PostgreSQL")
class WandererInboxPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.name = "lanshare_assessment_rehearsal_inbox_" + uuid.uuid4().hex[:10]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.name}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.name}"')
        finally:
            cls.admin.close()

    def connect(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.name)
        raw.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(raw)

    def setUp(self):
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"))
        self.enterContext(patch.object(schema_wanderers, "get_configured_db_engine", return_value="postgres"))
        self.enterContext(patch.object(schema_wanderers, "_SCHEMA_READY", False))
        self.enterContext(patch.dict(os.environ, {"WANDERER_DM_CONCURRENCY": "2"}))
        folder = self.enterContext(tempfile.TemporaryDirectory(prefix="wanderer-pg-memory-"))
        self.enterContext(patch("classroom_app.config.DATA_DIR", Path(folder)))
        with self.connect() as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            schema_wanderers.ensure_wanderer_schema(conn)
            conn.commit()

    def race(self, count, fn):
        barrier = threading.Barrier(count)
        def run(i):
            barrier.wait(timeout=10)
            return fn(i)
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count)))

    def seed(self, count=1):
        with self.connect() as conn:
            for index in range(count):
                inbox.enqueue(conn, 1, "social", f"dm:{index}", f"student:{index + 100}", index + 1)
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01'")
            conn.commit()

    def test_parallel_workers_respect_global_two_slot_budget(self):
        self.seed(8)
        results = self.race(6, lambda _: inbox.claim(2, factory=self.connect))
        tasks = [row for group in results for row in group]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len({row["id"] for row in tasks}), 2)

    def test_six_claims_of_same_conversation_only_one_owner(self):
        self.seed()
        results = self.race(6, lambda _: inbox.claim(1, factory=self.connect))
        self.assertEqual(sum(len(group) for group in results), 1)

    def test_arrival_during_claim_keeps_lease_and_latest_high_water(self):
        self.seed()
        task = inbox.claim(1, factory=self.connect)[0]
        def arrive(i):
            with self.connect() as conn:
                inbox.enqueue(conn, 1, "social", "dm:0", "student:100", 100 + i)
                conn.commit()
        self.race(6, arrive)
        with self.connect() as conn:
            row = inbox.owned(conn, task)
            self.assertEqual(row["latest_message_id"], 105)
            self.assertEqual(row["state"], "running")
            conn.commit()
        self.assertEqual(inbox.claim(2, factory=self.connect), [])

    def test_expired_worker_is_fenced_after_reclaim(self):
        self.seed()
        old = inbox.claim(1, factory=self.connect)[0]
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET lease_until='2000-01-01'")
            conn.commit()
        fresh = inbox.claim(1, factory=self.connect)[0]
        self.assertFalse(inbox.save_response(old, 1, {"parts": ["stale"]}, factory=self.connect))
        self.assertTrue(inbox.save_response(fresh, 1, {"parts": ["fresh"]}, factory=self.connect))

    def test_parallel_daily_reservation_never_exceeds_existing_cap(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, "daily_ai_call_cap", 3)
            conn.commit()
        results = self.race(8, lambda _: _reserve_call(self.connect, enforce_cap=True, interactive=True))
        self.assertEqual(sum(result[0] for result in results), 3)
        with self.connect() as conn:
            self.assertEqual(daily_ai_calls(conn), 3)

    def test_background_reserves_pending_human_capacity_without_raising_total(self):
        self.seed()
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, "daily_ai_call_cap", 10)
            conn.commit()
        results = self.race(12, lambda _: _reserve_call(self.connect, enforce_cap=True))
        self.assertEqual(sum(result[0] for result in results), 9)
        self.assertTrue(_reserve_call(self.connect, enforce_cap=True, interactive=True)[0])
        self.assertFalse(_reserve_call(self.connect, enforce_cap=True, interactive=True)[0])

    def test_parallel_memory_append_preserves_all_turns(self):
        wanderer = {"id": 1, "slug": "synthetic"}
        def append(i):
            with self.connect() as conn:
                append_turn(conn, wanderer, "social:synthetic", who="peer", text=f"turn-{i}")
                conn.commit()
        self.race(12, append)
        invalidate_thread_cache()
        with self.connect() as conn:
            history = load_thread(conn, 1, "social:synthetic")["history"]
        self.assertEqual({item["text"] for item in history}, {f"turn-{i}" for i in range(12)})
