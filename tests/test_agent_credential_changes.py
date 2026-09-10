"""Normal password workflows revoke both live and persistent Agent authority."""
from contextlib import contextmanager
import json
import sqlite3
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.routers import profile
from classroom_app.routers.ui_parts import auth
from classroom_app.routers.manage_parts import system_config
from classroom_app.services.account_credentials_service import credentials_changed, prepare_credentials_change
from classroom_app.services.agent_delegation_service import create_persistent_authorization, verify_task_delegation
from classroom_app.services.student_auth_service import build_password_setup_token
from tests.test_agent_platform_writes import PlatformWriteFixture


class CredentialChangeTests(PlatformWriteFixture):
    old_password = 'Original-password-2026!'
    new_password = 'Changed-password-2026!'

    def setUp(self):
        super().setUp()
        self.conn.execute('UPDATE teachers SET is_super_admin=1 WHERE id=7')
        hashed = dependencies.get_password_hash(self.old_password)
        self.conn.execute('UPDATE teachers SET hashed_password=? WHERE id=7', (hashed,))
        self.conn.execute('UPDATE students SET hashed_password=?,password_reset_required=0 WHERE id=7', (hashed,))
        self.tokens = {user['role']: self.token(user, scopes=['platform:read']) for user in (self.teacher, self.student)}
        for user in (self.teacher, self.student):
            create_persistent_authorization(self.conn, actor_role=user['role'], actor_id=7, source_session_id=user['session_id'],
                scopes=['platform:read'], intent_reference='fixture-subscription', ttl_seconds=600)
        self.conn.commit()
        self.app = FastAPI()
        self.app.include_router(profile.router)
        self.app.include_router(auth.router)
        self.app.include_router(system_config.router, prefix='/api/manage')
        for target in ('classroom_app.dependencies.get_db_connection', 'classroom_app.database.get_db_connection', 'classroom_app.db.sessions.get_db_connection',
                       'classroom_app.routers.ui_parts.common.get_db_connection', 'classroom_app.routers.profile.get_db_connection',
                       'classroom_app.routers.ui_parts.auth.get_db_connection', 'classroom_app.routers.manage_parts.system_config.get_db_connection'):
            guard=patch(target,self.connection);guard.start();self.addCleanup(guard.stop)
        # A missed imported connection alias must fail, never touch the local
        # application DB. All legitimate SQL uses the already-created fixture.
        for target in ('sqlite3.connect', 'classroom_app.db.connection.get_db_connection', 'classroom_app.db.connection.connect_postgres'):
            guard=patch(target,side_effect=AssertionError('Unexpected database connector outside credential fixture'))
            guard.start();self.addCleanup(guard.stop)

    def login(self, user):
        self.app.dependency_overrides[dependencies.get_current_user] = lambda: user
        self.app.dependency_overrides[dependencies.get_current_teacher] = lambda: user
        self.app.dependency_overrides[dependencies.get_current_student] = lambda: user

    def assert_revoked(self, role, *, session_retained):
        for table in ('agent_task_delegations','agent_persistent_authorizations'):
            rows=self.conn.execute(f'SELECT status,revoke_reason FROM {table} WHERE actor_role=? AND actor_id=7',(role,)).fetchall()
            self.assertTrue(rows)
            self.assertTrue(all(row['status']=='revoked' and row['revoke_reason']=='account_credential_changed' for row in rows))
        count=self.conn.execute('SELECT COUNT(*) FROM user_sessions WHERE session_user_key=?',(f'{role}:7',)).fetchone()[0]
        self.assertEqual(int(session_retained),count)
        with self.assertRaises(HTTPException): verify_task_delegation(self.conn,self.tokens[role],purpose='tools',required_scope='platform:read')

    def test_profile_password_changes_revoke_each_role_without_crossing_same_numbered_actor(self):
        with TestClient(self.app) as client:
            for user in (self.teacher,self.student):
                self.login(user)
                response=client.put('/api/profile/password',json={'current_password':self.old_password,'new_password':self.new_password,'confirm_password':self.new_password})
                self.assertEqual(200,response.status_code,response.text)
                self.assert_revoked(user['role'],session_retained=True)
                self.assertNotIn(self.new_password,response.text)
                if user['role']=='teacher':
                    verify_task_delegation(self.conn,self.tokens['student'],purpose='tools',required_scope='platform:read')
        row=self.conn.execute('SELECT hashed_password FROM students WHERE id=7').fetchone()
        self.assertTrue(dependencies.verify_password(self.new_password,row[0]))

    def test_student_password_form_preserves_web_session_but_retires_old_agent_authority(self):
        self.login(self.student)
        with TestClient(self.app) as client:
            response=client.post('/api/student/password/change',data={'current_password':self.old_password,'new_password':self.new_password,'confirm_password':self.new_password})
        self.assertEqual(200,response.status_code,response.text)
        self.assert_revoked('student',session_retained=True)
        verify_task_delegation(self.conn,self.tokens['teacher'],purpose='tools',required_scope='platform:read')

    def test_teacher_admin_reset_revokes_sessions_and_grants_in_same_commit(self):
        self.login(self.teacher)
        with TestClient(self.app) as client:
            response=client.post('/api/manage/system/teachers/7/reset-password',data={'password':self.new_password})
        self.assertEqual(200,response.status_code,response.text)
        self.assert_revoked('teacher',session_retained=False)
        verify_task_delegation(self.conn,self.tokens['student'],purpose='tools',required_scope='platform:read')

    def test_invalid_old_password_keeps_authority_and_commit_failure_rolls_back_password_and_revocation(self):
        self.login(self.teacher)
        payload={'current_password':'wrong','new_password':self.new_password,'confirm_password':self.new_password}
        with TestClient(self.app,raise_server_exceptions=False) as client:
            self.assertEqual(400,client.put('/api/profile/password',json=payload).status_code)
            verify_task_delegation(self.conn,self.tokens['teacher'],purpose='tools',required_scope='platform:read')
            payload['current_password']=self.old_password
            class CommitFailure(sqlite3.Connection):
                commit_calls = 0
                def commit(self):
                    self.commit_calls += 1
                    raise RuntimeError('Synthetic unavailable commit')
            # Keep the real SQLite connection type so dialect-aware actor
            # locks execute normally and the failure occurs at commit.
            failed_conn=CommitFailure(':memory:',check_same_thread=False)
            failed_conn.row_factory=sqlite3.Row
            self.addCleanup(failed_conn.close)
            self.conn.backup(failed_conn)
            @contextmanager
            def fail_commit():
                try:yield failed_conn
                except Exception:failed_conn.rollback();raise
            with patch.object(profile,'get_db_connection',fail_commit):
                self.assertEqual(500,client.put('/api/profile/password',json=payload).status_code)
            self.assertEqual(1,failed_conn.commit_calls)
            verify_task_delegation(failed_conn,self.tokens['teacher'],purpose='tools',required_scope='platform:read')
            self.assertEqual('active',failed_conn.execute("SELECT status FROM agent_persistent_authorizations WHERE actor_role='teacher'").fetchone()[0])
            self.assertTrue(dependencies.verify_password(self.old_password,failed_conn.execute('SELECT hashed_password FROM teachers WHERE id=7').fetchone()[0]))
        verify_task_delegation(self.conn,self.tokens['teacher'],purpose='tools',required_scope='platform:read')
        self.assertEqual('active',self.conn.execute("SELECT status FROM agent_persistent_authorizations WHERE actor_role='teacher'").fetchone()[0])
        self.assertTrue(dependencies.verify_password(self.old_password,self.conn.execute('SELECT hashed_password FROM teachers WHERE id=7').fetchone()[0]))

    def test_logout_alone_keeps_persistent_authority_and_helper_rollback_restores_all_rows(self):
        prepare_credentials_change(self.conn,role='teacher',user_id=7)
        credentials_changed(self.conn,role='teacher',user_id=7,invalidate_sessions=True)
        self.conn.rollback()
        verify_task_delegation(self.conn,self.tokens['teacher'],purpose='tools',required_scope='platform:read')
        dependencies.invalidate_session_for_user('7','teacher',conn=self.conn)
        self.conn.commit()
        self.assertEqual('active',self.conn.execute("SELECT status FROM agent_persistent_authorizations WHERE actor_role='teacher'").fetchone()[0])
        self.assertEqual('active',self.conn.execute("SELECT status FROM agent_task_delegations WHERE actor_role='teacher'").fetchone()[0])

    def test_student_reset_approval_revokes_before_setup_and_setup_creates_only_a_new_web_session(self):
        self.conn.execute("INSERT INTO student_password_reset_requests(id,student_id,class_id,teacher_id,status,request_name,request_student_id_number,request_class_name) VALUES(1,7,30,7,'pending','Student 7','S7','Class 30')")
        self.conn.commit();self.login(self.teacher)
        with TestClient(self.app) as client:
            approved=client.post('/api/manage/system/password-resets/1/approve',data={'review_note':'身份已核对'})
            self.assertEqual(200,approved.status_code,approved.text)
            self.assert_revoked('student',session_retained=False)
            token=build_password_setup_token(7,'/dashboard','password_reset',reset_request_id=1)
            result=client.post('/api/student/password/setup',data={'setup_token':token,'password':self.new_password,'confirm_password':self.new_password})
            self.assertEqual(200,result.status_code,result.text)
        self.assert_revoked('student',session_retained=True)
        self.assertNotEqual('student-session',self.conn.execute("SELECT session_id FROM user_sessions WHERE session_user_key='student:7'").fetchone()[0])
        self.assertEqual('completed',self.conn.execute('SELECT status FROM student_password_reset_requests WHERE id=1').fetchone()[0])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM agent_persistent_authorizations WHERE actor_role='student'").fetchone()[0])

    def test_first_password_setup_revokes_legacy_authority_and_rejects_invalid_setup_without_changes(self):
        self.conn.execute("UPDATE students SET hashed_password=NULL WHERE id=7");self.conn.commit()
        with TestClient(self.app) as client:
            response=client.post('/api/student/password/setup',data={'setup_token':'invalid','password':self.new_password,'confirm_password':self.new_password})
            self.assertEqual(400,response.status_code)
            self.assertEqual('active',self.conn.execute("SELECT status FROM agent_persistent_authorizations WHERE actor_role='student'").fetchone()[0])
            token=build_password_setup_token(7,'/dashboard','first_login')
            response=client.post('/api/student/password/setup',data={'setup_token':token,'password':self.new_password,'confirm_password':self.new_password})
            self.assertEqual(200,response.status_code,response.text)
        self.assert_revoked('student',session_retained=True)

    def test_normal_teacher_creation_restores_inactive_account_without_reviving_old_grants(self):
        self.conn.execute('UPDATE teachers SET is_active=0 WHERE id=7')
        self.conn.execute('UPDATE teachers SET is_super_admin=1 WHERE id=8');self.conn.commit()
        self.login({'role':'teacher','id':8})
        with TestClient(self.app) as client:
            response=client.post('/api/manage/system/teachers',data={'name':'Restored teacher','email':'7@example.test','password':self.new_password,'department':'Test Department'})
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual(7,response.json()['teacher']['id'])
        self.assert_revoked('teacher',session_retained=False)

    def test_reset_approval_that_waited_for_transition_cannot_overwrite_completed_request(self):
        self.conn.execute("INSERT INTO student_password_reset_requests(id,student_id,class_id,teacher_id,status,request_name,request_student_id_number,request_class_name) VALUES(1,7,30,7,'pending','Student 7','S7','Class 30')")
        self.conn.commit();self.login(self.teacher)
        from classroom_app.services import account_credentials_service as credentials
        original=credentials.prepare_credentials_change
        def completed_while_waiting(conn,**kwargs):
            # Test the exact post-wait state; a separate committed actor
            # transition may have completed setup before this lock returns.
            conn.execute("UPDATE student_password_reset_requests SET status='completed' WHERE id=1")
            conn.commit()
            original(conn,**kwargs)
        with TestClient(self.app) as client, patch.object(credentials,'prepare_credentials_change',side_effect=completed_while_waiting):
            response=client.post('/api/manage/system/password-resets/1/approve',data={'review_note':'Old browser approval'})
        self.assertEqual(409,response.status_code,response.text)
        self.assertEqual('completed',self.conn.execute('SELECT status FROM student_password_reset_requests WHERE id=1').fetchone()[0])
        self.assertEqual(0,self.conn.execute('SELECT password_reset_required FROM students WHERE id=7').fetchone()[0])
        verify_task_delegation(self.conn,self.tokens['student'],purpose='tools',required_scope='platform:read')
        self.assertEqual('active',self.conn.execute("SELECT status FROM agent_persistent_authorizations WHERE actor_role='student'").fetchone()[0])
