"""Native claim and material-lock races; original permissions and SQL run.

Uses a fresh synthetic database and the migrated signature schema. Notification
delivery alone is suppressed by the base fixture. This does not certify the
separate account/signature identity-edit lock protocol.
"""
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from classroom_app.services import signature_workflow_service as workflow, signature_point_service as points
from classroom_app.services.material_signature_revision_service import invalidate_pending_material_plans
from classroom_app.services.signature_service import SignatureServiceError
from classroom_app.services.signature_workflow_lock_service import lock_signature_workflows
from tests.test_signature_decisions_postgres import SignatureDecisionsPostgresTests


class SignatureClaimsPostgresTests(SignatureDecisionsPostgresTests):
    def setUp(self):
        super().setUp()
        self.conn.execute("""CREATE TABLE classes(id BIGINT PRIMARY KEY,school_code TEXT,school_name TEXT,college TEXT,department TEXT);
            INSERT INTO classes VALUES(30,'A','School A','C','D');
            ALTER TABLE material_ai_import_records ADD COLUMN document_type_label TEXT DEFAULT 'Synthetic analysis';""")
        self.seed_claim(10, 'teacher', 7)
        self.seed_claim(11, 'student', 7)

    def seed_claim(self, identifier, role, actor_id, signature_id=1):
        self.conn.execute("""INSERT INTO signature_access_requests(id,signature_id,requester_teacher_id,
            requester_role,requester_id,owner_role,owner_id,request_kind,context_type,context_id)
            VALUES(?,?,? ,?,?,'teacher',8,'claim','signature_claim',?)""",
            (identifier,signature_id,actor_id if role=='teacher' else None,role,actor_id,str(signature_id)))
        self.conn.execute("""INSERT INTO signature_access_request_reviewers(request_id,reviewer_role,reviewer_id,
            reviewer_kind,reviewer_name_snapshot) VALUES(?,'teacher',8,'signer','Other')""", (identifier,))
        self.conn.commit()

    def other_review(self, identifier):
        with self.connection() as conn:
            try:
                workflow.review_access_request(conn, {'role':'teacher','id':8}, identifier, action='approve')
                return 200
            except SignatureServiceError as exc:
                conn.rollback()
                return exc.status_code

    def test_claim_waits_for_signature_before_locking_any_competing_request(self):
        self.conn.execute('UPDATE electronic_signatures SET id=id WHERE id=1')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.other_review,10)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM signature_access_requests WHERE id IN (10,11) FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(200,future.result(timeout=5))

    def test_competing_claim_approvals_cancel_loser_without_double_transfer(self):
        result=workflow.review_access_request(self.conn,{'role':'teacher','id':8},10,action='approve')
        self.assertEqual('approved',result['request']['status'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.other_review,11)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409,future.result(timeout=5))
        signature=self.conn.execute('SELECT subject_role,subject_id FROM electronic_signatures WHERE id=1').fetchone()
        self.assertEqual(('teacher',7),(signature['subject_role'],signature['subject_id']))
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM signature_access_requests WHERE id=11').fetchone()[0])

    def test_direct_claim_closes_old_pending_claims_and_preserves_actor_identity(self):
        self.conn.execute("UPDATE electronic_signatures SET subject_name='Teacher',subject_id=NULL,identity_category='teacher' WHERE id=1")
        result=workflow.claim_signature(self.conn,{'role':'teacher','id':7},1)
        self.assertEqual('success',result['status'])
        self.conn.commit()
        self.assertEqual({'cancelled'},{row[0] for row in self.conn.execute('SELECT status FROM signature_access_requests WHERE id IN (10,11)').fetchall()})
        self.assertEqual(409,self.other_review(11))
        self.assertEqual('teacher',self.conn.execute('SELECT identity_category FROM teachers WHERE id=7').fetchone()[0])
        self.assertEqual('',self.conn.execute('SELECT identity_category FROM students WHERE id=7').fetchone()[0])

    def test_new_claim_reloads_new_owner_after_waiting_for_transfer(self):
        workflow.review_access_request(self.conn,{'role':'teacher','id':8},10,action='approve')
        def create():
            with self.connection() as conn:
                return workflow.create_claim_request(conn,{'role':'student','id':7},1)
        # Fixture IDs were explicit; synchronize the actual PG sequence before
        # exercising the production INSERT ... RETURNING path.
        self.conn.execute("SELECT setval(pg_get_serial_sequence('signature_access_requests','id'),11,true)")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(create)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            created=future.result(timeout=5)
        request_id=created['request']['id']
        self.assertEqual([7],[row[0] for row in self.conn.execute('SELECT reviewer_id FROM signature_access_request_reviewers WHERE request_id=?',(request_id,)).fetchall()])

    def test_reverse_claim_batch_locks_lowest_signature_before_other_requests(self):
        self.conn.execute("""INSERT INTO electronic_signatures(id,name,subject_name,subject_role,subject_id,
            owner_role,owner_id,owner_name_snapshot,scope_level,school_code,college,department,file_hash,stored_path)
            VALUES(2,'Second','Other','teacher',8,'teacher',8,'Other','platform','B','C','D',?,'second.png')""",('b'*64,))
        self.seed_claim(20,'teacher',7,2)
        self.seed_claim(21,'student',7,2)
        self.conn.execute('UPDATE electronic_signatures SET id=id WHERE id=1')
        def batch():
            with self.connection() as conn:
                return workflow.batch_review_access_requests(conn,{'role':'teacher','id':8},[20,10],action='approve')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(batch)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=2 FOR UPDATE NOWAIT')
                self.conn.execute('SELECT id FROM signature_access_requests WHERE id IN (10,11,20,21) FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            result=future.result(timeout=5)
        self.assertEqual((2,0),(result['processed'],result['failed']))
        self.assertEqual({'cancelled'},{row[0] for row in self.conn.execute('SELECT status FROM signature_access_requests WHERE id IN (11,21)').fetchall()})

    def test_legacy_review_waits_for_material_before_child_locks_and_invalidation(self):
        self.conn.execute('UPDATE material_ai_import_records SET id=id WHERE id=88')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.other_review,1)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM signature_access_request_reviewers WHERE request_id=1 FOR UPDATE NOWAIT')
                invalidate_pending_material_plans(self.conn,'academic_final_material','88','new-revision')
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409,future.result(timeout=5))
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM signature_point_flows WHERE id=1').fetchone()[0])

    def test_binding_waits_for_material_before_acquiring_binding_advisory_lock(self):
        self.conn.execute('UPDATE material_ai_import_records SET id=id WHERE id=88')
        def bind():
            with self.connection() as conn:
                return points.bind_point_signatures(conn,{'role':'teacher','id':7},
                    function_point_key='academic_final_material.exam_analysis.department_review_signature',
                    material_type='academic_final_material',material_id='88',signature_ids=[])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bind)
            try:
                self._assert_native_waiting(future)
                key='signature-point-binding:academic_final_material.exam_analysis.department_review_signature:academic_final_material:88:revision'
                self.assertTrue(self.conn.execute('SELECT pg_try_advisory_xact_lock(hashtext(?))',(key,)).fetchone()[0])
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual([],future.result(timeout=5))

    def test_signature_association_changed_while_waiting_fails_closed(self):
        self.conn.execute("INSERT INTO electronic_signatures(id,name,owner_role,owner_id,file_hash,stored_path) VALUES(2,'Second','teacher',8,?,'second.png')",('b'*64,))
        self.conn.commit()
        self.conn.execute('UPDATE material_ai_import_records SET id=id WHERE id=88')
        def lock():
            with self.connection() as conn:
                try:
                    lock_signature_workflows(conn,request_ids=[1])
                    return 200
                except SignatureServiceError as exc:
                    conn.rollback()
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(lock)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('UPDATE signature_access_requests SET signature_id=2 WHERE id=1')
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409,future.result(timeout=5))

    def test_assessment_rotation_waits_for_plan_before_canceling_children(self):
        from classroom_app.db import schema_assessment_plans
        from classroom_app.services.assessment_plan_service import rotate_signature_revision
        ready=patch.object(schema_assessment_plans,'_SCHEMA_READY',False)
        ready.start(); self.addCleanup(ready.stop)
        engine=patch.object(schema_assessment_plans,'get_configured_db_engine',return_value='postgres')
        engine.start(); self.addCleanup(engine.stop)
        schema_assessment_plans.ensure_assessment_plan_schema(self.conn)
        self.conn.execute("INSERT INTO assessment_plans(id,teacher_id,signature_revision) VALUES('plan-1',7,'old')")
        for table in ('signature_point_flows','signature_access_requests'):
            self.conn.execute(f"UPDATE {table} SET material_type='assessment_plan',material_id='plan-1',material_revision='old' WHERE id=1")
        self.conn.commit()
        self.conn.execute("UPDATE assessment_plans SET id=id WHERE id='plan-1'")
        def rotate():
            with self.connection() as conn:
                return rotate_signature_revision(conn,'plan-1')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(rotate)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM signature_access_request_reviewers WHERE request_id=1 FOR UPDATE NOWAIT')
                self.conn.execute('SELECT id FROM signature_point_flows WHERE id=1 FOR UPDATE NOWAIT')
                self.conn.execute('SELECT id FROM signature_access_requests WHERE id=1 FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            revision=future.result(timeout=5)
        self.assertNotEqual('old',revision)
        self.assertEqual(revision,self.conn.execute("SELECT signature_revision FROM assessment_plans WHERE id='plan-1'").fetchone()[0])
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM signature_access_requests WHERE id=1').fetchone()[0])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SignatureClaimsPostgresTests(name)
        for name,value in SignatureClaimsPostgresTests.__dict__.items()
        if name.startswith('test_') and callable(value))
