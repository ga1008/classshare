"""Daily review reservations use a shared DB, never a provider or real key."""
import contextlib
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import dotenv

with mock.patch.object(dotenv, "load_dotenv", return_value=False), mock.patch.dict(os.environ, {"DB_ENGINE": "sqlite", "AI_DURABLE_JOBS_ENABLED": "false"}, clear=True):
    from classroom_app.services import ai_usage_budget_service as service
    from classroom_app.db.schema_ai_jobs import AI_JOB_POSTGRES_RUNTIME_TABLES


class AdjudicationQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "review.db"
        with self.connect() as conn:
            for name in ("ai_review_daily_counters", "ai_review_reservations"):
                conn.execute(AI_JOB_POSTGRES_RUNTIME_TABLES[name])
            conn.commit()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(service, "get_db_connection", side_effect=self.connect))
        self.stack.enter_context(mock.patch.object(service, "get_configured_db_engine", return_value="sqlite"))

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def reserve(self, key, offering=1, **limits):
        return service.reserve_grading_review(logical_call_id=key, class_offering_id=offering,
            policy_version="test", reasons=["evidence_conflict"], **limits)

    def counts(self):
        with self.connect() as conn:
            return {(r["scope_type"], r["scope_id"]): r["reserved_count"]
                for r in conn.execute("SELECT * FROM ai_review_daily_counters")}

    def test_parallel_same_course_reserves_exactly_three(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda n: self.reserve(f"same:{n}"), range(24)))
        self.assertEqual(3, sum(r["allowed"] for r in results))
        self.assertEqual({("global", "*"): 3, ("offering", "1"): 3}, self.counts())

    def test_parallel_courses_global_limit_and_no_course_still_counts(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda n: self.reserve(f"site:{n}", n + 1 if n % 2 else None), range(30)))
        self.assertEqual(10, sum(r["allowed"] for r in results))
        self.assertEqual(10, self.counts()[("global", "*")])
        self.assertTrue(all(count <= 3 for (scope, _), count in self.counts().items() if scope == "offering"))

    def test_replayed_reservation_and_concurrent_dispatch_never_double_spend(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.reserve("one-call"), range(16)))
        ids = {r["reservation_id"] for r in results}
        self.assertEqual(1, len(ids))
        self.assertEqual(1, self.counts()[("global", "*")])
        reservation_id = ids.pop()
        with ThreadPoolExecutor(max_workers=8) as pool:
            dispatched = list(pool.map(lambda n: service.mark_grading_review_sent(reservation_id, f"worker-{n}"), range(8)))
        self.assertEqual(1, sum(dispatched))
        owner = f"worker-{dispatched.index(True)}"
        self.assertTrue(service.mark_grading_review_sent(reservation_id, owner))  # Same invocation's 429 retry.
        self.assertFalse(service.release_unsent_grading_review(reservation_id))
        self.assertTrue(service.finish_grading_review(reservation_id, owner, status="unknown"))
        self.assertFalse(self.reserve("one-call")["allowed"])
        self.assertEqual(1, self.counts()[("global", "*")])

    def test_only_proven_unsent_cancel_releases_both_scopes_once(self):
        reservation = self.reserve("cancel")
        self.assertTrue(service.release_unsent_grading_review(reservation["reservation_id"]))
        self.assertFalse(service.release_unsent_grading_review(reservation["reservation_id"]))
        self.assertEqual({("global", "*"): 0, ("offering", "1"): 0}, self.counts())
        self.assertFalse(self.reserve("cancel")["allowed"])

    def test_project_day_rollover_and_old_idempotency_survive_counter_retention(self):
        day = datetime(2026, 9, 7, 23, 59, tzinfo=timezone(timedelta(hours=8)))
        with mock.patch.object(service, "_review_now", return_value=day):
            first = self.reserve("old")
            for i in range(2):
                self.reserve(f"daily:{i}")
            self.assertFalse(self.reserve("full")["allowed"])
        with mock.patch.object(service, "_review_now", return_value=day + timedelta(minutes=2)):
            self.assertTrue(self.reserve("next-day")["allowed"])
        with mock.patch.object(service, "_review_now", return_value=day + timedelta(days=92)):
            self.assertTrue(self.reserve("new")["allowed"])
            replay = self.reserve("old")
            self.assertEqual(first["reservation_id"], replay["reservation_id"])
            self.assertFalse(replay["allowed"])
            self.assertEqual("reservation_day_expired", replay["reason"])
            self.assertFalse(service.mark_grading_review_sent(first["reservation_id"], "new-worker"))
        with self.connect() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM ai_review_daily_counters").fetchone()[0])

    def test_replay_cannot_change_course_and_postgres_locks_global_before_course(self):
        self.reserve("same-scope", offering=7)
        with self.assertRaisesRegex(service.AIUsageBudgetError, "course or policy"):
            self.reserve("same-scope", offering=8)
        statements = []
        base_connect = self.connect
        class Proxy:
            def __init__(self, conn):
                self.conn = conn
            def execute(self, sql, params=()):
                statements.append((sql, params))
                return self.conn.execute(sql.replace(" FOR UPDATE", ""), params)
            def commit(self):
                self.conn.commit()
            def rollback(self):
                self.conn.rollback()
        @contextlib.contextmanager
        def pg_contract_connection():
            with base_connect() as conn:
                yield Proxy(conn)
        with mock.patch.object(service, "get_db_connection", side_effect=pg_contract_connection), mock.patch.object(service, "get_configured_db_engine", return_value="postgres"):
            self.assertTrue(self.reserve("pg-contract", offering=8)["allowed"])
        locked = [params[1] for sql, params in statements if sql.endswith("FOR UPDATE")]
        self.assertEqual(["global", "offering"], locked)

    def test_counter_increment_failure_rolls_back_reservation_and_both_scopes(self):
        base_connect = self.connect
        class ZeroCursor:
            rowcount = 0
        class Proxy:
            def __init__(self, conn):
                self.conn = conn
            def execute(self, sql, params=()):
                if sql.startswith("UPDATE ai_review_daily_counters SET reserved_count=reserved_count+1") and params[2] == "offering":
                    return ZeroCursor()
                return self.conn.execute(sql, params)
            def commit(self):
                self.conn.commit()
            def rollback(self):
                self.conn.rollback()
        @contextlib.contextmanager
        def failing_connection():
            with base_connect() as conn:
                yield Proxy(conn)
        with mock.patch.object(service, "get_db_connection", side_effect=failing_connection):
            with self.assertRaises(service.AIUsageBudgetError):
                self.reserve("rollback")
        self.assertEqual({}, self.counts())
        with self.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM ai_review_reservations").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
