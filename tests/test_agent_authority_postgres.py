"""Opt-in real PostgreSQL tests, restricted to a new synthetic database.

Use the same explicit loopback cluster variables as the native migration suite.
No production DSN, dump, keys, app lifespan or background workers are consulted.
"""
from concurrent.futures import ThreadPoolExecutor
import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.db.schema_agent_model import ensure_agent_model_schema
from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.services.agent_delegation_service import (
    create_task_attempt, issue_task_delegation, verify_task_delegation,
)
from classroom_app.services.agent_operation_service import (
    claim_agent_operation, complete_agent_operation, claim_user_agent_operation, complete_user_agent_operation,
)
from classroom_app.services import agent_request_budget_service as budgets
from classroom_app.services import agent_key_service as keys
from tools.assessment_postgres_rehearsal import connect_offline


NOW = 2_000_000_000


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires an explicitly created offline PostgreSQL cluster")
class AgentAuthorityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_agent_" + uuid.uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        # Exclusive CREATE only; an existing database is never reused/dropped.
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def connection(self):
        conn = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        conn.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(conn)

    def setUp(self):
        self.engine = patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres")
        self.org_engine = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="postgres")
        self.engine.start()
        self.org_engine.start()
        self.addCleanup(self.engine.stop)
        self.addCleanup(self.org_engine.stop)
        self.enc = patch.object(keys, "encrypt_secret", side_effect=lambda value: "synthetic:" + value)
        self.dec = patch.object(keys, "decrypt_secret", side_effect=lambda value: value.removeprefix("synthetic:") if value else "")
        self.enc.start()
        self.dec.start()
        self.addCleanup(self.enc.stop)
        self.addCleanup(self.dec.stop)
        self.conn = self.connection()
        self.addCleanup(self.conn.close)
        if self.conn.execute("SELECT current_database()").fetchone()[0] != self.database:
            raise RuntimeError("Synthetic database identity mismatch")
        self.conn.execute("DROP SCHEMA public CASCADE")
        self.conn.execute("CREATE SCHEMA public")
        self.conn.execute("""
            CREATE TABLE teachers(id BIGINT PRIMARY KEY, name TEXT, is_active INTEGER, is_super_admin INTEGER,
                school_code TEXT, school_name TEXT, college TEXT, department TEXT);
            CREATE TABLE students(id BIGINT PRIMARY KEY, name TEXT, class_id BIGINT, enrollment_status TEXT,
                school_code TEXT, school_name TEXT, college TEXT, department TEXT);
            CREATE TABLE teacher_organization_memberships(id BIGINT PRIMARY KEY, teacher_id BIGINT,
                school_code TEXT, school_name TEXT, college TEXT, department TEXT,
                is_active INTEGER, is_primary INTEGER, updated_at TEXT);
            CREATE TABLE agent_tasks(id BIGINT PRIMARY KEY, actor_role TEXT, actor_id BIGINT,
                teacher_id BIGINT REFERENCES teachers(id), status TEXT, cancel_requested_at TEXT);
            CREATE TABLE user_sessions(session_user_key TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, role TEXT, expires_at TEXT);
            CREATE TABLE business_records(id BIGSERIAL PRIMARY KEY, title TEXT);
            INSERT INTO teachers VALUES(7,'Teacher',1,1,'A','School A','C','D'),(8,'Other',1,0,'B','School B','C','D');
            INSERT INTO students VALUES(7,'Student',30,'active','A','School A','C','D');
            INSERT INTO teacher_organization_memberships VALUES(1,7,'A','School A','C','D',1,1,'2026-09-10'),
                (2,7,'B','School B','C','D',1,0,'2026-09-10');
            INSERT INTO agent_tasks VALUES(10,'teacher',7,7,'running',NULL),(11,'student',7,NULL,'running',NULL);
            CREATE TABLE agent_runtime_api_keys(
                id BIGSERIAL PRIMARY KEY, provider TEXT NOT NULL, key_label TEXT NOT NULL,
                key_fingerprint TEXT NOT NULL UNIQUE, key_encrypted TEXT NOT NULL, key_suffix TEXT NOT NULL,
                base_url TEXT NOT NULL, model TEXT NOT NULL, enabled INTEGER NOT NULL, is_active INTEGER NOT NULL,
                created_by_teacher_id BIGINT REFERENCES teachers(id), last_test_status TEXT NOT NULL DEFAULT 'unchecked',
                last_test_message TEXT NOT NULL DEFAULT '', last_test_usage_json TEXT NOT NULL DEFAULT '{}',
                last_test_at TEXT, last_used_at TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE agent_runtime_key_checks(id BIGSERIAL PRIMARY KEY, key_id BIGINT REFERENCES agent_runtime_api_keys(id) ON DELETE CASCADE,
                status TEXT, message TEXT, response_ms INTEGER, usage_json TEXT, checked_by_teacher_id BIGINT, created_at TEXT);
        """)
        expires = datetime.fromtimestamp(NOW + 86400, timezone.utc).isoformat()
        for role, session in (("teacher", "teacher-session"), ("student", "student-session")):
            self.conn.execute("INSERT INTO user_sessions VALUES(?, ?, '7', ?, ?)", (role + ":7", session, role, expires))
        ensure_agent_authority_schema(self.conn)
        ensure_agent_model_schema(self.conn)
        ensure_agent_request_budget_schema(self.conn)
        self.attempt, self.issued = self.issue()
        self.conn.commit()

    def issue(self, *, task_id=10, session="teacher-session"):
        attempt = create_task_attempt(self.conn, task_id=task_id, worker_id="worker", startup_key="start-" + str(task_id), lease_seconds=300, now=NOW)
        issued = issue_task_delegation(self.conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
            purpose="tools", scopes=["actions.execute", "resources.read"], source_session_id=session, now=NOW)
        return attempt, issued

    def claim(self, conn=None):
        return claim_agent_operation(conn or self.conn, token=self.issued["token"], operation_id="logical-1",
            action="create_draft", params={"title": "Draft"}, required_scope="actions.execute", now=NOW)

    def _assert_native_waiting(self, future):
        waiting = False
        with self.connection() as observer:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not future.done():
                waiting = bool(observer.execute("""SELECT COUNT(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
                    WHERE a.datname=? AND NOT l.granted""", (self.database,)).fetchone()[0])
                if waiting: break
                time.sleep(.01)
        self.assertTrue(waiting, 'The competing transaction must wait on a real PostgreSQL lock')

    def _persistent(self, conn, *, role='teacher', session=None):
        from classroom_app.services.agent_delegation_service import create_persistent_authorization
        return create_persistent_authorization(conn, actor_role=role, actor_id=7,
            source_session_id=session or role+'-session', scopes=['platform:read'], intent_reference='consented-native-fixture', ttl_seconds=600, now=NOW)

    def test_native_credential_change_revokes_persistent_issuance_that_committed_first(self):
        from classroom_app.services.account_credentials_service import prepare_credentials_change, credentials_changed
        authority=self._persistent(self.conn)
        def change():
            with self.connection() as conn:
                prepare_credentials_change(conn, role='teacher', user_id=7)
                result=credentials_changed(conn, role='teacher', user_id=7)
                conn.commit()
                return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(change)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally: self.conn.rollback()
            self.assertEqual(1, future.result(timeout=5)['persistent_authorizations_revoked'])
        self.assertEqual('revoked',self.conn.execute('SELECT status FROM agent_persistent_authorizations WHERE id=?',(authority['id'],)).fetchone()[0])

    def test_native_new_consent_after_self_password_change_uses_preserved_live_session(self):
        from classroom_app.services.account_credentials_service import prepare_credentials_change, credentials_changed
        prepare_credentials_change(self.conn, role='teacher', user_id=7)
        credentials_changed(self.conn, role='teacher', user_id=7)
        def issue():
            with self.connection() as conn:
                result=self._persistent(conn)
                conn.commit()
                return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(issue)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally: self.conn.rollback()
            authority=future.result(timeout=5)
        self.assertEqual('active',authority['status'])
        self.assertEqual('revoked',self.conn.execute('SELECT status FROM agent_task_delegations WHERE id=?',(self.issued['id'],)).fetchone()[0])

    def test_native_persistent_issuance_rechecks_session_after_reset_commit(self):
        from classroom_app.services.account_credentials_service import prepare_credentials_change, credentials_changed
        prepare_credentials_change(self.conn, role='teacher', user_id=7)
        credentials_changed(self.conn, role='teacher', user_id=7)
        def issue():
            with self.connection() as conn:
                try:
                    self._persistent(conn)
                    conn.commit()
                    return 'unexpected authorization'
                except HTTPException as error:
                    conn.rollback()
                    return error.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(issue)
            try:
                self._assert_native_waiting(future)
                self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
                self.conn.commit()
            finally: self.conn.rollback()
            self.assertEqual(401,future.result(timeout=5))
        self.assertEqual(0,self.conn.execute('SELECT count(*) FROM agent_persistent_authorizations').fetchone()[0])

    def test_native_task_started_after_credential_task_snapshot_cannot_issue_across_reset(self):
        from classroom_app.services.account_credentials_service import prepare_credentials_change, credentials_changed
        prepare_credentials_change(self.conn, role='teacher', user_id=7)
        credentials_changed(self.conn, role='teacher', user_id=7)
        started=threading.Event()
        def issue():
            with self.connection() as conn:
                conn.execute("INSERT INTO agent_tasks VALUES(12,'teacher',7,7,'running',NULL)")
                attempt=create_task_attempt(conn,task_id=12,worker_id='new-after-snapshot',startup_key='new-after-snapshot',lease_seconds=300,now=NOW)
                conn.commit()
                started.set()
                try:
                    issue_task_delegation(conn,task_id=12,attempt_id=attempt['id'],fencing_token=attempt['fencing_token'],
                        purpose='tools',scopes=['platform:read'],source_session_id='teacher-session',now=NOW)
                    conn.commit()
                    return 'unexpected authorization'
                except HTTPException as error:
                    conn.rollback()
                    return error.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(issue)
            try:
                self.assertTrue(started.wait(timeout=3))
                self._assert_native_waiting(future)
                self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
                self.conn.commit()
            finally: self.conn.rollback()
            self.assertEqual(401,future.result(timeout=5))
        self.assertEqual(0,self.conn.execute('SELECT count(*) FROM agent_task_delegations WHERE task_id=12').fetchone()[0])

    def test_native_credential_mutex_distinguishes_same_numbered_roles(self):
        from classroom_app.services.account_credentials_service import prepare_credentials_change
        prepare_credentials_change(self.conn, role='teacher', user_id=7)
        def issue():
            with self.connection() as conn:
                authority=self._persistent(conn,role='student')
                conn.commit()
                return authority
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(issue)
            try: self.assertEqual('active',future.result(timeout=3)['status'])
            finally: self.conn.rollback()

    def _prepare_identity_management(self):
        from tests.test_agent_identity_management import extend_account_fields
        extend_account_fields(self.conn, engine="postgres")
        self.conn.execute("UPDATE teachers SET email=CAST(id AS TEXT)||'@example.test',is_super_admin=1")
        self.conn.execute("UPDATE agent_task_delegations SET scopes_json='[\"platform:read\",\"platform:write\"]'")
        self.conn.commit()

    def test_native_self_demotion_preserves_atomic_receipt_and_revokes_old_source(self):
        from classroom_app.services import agent_identity_management_adapter as identity, teacher_account_service as accounts
        self._prepare_identity_management()
        params = {"teacher_id": 7, "expected_revision": identity._revision(accounts.get_teacher_account(self.conn, 7))}
        result = identity.dispatch_identity_write(self.conn, self.issued["token"], "self-demotion", "revoke_teacher_super_admin", params)
        self.conn.commit()
        self.assertTrue(result["result"]["agent_stop_required"])
        self.assertEqual(0, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual("completed", self.conn.execute("SELECT status FROM agent_action_executions").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:7'").fetchone()[0])

    def test_native_authority_change_and_receipt_roll_back_as_one_transaction(self):
        from classroom_app.services import agent_identity_management_adapter as identity, teacher_account_service as accounts
        self._prepare_identity_management()
        params = {"teacher_id": 7, "expected_revision": identity._revision(accounts.get_teacher_account(self.conn, 7))}
        identity.dispatch_identity_write(self.conn, self.issued["token"], "rollback-demotion", "revoke_teacher_super_admin", params)
        self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT is_super_admin FROM teachers WHERE id=7").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])
        verify_task_delegation(self.conn, self.issued["token"], purpose="tools", now=NOW)

    def test_native_terminal_confirmation_self_demotion_never_restores_runner(self):
        from classroom_app.services import agent_identity_management_adapter as identity, teacher_account_service as accounts
        self._prepare_identity_management()
        self.conn.execute("UPDATE agent_tasks SET status='failed' WHERE id=10")
        self.conn.execute("UPDATE agent_task_attempts SET status='failed',lease_expires_at=0")
        self.conn.execute("UPDATE agent_task_delegations SET status='revoked'")
        self.conn.commit()
        params = {"teacher_id": 7, "expected_revision": identity._revision(accounts.get_teacher_account(self.conn, 7))}
        result = identity.dispatch_user_identity_write(self.conn,
            user={"role": "teacher", "id": "7", "session_id": "teacher-session"}, source_session_id="teacher-session",
            task_id=10, operation_id="proposal:10:0", action="revoke_teacher_super_admin", params=params)
        self.conn.commit()
        self.assertTrue(result["result"]["agent_stop_required"])
        row = self.conn.execute("SELECT * FROM agent_action_executions").fetchone()
        self.assertEqual("user_confirmation", row["source_kind"])
        self.assertEqual("completed", row["status"])
        self.assertIsNone(row["attempt_id"])
        self.assertIsNone(row["delegation_id"])
        self.assertEqual("failed", self.conn.execute("SELECT status FROM agent_task_attempts").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='teacher:7'").fetchone()[0])

    def test_native_web_and_agent_account_lock_prevents_removing_both_last_admins(self):
        from classroom_app.services import teacher_account_service as accounts
        self._prepare_identity_management()
        def revoke(conn, index):
            try:
                accounts.revoke_teacher_super_admin(conn, teacher_id=7 + index)
                return "done"
            except ValueError:
                return "last_admin"
        self.assertCountEqual(["done", "last_admin"], self.concurrent(revoke))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM teachers WHERE is_active=1 AND is_super_admin=1").fetchone()[0])

    def _prepare_http_observations(self):
        from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
        from classroom_app.services import agent_platform_request_service as requests
        from classroom_app.services.agent_platform_request_registry import CAPABILITIES, arguments
        ensure_agent_platform_requests_schema(self.conn)
        self.conn.execute("UPDATE agent_task_delegations SET scopes_json='[\"platform:read\",\"platform:write\"]'")
        self.conn.commit()
        guard = patch.object(requests, "get_db_connection", self.connection)
        guard.start()
        self.addCleanup(guard.stop)
        operation = next(item for item in CAPABILITIES if item.key == "http.blog.bookmark.toggle")
        path, query, body, normalized = arguments(operation, path_params={"post_id": 42})
        return requests, operation, (path, query, body, normalized)

    def test_native_http_cross_task_operation_collision_is_409_not_an_unhandled_constraint(self):
        requests, operation, args = self._prepare_http_observations()
        self.conn.execute("INSERT INTO agent_tasks VALUES(12,'teacher',7,7,'running',NULL)")
        attempt = create_task_attempt(self.conn, task_id=12, worker_id="second-task", startup_key="second-task", now=NOW)
        second = issue_task_delegation(self.conn, task_id=12, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
            purpose="tools", scopes=["platform:write"], source_session_id="teacher-session", now=NOW)["token"]
        self.conn.commit()
        identifier = str(uuid.uuid4())
        def admit(unused, index):
            try:
                claim, grant, prior = requests._admit((self.issued["token"], second)[index], operation, identifier, *args)
                return "admitted" if claim else "replayed"
            except HTTPException as exc:
                return exc.status_code
        self.assertCountEqual(["admitted", 409], self.concurrent(admit))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_platform_requests").fetchone()[0])

    def test_native_http_unresolved_intent_cannot_be_reissued_with_new_uuid(self):
        requests, operation, args = self._prepare_http_observations()
        def admit(unused, index):
            try:
                requests._admit(self.issued["token"], operation, str(uuid.uuid4()), *args)
                return "admitted"
            except HTTPException as exc:
                return exc.status_code
        self.assertCountEqual(["admitted", 409], self.concurrent(admit))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_platform_requests").fetchone()[0])

    def test_native_http_host_can_settle_after_revocation_but_cannot_deliver_or_overwrite(self):
        requests, operation, args = self._prepare_http_observations()
        claim, grant, prior = requests._admit(self.issued["token"], operation, str(uuid.uuid4()), *args)
        requests._mark_executing(claim, self.issued["token"], "platform:write")
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        self.conn.execute("UPDATE agent_tasks SET status='canceled' WHERE id=10")
        requests.mark_abandoned_platform_requests_uncertain(self.conn, task_id=10, attempt_id=grant.attempt["id"])
        self.conn.commit()
        with self.assertRaises(HTTPException):
            requests._settle(requests._Settlement(claim.request_id, "forged"), "observed_http_result", {})
        result = requests._settle(claim, "observed_http_result", {"http_status": 200, "data": {"private": "synthetic response"}})
        self.assertFalse(result["verified_business"])
        with self.assertRaises(HTTPException):
            requests._authorize_return(self.issued["token"], grant, "platform:write")
        with self.assertRaises(HTTPException):
            requests._settle(claim, "uncertain", {})
        requests._uncertain(claim, "late timeout cannot overwrite settlement")
        row = self.conn.execute("SELECT status,result_json FROM agent_platform_requests").fetchone()
        self.assertEqual("observed_http_result", row["status"])
        self.assertIn("synthetic response", row["result_json"])

    def test_native_two_manual_declarations_preserve_one_audit_and_late_host_fact(self):
        from classroom_app.services import agent_platform_request_reconciliation as reconciliation
        requests, operation, args = self._prepare_http_observations()
        self.conn.execute("CREATE TABLE agent_task_events(id BIGSERIAL PRIMARY KEY,task_id BIGINT,event_type TEXT,message TEXT,detail_json TEXT,created_at TEXT)")
        self.conn.commit()
        claim, grant, prior = requests._admit(self.issued["token"], operation, str(uuid.uuid4()), *args)
        requests._mark_executing(claim, self.issued["token"], "platform:write")
        requests._uncertain(claim, "synthetic lost response")
        requests._finish_host_execution(claim)
        self.conn.execute("UPDATE agent_tasks SET status='failed' WHERE id=10")
        self.conn.commit()
        source = {"user": {"role": "teacher", "id": 7, "session_id": "teacher-session"}, "source_session_id": "teacher-session",
                  "task_id": 10, "request_id": claim.request_id}
        current = reconciliation.get_user_platform_request(self.conn, **source)
        self.conn.commit()
        def declare(conn, index):
            return reconciliation.reconcile_user_platform_request(conn, **source, resolution="not_occurred", note="Checked original business page",
                expected_revision=current["revision"])["replayed"]
        self.assertCountEqual([True, False], self.concurrent(declare))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_events WHERE event_type='platform_request_reconciled'").fetchone()[0])
        self.conn.commit()
        requests._settle(claim, "observed_http_result", {"http_status": 200, "data": {"bookmarked": True}})
        row = reconciliation.get_user_platform_request(self.conn, **source)
        self.assertEqual("not_occurred", row["reconciliation"]["resolution"])
        self.assertTrue(row["reconciliation"]["late_http_observation"])
        self.assertEqual("observed_http_result", row["observation"]["status"])
        self.assertFalse(row["observation"]["verified_business"])

    def test_native_admission_waits_for_manual_source_lock_then_rechecks_fresh_authority(self):
        from classroom_app.services import agent_platform_request_reconciliation as reconciliation
        requests, operation, args = self._prepare_http_observations()
        self.conn.execute("CREATE TABLE agent_task_events(id BIGSERIAL PRIMARY KEY,task_id BIGINT,event_type TEXT,message TEXT,detail_json TEXT,created_at TEXT)")
        self.conn.execute("INSERT INTO agent_tasks VALUES(12,'teacher',7,7,'running',NULL)")
        attempt = create_task_attempt(self.conn, task_id=12, worker_id="new-task", startup_key="new-task", lease_seconds=300)
        token = issue_task_delegation(self.conn, task_id=12, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
            purpose="tools", scopes=["platform:write"], source_session_id="teacher-session")["token"]
        self.conn.commit()
        claim, grant, _ = requests._admit(self.issued["token"], operation, str(uuid.uuid4()), *args)
        requests._uncertain(claim, "synthetic response lost")
        requests._finish_host_execution(claim)
        self.conn.execute("UPDATE agent_tasks SET status='failed' WHERE id=10")
        self.conn.commit()
        source = {"user": {"role": "teacher", "id": 7}, "source_session_id": "teacher-session", "task_id": 10, "request_id": claim.request_id}
        view = reconciliation.get_user_platform_request(self.conn, **source)
        self.conn.commit()
        self.conn.execute("UPDATE user_sessions SET expires_at=expires_at WHERE session_user_key='teacher:7'")
        def admit():
            try:
                requests._admit(token, operation, str(uuid.uuid4()), *args)
                return "unexpected admission"
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(admit)
            waiting = False
            try:
                with self.connection() as observer:
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and not future.done():
                        waiting = bool(observer.execute("""SELECT COUNT(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
                            WHERE a.datname=? AND NOT l.granted""", (self.database,)).fetchone()[0])
                        if waiting:
                            break
                        time.sleep(0.01)
                self.assertTrue(waiting, "Admission must share the actor/session lock with manual reconciliation")
                reconciliation.reconcile_user_platform_request(self.conn, **source, resolution="not_occurred", note="Checked original page",
                    expected_revision=view["revision"])
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409, future.result(timeout=10))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_platform_requests").fetchone()[0])

    def _prepare_native_task_history(self):
        from classroom_app.db import schema_classroom_activity, schema_agent_ext
        from classroom_app.services import agent_task_service as tasks
        # Use the real table declarations, translating only SQLite's identity
        # spelling for this fresh, explicitly owned PostgreSQL fixture.
        tree = ast.parse(Path(schema_classroom_activity.__file__).read_text(encoding="utf-8"))
        declarations = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.conn.execute("DROP TABLE agent_tasks")
        for table in ("agent_tasks", "agent_task_events", "agent_task_composers"):
            matches = [sql for sql in declarations if re.sub(r"\s+", " ", sql).strip().startswith("CREATE TABLE IF NOT EXISTS " + table + " (")]
            self.assertEqual(1, len(matches), table)
            self.conn.execute(matches[0].replace("id INTEGER PRIMARY KEY AUTOINCREMENT", "id BIGSERIAL PRIMARY KEY"))
        for target, value in ((tasks, "get_configured_db_engine"), (schema_agent_ext, "get_configured_db_engine")):
            guard = patch.object(target, value, return_value="postgres")
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(schema_agent_ext, "_SCHEMA_READY", False)
        guard.start()
        self.addCleanup(guard.stop)
        schema_agent_ext.ensure_agent_task_extension_schema(self.conn, force=True, engine="postgres")
        self.conn.execute("""INSERT INTO agent_tasks(id,task_uuid,teacher_id,actor_role,actor_id,teacher_name,task_type,title,private_instruction,status)
            VALUES(10,'synthetic-history-parent',7,'teacher',7,'Teacher','general_teaching_task','Parent','Prepare teaching material','failed')""")
        self.conn.execute("SELECT setval(pg_get_serial_sequence('agent_tasks','id'),10,true)")
        self.conn.commit()
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        for target, options in (("build_teacher_page_context", {"return_value": {}}),
                                ("_remove_task_workspace", {"return_value": False}),
                                ("AGENT_TASK_WORKSPACE_ROOT", {"new": Path(workspace.name)})):
            guard = patch.object(tasks, target, **options)
            guard.start()
            self.addCleanup(guard.stop)
        return tasks

    def _history_race(self, *, creator_first, bulk=False):
        tasks = self._prepare_native_task_history()
        reached, release = threading.Event(), threading.Event()
        interception = "create_agent_task" if creator_first else "_owned_task_subtree_rows"
        original = getattr(tasks, interception)
        def pause(*args, **kwargs):
            reached.set()
            if not release.wait(timeout=10):
                raise RuntimeError("Synthetic history race did not release")
            return original(*args, **kwargs)
        def follow():
            with self.connection() as conn:
                try:
                    return tasks.create_follow_up_task(conn, {"id": 7, "role": "teacher", "name": "Teacher"}, 10,
                        "请继续完善教学材料。", source_session_id="teacher-session")["id"]
                except HTTPException as error:
                    return error.status_code
        def delete():
            with self.connection() as conn:
                try:
                    if bulk:
                        return tasks.delete_agent_task_history(conn, teacher_id=7, actor_role="teacher")["deleted_count"]
                    return tasks.delete_agent_task(conn, 10, teacher_id=7, actor_role="teacher")["deleted_count"]
                except HTTPException as error:
                    return error.status_code
        with patch.object(tasks, interception, side_effect=pause), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(follow if creator_first else delete)
            try:
                self.assertTrue(reached.wait(timeout=5))
                second = pool.submit(delete if creator_first else follow)
                waiting = False
                with self.connection() as observer:
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and not second.done():
                        waiting = bool(observer.execute("""SELECT COUNT(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
                            WHERE a.datname=? AND NOT l.granted""", (self.database,)).fetchone()[0])
                        if waiting:
                            break
                        time.sleep(0.01)
                self.assertTrue(waiting, "History creation/deletion must serialize before reading the parent")
            finally:
                release.set()
            values = first.result(timeout=10), second.result(timeout=10)
        rows = self.conn.execute("SELECT id,parent_task_id,status FROM agent_tasks ORDER BY id").fetchall()
        self.assertEqual(0, self.conn.execute("""SELECT COUNT(*) FROM agent_tasks t LEFT JOIN agent_tasks p ON p.id=t.parent_task_id
            WHERE t.parent_task_id IS NOT NULL AND p.id IS NULL""").fetchone()[0])
        return values, rows

    def test_native_follow_up_wins_delete_race_and_active_child_protects_its_parent(self):
        values, rows = self._history_race(creator_first=True)
        self.assertGreater(values[0], 10)
        self.assertEqual(400, values[1])
        self.assertEqual([(10, None, "failed"), (values[0], 10, "queued")], [tuple(row.values()) for row in rows])

    def test_native_delete_wins_follow_up_race_without_creating_an_orphan(self):
        values, rows = self._history_race(creator_first=False)
        self.assertEqual((1, 404), values)
        self.assertEqual([], rows)

    def test_native_bulk_clear_race_preserves_the_new_active_child_and_parent(self):
        values, rows = self._history_race(creator_first=True, bulk=True)
        self.assertGreater(values[0], 10)
        self.assertEqual(0, values[1])
        self.assertEqual(2, len(rows))

    def complete(self, conn=None, result=None):
        return complete_agent_operation(conn or self.conn, token=self.issued["token"], operation_id="logical-1",
            result=result or {"id": 1}, required_scope="actions.execute", now=NOW)

    def concurrent(self, work):
        barrier = threading.Barrier(2)

        def run(index):
            with self.connection() as conn:
                barrier.wait(timeout=5)
                return work(conn, index)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, index) for index in range(2)]
            return [future.result(timeout=15) for future in futures]

    def test_native_two_confirmations_commit_one_business_and_one_receipt(self):
        def execute(conn, _):
            claimed = self.claim(conn)
            if claimed["claimed"]:
                identifier = conn.execute("INSERT INTO business_records(title) VALUES('Draft') RETURNING id").fetchone()["id"]
                result = self.complete(conn, {"id": identifier})
            else:
                result = claimed["operation"]
            return claimed["claimed"], result["result"]

        values = self.concurrent(execute)
        self.assertEqual([False, True], sorted(item[0] for item in values))
        self.assertEqual(values[0][1], values[1][1])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])

    def test_native_source_session_revocation_rolls_back_inflight_business(self):
        self.claim()
        self.conn.execute("INSERT INTO business_records(title) VALUES('Draft')")
        with self.connection() as other:
            other.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        with self.assertRaises(HTTPException) as caught:
            self.complete()
        self.assertEqual(401, caught.exception.status_code)
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_action_executions").fetchone()[0])

    def test_native_role_collision_admin_demotion_and_membership_revocation(self):
        _, student = self.issue(task_id=11, session="student-session")
        self.conn.commit()
        teacher = verify_task_delegation(self.conn, self.issued["token"], purpose="tools", now=NOW)
        learner = verify_task_delegation(self.conn, student["token"], purpose="tools", now=NOW)
        self.assertNotEqual(teacher.actor.key, learner.actor.key)
        self.conn.rollback()
        with self.connection() as other:
            other.execute("UPDATE teachers SET is_super_admin=0 WHERE id=7")
        with self.assertRaises(HTTPException) as caught:
            verify_task_delegation(self.conn, self.issued["token"], purpose="tools", now=NOW)
        self.assertEqual(401, caught.exception.status_code)
        self.conn.rollback()
        with self.connection() as other:
            other.execute("UPDATE teachers SET is_super_admin=1 WHERE id=7")
            other.execute("UPDATE teacher_organization_memberships SET is_active=0 WHERE teacher_id=7")
        with self.assertRaises(HTTPException):
            verify_task_delegation(self.conn, self.issued["token"], purpose="tools", now=NOW)
        self.assertEqual("student:7", verify_task_delegation(self.conn, student["token"], purpose="tools", now=NOW).actor.key)

    def test_native_expired_attempt_has_one_cas_reclaimer_and_fences_old_token(self):
        def reclaim(conn, index):
            try:
                item = create_task_attempt(conn, task_id=10, worker_id="new-" + str(index), startup_key="reclaim-" + str(index), now=NOW + 301)
                return item["fencing_token"]
            except HTTPException as exc:
                conn.rollback()
                return exc.status_code

        self.assertEqual([2, 409], sorted(self.concurrent(reclaim)))
        with self.assertRaises(HTTPException):
            verify_task_delegation(self.conn, self.issued["token"], purpose="tools", now=NOW + 301)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_attempts WHERE status='running'").fetchone()[0])

    def test_native_web_budget_cannot_be_overbooked_between_connections(self):
        grant = verify_task_delegation(self.conn, self.issued["token"], purpose="tools", now=NOW)
        self.conn.rollback()

        def reserve(conn, _):
            try:
                return budgets.reserve_agent_request_budget(conn, grant=grant, channel="web", now=NOW).id
            except HTTPException as exc:
                conn.rollback()
                return exc.status_code

        outcomes = self.concurrent(reserve)
        self.assertEqual(1, outcomes.count(429))
        lease_id = next(value for value in outcomes if isinstance(value, str))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_request_budget_leases").fetchone()[0])
        self.assertTrue(budgets.renew_agent_request_budget(self.conn, lease_id, now=NOW + 20))
        self.assertTrue(budgets.finish_agent_request_budget(self.conn, lease_id, now=NOW + 21))
        self.assertFalse(budgets.renew_agent_request_budget(self.conn, lease_id, now=NOW + 22))

    def test_native_key_switch_serializes_and_soft_delete_preserves_checks(self):
        key_ids = [keys.create_agent_api_key(self.conn, {"api_key": "synthetic-" + label, "key_label": label,
            "test_on_save": True, "make_active": False}, teacher_id=7,
            test_result={"status": "valid", "message": "Synthetic", "usage": {}})["key"]["id"] for label in ("a", "b")]
        self.conn.commit()
        self.concurrent(lambda conn, index: keys.set_active_agent_api_key(conn, key_ids[index]))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_api_keys WHERE is_active=1").fetchone()[0])
        selected = keys.get_active_agent_api_key(self.conn)[0]["id"]
        keys.delete_agent_api_key(self.conn, selected)
        self.conn.commit()
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_key_checks").fetchone()[0])
        self.assertEqual("", self.conn.execute("SELECT key_encrypted FROM agent_runtime_api_keys WHERE id=?", (selected,)).fetchone()[0])
        self.assertIsNone(keys.get_active_agent_api_key(self.conn))

    def test_native_fresh_user_confirmations_share_receipt_without_reviving_runner(self):
        self.conn.execute("UPDATE agent_tasks SET status='completed' WHERE id=10")
        self.conn.commit()
        identity = {"user": {"role": "teacher", "id": 7, "session_id": "teacher-session"},
                    "source_session_id": "teacher-session", "task_id": 10, "operation_id": "proposal:10:0", "now": NOW}

        def execute(conn, _):
            claimed = claim_user_agent_operation(conn, **identity, action="create_draft", params={"title": "Draft"})
            if claimed["claimed"]:
                identifier = conn.execute("INSERT INTO business_records(title) VALUES('Draft') RETURNING id").fetchone()["id"]
                complete_user_agent_operation(conn, **identity, result={"id": identifier})
            return claimed["claimed"]

        self.assertEqual([False, True], sorted(self.concurrent(execute)))
        row = self.conn.execute("SELECT source_kind,attempt_id,delegation_id,fencing_token FROM agent_action_executions").fetchone()
        self.assertEqual(("user_confirmation", None, None, None), tuple(row.values()))
        self.assertEqual("completed", self.conn.execute("SELECT status FROM agent_tasks WHERE id=10").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
