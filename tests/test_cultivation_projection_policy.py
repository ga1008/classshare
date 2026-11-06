"""Candidate B policy closure, not a wall-clock capacity certificate.

Run only after candidate B is applied, through tools/test_backend.py. The
controlled clock models service opportunities; publication/reads use real
isolated SQLite rows, stages, events and version/CAS code.
"""
from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from classroom_app.database import get_db_connection
from classroom_app.services import learning_progress_service as progress
from classroom_app.services import scheduled_task_handlers as handlers
from classroom_app.services import scheduled_task_service as scheduler
from tests import test_learning_progress_snapshots as snapshot_fixture
from tests.sqlite_database_fixture import isolated_sqlite_database


class ProjectionBudgetAndDiscoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = snapshot_fixture.LearningProgressSnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.conn = fixture.conn
        self.conn.execute("INSERT INTO class_offerings(id,class_id,course_id,teacher_id) VALUES(1,1,1,1)")
        self.conn.executemany("INSERT INTO students(id,class_id,name,student_id_number) VALUES(?,1,?,?)",
                              [(student, f"Synthetic {student}", f"S{student}") for student in range(1, 201)])
        self.conn.commit()

    @contextmanager
    def connection(self):
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def test_manual_call_keeps_quarter_discovery_and_existing_large_budget(self):
        with patch.object(progress, "get_db_connection", self.connection), \
             patch.object(progress, "_build_learning_metrics", return_value=snapshot_fixture._metrics(1, task=1)):
            small = progress.refresh_pending_learning_snapshots(limit=8, time_budget_seconds=30)
            manual = progress.refresh_pending_learning_snapshots(limit=100, time_budget_seconds=30)
        self.assertEqual((2, 2), (small["seeded"], small["refreshed"]))
        self.assertEqual((25, 25), (manual["seeded"], manual["refreshed"]))

    def test_budget_finishes_current_atomic_key_then_stops(self):
        for student in range(1, 21):
            progress.mark_student_learning_progress_dirty(self.conn, 1, student)
        self.conn.commit()
        clock = [0.0]
        built = []
        def metrics(_conn, _offering, student):
            built.append(student)
            clock[0] += 0.31
            return snapshot_fixture._metrics(1, task=1)
        with patch.object(progress, "get_db_connection", self.connection), \
             patch.object(progress, "_pending_learning_snapshot_keys", return_value=[]), \
             patch.object(progress, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
             patch.object(progress, "_build_learning_metrics", side_effect=metrics):
            result = progress.refresh_pending_learning_snapshots(limit=25, time_budget_seconds=0.75,
                discovery_limit=25, discovery_time_budget_seconds=0.1)
        self.assertEqual((3, 3, 0), (result["checked"], result["refreshed"], result["failed"]))
        self.assertEqual([1, 2, 3], built)
        self.assertGreater(clock[0], 0.75, "A live publication is completed rather than cancelled at the budget")
        self.assertEqual(17, self.conn.execute("SELECT COUNT(*) FROM learning_progress_snapshots WHERE dirty=1").fetchone()[0])
        for student in built:
            row = self.conn.execute("SELECT dirty_version,calculated_version,score FROM learning_progress_snapshots WHERE student_id=?", (student,)).fetchone()
            self.assertEqual((1, 1, 1), tuple(row))

    def test_short_discovery_yields_to_oldest_dirty_without_resetting_its_age(self):
        progress.mark_student_learning_progress_dirty(self.conn, 1, 200)
        self.conn.execute("UPDATE learning_progress_snapshots SET dirty_at='2000-01-01' WHERE student_id=200")
        self.conn.commit()
        clock = [0.0]
        built = []
        original_mark = progress.mark_student_learning_progress_dirty
        def mark(*args, **kwargs):
            original_mark(*args, **kwargs)
            clock[0] += 0.06
        def metrics(_conn, _offering, student):
            built.append(student)
            clock[0] += 0.2
            return snapshot_fixture._metrics(1, task=1)
        with patch.object(progress, "get_db_connection", self.connection), \
             patch.object(progress, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
             patch.object(progress, "mark_student_learning_progress_dirty", side_effect=mark), \
             patch.object(progress, "_build_learning_metrics", side_effect=metrics):
            result = progress.refresh_pending_learning_snapshots(limit=25, time_budget_seconds=0.75,
                discovery_limit=25, discovery_time_budget_seconds=0.1)
        self.assertEqual(2, result["seeded"])
        self.assertEqual(200, built[0])
        self.assertGreater(result["refreshed"], 0)

    def test_200_initial_building_reads_close_through_bounded_real_publications(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        for student in range(1, 201):
            state = progress.get_student_learning_state(self.conn, 1, student)
            self.assertEqual("building", state["snapshot_status"])
            self.assertIsNone(state["score"])
        self.conn.set_trace_callback(None)
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for sql in statements))
        clock = [5.0]
        results = []
        original_mark = progress.mark_student_learning_progress_dirty
        def mark(*args, **kwargs):
            original_mark(*args, **kwargs)
            clock[0] += 0.004  # 25 discoveries consume the reserved 0.1s.
        def metrics(*_args):
            clock[0] += 0.043145
            return snapshot_fixture._metrics(1, task=1)
        with patch.object(progress, "get_db_connection", self.connection), \
             patch.object(progress, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
             patch.object(progress, "mark_student_learning_progress_dirty", side_effect=mark), \
             patch.object(progress, "_build_learning_metrics", side_effect=metrics):
            for _ in range(30):
                result = progress.refresh_pending_learning_snapshots(limit=25, time_budget_seconds=0.75,
                    discovery_limit=25, discovery_time_budget_seconds=0.1)
                results.append(result)
                count, dirty = self.conn.execute("SELECT COUNT(*),COALESCE(SUM(dirty),0) FROM learning_progress_snapshots").fetchone()
                if (count, dirty) == (200, 0):
                    break
                clock[0] += 5.1
        self.assertEqual((200, 0), (count, dirty))
        self.assertEqual(200, sum(item["seeded"] for item in results))
        self.assertTrue(all(item["seeded"] <= 25 and item["checked"] <= 25 for item in results))
        for student in range(1, 201):
            state = progress.get_student_learning_state(self.conn, 1, student)
            self.assertEqual(("ready", 1), (state["snapshot_status"], state["score"]))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM learning_progress_snapshots WHERE dirty_version<>calculated_version").fetchone()[0])


class ProjectionHandlerPolicyTests(unittest.TestCase):
    def test_old_payload_100_is_capped_but_explicit_smaller_limit_is_preserved(self):
        with patch.object(handlers, "CULTIVATION_SNAPSHOT_REFRESH_LIMIT", 25), \
             patch.object(handlers, "CULTIVATION_SNAPSHOT_REFRESH_BUDGET_SECONDS", 0.75), \
             patch.object(handlers, "CULTIVATION_SNAPSHOT_DISCOVERY_LIMIT", 25), \
             patch.object(handlers, "CULTIVATION_SNAPSHOT_DISCOVERY_BUDGET_SECONDS", 0.1), \
             patch.object(handlers, "refresh_pending_learning_snapshots", return_value={}) as refresh:
            for old_limit, expected in ((100, 25), (7, 7), (None, 25)):
                handlers.handle_cultivation_snapshot_refresh({"payload": {"limit": old_limit}})
                refresh.assert_called_with(limit=expected, time_budget_seconds=0.75,
                    discovery_limit=25, discovery_time_budget_seconds=0.1)

    def test_environment_policy_has_finite_bounds_and_invalid_values_fall_back(self):
        for raw, expected in (("999999", 100), ("0", 1), ("bad", 25), ("1.5", 25)):
            with patch.dict(os.environ, {"UNIT_SNAPSHOT_SETTING": raw}):
                self.assertEqual(expected, progress._bounded_snapshot_setting("UNIT_SNAPSHOT_SETTING", 25, 1, 100))
        for raw, expected in (("NaN", 0.75), ("inf", 0.75), ("-1", 0.1), ("100", 3.0), ("0.5", 0.5)):
            with patch.dict(os.environ, {"UNIT_SNAPSHOT_SETTING": raw}):
                self.assertEqual(expected, progress._bounded_snapshot_setting("UNIT_SNAPSHOT_SETTING", 0.75, 0.1, 3.0))


class ProjectionTimerMaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM scheduled_tasks")

    def row(self, conn, task_id):
        return dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())

    def test_runtime_keeps_healthy_future_time_and_old_payload_startup_can_advance(self):
        future = (datetime.now()+timedelta(hours=2)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            conn.execute("UPDATE scheduled_tasks SET run_at=?,recurrence_seconds=20,payload_json=?,updated_at='2000-01-01' WHERE id=?",
                         (future, json.dumps({"limit": 100}), task_id))
            before = self.row(conn, task_id)
            for _ in range(3):
                progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
            after = self.row(conn, task_id)
            self.assertEqual(progress.CULTIVATION_SNAPSHOT_REFRESH_INTERVAL_SECONDS, after["recurrence_seconds"])
            for field in before.keys()-{"recurrence_seconds"}:
                self.assertEqual(before[field], after[field], field)
            progress.ensure_cultivation_snapshot_refresh_task(conn)
            self.assertLess(self.row(conn, task_id)["run_at"], future)

    def test_runtime_does_not_touch_running_cancelled_or_their_claim_fields(self):
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            for status in ("running", "cancelled"):
                conn.execute("UPDATE scheduled_tasks SET status=?,recurrence_seconds=59,locked_at='2000-01-01',locked_by='immutable-owner',attempt_count=2 WHERE id=?",
                             (status, task_id))
                before = self.row(conn, task_id)
                progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
                self.assertEqual(before, self.row(conn, task_id))

    def test_runtime_failed_cooldown_error_and_other_failed_are_preserved(self):
        timestamp = datetime.now().replace(microsecond=0)
        old = (timestamp-timedelta(minutes=5)).isoformat()
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            other = scheduler.schedule_task(conn, task_kind="unit_unrelated", dedupe_key="unit:failed", run_at=old)
            conn.execute("UPDATE scheduled_tasks SET status='failed',attempt_count=3,last_error='diagnostic',finished_at=?,updated_at=? WHERE id IN (?,?)",
                         (old, old, task_id, other))
            progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
            recovered = self.row(conn, task_id)
            self.assertEqual(("pending", 0, "diagnostic"), (recovered["status"], recovered["attempt_count"], recovered["last_error"]))
            self.assertEqual("failed", self.row(conn, other)["status"])
            conn.execute("UPDATE scheduled_tasks SET status='failed',finished_at=?,updated_at=?,recurrence_seconds=59 WHERE id=?",
                         (timestamp.isoformat(), timestamp.isoformat(), task_id))
            before = self.row(conn, task_id)
            progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
            after = self.row(conn, task_id)
            for field in before.keys()-{"recurrence_seconds"}:
                self.assertEqual(before[field], after[field], field)

    def test_runtime_missing_timer_is_durable_idempotent_and_not_retimed(self):
        with get_db_connection() as conn:
            first = progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
            before = self.row(conn, first)
            second = progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
            self.assertEqual(first, second)
            self.assertEqual(before, self.row(conn, second))

    def test_real_maintenance_uses_non_advancing_mode(self):
        future = (datetime.now()+timedelta(hours=2)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            conn.execute("UPDATE scheduled_tasks SET run_at=? WHERE id=?", (future, task_id))
        for _ in range(3):
            handlers.maintain_cultivation_projection_task()
        with get_db_connection() as conn:
            self.assertEqual(future, self.row(conn, task_id)["run_at"])

    def test_runtime_does_not_commit_its_callers_transaction(self):
        old = (datetime.now()-timedelta(minutes=5)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            conn.execute("UPDATE scheduled_tasks SET status='failed',finished_at=?,updated_at=? WHERE id=?", (old, old, task_id))
            conn.commit()
            before = self.row(conn, task_id)
            conn.execute("UPDATE scheduled_tasks SET title='uncommitted business update' WHERE id=?", (task_id,))
            progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
            conn.rollback()
            self.assertEqual(before, self.row(conn, task_id))

    def test_runtime_cas_preserves_new_claim_arriving_after_dedupe_lookup(self):
        old = (datetime.now()-timedelta(minutes=5)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            conn.execute("UPDATE scheduled_tasks SET status='failed',finished_at=?,updated_at=? WHERE id=?", (old, old, task_id))
            class CompetingClaim:
                triggered = False
                def execute(self, statement, params=()):
                    if not self.triggered and " ".join(statement.split()).startswith("UPDATE scheduled_tasks SET status='pending'"):
                        self.triggered = True
                        conn.execute("UPDATE scheduled_tasks SET status='running',locked_at='new-claim',locked_by='new-owner' WHERE id=?", (task_id,))
                    return conn.execute(statement, params)
            racing = CompetingClaim()
            progress.ensure_cultivation_snapshot_refresh_task(racing, advance_pending=False)
            self.assertTrue(racing.triggered)
            row = self.row(conn, task_id)
            self.assertEqual(("running", "new-claim", "new-owner"), (row["status"], row["locked_at"], row["locked_by"]))
