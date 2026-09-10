"""Manual declarations retain their own evidence and never resume an Agent."""
import json
import uuid
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.routers import agent_tasks
from classroom_app.services import agent_platform_request_service as requests
from classroom_app.services import agent_platform_request_reconciliation as reconciliation
from classroom_app.services.agent_platform_request_registry import CAPABILITIES, arguments
from tests.test_agent_platform_writes import PlatformWriteFixture


class PlatformRequestReconciliationTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        ensure_agent_platform_requests_schema(self.conn)
        self.token_value = self.token(scopes=["platform:read", "platform:write"])
        for target in (requests, agent_tasks):
            guard = patch.object(target, "get_db_connection", self.connection)
            guard.start()
            self.addCleanup(guard.stop)
        operation = next(item for item in CAPABILITIES if item.key == "http.blog.bookmark.toggle")
        self.arguments = arguments(operation, path_params={"post_id": 42})
        self.operation = operation
        self.claim, self.grant, _ = requests._admit(self.token_value, operation, str(uuid.uuid4()), *self.arguments)
        requests._mark_executing(self.claim, self.token_value, "platform:write")
        requests._uncertain(self.claim, "synthetic interrupted response")
        requests._finish_host_execution(self.claim)
        self.conn.execute("UPDATE agent_tasks SET status='failed' WHERE id=10")
        self.conn.commit()
        self.source = {"user": self.teacher, "source_session_id": "teacher-session", "task_id": 10}
        self.user = self.teacher
        app = FastAPI()
        app.include_router(agent_tasks.router)
        app.dependency_overrides[agent_tasks.get_current_user] = lambda: self.user
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.url = f"/api/agent-tasks/10/platform-requests/{self.claim.request_id}"

    def view(self):
        return reconciliation.get_user_platform_request(self.conn, **self.source, request_id=self.claim.request_id)

    def payload(self, resolution="not_occurred"):
        return {"resolution": resolution, "note": "已在原业务页面核对记录。", "expected_revision": self.view()["revision"]}

    def test_real_http_owner_can_declare_once_without_changing_http_facts_or_resuming_task(self):
        listing = self.client.get("/api/agent-tasks/10/platform-requests")
        self.assertEqual(200, listing.status_code, listing.text)
        self.assertEqual(self.claim.request_id, listing.json()["requests"][0]["id"])
        self.assertFalse(listing.json()["requests"][0]["details_loaded"])
        self.assertTrue(self.client.get(self.url).json()["request"]["details_loaded"])
        self.assertNotIn("source_session_hash", listing.text)
        self.assertNotIn("settlement_hash", listing.text)
        self.assertNotIn("teacher-session", listing.text)
        before = self.conn.execute("SELECT result_json,settled_at FROM agent_platform_requests").fetchone()
        payload = self.payload()
        first = self.client.post(self.url + "/reconcile", json=payload)
        self.assertEqual(200, first.status_code, first.text)
        result = first.json()["request"]
        self.assertEqual("not_occurred", result["reconciliation"]["resolution"])
        self.assertFalse(result["reconciliation"]["verified_business"])
        self.assertEqual("uncertain", result["observation"]["status"])
        again = self.client.post(self.url + "/reconcile", json=payload)
        self.assertEqual(200, again.status_code, again.text)
        self.assertTrue(again.json()["replayed"])
        after = self.conn.execute("SELECT result_json,settled_at FROM agent_platform_requests").fetchone()
        self.assertEqual(tuple(before), tuple(after))
        self.assertEqual("failed", self.conn.execute("SELECT status FROM agent_tasks WHERE id=10").fetchone()[0])
        self.assertEqual(1, self.count("agent_task_attempts"))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_events WHERE event_type='platform_request_reconciled'").fetchone()[0])
        self.assertEqual(409, self.client.post(self.url + "/reconcile", json={**payload, "resolution": "occurred"}).status_code)

    def test_same_numbered_student_different_owner_and_revoked_session_are_rejected(self):
        self.user = self.student
        self.assertEqual(403, self.client.get(self.url).status_code)
        self.assertEqual(403, self.client.post(self.url + "/reconcile", json=self.payload()).status_code)
        self.user = self.teacher
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        self.conn.commit()
        self.assertEqual(401, self.client.get(self.url).status_code)
        self.assertEqual(401, self.client.post(self.url + "/reconcile", json={"resolution": "not_occurred", "note": "Checked", "expected_revision": "a" * 64}).status_code)
        self.assertEqual("pending", self.conn.execute("SELECT reconciliation_status FROM agent_platform_requests").fetchone()[0])

    def test_unproven_host_stop_and_running_task_cannot_release_the_intent(self):
        for mutation in ("UPDATE agent_platform_requests SET host_execution_finished_at=NULL", "UPDATE agent_tasks SET status='running' WHERE id=10"):
            with self.subTest(mutation=mutation):
                self.conn.execute(mutation)
                snapshot = self.view()
                self.assertFalse(snapshot["can_reconcile_not_occurred"])
                with self.assertRaises(HTTPException) as error:
                    reconciliation.reconcile_user_platform_request(self.conn, **self.source, request_id=self.claim.request_id, **self.payload())
                self.assertEqual(409, error.exception.status_code)
                self.conn.rollback()
        self.assertEqual("pending", self.conn.execute("SELECT reconciliation_status FROM agent_platform_requests").fetchone()[0])

    def test_late_host_settlement_and_manual_declaration_remain_independent(self):
        result = reconciliation.reconcile_user_platform_request(self.conn, **self.source, request_id=self.claim.request_id, **self.payload())
        self.conn.commit()
        requests._settle(self.claim, "observed_http_result", {"http_status": 200, "data": {"bookmarked": True}})
        current = self.view()
        self.assertEqual(result["request"]["reconciliation"]["resolution"], current["reconciliation"]["resolution"])
        self.assertEqual("not_occurred", current["reconciliation"]["resolution"])
        self.assertTrue(current["reconciliation"]["late_http_observation"])
        self.assertEqual("observed_http_result", current["observation"]["status"])
        self.assertTrue(current["observation"]["result"]["data"]["bookmarked"])
        self.assertFalse(current["observation"]["verified_business"])

    def test_continuation_exposes_manual_declaration_separately_from_late_http_fact(self):
        from types import SimpleNamespace
        from classroom_app.services.agent_continuation_service import read_task_continuation

        reconciliation.reconcile_user_platform_request(self.conn, **self.source, request_id=self.claim.request_id, **self.payload())
        self.conn.commit()
        requests._settle(self.claim, "observed_http_result", {"http_status": 200, "data": {"bookmarked": True}})
        grant = SimpleNamespace(actor=self.grant.actor, task=dict(self.conn.execute("SELECT * FROM agent_tasks WHERE id=10").fetchone()))
        with patch("classroom_app.services.agent_task_service.collect_task_workspace_artifacts", return_value=[]):
            result = read_task_continuation(self.conn, grant)
        request = result["platform_requests"][0]
        self.assertEqual("observed_http_result", request["status"])
        self.assertTrue(request["result"]["data"]["bookmarked"])
        self.assertEqual("not_occurred", request["reconciliation"]["resolution"])
        self.assertFalse(request["reconciliation"]["verified_business"])
        self.assertFalse(request["automatic_retry_allowed"])
        self.assertIsNotNone(request["host_execution_finished_at"])
        self.assertNotIn("session_hash", json.dumps(result))

    def test_late_observation_before_confirmation_invalidates_stale_revision(self):
        payload = self.payload()
        requests._settle(self.claim, "observed_http_result", {"http_status": 200})
        with self.assertRaises(HTTPException) as error:
            reconciliation.reconcile_user_platform_request(self.conn, **self.source, request_id=self.claim.request_id, **payload)
        self.assertEqual(409, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual("pending", self.conn.execute("SELECT reconciliation_status FROM agent_platform_requests").fetchone()[0])

    def test_audit_failure_rolls_back_declaration_and_intent_release(self):
        with patch("classroom_app.services.agent_task_service.append_task_event", side_effect=RuntimeError("synthetic audit failure")):
            response = self.client.post(self.url + "/reconcile", json=self.payload())
        self.assertEqual(500, response.status_code)
        self.assertEqual("pending", self.conn.execute("SELECT reconciliation_status FROM agent_platform_requests").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT reconciled_session_hash FROM agent_platform_requests").fetchone()[0])

    def test_untrusted_fields_bad_resolution_and_oversized_body_are_rejected(self):
        payload = self.payload()
        for data in ({**payload, "source_session_id": "forged"}, {**payload, "resolution": []}, {**payload, "note": "\ud800"}):
            with self.subTest(data_type=type(data.get("resolution")).__name__):
                # JSON-escaped invalid Unicode exercises the HTTP parser too.
                response = self.client.post(self.url + "/reconcile", content=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
                self.assertEqual(400, response.status_code, response.text)
        oversized = self.client.post(self.url + "/reconcile", content=b"x" * 17000)
        self.assertEqual(413, oversized.status_code)
        self.assertEqual("pending", self.conn.execute("SELECT reconciliation_status FROM agent_platform_requests").fetchone()[0])

    def test_older_unknown_request_remains_reachable_beyond_first_page(self):
        original = dict(self.conn.execute("SELECT * FROM agent_platform_requests").fetchone())
        columns = list(original)
        for index in range(3):
            newer = {**original, "id": str(uuid.uuid4()), "operation_id": str(uuid.uuid4()), "created_at": original["created_at"] + index + 1,
                     "intent_hash": "different-intent-" + str(index), "mutates": 0,
                     "result_json": json.dumps({"http_status": 200, "data": {"large": "x" * 10000}})}
            self.conn.execute("INSERT INTO agent_platform_requests (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                              [newer[name] for name in columns])
        self.conn.commit()
        first = self.client.get("/api/agent-tasks/10/platform-requests?limit=2").json()
        self.assertTrue(first["has_more"])
        self.assertEqual(2, first["next_offset"])
        self.assertNotIn("x" * 100, json.dumps(first))
        second = self.client.get("/api/agent-tasks/10/platform-requests?limit=2&offset=2").json()
        self.assertFalse(second["has_more"])
        self.assertIn(self.claim.request_id, {item["id"] for item in second["requests"]})
