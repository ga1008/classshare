import json
import unittest
from unittest.mock import patch

from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.services import agent_gateway_budget
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests import test_agent_authority as authority_fixture


class McpBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = authority_fixture.AgentAuthorityTests()
        self.fixture.setUp(http_authority=False)
        self.conn = self.fixture.conn
        ensure_agent_authority_schema(self.conn)
        ensure_agent_request_budget_schema(self.conn)
        self.budget_patch = patch.object(agent_gateway_budget, "get_db_connection", self.fixture.borrow_connection)
        self.budget_patch.start()
        self.conn.executescript("""
            CREATE TABLE user_sessions (session_id TEXT, session_user_key TEXT, user_id TEXT, role TEXT, expires_at TEXT);
            INSERT INTO user_sessions VALUES ('mcp-teacher', 'teacher:1', '1', 'teacher', '2099-01-01T00:00:00+00:00');
            INSERT INTO user_sessions VALUES ('mcp-student', 'student:1', '1', 'student', '2099-01-01T00:00:00+00:00');
            INSERT INTO agent_tasks VALUES (9, NULL, 'student', 1, 'running', NULL, 'Student task');
        """)
        self.tokens = {}
        for task_id, role in ((7, "teacher"), (9, "student")):
            attempt = create_task_attempt(self.conn, task_id=task_id, worker_id="test", startup_key="mcp")
            self.tokens[role] = issue_task_delegation(self.conn, task_id=task_id, attempt_id=attempt["id"],
                fencing_token=attempt["fencing_token"], purpose="tools", scopes=["platform:read", "web:fetch"],
                source_session_id="mcp-" + role)["token"]
        self.conn.commit()

    def tearDown(self):
        self.budget_patch.stop()
        self.fixture.tearDown()

    def call(self, method, params=None, *, role="teacher"):
        return self.fixture.client.post("/api/agent-bridge/mcp", headers={"Authorization": "Bearer " + self.tokens[role]},
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def test_initialize_and_tools_are_native_mcp(self):
        result = self.call("initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
        self.assertEqual("2025-06-18", result["protocolVersion"])
        self.assertIn("tools", result["capabilities"])
        tools = self.call("tools/list").json()["result"]["tools"]
        self.assertIn("platform_read", [tool["name"] for tool in tools])
        self.assertNotIn("sql", str([tool["name"] for tool in tools]))

    def test_same_number_student_and_teacher_receive_own_identity_and_catalog(self):
        for role in ("teacher", "student"):
            response = self.call("tools/call", {"name": "platform_overview"}, role=role).json()["result"]
            value = json.loads(response["content"][0]["text"])
            self.assertEqual(role + ":1", value["actor"]["actor_key"])
        names = [tool["name"] for tool in self.call("tools/list", role="student").json()["result"]["tools"]]
        self.assertNotIn("platform_query", names)
        self.assertIn("platform_read", names)

    def test_named_query_still_enforces_actor_scope(self):
        response = self.call("tools/call", {"name": "platform_query", "arguments": {"query": "my_classrooms"}})
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        value = json.loads(result["content"][0]["text"])
        self.assertIn("Course A", str(value))
        self.assertNotIn("Course B", str(value))
        denied = self.call("tools/call", {"name": "platform_query", "arguments": {"query": "my_classrooms", "params": {"teacher_id": 2}}})
        self.assertTrue(denied.json()["result"]["isError"])

    def test_model_token_and_legacy_token_cannot_enter_mcp(self):
        response = self.fixture.client.post("/api/agent-bridge/mcp", headers=self.fixture.headers,
                                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(401, response.status_code)
        self.conn.execute("UPDATE agent_task_delegations SET purpose = 'model'")
        self.conn.commit()
        self.assertEqual(401, self.call("tools/list").status_code)

    def test_reserved_arguments_and_unregistered_tools_are_rejected(self):
        for params in ({"name": "exec_sql", "arguments": {"sql": "select * from teachers"}},
                       {"name": "platform_query", "arguments": {"query": "my_classrooms", "sql": "select * from teachers"}}):
            self.assertEqual(-32602, self.call("tools/call", params).json()["error"]["code"])

    def test_revocation_is_checked_on_every_rpc(self):
        self.assertEqual(200, self.call("ping").status_code)
        self.conn.execute("DELETE FROM user_sessions WHERE role = 'teacher'")
        self.conn.commit()
        self.assertEqual(401, self.call("ping").status_code)

    def test_catalog_keeps_discovery_complete_but_loads_parameters_on_demand(self):
        from classroom_app.services.agent_platform_write_service import platform_write_catalog

        def catalog(arguments=None):
            response = self.call("tools/call", {"name": "platform_capabilities", "arguments": arguments or {}}).json()["result"]
            self.assertFalse(response["isError"])
            return json.loads(response["content"][0]["text"])

        index = catalog()
        self.assertEqual("index", index["catalog_mode"])
        expected = platform_write_catalog(actor_role="teacher")["actions"]
        self.assertEqual({item["action"] for item in expected}, {item["action"] for item in index["writes"]["actions"]})
        self.assertTrue(all("fields" not in item for item in index["writes"]["actions"]))
        details = catalog({"keys": ["create_blog_draft"]})
        self.assertEqual("parameters", details["catalog_mode"])
        self.assertEqual(["create_blog_draft"], [item["action"] for item in details["writes"]["actions"]])
        self.assertIn("content_md", details["writes"]["actions"][0]["fields"])
        self.assertEqual([], details["unavailable_keys"])
        self.assertEqual([], details["platform_requests"])
        # The real complete schema remains available; only repeated discovery
        # payloads shrink. No business capability is removed for this reduction.
        self.assertLess(len(json.dumps(index["writes"]["actions"])), len(json.dumps(expected)) * 0.5)

    def test_catalog_filters_discovery_and_never_promotes_unknown_or_admin_keys(self):
        for arguments in ({"keys": ["create_teacher_account_secure"]}, {"keys": ["arbitrary_sql"]}):
            result = self.call("tools/call", {"name": "platform_capabilities", "arguments": arguments}, role="student").json()["result"]
            self.assertFalse(result["isError"])
            value = json.loads(result["content"][0]["text"])
            self.assertEqual(arguments["keys"], value["unavailable_keys"])
            self.assertEqual([], value["user_input_actions"])
        result = self.call("tools/call", {"name": "platform_capabilities", "arguments": {"query": "blog"}}).json()["result"]
        value = json.loads(result["content"][0]["text"])
        self.assertTrue(value["writes"]["actions"])
        self.assertTrue(all("blog" in item["action"] for item in value["writes"]["actions"]))
        for arguments in ({"keys": []}, {"keys": ["a"] * 9}, {"keys": ["a", "a"]}, {"query": ""}, {"keys": ["a"], "query": "a"}):
            result = self.call("tools/call", {"name": "platform_capabilities", "arguments": arguments}).json()["result"]
            self.assertTrue(result["isError"])
