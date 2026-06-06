import unittest

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.dependencies import get_current_user
from classroom_app.schemas.api_common import ApiErrorCode, ApiErrorResponse, build_error_payload
from classroom_app.schemas.homework_contracts import (
    AssignmentDraftResponse,
    AssignmentSubmissionsResponse,
    AssignmentTimeStateResponse,
)
from classroom_app.schemas.materials_contracts import (
    MaterialAiImportActiveResponse,
    MaterialLibraryResponse,
)
from classroom_app.schemas.message_center_contracts import (
    MessageCenterItemsResponse,
    MessageCenterSummaryResponse,
)


class ApiContractSchemaTests(unittest.TestCase):
    def test_error_payload_preserves_legacy_fields_and_structured_code(self):
        payload = build_error_payload(
            detail={"message": "Too many requests", "retry_after_seconds": 8},
            message="Too many requests",
            code=ApiErrorCode.RATE_LIMITED,
            details={"retry_after_seconds": 8},
            request_id="req-1",
            legacy_fields={"retry_after_seconds": 8},
        )

        validated = ApiErrorResponse.model_validate(payload)

        self.assertEqual(ApiErrorCode.RATE_LIMITED, validated.error.code)
        self.assertEqual("Too many requests", validated.error.message)
        self.assertEqual({"retry_after_seconds": 8}, validated.error.details)
        self.assertEqual(8, payload["retry_after_seconds"])

    def test_global_api_404_uses_structured_error_contract(self):
        client = TestClient(app)
        try:
            response = client.get("/api/does-not-exist-for-contract-test")
        finally:
            client.close()

        self.assertEqual(404, response.status_code)
        body = response.json()
        self.assertEqual("not_found", body["code"])
        self.assertEqual("not_found", body["error"]["code"])
        self.assertIn("detail", body)

    def test_global_api_validation_error_uses_enumerable_code(self):
        previous_override = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: {
            "id": 1,
            "role": "teacher",
            "name": "Contract Teacher",
        }
        client = TestClient(app)
        try:
            response = client.get("/api/message-center/private/conversation")
        finally:
            client.close()
            if previous_override is None:
                app.dependency_overrides.pop(get_current_user, None)
            else:
                app.dependency_overrides[get_current_user] = previous_override

        self.assertEqual(422, response.status_code)
        body = response.json()
        self.assertEqual("validation_error", body["code"])
        self.assertEqual("validation_error", body["error"]["code"])
        self.assertIsInstance(body["detail"], list)
        self.assertIsInstance(body["error"]["details"]["errors"], list)

    def test_internal_health_and_metrics_include_database_backend_state(self):
        client = TestClient(app)
        try:
            health_response = client.get("/api/internal/health")
            metrics_response = client.get("/api/internal/metrics")
        finally:
            client.close()

        self.assertEqual(200, health_response.status_code)
        self.assertEqual(200, metrics_response.status_code)
        health_backend = health_response.json()["database_backend"]
        metrics_backend = metrics_response.json()["database_backend"]
        self.assertEqual("sqlite", health_backend["engine"])
        self.assertTrue(health_backend["configured"])
        self.assertEqual(health_backend["engine"], metrics_backend["engine"])

    def test_message_center_contract_fixtures(self):
        summary = MessageCenterSummaryResponse.model_validate(
            {
                "status": "success",
                "summary": {"unread_total": 2, "private_unread_count": 1},
                "latest_unread": None,
            }
        )
        items = MessageCenterItemsResponse.model_validate(
            {
                "status": "success",
                "items": [{"id": 1, "category": "assignment", "title": "Homework"}],
            }
        )

        self.assertEqual(2, summary.summary["unread_total"])
        self.assertEqual("assignment", items.items[0]["category"])

    def test_homework_contract_fixtures(self):
        time_state = AssignmentTimeStateResponse.model_validate(
            {
                "status": "success",
                "server_now": "2026-06-04T08:00:00+08:00",
                "assignments": [
                    {
                        "id": 10,
                        "assignment_id": 10,
                        "status": "published",
                        "effective_status": "published",
                    }
                ],
            }
        )
        draft = AssignmentDraftResponse.model_validate(
            {
                "exists": False,
                "answers_json": "",
                "current_page": 0,
                "client_updated_at": "",
                "server_updated_at": "",
                "server_version": 0,
                "files": [],
                "files_by_question": {},
            }
        )
        submissions = AssignmentSubmissionsResponse.model_validate(
            {
                "status": "success",
                "stats": {"total_students": 1},
                "submissions": [{"student_pk_id": 1, "status": "unsubmitted"}],
                "assignment": {"id": 10, "title": "Homework"},
            }
        )

        self.assertEqual(10, time_state.assignments[0].id)
        self.assertEqual(10, time_state.assignments[0].assignment_id)
        self.assertFalse(draft.exists)
        self.assertEqual(1, submissions.stats["total_students"])

    def test_material_contract_fixtures(self):
        library = MaterialLibraryResponse.model_validate(
            {
                "status": "success",
                "current_folder": None,
                "breadcrumbs": [],
                "items": [{"id": 1, "name": "README.md", "node_type": "file"}],
                "stats": {},
                "filters": {},
                "facets": {},
                "overview": {},
            }
        )
        active = MaterialAiImportActiveResponse.model_validate(
            {
                "status": "success",
                "tasks": [{"id": 7, "parse_status": "queued"}],
                "poll_interval_ms": 3500,
            }
        )

        self.assertEqual("README.md", library.items[0]["name"])
        self.assertEqual(3500, active.poll_interval_ms)


if __name__ == "__main__":
    unittest.main()
