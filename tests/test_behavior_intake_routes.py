from contextlib import contextmanager
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.db.errors import DatabaseBusyError
from classroom_app.dependencies import get_current_user
from classroom_app.routers import behavior
from classroom_app.services.behavior_inbox_service import new_batch_id, BehaviorBatchConflict
from classroom_app.services.behavior_event_validation import BehaviorValidationError


class BehaviorIntakeRouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(behavior.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 10, "role": "student", "name": "Synthetic"}
        self.client = TestClient(app)
        self.body = {"batch_id": new_batch_id(), "user_key": "student:10", "events": [{"action_type": "click"}]}

    @contextmanager
    def connection(self):
        yield object()

    def request(self, result=None, error=None):
        with patch.object(behavior, "get_db_connection", self.connection), patch.object(
            behavior, "_ensure_behavior_access"
        ), patch.object(behavior, "record_behavior_batch", return_value=result, side_effect=error) as record:
            response = self.client.post("/api/classrooms/1/behavior/batch", json=self.body)
        return response, record

    def test_202_requires_durable_receipt_and_reports_queued_separately(self):
        response, record = self.request({"write_mode": "durable_queued", "accepted_event_count": 0,
                                        "durable_event_count": 1, "queued_event_count": 1})
        self.assertEqual(202, response.status_code)
        self.assertEqual(0, response.json()["accepted_event_count"])
        self.assertTrue(record.call_args.kwargs["require_durable"])
        self.assertEqual(self.body["batch_id"], record.call_args.kwargs["batch_id"])

    def test_account_switch_rejects_before_intake(self):
        self.body["user_key"] = "student:11"
        response, record = self.request({})
        self.assertEqual(409, response.status_code)
        self.assertEqual("identity_changed", response.json()["detail"]["error_code"])
        self.assertEqual("student:10", response.json()["detail"]["current_user_key"])
        record.assert_not_called()

    def test_capacity_failure_propagates_to_shared_503_handler(self):
        with self.assertRaises(DatabaseBusyError):
            self.request(error=DatabaseBusyError("synthetic capacity"))

    def test_applied_duplicate_returns_200_and_old_opaque_id_cannot_enter(self):
        response, _ = self.request({"write_mode": "synced", "accepted_event_count": 1})
        self.assertEqual(200, response.status_code)
        self.body["batch_id"] = "opaque-unbounded-lifetime-id"
        response = self.client.post("/api/classrooms/1/behavior/batch", json=self.body)
        self.assertEqual(422, response.status_code)
        self.assertEqual("batch_payload_invalid", response.json()["detail"]["error_code"])
        self.assertEqual("quarantine", response.json()["detail"]["batch_disposition"])

    def test_malformed_new_data_is_422_and_existing_quarantine_is_an_explicit_409(self):
        response, _record = self.request(error=BehaviorValidationError("synthetic invalid heartbeat"))
        self.assertEqual(response.status_code, 422)
        for state in ("quarantined", "discarded"):
            with self.subTest(state=state):
                response, _record = self.request({"write_mode": state, "accepted_event_count": 0,
                    "durable_event_count": 1, "queued_event_count": 0, "degraded": True})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["status"], "error")
                self.assertEqual(response.json()["accepted_event_count"], 0)
                self.assertEqual("quarantine", response.json()["collection_error"]["batch_disposition"])

    def test_known_conflict_codes_are_terminal_without_rekeying_the_batch(self):
        for code in ("batch_payload_conflict", "batch_expired", "batch_timestamp_invalid"):
            response, record = self.request(error=BehaviorBatchConflict("synthetic conflict", code))
            self.assertEqual(409, response.status_code)
            self.assertEqual(code, response.json()["detail"]["error_code"])
            self.assertEqual("quarantine", response.json()["detail"]["batch_disposition"])
            self.assertEqual(self.body["batch_id"], record.call_args.kwargs["batch_id"])
