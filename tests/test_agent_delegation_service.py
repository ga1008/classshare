from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.services.agent_delegation_service import (
    create_persistent_authorization,
    create_task_attempt,
    finish_task_attempt,
    issue_task_delegation,
    renew_task_attempt,
    revoke_persistent_authorization,
    revoke_task_delegations,
    verify_task_delegation,
    verify_stored_task_delegation,
)


NOW = 2_000_000_000


def fixture_connection(path: str = ":memory:") -> sqlite3.Connection:
    """Small real SQL fixture; never read application DB or production config."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER,
            is_super_admin INTEGER, school_code TEXT, school_name TEXT, college TEXT, department TEXT);
        CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, class_id INTEGER,
            enrollment_status TEXT, school_code TEXT, school_name TEXT, college TEXT, department TEXT);
        CREATE TABLE teacher_organization_memberships (id INTEGER PRIMARY KEY,
            teacher_id INTEGER, school_code TEXT, school_name TEXT, college TEXT, department TEXT,
            is_active INTEGER, is_primary INTEGER, updated_at TEXT);
        CREATE TABLE agent_tasks (id INTEGER PRIMARY KEY, actor_role TEXT, actor_id INTEGER,
            teacher_id INTEGER, status TEXT, cancel_requested_at TEXT);
        CREATE TABLE user_sessions (session_user_key TEXT PRIMARY KEY, session_id TEXT,
            user_id TEXT, role TEXT, expires_at TEXT);
        INSERT INTO teachers VALUES (7, 'Teacher', 1, 1, 'A', 'School A', 'C', 'D');
        INSERT INTO teachers VALUES (8, 'Other teacher', 1, 0, 'B', 'School B', 'C', 'D');
        INSERT INTO students VALUES (7, 'Student', 30, 'active', 'A', 'School A', 'C', 'D');
        INSERT INTO teacher_organization_memberships VALUES (1, 7, 'A', 'School A', 'C', 'D', 1, 1, '2026-09-10');
        INSERT INTO teacher_organization_memberships VALUES (2, 7, 'B', 'School B', 'C', 'D', 1, 0, '2026-09-10');
        INSERT INTO agent_tasks VALUES (10, 'teacher', 7, 7, 'running', NULL);
        INSERT INTO agent_tasks VALUES (11, 'student', 7, NULL, 'running', NULL);
        INSERT INTO agent_tasks VALUES (12, 'teacher', 8, 8, 'running', NULL);
    """)
    expires = datetime.fromtimestamp(NOW + 86400, timezone.utc).isoformat()
    conn.executemany("INSERT INTO user_sessions VALUES (?, ?, ?, ?, ?)", [
        ("teacher:7", "teacher-session", "7", "teacher", expires),
        ("student:7", "student-session", "7", "student", expires),
        ("teacher:8", "other-session", "8", "teacher", expires),
    ])
    ensure_agent_authority_schema(conn)
    conn.commit()
    return conn


