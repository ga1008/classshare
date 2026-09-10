"""Native signature review/cancel races using the actual migrated schema.

Only notification delivery is suppressed; actor lookup, scope rules, row locks,
request decisions, flow projection and transaction boundaries are real.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

from classroom_app.db import schema_signature_workflow
from classroom_app.services import signature_point_service as points, signature_workflow_service as workflow
from classroom_app.services.signature_service import SignatureServiceError
from classroom_app.services.signature_workflow_lock_service import lock_signature_workflows
from tests.test_agent_authority_postgres import AgentAuthorityPostgresTests


class SignatureDecisionsPostgresTests(AgentAuthorityPostgresTests):
    def setUp(self):
        super().setUp()
        for target, value in (
            ('classroom_app.db.schema_signature_workflow.get_configured_db_engine', 'postgres'),
            ('classroom_app.services.signature_workflow_service.get_configured_db_engine', 'postgres'),
            ('classroom_app.services.signature_point_service.get_configured_db_engine', 'postgres'),
        ):
            item = patch(target, return_value=value)
            item.start(); self.addCleanup(item.stop)
        for target, value in (
            ('classroom_app.db.schema_signature_workflow._SCHEMA_READY', False),
        ):
            item = patch(target, value)
            item.start(); self.addCleanup(item.stop)
        notification = patch.object(workflow, '_notify')
        notification.start(); self.addCleanup(notification.stop)
        source = Path('classroom_app/db/schema_learning_blog.py').read_text(encoding='utf-8-sig')
        for table in ('electronic_signatures','signature_usage_logs','signature_access_requests'):
            candidates = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Constant)
                and isinstance(node.value,str) and f'CREATE TABLE IF NOT EXISTS {table} (' in node.value]
            self.assertEqual(1,len(candidates))
            self.conn.execute(candidates[0].replace('INTEGER PRIMARY KEY AUTOINCREMENT','BIGSERIAL PRIMARY KEY'))
        schema_signature_workflow.ensure_signature_workflow_schema(self.conn)
        self.conn.execute("""INSERT INTO electronic_signatures(id,name,subject_name,subject_role,subject_id,
            owner_role,owner_id,owner_name_snapshot,scope_level,school_code,college,department,file_hash,stored_path)
            VALUES(1,'Synthetic signature','Other','teacher',8,'teacher',8,'Other','platform','A','C','D',?,'synthetic.png')""", ('a'*64,))
        self.conn.execute("""CREATE TABLE material_ai_import_records(id BIGINT PRIMARY KEY,teacher_id BIGINT,
            signature_revision TEXT,source_file_hash TEXT,parse_status TEXT,document_type TEXT,export_payload_json TEXT);
            INSERT INTO material_ai_import_records VALUES(88,7,'revision','hash','completed','academic_exam_analysis','{}')""")
        self.seed(1)

    def seed(self, identifier):
        point = 'academic_final_material.exam_analysis.' + ('department_review_signature' if identifier==1 else 'dean_review_signature')
        self.conn.execute("""INSERT INTO signature_point_flows(id,requester_role,requester_id,function_point_key,
            material_type,material_id,material_revision,status,apply_status) VALUES(?,'teacher',7,?,'academic_final_material','88','revision','pending','manual')""", (identifier,point))
        self.conn.execute("""INSERT INTO signature_access_requests(id,signature_id,requester_teacher_id,requester_role,requester_id,
            owner_role,owner_id,flow_id,function_point_key,material_type,material_id,material_revision)
            VALUES(?,1,7,'teacher',7,'teacher',8,?,?,'academic_final_material','88','revision')""", (identifier,identifier,point))
        self.conn.execute("""INSERT INTO signature_access_request_reviewers(request_id,reviewer_role,reviewer_id,reviewer_kind,reviewer_name_snapshot)
            VALUES(?,'teacher',8,'owner','Other')""", (identifier,))
        self.conn.execute("""INSERT INTO signature_access_request_items(request_id,function_point_key,function_point_label_snapshot)
            VALUES(?,?,'Synthetic point')""", (identifier,point))
        self.conn.execute("""INSERT INTO signature_point_flow_items(flow_id,signature_id,request_id,display_order,status)
            VALUES(?,1,?,0,'pending')""", (identifier,identifier))
        self.conn.commit()

    def call(self, conn, action, identifier=1):
        if action=='approve':
            return workflow.review_access_request(conn, {'role':'teacher','id':8}, identifier, action='approve')
        if action=='cancel':
            return workflow.cancel_access_request(conn, {'role':'teacher','id':7}, identifier)
        return points.end_point_flow(conn, {'role':'teacher','id':7}, identifier)

    def competing(self, action, identifier=1):
        with self.connection() as conn:
            try:
                self.call(conn,action,identifier)
                return 200
            except SignatureServiceError as exc:
                conn.rollback()
                return exc.status_code

    def ordered_race(self, first, second, expected):
        self.call(self.conn,first)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.competing,second)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(expected,future.result(timeout=5))

    def test_approval_then_cancel_is_conflict_and_keeps_approved_children(self):
        self.ordered_race('approve','cancel',409)
        row=self.conn.execute('SELECT status FROM signature_access_requests WHERE id=1').fetchone()
        self.assertEqual('approved',row[0])
        self.assertEqual('available',self.conn.execute('SELECT status FROM signature_access_request_items WHERE request_id=1').fetchone()[0])

    def test_cancel_then_approval_is_conflict_and_cannot_grant_use(self):
        self.ordered_race('cancel','approve',409)
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM signature_access_requests WHERE id=1').fetchone()[0])
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM signature_access_request_items WHERE request_id=1').fetchone()[0])

    def test_end_flow_then_approval_is_conflict_without_deadlock(self):
        self.ordered_race('end','approve',409)
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM signature_point_flows WHERE id=1').fetchone()[0])

    def test_applied_flow_cannot_be_overwritten_by_stale_end(self):
        self.conn.execute("UPDATE signature_point_flows SET status='approved',apply_status='applied' WHERE id=1")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.competing,'end')
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(409,future.result(timeout=5))
        self.assertEqual('applied',self.conn.execute('SELECT apply_status FROM signature_point_flows WHERE id=1').fetchone()[0])

    def test_material_lock_precedes_flow_and_request_locks(self):
        self.conn.execute("""INSERT INTO signature_material_snapshots(id,material_type,material_id,material_revision,document_type,
            owner_role,owner_id,title,content_fingerprint,payload_json,artifact_json)
            VALUES('snapshot','academic_final_material','88','revision','academic_exam_analysis','teacher',7,'Synthetic','fingerprint','{}','{}')""")
        self.conn.execute("UPDATE signature_access_requests SET snapshot_id='snapshot' WHERE id=1")
        self.conn.execute("UPDATE signature_point_flows SET snapshot_id='snapshot' WHERE id=1")
        self.conn.commit()
        self.conn.execute('UPDATE material_ai_import_records SET id=id WHERE id=88')
        def lock():
            with self.connection() as conn:
                lock_signature_workflows(conn,request_ids=[1])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(lock)
            try:
                self._assert_native_waiting(future)
                # If the other connection had acquired either row before the
                # material, these NOWAIT locks would fail instead of passing.
                self.conn.execute('SELECT id FROM signature_point_flows WHERE id=1 FOR UPDATE NOWAIT')
                self.conn.execute('SELECT id FROM signature_access_requests WHERE id=1 FOR UPDATE NOWAIT')
                self.conn.commit()
            finally:
                self.conn.rollback()
            future.result(timeout=5)

    def test_reverse_batch_order_serializes_and_reports_already_finished_requests(self):
        self.seed(2)
        first=workflow.batch_review_access_requests(self.conn,{'role':'teacher','id':8},[2,1],action='reject')
        self.assertEqual(2,first['processed'])
        def other():
            with self.connection() as conn:
                return workflow.batch_review_access_requests(conn,{'role':'teacher','id':8},[1,2],action='reject')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(other)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            result=future.result(timeout=5)
        self.assertEqual((0,2),(result['processed'],result['failed']))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SignatureDecisionsPostgresTests(name)
        for name,value in SignatureDecisionsPostgresTests.__dict__.items()
        if name.startswith('test_') and callable(value))
