"""Real proposal HTTP, signature policies, frozen artifacts, SQL and receipts.

Only document rendering and mail delivery use the existing synthetic fixture.
These checks do not replace the native concurrent signature lock suite.
"""
import ast
import json
from pathlib import Path

from classroom_app.db.schema_agent_ext import ensure_agent_task_extension_schema
from classroom_app.routers import agent_tasks
from classroom_app.services import agent_platform_write_service as writes
from classroom_app.services.agent_user_confirmation_actions import user_confirmation_action_catalog
from tests.test_agent_platform_request_signatures import SignatureFlowFixture


class AgentSignatureConfirmationHTTPTests(SignatureFlowFixture):
    def setUp(self):
        super().setUp()
        source = Path('classroom_app/db/schema_classroom_activity.py').read_text(encoding='utf-8-sig')
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM agent_tasks')]
            conn.execute('DROP TABLE agent_tasks')
            for table in ('agent_tasks', 'agent_task_events', 'agent_task_composers'):
                statements = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)
                    and isinstance(node.value, str) and f'CREATE TABLE IF NOT EXISTS {table}\n' in node.value]
                self.assertEqual(1, len(statements))
                conn.execute(statements[0])
            ensure_agent_task_extension_schema(conn, force=True, engine='sqlite')
            for row in rows:
                data = {**row, 'task_uuid':f"synthetic-{row['id']}", 'task_type':'general',
                    'title':'Synthetic signature review', 'private_instruction':'Review my signature request'}
                columns = ','.join(data)
                conn.execute(f"INSERT INTO agent_tasks({columns}) VALUES({','.join('?' for _ in data)})",tuple(data.values()))
            conn.commit()
        self.patched('classroom_app.routers.agent_tasks.get_db_connection', self.connection)
        self.propose()
        self.actor = {'role':'teacher', 'id':8, 'session_id':'other-session'}
        self.app.dependency_overrides[agent_tasks.get_current_user] = lambda:self.actor
        self.app.include_router(agent_tasks.router)

    def propose(self, task_id=12):
        created = self.flow()
        self.assertEqual(200, created['result']['http_status'], created)
        self.flow_id = created['result']['data']['flow']['id']
        self.request_id = created['result']['data']['flow']['items'][0]['request_id']
        self.sql("UPDATE agent_tasks SET status='failed',result_detail_json=? WHERE id=?",(json.dumps({
            'proposed_actions':[{'action':'review_signature_request','params':{'request_id':self.request_id}}]}),task_id))
        self.url = f'/api/agent-tasks/{task_id}/actions/0'

    def preview(self):
        response = self.client.post(self.url+'/preview',json={})
        self.assertEqual(200,response.status_code,response.text)
        return response.json()

    def declaration(self, preview=None, decision='approve', note='Synthetic user reviewed the frozen document'):
        value = preview or self.preview()
        return {'params':value['params'],'confirmation_token':value['confirmation_token'],
            'confirmation_inputs':{'decision':decision,'accepted_warning_codes':[
                item['code'] for item in value['confirmation_review']['warnings']], 'confirmation_note':note}}

    def status(self):
        return self.sql('SELECT status FROM signature_access_requests WHERE id=?',(self.request_id,))[0][0]

    def receipt_count(self):
        return self.sql('SELECT COUNT(*) FROM agent_action_executions')[0][0]

    def test_human_catalog_and_model_cannot_choose_or_execute_approval(self):
        for role in ('teacher','student'):
            definition = next(item for item in user_confirmation_action_catalog(actor_role=role) if item['action']=='review_signature_request')
            self.assertFalse(definition['executable'])
            self.assertNotIn('decision', definition['fields'])
        self.assertNotIn('review_signature_request',writes.TRANSACTIONAL_ACTIONS)
        self.assertEqual(400,self.client.post(self.url+'/preview',json={'params':{'decision':'approve'}}).status_code)
        request = self.declaration()
        request.pop('confirmation_inputs')
        self.assertEqual(400,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertEqual('pending',self.status())

    def test_approval_replay_has_one_receipt_without_applying_signature(self):
        request = self.declaration()
        result = self.client.post(self.url+'/execute',json=request)
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('approved',self.status())
        self.assertEqual('authenticated_user',result.json()['result']['confirmation_source'])
        self.assertTrue(self.client.post(self.url+'/execute',json=request).json()['replayed'])
        self.assertEqual(1,self.receipt_count())
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM signature_point_bindings')[0][0])
        self.assertEqual('manual',self.sql('SELECT apply_status FROM signature_point_flows WHERE id=?',(self.flow_id,))[0][0])
        changed = {**request,'confirmation_inputs':{**request['confirmation_inputs'],'decision':'reject'}}
        self.assertEqual(409,self.client.post(self.url+'/execute',json=changed).status_code)

    def test_rejection_needs_note_and_records_actual_reviewer(self):
        request = self.declaration(decision='reject',note='')
        self.assertEqual(400,self.client.post(self.url+'/execute',json=request).status_code)
        request['confirmation_inputs']['confirmation_note']='Material needs correction'
        result = self.client.post(self.url+'/execute',json=request)
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('rejected',self.status())
        reviewer = self.sql('SELECT reviewer_role,reviewer_id,review_note FROM signature_access_request_reviewers WHERE request_id=?',(self.request_id,))[0]
        self.assertEqual(('teacher',8,'Material needs correction'),tuple(reviewer))

    def test_material_change_invalidates_old_confirmation_and_new_review_only_allows_rejection(self):
        old = self.declaration()
        self.sql("UPDATE material_ai_import_records SET signature_revision='revision-b' WHERE id=88")
        self.assertEqual(409,self.client.post(self.url+'/execute',json=old).status_code)
        current = self.preview()
        self.assertFalse(current['confirmation_review']['approve_allowed'])
        self.assertNotEqual(old['params']['expected_review_hash'],current['params']['expected_review_hash'])
        self.assertEqual(409,self.client.post(self.url+'/execute',json=self.declaration(current)).status_code)
        result = self.client.post(self.url+'/execute',json=self.declaration(current,decision='reject'))
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('rejected',self.status())

    def test_replaced_signature_can_be_rejected_but_cannot_be_approved(self):
        self.sql("UPDATE electronic_signatures SET file_hash=? WHERE id=1",('b'*64,))
        current = self.preview()
        self.assertFalse(current['confirmation_review']['approve_allowed'])
        result = self.client.post(self.url+'/execute',json=self.declaration(current,decision='reject'))
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('rejected',self.status())

    def test_one_reviewer_rejection_does_not_claim_the_whole_request_has_ended(self):
        self.sql("""INSERT INTO signature_access_request_reviewers(request_id,reviewer_role,reviewer_id,reviewer_kind,reviewer_name_snapshot)
            VALUES(?,'teacher',7,'admin','Synthetic second reviewer')""",(self.request_id,))
        current = self.preview()
        self.assertIn('other_reviewers_pending',[item['code'] for item in current['confirmation_review']['warnings']])
        result = self.client.post(self.url+'/execute',json=self.declaration(current,decision='reject'))
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('pending',self.status())
        self.assertEqual('pending',result.json()['result']['request']['status'])
        self.assertIn('仍待其他审批人处理',result.json()['result']['label'])

    def test_admin_override_is_explicit_and_demotion_invalidates_authority(self):
        self.actor = {'role':'teacher','id':7,'session_id':'teacher-session'}
        self.sql("UPDATE agent_tasks SET status='failed',result_detail_json=(SELECT result_detail_json FROM agent_tasks WHERE id=12) WHERE id=10")
        self.url = '/api/agent-tasks/10/actions/0'
        current = self.preview()
        self.assertIn('admin_override',[item['code'] for item in current['confirmation_review']['warnings']])
        request = self.declaration(current)
        missing = {**request,'confirmation_inputs':{**request['confirmation_inputs'],'accepted_warning_codes':[]}}
        denied = self.client.post(self.url+'/execute',json=missing)
        self.assertEqual(400,denied.status_code,denied.text)
        self.sql('UPDATE teachers SET is_super_admin=0 WHERE id=7')
        self.assertEqual(403,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertEqual('pending',self.status())
        self.assertEqual(0,self.receipt_count())

    def test_claim_requests_and_unsnapshotted_legacy_requests_are_not_material_approvals(self):
        self.sql("UPDATE signature_access_requests SET request_kind='claim' WHERE id=?",(self.request_id,))
        self.assertEqual(400,self.client.post(self.url+'/preview',json={}).status_code)
        self.sql("UPDATE signature_access_requests SET request_kind='use',snapshot_id='' WHERE id=?",(self.request_id,))
        self.assertEqual(409,self.client.post(self.url+'/preview',json={}).status_code)
        self.assertEqual(0,self.receipt_count())

    def test_authorized_student_reviewer_uses_their_own_role_and_session(self):
        self.sql("UPDATE electronic_signatures SET owner_role='student',owner_id=7,subject_role='student',subject_id=7 WHERE id=1")
        self.sql("UPDATE signature_access_requests SET owner_role='student',owner_id=7 WHERE id=?",(self.request_id,))
        self.sql("UPDATE signature_access_request_reviewers SET reviewer_role='student',reviewer_id=7 WHERE request_id=?",(self.request_id,))
        self.sql("UPDATE agent_tasks SET status='failed',result_detail_json=(SELECT result_detail_json FROM agent_tasks WHERE id=12) WHERE id=11")
        self.actor = {'role':'student','id':7,'session_id':'student-session'}
        self.url = '/api/agent-tasks/11/actions/0'
        result = self.client.post(self.url+'/execute',json=self.declaration())
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('approved',self.status())
        receipt = self.sql('SELECT actor_role,actor_id FROM agent_action_executions')[0]
        self.assertEqual(('student',7),tuple(receipt))

    def test_explicit_admin_override_records_admin_without_impersonating_owner(self):
        self.actor = {'role':'teacher','id':7,'session_id':'teacher-session'}
        self.sql("UPDATE agent_tasks SET status='failed',result_detail_json=(SELECT result_detail_json FROM agent_tasks WHERE id=12) WHERE id=10")
        self.url = '/api/agent-tasks/10/actions/0'
        result = self.client.post(self.url+'/execute',json=self.declaration())
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual('approved',self.status())
        reviewers = self.sql('SELECT reviewer_role,reviewer_id,status FROM signature_access_request_reviewers WHERE request_id=? ORDER BY reviewer_id',(self.request_id,))
        self.assertIn(('teacher',7,'approved'),[tuple(row) for row in reviewers])

    def test_invalid_declarations_and_cancelled_request_never_mutate_or_leave_receipts(self):
        request = self.declaration()
        for inputs in ({**request['confirmation_inputs'],'decision':['approve']},
                       {**request['confirmation_inputs'],'accepted_warning_codes':[{}]},
                       {**request['confirmation_inputs'],'confirmation_note':'x'*301},
                       {**request['confirmation_inputs'],'confirmed':True}):
            self.assertEqual(400,self.client.post(self.url+'/execute',json={**request,'confirmation_inputs':inputs}).status_code)
        self.sql("UPDATE signature_access_requests SET status='cancelled' WHERE id=?",(self.request_id,))
        self.assertEqual(409,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertEqual('cancelled',self.status())
        self.assertEqual(0,self.receipt_count())

    def test_receipt_failure_rolls_back_approval_notifications_and_proposal(self):
        request = self.declaration()
        count = self.sql('SELECT COUNT(*) FROM message_center_notifications')[0][0]
        from unittest.mock import patch
        with patch('classroom_app.services.agent_operation_service.complete_user_agent_operation',side_effect=RuntimeError('Synthetic receipt failure')):
            with self.assertRaisesRegex(RuntimeError,'Synthetic receipt failure'):
                self.client.post(self.url+'/execute',json=request)
        self.assertEqual('pending',self.status())
        self.assertEqual(count,self.sql('SELECT COUNT(*) FROM message_center_notifications')[0][0])
        self.assertEqual(0,self.receipt_count())

    def test_revoked_session_and_other_same_numbered_actor_cannot_confirm(self):
        request = self.declaration()
        self.actor = {'role':'student','id':7,'session_id':'student-session'}
        self.assertIn(self.client.post(self.url+'/execute',json=request).status_code,(403,404))
        self.actor = {'role':'teacher','id':8,'session_id':'other-session'}
        self.sql("DELETE FROM user_sessions WHERE session_user_key='teacher:8'")
        self.assertEqual(401,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertEqual('pending',self.status())
        self.assertEqual(0,self.receipt_count())
