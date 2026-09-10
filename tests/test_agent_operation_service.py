from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from classroom_app.services.agent_operation_service import (
    claim_agent_operation, complete_agent_operation,
    claim_user_agent_operation, complete_user_agent_operation,
)
from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from tests.test_agent_delegation_service import NOW, fixture_connection, issue_fixture


class AgentUserOperationTests(unittest.TestCase):
    def setUp(self):
        self.engine = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        self.engine.start()
        self.addCleanup(self.engine.stop)
        self.conn = fixture_connection()
        self.addCleanup(self.conn.close)
        self.conn.execute("UPDATE agent_tasks SET status='completed'")
        self.conn.execute("CREATE TABLE business_records(id INTEGER PRIMARY KEY, title TEXT)")
        self.conn.commit()

    def claim(self, conn=None, **overrides):
        return claim_user_agent_operation(conn or self.conn, **{
            "user": {"id": 7, "role": "teacher", "session_id": "teacher-session"},
            "source_session_id": "teacher-session", "task_id": 10,
            "operation_id": "proposal:10:0", "action": "create_draft", "params": {"title": "Draft"},
            "now": NOW, **overrides,
        })

    def complete(self, conn=None, **overrides):
        return complete_user_agent_operation(conn or self.conn, **{
            "user": {"id": 7, "role": "teacher", "session_id": "teacher-session"},
            "source_session_id": "teacher-session", "task_id": 10,
            "operation_id": "proposal:10:0", "result": {"id": 1}, "now": NOW, **overrides,
        })

    def assert_http(self, code, fn, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            fn(**kwargs)
        self.assertEqual(code, caught.exception.status_code)

    def test_fresh_confirmation_never_creates_or_revives_runner_authority(self):
        claimed = self.claim()
        self.assertTrue(claimed["claimed"])
        self.conn.execute("INSERT INTO business_records VALUES (1, 'Draft')")
        receipt = self.complete()
        self.conn.commit()
        self.assertEqual("user_confirmation", receipt["source_kind"])
        self.assertIsNone(receipt["attempt_id"])
        self.assertIsNone(receipt["delegation_id"])
        self.assertIsNone(receipt["fencing_token"])
        self.assertNotIn("source_session_hash", receipt)
        self.assertNotIn("authority_fingerprint", receipt)
        self.assertEqual("completed", self.conn.execute("SELECT status FROM agent_tasks WHERE id=10").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_attempts").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_delegations").fetchone()[0])
        replay = self.claim()
        self.assertFalse(replay["claimed"])
        self.assertEqual({"id": 1}, replay["operation"]["result"])
        self.assert_http(409, self.claim, params={"title": "Different"})
        self.assert_http(409, self.complete, result={"id": 2})

    def test_teacher_student_same_id_and_admin_cannot_impersonate_task_owner(self):
        self.claim()
        student = {"user": {"id": 7, "role": "student", "session_id": "student-session"},
                   "source_session_id": "student-session", "task_id": 11}
        self.assertTrue(self.claim(**student)["claimed"])
        self.assert_http(403, self.claim, task_id=11)
        self.assert_http(403, self.claim, task_id=12)
        self.assert_http(401, self.claim, source_session_id="other-session")

    def test_active_and_waiting_tasks_are_not_fresh_user_confirmation_sources(self):
        for status in ("queued", "running", "waiting_input", "cancel_requested"):
            self.conn.execute("UPDATE agent_tasks SET status=? WHERE id=10", (status,))
            self.assert_http(409, self.claim)
        for status in ("completed", "failed", "canceled"):
            self.conn.execute("UPDATE agent_tasks SET status=? WHERE id=10", (status,))
            self.assertTrue(self.claim(operation_id=f"terminal:{status}")["claimed"])

    def test_changed_session_or_authority_rolls_back_business_and_receipt(self):
        changes = [
            ("DELETE FROM user_sessions WHERE session_user_key='teacher:7'", 401),
            ("UPDATE user_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE session_user_key='teacher:7'", 401),
            ("UPDATE teachers SET is_active=0 WHERE id=7", 403),
            ("UPDATE teachers SET is_super_admin=0 WHERE id=7", 401),
            ("UPDATE teacher_organization_memberships SET is_active=0 WHERE teacher_id=7", 401),
        ]
        for sql, status in changes:
            with self.subTest(sql=sql):
                self.claim()
                self.conn.execute("INSERT INTO business_records VALUES(1, 'Draft')")
                self.conn.execute(sql)
                self.assert_http(status, self.complete)
                self.conn.rollback()
                self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
                self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])

    def test_new_session_can_replay_completed_receipt_but_cannot_reclaim_unknown_write(self):
        self.claim()
        self.conn.commit()
        self.conn.execute("UPDATE user_sessions SET session_id='new-session' WHERE session_user_key='teacher:7'")
        changed = {"user": {"role": "teacher", "id": 7, "session_id": "new-session"}, "source_session_id": "new-session"}
        self.assert_http(409, self.claim, **changed)
        self.conn.rollback()
        self.complete()
        self.conn.commit()
        self.conn.execute("UPDATE user_sessions SET session_id='new-session' WHERE session_user_key='teacher:7'")
        self.assertFalse(self.claim(**changed)["claimed"])
        self.assert_http(401, self.complete, **changed)

    def test_runner_and_user_sources_cannot_reuse_the_same_operation_key(self):
        self.conn.execute("UPDATE agent_tasks SET status='running' WHERE id=10")
        _, issued = issue_fixture(self.conn)
        claim_agent_operation(self.conn, token=issued["token"], operation_id="proposal:10:0",
            action="create_draft", params={"title": "Draft"}, required_scope="actions.execute", now=NOW)
        self.conn.execute("UPDATE agent_tasks SET status='completed' WHERE id=10")
        self.assert_http(409, self.claim)

    def test_concurrent_confirmations_write_one_business_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "confirm.sqlite")
            seed = fixture_connection(path)
            seed.execute("UPDATE agent_tasks SET status='completed'")
            seed.execute("CREATE TABLE business_records(id INTEGER PRIMARY KEY, title TEXT)")
            seed.commit()
            seed.close()
            barrier = threading.Barrier(2)

            def confirm():
                conn = sqlite3.connect(path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    result = self.claim(conn=conn)
                    if result["claimed"]:
                        conn.execute("INSERT INTO business_records VALUES(1, 'Draft')")
                        self.complete(conn=conn)
                    conn.commit()
                    return result["claimed"]
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(confirm) for _ in range(2)]
                self.assertEqual([False, True], sorted(future.result(timeout=10) for future in futures))
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
            finally:
                conn.close()

    def test_v1_schema_upgrade_preserves_historical_receipt_and_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE agent_action_executions (
                id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, actor_role TEXT NOT NULL,
                actor_id BIGINT NOT NULL, task_id BIGINT NOT NULL, attempt_id TEXT NOT NULL,
                fencing_token BIGINT NOT NULL, delegation_id TEXT NOT NULL, action TEXT NOT NULL,
                params_hash TEXT NOT NULL, resource_revision TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL, completed_at BIGINT,
                UNIQUE(actor_role,actor_id,operation_id)
            );
            INSERT INTO agent_action_executions VALUES(
                'old','old-op','teacher',7,10,'attempt',1,'delegation','write','hash','',
                'completed','{"id":42}','',1,2,2
            );
        """)
        conn.execute("BEGIN")
        ensure_agent_authority_schema(conn)
        ensure_agent_authority_schema(conn)
        row = conn.execute("SELECT * FROM agent_action_executions").fetchone()
        self.assertEqual('{"id":42}', row["result_json"])
        self.assertEqual("delegation", row["source_kind"])
        self.assertIsNone(row["source_session_hash"])
        self.assertEqual(0, {row[1]: row[3] for row in conn.execute("PRAGMA table_info(agent_action_executions)")}["attempt_id"])
        conn.rollback()
        self.assertEqual(1, {row[1]: row[3] for row in conn.execute("PRAGMA table_info(agent_action_executions)")}["attempt_id"])
        self.assertEqual('{"id":42}', conn.execute("SELECT result_json FROM agent_action_executions").fetchone()[0])


class AgentOperationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        self.engine.start()
        self.addCleanup(self.engine.stop)
        self.conn = fixture_connection()
        self.addCleanup(self.conn.close)
        self.attempt, self.issued = issue_fixture(self.conn)
        self.conn.execute("CREATE TABLE business_records (id INTEGER PRIMARY KEY, title TEXT)")
        self.conn.commit()

    def claim(self, **overrides):
        return claim_agent_operation(self.conn, **{
            "token": self.issued["token"], "operation_id": "logical-action-1",
            "action": "create_draft", "params": {"title": "Draft", "nested": {"b": 2, "a": 1}},
            "required_scope": "actions.execute", "resource_revision": "rev-1", "now": NOW, **overrides,
        })

    def complete(self, **overrides):
        return complete_agent_operation(self.conn, **{
            "token": self.issued["token"], "operation_id": "logical-action-1", "result": {"id": 1},
            "required_scope": "actions.execute", "now": NOW, **overrides,
        })

    def assert_http(self, code, callable_, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            callable_(**kwargs)
        self.assertEqual(code, caught.exception.status_code)

    def test_same_logical_action_is_claimed_once_and_result_replay_is_stable(self):
        first = self.claim()
        self.assertTrue(first["claimed"])
        self.conn.execute("INSERT INTO business_records VALUES(1, 'Draft')")
        result = self.complete()
        self.conn.commit()
        retry = self.claim(params={"nested": {"a": 1, "b": 2}, "title": "Draft"})
        self.assertFalse(retry["claimed"])
        self.assertEqual("completed", retry["operation"]["status"])
        self.assertEqual({"id": 1}, retry["operation"]["result"])
        self.assertEqual(result["id"], self.complete()["id"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
        self.assert_http(409, self.complete, result={"id": 2})

    def test_same_key_cannot_change_params_action_or_resource_version(self):
        self.claim()
        for change in [{"params": {"title": "Different"}}, {"action": "delete_draft"}, {"resource_revision": "rev-2"}]:
            with self.subTest(change=change):
                self.assert_http(409, self.claim, **change)

    def test_claim_business_and_receipt_roll_back_together(self):
        self.claim()
        self.conn.execute("INSERT INTO business_records VALUES(1, 'Draft')")
        self.complete()
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])
        self.assertTrue(self.claim()["claimed"])

    def test_receipt_rejects_cancel_and_lease_expiry_before_commit(self):
        self.claim()
        self.conn.execute("INSERT INTO business_records VALUES(1, 'Draft')")
        self.conn.execute("UPDATE agent_tasks SET cancel_requested_at='now' WHERE id=10")
        self.assert_http(401, self.complete)
        self.conn.rollback()
        self.claim()
        self.conn.execute("INSERT INTO business_records VALUES(1, 'Draft')")
        self.assert_http(409, self.complete, now=NOW + 301)
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])

    def test_old_attempt_cannot_finish_or_reclaim_a_durable_executing_operation(self):
        self.claim()
        self.conn.commit()
        new_attempt = create_task_attempt(self.conn, task_id=10, worker_id="replacement", startup_key="restart", now=NOW + 301)
        new_grant = issue_task_delegation(self.conn, task_id=10, attempt_id=new_attempt["id"], fencing_token=2, purpose="tools", scopes=["actions.execute"], source_session_id="teacher-session", now=NOW + 301)
        self.assert_http(401, self.complete, now=NOW + 301)
        replay = self.claim(token=new_grant["token"], now=NOW + 301)
        self.assertFalse(replay["claimed"])
        self.assertEqual("executing", replay["operation"]["status"])
        self.assert_http(409, self.complete, token=new_grant["token"], now=NOW + 301)

    def test_same_numeric_id_different_role_has_separate_operation_namespace(self):
        teacher = self.claim()
        _, student = issue_fixture(self.conn, task_id=11, session="student-session")
        student_claim = self.claim(token=student["token"])
        self.assertTrue(student_claim["claimed"])
        self.assertNotEqual(teacher["operation"]["id"], student_claim["operation"]["id"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])

    def test_receipt_survives_task_deletion_and_non_json_is_rejected(self):
        self.assert_http(400, self.claim, params={"invalid": float("nan")})
        self.assert_http(400, self.claim, params={"invalid": object()})
        self.claim()
        self.complete()
        self.conn.commit()
        self.conn.execute("DELETE FROM agent_tasks WHERE id=10")
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])

    def test_two_concurrent_confirmations_commit_one_business_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "operations.sqlite")
            setup = fixture_connection(path)
            _, grant = issue_fixture(setup)
            setup.execute("CREATE TABLE business_records (id INTEGER PRIMARY KEY, title TEXT)")
            setup.commit()
            setup.close()
            barrier = threading.Barrier(2)

            def confirm(_):
                conn = sqlite3.connect(path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    claim = claim_agent_operation(conn, token=grant["token"], operation_id="same-action", action="create", params={"title": "Draft"}, required_scope="actions.execute", now=NOW)
                    if claim["claimed"]:
                        conn.execute("INSERT INTO business_records(title) VALUES ('Draft')")
                        complete_agent_operation(conn, token=grant["token"], operation_id="same-action", result={"id": 1}, required_scope="actions.execute", now=NOW)
                    conn.commit()
                    return claim["claimed"]
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(confirm, [1, 2]))
            self.assertEqual([False, True], sorted(outcomes))
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM agent_action_executions WHERE status='completed'").fetchone()[0])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
