"""Synthetic, offline regressions for Agent identity and bridge boundaries."""
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.routers import agent_bridge
from classroom_app.services import agent_bridge_service, agent_scoped_read_service
from classroom_app.services.agent_actor_service import resolve_agent_actor, resolve_live_task_actor
from classroom_app.services.organization_scope_service import load_teacher_org_memberships


class AgentAuthorityTests(unittest.TestCase):
    def setUp(self, *, http_authority=True):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER,
                is_super_admin INTEGER, school_code TEXT, school_name TEXT, college TEXT,
                department TEXT, hashed_password TEXT);
            INSERT INTO teachers VALUES (1, 'A', 1, 0, 'school-a', 'School A', 'college', 'one', 'SYNTHETIC-A');
            INSERT INTO teachers VALUES (2, 'B', 1, 0, 'school-b', 'School B', 'college', 'two', 'SYNTHETIC-B');
            CREATE TABLE teacher_organization_memberships (id INTEGER PRIMARY KEY, teacher_id INTEGER,
                school_code TEXT, school_name TEXT, college TEXT, department TEXT,
                is_primary INTEGER, is_active INTEGER, updated_at TEXT);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, class_id INTEGER,
                school_code TEXT, school_name TEXT, college TEXT, department TEXT, enrollment_status TEXT);
            INSERT INTO students VALUES (1, 'Student A', 10, 'school-a', 'School A', '', '', 'active');
            CREATE TABLE agent_tasks (id INTEGER PRIMARY KEY, teacher_id INTEGER, actor_role TEXT,
                actor_id INTEGER, status TEXT, cancel_requested_at TEXT, private_instruction TEXT);
            INSERT INTO agent_tasks VALUES (7, 1, NULL, NULL, 'running', NULL, 'SYNTHETIC TASK A');
            INSERT INTO agent_tasks VALUES (8, 2, NULL, NULL, 'running', NULL, 'SYNTHETIC TASK B');
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT, created_by_teacher_id INTEGER);
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, teacher_id INTEGER, course_id INTEGER, class_id INTEGER);
            INSERT INTO courses VALUES (10, 'Course A', 1), (20, 'Course B', 2);
            INSERT INTO classes VALUES (10, 'Class A'), (20, 'Class B');
            INSERT INTO class_offerings VALUES (10, 1, 10, 10), (20, 2, 20, 20);
            CREATE TABLE assignments (id INTEGER PRIMARY KEY, class_offering_id INTEGER, course_id INTEGER,
                title TEXT, status TEXT, due_at TEXT, created_at TEXT);
            INSERT INTO assignments VALUES (10, 10, 10, 'Assignment A', 'published', '', '2026-01-01');
            INSERT INTO assignments VALUES (20, 20, 20, 'Assignment B', 'published', '', '2026-01-01');
            CREATE TABLE submissions (id INTEGER PRIMARY KEY, assignment_id INTEGER, student_pk_id INTEGER);
        """)
        self.conn.commit()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tasks" / "7").mkdir(parents=True)
        (self.root / "tasks" / "8").mkdir(parents=True)
        (self.root / "tasks" / "7" / "RESULT.md").write_text("own result", encoding="utf-8")
        (self.root / "tasks" / "7" / "BRIDGE.md").write_text("SYNTHETIC BEARER", encoding="utf-8")
        (self.root / "tasks" / "8" / "RESULT.md").write_text("other result", encoding="utf-8")
        self.patches = [
            patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite"),
            patch.object(agent_scoped_read_service, "get_configured_db_engine", return_value="sqlite"),
            patch.object(agent_scoped_read_service, "AGENT_TASK_WORKSPACE_ROOT", self.root),
            patch.object(agent_bridge_service, "allowed_file_roots", return_value=[self.root]),
            patch.object(agent_bridge, "get_db_connection", self.borrow_connection),
            patch.object(agent_bridge, "build_user_knowledge_block", return_value="synthetic profile"),
        ]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(agent_bridge.router)
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer retired.legacy.token"}
        if http_authority:
            from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
            from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
            from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
            from classroom_app.services import agent_gateway_budget
            ensure_agent_authority_schema(self.conn)
            ensure_agent_request_budget_schema(self.conn)
            self.conn.executescript("""
                CREATE TABLE user_sessions (session_id TEXT, session_user_key TEXT, user_id TEXT, role TEXT, expires_at TEXT);
                INSERT INTO user_sessions VALUES ('fixture-session', 'teacher:1', '1', 'teacher', '2099-01-01T00:00:00+00:00');
            """)
            attempt = create_task_attempt(self.conn, task_id=7, worker_id="fixture", startup_key="http-authority")
            token = issue_task_delegation(self.conn, task_id=7, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                purpose="tools", scopes=["platform:read", "web:fetch"], source_session_id="fixture-session")["token"]
            self.conn.commit()
            budget_patch = patch.object(agent_gateway_budget, "get_db_connection", self.borrow_connection)
            budget_patch.start()
            self.patches.append(budget_patch)
            self.headers = {"Authorization": "Bearer " + token}

    @contextlib.contextmanager
    def borrow_connection(self):
        yield self.conn

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_actor_role_is_part_of_identity_and_no_credentials_are_returned(self):
        teacher = resolve_agent_actor(self.conn, "teacher", 1)
        student = resolve_agent_actor(self.conn, "student", 1)
        self.assertEqual("teacher:1", teacher.key)
        self.assertEqual("student:1", student.key)
        self.assertNotIn("SYNTHETIC-A", str(teacher.public_context()))
        self.assertFalse(student.is_super_admin)
        for role in ("admin", "assistant", "", "teacher:1"):
            with self.subTest(role=role), self.assertRaises(HTTPException):
                resolve_agent_actor(self.conn, role, 1)

    def test_admin_and_enrollment_are_loaded_live_not_cached(self):
        original = resolve_agent_actor(self.conn, "teacher", 1)
        self.conn.execute("UPDATE teachers SET is_super_admin = 1 WHERE id = 1")
        admin = resolve_agent_actor(self.conn, "teacher", 1)
        self.assertTrue(admin.is_super_admin)
        self.assertNotEqual(original.authority_fingerprint, admin.authority_fingerprint)
        self.conn.execute("UPDATE teachers SET is_active = 0 WHERE id = 1")
        with self.assertRaises(HTTPException):
            resolve_agent_actor(self.conn, "teacher", 1)
        self.conn.execute("UPDATE students SET enrollment_status = 'suspended' WHERE id = 1")
        with self.assertRaises(HTTPException):
            resolve_agent_actor(self.conn, "student", 1)

    def test_revoked_memberships_never_resurrect_legacy_scope(self):
        self.assertEqual("school-a", load_teacher_org_memberships(self.conn, 1)[0]["school_code"])
        self.conn.execute("INSERT INTO teacher_organization_memberships VALUES (1, 1, 'school-a', 'A', 'college', 'one', 1, 0, '')")
        self.assertEqual([], load_teacher_org_memberships(self.conn, 1))
        self.assertEqual([], list(resolve_agent_actor(self.conn, "teacher", 1).memberships))
        self.assertEqual(1, len(load_teacher_org_memberships(self.conn, 1, include_inactive=True)))

    def test_same_school_distinct_department_memberships_remain_available(self):
        self.conn.executemany(
            "INSERT INTO teacher_organization_memberships VALUES (?, 1, 'school-a', 'A', 'college', ?, 0, 1, '')",
            [(1, 'one'), (2, 'two')],
        )
        scopes = load_teacher_org_memberships(self.conn, 1)
        self.assertEqual({"1", "2"}, {scope["membership_id"] for scope in scopes})
        self.assertEqual(2, len({scope["department"] for scope in scopes}))

    def test_null_membership_active_flag_keeps_legacy_active_semantics(self):
        self.conn.execute("INSERT INTO teacher_organization_memberships VALUES (1, 1, 'school-b', 'B', '', '', 1, NULL, '')")
        self.assertEqual("school-b", load_teacher_org_memberships(self.conn, 1)[0]["school_code"])

    def test_all_bridge_routes_reject_stopped_or_cancel_requested_task(self):
        requests = [
            ("GET", "/meta", None), ("GET", "/schema", None),
            ("POST", "/query", {"query": "my_classrooms"}),
            ("POST", "/search", {"keyword": "A"}),
            ("POST", "/file", {"path": "RESULT.md"}),
            ("POST", "/web", {"url": "https://example.com"}),
        ]
        for status in ("queued", "completed", "failed", "canceled", "running"):
            self.conn.execute("UPDATE agent_tasks SET status=?, cancel_requested_at=? WHERE id=7",
                              (status, "2026-01-01" if status == "running" else None))
            for method, suffix, payload in requests:
                with self.subTest(status=status, endpoint=suffix):
                    response = self.client.request(method, "/api/agent-bridge" + suffix, json=payload, headers=self.headers)
                    self.assertEqual(401, response.status_code, response.text)

    def test_bridge_rejects_disabled_actor_and_forged_bearer(self):
        self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=1")
        response = self.client.get("/api/agent-bridge/schema", headers=self.headers)
        self.assertEqual(403, response.status_code)
        response = self.client.get("/api/agent-bridge/schema", headers={"Authorization": "Bearer 7.9999999999.fake"})
        self.assertEqual(401, response.status_code)

    def test_private_sql_and_alias_masking_bypass_are_not_executable(self):
        for sql in (
            "SELECT hashed_password AS value FROM teachers",
            "SELECT private_instruction FROM agent_tasks",
            "SELECT * FROM teachers", "WITH t AS (SELECT * FROM students) SELECT * FROM t",
        ):
            with self.subTest(sql=sql):
                response = self.client.post("/api/agent-bridge/query", json={"sql": sql}, headers=self.headers)
                self.assertEqual(422, response.status_code)
                self.assertNotIn("SYNTHETIC-A", response.text)
                self.assertNotIn("SYNTHETIC TASK B", response.text)

    def test_template_uses_current_actor_and_old_exact_template_is_compatible(self):
        expected = agent_bridge_service.EXAMPLE_QUERIES[0]["sql"]
        for payload in ({"query": "my_classrooms"}, {"sql": expected, "params": {"teacher_id": 1}}):
            response = self.client.post("/api/agent-bridge/query", json=payload, headers=self.headers)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual([10], [row["class_offering_id"] for row in response.json()["rows"]])
        denied = self.client.post("/api/agent-bridge/query", json={"query": "my_classrooms", "params": {"teacher_id": 2}}, headers=self.headers)
        self.assertEqual(403, denied.status_code)

    def test_cross_classroom_and_mismatched_assignment_are_rejected(self):
        for query, params in (
            ("classroom_assignments", {"class_offering_id": 20}),
            ("assignment_missing_students", {"class_offering_id": 10, "assignment_id": 20}),
        ):
            response = self.client.post("/api/agent-bridge/query", json={"query": query, "params": params}, headers=self.headers)
            self.assertEqual(403, response.status_code, response.text)

    def test_schema_contains_capabilities_not_unrestricted_database_tables(self):
        response = self.client.get("/api/agent-bridge/schema", headers=self.headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual({}, response.json()["tables"])
        self.assertIn("my_classrooms", {item["name"] for item in response.json()["queries"]})

    def test_task_file_scope_and_configuration_are_enforced(self):
        own = self.client.post("/api/agent-bridge/file", json={"path": "RESULT.md"}, headers=self.headers)
        self.assertEqual(200, own.status_code, own.text)
        self.assertEqual("own result", own.json()["content"])
        for path in ("../8/RESULT.md", "BRIDGE.md", str(self.root / "tasks" / "8" / "RESULT.md")):
            with self.subTest(path=path):
                response = self.client.post("/api/agent-bridge/file", json={"path": path}, headers=self.headers)
                self.assertEqual(403, response.status_code, response.text)

    def test_student_task_identity_does_not_fall_back_to_teacher_with_same_id(self):
        self.conn.execute("UPDATE agent_tasks SET actor_role='student', actor_id=1 WHERE id=7")
        _task, actor = resolve_live_task_actor(self.conn, 7)
        self.assertEqual("student:1", actor.key)
        response = self.client.post("/api/agent-bridge/query", json={"query": "my_classrooms"}, headers=self.headers)
        self.assertEqual(401, response.status_code)  # Former teacher delegation is revoked by actor change.


if __name__ == "__main__":
    unittest.main()
