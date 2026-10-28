"""Real native admission/migration races on an explicitly owned disposable cluster."""
import os
import unittest
from unittest.mock import patch

from classroom_app.db import schema_wanderers
from classroom_app.services import wanderer_inbox_service as inbox
from tests import test_wanderer_inbox_postgres as fixture


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires owned offline PostgreSQL")
class WandererSupportPostgresTest(unittest.TestCase):
    setUpClass = classmethod(fixture.WandererInboxPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.WandererInboxPostgresTest.tearDownClass.__func__)
    setUp = fixture.WandererInboxPostgresTest.setUp
    connect = fixture.WandererInboxPostgresTest.connect
    race = fixture.WandererInboxPostgresTest.race

    def seed(self, count=8):
        with self.connect() as conn:
            for i in range(count):
                inbox.enqueue(conn, 1, "social", f"normal:{i}", "student:100", i+1)
                inbox.enqueue(conn, 1, "social", f"urgent:{i}", "student:100", i+100, support_urgent=True)
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01'")
            conn.commit()

    def test_parallel_admission_keeps_two_normal_and_one_safety_without_double_claim(self):
        self.seed()
        results = self.race(8, lambda i: inbox.claim_safety(factory=self.connect) if i % 2 else inbox.claim(2, factory=self.connect))
        tasks = [row for rows in results for row in rows]
        self.assertEqual(sum(t["lease_lane"] == "normal" for t in tasks), 2)
        self.assertEqual(sum(t["lease_lane"] == "safety" for t in tasks), 1)
        self.assertEqual(len({t["id"] for t in tasks}), 3)

    def test_new_urgency_cannot_move_running_normal_lease_to_extra_lane(self):
        self.seed(1)
        task = inbox.claim(1, factory=self.connect)[0]
        with self.connect() as conn:
            inbox.enqueue(conn, 1, "social", task["conversation_key"], "student:100", 999, support_urgent=True)
            row = inbox.owned(conn, task)
            self.assertEqual(row["lease_lane"], "normal")
            self.assertEqual(row["lease_token"], task["lease_token"])
            self.assertEqual(row["support_urgent"], 1)
            conn.commit()
        safety = inbox.claim_safety(factory=self.connect)
        self.assertEqual(len(safety), 1)
        self.assertNotEqual(safety[0]["id"], task["id"])

    def test_expired_safety_lease_is_fenced_and_resumes_existing_response(self):
        self.seed(1)
        old = inbox.claim_safety(factory=self.connect)[0]
        self.assertTrue(inbox.save_response(old, 100, {"parts": ["有限支持"]}, factory=self.connect))
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET lease_until='2000-01-01' WHERE id=?", (old["id"],))
            conn.commit()
        fresh = inbox.claim_safety(factory=self.connect)[0]
        self.assertEqual(fresh["id"], old["id"])
        self.assertNotEqual(fresh["lease_token"], old["lease_token"])
        self.assertTrue(fresh["response_json"])
        self.assertFalse(inbox.save_response(old, 100, {"parts": ["过期"]}, factory=self.connect))

    def test_old_inbox_without_support_columns_migrates_before_new_index(self):
        self.seed(1)
        with self.connect() as conn:
            conn.execute("ALTER TABLE wanderer_social_inbox DROP COLUMN support_urgent CASCADE")
            conn.execute("ALTER TABLE wanderer_social_inbox DROP COLUMN lease_lane")
            conn.execute("ALTER TABLE wanderer_social_inbox DROP COLUMN support_handled_message_id")
            before = [dict(r) for r in conn.execute("SELECT * FROM wanderer_social_inbox ORDER BY id")]
            conn.commit()
        for _ in range(2):
            with patch.object(schema_wanderers, "_SCHEMA_READY", False), self.connect() as conn:
                schema_wanderers.ensure_wanderer_schema(conn, engine="postgres")
                conn.commit()
        with self.connect() as conn:
            after = [dict(r) for r in conn.execute("SELECT * FROM wanderer_social_inbox ORDER BY id")]
            self.assertEqual([{k:v for k,v in r.items() if k not in {"support_urgent","lease_lane","support_handled_message_id"}} for r in after], before)
            self.assertTrue(all(r["support_urgent"] == 0 and r["lease_lane"] == "normal" and r["support_handled_message_id"] == 0 for r in after))
            self.assertIsNotNone(conn.execute("SELECT 1 FROM pg_indexes WHERE indexname='idx_wanderer_inbox_support_due'").fetchone())
