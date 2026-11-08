from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app import config, database
from classroom_app.db.connection import get_db_connection
from classroom_app.db.schema import init_database
from classroom_app.dependencies import get_current_user
from classroom_app.routers import behavior as behavior_route
from classroom_app.services import behavior_inbox_service as inbox
from classroom_app.services import behavior_tracking_service as tracking
from classroom_app.services import learning_progress_service, student_points_service, student_streak_service


class BehaviorHeartbeatDurabilityTests(unittest.TestCase):
    offering = 410
    student = 501

    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory(prefix="behavior-heartbeat-durable-"))
        db_path = Path(folder) / "classroom.db"
        self.enterContext(patch.object(config, "DB_PATH", db_path))
        self.enterContext(patch.object(database, "DB_PATH", db_path))
        init_database()
        with get_db_connection() as conn:
            conn.executescript("""
                INSERT INTO teachers (id, name, email, hashed_password)
                    VALUES (101, 'Synthetic teacher', 'heartbeat@example.invalid', 'x');
                INSERT INTO classes (id, name, created_by_teacher_id) VALUES (201, 'Synthetic classroom', 101);
                INSERT INTO courses (id, name, created_by_teacher_id)
                    VALUES (301, 'Synthetic course', 101), (302, 'Other synthetic course', 101);
                INSERT INTO class_offerings (id, class_id, course_id, teacher_id)
                    VALUES (410, 201, 301, 101), (411, 201, 302, 101);
                INSERT INTO students (id, student_id_number, name, class_id)
                    VALUES (501, 'HB501', 'Synthetic pupil', 201), (502, 'HB502', 'Other synthetic pupil', 201);
                """)
        self.origin = datetime(2026, 11, 5, 9, 0, 0).astimezone()
        self.received = self.origin
        self.actor = {"id": self.student, "role": "student", "name": "Synthetic pupil", "login_time": self.local(-60)}
        self.batch_counter = 0
        test = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test.received.replace(tzinfo=None) if tz is None else test.received.astimezone(tz)

        self.enterContext(patch.object(inbox, "datetime", Clock))
        self.enterContext(patch.object(tracking, "datetime", Clock))
        self.pipeline = inbox.BehaviorWritePipeline(tracking._record_behavior_batch_in_connection)
        self.enterContext(patch.object(self.pipeline, "start"))
        self.enterContext(patch.object(tracking, "_get_behavior_write_pipeline", return_value=self.pipeline))
        self.enterContext(patch.object(student_points_service, "_SCHEMA_READY", False))
        self.enterContext(patch.object(student_streak_service, "_SCHEMA_READY", False))
        tracking.start_behavior_write_pipeline()
        app = FastAPI()
        app.include_router(behavior_route.router)
        app.dependency_overrides[get_current_user] = lambda: self.actor.copy()
        self.client = self.enterContext(TestClient(app))

    def at(self, seconds):
        return self.origin + timedelta(seconds=seconds)

    def local(self, seconds):
        return self.at(seconds).replace(tzinfo=None).isoformat()

    def ms(self, seconds):
        return int(self.origin.timestamp()) * 1000 + round(seconds * 1000)

    def heartbeat(self, observed, *, start=None, end=None, visible=True, focused=True, idle=0):
        payload = {"presence_version": 2, "presence_observed_ms": self.ms(observed),
                   "visibility_state": "visible" if visible else "hidden", "focused": focused,
                   "idle_seconds": idle, "ai_panel_open": False}
        if start is not None:
            payload["interval_start_ms"] = self.ms(start)
        if end is not None:
            payload["interval_end_ms"] = self.ms(end)
        return {"action_type": "presence_heartbeat", "summary_text": "", "payload": payload}

    def body(self, received, *events):
        self.batch_counter += 1
        return {"user_key": f"student:{self.actor['id']}", "page_key": "classroom_discussion",
                "batch_id": f"b1_{self.ms(received)}_synthetic{self.batch_counter}", "events": list(events)}

    def post(self, received, body, *, offering=None):
        self.received = self.at(received)
        return self.client.post(f"/api/classrooms/{offering or self.offering}/behavior/batch", json=body)

    def enqueue(self, received, *events, offering=None):
        body = self.body(received, *events)
        response = self.post(received, body, offering=offering)
        self.assertEqual(202, response.status_code, response.text)
        self.assertEqual(len(events), response.json()["durable_event_count"])
        return body

    def drain(self, seconds, *, pipeline=None):
        self.received = self.at(seconds)
        self.assertTrue((pipeline or self.pipeline)._drain_once())

    def state(self, *, offering=None, student=None):
        with get_db_connection() as conn:
            row = conn.execute("""
                SELECT * FROM classroom_behavior_states
                WHERE class_offering_id = ? AND user_role = 'student' AND user_pk = ?
                """, (offering or self.offering, student or self.student)).fetchone()
        return dict(row) if row else None

    def durations(self, **scope):
        row = self.state(**scope)
        self.assertIsNotNone(row)
        return tuple(row["learning_" + name] for name in ("online_seconds", "focus_seconds"))

    def receipt(self, body):
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_id = ?", (body["batch_id"],)).fetchone()
        return dict(row) if row else None

    def dirty_version(self):
        with get_db_connection() as conn:
            row = conn.execute("""
                SELECT dirty_version FROM learning_progress_snapshots
                WHERE class_offering_id = ? AND student_id = ?
                """, (self.offering, self.student)).fetchone()
        return row["dirty_version"] if row else None

    def test_queue_delay_failed_attempt_restart_and_receipt_retry_credit_once(self):
        event = self.heartbeat(60, start=0, end=60)
        body = self.enqueue(65, event)
        self.assertIsNone(self.state())
        self.assertEqual(("pending", self.local(65)), tuple(self.receipt(body)[k] for k in ("state", "received_at")))

        def fail_after_real_effects(conn, **kwargs):
            tracking._record_behavior_batch_in_connection(conn, **kwargs)
            conn.execute("SELECT missing_synthetic_column")

        with patch.object(self.pipeline, "_apply_batch", fail_after_real_effects):
            self.drain(3600)
        self.assertIsNone(self.state())
        self.assertIsNone(self.dirty_version())
        self.assertEqual(("pending", 1), tuple(self.receipt(body)[k] for k in ("state", "attempts")))

        restarted = inbox.BehaviorWritePipeline(tracking._record_behavior_batch_in_connection)
        processed = 8 * 86400
        self.drain(processed, pipeline=restarted)
        self.assertEqual((60, 60), self.durations())
        version = self.dirty_version()
        self.assertGreater(version, 0)
        stored = self.receipt(body)
        self.assertEqual(("applied", self.local(65), self.local(processed)),
                         tuple(stored[k] for k in ("state", "received_at", "applied_at")))
        response = self.post(processed + 1, body)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("synced", response.json()["write_mode"])
        self.assertFalse(restarted._drain_once())
        self.assertEqual((60, 60), self.durations())
        self.assertEqual(version, self.dirty_version())
        with get_db_connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM classroom_behavior_events WHERE user_pk = ?",
                                             (self.student,)).fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM student_point_ledger WHERE student_id = ?",
                                             (self.student,)).fetchone()[0])

    def test_zero_observation_and_rejected_sample_have_distinct_durable_quality(self):
        zero = self.enqueue(65, self.heartbeat(60, start=0, end=60, focused=False))
        bad = self.enqueue(130, self.heartbeat(125, start=0, end=125))
        self.drain(140)
        self.drain(141)
        zero_quality = self.post(142, zero).json()["collection"]
        bad_quality = self.post(143, bad).json()["collection"]
        self.assertEqual((1, 0), (zero_quality["valid_sample_count"], zero_quality["rejected_sample_count"]))
        self.assertEqual((0, 1), (bad_quality["valid_sample_count"], bad_quality["rejected_sample_count"]))
        self.assertIn("presence_interval_invalid", bad_quality["reason_codes"])
        self.assertEqual((0, 0), self.durations())
        with get_db_connection() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM behavior_collection_observations").fetchone()[0])

    def test_client_failure_evidence_is_idempotent_and_cannot_create_learning_or_activity(self):
        body = {"user_key": "student:501", "diagnostics": [{"diagnostic_id": "d1_synthetic_failure",
                "error_code": "auth_required", "observed_ms": self.ms(60), "event_count": 2}]}
        for _ in range(2):
            response = self.client.post(f"/api/classrooms/{self.offering}/behavior/diagnostics", json=body)
            self.assertEqual(200, response.status_code, response.text)
        self.assertIsNone(self.state())
        with get_db_connection() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM behavior_collection_observations").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM classroom_behavior_events").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM student_point_ledger").fetchone()[0])
        body["user_key"] = "student:502"
        response = self.client.post(f"/api/classrooms/{self.offering}/behavior/diagnostics", json=body)
        self.assertEqual(409, response.status_code)

    def test_distinct_batches_in_two_tabs_credit_union_and_keep_classrooms_separate(self):
        self.enqueue(65, self.heartbeat(60, start=0, end=60))
        self.enqueue(96, self.heartbeat(90, start=30, end=90))
        self.enqueue(97, self.heartbeat(90, start=30, end=90))
        self.enqueue(98, self.heartbeat(90, start=30, end=90), offering=411)
        self.actor["id"] = 502
        self.enqueue(99, self.heartbeat(50, start=20, end=50))
        for _ in range(5):
            self.drain(200)
        self.assertEqual((90, 90), self.durations())
        self.assertEqual((60, 60), self.durations(offering=411))
        self.assertEqual((30, 30), self.durations(student=502))

    def test_delayed_disjoint_interval_counts_without_erasing_newer_coverage(self):
        self.enqueue(125, self.heartbeat(120, start=60, end=120))
        self.enqueue(130, self.heartbeat(45, start=0, end=45))
        self.enqueue(135, self.heartbeat(70, start=20, end=70))
        self.enqueue(140, self.heartbeat(40, visible=False, focused=False))
        for _ in range(4):
            self.drain(160)
        self.assertEqual((120, 120), self.durations())
        state = self.state()
        self.assertEqual(("visible", "focused"), (state["last_visibility_state"], state["last_focus_state"]))

    def test_status_only_and_old_heartbeats_do_not_assume_elapsed_time(self):
        legacy = {"action_type": "presence_heartbeat", "summary_text": "",
                  "payload": {"visibility_state": "visible", "focused": True, "idle_seconds": 0}}
        self.enqueue(2, legacy)
        self.enqueue(80, self.heartbeat(80))
        self.enqueue(150, legacy)
        for _ in range(3):
            self.drain(160)
        state = self.state()
        self.assertEqual((0, 0), (state["online_accumulated_seconds"], state["focus_total_seconds"]))
        self.assertEqual((0, 0), tuple(value or 0 for value in self.durations()))
        self.assertEqual("visible", state["last_visibility_state"])

    def test_transition_status_wins_over_a_measured_interval_with_the_same_observed_time(self):
        interval = self.heartbeat(60, start=0, end=60)
        hidden = self.heartbeat(60, visible=False, focused=False)
        self.enqueue(65, interval, hidden)
        self.drain(66)
        self.assertEqual((60, 60), self.durations())
        state = self.state()
        self.assertEqual(("hidden", "blurred"), (state["last_visibility_state"], state["last_focus_state"]))

        self.enqueue(70, interval)
        self.drain(71)
        self.assertEqual((60, 60), self.durations())
        state = self.state()
        self.assertEqual(("hidden", "blurred"), (state["last_visibility_state"], state["last_focus_state"]))

    def test_visibility_focus_and_idle_limit_count_only_eligible_seconds(self):
        self.enqueue(62, self.heartbeat(60, start=0, end=60, idle=170))
        self.enqueue(122, self.heartbeat(120, start=60, end=120, focused=False))
        self.enqueue(182, self.heartbeat(180, start=120, end=180, visible=False))
        self.enqueue(242, self.heartbeat(240, start=180, end=240, idle=250))
        self.enqueue(272, self.heartbeat(270, start=240, end=270))
        expected_totals = (40, 40, 40, 40, 70)
        for total in expected_totals:
            self.drain(300)
            self.assertEqual((total, total), self.durations())

    def test_fractional_intervals_do_not_round_up_or_lose_carry(self):
        for start, end in ((0, 0.6), (1, 1.6), (2, 2.6), (3, 3.3)):
            self.enqueue(end + 0.1, self.heartbeat(end, start=start, end=end))
        for total in (0, 1, 1, 2):
            self.drain(10)
            self.assertEqual((total, total), tuple(value or 0 for value in self.durations()))

    def test_temporal_bounds_use_signed_session_and_immutable_receive_time(self):
        self.actor["login_time"] = self.local(20)
        self.enqueue(65, self.heartbeat(60, start=0, end=60))
        self.enqueue(70, self.heartbeat(400, start=340, end=400))
        self.enqueue(75, self.heartbeat(70, start=70 - 8 * 86400, end=70 - 8 * 86400 + 60))
        self.enqueue(80, self.heartbeat(82, start=60, end=82))
        for total in (40, 40, 40, 60):
            self.drain(500)
            self.assertEqual((total, total), self.durations())

    def test_untrusted_timing_does_not_poison_a_mixed_durable_batch(self):
        malformed = self.heartbeat(75, start=10)
        out_of_range = self.heartbeat(120, start=0, end=120)
        malformed_idle = self.heartbeat(100, start=60, end=100, idle="invalid")
        real_event = {"action_type": "page_action", "summary_text": "Synthetic study action"}
        body = self.enqueue(125, malformed, out_of_range, malformed_idle, real_event)
        self.drain(180)
        self.assertEqual("applied", self.receipt(body)["state"])
        self.assertEqual((0, 0), tuple(value or 0 for value in self.durations()))
        with get_db_connection() as conn:
            events = conn.execute("""
                SELECT summary_text, created_at FROM classroom_behavior_events WHERE user_pk = ?
                """, (self.student,)).fetchall()
            points = conn.execute("""
                SELECT reason_kind, reason_ref, delta FROM student_point_ledger WHERE student_id = ?
                """, (self.student,)).fetchall()
        self.assertEqual([("Synthetic study action", self.local(125))], [tuple(row) for row in events])
        self.assertEqual([("daily_active", self.origin.date().isoformat(), 10)], [tuple(row) for row in points])

    def test_login_and_profile_window_reset_keep_preexisting_and_new_durable_time(self):
        self.enqueue(0, self.heartbeat(0))
        self.drain(0)
        self.enqueue(1, self.heartbeat(1), offering=411)
        self.drain(1)
        with get_db_connection() as conn:
            conn.execute("""
                UPDATE classroom_behavior_states
                SET online_accumulated_seconds = 100, focus_total_seconds = 40, total_activity_count = 7,
                    learning_online_seconds = NULL, learning_focus_seconds = NULL
                WHERE class_offering_id = ? AND user_pk = ?
                """, (self.offering, self.student))
            conn.execute("""
                UPDATE classroom_behavior_states
                SET online_accumulated_seconds = 37, focus_total_seconds = 25, total_activity_count = 3,
                    learning_online_seconds = NULL, learning_focus_seconds = NULL
                WHERE class_offering_id = 411 AND user_pk = ?
                """, (self.student,))

        # A legacy row can encounter either reset first after upgrade.
        tracking._finalize_profile_generation(class_offering_id=411, user_pk=self.student,
                                              user_role="student", success=True)
        self.assertEqual((37, 25), self.durations(offering=411))
        other = self.state(offering=411)
        self.assertEqual((0, 0, 3), tuple(other[k] for k in (
            "online_accumulated_seconds", "focus_total_seconds", "total_activity_count",
        )))

        self.actor["login_time"] = self.local(70)
        self.enqueue(72, self.heartbeat(72))
        self.drain(90)
        self.assertEqual((100, 40), self.durations())
        state = self.state()
        self.assertEqual((0, 0, 7), tuple(state[k] for k in (
            "online_accumulated_seconds", "focus_total_seconds", "total_activity_count",
        )))

        self.enqueue(112, self.heartbeat(110, start=80, end=110))
        self.drain(115)
        self.assertEqual((130, 70), self.durations())
        self.assertEqual(30, self.state()["focus_total_seconds"])
        with get_db_connection() as conn:
            before = learning_progress_service._load_interaction_metrics(conn, self.offering, self.student)
        self.assertEqual((130, 70), (before["online_seconds"], before["focus_seconds"]))
        tracking._finalize_profile_generation(class_offering_id=self.offering, user_pk=self.student,
                                              user_role="student", success=True)
        self.assertEqual((130, 70), self.durations())
        state = self.state()
        self.assertEqual((0, 0, 7), tuple(state[k] for k in (
            "online_accumulated_seconds", "focus_total_seconds", "total_activity_count",
        )))
        with get_db_connection() as conn:
            after = learning_progress_service._load_interaction_metrics(conn, self.offering, self.student)
        self.assertEqual(before["consistency_ratio"], after["consistency_ratio"])

        self.enqueue(130, self.heartbeat(110, start=80, end=110))
        self.drain(135)
        self.assertEqual((130, 70), self.durations())
        self.assertEqual(other, self.state(offering=411))
