"""Server-window attendance; synthetic local HTTP/DB only, no school calls."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

import test_login_attendance_router as fixture
from classroom_app.db import schema_scheduler
from classroom_app.services import login_attendance_service as local
from classroom_app.services import login_attendance_settlement_service as settlement
from classroom_app.services.login_attendance_access_service import record_access


class ActualWindowTests(unittest.TestCase):
    database = fixture.AttendanceRouterTests.database

    def setUp(self):
        fixture.AttendanceRouterTests.setUp(self)
        self.conn.execute('ALTER TABLE teachers ADD COLUMN is_active INTEGER DEFAULT 1')
        self.enterContext(patch.object(schema_scheduler,'_SCHEMA_READY',False))
        schema_scheduler.ensure_scheduler_schema(self.conn)
        self.enterContext(patch.object(settlement,'get_db_connection',self.database))
        self.conn.commit()

    def create(self, **payload):
        response = self.client.post(self.base+'/sessions/21/rounds',headers=self.headers,
            json={'request_key':'actual-window-request','duration_minutes':90,**payload})
        self.assertEqual(200,response.status_code,response.text)
        return response.json()

    def access(self, sid, stamp, ip='203.0.113.9'):
        record_access(self.conn,student_id=sid,ip_address=ip,source='authenticated_request',observed_at_ms=stamp)
        self.conn.commit()

    def evaluate(self, rnd, stamp=None):
        self.mock_now.return_value=stamp or rnd['settle_at_ms']
        local.evaluate_round(self.conn,rnd['id']); self.conn.commit()
        return local.round_view(self.conn,rnd['id'],self.user)

    def test_cross_day_start_uses_server_now_and_preserves_original_lesson_identity(self):
        now=self.start_ms+4*86400_000+123
        self.mock_now.return_value=now
        rnd=self.create(duration_minutes=70)
        self.assertEqual(now,rnd['starts_at_ms'])
        self.assertEqual(now,rnd['window_start_ms'])
        self.assertEqual(now+70*60000,rnd['ends_at_ms'])
        self.assertEqual(rnd['ends_at_ms'],rnd['window_end_ms'])
        self.assertEqual('2026-11-08',rnd['context']['session_date'])
        self.assertEqual('2,3',rnd['context']['section_text'])
        self.assertEqual('server_actual',rnd['context']['time_source'])
        self.assertFalse(rnd['policy']['enforce_network'])
        self.assertEqual([],rnd['policy']['networks'])
        self.assertEqual(now,rnd['server_now_ms'])
        self.assertFalse(rnd['needs_rule_upgrade'])
        self.access(1,now+1000)
        self.assertEqual('PRESENT',self.evaluate(rnd)['records'][0]['status'])

    def test_individual_access_is_positive_without_majority_and_before_settlement(self):
        rnd=self.create(); self.access(1,rnd['starts_at_ms']+1000)
        result=self.evaluate(rnd,rnd['starts_at_ms']+2000)
        self.assertEqual('running',result['state'])
        self.assertEqual('PRESENT',result['records'][0]['status'])
        self.assertEqual(4,result['summary']['expected_counts']['PENDING'])
        self.assertEqual('not_required',result['baseline']['state'])

    def test_scattered_access_never_needs_a_common_twenty_minute_peak(self):
        rnd=self.create(duration_minutes=180)
        for sid in range(1,6):
            self.access(sid,rnd['starts_at_ms']+sid*25*60000)
        result=self.evaluate(rnd)
        self.assertEqual(5,result['summary']['expected_counts']['PRESENT'])

    def test_network_on_requires_matching_server_address_and_freezes_policy(self):
        rnd=self.create(enforce_network=True,networks=['192.0.2.10/24'])
        self.access(1,rnd['starts_at_ms']+1000,'192.0.2.33')
        self.access(2,rnd['starts_at_ms']+1000,'198.51.100.33')
        self.access(3,rnd['starts_at_ms']+1000,'')
        policy=local.load_policy(self.conn,10)
        local.save_policy(self.conn,10,self.user,{**policy,'enforce_network':False,'networks':[]});self.conn.commit()
        result=self.evaluate(rnd)
        self.assertTrue(result['policy']['enforce_network'])
        self.assertEqual(['PRESENT','PENDING','PENDING'],[r['status'] for r in result['records'][:3]])

    def test_unknown_address_can_be_recorded_but_only_ip_off_consumes_it(self):
        rnd=self.create();self.access(1,rnd['starts_at_ms']+1000,'')
        result=self.evaluate(rnd)
        self.assertEqual('PRESENT',result['records'][0]['status'])
        self.assertEqual('',self.conn.execute('SELECT ip_address FROM login_attendance_access').fetchone()[0])

    def test_before_start_and_at_end_access_never_count_even_with_old_timetable(self):
        rnd=self.create()
        self.access(1,rnd['starts_at_ms']-1)
        self.access(2,rnd['ends_at_ms'])
        self.access(3,rnd['ends_at_ms']-1)
        result=self.evaluate(rnd)
        self.assertEqual(['PENDING','PENDING','PRESENT'],[r['status'] for r in result['records'][:3]])

    def test_only_applied_in_window_server_receipts_count_for_the_same_classroom(self):
        rnd=self.create()
        stamp=datetime.fromtimestamp((rnd['starts_at_ms']+1000)/1000,local.rules.SHANGHAI).replace(tzinfo=None).isoformat()
        for sid,state in ((1,'applied'),(2,'pending'),(3,'quarantined'),(4,'discarded')):
            self.conn.execute("INSERT INTO behavior_write_inbox VALUES(10,?,'student',?,?)",(sid,state,stamp))
        self.conn.execute("INSERT INTO behavior_write_inbox VALUES(11,5,'student','applied',?)",(stamp,))
        self.conn.commit()
        result=self.evaluate(rnd)
        self.assertEqual(['PRESENT','PENDING','PENDING','PENDING','PENDING'],[r['status'] for r in result['records'][:5]])
        self.assertEqual('server_learning_received',result['records'][0]['evidence']['reason_code'])
        self.assertIsNone(result['records'][0]['metrics']['access_span_seconds'])

    def test_client_learning_ranges_are_metrics_not_a_server_attendance_vote(self):
        rnd=self.create()
        start=rnd['starts_at_ms']+1000
        # The existing presence subsystem stores the validated interval union.
        data={'reference':start+30000,'ranges':{'learning':[[start,start+30000]]}}
        self.conn.execute("INSERT INTO classroom_behavior_states VALUES(10,1,'student',?)",(json.dumps(data),))
        self.conn.commit()
        result=self.evaluate(rnd)
        self.assertEqual('PENDING',result['records'][0]['status'])
        self.assertEqual(30,result['records'][0]['metrics']['learning_seconds'])

    def test_network_rule_cannot_be_bypassed_by_applied_receipt_without_an_ip(self):
        rnd=self.create(enforce_network=True,networks=['192.0.2.0/24'])
        stamp=datetime.fromtimestamp((rnd['starts_at_ms']+1000)/1000,local.rules.SHANGHAI).replace(tzinfo=None).isoformat()
        self.conn.execute("INSERT INTO behavior_write_inbox VALUES(10,1,'student','applied',?)",(stamp,));self.conn.commit()
        self.assertEqual('PENDING',self.evaluate(rnd)['records'][0]['status'])

    def test_v3_uses_server_receipt_clock_when_observation_timestamp_differs(self):
        rnd=self.create();self.access(1,rnd['starts_at_ms']+1000)
        self.conn.execute('UPDATE login_attendance_access SET observed_at_ms=?',(rnd['ends_at_ms']+86400000,));self.conn.commit()
        self.assertEqual('PRESENT',self.evaluate(rnd)['records'][0]['status'])

    def test_policy_additive_migration_preserves_frozen_legacy_rounds(self):
        rnd=self.legacy()
        before=self.conn.execute('SELECT policy_json FROM login_attendance_rounds WHERE id=?',(rnd['id'],)).fetchone()[0]
        self.conn.execute('ALTER TABLE login_attendance_policies DROP COLUMN enforce_network')
        from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
        ensure_login_attendance_schema(self.conn,engine='sqlite');ensure_login_attendance_schema(self.conn,engine='sqlite')
        self.assertFalse(local.load_policy(self.conn,10)['enforce_network'])
        self.assertEqual(before,self.conn.execute('SELECT policy_json FROM login_attendance_rounds WHERE id=?',(rnd['id'],)).fetchone()[0])

    def test_access_span_is_not_continuous_time_and_duplicates_do_not_add_duration(self):
        rnd=self.create()
        for delta in (1000,1000,31*60000+1000):
            self.access(1,rnd['starts_at_ms']+delta)
        result=self.evaluate(rnd)
        metrics=result['records'][0]['metrics']
        self.assertEqual(31*60,metrics['access_span_seconds'])
        self.assertEqual(2,metrics['access_observation_count'])
        self.assertNotIn('login_seconds',metrics)

    def test_manual_decision_stays_authoritative(self):
        rnd=self.create();record=rnd['records'][0]
        local.change_records(self.conn,rnd['id'],self.user,{'records':[{'student_id':1,'revision':record['revision'],'status':'SICK_LEAVE'}],'reason':'Verified leave'})
        self.conn.commit();self.access(1,rnd['starts_at_ms']+1000)
        result=self.evaluate(rnd)['records'][0]
        self.assertEqual('SICK_LEAVE',result['status']);self.assertEqual('Verified leave',result['reason'])
        self.assertEqual('PRESENT',result['suggestion'])

    def legacy(self):
        rnd=self.create(enforce_network=True,networks=['192.0.2.0/24'])
        policy=dict(rnd['policy']);policy['algorithm_version']=local.rules.VERSION;policy.pop('enforce_network')
        self.conn.execute('UPDATE login_attendance_rounds SET policy_json=? WHERE id=?',(local._json(policy),rnd['id']))
        self.conn.commit()
        self.access(1,rnd['starts_at_ms']+1000,'192.0.2.1')
        self.access(2,rnd['starts_at_ms']+1000,'198.51.100.2')
        return self.evaluate(rnd)

    def test_legacy_rules_are_frozen_until_explicit_upgrade_and_keep_original_network(self):
        rnd=self.legacy();self.assertEqual('PENDING',rnd['records'][0]['status'])
        self.assertTrue(rnd['needs_rule_upgrade'])
        path=self.base+f"/rounds/{rnd['id']}/settlement"
        status=self.client.get(path).json()
        payload={'idempotency_key':'upgrade-legacy-round','expected_round_revision':status['round_revision'],
                 'restart':False,'adopt_current_rules':True}
        response=self.client.post(path,headers=self.headers,json=payload)
        self.assertEqual(202,response.status_code,response.text)
        task=response.json()['task']
        self.assertTrue(response.json()['round']['policy']['enforce_network'])
        settlement.process_settlement(7,task['id'])
        final=local.round_view(self.conn,rnd['id'],self.user)
        self.assertEqual(['PRESENT','PENDING'],[r['status'] for r in final['records'][:2]])
        for key in ('starts_at_ms','ends_at_ms','window_start_ms','window_end_ms'):
            self.assertEqual(rnd[key],final[key])
        self.assertFalse(final['needs_rule_upgrade'])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='attendance_rules_upgraded'").fetchone()[0])
        replay=self.client.post(path,headers=self.headers,json=payload)
        self.assertEqual(202,replay.status_code,replay.text)
        self.assertEqual(task['id'],replay.json()['task']['id'])

    def test_ordinary_legacy_recalculation_does_not_upgrade(self):
        rnd=self.legacy()
        self.assertTrue(self.evaluate(rnd)['needs_rule_upgrade'])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='attendance_rules_upgraded'").fetchone()[0])

    def test_readonly_processing_distinguishes_real_grace_from_queue(self):
        rnd=self.create();self.mock_now.return_value=rnd['starts_at_ms']+60000
        ended=local.end_round(self.conn,rnd['id'],self.user,{'revision':rnd['revision']});self.conn.commit()
        self.assertEqual('waiting_receipts',ended['processing']['stage'])
        self.assertEqual(120000,ended['processing']['remaining_ms'])
        self.mock_now.return_value=ended['settle_at_ms']+1
        status=settlement.status(self.conn,self.user,10,rnd['id'])
        self.assertEqual('queued',status['processing']['stage'])
        self.assertIsNone(status['task'])
        self.assertNotIn('percent',status['processing'])

    def test_api_defaults_cidr_and_rejects_ambiguous_or_invalid_window(self):
        view=self.client.get(self.base+'/sessions/21',headers={'X-Real-IP':'192.0.2.43'}).json()
        self.assertEqual('192.0.2.0/24',view['teacher_network']['suggested_cidr'])
        for bad in ({'duration_minutes':0},{'duration_minutes':True},{'enforce_network':'false'},
                    {'starts_at':'2026-11-08T08:30:00+08:00'}, {'enforce_network':True,'networks':[]}):
            response=self.client.post(self.base+'/sessions/21/rounds',headers=self.headers,
                json={'request_key':'invalid-window-request',**bad})
            self.assertEqual(422,response.status_code,response.text)
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM login_attendance_rounds').fetchone()[0])
