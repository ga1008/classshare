"""Durable settlement, real local HTTP/data; no school network or business DB."""
import json
import unittest
from unittest.mock import patch

import test_login_attendance_router as fixture
import test_login_attendance_service as service_fixture
from classroom_app.services import login_attendance_service as local
from classroom_app.services import login_attendance_settlement_service as settle
from classroom_app.services import login_attendance_jobs as jobs
from classroom_app.services import scheduled_task_service as scheduler
from classroom_app.db import schema_scheduler


class SettlementTests(unittest.TestCase):
    database = fixture.AttendanceRouterTests.database
    create = fixture.AttendanceRouterTests.create
    observe = service_fixture.LoginAttendanceServiceTests.observe

    def setUp(self):
        fixture.AttendanceRouterTests.setUp(self)
        self.conn.execute('ALTER TABLE teachers ADD COLUMN is_active INTEGER DEFAULT 1')
        self.enterContext(patch.object(schema_scheduler,'_SCHEMA_READY',False))
        schema_scheduler.ensure_scheduler_schema(self.conn); self.conn.commit()
        for module in (settle,jobs,scheduler):
            self.enterContext(patch.object(module,'get_db_connection',self.database))
        self.round = self.create(); self.observe()
        self.path = self.base+f"/rounds/{self.round['id']}/settlement"

    def get(self):
        response=self.client.get(self.path)
        self.assertEqual(200,response.status_code,response.text)
        return response.json()

    def payload(self,key='settlement-request-1',restart=None):
        view=self.get(); task=view['task']
        return {'idempotency_key':key,'expected_round_revision':view['round_revision'],
            'restart':view['gate']['can_restart'] if restart is None else restart,
            'expected_task_id':task['id'] if task else None,
            'expected_task_generation':task['generation'] if task else None}

    def request(self,**kwargs):
        response=self.client.post(self.path,headers=self.headers,json=self.payload(**kwargs))
        self.assertEqual(202,response.status_code,response.text)
        return response.json()

    def process(self,task):
        return settle.process_settlement(7,task['id'])

    def test_immediate_request_stops_window_without_grace_and_commits_real_stages(self):
        requested=self.request()
        self.assertEqual('settling',requested['round']['state'])
        self.assertEqual(self.mock_now.return_value,requested['round']['settle_at_ms'])
        self.assertEqual(self.mock_now.return_value,requested['round']['window_end_ms'])
        self.assertEqual(0,requested['task']['completed'])
        self.assertIsNone(requested['task']['percent'])
        progress=[]; real_compute=local.compute_evaluation
        def compute(prepared):
            self.assertFalse(self.conn.in_transaction)
            progress.append(self.get()['task'])
            return real_compute(prepared)
        with patch.object(local,'compute_evaluation',side_effect=compute):
            self.assertEqual('completed',self.process(requested['task'])['state'])
        self.assertEqual(('calculating',1,25),(progress[0]['stage'],progress[0]['completed'],progress[0]['percent']))
        finished=self.get(); self.assertEqual(('completed',4,100),(finished['task']['state'],finished['task']['completed'],finished['task']['percent']))
        row=local.round_view(self.conn,self.round['id'],self.user)
        self.assertEqual('published',row['state']); self.assertEqual(5,row['summary']['expected_counts']['PRESENT'])
        audit=json.loads(self.conn.execute("SELECT before_json FROM login_attendance_audit WHERE action='settlement_requested'").fetchone()[0])
        self.assertEqual('running',audit['state'])

    def test_same_key_replay_never_ends_again_or_queues_again(self):
        payload=self.payload()
        first=self.client.post(self.path,headers=self.headers,json=payload).json()
        self.process(first['task'])
        repeated=self.client.post(self.path,headers=self.headers,json=payload)
        self.assertEqual(202,repeated.status_code,repeated.text)
        self.assertEqual(first['task']['id'],repeated.json()['task']['id'])
        self.assertEqual(1,self.conn.execute('SELECT COUNT(*) FROM login_attendance_settlement_runs').fetchone()[0])
        self.assertEqual(1,self.conn.execute('SELECT COUNT(*) FROM scheduled_tasks WHERE task_kind=?',(settle.TASK_KIND,)).fetchone()[0])

    def test_lost_old_request_replay_after_restart_returns_current_without_third_run(self):
        payload=self.payload()
        original=self.client.post(self.path,headers=self.headers,json=payload).json()
        replacement=self.request(key='replacement-after-lost-response',restart=True)
        repeated=self.client.post(self.path,headers=self.headers,json=payload)
        self.assertEqual(202,repeated.status_code,repeated.text)
        self.assertEqual(replacement['task']['id'],repeated.json()['task']['id'])
        self.assertNotEqual(original['task']['id'],repeated.json()['task']['id'])
        self.assertEqual(2,self.conn.execute('SELECT COUNT(*) FROM login_attendance_settlement_runs').fetchone()[0])

    def test_restart_during_compute_supersedes_old_worker_and_its_failure(self):
        old=self.request()['task']; real_compute=local.compute_evaluation; newer=[]
        def restart(prepared):
            newer.append(self.request(key='replacement-generation',restart=True)['task'])
            return real_compute(prepared)
        with patch.object(local,'compute_evaluation',side_effect=restart):
            self.assertEqual('superseded',self.process(old)['state'])
        self.assertTrue(all(r[0]=='PENDING' for r in self.conn.execute('SELECT status FROM login_attendance_records')))
        self.assertEqual('superseded',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(old['id'],)).fetchone()[0])
        self.assertEqual('completed',self.process(newer[0])['state'])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='settlement_completed'").fetchone()[0])

    def test_old_compute_failure_does_not_mark_replacement_failed(self):
        old=self.request()['task']; newer=[]
        def replace_then_fail(prepared):
            newer.append(self.request(key='restart-old-will-fail',restart=True)['task'])
            raise RuntimeError('old abandoned calculation failed')
        with patch.object(local,'compute_evaluation',side_effect=replace_then_fail):
            with self.assertRaises(RuntimeError): self.process(old)
        current=self.get()['task']
        self.assertEqual(newer[0]['id'],current['id'])
        self.assertEqual('queued',current['state'])
        self.assertEqual('superseded',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(old['id'],)).fetchone()[0])

    def test_manual_change_during_compute_keeps_status_reason_audit_and_timestamp(self):
        task=self.request()['task']; real_compute=local.compute_evaluation
        def correct(prepared):
            self.mock_now.return_value+=30_000
            record=local._records(self.conn,self.round['id'])[0]
            local.change_records(self.conn,self.round['id'],self.user,{'records':[{'student_id':record['student_id'],'revision':record['revision'],'status':'EARLY'}],'reason':'Teacher explicitly verified early departure'})
            self.conn.commit()
            return real_compute(prepared)
        with patch.object(local,'compute_evaluation',side_effect=correct):
            self.assertEqual('completed',self.process(task)['state'])
        record=local._records(self.conn,self.round['id'])[0]
        self.assertEqual('EARLY',record['status'])
        self.assertEqual('Teacher explicitly verified early departure',record['reason'])
        self.assertEqual(self.mock_now.return_value,record['decided_at_ms'])
        self.assertEqual(self.mock_now.return_value,record['updated_at_ms'])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='manual_decision'").fetchone()[0])

    def test_failed_compute_preserves_records_and_scanner_cannot_take_over(self):
        task=self.request()['task']
        with patch.object(local,'compute_evaluation',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError): self.process(task)
        self.assertEqual('failed',self.get()['task']['state'])
        self.assertTrue(self.get()['gate']['can_restart'])
        local.evaluate_round(self.conn,self.round['id'],timestamp=self.mock_now.return_value+1_000_000); self.conn.commit()
        self.assertEqual('settling',local.load_round(self.conn,self.round['id'])['state'])
        self.assertTrue(all(r[0]=='PENDING' for r in self.conn.execute('SELECT status FROM login_attendance_records')))

    def test_same_generation_new_worker_token_prevents_duplicate_publish(self):
        task=self.request()['task']; real_compute=local.compute_evaluation; nested=[]
        def replace_worker(prepared):
            if not nested:
                nested.append(True)
                self.assertEqual('completed',self.process(task)['state'])
            return real_compute(prepared)
        with patch.object(local,'compute_evaluation',side_effect=replace_worker):
            self.assertEqual('superseded',self.process(task)['state'])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='settlement_completed'").fetchone()[0])

    def test_automatic_grace_requires_explicit_restart_without_prior_task(self):
        local.end_round(self.conn,self.round['id'],self.user,{'revision':self.round['revision']}); self.conn.commit()
        payload=self.payload(restart=False)
        response=self.client.post(self.path,headers=self.headers,json=payload)
        self.assertEqual(409,response.status_code)
        requested=self.request(restart=True)
        self.assertEqual(1,requested['task']['generation'])
        self.assertEqual('completed',self.process(requested['task'])['state'])

    def test_stale_scan_timestamp_cannot_regress_published_result(self):
        stamp=self.mock_now.return_value; task=self.request()['task']; self.process(task)
        before=local.round_view(self.conn,self.round['id'],self.user)
        local.evaluate_round(self.conn,self.round['id'],timestamp=stamp-30_000); self.conn.commit()
        after=local.round_view(self.conn,self.round['id'],self.user)
        self.assertEqual('published',after['state'])
        self.assertGreaterEqual(after['evaluated_at_ms'],before['evaluated_at_ms'])
        self.assertEqual(before['summary'],after['summary'])

    def test_void_during_compute_cancels_task_and_cannot_publish(self):
        task=self.request()['task']; real_compute=local.compute_evaluation
        def invalidate(prepared):
            row=local.load_round(self.conn,self.round['id'])
            local.void_round(self.conn,row['id'],self.user,{'revision':row['revision'],'reason':'Cancel class'}); self.conn.commit()
            return real_compute(prepared)
        with patch.object(local,'compute_evaluation',side_effect=invalidate):
            self.assertEqual('superseded',self.process(task)['state'])
        self.assertEqual('void',local.load_round(self.conn,self.round['id'])['state'])

    def test_request_requires_permissions_origin_strict_boolean_and_current_versions(self):
        payload=self.payload(); payload['restart']='false'
        self.assertEqual(422,self.client.post(self.path,headers=self.headers,json=payload).status_code)
        payload=self.payload(); payload['expected_round_revision']+=1
        self.assertEqual(409,self.client.post(self.path,headers=self.headers,json=payload).status_code)
        self.assertEqual(403,self.client.post(self.path,headers={'Origin':'https://other.test'},json=self.payload()).status_code)
        self.user={'id':8,'role':'teacher'}
        self.assertEqual(403,self.client.get(self.path).status_code)
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM login_attendance_settlement_runs').fetchone()[0])

    def test_scheduler_uses_local_deadline_lane_and_fences_cancelled_claim(self):
        requested=self.request()
        self.assertIn(settle.TASK_KIND,scheduler.DEADLINE_TASK_KINDS)
        jobs.register_handlers()
        with patch.object(scheduler,'_now',return_value=__import__('datetime').datetime.fromtimestamp(self.mock_now.return_value/1000)):
            task=scheduler._claim_due_tasks(1,lane='deadline')[0]
        self.assertEqual(settle.TASK_KIND,task['task_kind'])
        self.conn.execute("UPDATE scheduled_tasks SET status='cancelled' WHERE id=?",(task['id'],)); self.conn.commit()
        result=settle.process_settlement(7,requested['task']['id'],scheduler_task=task)
        self.assertEqual('queued',result['state'])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='settlement_completed'").fetchone()[0])

    def test_future_round_and_expired_evidence_are_gated(self):
        self.mock_now.return_value=self.start_ms-1
        self.assertEqual('not_started',self.get()['gate']['code'])
        self.mock_now.return_value=self.round['window_end_ms']+7*86400_000+1
        self.assertEqual('evidence_expired',self.get()['gate']['code'])

    def test_inactive_teacher_before_claim_persists_failed_without_changing_results(self):
        task=self.request()['task']
        self.conn.execute('UPDATE teachers SET is_active=0 WHERE id=7'); self.conn.commit()
        before=dict(self.conn.execute('SELECT * FROM login_attendance_rounds WHERE id=?',(self.round['id'],)).fetchone())
        self.assertEqual('failed',self.process(task)['state'])
        self.assertEqual('failed',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(task['id'],)).fetchone()[0])
        self.assertEqual(before,dict(self.conn.execute('SELECT * FROM login_attendance_rounds WHERE id=?',(self.round['id'],)).fetchone()))

    def test_transferred_classroom_before_claim_marks_original_receipt_failed(self):
        task=self.request()['task']
        self.conn.execute('UPDATE class_offerings SET teacher_id=8 WHERE id=10'); self.conn.commit()
        self.assertEqual('failed',self.process(task)['state'])
        self.assertEqual('failed',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(task['id'],)).fetchone()[0])
        self.assertTrue(all(r[0]=='PENDING' for r in self.conn.execute('SELECT status FROM login_attendance_records')))

    def test_wrong_teacher_or_stale_generation_cannot_mark_current_receipt_failed(self):
        old=self.request()['task']
        self.assertEqual('failed',settle.process_settlement(8,old['id'])['state'])
        self.assertEqual('queued',self.get()['task']['state'])
        new=self.request(key='inactive-old-generation',restart=True)['task']
        self.conn.execute('UPDATE teachers SET is_active=0 WHERE id=7'); self.conn.commit()
        self.process(old)
        self.assertEqual('superseded',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(old['id'],)).fetchone()[0])
        self.assertEqual('queued',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(new['id'],)).fetchone()[0])

    def test_new_recording_supersedes_settling_round_and_fences_its_worker(self):
        task=self.request()['task']; real_compute=local.compute_evaluation; replacement=[]
        def reopen(prepared):
            if not replacement:
                replacement.append(None)
                replacement[0]=local.create_round(self.conn,10,21,self.user,{'request_key':'new-recording-after-settle',
                    'networks':['192.0.2.0/24'],'supersedes_id':self.round['id']})
                self.conn.commit()
            return real_compute(prepared)
        with patch.object(local,'compute_evaluation',side_effect=reopen):
            self.assertEqual('superseded',self.process(task)['state'])
        original=local.load_round(self.conn,self.round['id'])
        self.assertEqual(0,original['is_current'])
        self.assertEqual('running',replacement[0]['state'])
        self.assertEqual('superseded',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE id=?',(task['id'],)).fetchone()[0])
        self.assertEqual('cancelled',self.conn.execute('SELECT status FROM scheduled_tasks WHERE dedupe_key=?',('attendance-settle:'+task['id'],)).fetchone()[0])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='settlement_completed'").fetchone()[0])


if __name__=='__main__':
    unittest.main()
