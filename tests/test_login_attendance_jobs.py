"""Durable scans and bounded retention on synthetic SQLite."""
from contextlib import contextmanager
import unittest
from unittest.mock import patch
from classroom_app.services import login_attendance_jobs as jobs
from classroom_app.services.login_attendance_access_service import record_access
from test_login_attendance_service import LoginAttendanceServiceTests as Fixture


class AttendanceJobTests(unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)
        p=patch.object(jobs,'get_db_connection',self.database)
        p.start();self.addCleanup(p.stop)

    @contextmanager
    def database(self):
        try:yield self.conn
        except BaseException:self.conn.rollback();raise

    def test_dispatch_scan_publishes_due_round_and_retries_failure_without_duplicates(self):
        rnd=Fixture.create(self)
        Fixture.observe(self)
        self.mock_now.return_value=rnd['settle_at_ms']
        with patch.object(jobs.attendance,'evaluate_round',side_effect=RuntimeError('Synthetic worker failure')):
            with self.assertRaises(RuntimeError):jobs.handle_scan({'payload':{}})
        self.assertEqual('running',jobs.attendance.load_round(self.conn,rnd['id'])['state'])
        outcome=jobs.handle_scan({'payload':{}})
        self.assertEqual([rnd['id']],outcome['completed'])
        self.assertEqual('published',jobs.attendance.load_round(self.conn,rnd['id'])['state'])
        again=jobs.handle_scan({'payload':{}})
        self.assertEqual([],again['completed'])
        self.assertEqual(6,self.conn.execute('SELECT COUNT(*) FROM login_attendance_records').fetchone()[0])

    def settlement_run(self, rnd, generation, state):
        key=f"scan-fairness-{rnd['id']}-{generation}"
        self.conn.execute("INSERT INTO login_attendance_settlement_runs(id,round_id,owner_teacher_id,generation,request_key,request_hash,state,stage,cutoff_ms,scope_json,created_at_ms,updated_at_ms) "
            "VALUES(?,?,7,?,?,?,?,?,?,'{}',?,?)",
            (key,rnd['id'],generation,key,key,state,state,rnd['settle_at_ms'],self.start_ms,self.start_ms))

    def test_twelve_durable_settlements_cannot_starve_thirteenth_ordinary_round(self):
        rounds=[]
        for index in range(13):
            session_id=100+index
            self.conn.execute("INSERT INTO class_offering_sessions SELECT ?,class_offering_id,title,session_date,weekday,week_index,academic_section_text,academic_time_text,academic_course_code,schedule_status FROM class_offering_sessions WHERE id=21",(session_id,))
            rnd=jobs.attendance.create_round(self.conn,10,session_id,self.user,{'request_key':f'fair-scan-round-{index}'})
            self.conn.execute('UPDATE login_attendance_rounds SET next_evaluation_ms=? WHERE id=?',(self.start_ms+index,rnd['id']))
            if index < 12:
                self.conn.execute('UPDATE login_attendance_rounds SET settlement_generation=1 WHERE id=?',(rnd['id'],))
                self.settlement_run(rnd,1,('queued','running','failed')[index%3])
            rounds.append(rnd)
        self.conn.commit();Fixture.observe(self)
        self.mock_now.return_value=rounds[-1]['settle_at_ms']
        before=[jobs.attendance._records(self.conn,r['id']) for r in rounds[:-1]]
        outcome=jobs.handle_scan({'payload':{}})
        self.assertEqual([rounds[-1]['id']],outcome['completed'])
        self.assertEqual('published',jobs.attendance.load_round(self.conn,rounds[-1]['id'])['state'])
        self.assertEqual(before,[jobs.attendance._records(self.conn,r['id']) for r in rounds[:-1]])

    def test_historical_failed_generation_does_not_block_current_ordinary_scan(self):
        rnd=Fixture.create(self);Fixture.observe(self)
        self.conn.execute('UPDATE login_attendance_rounds SET settlement_generation=2 WHERE id=?',(rnd['id'],))
        self.settlement_run(rnd,1,'failed');self.settlement_run(rnd,2,'completed');self.conn.commit()
        self.mock_now.return_value=rnd['settle_at_ms']
        outcome=jobs.handle_scan({'payload':{}})
        self.assertEqual([rnd['id']],outcome['completed'])
        self.assertEqual('published',jobs.attendance.load_round(self.conn,rnd['id'])['state'])
        self.assertEqual('failed',self.conn.execute('SELECT state FROM login_attendance_settlement_runs WHERE generation=1').fetchone()[0])

    def test_retention_removes_expired_unpinned_observations_and_preserves_pending_appeal(self):
        rnd=Fixture.create(self)
        Fixture.observe(self,students=(1,2))
        record=self.conn.execute('SELECT id FROM login_attendance_records WHERE student_id=1').fetchone()
        self.conn.execute("INSERT INTO approval_requests(request_type,status,applicant_role,applicant_user_pk,subject_type,subject_id,title,reason,dedupe_key,created_at,updated_at) "
            "VALUES('login_attendance_appeal','pending','student',1,'login_attendance_record',?,'Synthetic','Evidence pin','pin','2026-11-08','2026-11-08')",(str(record['id']),))
        self.conn.commit()
        self.mock_now.return_value=self.start_ms+91*86400_000
        record_access(self.conn,student_id=2,ip_address='192.0.2.2',source='authenticated_request',observed_at_ms=self.mock_now.return_value)
        self.conn.commit()
        result=jobs.handle_retention({'payload':{}})
        self.assertEqual(1,result['removed_access_observations'])
        self.assertEqual(2,self.conn.execute('SELECT COUNT(*) FROM login_attendance_access').fetchone()[0])
        self.assertEqual(1,self.conn.execute('SELECT COUNT(*) FROM login_attendance_access WHERE student_id=1').fetchone()[0])

    def test_historical_recalculation_cannot_erase_sealed_evidence(self):
        rnd=Fixture.create(self);Fixture.observe(self)
        final=Fixture.evaluate(self,rnd,True)
        before=jobs.attendance._records(self.conn,rnd['id'])
        self.conn.execute('DELETE FROM login_attendance_access');self.conn.commit()
        self.mock_now.return_value=rnd['window_end_ms']+8*86400_000
        jobs.attendance.evaluate_round(self.conn,rnd['id'])
        self.assertEqual(before,jobs.attendance._records(self.conn,rnd['id']))

    def test_retention_before_first_approval_page_initializes_its_lazy_dependency(self):
        from classroom_app.services import approval_workflow_schema

        for table in ('approval_request_events','approval_request_reviewers','approval_requests'):
            self.conn.execute('DROP TABLE '+table)
        self.conn.commit()
        with patch.object(approval_workflow_schema,'_SCHEMA_READY',False):
            self.assertEqual({'removed_access_observations':0},jobs.handle_retention({'payload':{}}))
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM approval_requests').fetchone()[0])

    def test_settlement_tail_pending_receipt_remains_pending_quality(self):
        rnd=Fixture.create(self);Fixture.observe(self)
        stamp=jobs.attendance.rules.display_time(rnd['window_end_ms']+1000)
        # Existing receipt timestamps are server-local naive text.
        stamp=stamp.replace('+08:00','')
        self.conn.execute("INSERT INTO behavior_write_inbox VALUES(10,1,'student','pending',?)",(stamp,))
        self.conn.commit()
        final=Fixture.evaluate(self,rnd,True)
        self.assertFalse(final['watermark']['complete'])
        record=next(x for x in final['records'] if x['student_id']==1)
        self.assertEqual('PRESENT',record['status'])
        self.assertEqual('pending',record['quality'])
        self.assertIsNone(record['evidence']['learning']['measured_seconds'])
