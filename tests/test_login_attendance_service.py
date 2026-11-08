"""Real local attendance transactions on isolated synthetic SQLite."""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
from classroom_app.services import login_attendance_service as service
from classroom_app.services.login_attendance_access_service import record_access
from classroom_app.services import approval_workflow_schema
from test_classroom_activity_evidence import ClassroomActivityEvidenceTests


class LoginAttendanceServiceTests(unittest.TestCase):
    def setUp(self):
        ClassroomActivityEvidenceTests.setUp(self)
        self.conn.executescript("""
          ALTER TABLE class_offerings ADD COLUMN teacher_id INTEGER DEFAULT 7;
          ALTER TABLE class_offerings ADD COLUMN course_id INTEGER DEFAULT 1;
          ALTER TABLE class_offerings ADD COLUMN semester TEXT DEFAULT '2026-2027学年第1学期';
          CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT);
          INSERT INTO courses VALUES(1,'Synthetic course');
          CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT,is_super_admin INTEGER DEFAULT 0);
          INSERT INTO teachers VALUES(7,'Teacher',0),(8,'Other',0);
          CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,title TEXT,
            session_date TEXT,weekday INTEGER,week_index INTEGER,academic_section_text TEXT,academic_time_text TEXT,
            academic_course_code TEXT,schedule_status TEXT);
          INSERT INTO class_offering_sessions VALUES(21,10,'Lesson','2026-11-08',6,11,'2,3','','E1','normal');
          INSERT INTO class_offering_sessions VALUES(22,10,'Later','2026-11-08',6,11,'4,5','','E1','normal');
        """)
        ensure_login_attendance_schema(self.conn,engine='sqlite')
        patcher=patch.object(approval_workflow_schema,'_SCHEMA_READY',False)
        patcher.start(); self.addCleanup(patcher.stop)
        approval_workflow_schema.ensure_approval_workflow_schema(self.conn)
        self.conn.commit()
        self.user={'id':7,'role':'teacher','name':'Teacher'}
        self.start_ms=service.rules.to_ms('2026-11-08T08:30:00+08:00')
        self.clock=patch.object(service,'now_ms',return_value=self.start_ms+60_000)
        self.mock_now=self.clock.start();self.addCleanup(self.clock.stop)

    def create(self,**changes):
        payload={'request_key':'synthetic-round-1','networks':['192.0.2.0/24'],**changes}
        result=service.create_round(self.conn,10,21,self.user,payload)
        self.conn.commit()
        return result

    def observe(self,students=(1,2,3,4,5)):
        for sid in students:
            record_access(self.conn,student_id=sid,ip_address=f'192.0.2.{sid}',source='authenticated_request',
                          observed_at_ms=self.start_ms+sid*1000)
        self.conn.commit()

    def evaluate(self,rnd,final=False):
        stamp=rnd['settle_at_ms'] if final else self.start_ms+120_000
        self.mock_now.return_value=stamp
        service.evaluate_round(self.conn,rnd['id'],timestamp=stamp)
        self.conn.commit()
        return service.round_view(self.conn,rnd['id'],self.user)

    def test_live_then_final_with_zero_learning_and_retakes(self):
        rnd=self.create();self.observe()
        live=self.evaluate(rnd)
        self.assertEqual(5,live['summary']['provisionally_present'])
        self.assertTrue(all(r['status']=='PENDING' for r in live['records']))
        final=self.evaluate(rnd,True)
        self.assertEqual('published',final['state'])
        self.assertEqual(5,final['summary']['expected_counts']['PRESENT'])
        self.assertEqual(1,final['summary']['exempt'])
        self.assertTrue(all(r['evidence']['learning']['measured_seconds'] is None for r in final['records']))
        self.assertEqual('PENDING',next(r for r in final['records'] if r['student_id']==6)['status'])
        sync=service.load_attendance_sync_context(self.conn,self.user,rnd['id'])
        self.assertEqual([6],sync['exempt_students'])
        self.assertEqual(5,len(sync['students']))

    def test_missing_collection_never_becomes_absence(self):
        final=self.evaluate(self.create(),True)
        self.assertEqual({'PENDING':5},final['summary']['expected_counts'])
        for row in final['records']:
            self.assertEqual('no_observation',row['quality'])
            self.assertIsNone(row['evidence']['learning']['measured_seconds'])

    def test_manual_decision_is_not_overwritten_and_stale_batch_preserves_other_rows(self):
        rnd=self.create();self.observe()
        rec=rnd['records'][0]
        changed=service.change_records(self.conn,rnd['id'],self.user,{'records':[
            {'student_id':rec['student_id'],'revision':rec['revision'],'status':'SICK_LEAVE'},
            {'student_id':2,'revision':-1,'status':'ABSENT'}],'reason':'Verified leave'})
        self.conn.commit()
        self.assertEqual(['updated','conflict'],[r['result'] for r in changed['results']])
        final=self.evaluate(rnd,True)
        result=next(r for r in final['records'] if r['student_id']==rec['student_id'])
        self.assertEqual('SICK_LEAVE',result['status'])
        self.assertEqual('Verified leave',result['reason'])

    def test_idempotent_create_void_and_scope_enforced(self):
        rnd=self.create()
        self.assertEqual(rnd['id'],self.create()['id'])
        with self.assertRaises(HTTPException) as error:
            service.create_round(self.conn,10,22,self.user,{'request_key':'synthetic-round-1','networks':['192.0.2.0/24']})
        self.assertEqual(409,error.exception.status_code);self.conn.rollback()
        with self.assertRaises(HTTPException):
            service.round_view(self.conn,rnd['id'],{'id':8,'role':'teacher'})
        void=service.void_round(self.conn,rnd['id'],self.user,{'revision':rnd['revision'],'reason':'Test cancellation'})
        self.conn.commit()
        again=service.void_round(self.conn,rnd['id'],self.user,{'revision':rnd['revision'],'reason':'Test cancellation'})
        self.assertEqual(void['revision'],again['revision'])
        self.conn.commit()
        self.evaluate(rnd,True)
        self.assertEqual('void',service.load_round(self.conn,rnd['id'])['state'])

    def test_course_closed_blocks_new_but_allows_historical_correction(self):
        rnd=self.create()
        service.close_attendance_for_course(self.conn,10,self.user);self.conn.commit()
        self.assertIsNotNone(service.load_policy(self.conn,10)['closed_at_ms'])
        with self.assertRaises(HTTPException) as error:self.create(request_key='another-round-key')
        self.assertEqual(409,error.exception.status_code);self.conn.rollback()
        rec=service.round_view(self.conn,rnd['id'],self.user)['records'][0]
        result=service.change_records(self.conn,rnd['id'],self.user,{'records':[
            {'student_id':rec['student_id'],'revision':rec['revision'],'status':'PRESENT'}],'reason':'Historical correction'})
        self.assertEqual('updated',result['results'][0]['result'])

    def test_student_projection_only_own_record_and_no_hidden_rule_data(self):
        rnd=self.create();self.observe();self.evaluate(rnd,True)
        view=service.student_view(self.conn,10,21,{'id':1,'role':'student'})
        self.assertEqual(1,view['student_id']);self.assertTrue(view['can_appeal'])
        encoded=json.dumps(view)
        for key in ('baseline','networks','reason_code','deviation_ms','evidence_json'):
            self.assertNotIn(key,encoded)

    def test_replaced_round_never_recomputed_or_changed(self):
        rnd=self.create()
        ended=service.end_round(self.conn,rnd['id'],self.user,{'revision':rnd['revision']});self.conn.commit()
        new=self.create(request_key='replacement-key',supersedes_id=rnd['id'])
        self.assertNotEqual(rnd['id'],new['id'])
        before=service.load_round(self.conn,rnd['id'])
        service.evaluate_round(self.conn,rnd['id'],timestamp=rnd['settle_at_ms']);self.conn.commit()
        self.assertEqual(before,service.load_round(self.conn,rnd['id']))
        rec=ended['records'][0]
        with self.assertRaises(HTTPException):
            service.change_records(self.conn,rnd['id'],self.user,{'records':[
                {'student_id':rec['student_id'],'revision':rec['revision'],'status':'ABSENT'}],'reason':'Stale tab'})