def issue_fixture(conn, *, task_id=10, session="teacher-session", purpose="tools", scopes=None, lease_seconds=300):
    attempt = create_task_attempt(conn, task_id=task_id, worker_id="fixture-worker", startup_key=f"start-{task_id}", lease_seconds=lease_seconds, now=NOW)
    grant = issue_task_delegation(conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"], purpose=purpose, scopes=scopes or ["resources.read", "actions.execute"], source_session_id=session, ttl_seconds=600, now=NOW)
    return attempt, grant


class AgentDelegationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        self.engine.start()
        self.addCleanup(self.engine.stop)
        self.conn = fixture_connection()
        self.addCleanup(self.conn.close)

    def assert_http(self, code, callable_, *args, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(code, caught.exception.status_code)

    def test_opaque_token_and_source_session_are_only_stored_as_hashes(self):
        attempt, issued = issue_fixture(self.conn)
        verified = verify_task_delegation(self.conn, issued["token"], purpose="tools", required_scope="resources.read", now=NOW)
        self.assertEqual("teacher:7", verified.actor.key)
        self.assertEqual(attempt["id"], verified.attempt["id"])
        stored = json.dumps(dict(self.conn.execute("SELECT * FROM agent_task_delegations").fetchone()))
        self.assertNotIn(issued["token"], stored)
        self.assertNotIn("teacher-session", stored)
        self.assertEqual(64, len(verified.delegation["token_hash"]))
        self.assertTrue(self.conn.in_transaction)

    def test_source_session_hash_can_be_carried_by_trusted_task_columns(self):
        attempt = create_task_attempt(self.conn, task_id=10, worker_id="w", startup_key="s", now=NOW)
        issued = issue_task_delegation(self.conn, task_id=10, attempt_id=attempt["id"], fencing_token=1, purpose="tools", scopes=["resources.read"], source_session_hash=hashlib.sha256(b"teacher-session").hexdigest(), source_session_key="teacher:7", now=NOW)
        self.assertEqual("teacher:7", verify_task_delegation(self.conn, issued["token"], purpose="tools", now=NOW).actor.key)
        self.assert_http(401, issue_task_delegation, self.conn, task_id=10, attempt_id=attempt["id"], fencing_token=1, purpose="tools", scopes=["resources.read"], source_session_hash=hashlib.sha256(b"teacher-session").hexdigest(), source_session_key="student:7", now=NOW)

    def test_internal_stored_reference_reuses_live_checks_but_is_not_a_bearer_token(self):
        _, issued = issue_fixture(self.conn)
        grant = verify_stored_task_delegation(self.conn, issued["id"], purpose="tools", required_scope="resources.read", lock_task=True, now=NOW)
        self.assertEqual("teacher:7", grant.actor.key)
        self.assert_http(401, verify_task_delegation, self.conn, issued["id"], purpose="tools", now=NOW)
        self.assert_http(401, verify_stored_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
        self.assert_http(401, verify_stored_task_delegation, self.conn, issued["id"], purpose="model", now=NOW)
        self.assert_http(403, verify_stored_task_delegation, self.conn, issued["id"], purpose="tools", required_scope="absent", now=NOW)
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        self.assert_http(401, verify_stored_task_delegation, self.conn, issued["id"], purpose="tools", now=NOW)

    def test_purpose_scope_expiry_and_revoke_are_enforced(self):
        _, issued = issue_fixture(self.conn, purpose="model", scopes=["model.generate"])
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
        self.assert_http(403, verify_task_delegation, self.conn, issued["token"], purpose="model", required_scope="actions.execute", now=NOW)
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="model", now=NOW + 600)
        self.assertEqual(1, revoke_task_delegations(self.conn, task_id=10, now=NOW))
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="model", now=NOW)

    def test_disabled_admin_demoted_and_membership_revoked_invalidate_live_grant(self):
        _, issued = issue_fixture(self.conn)
        for mutation, restore, code in [
            ("UPDATE teachers SET is_active=0 WHERE id=7", "UPDATE teachers SET is_active=1 WHERE id=7", 403),
            ("UPDATE teachers SET is_super_admin=0 WHERE id=7", "UPDATE teachers SET is_super_admin=1 WHERE id=7", 401),
            ("UPDATE teacher_organization_memberships SET is_active=0 WHERE id=2", "UPDATE teacher_organization_memberships SET is_active=1 WHERE id=2", 401),
            ("UPDATE teacher_organization_memberships SET is_active=0", "UPDATE teacher_organization_memberships SET is_active=1", 401),
        ]:
            with self.subTest(mutation=mutation):
                self.conn.execute(mutation)
                self.assert_http(code, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
                self.conn.execute(restore)

    def test_role_collision_and_student_class_or_status_change_are_enforced(self):
        _, teacher = issue_fixture(self.conn)
        _, student = issue_fixture(self.conn, task_id=11, session="student-session")
        self.assertEqual("teacher:7", verify_task_delegation(self.conn, teacher["token"], purpose="tools", now=NOW).actor.key)
        self.assertEqual("student:7", verify_task_delegation(self.conn, student["token"], purpose="tools", now=NOW).actor.key)
        self.conn.execute("UPDATE students SET class_id=31 WHERE id=7")
        self.assert_http(401, verify_task_delegation, self.conn, student["token"], purpose="tools", now=NOW)
        self.conn.execute("UPDATE students SET class_id=30, enrollment_status='suspended' WHERE id=7")
        self.assert_http(403, verify_task_delegation, self.conn, student["token"], purpose="tools", now=NOW)
        self.assertEqual("teacher:7", verify_task_delegation(self.conn, teacher["token"], purpose="tools", now=NOW).actor.key)

    def test_current_session_must_match_role_id_hash_and_expiry(self):
        _, issued = issue_fixture(self.conn)
        for sql in [
            "UPDATE user_sessions SET session_id='replacement' WHERE session_user_key='teacher:7'",
            "UPDATE user_sessions SET role='student' WHERE session_user_key='teacher:7'",
            "UPDATE user_sessions SET user_id='8' WHERE session_user_key='teacher:7'",
            "UPDATE user_sessions SET expires_at='invalid' WHERE session_user_key='teacher:7'",
            "DELETE FROM user_sessions WHERE session_user_key='teacher:7'",
        ]:
            self.conn.execute("SAVEPOINT session_change")
            self.conn.execute(sql)
            self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
            self.conn.execute("ROLLBACK TO SAVEPOINT session_change")
            self.conn.execute("RELEASE SAVEPOINT session_change")

    def test_waiting_cancel_finished_and_deleted_tasks_reject_old_tokens(self):
        _, issued = issue_fixture(self.conn)
        for status in ["queued", "waiting_input", "waiting_confirmation", "completed", "failed", "canceled"]:
            self.conn.execute("UPDATE agent_tasks SET status=? WHERE id=10", (status,))
            self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
        self.conn.execute("UPDATE agent_tasks SET status='running',cancel_requested_at='now' WHERE id=10")
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
        self.conn.execute("DELETE FROM agent_tasks WHERE id=10")
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_attempts").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_delegations").fetchone()[0])

    def test_expired_attempt_reclaim_fences_prior_worker_and_preserves_startup_idempotency(self):
        first, issued = issue_fixture(self.conn, lease_seconds=10)
        same = create_task_attempt(self.conn, task_id=10, worker_id="fixture-worker", startup_key="start-10", now=NOW + 1)
        self.assertEqual(first["id"], same["id"])
        self.assert_http(409, create_task_attempt, self.conn, task_id=10, worker_id="other", startup_key="second", now=NOW + 1)
        second = create_task_attempt(self.conn, task_id=10, worker_id="other", startup_key="second", now=NOW + 10)
        self.assertEqual(2, second["fencing_token"])
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW + 10)
        self.assert_http(409, renew_task_attempt, self.conn, task_id=10, attempt_id=first["id"], fencing_token=1, worker_id="fixture-worker", now=NOW + 10)
        self.assert_http(409, finish_task_attempt, self.conn, attempt_id=first["id"], fencing_token=1, status="completed", now=NOW + 10)
        self.assertEqual("running", self.conn.execute("SELECT status FROM agent_task_attempts WHERE id=?", (second["id"],)).fetchone()[0])

    def test_persistent_authorization_outlives_session_but_revoke_stops_derived_grants(self):
        persistent = create_persistent_authorization(self.conn, actor_role="teacher", actor_id=7, source_session_id="teacher-session", scopes=["resources.read"], intent_reference="subscription:42", ttl_seconds=3600, now=NOW)
        attempt = create_task_attempt(self.conn, task_id=10, worker_id="w", startup_key="s", now=NOW)
        issued = issue_task_delegation(self.conn, task_id=10, attempt_id=attempt["id"], fencing_token=1, purpose="tools", scopes=["resources.read"], persistent_authorization_id=persistent["id"], now=NOW)
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        verify_task_delegation(self.conn, issued["token"], purpose="tools", now=NOW)
        self.assert_http(403, revoke_persistent_authorization, self.conn, authorization_id=persistent["id"], actor_role="teacher", actor_id=8, now=NOW)
        revoke_persistent_authorization(self.conn, authorization_id=persistent["id"], actor_role="teacher", actor_id=7, now=NOW)
        self.assert_http(401, verify_task_delegation, self.conn, issued["token"], purpose="tools", now=NOW)
        self.assertEqual("revoked", self.conn.execute("SELECT status FROM agent_task_delegations").fetchone()[0])

    def test_issue_cannot_widen_persistent_scope_or_borrow_other_identity(self):
        persistent = create_persistent_authorization(self.conn, actor_role="teacher", actor_id=7, source_session_id="teacher-session", scopes=["resources.read"], intent_reference="subscription:42", ttl_seconds=3600, now=NOW)
        attempt = create_task_attempt(self.conn, task_id=10, worker_id="w", startup_key="s", now=NOW)
        options = dict(task_id=10, attempt_id=attempt["id"], fencing_token=1, purpose="tools", scopes=["actions.execute"], persistent_authorization_id=persistent["id"], now=NOW)
        self.assert_http(403, issue_task_delegation, self.conn, **options)
        other = create_task_attempt(self.conn, task_id=11, worker_id="w", startup_key="s", now=NOW)
        self.assert_http(401, issue_task_delegation, self.conn, **{**options, "task_id": 11, "attempt_id": other["id"], "scopes": ["resources.read"]})

    def test_schema_and_service_do_not_commit_caller_transaction(self):
        issue_fixture(self.conn)
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_attempts").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_delegations").fetchone()[0])
        ensure_agent_authority_schema(self.conn)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_attempts").fetchone()[0])

    def test_two_reclaimers_get_one_current_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "leases.sqlite")
            setup = fixture_connection(path)
            first = create_task_attempt(setup, task_id=10, worker_id="first", startup_key="first", lease_seconds=1, now=NOW)
            setup.commit()
            setup.close()
            barrier = threading.Barrier(2)

            def reclaim(index):
                conn = sqlite3.connect(path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    attempt = create_task_attempt(conn, task_id=10, worker_id=f"w{index}", startup_key=f"s{index}", now=NOW + 2)
                    conn.commit()
                    return attempt["fencing_token"]
                except HTTPException as exc:
                    conn.rollback()
                    return exc.status_code
                finally:
                    conn.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reclaim, [1, 2]))
            self.assertEqual([2, 409], sorted(outcomes))
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM agent_task_attempts WHERE status='running'").fetchone()[0])
                self.assertEqual("superseded", conn.execute("SELECT status FROM agent_task_attempts WHERE id=?", (first["id"],)).fetchone()[0])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
