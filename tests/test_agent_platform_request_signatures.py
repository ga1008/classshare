"""Normal signature HTTP policies and real SQL, without replacing permissions."""
import ast
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid
from fastapi import HTTPException

from classroom_app.db import schema_signature_workflow
from classroom_app.routers import signatures
from classroom_app.services import agent_platform_request_registry as registry
from classroom_app.services.agent_platform_request_signatures import build_capabilities
from tests.test_agent_platform_request_learning import LearningRequestFixture


class SignatureRequestFixture(LearningRequestFixture):
    def setUp(self):
        super().setUp()
        self.patched('classroom_app.routers.signatures.get_db_connection',self.connection)
        self.patched('classroom_app.db.schema_signature_workflow._SCHEMA_READY',False)
        self.patched('classroom_app.db.schema_signature_workflow.get_configured_db_engine',return_value='sqlite')
        self.patched('classroom_app.services.signature_workflow_service.get_configured_db_engine',return_value='sqlite')
        self.patched('classroom_app.services.organization_management_service.get_configured_db_engine',return_value='sqlite')
        source=Path('classroom_app/db/schema_learning_blog.py').read_text(encoding='utf-8-sig')
        with self.connection() as conn:
            for table in ('electronic_signatures','signature_usage_logs','signature_access_requests'):
                candidates=[node.value for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Constant)
                    and isinstance(node.value,str) and f'CREATE TABLE IF NOT EXISTS {table} (' in node.value]
                self.assertEqual(1,len(candidates))
                conn.execute(candidates[0])
            for table in ('classes','courses','blog_posts','academic_semesters'):
                for column in ('school_code','school_name','college','department'):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT DEFAULT ''")
            conn.execute("ALTER TABLE teachers ADD COLUMN email TEXT DEFAULT ''")
            conn.executescript("""
                CREATE TABLE organization_schools(id INTEGER PRIMARY KEY,school_code TEXT,school_name TEXT,
                    display_order INTEGER DEFAULT 0,is_active INTEGER DEFAULT 1,source TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',updated_at TEXT DEFAULT '',deactivated_at TEXT DEFAULT '');
                INSERT INTO organization_schools(id,school_code,school_name) VALUES(1,'a','School A'),(2,'b','School B');
                UPDATE classes SET school_code='A',school_name='School A',college='C',department='D' WHERE id=30;
            """)
            schema_signature_workflow.ensure_signature_workflow_schema(conn)
            for identifier,owner_role,owner_id,scope,school in ((1,'teacher',8,'platform','A'),(2,'student',7,'personal','A'),(3,'teacher',8,'personal','B')):
                conn.execute('''INSERT INTO electronic_signatures(id,name,subject_name,subject_role,subject_id,
                    owner_role,owner_id,owner_name_snapshot,scope_level,school_code,college,department,file_hash,stored_path)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identifier,f'Signature {identifier}','Synthetic signer',owner_role,owner_id,owner_role,owner_id,'Synthetic owner',scope,school,'C','D','a'*64,'private-not-exposed.png'))
            for identifier,role,owner in ((1,'student',7),(2,'teacher',7)):
                conn.execute('''INSERT INTO signature_access_requests(id,signature_id,requester_teacher_id,requester_role,requester_id,owner_role,owner_id)
                    VALUES(?,1,?,?,?,'teacher',8)''',(identifier,owner if role=='teacher' else None,role,owner))
                conn.execute('''INSERT INTO signature_access_request_reviewers(request_id,reviewer_role,reviewer_id,reviewer_kind,reviewer_name_snapshot)
                    VALUES(?,'teacher',8,'owner','Other teacher')''',(identifier,))
            conn.execute("INSERT INTO signature_usage_logs(signature_id,actor_role,actor_id,action,context_label) VALUES(2,'teacher',8,'use','Own signature usage')")
            conn.commit()
        self.app.include_router(signatures.router)


class AgentSignatureRequestTests(SignatureRequestFixture):
    def test_visibility_and_direct_use_remain_separate_with_normal_http_parity(self):
        for role in ('student','teacher','other','peer'):
            observed=self.restored_parity(role,'http.signatures.list',query_params={'limit':50})
            self.assertEqual(200,observed['result']['http_status'])
            items={item['id']:item for item in observed['result']['data']['items']}
            if role=='student':
                self.assertEqual({1,2},set(items))
                self.assertFalse(items[1]['can_use'])
                self.assertFalse(items[1]['can_request_use'])  # The existing pending request blocks a duplicate.
                self.assertTrue(items[2]['can_use'])
            self.assertNotIn('private-not-exposed.png',str(items))

    def test_school_teacher_and_function_point_options_use_actual_actor_scope(self):
        for key in ('http.signatures.schools','http.signatures.teachers','http.signatures.function_points'):
            for role in ('student','teacher','other'):
                with self.subTest(key=key,role=role):
                    observed=self.restored_parity(role,key)
                    self.assertEqual(200,observed['result']['http_status'])
        denied=self.restored_parity('student','http.signatures.teachers',query_params={'school_code':'b'})
        self.assertEqual(403,denied['result']['http_status'])

    def test_requests_list_and_int_converter_detail_preserve_requester_reviewer_admin_boundaries(self):
        for role,own_ids in (('student',[1]),('teacher',[2]),('peer',[])):
            observed=self.restored_parity(role,'http.signatures.requests.list',query_params={'direction':'outgoing'})
            self.assertEqual(200,observed['result']['http_status'])
            self.assertEqual(own_ids,[item['id'] for item in observed['result']['data']['items']])
        for role,status in (('student',200),('other',200),('teacher',200),('peer',404)):
            detail=self.restored_parity(role,'http.signatures.request.detail',path_params={'request_id':1})
            self.assertEqual(status,detail['result']['http_status'])
            self.assertFalse(detail['result']['verified_business'])
        other=self.restored_parity('student','http.signatures.request.detail',path_params={'request_id':2})
        self.assertEqual(404,other['result']['http_status'])

    def test_usage_trail_does_not_cross_same_numbered_signature_owner_roles(self):
        for role,count in (('student',1),('teacher',0),('other',0)):
            observed=self.restored_parity(role,'http.signatures.usage')
            self.assertEqual(200,observed['result']['http_status'])
            self.assertEqual(count,len(observed['result']['data']['items']))

    def test_only_reviewed_capabilities_are_registered_with_bounded_arguments(self):
        operations=build_capabilities(registry.RequestCapability,registry._spec,registry.ID)
        self.assertEqual(11,len(operations))
        self.assertEqual({'http.signatures.flow.create','http.signatures.flow.end','http.signatures.request.cancel'},
                         {item.key for item in operations if item.mutates})
        for operation in operations:
            self.assertEqual(operation,registry.resolve_capability(self.app,operation.key)[0])
        operation=next(op for op in operations if op.key=='http.signatures.request.detail')
        self.assertEqual('/api/signatures/requests/12',registry.arguments(operation,path_params={'request_id':12})[0])
        for invalid in ({'request_id':True},{'request_id':'../../image/1'},{'request_id':-1}):
            with self.assertRaises(HTTPException) as raised:registry.arguments(operation,path_params=invalid)
            self.assertEqual(400,raised.exception.status_code)

    def test_unknown_or_error_collection_shapes_remain_unverified(self):
        import httpx
        from classroom_app.services.agent_platform_request_service import _observation
        operation=next(op for op in registry.CAPABILITIES if op.key=='http.signatures.usage')
        for payload in ({'items':[]}, {'items':'wrong','actor':{'role':'student','id':7}},
                        {'items':[],'actor':{'role':'student','id':7},'status':'error'},
                        {'items':[],'actor':{'role':'student','id':7},'status':{}}):
            response=httpx.Response(200,json=payload)
            state,result=_observation(response,operation)
            self.assertEqual('uncertain',state)
            self.assertFalse(result['verified_business'])


class SignatureFlowFixture(SignatureRequestFixture):
    """Actual snapshot/permission/request SQL; only document rendering and mail delivery substituted.

    A valid synthetic Word artifact isolates these broker/workflow tests from
    unrelated native school-template rendering, which remains in its own suite.
    """
    point='academic_final_material.exam_analysis.department_review_signature'

    def setUp(self):
        super().setUp()
        from docx import Document
        from PIL import Image
        self.data_root=Path(self.path).parent/'signature-data'
        self.data_root.mkdir()
        self.patched('classroom_app.services.material_signature_service.DATA_DIR',self.data_root)
        self.patched('classroom_app.services.material_signature_service.get_db_connection',self.connection)
        self.patched('classroom_app.services.signature_point_service.get_configured_db_engine',return_value='sqlite')
        self.patched('classroom_app.services.message_center_service.queue_notification_email_if_applicable')
        document=Document();document.add_paragraph('Synthetic review document')
        buffer=BytesIO();document.save(buffer)
        self.document=buffer.getvalue()
        self.patched('classroom_app.services.material_signature_service.build_document_artifact',return_value=SimpleNamespace(
            content=self.document,filename='Synthetic.docx',media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
        picture=self.data_root/'signature.png'
        Image.new('RGB',(8,8),'black').save(picture)
        self.sql('UPDATE electronic_signatures SET stored_path=?,file_hash=? WHERE id=1',
            (str(picture),hashlib.sha256(picture.read_bytes()).hexdigest()))
        with self.connection() as conn:
            conn.executescript("""CREATE TABLE material_ai_import_records(id INTEGER PRIMARY KEY,teacher_id INTEGER,
                source_file_hash TEXT,signature_revision TEXT,document_type TEXT,document_type_label TEXT,
                export_payload_json TEXT,parse_status TEXT);
                INSERT INTO material_ai_import_records VALUES(88,7,'sourcehash','revision-a','academic_exam_analysis','Exam analysis',
                    '{"fields":{"course_name":"Synthetic course","class_name":"Synthetic class"}}','completed');""")
            conn.commit()

    def flow(self, role='teacher', **changes):
        body={'material_type':'academic_final_material','material_id':'88','expected_revision':'revision-a',
              'signature_ids':[1],'note':'Please review the current synthetic document'}
        body.update(changes)
        return self.dispatch(role,'http.signatures.flow.create',path_params={'function_point_key':self.point},body=body)


class AgentSignatureFlowTests(SignatureFlowFixture):
    def test_normal_and_delegated_create_freeze_document_without_applying_or_using_signature(self):
        snapshot=sqlite3.connect(':memory:')
        with self.connection() as conn:conn.backup(snapshot)
        try:
            normal=self.web('teacher','POST',f'/api/signatures/points/{self.point}/flows',json={
                'material_type':'academic_final_material','material_id':'88','expected_revision':'revision-a','signature_ids':[1]})
            self.assertEqual(200,normal.status_code,normal.text)
            normal_flow=normal.json()['flow']
            with self.connection() as conn:snapshot.backup(conn)
        finally:
            snapshot.close()
        result=self.flow()
        self.assertEqual(200,result['result']['http_status'],result)
        flow=result['result']['data']['flow']
        for key in ('status','snapshot_id','apply_status','material_id','material_revision'):
            self.assertEqual(normal_flow[key],flow[key])
        self.assertEqual('manual',flow['apply_status'])
        self.assertFalse(result['verified_business'])
        self.assertEqual(1,self.sql('SELECT COUNT(*) FROM signature_material_snapshots')[0][0])
        files=list(self.data_root.rglob('*.docx'))
        self.assertEqual(1,len(files));self.assertEqual(self.document,files[0].read_bytes())
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM signature_point_bindings')[0][0])
        self.assertEqual(1,self.sql('SELECT COUNT(*) FROM signature_usage_logs')[0][0])  # Only the original fixture log.
        self.assertGreater(self.sql("SELECT COUNT(*) FROM message_center_notifications WHERE category='signature_workflow'")[0][0],0)

    def test_material_state_then_request_cancel_and_flow_end_preserve_owner_checks(self):
        result=self.restored_parity('teacher','http.signatures.point.state',path_params={'function_point_key':self.point},
            query_params={'material_type':'academic_final_material','material_id':'88'})
        self.assertEqual('revision-a',result['result']['data']['material']['revision'])
        created=self.flow()
        self.assertEqual(200,created['result']['http_status'],created)
        flow=created['result']['data']['flow']
        request_id=flow['items'][0]['request_id']
        for role in ('student','other'):
            denied=self.restored_parity(role,'http.signatures.request.cancel',path_params={'request_id':request_id})
            self.assertEqual(403,denied['result']['http_status'])
        canceled=self.restored_parity('teacher','http.signatures.request.cancel',path_params={'request_id':request_id})
        self.assertEqual(200,canceled['result']['http_status'])
        self.assertEqual('cancelled',canceled['result']['data']['request']['status'])
        created=self.flow()
        self.assertEqual(200,created['result']['http_status'],created)
        flow_id=created['result']['data']['flow']['id']
        ended=self.restored_parity('teacher','http.signatures.flow.end',path_params={'flow_id':flow_id})
        self.assertEqual(200,ended['result']['http_status'])
        self.assertEqual('cancelled',self.sql('SELECT status FROM signature_point_flows WHERE id=?',(flow_id,))[0][0])
        self.assertEqual(409,self.dispatch('teacher','http.signatures.flow.end',path_params={'flow_id':flow_id})['result']['http_status'])

    def test_creation_requires_current_revision_owner_and_explicit_bounded_parameters(self):
        for role in ('student','other'):
            denied=self.flow(role)
            self.assertEqual(403,denied['result']['http_status'])
        self.assertEqual(409,self.flow(expected_revision='older')['result']['http_status'])
        for changes in ({'auto_apply':True},{'opinion_mode':'stamp'},{'signature_ids':[True]},
                        {'signature_ids':list(range(1,14))},{'expected_revision':''}):
            with self.subTest(changes=changes),self.assertRaises(HTTPException):
                self.flow(**changes)
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM signature_point_flows')[0][0])

    def test_duplicate_and_replay_do_not_create_duplicate_requests_or_notifications(self):
        operation_id=str(uuid.uuid4())
        kwargs={'path_params':{'function_point_key':self.point},'body':{'material_type':'academic_final_material',
            'material_id':'88','expected_revision':'revision-a','signature_ids':[1]}}
        first=self.dispatch('teacher','http.signatures.flow.create',operation_id=operation_id,**kwargs)
        self.assertEqual(200,first['result']['http_status'],first)
        notification_count=self.sql('SELECT COUNT(*) FROM message_center_notifications')[0][0]
        replay=self.dispatch('teacher','http.signatures.flow.create',operation_id=operation_id,**kwargs)
        self.assertEqual(first['result'],replay['result'])
        self.assertEqual(409,self.flow()['result']['http_status'])
        self.assertEqual(1,self.sql('SELECT COUNT(*) FROM signature_point_flows')[0][0])
        self.assertEqual(notification_count,self.sql('SELECT COUNT(*) FROM message_center_notifications')[0][0])

    def test_material_changed_while_rendering_is_rejected_before_pinning_or_requesting(self):
        def render_then_change(*args,**kwargs):
            self.sql("UPDATE material_ai_import_records SET signature_revision='revision-b' WHERE id=88")
            return SimpleNamespace(content=self.document,filename='Synthetic.docx',
                media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.patched('classroom_app.services.material_signature_service.build_document_artifact',side_effect=render_then_change)
        result=self.flow()
        self.assertEqual(409,result['result']['http_status'])
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM signature_material_snapshots')[0][0])
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM signature_point_flows')[0][0])
        self.assertEqual(0,self.sql("SELECT COUNT(*) FROM message_center_notifications WHERE category='signature_workflow'")[0][0])
