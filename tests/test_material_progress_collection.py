"""Material/classroom evidence must commit together and survive replay without recalculation."""
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.db.connection import get_db_connection
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.dependencies import get_current_user
from classroom_app.routers import learning
from classroom_app.services import material_progress_collection_service as collection, learning_progress_service as progress
from tests import test_behavior_heartbeat_durability as support


class MaterialProgressCollectionTests(unittest.TestCase):
    offering, student, material = 410, 501, 9901
    setUpBase = support.BehaviorHeartbeatDurabilityTests.setUp
    at = support.BehaviorHeartbeatDurabilityTests.at
    ms = support.BehaviorHeartbeatDurabilityTests.ms
    local = support.BehaviorHeartbeatDurabilityTests.local
    heartbeat = support.BehaviorHeartbeatDurabilityTests.heartbeat
    base_body = support.BehaviorHeartbeatDurabilityTests.body
    drain = support.BehaviorHeartbeatDurabilityTests.drain
    state = support.BehaviorHeartbeatDurabilityTests.state
    durations = support.BehaviorHeartbeatDurabilityTests.durations

    def setUp(self):
        self.setUpBase()
        with get_db_connection() as conn:
            conn.execute("""INSERT INTO course_materials(id,teacher_id,material_path,name,node_type,preview_type,
                mime_type,file_ext,scope_level,owner_role,owner_user_pk)
                VALUES (9901,101,'synthetic-reading.md','Synthetic reading','file','markdown','text/markdown','.md','public','teacher',101)""")
        self.enterContext(patch.object(collection, "datetime", support.tracking.datetime))
        self.enterContext(patch.object(learning, "_ensure_material_in_classroom"))
        app = FastAPI()
        app.include_router(learning.router)
        app.dependency_overrides[get_current_user] = lambda: self.actor.copy()
        self.material_client = self.enterContext(TestClient(app))

    def body(self, received, *events):
        return {**self.base_body(received, *events), "material_id": self.material, "progress_version": 2,
                "scroll_ratio": 0, "completed": False, "page_key": "material_viewer"}

    def collect(self, received, body):
        self.received = self.at(received)
        with get_db_connection() as conn:
            result = collection.collect_material_progress(conn, class_offering_id=self.offering, user=self.actor,
                payload=body, received_at=self.received.replace(tzinfo=None))
            conn.commit()
        return result

    def material_state(self):
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM learning_material_progress WHERE material_id=?", (self.material,)).fetchone()
            return dict(row) if row else None

    def test_material_and_durable_presence_commit_once_even_after_unknown_ack(self):
        body = self.body(65, self.heartbeat(60, start=0, end=60))
        with patch.object(progress, "_build_learning_metrics", side_effect=AssertionError("heartbeat must not rebuild projection")):
            result = self.collect(65, body)
            self.assertEqual("durable_queued", result["write_mode"])
            before = self.material_state()
            self.assertEqual((60, 60, 1), tuple(before[key] for key in ("accumulated_seconds", "active_seconds", "view_count")))
            self.assertIsNone(self.state())
            self.drain(70)
            self.assertEqual((60, 60), self.durations())
            replay = self.collect(2000, body)
        self.assertEqual("synced", replay["write_mode"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(before, self.material_state())

    def test_tail_beacon_and_tabs_with_new_ids_credit_interval_union(self):
        for received, start, end in ((65, 0, 60), (95, 30, 90), (96, 30, 90)):
            self.collect(received, self.body(received, self.heartbeat(end, start=start, end=end)))
        material = self.material_state()
        self.assertEqual((90, 90), (material["accumulated_seconds"], material["active_seconds"]))
        for n in range(3): self.drain(100 + n)
        self.assertEqual((90, 90), self.durations())

    def test_visible_and_hidden_tabs_share_one_elapsed_interval(self):
        self.collect(65, self.body(65, self.heartbeat(60, start=0, end=60)))
        self.collect(66, self.body(66, self.heartbeat(60, start=0, end=60, visible=False)))
        row = self.material_state()
        self.assertEqual((60, 60), (row["accumulated_seconds"], row["active_seconds"]))

    def test_cumulative_active_completion_and_response_remain_monotonic(self):
        for end in (60, 120, 180):
            result = self.collect(end + 5, self.body(end + 5, self.heartbeat(end, start=end - 60, end=end)))
        self.assertTrue(result["completed"])
        self.assertTrue(result["mastered"])
        self.assertEqual((180, 180, 1), tuple(self.material_state()[key] for key in ("accumulated_seconds", "active_seconds", "completed")))
        result = self.collect(190, self.body(190, self.heartbeat(185)))
        self.assertTrue(result["completed"])

    def test_scroll_completion_is_not_undone_in_later_status_receipt(self):
        first = {**self.body(1, self.heartbeat(1)), "scroll_ratio": .95, "completed": True}
        self.assertTrue(self.collect(1, first)["completed"])
        self.assertTrue(self.collect(2, self.body(2, self.heartbeat(2)))["completed"])

    def test_changed_material_fields_under_same_batch_are_rejected_atomically(self):
        body = self.body(65, self.heartbeat(60, start=0, end=60))
        self.collect(65, body)
        before = self.material_state()
        with self.assertRaises(collection.MaterialCollectionError) as caught:
            self.collect(66, {**body, "scroll_ratio": 1, "completed": True})
        self.assertEqual("batch_payload_conflict", caught.exception.code)
        self.assertEqual(before, self.material_state())

    def test_failed_durable_intake_rolls_back_material_facts_and_projection_dirty(self):
        body = self.body(65, self.heartbeat(60, start=0, end=60))
        with patch.object(collection, "record_behavior_batch", side_effect=DatabaseBusyError("synthetic unavailable")):
            with self.assertRaises(DatabaseBusyError): self.collect(65, body)
        self.assertIsNone(self.material_state())
        with get_db_connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM learning_progress_snapshots").fetchone()[0])
        self.collect(66, body)
        self.assertEqual(60, self.material_state()["active_seconds"])

    def test_legacy_writer_preserves_v2_replay_coverage(self):
        body = self.body(65, self.heartbeat(60, start=0, end=60))
        self.collect(65, body)
        before = json.loads(self.material_state()["metadata_json"])["_collection_coverage_v2"]
        with get_db_connection() as conn:
            progress.record_material_learning_progress(conn, class_offering_id=self.offering, student_id=self.student,
                material_id=self.material, duration_seconds=5, active_seconds=5, metadata={"page_key": "legacy"}, defer_projection=True)
        self.assertEqual(before, json.loads(self.material_state()["metadata_json"])["_collection_coverage_v2"])
        self.collect(67, self.body(67, self.heartbeat(60, start=0, end=60)))
        self.assertEqual(65, self.material_state()["active_seconds"])

    def test_fractional_hidden_and_authentication_bounds_preserve_real_measurement(self):
        self.collect(1, self.body(1, self.heartbeat(.5, start=0, end=.5)))
        self.collect(2, self.body(2, self.heartbeat(1, start=.5, end=1)))
        self.assertEqual((1, 1), tuple(self.material_state()[key] for key in ("accumulated_seconds", "active_seconds")))
        self.collect(62, self.body(62, self.heartbeat(61, start=1, end=61, visible=False)))
        self.assertEqual((61, 1), tuple(self.material_state()[key] for key in ("accumulated_seconds", "active_seconds")))
        self.actor["login_time"] = self.local(100)
        self.collect(125, self.body(125, self.heartbeat(120, start=60, end=120)))
        self.assertEqual((81, 21), tuple(self.material_state()[key] for key in ("accumulated_seconds", "active_seconds")))

    def test_http_receipt_and_identity_are_compatible_with_shared_collector(self):
        self.received = self.at(65)
        body = self.body(65, self.heartbeat(60, start=0, end=60))
        response = self.material_client.post(f"/api/classrooms/{self.offering}/learning/material-progress", json=body)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(("success", "durable_queued", body["batch_id"], "student:501"),
            tuple(response.json()[key] for key in ("status", "write_mode", "batch_id", "current_user_key")))
        bad = {**self.body(66, self.heartbeat(60, start=0, end=60)), "user_key": "student:502"}
        response = self.material_client.post(f"/api/classrooms/{self.offering}/learning/material-progress", json=bad)
        self.assertEqual(409, response.status_code)
        self.assertEqual("identity_changed", response.json()["detail"]["error_code"])
