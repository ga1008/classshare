"""Credential proposals use real HTTP, account services and an atomic receipt."""
import json
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.dependencies import verify_password
from classroom_app.routers import agent_tasks
from classroom_app.services import agent_identity_management_adapter as identity
from classroom_app.services import teacher_account_service as accounts
from classroom_app.services.agent_delegation_service import create_persistent_authorization
from classroom_app.services.agent_platform_write_service import dispatch_write, platform_write_catalog
from classroom_app.services.agent_secure_account_actions import secure_action_catalog
from tests.test_agent_platform_writes import PlatformWriteFixture


class SecureConfirmationHTTPTests(PlatformWriteFixture):
    password = "Synthetic-password-2026!"

    def setUp(self):
        super().setUp()
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=7")
        self.conn.commit()
        self.actor = self.teacher
        app = FastAPI()
        app.include_router(agent_tasks.router)
        app.dependency_overrides[agent_tasks.get_current_user] = lambda: self.actor
        guard = patch.object(agent_tasks, "get_db_connection", self.connection)
        guard.start()
        self.addCleanup(guard.stop)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.url = "/api/agent-tasks/10/actions/0"

    def profile(self, **extra):
        return {"name": "Synthetic teacher", "email": "new@example.test", "is_super_admin": False,
                "school_code": "test-school", "school_name": "Test school", "college": "Test college",
                "department": "Test department", **extra}

    def target(self, teacher_id=8, **extra):
        return {"teacher_id": teacher_id, "expected_revision": identity._revision(accounts.get_teacher_account(self.conn, teacher_id)), **extra}

    def proposal(self, action, params):
        self.conn.execute("UPDATE agent_tasks SET status='failed',result_detail_json=? WHERE id=10",
            (json.dumps({"proposed_actions": [{"action": action, "params": params}]}),))
        self.conn.commit()
        response = self.client.post(self.url + "/preview", json={})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("secure_input", response.json()["execution_mode"])
        self.assertEqual("password", response.json()["secure_fields"][0]["name"])
        self.assertNotIn("password", response.json()["params"])
        self.assertNotIn(self.password, response.text)
        return {"confirmation_token": response.json()["confirmation_token"], "secure_inputs": {"password": self.password}}

    def assert_no_secret_in_history(self, *responses):
        serialized = "\n".join(str(tuple(row)) for table in ("agent_tasks", "agent_task_events", "agent_action_executions")
                               for row in self.conn.execute(f"SELECT * FROM {table}"))
        serialized += "\n".join(response.text for response in responses)
        self.assertNotIn(self.password, serialized)
        self.assertNotIn("hashed_password", serialized)
        self.assertNotIn("secure_input_fingerprint", serialized)

    def test_create_replays_exact_receipt_and_rejects_new_secret_same_operation(self):
        request = self.proposal("create_teacher_account_secure", self.profile())
        first = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, first.status_code, first.text)
        second = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, second.status_code, second.text)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["result"], second.json()["result"])
        self.assertEqual(1, self.count("agent_action_executions"))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_events WHERE event_type='action_executed'").fetchone()[0])
        created = self.conn.execute("SELECT * FROM teachers WHERE email='new@example.test'").fetchone()
        self.assertTrue(verify_password(self.password, created["hashed_password"]))
        self.assertFalse(created["is_super_admin"])
        changed = self.client.post(self.url + "/execute", json={**request, "secure_inputs": {"password": "Another-valid-password!"}})
        self.assertEqual(409, changed.status_code, changed.text)
        self.assertEqual(3, self.count("teachers"))
        self.assert_no_secret_in_history(first, second, changed)

    def test_password_never_accepted_as_public_parameter_or_mcp_write(self):
        token = self.token()
        with self.assertRaises(HTTPException):
            dispatch_write(self.conn, token, "model-must-not-reset", "reset_teacher_password_secure", self.target())
        self.conn.rollback()
        self.assertNotIn("reset_teacher_password_secure", [item["action"] for item in platform_write_catalog(actor_role="teacher", is_super_admin=True)["actions"]])
        self.assertEqual([], secure_action_catalog(actor_role="student", is_super_admin=True))
        self.assertEqual([], secure_action_catalog(actor_role="teacher", is_super_admin=False))
        request = self.proposal("reset_teacher_password_secure", self.target())
        response = self.client.post(self.url + "/preview", json={"params": {"password": self.password}})
        self.assertEqual(400, response.status_code, response.text)
        response = self.client.post(self.url + "/execute", json={**request, "params": {"password": self.password}})
        self.assertEqual(400, response.status_code, response.text)
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assert_no_secret_in_history(response)

    def test_secret_validation_stale_revision_and_live_admin_check(self):
        request = self.proposal("reset_teacher_password_secure", self.target())
        for secret in (None, {}, {"password": "short"}, {"password": "密" * 25}, {"password": self.password, "extra": "x"}):
            response = self.client.post(self.url + "/execute", json={**request, "secure_inputs": secret})
            self.assertEqual(400, response.status_code, response.text)
        self.conn.execute("UPDATE teachers SET name='Changed on Web' WHERE id=8")
        self.conn.commit()
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(409, response.status_code, response.text)
        self.conn.execute("UPDATE teachers SET is_super_admin=0 WHERE id=7")
        self.conn.commit()
        for suffix in ("/preview", "/execute"):
            response = self.client.post(self.url + suffix, json=request)
            self.assertEqual(403, response.status_code, response.text)
        self.actor = self.student
        response = self.client.post(self.url + "/execute", json=request)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assert_no_secret_in_history(response)

    def test_self_reset_commits_public_receipt_and_revokes_login_and_persistent_grant(self):
        self.token()
        persistent = create_persistent_authorization(self.conn, actor_role="teacher", actor_id=7,
            source_session_id="teacher-session", scopes=["platform:read"], intent_reference="synthetic rule", ttl_seconds=600)
        self.conn.commit()
        request = self.proposal("reset_teacher_password_secure", self.target(7))
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json()["result"]["requires_relogin"])
        self.assertTrue(response.json()["task"]["result_detail"]["proposed_actions"][0]["executed"])
        self.assertEqual("completed", self.conn.execute("SELECT status FROM agent_action_executions").fetchone()[0])
        self.assertEqual("revoked", self.conn.execute("SELECT status FROM agent_task_delegations").fetchone()[0])
        self.assertEqual("revoked", self.conn.execute("SELECT status FROM agent_persistent_authorizations WHERE id=?", (persistent["id"],)).fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:7'").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='student:7'").fetchone()[0])
        self.assertTrue(verify_password(self.password, self.conn.execute("SELECT hashed_password FROM teachers WHERE id=7").fetchone()[0]))
        self.assert_no_secret_in_history(response)

    def test_marker_failure_rolls_back_password_receipt_and_revocation_then_retry_succeeds(self):
        self.token()
        request = self.proposal("reset_teacher_password_secure", self.target(7))
        with patch.object(agent_tasks, "mark_proposed_action_executed", side_effect=RuntimeError("synthetic failure")):
            response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(500, response.status_code)
        self.assertEqual("test", self.conn.execute("SELECT hashed_password FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assertEqual("active", self.conn.execute("SELECT status FROM agent_task_delegations").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:7'").fetchone()[0])
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, response.status_code, response.text)
        self.assert_no_secret_in_history(response)

    def test_restore_is_explicit_and_preserves_target_identifier(self):
        self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=8")
        self.conn.commit()
        request = self.proposal("create_teacher_account_secure", self.profile(email="8@example.test"))
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual(0, self.conn.execute("SELECT is_active FROM teachers WHERE id=8").fetchone()[0])
        request = self.proposal("restore_teacher_account_secure", {**self.target(), **self.profile(email="8@example.test", is_super_admin=True)})
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(8, response.json()["result"]["ref_id"])
        self.assertEqual(2, self.count("teachers"))
        self.assertEqual(1, self.conn.execute("SELECT is_active FROM teachers WHERE id=8").fetchone()[0])
        self.assertTrue(response.json()["result"]["teacher"]["is_super_admin"])
        self.assert_no_secret_in_history(response)
