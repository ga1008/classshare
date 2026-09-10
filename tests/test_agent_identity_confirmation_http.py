"""Real HTTP preview/confirmation through normal domain writes and one transaction.

Only the outer logged-in-user dependency is a synthetic login; task loading,
preview signature, live database authority, domain services and receipts are real.
"""
import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers import agent_tasks
from classroom_app.services import agent_identity_management_adapter as identity
from classroom_app.services import teacher_account_service as accounts
from tests.test_agent_platform_writes import PlatformWriteFixture


class IdentityConfirmationHTTPTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        self.conn.execute("UPDATE teachers SET is_super_admin=1")
        accounts.upsert_teacher_membership(self.conn, teacher_id=8, school_code="B", school_name="Other school",
            college="Other college", department="Other department", is_primary=True, actor_teacher_id=7)
        self.conn.commit()
        app = FastAPI()
        app.include_router(agent_tasks.router)
        app.dependency_overrides[agent_tasks.get_current_user] = lambda: self.teacher
        guard = patch.object(agent_tasks, "get_db_connection", self.connection)
        guard.start()
        self.addCleanup(guard.stop)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.url = "/api/agent-tasks/10/actions/0"

    def proposal(self, action, params):
        proposal = {"action": action, "params": params, "label": action}
        self.conn.execute("UPDATE agent_tasks SET status='failed',result_detail_json=? WHERE id=10",
                          (json.dumps({"proposed_actions": [proposal]}),))
        self.conn.commit()
        preview = self.client.post(self.url + "/preview", json={})
        self.assertEqual(200, preview.status_code, preview.text)
        return {"confirmation_token": preview.json()["confirmation_token"]}

    def params(self, target, **extra):
        return {"teacher_id": target, "expected_revision": identity._revision(accounts.get_teacher_account(self.conn, target)), **extra}

    def test_real_http_self_demotion_returns_receipt_then_logs_out_without_new_runner(self):
        request = self.proposal("revoke_teacher_super_admin", self.params(7))
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertTrue(body["result"]["agent_stop_required"])
        self.assertTrue(body["task"]["result_detail"]["proposed_actions"][0]["executed"])
        self.assertNotIn("source_session", response.text)
        self.assertNotIn("hashed_password", response.text)
        row = self.conn.execute("SELECT * FROM agent_action_executions").fetchone()
        self.assertEqual("completed", row["status"])
        self.assertEqual("user_confirmation", row["source_kind"])
        self.assertIsNone(row["attempt_id"])
        self.assertEqual(0, self.count("agent_task_attempts"))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:7'").fetchone()[0])
        self.assertEqual("failed", self.conn.execute("SELECT status FROM agent_tasks WHERE id=10").fetchone()[0])

    def test_real_http_identity_confirmation_replays_one_committed_receipt(self):
        request = self.proposal("manage_teacher_account", self.params(8, name="Updated through HTTP"))
        first = self.client.post(self.url + "/execute", json=request)
        second = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(200, second.status_code, second.text)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["result"], second.json()["result"])
        self.assertEqual(1, self.count("agent_action_executions"))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_events WHERE event_type='action_executed'").fetchone()[0])

    def test_real_http_self_college_rename_updates_scope_and_marks_proposal_once(self):
        accounts.upsert_teacher_membership(self.conn, teacher_id=7, school_code="SELF", school_name="Own school",
            college="Own college", department="Own department", is_primary=True, actor_teacher_id=7)
        self.conn.commit()
        self.token()
        college = self.conn.execute("SELECT * FROM organization_colleges WHERE college_name='Own college'").fetchone()
        request = self.proposal("update_organization_college", {"college_id": college["id"], "college_name": "Renamed college",
                                                                 "expected_updated_at": college["updated_at"]})
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json()["result"]["agent_stop_required"])
        self.assertEqual("Renamed college", self.conn.execute("SELECT college FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual("Renamed college", self.conn.execute("SELECT college FROM teacher_organization_memberships WHERE teacher_id=7").fetchone()[0])
        self.assertEqual("revoked", self.conn.execute("SELECT status FROM agent_task_delegations").fetchone()[0])
        self.assertTrue(response.json()["task"]["result_detail"]["proposed_actions"][0]["executed"])

    def test_proposal_marker_failure_rolls_back_domain_receipt_and_session_revocation(self):
        request = self.proposal("revoke_teacher_super_admin", self.params(7))
        with patch.object(agent_tasks, "mark_proposed_action_executed", side_effect=RuntimeError("synthetic marker failure")):
            response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(500, response.status_code)
        self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:7'").fetchone()[0])
        self.assertEqual(0, self.count("agent_action_executions"))
        proposal = json.loads(self.conn.execute("SELECT result_detail_json FROM agent_tasks WHERE id=10").fetchone()[0])["proposed_actions"][0]
        self.assertFalse(proposal.get("executed"))

    def test_preview_tampering_and_stale_identity_revision_cannot_mutate(self):
        request = self.proposal("manage_teacher_account", self.params(8, name="Originally approved"))
        response = self.client.post(self.url + "/execute", json={**request, "params": {"name": "Unapproved replacement"}})
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual(0, self.count("agent_action_executions"))
        self.conn.execute("UPDATE teachers SET name='Concurrent Web change' WHERE id=8")
        self.conn.commit()
        response = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assertEqual("Concurrent Web change", self.conn.execute("SELECT name FROM teachers WHERE id=8").fetchone()[0])
