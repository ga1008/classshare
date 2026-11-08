"""Receipt-local state aggregation preserves the durable public contract."""
from __future__ import annotations

import unittest

from classroom_app.db.connection import get_db_connection
from classroom_app.services import behavior_tracking_service as tracking
from tests import test_behavior_heartbeat_durability as durability


class BehaviorStateApplyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = durability.BehaviorHeartbeatDurabilityTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def apply(self, seconds, events, *, session=None):
        fixture = self.fixture
        fixture.received = fixture.at(seconds)
        with get_db_connection() as conn:
            return tracking._record_behavior_batch_in_connection(
                conn, class_offering_id=fixture.offering, user_pk=fixture.student,
                user_role="student", display_name="Synthetic pupil", page_key="classroom",
                events=events, session_started_at=session or fixture.local(-60),
                recorded_at=fixture.local(seconds),
            )

    def test_multi_event_order_deduplicates_coverage_and_preserves_public_fields(self):
        fixture = self.fixture
        heartbeat = fixture.heartbeat(60, start=0, end=60)
        heartbeat["page_key"] = "classroom_material"
        action = {"action_type": "page_action", "summary_text": "Synthetic action", "page_key": "discussion"}
        result = self.apply(65, [action, heartbeat, heartbeat, action])
        state = fixture.state()
        self.assertEqual((60, 60), fixture.durations())
        self.assertEqual(2, state["total_activity_count"])
        self.assertEqual("discussion", state["last_page_key"])
        self.assertEqual(fixture.local(65), state["last_event_at"])
        self.assertEqual(fixture.local(65), state["last_presence_at"])
        self.assertEqual(2, len(result["logged_event_ids"]))
        self.assertEqual(2, result["collection"]["valid_sample_count"])
        public_fields = set("""class_offering_id user_pk user_role total_activity_count
            last_profiled_activity_count profile_generation_pending last_event_at last_profiled_at
            next_profile_interval_seconds next_profile_due_at online_accumulated_seconds
            current_session_started_at last_presence_at last_page_key last_visibility_state last_focus_state
            last_idle_seconds focus_total_seconds blur_total_seconds visible_total_seconds hidden_total_seconds
            discussion_lurk_total_seconds ai_panel_open_total_seconds learning_online_seconds learning_focus_seconds
            created_at updated_at""".split())
        self.assertEqual(public_fields | {
            "seconds_until_next_profile", "logged_event_ids", "accepted_event_count", "collection",
        }, set(result))
        self.assertNotIn("presence_coverage_json", result)
        with get_db_connection() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM student_point_ledger").fetchone()["n"])
        self.apply(66, [heartbeat, action])
        self.assertEqual((60, 60), fixture.durations())
        with get_db_connection() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM student_point_ledger").fetchone()["n"])

    def test_session_reset_keeps_null_fallback_lifetime_and_profile_columns(self):
        fixture = self.fixture
        self.apply(65, [fixture.heartbeat(60, start=0, end=60)])
        profiled = fixture.local(30)
        with get_db_connection() as conn:
            conn.execute("""UPDATE classroom_behavior_states
                SET learning_online_seconds=NULL, learning_focus_seconds=0,
                    last_profiled_at=?, last_profiled_activity_count=7,
                    profile_generation_pending=1 WHERE class_offering_id=?""",
                         (profiled, fixture.offering))
        result = self.apply(130, [fixture.heartbeat(125, start=100, end=125)], session=fixture.local(100))
        self.assertEqual((85, 25), fixture.durations())
        self.assertEqual((25, 25), (result["online_accumulated_seconds"], result["focus_total_seconds"]))
        self.assertEqual(profiled, result["last_profiled_at"])
        self.assertEqual(7, result["last_profiled_activity_count"])
        self.assertEqual(0, result["profile_generation_pending"])

    def test_empty_legacy_invalid_and_idle_updates_keep_existing_semantics(self):
        fixture = self.fixture
        self.apply(10, [])
        for seconds, event in (
            (20, {"action_type": "heartbeat", "payload": {}}),
            (30, fixture.heartbeat(30, start=20, end=30, idle=9999)),
            (40, fixture.heartbeat(400000, start=399990, end=400000)),
        ):
            result = self.apply(seconds, [event])
            self.assertEqual(fixture.local(seconds), result["updated_at"])
            self.assertEqual((0, 0), fixture.durations())
            self.assertEqual([], result["logged_event_ids"])
        with get_db_connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM student_point_ledger").fetchone()["n"])

    def test_warm_batch_reads_and_writes_state_once_even_with_multiple_events(self):
        fixture = self.fixture
        self.apply(10, [])
        statements = []
        with get_db_connection() as conn:
            class Traced:
                def execute(self, sql, parameters=()):
                    if "classroom_behavior_states" in sql:
                        statements.append(" ".join(sql.split()))
                    return conn.execute(sql, parameters)

            fixture.received = fixture.at(65)
            tracking._record_behavior_batch_in_connection(
                Traced(), class_offering_id=fixture.offering, user_pk=fixture.student,
                user_role="student", display_name="Synthetic pupil", page_key="classroom",
                events=[fixture.heartbeat(60, start=0, end=60),
                        {"action_type": "page_action", "summary_text": "Synthetic action"},
                        fixture.heartbeat(60, start=0, end=60)],
                session_started_at=fixture.local(-60), recorded_at=fixture.local(65),
            )
        self.assertEqual(2, len(statements), statements)
        self.assertTrue(statements[0].startswith("SELECT "))
        self.assertTrue(statements[1].startswith("UPDATE "))
        self.assertIn("RETURNING", statements[1])


if __name__ == "__main__":
    unittest.main()
