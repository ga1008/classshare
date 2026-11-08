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
