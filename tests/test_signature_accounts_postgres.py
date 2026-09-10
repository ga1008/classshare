"""Native account/identity/signature races in an exclusive synthetic database."""
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from classroom_app.services import signature_identity_service as identities
from classroom_app.services import signature_service as signatures
from classroom_app.services import signature_workflow_service as workflow, signature_point_service as points
from tests.test_signature_claims_postgres import SignatureClaimsPostgresTests


class SignatureAccountsPostgresTests(SignatureClaimsPostgresTests):
    def setUp(self):
        super().setUp()
        from classroom_app.db import schema_assessment_plans
        for name, value in [('_SCHEMA_READY', False), ('get_configured_db_engine', lambda: 'postgres')]:
            handle = patch.object(schema_assessment_plans, name, value)
            handle.start(); self.addCleanup(handle.stop)
        schema_assessment_plans.ensure_assessment_plan_schema(self.conn)
        self.conn.commit()

    def second_signature(self):
        self.conn.execute("""INSERT INTO electronic_signatures(id,name,subject_name,subject_role,subject_id,
            owner_role,owner_id,owner_name_snapshot,scope_level,school_code,college,department,file_hash,stored_path)
            VALUES(2,'Second','Other','teacher',8,'teacher',8,'Other','platform','A','C','D',?,'second.png')""", ('b'*64,))
        self.conn.commit()

    def merge(self, primary=1, duplicate=2):
        with self.connection() as conn:
            try:
                signatures.merge_duplicate_signatures(conn, {'role':'teacher','id':7}, primary, [duplicate])
                return 200
            except signatures.SignatureServiceError as exc:
                conn.rollback()
                return exc.status_code

    def test_appointment_writer_and_claim_do_not_take_opposite_locks(self):
        self.conn.execute('SELECT id FROM teachers WHERE id=8 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.other_review, 10)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=1 FOR UPDATE NOWAIT')
                identities.set_identity_appointments(self.conn, 'teacher', 8, [{'identity_category':'dean'}])
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(200, future.result(timeout=5))
        self.assertEqual('dean', self.conn.execute('SELECT identity_category FROM teachers WHERE id=7').fetchone()[0])
        self.assertEqual('', self.conn.execute('SELECT identity_category FROM students WHERE id=7').fetchone()[0])

    def test_binding_change_while_waiting_is_conflict_without_late_account_lock(self):
        self.conn.execute('SELECT id FROM teachers WHERE id=8 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.other_review, 10)
            try:
                self._assert_native_waiting(future)
                self.conn.execute("UPDATE electronic_signatures SET subject_role='student',subject_id=7 WHERE id=1")
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409, future.result(timeout=5))
        self.assertEqual('pending', self.conn.execute('SELECT status FROM signature_access_requests WHERE id=10').fetchone()[0])
        self.assertEqual('student', self.conn.execute('SELECT subject_role FROM electronic_signatures WHERE id=1').fetchone()[0])

    def test_metadata_waits_for_account_and_rejects_changed_source(self):
        self.conn.execute('SELECT id FROM teachers WHERE id=8 FOR UPDATE')
        def edit():
            with self.connection() as conn:
                try:
                    signatures.update_signature_metadata(conn, {'role':'teacher','id':7}, 1, {'description':'new'})
                    return 200
                except signatures.SignatureServiceError as exc:
                    conn.rollback()
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=1 FOR UPDATE NOWAIT')
                identities.set_identity_appointments(self.conn, 'teacher', 8, [{'identity_category':'dean'}])
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409, future.result(timeout=5))
        self.assertEqual('dean', self.conn.execute('SELECT identity_category FROM electronic_signatures WHERE id=1').fetchone()[0])

    def test_merge_waits_for_account_before_signatures_and_keeps_identity_update(self):
        self.second_signature()
        self.conn.execute('SELECT id FROM teachers WHERE id=8 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.merge)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id IN (1,2) FOR UPDATE NOWAIT')
                identities.set_identity_appointments(self.conn, 'teacher', 8, [{'identity_category':'dean'}])
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(200, future.result(timeout=5))
        self.assertEqual('dean', self.conn.execute('SELECT identity_category FROM electronic_signatures WHERE id=1').fetchone()[0])
        self.assertEqual('deleted', self.conn.execute('SELECT status FROM electronic_signatures WHERE id=2').fetchone()[0])

    def test_new_reference_while_merge_waits_for_material_conflicts_without_repointing(self):
        self.second_signature()
        self.conn.execute('SELECT id FROM material_ai_import_records WHERE id=88 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.merge)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=2 FOR UPDATE NOWAIT')
                self.conn.execute("INSERT INTO signature_point_flow_items(flow_id,signature_id,display_order,status) VALUES(1,2,1,'pending')")
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409, future.result(timeout=5))
        self.assertEqual('active', self.conn.execute('SELECT status FROM electronic_signatures WHERE id=2').fetchone()[0])
        self.assertEqual(2, self.conn.execute('SELECT signature_id FROM signature_point_flow_items WHERE flow_id=1 AND display_order=1').fetchone()[0])

    def test_reverse_merge_takes_lowest_signature_before_any_later_signature(self):
        self.second_signature()
        self.conn.execute('SELECT id FROM electronic_signatures WHERE id=1 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.merge, 2, 1)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=2 FOR UPDATE NOWAIT')
                self.conn.execute('SELECT id FROM signature_point_flows WHERE id=1 FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(200, future.result(timeout=5))
        self.assertEqual('deleted', self.conn.execute('SELECT status FROM electronic_signatures WHERE id=1').fetchone()[0])

    def test_request_waiting_for_merge_cannot_reference_deleted_duplicate(self):
        self.second_signature()
        signatures.merge_duplicate_signatures(self.conn, {'role':'teacher','id':7}, 1, [2])
        def create():
            with self.connection() as conn:
                try:
                    workflow.create_access_request(conn, {'role':'teacher','id':7}, 2,
                        function_point_keys=['assessment_plan.reviewer_signature'])
                    return 200
                except signatures.SignatureServiceError as exc:
                    conn.rollback()
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(create)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(404, future.result(timeout=5))
        self.assertEqual(0, self.conn.execute('SELECT COUNT(*) FROM signature_access_requests WHERE signature_id=2').fetchone()[0])

    def test_application_waits_for_signature_before_flow_lock(self):
        from classroom_app.services.material_signature_apply_service import validate_application
        self.conn.execute('SELECT id FROM electronic_signatures WHERE id=1 FOR UPDATE')
        def validate():
            with self.connection() as conn:
                try:
                    validate_application(conn, 1)
                    return 200
                except signatures.SignatureServiceError as exc:
                    conn.rollback()
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(validate)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM signature_point_flows WHERE id=1 FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409, future.result(timeout=5))

    def test_reverse_point_selection_prelocks_full_set_before_creating_flow(self):
        self.second_signature()
        self.conn.execute("INSERT INTO material_ai_import_records(id,teacher_id,signature_revision,source_file_hash,parse_status,document_type,export_payload_json) VALUES(89,7,'revision','hash','completed','academic_exam_analysis','{}')")
        for table, maximum in [('signature_point_flows', 1), ('signature_access_requests', 11)]:
            self.conn.execute('SELECT setval(pg_get_serial_sequence(?, ?), ?, true)', (table, 'id', maximum))
        self.conn.commit()
        self.conn.execute('SELECT id FROM electronic_signatures WHERE id=1 FOR UPDATE')
        def create():
            with self.connection() as conn:
                return points.create_point_flow(conn, {'role':'teacher','id':7},
                    function_point_key='academic_final_material.exam_analysis.department_review_signature',
                    material_type='academic_final_material', material_id='89', signature_ids=[2,1])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(create)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=2 FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            result = future.result(timeout=5)
        self.assertEqual([2,1], [item['signature_id'] for item in result['flow']['items']])

    def test_live_plan_scalar_or_json_reference_blocks_merge_without_document_rewrite(self):
        self.second_signature()
        for column, value in [('examiner_signature_id', 2), ('reviewer_signature_ids_json', '["2"]')]:
            with self.subTest(column=column):
                self.conn.execute(f"INSERT INTO assessment_plans(id,teacher_id,title,{column}) VALUES('live-plan',7,'Existing paper',?)", (value,))
                self.conn.commit()
                self.assertEqual(409, self.merge())
                self.assertEqual(value, self.conn.execute(f"SELECT {column} FROM assessment_plans WHERE id='live-plan'").fetchone()[0])
                self.assertEqual('active', self.conn.execute('SELECT status FROM electronic_signatures WHERE id=2').fetchone()[0])
                self.conn.execute("DELETE FROM assessment_plans WHERE id='live-plan'")
                self.conn.commit()

    def test_legacy_material_and_current_binding_each_block_merge_without_soft_delete(self):
        self.second_signature()
        payload='{"fields":{"teacher_signature_ids":[2]}}'
        self.conn.execute('UPDATE material_ai_import_records SET signature_revision=?,export_payload_json=? WHERE id=88', ('',payload))
        self.conn.commit()
        self.assertEqual(409, self.merge())
        self.assertEqual(payload, self.conn.execute('SELECT export_payload_json FROM material_ai_import_records WHERE id=88').fetchone()[0])
        self.conn.execute("UPDATE material_ai_import_records SET signature_revision='revision',export_payload_json='{}' WHERE id=88")
        self.conn.execute("""INSERT INTO signature_point_bindings(function_point_key,material_type,material_id,material_revision,
            signature_id,display_order,bound_by_role,bound_by_id) VALUES('academic_final_material.exam_analysis.department_review_signature','academic_final_material','88','revision',2,0,'teacher',7)""")
        self.conn.commit()
        self.assertEqual(409, self.merge())
        self.assertEqual('active', self.conn.execute('SELECT status FROM electronic_signatures WHERE id=2').fetchone()[0])

    def test_plan_write_waiting_for_merge_revalidates_signature_and_rolls_back(self):
        from classroom_app.services.assessment_plan_service import set_signatures
        self.second_signature()
        self.conn.execute("INSERT INTO assessment_plans(id,teacher_id,title) VALUES('new-plan',7,'Empty plan')")
        self.conn.commit()
        signatures.merge_duplicate_signatures(self.conn, {'role':'teacher','id':7}, 1, [2])
        def bind():
            with self.connection() as conn:
                try:
                    set_signatures(conn, 'new-plan', role='examiner', signature_ids=[2])
                    return 200
                except signatures.SignatureServiceError as exc:
                    conn.rollback()
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(bind)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(404, future.result(timeout=5))
        self.assertIsNone(self.conn.execute("SELECT examiner_signature_id FROM assessment_plans WHERE id='new-plan'").fetchone()[0])

    def test_expiry_sweep_prelocks_all_holders_signatures_in_global_id_order(self):
        self.second_signature()
        self.conn.execute('UPDATE electronic_signatures SET subject_id=7 WHERE id=2')
        for identifier in (7,8):
            identities.set_identity_appointments(self.conn, 'teacher', identifier,
                [{'identity_category':'dean','term_end':'2020-01-01'}])
        self.conn.commit()
        self.conn.execute('SELECT id FROM electronic_signatures WHERE id=1 FOR UPDATE')
        def expire():
            with self.connection() as conn:
                return identities.expire_identity_appointments(conn, today='2026-09-10')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(expire)
            try:
                self._assert_native_waiting(future)
                self.conn.execute('SELECT id FROM electronic_signatures WHERE id=2 FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(2, future.result(timeout=5))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SignatureAccountsPostgresTests(name)
        for name, value in SignatureAccountsPostgresTests.__dict__.items()
        if name.startswith('test_') and callable(value))
