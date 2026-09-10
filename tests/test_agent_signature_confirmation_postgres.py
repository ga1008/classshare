"""Native C transaction ordering against material edits, revocation and logout.

Uses fresh synthetic PostgreSQL databases and the existing signature schema
fixture. Snapshot metadata is seeded; document rendering/delivery is covered
by the HTTP fixture, not claimed by these database lock tests.
"""
from concurrent.futures import ThreadPoolExecutor
import unittest

from fastapi import HTTPException

from classroom_app.services.account_credentials_service import prepare_credentials_change
from classroom_app.services import agent_signature_confirmation_service as confirmation
from classroom_app.services.material_signature_service import load_material
from tests.test_signature_decisions_postgres import SignatureDecisionsPostgresTests


class SignatureConfirmationPostgresTests(SignatureDecisionsPostgresTests):
    def setUp(self):
        super().setUp()
        self.user = {'role':'teacher','id':8,'session_id':'other-session'}
        self.conn.execute("INSERT INTO agent_tasks VALUES(12,'teacher',8,8,'failed',NULL)")
        self.conn.execute("INSERT INTO user_sessions VALUES('teacher:8','other-session','8','teacher','2033-05-19T00:00:00+00:00')")
        material = load_material(self.conn,{'role':'teacher','id':7},{'material_type':'academic_final_material','material_id':'88'})
        self.conn.execute("""INSERT INTO signature_material_snapshots(id,material_type,material_id,material_revision,document_type,
            owner_role,owner_id,title,content_fingerprint,payload_json,artifact_json)
            VALUES(?,'academic_final_material','88','revision','academic_exam_analysis','teacher',7,?,?,'{}','{}')""",
            ('c'*64,material['title'],material['content_fingerprint']))
        self.conn.execute('UPDATE signature_access_requests SET snapshot_id=?,signature_hash=? WHERE id=1',('c'*64,'a'*64))
        self.conn.execute('UPDATE signature_point_flows SET snapshot_id=? WHERE id=1',('c'*64,))
        self.conn.commit()
        self.prepared = confirmation.prepare_user_confirmation(self.conn,action='review_signature_request',params={'request_id':1},user=self.user)
        self.conn.commit()

    def confirm(self, conn):
        return confirmation.dispatch_user_confirmation(conn,user=self.user,source_session_id='other-session',task_id=12,
            operation_id='proposal:12:0',action='review_signature_request',params=self.prepared['params'],
            confirmation_inputs={'decision':'approve','accepted_warning_codes':[],'confirmation_note':'Synthetic reviewer checked the original document'})

    def competing_confirmation(self):
        with self.connection() as conn:
            try:
                self.confirm(conn)
                conn.commit()
                return 200
            except HTTPException as exc:
                conn.rollback()
                return exc.status_code

    def assert_wait_then_reject(self, status):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.competing_confirmation)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(status,future.result(timeout=5))
        self.assertEqual('pending',self.conn.execute('SELECT status FROM signature_access_requests WHERE id=1').fetchone()[0])
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM agent_action_executions').fetchone()[0])

    def test_material_change_committed_first_invalidates_waiting_human_confirmation(self):
        self.conn.execute("UPDATE material_ai_import_records SET signature_revision='changed' WHERE id=88")
        self.assert_wait_then_reject(409)

    def test_account_revocation_committed_first_rejects_waiting_confirmation(self):
        prepare_credentials_change(self.conn,role='teacher',user_id=8)
        self.conn.execute('UPDATE teachers SET is_active=0 WHERE id=8')
        self.assert_wait_then_reject(403)

    def test_logout_committed_first_rejects_waiting_confirmation(self):
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:8'")
        self.assert_wait_then_reject(401)

    def test_confirmation_committed_first_orders_later_account_revocation(self):
        self.confirm(self.conn)
        def revoke():
            with self.connection() as conn:
                prepare_credentials_change(conn,role='teacher',user_id=8)
                conn.execute('UPDATE teachers SET is_active=0 WHERE id=8')
                conn.commit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(revoke)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            future.result(timeout=5)
        self.assertEqual('approved',self.conn.execute('SELECT status FROM signature_access_requests WHERE id=1').fetchone()[0])
        self.assertEqual('completed',self.conn.execute('SELECT status FROM agent_action_executions').fetchone()[0])
        self.assertEqual(403,self.competing_confirmation())

    def test_confirmation_committed_first_orders_later_logout(self):
        self.confirm(self.conn)
        def logout():
            with self.connection() as conn:
                conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:8'")
                conn.commit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(logout)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            future.result(timeout=5)
        self.assertEqual('approved',self.conn.execute('SELECT status FROM signature_access_requests WHERE id=1').fetchone()[0])
        self.assertEqual('completed',self.conn.execute('SELECT status FROM agent_action_executions').fetchone()[0])
        self.assertEqual(401,self.competing_confirmation())


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SignatureConfirmationPostgresTests(name)
        for name,value in SignatureConfirmationPostgresTests.__dict__.items()
        if name.startswith('test_') and callable(value))
