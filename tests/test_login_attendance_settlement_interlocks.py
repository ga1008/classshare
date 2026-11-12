"""Local settlement never inherits or races an existing school authorization."""
import unittest
from unittest.mock import Mock

from fastapi import HTTPException

from tests import test_smart_classroom_workflow as workflow_fixture
import test_smart_classroom_student_push as student_fixture
from classroom_app.services import login_attendance_settlement_guard as guard
from classroom_app.services import smart_classroom_workflow_service as workflow
from classroom_app.services import smart_classroom_student_push_service as single
from classroom_app.services import smart_classroom_auto_sync_service as auto


class SettlementPlanGuardTests(unittest.TestCase):
    def setUp(self):
        self.f = workflow_fixture.WorkflowTests('test_match_before_round_and_fail_keeps_local_round')
        self.f.addCleanup = self.addCleanup
        self.f.setUp()

    def test_dry_gate_preserves_plan_and_request_atomically_revokes_old_authorization(self):
        before = self.f.plan()
        guard.settlement_school_guard(self.f.conn, 'local', pause=False)
        self.assertEqual(before, self.f.plan())
        guard.settlement_school_guard(self.f.conn, 'local')
        stopped = self.f.plan()
        self.assertEqual((0, 'awaiting_push', before['revision']+1),
                         (stopped['enabled'], stopped['state'], stopped['revision']))
        self.f.conn.rollback()
        self.assertEqual(before, self.f.plan(), 'Failed local request must restore the old authorization')
        self.assertEqual([], self.f.writes)

    def test_unknown_lifecycle_is_never_swallowed_by_settlement(self):
        self.f.conn.execute("UPDATE smart_attendance_auto_sync SET state='outcome_unknown',intent_json=?", ('{"operation":"stop","acknowledged":false}',))
        self.f.conn.commit()
        before = self.f.plan()
        with self.assertRaises(HTTPException) as error:
            guard.settlement_school_guard(self.f.conn, 'local')
        self.assertEqual('school_sync_busy', error.exception.detail['code'])
        self.assertEqual(before, self.f.plan())

    def test_unsent_auto_ledger_is_superseded_and_cannot_later_submit(self):
        self.f.patch(auto, '_assert_alignment', Mock())
        plan = self.f.plan()
        snapshot = {'record':self.f.record, 'students':self.f.remote}
        plan['binding_id'] = auto._bind_plan(self.f.user,plan,snapshot)
        run = auto._new_run(self.f.user,plan,self.f.ctx,snapshot)
        operation = dict(self.f.conn.execute("SELECT * FROM smart_attendance_sync_operations WHERE run_id=? AND state='pending' LIMIT 1", (run['id'],)).fetchone())
        guard.settlement_school_guard(self.f.conn,'local')
        self.f.conn.commit()
        self.assertEqual('superseded',self.f.conn.execute('SELECT state FROM smart_attendance_sync_runs WHERE id=?',(run['id'],)).fetchone()[0])
        self.assertEqual('superseded',self.f.conn.execute('SELECT state FROM smart_attendance_sync_operations WHERE id=?',(operation['id'],)).fetchone()[0])
        with self.assertRaises(HTTPException):
            auto._submission_intent(self.f.user,plan,run,operation)
        self.assertEqual([],self.f.writes)


class SettlementStudentInterlockTests(unittest.TestCase):
    def setUp(self):
        self.f = student_fixture.StudentPushTests('test_local_save_is_idempotent_and_never_contacts_school')
        self.f.addCleanup = self.addCleanup
        self.f.setUp()

    def unsettled(self, state='queued'):
        self.f.conn.execute("INSERT INTO login_attendance_settlement_runs(id,round_id,owner_teacher_id,generation,request_key,request_hash,state,stage,cutoff_ms,scope_json,created_at_ms,updated_at_ms) "
            "VALUES('settlement',31,7,1,'settlement-key','hash',?,'collecting',3,'{}',3,3)", (state,))
        # Keep a published state to prove the dedicated guard, rather than
        # relying only on the ordinary round.state gate.
        self.f.conn.execute('UPDATE login_attendance_rounds SET settlement_generation=1 WHERE id=31')
        self.f.conn.commit()

    def test_unpublished_settlement_blocks_remote_but_preserves_manual_local_save(self):
        self.unsettled('failed')
        status = single.status(self.f.conn, self.f.user, 10, 31, 1)
        self.assertEqual('settlement_pending', status['gate']['code'])
        before = self.f.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=2').fetchone()[0]
        saved = self.f.save(mode='local', status='SICK_LEAVE')
        self.assertTrue(saved['local_saved'])
        blocked = self.f.save(mode='push', status='PERSONAL_LEAVE', key='blocked-after-settle')
        self.assertEqual('blocked', blocked['push']['state'])
        self.assertEqual('settlement_pending', blocked['push']['code'])
        self.assertEqual(before, self.f.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=2').fetchone()[0])
        self.assertFalse(workflow.workflow(self.f.conn, self.f.user, 10, 21)['can_sync'])
        response = self.f.client.post('/api/classrooms/10/login-attendance/rounds/31/external/sync', headers=self.f.headers,
            json={'idempotency_key':'batch-during-settlement','expected_binding_revision':self.f.binding_revision})
        self.assertEqual(409, response.status_code)
        self.assertEqual('settlement_pending', response.json()['detail']['code'])

    def test_single_pending_or_unknown_prevents_replacing_local_settlement(self):
        saved = self.f.save(mode='push', status='SICK_LEAVE')
        for state in ('queued', 'checking', 'outcome_unknown'):
            self.f.conn.execute('UPDATE smart_attendance_student_corrections SET state=? WHERE id=?', (state,saved['push']['id']))
            self.f.conn.commit()
            with self.assertRaises(HTTPException) as error:
                guard.settlement_school_guard(self.f.conn,31)
            self.assertEqual('school_sync_busy',error.exception.detail['code'])
            self.assertEqual(state,self.f.conn.execute('SELECT state FROM smart_attendance_student_corrections WHERE id=?',(saved['push']['id'],)).fetchone()[0])
