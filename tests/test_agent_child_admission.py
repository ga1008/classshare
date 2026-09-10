"""Actual scoped-auth HTTP + SQL; all credentials and tasks are synthetic."""
import uuid
import unittest

from fastapi import HTTPException

from classroom_app.db.schema_agent_children import ensure_agent_children_schema
from classroom_app.services import agent_child_admission_service as children
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests import test_agent_authority as fixture_module


class AgentChildAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.AgentAuthorityTests()
        self.fixture.setUp()
        self.conn = self.fixture.conn
        ensure_agent_children_schema(self.conn)
        self.conn.commit()
        self.client = self.fixture.client
        self.headers = self.fixture.headers
        self.token = self.headers["Authorization"][7:]
        self.parent = str(uuid.uuid4())

    def tearDown(self):
        self.fixture.tearDown()

    def payload(self, **changed):
        return {"request_id": str(uuid.uuid4()), "parent_session_id": self.parent,
                "child_session_id": str(uuid.uuid4()), "depth": 1, **changed}

    def admit(self, payload=None):
        response = self.client.post("/api/agent-bridge/children/admit", headers=self.headers, json=payload or self.payload())
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_http_idempotency_is_exact_and_finish_never_refunds(self):
        payload = self.payload()
        item = self.admit(payload)
        self.assertEqual(item, self.admit(payload))
        changed = self.client.post("/api/agent-bridge/children/admit", headers=self.headers,
                                   json={**payload, "child_session_id": str(uuid.uuid4())})
        self.assertEqual(409, changed.status_code)
        for status in ("completed", "completed", "error"):
            response = self.client.post(f"/api/agent-bridge/children/{item['id']}/finish", headers=self.headers, json={"status": status})
            self.assertEqual(409 if status == "error" else 200, response.status_code, response.text)
            if status == "completed":
                self.assertFalse(response.json()["host_execution_verified"])
                self.assertFalse(response.json()["capacity_refunded"])
        for _ in range(3):
            children.admit_child(self.conn, self.token, **self.payload())
            self.conn.commit()
        denied = self.client.post("/api/agent-bridge/children/admit", headers=self.headers, json=self.payload())
        self.assertEqual(429, denied.status_code)
        self.assertEqual(4, self.conn.execute("SELECT COUNT(*) FROM agent_task_children").fetchone()[0])

    def test_task_total_survives_new_attempt_and_old_token_is_revoked(self):
        for _ in range(3):
            self.admit()
        old_token = self.token
        self.conn.execute("UPDATE agent_task_attempts SET lease_expires_at=0")
        self.conn.commit()
        attempt = create_task_attempt(self.conn, task_id=7, worker_id="next", startup_key="next")
        self.token = issue_task_delegation(self.conn, task_id=7, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                                          purpose="tools", scopes=["platform:read"], source_session_id="fixture-session")["token"]
        self.conn.commit()
        self.headers = {"Authorization": "Bearer " + self.token}
        self.parent = str(uuid.uuid4())
        self.assertEqual(4, self.admit()["ordinal"])
        response = self.client.post("/api/agent-bridge/children/admit", headers=self.headers, json=self.payload())
        self.assertEqual(429, response.status_code)
        with self.assertRaises(HTTPException):
            children.admit_child(self.conn, old_token, **self.payload())
        self.conn.rollback()

    def test_parent_and_depth_are_pinned_and_cannot_reuse_child_identity(self):
        item = self.admit()
        for payload in (self.payload(depth=2), self.payload(depth=True), self.payload(parent_session_id=item["child_session_id"]),
                        self.payload(parent_session_id=str(uuid.uuid4())), self.payload(child_session_id=item["child_session_id"]),
                        self.payload(extra="ignored-must-fail")):
            response = self.client.post("/api/agent-bridge/children/admit", headers=self.headers, json=payload)
            self.assertIn(response.status_code, (400, 409, 422), response.text)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_children").fetchone()[0])

    def test_revocation_refuses_new_work_and_does_not_claim_cleanup(self):
        item = self.admit()
        self.conn.execute("UPDATE agent_tasks SET cancel_requested_at='now' WHERE id=7")
        self.conn.commit()
        for path, body in (("/admit", self.payload()), (f"/{item['id']}/finish", {"status": "aborted"})):
            response = self.client.post("/api/agent-bridge/children" + path, headers=self.headers, json=body)
            self.assertEqual(401, response.status_code)
        row = self.conn.execute("SELECT * FROM agent_task_children").fetchone()
        self.assertIsNone(row["runtime_reported_at"])
        self.assertIsNone(row["runtime_reported_status"])

    def test_other_actor_and_task_do_not_receive_child_receipts(self):
        item = self.admit()
        self.conn.execute("INSERT INTO user_sessions VALUES ('other-session','teacher:2','2','teacher','2099-01-01T00:00:00+00:00')")
        attempt = create_task_attempt(self.conn, task_id=8, worker_id="other", startup_key="other")
        token = issue_task_delegation(self.conn, task_id=8, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                                      purpose="tools", scopes=["platform:read"], source_session_id="other-session")["token"]
        self.conn.commit()
        response = self.client.post(f"/api/agent-bridge/children/{item['id']}/finish", headers={"Authorization": "Bearer " + token}, json={"status": "completed"})
        self.assertEqual(404, response.status_code)

    def test_startup_is_idempotent_and_preserves_observation_bytes(self):
        item = self.admit()
        children.report_child_finish(self.conn, self.token, item["id"], status="error")
        self.conn.commit()
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM agent_task_children")]
        ensure_agent_children_schema(self.conn)
        ensure_agent_children_schema(self.conn)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM agent_task_children")])

    def test_malformed_finish_status_is_refused_without_mutation(self):
        item = self.admit()
        for status in ({}, []):
            response = self.client.post(f"/api/agent-bridge/children/{item['id']}/finish", headers=self.headers, json={"status": status})
            self.assertEqual(422, response.status_code)
            with self.assertRaises(HTTPException) as error:
                children.report_child_finish(self.conn, self.token, item["id"], status=status)
            self.assertEqual(400, error.exception.status_code)
            self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT runtime_reported_status FROM agent_task_children").fetchone()[0])
