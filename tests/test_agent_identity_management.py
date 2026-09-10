import contextlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.migrations import _ensure_organization_catalog_schema
from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.services import agent_identity_management_adapter as adapter, teacher_account_service as domain
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation, verify_task_delegation


def extend_account_fields(conn, *, engine="sqlite"):
    """Extend the deliberately small native authority fixture, without DDL magic."""
    for name in ("email", "phone", "wechat", "qq", "homepage_url", "description", "created_at", "updated_at", "password_updated_at", "deactivated_at"):
        conn.execute(f"ALTER TABLE teachers ADD COLUMN {name} TEXT DEFAULT ''")
    conn.execute("ALTER TABLE teachers ADD COLUMN deactivated_by_teacher_id BIGINT")
    for name in ("source", "created_at", "deactivated_at"):
        conn.execute(f"ALTER TABLE teacher_organization_memberships ADD COLUMN {name} TEXT DEFAULT ''")
    for name in ("created_by_teacher_id", "updated_by_teacher_id"):
        conn.execute(f"ALTER TABLE teacher_organization_memberships ADD COLUMN {name} BIGINT")
    key_type = "BIGSERIAL" if engine == "postgres" else "INTEGER"
    conn.execute(f"CREATE TABLE agent_task_events(id {key_type} PRIMARY KEY,task_id BIGINT,event_type TEXT,message TEXT,detail_json TEXT,created_at TEXT)")


class AgentIdentityManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "identity.sqlite3"
        self.conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.patch = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.conn.executescript("""
            CREATE TABLE teachers(id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER DEFAULT 1,is_super_admin INTEGER DEFAULT 0,
                school_code TEXT,school_name TEXT,college TEXT,department TEXT);
            CREATE TABLE teacher_organization_memberships(id INTEGER PRIMARY KEY AUTOINCREMENT,teacher_id INTEGER,
                school_code TEXT,school_name TEXT,college TEXT,department TEXT,is_active INTEGER DEFAULT 1,is_primary INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT '', UNIQUE(teacher_id,school_code));
            CREATE TABLE agent_tasks(id INTEGER PRIMARY KEY,teacher_id INTEGER,actor_role TEXT,actor_id INTEGER,status TEXT,cancel_requested_at TEXT);
            CREATE TABLE user_sessions(session_user_key TEXT PRIMARY KEY,session_id TEXT,user_id TEXT,role TEXT,expires_at TEXT);
            INSERT INTO teachers VALUES(7,'Admin A',1,1,'a','School A','College','Department'),(8,'Admin B',1,1,'b','School B','College','Department'),
                (9,'Teacher C',1,0,'a','School A','College','Department');
            INSERT INTO agent_tasks VALUES(10,7,'teacher',7,'running',NULL),(11,9,'teacher',9,'running',NULL);
            INSERT INTO user_sessions VALUES('teacher:7','admin-session','7','teacher','2099-01-01T00:00:00+00:00'),
                ('teacher:8','second-admin-session','8','teacher','2099-01-01T00:00:00+00:00'),
                ('teacher:9','teacher-session','9','teacher','2099-01-01T00:00:00+00:00');
        """)
        extend_account_fields(self.conn)
        _ensure_organization_catalog_schema(self.conn)
        self.conn.execute("UPDATE teachers SET email=CAST(id AS TEXT)||'@example.test'")
        for teacher in (7, 8, 9):
            domain.upsert_teacher_membership(self.conn, teacher_id=teacher, is_primary=True)
        ensure_agent_authority_schema(self.conn)
        self.attempt = create_task_attempt(self.conn, task_id=10, worker_id="identity", startup_key="identity")
        self.token = issue_task_delegation(self.conn, task_id=10, attempt_id=self.attempt["id"], fencing_token=1,
            purpose="tools", scopes=["platform:read", "platform:write"], source_session_id="admin-session")["token"]
        self.conn.commit()

    def params(self, teacher_id=8, **extra):
        return {"teacher_id": teacher_id, "expected_revision": adapter._revision(domain.get_teacher_account(self.conn, teacher_id)), **extra}

    def execute(self, action, params, operation_id="identity-write"):
        result = adapter.dispatch_identity_write(self.conn, self.token, operation_id, action, params)
        self.conn.commit()
        return result

    def test_reads_are_super_admin_only_bounded_and_never_return_passwords(self):
        page = adapter.read_identity_management(self.conn, self.token, "identity.teachers", {"limit": 2})
        self.assertEqual(2, len(page["items"]))
        self.assertTrue(page["has_more"])
        self.assertNotIn("hashed_password", json.dumps(page))
        self.assertEqual([], adapter.read_identity_management(self.conn, self.token, "identity.teachers", {"q": "%"})["items"])
        attempt = create_task_attempt(self.conn, task_id=11, worker_id="ordinary", startup_key="ordinary")
        token = issue_task_delegation(self.conn, task_id=11, attempt_id=attempt["id"], fencing_token=1, purpose="tools",
            scopes=["platform:read", "platform:write"], source_session_id="teacher-session")["token"]
        with self.assertRaises(HTTPException) as error:
            adapter.read_identity_management(self.conn, token, "identity.teacher", {"teacher_id": 7})
        self.assertEqual(403, error.exception.status_code)
        with self.assertRaises(HTTPException):
            adapter.read_identity_management(self.conn, self.token, "identity.teachers", {"limit": 1000})

    def test_same_operation_replays_once_and_rejects_changed_or_stale_params(self):
        params = self.params(9)
        first = self.execute("grant_teacher_super_admin", params)
        second = self.execute("grant_teacher_super_admin", params)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["result"], second["result"])
        with self.assertRaises(HTTPException) as error:
            self.execute("grant_teacher_super_admin", self.params(8))
        self.assertEqual(409, error.exception.status_code)
        self.conn.rollback()
        with self.assertRaises(HTTPException) as error:
            self.execute("revoke_teacher_super_admin", params, "stale-operation")
        self.assertEqual(409, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])

    def test_self_demotion_commits_receipt_then_revokes_tokens_and_session(self):
        result = self.execute("revoke_teacher_super_admin", self.params(7))
        self.assertTrue(result["result"]["agent_stop_required"])
        self.assertEqual(0, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual("completed", self.conn.execute("SELECT status FROM agent_action_executions").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id='7'").fetchone()[0])
        with self.assertRaises(HTTPException):
            verify_task_delegation(self.conn, self.token, purpose="tools")

    def test_own_membership_change_commits_and_stops_old_authority_without_logging_out(self):
        result = self.execute("upsert_teacher_membership", self.params(7, school_code="a", school_name="School A",
            college="Changed College", department="Changed Department", is_primary=1))
        self.assertTrue(result["result"]["authority_transition"])
        self.assertEqual(result["result"]["teacher"]["department"], self.conn.execute("SELECT department FROM teachers WHERE id=7").fetchone()[0])
        self.assertNotEqual("Department", result["result"]["teacher"]["department"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id='7'").fetchone()[0])
        self.assertEqual("revoked", self.conn.execute("SELECT status FROM agent_task_delegations").fetchone()[0])

    def test_display_name_change_does_not_revoke_unchanged_authority(self):
        result = self.execute("manage_teacher_account", self.params(7, name="Renamed Administrator"))
        self.assertFalse(result["result"]["agent_stop_required"])
        verify_task_delegation(self.conn, self.token, purpose="tools")

    def test_self_deactivate_last_admin_and_last_membership_rules_match_web(self):
        for action, params in [
            ("deactivate_teacher_account", self.params(7)),
            ("deactivate_teacher_membership", self.params(7, membership_id=domain.list_teacher_memberships(self.conn, 7)[0]["id"])),
        ]:
            with self.assertRaises(HTTPException) as error:
                self.execute(action, params)
            self.assertEqual(400, error.exception.status_code)
            self.conn.rollback()
        self.conn.execute("UPDATE teachers SET is_super_admin=0 WHERE id=8")
        self.conn.commit()
        with self.assertRaises(HTTPException) as error:
            self.execute("revoke_teacher_super_admin", self.params(7))
        self.assertEqual(400, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])

    def test_business_receipt_and_revocation_roll_back_together(self):
        params = self.params(7)
        adapter.dispatch_identity_write(self.conn, self.token, "rolled-back", "revoke_teacher_super_admin", params)
        self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])
        verify_task_delegation(self.conn, self.token, purpose="tools")

    def test_transition_proof_cannot_survive_commit_or_accept_unregistered_action(self):
        with self.assertRaises(HTTPException):
            adapter.begin_authority_changing_operation(self.conn, self.token, "bad", "arbitrary_sql", {})
        claim = adapter.begin_authority_changing_operation(self.conn, self.token, "proof", "update_organization_college", {"college_id": 1})
        self.conn.commit()
        with self.assertRaises(HTTPException) as error:
            adapter.complete_authority_changing_operation(self.conn, claim["proof"], {})
        self.assertEqual(409, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual("executing", self.conn.execute("SELECT status FROM agent_action_executions").fetchone()[0])

    def test_transition_proof_is_connection_bound_and_single_use(self):
        claim = adapter.begin_authority_changing_operation(self.conn, self.token, "one-use", "manage_teacher_account", self.params(9, name="Changed"))
        other = sqlite3.connect(self.path)
        self.addCleanup(other.close)
        with self.assertRaises(HTTPException) as error:
            adapter.complete_authority_changing_operation(other, claim["proof"], {})
        self.assertEqual(403, error.exception.status_code)
        adapter.complete_authority_changing_operation(self.conn, claim["proof"], {"label": "unchanged"})
        with self.assertRaises(HTTPException) as error:
            adapter.complete_authority_changing_operation(self.conn, claim["proof"], {})
        self.assertEqual(403, error.exception.status_code)

    def fresh(self, action, params, operation_id="proposal:10:0", **source):
        return adapter.dispatch_user_identity_write(self.conn, user={"role": "teacher", "id": 7, "session_id": "admin-session"},
            source_session_id=source.pop("source_session_id", "admin-session"), task_id=source.pop("task_id", 10),
            operation_id=operation_id, action=action, params=params, **source)

    def test_fresh_terminal_self_demotion_has_receipt_without_reviving_attempt(self):
        self.conn.execute("UPDATE agent_tasks SET status='failed' WHERE id=10")
        self.conn.execute("UPDATE agent_task_attempts SET status='failed',lease_expires_at=0")
        self.conn.execute("UPDATE agent_task_delegations SET status='revoked'")
        self.conn.commit()
        result = self.fresh("revoke_teacher_super_admin", self.params(7))
        self.conn.commit()
        self.assertTrue(result["result"]["agent_stop_required"])
        row = dict(self.conn.execute("SELECT * FROM agent_action_executions").fetchone())
        self.assertEqual("completed", row["status"])
        self.assertEqual("user_confirmation", row["source_kind"])
        self.assertIsNone(row["attempt_id"])
        self.assertIsNone(row["delegation_id"])
        self.assertEqual("failed", self.conn.execute("SELECT status FROM agent_tasks WHERE id=10").fetchone()[0])
        self.assertEqual("failed", self.conn.execute("SELECT status FROM agent_task_attempts").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id='7'").fetchone()[0])

    def test_fresh_confirmation_replay_and_rollback_preserve_all_three_boundaries(self):
        self.conn.execute("UPDATE agent_tasks SET status='completed' WHERE id=10")
        self.conn.commit()
        params = self.params(7)
        self.fresh("revoke_teacher_super_admin", params)
        self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id='7'").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])
        params = self.params(9)
        first = self.fresh("grant_teacher_super_admin", params)
        self.conn.commit()
        replay = self.fresh("grant_teacher_super_admin", params)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["result"], replay["result"])

    def test_fresh_confirmation_rejects_wrong_session_owner_and_running_task(self):
        for source in ({}, {"source_session_id": "forged"}, {"task_id": 11}):
            with self.subTest(source=source), self.assertRaises(HTTPException):
                self.fresh("grant_teacher_super_admin", self.params(9), **source)
            self.conn.rollback()
        self.conn.execute("UPDATE agent_tasks SET status='completed'")
        self.conn.commit()
        for source in ({"source_session_id": "forged"}, {"task_id": 11}):
            with self.subTest(source=source), self.assertRaises(HTTPException):
                self.fresh("grant_teacher_super_admin", self.params(9), **source)
            self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=9").fetchone()[0])

    def test_fresh_proof_checks_session_and_transaction_after_self_change(self):
        self.conn.execute("UPDATE agent_tasks SET status='canceled' WHERE id=10")
        self.conn.commit()
        for mutation in ("DELETE FROM user_sessions WHERE user_id='7'", "UPDATE agent_tasks SET status='running' WHERE id=10", None):
            with self.subTest(mutation=mutation):
                claim = adapter.begin_user_authority_changing_operation(self.conn,
                    user={"role": "teacher", "id": 7}, source_session_id="admin-session", task_id=10,
                    operation_id="fresh-proof", action="revoke_teacher_super_admin", params=self.params(7))
                domain.revoke_teacher_super_admin(self.conn, teacher_id=7)
                if mutation:
                    self.conn.execute(mutation)
                else:
                    self.conn.execute("RELEASE SAVEPOINT " + claim["proof"].savepoint)
                with self.assertRaises(HTTPException):
                    adapter.complete_user_authority_changing_operation(self.conn, claim["proof"], {})
                self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])

    def test_transition_rechecks_session_cancel_and_lease_before_receipt(self):
        for mutation in ("DELETE FROM user_sessions WHERE user_id='7'", "UPDATE agent_tasks SET cancel_requested_at='cancel' WHERE id=10",
                         "UPDATE agent_task_attempts SET lease_expires_at=0"):
            with self.subTest(mutation=mutation):
                claim = adapter.begin_authority_changing_operation(self.conn, self.token, "transition", "revoke_teacher_super_admin", self.params(7))
                domain.revoke_teacher_super_admin(self.conn, teacher_id=7)
                self.conn.execute(mutation)
                with self.assertRaises(HTTPException):
                    adapter.complete_authority_changing_operation(self.conn, claim["proof"], {})
                self.conn.rollback()
                self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])

    def test_ordinary_web_concurrent_demotion_keeps_one_active_admin(self):
        barrier = threading.Barrier(2)
        def revoke(teacher):
            conn = sqlite3.connect(self.path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                barrier.wait(timeout=5)
                domain.revoke_teacher_super_admin(conn, teacher_id=teacher)
                conn.commit()
                return "done"
            except ValueError:
                conn.rollback()
                return "last_admin"
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(revoke, (7, 8)))
        self.assertCountEqual(["done", "last_admin"], results)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM teachers WHERE is_super_admin=1").fetchone()[0])

    def test_secret_input_is_explicitly_not_accepted_by_model_tool(self):
        with self.assertRaises(HTTPException) as error:
            self.execute("manage_teacher_account", self.params(9, password="SYNTHETIC-SECRET"))
        self.assertEqual(400, error.exception.status_code)
        self.assertNotIn("SYNTHETIC-SECRET", str(error.exception.detail))

    def test_real_mcp_catalog_and_reads_enforce_admin_identity_and_revocation(self):
        from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
        from classroom_app.routers import agent_bridge
        from classroom_app.services import agent_gateway_budget
        ensure_agent_request_budget_schema(self.conn)
        self.conn.executescript("""
            CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,class_id INTEGER,school_code TEXT,school_name TEXT,college TEXT,
                department TEXT,enrollment_status TEXT);
            INSERT INTO students VALUES(7,'Student same ID',1,'a','School A','College','Department','active');
            INSERT INTO agent_tasks VALUES(12,NULL,'student',7,'running',NULL);
            INSERT INTO user_sessions VALUES('student:7','student-session','7','student','2099-01-01T00:00:00+00:00');
        """)
        tokens = {"admin": self.token}
        for role, task_id, session in (("teacher", 11, "teacher-session"), ("student", 12, "student-session")):
            attempt = create_task_attempt(self.conn, task_id=task_id, worker_id=role, startup_key=role)
            tokens[role] = issue_task_delegation(self.conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=1,
                purpose="tools", scopes=["platform:read"], source_session_id=session)["token"]
        self.conn.commit()
        @contextlib.contextmanager
        def connection():
            with self.conn:
                yield self.conn
        app = FastAPI()
        app.include_router(agent_bridge.router)
        with patch.object(agent_bridge, "get_db_connection", connection), patch.object(agent_gateway_budget, "get_db_connection", connection), TestClient(app) as client:
            def call(role, name, arguments=None):
                return client.post("/api/agent-bridge/mcp", headers={"Authorization": "Bearer " + tokens[role]},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
            admin = call("admin", "platform_capabilities").json()["result"]
            catalog = json.loads(admin["content"][0]["text"])
            self.assertTrue(adapter.IDENTITY_READ_KEYS.issubset({item["key"] for item in catalog["operations"]}))
            response = call("admin", "platform_read", {"operation_key": "identity.teacher", "query_params": {"teacher_id": 9}})
            self.assertEqual(200, response.status_code)
            data = json.loads(response.json()["result"]["content"][0]["text"])
            self.assertEqual(9, data["teacher"]["id"])
            self.assertNotIn("hashed_password", str(data))
            self.assertNotIn("admin-session", str(data))
            for key, params in (("identity.teachers", {"limit": 3}), ("identity.memberships", {"teacher_id": 9})):
                response = call("admin", "platform_read", {"operation_key": key, "query_params": params})
                self.assertEqual(200, response.status_code)
                self.assertFalse(response.json()["result"].get("isError", False))
                self.assertNotIn("hashed_password", response.text)
                self.assertNotIn("admin-session", response.text)
            for role in ("teacher", "student"):
                hidden = json.loads(call(role, "platform_capabilities").json()["result"]["content"][0]["text"])
                self.assertFalse(adapter.IDENTITY_READ_KEYS & {item["key"] for item in hidden["operations"]})
                rejected = call(role, "platform_read", {"operation_key": "identity.teacher", "query_params": {"teacher_id": 7}})
                self.assertTrue(rejected.json()["result"]["isError"])
            self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
            self.conn.commit()
            self.assertEqual(401, call("admin", "platform_read", {"operation_key": "identity.teachers"}).status_code)
