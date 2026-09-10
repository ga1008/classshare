"""Real SQLite concurrency/token tests with synthetic, freshly verified grants."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation, verify_task_delegation
from classroom_app.services import agent_request_budget_service as budgets
from tests.test_agent_delegation_service import NOW, fixture_connection, issue_fixture


class AgentRequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.engine = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        self.engine.start()
        self.addCleanup(self.engine.stop)
        self.conn = fixture_connection()
        self.addCleanup(self.conn.close)
        ensure_agent_request_budget_schema(self.conn)
        self.grant = self.make_grant()
        self.conn.commit()

    def make_grant(self, *, conn=None, task_id=10, session="teacher-session", purpose="tools"):
        conn = conn or self.conn
        _, issued = issue_fixture(conn, task_id=task_id, session=session, purpose=purpose)
        return verify_task_delegation(conn, issued["token"], purpose=purpose, now=NOW)

    def reserve(self, **overrides):
        return budgets.reserve_agent_request_budget(self.conn, **{
            "grant": self.grant, "channel": "tools", "now": NOW, **overrides,
        })

    def assert_limited(self, fn=None, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            (fn or self.reserve)(**kwargs)
        self.assertEqual(429, caught.exception.status_code)
        self.assertGreaterEqual(int(caught.exception.headers["Retry-After"]), 1)

    def test_reservation_and_release_never_commit_or_issue_runtime_ddl(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        lease = self.reserve()
        self.assertGreater(lease.expires_at, NOW)
        self.assertTrue(self.conn.in_transaction)
        self.assertTrue(budgets.finish_agent_request_budget(self.conn, lease.id, now=NOW))
        self.assertFalse(budgets.finish_agent_request_budget(self.conn, lease.id, status="failed", now=NOW))
        self.assertFalse(any(sql.upper().lstrip().startswith(("CREATE", "ALTER", "COMMIT")) for sql in statements))
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_request_budget_leases").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_request_buckets").fetchone()[0])

    def test_rate_refills_after_wait_and_failed_work_does_not_refund_tokens(self):
        for _ in range(budgets.CHANNEL_BUDGETS["tools"].task.capacity):
            lease = self.reserve()
            budgets.finish_agent_request_budget(self.conn, lease.id, status="failed", now=NOW)
        self.assert_limited()
        self.assertIsNotNone(self.reserve(now=NOW + 1))
        self.assertEqual(9, self.conn.execute("SELECT used_count FROM agent_request_buckets WHERE scope_key='task:10' AND channel='tools'").fetchone()[0])

    def test_concurrency_is_released_on_completion_and_expiry_but_not_rate_refunded(self):
        first = self.reserve(channel="web")
        self.assert_limited(channel="web")
        budgets.finish_agent_request_budget(self.conn, first.id, status="canceled", now=NOW)
        second = self.reserve(channel="web")
        self.assertGreater(second.expires_at, NOW)
        self.assert_limited(channel="web")
        reclaimed = self.reserve(channel="web", now=NOW + 26)
        self.assertNotEqual(second.id, reclaimed.id)
        # A stale cleanup identifies only its own UUID, never the successor.
        budgets.finish_agent_request_budget(self.conn, second.id, now=NOW + 27)
        self.assertEqual("active", self.conn.execute("SELECT status FROM agent_request_budget_leases WHERE id=?", (reclaimed.id,)).fetchone()[0])

    def test_same_user_cannot_bypass_frequency_with_another_task(self):
        for task_id in (13, 14):
            self.conn.execute("INSERT INTO agent_tasks VALUES (?, 'teacher', 7, 7, 'running', NULL)", (task_id,))
        second = self.make_grant(task_id=13)
        third = self.make_grant(task_id=14)
        for grant in (self.grant, second):
            for _ in range(8):
                lease = self.reserve(grant=grant)
                budgets.finish_agent_request_budget(self.conn, lease.id, now=NOW)
        self.assert_limited(grant=third)
        other = self.make_grant(task_id=12, session="other-session")
        self.assertIsNotNone(self.reserve(grant=other))

    def test_teacher_student_same_numeric_id_have_independent_actor_buckets(self):
        student = self.make_grant(task_id=11, session="student-session")
        first = self.reserve(channel="web")
        second = self.reserve(channel="web", grant=student)
        self.assertNotEqual(first.id, second.id)
        scopes = {row[0] for row in self.conn.execute("SELECT scope_key FROM agent_request_buckets")}
        self.assertIn("actor:teacher:7", scopes)
        self.assertIn("actor:student:7", scopes)

    def test_global_and_actor_concurrency_are_enforced_across_tasks(self):
        self.conn.execute("INSERT INTO agent_tasks VALUES (13, 'teacher', 7, 7, 'running', NULL)")
        second = self.make_grant(task_id=13)
        other = self.make_grant(task_id=12, session="other-session")
        base = budgets.CHANNEL_BUDGETS["web"]
        limit = replace(base, actor=replace(base.actor, concurrent=1), platform=replace(base.platform, concurrent=1))
        with patch.dict(budgets.CHANNEL_BUDGETS, {"web": limit}):
            self.reserve(channel="web")
            self.assert_limited(channel="web", grant=second)
            self.assert_limited(channel="web", grant=other)

    def test_task_total_survives_completion_and_new_attempt(self):
        policy = replace(budgets.CHANNEL_BUDGETS["tools"], task_total=1)
        with patch.dict(budgets.CHANNEL_BUDGETS, {"tools": policy}):
            lease = self.reserve()
            budgets.finish_agent_request_budget(self.conn, lease.id, now=NOW)
            attempt = create_task_attempt(self.conn, task_id=10, worker_id="new-worker", startup_key="reclaimed", now=NOW + 301)
            issued = issue_task_delegation(self.conn, task_id=10, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                purpose="tools", scopes=["resources.read"], source_session_id="teacher-session", now=NOW + 301)
            grant = verify_task_delegation(self.conn, issued["token"], purpose="tools", now=NOW + 301)
            self.assertEqual(2, grant.attempt["fencing_token"])
            self.assert_limited(grant=grant, now=NOW + 301)
        self.conn.commit()
        ensure_agent_request_budget_schema(self.conn)
        self.assertEqual(1, self.conn.execute("SELECT used_count FROM agent_request_buckets WHERE scope_key='task:10' AND channel='tools'").fetchone()[0])

    def test_wrong_purpose_and_reused_server_request_id_never_start_second_work(self):
        with self.assertRaises(HTTPException) as purpose:
            self.reserve(channel="model")
        self.assertEqual(403, purpose.exception.status_code)
        lease = self.reserve(request_id="server-request")
        with self.assertRaises(HTTPException) as duplicate:
            self.reserve(request_id="server-request")
        self.assertEqual(409, duplicate.exception.status_code)
        budgets.finish_agent_request_budget(self.conn, lease.id, now=NOW)
        with self.assertRaises(HTTPException):
            self.reserve(request_id="server-request")
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_request_budget_leases").fetchone()[0])

    def test_clock_going_backwards_does_not_refill_or_erase_usage(self):
        first = self.reserve()
        budgets.finish_agent_request_budget(self.conn, first.id, now=NOW)
        second = self.reserve(now=NOW - 100)
        budgets.finish_agent_request_budget(self.conn, second.id, now=NOW)
        row = self.conn.execute("SELECT * FROM agent_request_buckets WHERE scope_key='task:10' AND channel='tools'").fetchone()
        self.assertEqual(NOW * 1000, row["updated_at_ms"])
        self.assertEqual(6, row["tokens"])
        self.assertEqual(2, row["used_count"])

    def test_two_application_connections_cannot_overbook_same_task(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "budget.sqlite")
            seed = fixture_connection(path)
            ensure_agent_request_budget_schema(seed)
            grant = self.make_grant(conn=seed)
            seed.commit()
            seed.close()
            barrier = threading.Barrier(2)

            def reserve():
                conn = sqlite3.connect(path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    lease = budgets.reserve_agent_request_budget(conn, grant=grant, channel="web", now=NOW)
                    conn.commit()
                    return lease.id
                except HTTPException as exc:
                    conn.rollback()
                    return exc.status_code
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(reserve) for _ in range(2)]
                results = [future.result(timeout=10) for future in futures]
            self.assertEqual(1, results.count(429))
            self.assertEqual(1, sum(isinstance(value, str) for value in results))
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM agent_request_budget_leases").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT used_count FROM agent_request_buckets WHERE scope_key='task:10'").fetchone()[0])
            finally:
                conn.close()

    def test_renew_keeps_capacity_without_refunding_or_resurrecting(self):
        lease = self.reserve(channel="web")
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM agent_request_buckets ORDER BY scope_key")]
        self.assertTrue(budgets.renew_agent_request_budget(self.conn, lease.id, now=NOW + 20))
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM agent_request_buckets ORDER BY scope_key")])
        self.assert_limited(channel="web", now=NOW + 26)
        self.assertFalse(budgets.renew_agent_request_budget(self.conn, lease.id, now=NOW + 45))
        successor = self.reserve(channel="web", now=NOW + 46)
        budgets.finish_agent_request_budget(self.conn, successor.id, now=NOW + 47)
        self.assertFalse(budgets.renew_agent_request_budget(self.conn, successor.id, now=NOW + 47))
        self.assertFalse(budgets.renew_agent_request_budget(self.conn, "missing", now=NOW))

    def test_renew_compare_and_swap_cannot_overwrite_concurrent_finish(self):
        lease = self.reserve()
        conn = self.conn

        class FinishBetweenReadAndWrite:
            def execute(self, sql, args=()):
                if "SET expires_at_ms" in sql:
                    budgets.finish_agent_request_budget(conn, lease.id, status="canceled", now=NOW + 1)
                return conn.execute(sql, args)

        self.assertFalse(budgets.renew_agent_request_budget(FinishBetweenReadAndWrite(), lease.id, now=NOW + 1))
        row = self.conn.execute("SELECT status, expires_at_ms FROM agent_request_budget_leases WHERE id=?", (lease.id,)).fetchone()
        self.assertEqual(("canceled", int(lease.expires_at * 1000)), tuple(row))


if __name__ == "__main__":
    unittest.main()
