import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers import agent_bridge
from classroom_app.services import agent_task_service
from tests.test_agent_platform_writes import PlatformWriteFixture


class ContinuationTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO agent_tasks(id,task_uuid,teacher_id,actor_role,actor_id,teacher_name,task_type,title,private_instruction,status,result_detail_json) VALUES(12,'prior-task',7,'teacher',7,'Teacher 7','general','Prior','Prepare material','failed',?)",
            (json.dumps({"deliverable_markdown": "# Prior useful result", "completion_kind": "partial"}),))
        self.conn.execute("UPDATE agent_tasks SET parent_task_id=12 WHERE id=10")
        self.conn.execute("INSERT INTO agent_action_executions(id,operation_id,actor_role,actor_id,task_id,action,params_hash,status,result_json,created_at,updated_at) VALUES('receipt-old','prior-generate','teacher',7,12,'generate_session_document','hash','completed',?,1,1)",
            (json.dumps({"generation_task": {"id": 19}, "completion_status": "pending"}),))
        self.conn.commit()
        self.bearer = self.token(scopes=["platform:read"])
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        parent = self.workspace / "tasks" / "12"
        parent.mkdir(parents=True)
        (parent / "report.md").write_text("# Continue this actual document", encoding="utf-8")
        for name, value in (("classroom_app.services.agent_task_service.AGENT_TASK_WORKSPACE_ROOT", self.workspace),
                            ("classroom_app.services.agent_scoped_read_service.AGENT_TASK_WORKSPACE_ROOT", self.workspace),
                            ("classroom_app.routers.agent_bridge.get_db_connection", self.connection),
                            ("classroom_app.services.agent_gateway_budget.get_db_connection", self.connection)):
            guard = patch(name, value)
            guard.start()
            self.addCleanup(guard.stop)
        from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
        ensure_agent_request_budget_schema(self.conn)
        from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
        ensure_agent_platform_requests_schema(self.conn)
        self.conn.commit()
        guard = patch("classroom_app.services.agent_bridge_service.allowed_file_roots", return_value=[self.workspace])
        guard.start()
        self.addCleanup(guard.stop)
        app = FastAPI()
        app.include_router(agent_bridge.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def rpc(self, name, arguments):
        return self.client.post("/api/agent-bridge/mcp", headers={"Authorization": "Bearer " + self.bearer},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})

    def test_follow_up_reads_actual_prior_receipts_and_artifact_without_resubmitting(self):
        result = self.rpc("platform_task_context", {"task_id": 12}).json()["result"]
        self.assertFalse(result["isError"])
        value = json.loads(result["content"][0]["text"])
        self.assertEqual("prior-generate", value["operations"][0]["operation_id"])
        self.assertEqual(19, value["operations"][0]["result"]["generation_task"]["id"])
        self.assertEqual("report.md", value["artifacts"][0]["path"])
        document = self.rpc("platform_file", {"parent_task_id": 12, "path": "report.md"}).json()["result"]
        self.assertFalse(document["isError"])
        self.assertIn("Continue this actual document", document["content"][0]["text"])
        self.assertEqual(1, self.count("agent_action_executions"))

    def test_same_number_student_unrelated_task_and_running_parent_are_denied(self):
        for mutation in (
            "UPDATE agent_tasks SET actor_role='student',teacher_id=NULL WHERE id=12",
            "UPDATE agent_tasks SET actor_role='teacher',teacher_id=7,status='running' WHERE id=12",
            "UPDATE agent_tasks SET status='failed' WHERE id=12; UPDATE agent_tasks SET parent_task_id=NULL WHERE id=10",
        ):
            self.conn.executescript(mutation)
            result = self.rpc("platform_task_context", {"task_id": 12}).json()["result"]
            self.assertTrue(result["isError"])
        self.conn.execute("UPDATE agent_tasks SET parent_task_id=12 WHERE id=10")
        self.conn.execute("DELETE FROM user_sessions WHERE role='teacher'")
        self.conn.commit()
        self.assertEqual(401, self.rpc("platform_task_context", {"task_id": 12}).status_code)

    def test_file_escape_and_secret_configuration_stay_denied(self):
        (self.workspace / "secret.txt").write_text("hidden", encoding="utf-8")
        for path in ("../../secret.txt", "BRIDGE.md", ".env"):
            result = self.rpc("platform_file", {"parent_task_id": 12, "path": path}).json()["result"]
            self.assertTrue(result["isError"])
            self.assertNotIn("hidden", result["content"][0]["text"])

    def test_retry_carries_server_parent_reference_and_delivered_excerpt_even_with_no_history(self):
        self.conn.execute("UPDATE agent_tasks SET context_snapshot_json=? WHERE id=12", (json.dumps({"agent_options": {"no_history": True}}),))
        with patch.object(agent_task_service, "build_teacher_page_context", return_value={}):
            child = agent_task_service.create_retry_task(self.conn, self.teacher, 12)
        raw = self.conn.execute("SELECT context_snapshot_json FROM agent_tasks WHERE id=?", (child["id"],)).fetchone()[0]
        context = json.loads(raw)
        self.assertTrue(context["agent_options"]["no_history"])
        self.assertEqual(12, context["follow_up"]["parent_task_id"])
        self.assertIn("Prior useful result", context["follow_up"]["previous_delivery_excerpt"])
