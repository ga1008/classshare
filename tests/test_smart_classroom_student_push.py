"""Isolated real routes/transactions; every school interaction is synthetic."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import test_smart_classroom_workflow_router as fixture
from classroom_app.db import schema_scheduler
from classroom_app.services import smart_classroom_student_push_service as single
from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_write_service as ledger
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from classroom_app.services.smart_classroom_session_service import SmartClassroomSessionError


class StudentPushTests(unittest.TestCase):
    database = fixture.WorkflowRouterTests.database
    authenticated = fixture.WorkflowRouterTests.authenticated
    seed_round = fixture.WorkflowRouterTests.seed_round

    def setUp(self):
        fixture.WorkflowRouterTests.setUp(self)
        self.enterContext(patch.object(schema_scheduler, '_SCHEMA_READY', False))
        schema_scheduler.ensure_scheduler_schema(self.conn)
        self.conn.commit()
        self.enterContext(patch.object(single, 'get_db_connection', self.database))
        self.enterContext(patch.object(single, 'open_authenticated_smart_classroom_client', self.authenticated))
        self.enterContext(patch.object(mini, 'start_checkin', AsyncMock(side_effect=AssertionError('Single push cannot start a rollcall'))))
        self.enterContext(patch.object(mini, 'stop_checkin', AsyncMock(side_effect=AssertionError('Single push cannot stop a rollcall'))))
        with single._CHECK_LOCK:
            single._CHECK_CACHE.clear()
        result = self.client.post(self.base+'/match', headers=self.headers, json={})
        self.assertEqual(200, result.status_code, result.text)
        self.binding_revision = result.json()['binding']['revision']
        self.seed_round()
        self.path = '/api/classrooms/10/login-attendance/rounds/31/external/students/1'

    async def read_slot(self, client, slot, source, expected=None):
        self.assertFalse(self.conn.in_transaction)
        if expected and (not self.record or expected != self.record['id']):
            from classroom_app.services.smart_classroom_attendance_adapter import AttendanceSourceError
            raise AttendanceSourceError('round_replaced', 'synthetic replaced')
        return {'record': copy.deepcopy(self.record), 'students': copy.deepcopy(self.remote), 'can_checkin': True}

    async def change(self, client, checkin, number, status):
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual('remote', checkin)
        intent = self.conn.execute("SELECT state FROM smart_attendance_student_corrections WHERE student_number=? ORDER BY created_at DESC LIMIT 1", (number,)).fetchone()
        self.assertEqual('outcome_unknown', intent['state'], 'Intent must commit before sending')
        self.remote[number] = status

    def payload(self, *, mode='push', key='single-request-01', status='PRESENT', revision=None):
        if revision is None:
            revision = self.conn.execute('SELECT revision FROM login_attendance_records WHERE round_id=31 AND student_id=1').fetchone()[0]
        result = {'mode': mode, 'status': status, 'revision': revision, 'reason': '教师核对后更正', 'idempotency_key': key}
        if mode == 'push':
            result.update(expected_binding_revision=self.binding_revision, expected_remote_checkin_id='remote')
        return result

    def save(self, **kwargs):
        response = self.client.post(self.path+'/corrections', headers=self.headers, json=self.payload(**kwargs))
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json()['local_saved'])
        return response.json()

    def process(self, intent):
        return asyncio.run(single.process(7, intent['id']))

    def test_local_save_is_idempotent_and_never_contacts_school(self):
        mini.read_slot.reset_mock(); mini.change_student.reset_mock()
        payload = self.payload(mode='local')
        first = self.client.post(self.path+'/corrections', headers=self.headers, json=payload)
        replay = self.client.post(self.path+'/corrections', headers=self.headers, json=payload)
        self.assertEqual(200, first.status_code)
        self.assertEqual(first.json()['saved_record_revision'], replay.json()['saved_record_revision'])
        self.assertIsNone(replay.json()['push'])
        self.assertEqual(1, self.conn.execute('SELECT COUNT(*) FROM smart_attendance_student_corrections').fetchone()[0])
        mini.read_slot.assert_not_awaited(); mini.change_student.assert_not_awaited()

    def test_atomic_receipt_collision_rolls_back_edit(self):
        payload = self.payload(mode='local')
        self.client.post(self.path+'/corrections', headers=self.headers, json=payload)
        payload['status'] = 'SICK_LEAVE'
        result = self.client.post(self.path+'/corrections', headers=self.headers, json=payload)
        self.assertEqual(409, result.status_code)
        self.assertEqual('PRESENT', self.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=1').fetchone()[0])

    def test_single_student_push_while_local_running_and_remote_open(self):
        self.conn.execute("UPDATE login_attendance_rounds SET state='running' WHERE id=31"); self.conn.commit()
        self.record['isStop'] = False
        saved = self.save(status='SICK_LEAVE')
        self.assertEqual('confirmed', self.process(saved['push'])['state'])
        self.assertEqual({'S1':'SICK_LEAVE','S2':'CHECKED'}, self.remote)
        mini.change_student.assert_awaited_once()
        self.assertEqual(1, self.conn.execute('SELECT COUNT(*) FROM smart_attendance_sync_operations').fetchone()[0])
        mini.start_checkin.assert_not_awaited(); mini.stop_checkin.assert_not_awaited()

    def test_six_explicit_statuses_can_replace_ordinary_leave(self):
        for index, status in enumerate(ledger.LOCAL_STATUS_MAP):
            with self.subTest(status=status):
                self.remote['S1'] = 'SICK_LEAVE' if status != 'SICK_LEAVE' else 'PERSONAL_LEAVE'
                saved = self.save(status=status, key=f'six-status-{index}')
                self.assertEqual('confirmed', self.process(saved['push'])['state'])
                self.assertEqual(ledger.LOCAL_STATUS_MAP[status], self.remote['S1'])
                self.assertEqual('CHECKED', self.remote['S2'])

    def test_preflight_never_starts_school_and_shares_course_read(self):
        mini.read_slot.reset_mock()
        first = self.client.post(self.path+'/push/check', headers=self.headers, json={})
        second = self.client.post(self.path.replace('/students/1','/students/2')+'/push/check', headers=self.headers, json={})
        self.assertTrue(first.json()['gate']['allowed']); self.assertTrue(second.json()['gate']['allowed'])
        self.assertEqual(1, mini.read_slot.await_count)
        for _ in range(3):
            self.client.get(self.path+'/push')
        self.assertEqual(1, mini.read_slot.await_count)
        self.record = None
        with single._CHECK_LOCK: single._CHECK_CACHE.clear()
        stopped = self.client.post(self.path+'/push/check', headers=self.headers, json={})
        self.assertFalse(stopped.json()['gate']['allowed'])
        mini.start_checkin.assert_not_awaited(); mini.stop_checkin.assert_not_awaited()

    def test_preflight_remote_replacement_cannot_be_written(self):
        saved = self.save()
        self.record['id'] = 'new-remote'
        result = self.process(saved['push'])
        self.assertIn(result['state'], {'blocked','failed'})
        self.assertEqual('round_replaced', result['code'])
        mini.change_student.assert_not_awaited()

    def test_remote_absent_and_special_states_remain_local_saved(self):
        for index, remote in enumerate(({}, {'S1':'PRACTICE','S2':'CHECKED'})):
            self.remote = remote
            saved = self.save(key=f'blocked-{index}')
            result = self.process(saved['push'])
            self.assertIn(result['code'], {'roster_changed','protected_remote_status'})
            self.assertEqual('PRESENT', self.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=1').fetchone()[0])
        mini.change_student.assert_not_awaited()

    def test_unknown_after_response_loss_only_reads_even_after_local_edit(self):
        async def lost(client, rid, number, state):
            await self.change(client, rid, number, state)
            raise httpx.ReadTimeout('synthetic response lost')
        mini.change_student.side_effect = lost
        saved = self.save(status='SICK_LEAVE')
        unknown = self.process(saved['push'])
        self.assertEqual('outcome_unknown', unknown['state'])
        self.save(mode='local', key='local-after-unknown', status='ABSENT')
        result = self.process(unknown)
        self.assertEqual('confirmed', result['state'])
        self.assertEqual('ABSENT', self.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=1').fetchone()[0])
        self.assertEqual(1, mini.change_student.await_count)

    def test_unknown_baseline_is_never_blindly_replayed(self):
        mini.change_student.side_effect = httpx.ReadTimeout('synthetic request unknown')
        saved = self.save(status='PERSONAL_LEAVE')
        unknown = self.process(saved['push'])
        result = self.process(unknown)
        self.assertEqual('outcome_unknown', result['state'])
        self.assertEqual(1, mini.change_student.await_count)

    def test_401_recovers_only_the_known_rejected_operation(self):
        mini.change_student.side_effect = SmartClassroomSessionError('session_refreshed', 'synthetic login refreshed')
        saved = self.save(status='PERSONAL_LEAVE')
        result = self.process(saved['push'])
        self.assertEqual('queued', result['state'])
        mini.change_student.side_effect = self.change
        self.assertEqual('confirmed', self.process(result)['state'])
        self.assertEqual(2, mini.change_student.await_count)

    def test_new_local_edit_fences_unsent_old_task(self):
        old = self.save(status='SICK_LEAVE')
        self.save(mode='local', key='new-local-version', status='ABSENT')
        self.assertEqual('superseded', self.process(old['push'])['state'])
        mini.change_student.assert_not_awaited()

    def test_evidence_only_revision_does_not_cancel_teacher_target(self):
        old = self.save(status='SICK_LEAVE')
        self.conn.execute('UPDATE login_attendance_records SET revision=revision+1,updated_at_ms=99 WHERE student_id=1'); self.conn.commit()
        self.assertEqual('confirmed', self.process(old['push'])['state'])

    def test_local_only_revokes_legacy_autopush_authorization(self):
        from classroom_app.services import smart_classroom_workflow_service as flow
        with self.database() as conn:
            flow.enqueue_sync(conn, self.user, 10, 31, {'idempotency_key':'legacy-batch','expected_binding_revision':self.binding_revision})
            conn.execute("UPDATE smart_attendance_auto_sync SET intent_json='{}'")
        self.save(mode='local', status='SICK_LEAVE')
        plan = self.conn.execute('SELECT enabled,state FROM smart_attendance_auto_sync').fetchone()
        self.assertEqual((0,'awaiting_push'), tuple(plan))
        mini.read_slot.reset_mock()
        asyncio.run(auto.process_auto_sync(7,31))
        mini.read_slot.assert_not_awaited(); mini.change_student.assert_not_awaited()

    def test_permission_revision_and_origin_denials_do_not_save(self):
        response = self.client.post(self.path+'/corrections', headers=self.headers, json=self.payload(revision=99))
        self.assertEqual(409,response.status_code)
        self.user = {'id':8,'role':'teacher'}
        self.assertEqual(403, self.client.post(self.path+'/corrections',headers=self.headers,json=self.payload()).status_code)
        self.user = {'id':7,'role':'teacher'}
        self.assertEqual(403, self.client.post(self.path+'/corrections',headers={'Origin':'https://other.test'},json=self.payload()).status_code)
        self.assertEqual(0, self.conn.execute('SELECT COUNT(*) FROM smart_attendance_student_corrections').fetchone()[0])

    def test_ledger_legacy_refresh_cannot_mutate_single_operation(self):
        saved = self.save(); self.process(saved['push'])
        row = self.conn.execute('SELECT run_id FROM smart_attendance_student_corrections').fetchone()
        self.assertEqual('student_push', ledger.get_run(self.user,31,row['run_id'])['mode'])
        with self.assertRaises(Exception) as ctx:
            asyncio.run(ledger.reconcile_run(self.user,31,row['run_id']))
        self.assertEqual(409,ctx.exception.status_code)

    def test_unknown_is_visible_after_local_only_save(self):
        mini.change_student.side_effect = httpx.ReadTimeout('synthetic unknown')
        saved = self.save(status='PERSONAL_LEAVE')
        self.process(saved['push'])
        result = self.save(mode='local', key='local-preserve-unknown', status='ABSENT')
        self.assertEqual(saved['push']['id'], result['push']['id'])
        self.assertTrue(result['push']['readback_only'])
        status = self.client.get(self.path+'/push').json()
        self.assertEqual('outcome_unknown', status['push']['state'])
        self.assertFalse(status['gate']['allowed'])

    def test_lease_competition_queues_an_automatic_retry(self):
        saved = self.save()
        account = self.conn.execute('SELECT external_account_key FROM smart_attendance_student_corrections').fetchone()[0]
        self.conn.execute("INSERT INTO smart_attendance_account_leases(external_account_key,lease_token,lease_until) VALUES(?,'other','2099-01-01')", (account,)); self.conn.commit()
        result = self.process(saved['push'])
        self.assertEqual('queued', result['state'])
        self.assertEqual('session_busy', result['code'])
        self.assertEqual(2, self.conn.execute('SELECT COUNT(*) FROM scheduled_tasks WHERE task_kind=?', (single.TASK_KIND,)).fetchone()[0])
        mini.change_student.assert_not_awaited()

    def test_record_cas_failure_does_not_revoke_batch(self):
        from classroom_app.services import smart_classroom_workflow_service as flow
        with self.database() as conn:
            flow.enqueue_sync(conn, self.user, 10, 31, {'idempotency_key':'old-batch-authorization','expected_binding_revision':self.binding_revision})
        result = self.client.post(self.path+'/corrections', headers=self.headers, json=self.payload(mode='local', revision=99))
        self.assertEqual(409, result.status_code)
        self.assertEqual(1, self.conn.execute('SELECT enabled FROM smart_attendance_auto_sync').fetchone()[0])

    def test_two_students_have_independent_authorized_targets(self):
        first = self.save(status='SICK_LEAVE')
        path = self.path.replace('/students/1', '/students/2')
        payload = self.payload(status='PERSONAL_LEAVE', key='student-two-unique', revision=1)
        second = self.client.post(path+'/corrections', headers=self.headers, json=payload).json()
        self.assertEqual('confirmed', self.process(first['push'])['state'])
        self.assertEqual('confirmed', self.process(second['push'])['state'])
        self.assertEqual({'S1':'SICK_LEAVE','S2':'PERSONAL_LEAVE'}, self.remote)

    def test_worker_alignment_drift_keeps_saved_local_state(self):
        saved = self.save(status='PERSONAL_LEAVE')
        self.conn.execute("UPDATE smart_attendance_session_links SET state='stale',revision=revision+1"); self.conn.commit()
        result = self.process(saved['push'])
        self.assertEqual('blocked', result['state'])
        self.assertEqual('PERSONAL_LEAVE', self.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=1').fetchone()[0])
        mini.change_student.assert_not_awaited()

    def test_exact_school_projection_updates_without_changing_local_fine_status(self):
        checked = self.client.post(self.path+'/push/check', headers=self.headers, json={})
        self.assertTrue(checked.json()['gate']['allowed'])
        self.assertEqual('UNCHECKED', self.conn.execute("SELECT status FROM smart_classroom_checkin_students WHERE student_number='S1'").fetchone()[0])
        saved = self.save(status='EARLY')
        self.assertEqual('confirmed', self.process(saved['push'])['state'])
        self.assertEqual('LATE_OR_EARLY', self.conn.execute("SELECT status FROM smart_classroom_checkin_students WHERE student_number='S1'").fetchone()[0])
        self.assertEqual('EARLY', self.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=1').fetchone()[0])
        # A cached preflight cannot put its old UNCHECKED snapshot back.
        self.client.post(self.path+'/push/check', headers=self.headers, json={})
        self.assertEqual('LATE_OR_EARLY', self.conn.execute("SELECT status FROM smart_classroom_checkin_students WHERE student_number='S1'").fetchone()[0])

    def test_projection_failure_cannot_turn_confirmation_into_resend(self):
        from classroom_app.services import smart_classroom_workflow_service as flow
        saved = self.save(status='PERSONAL_LEAVE')
        with patch.object(flow, 'persist_remote_snapshot', side_effect=RuntimeError('synthetic unavailable projection')):
            self.assertEqual('confirmed', self.process(saved['push'])['state'])
        self.assertEqual('confirmed', self.process(saved['push'])['state'])
        self.assertEqual(1, mini.change_student.await_count)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM smart_attendance_sync_operations WHERE state='confirmed'").fetchone()[0])

    def test_unknown_projection_is_original_scope_only_and_preserves_new_local_decision(self):
        async def lost(client, rid, number, state):
            await self.change(client, rid, number, state)
            raise httpx.ReadTimeout('synthetic lost')
        mini.change_student.side_effect = lost
        saved = self.save(status='PERSONAL_LEAVE')
        self.process(saved['push'])
        self.save(mode='local', status='EARLY', key='after-projection-unknown')
        self.assertEqual('confirmed', self.process(saved['push'])['state'])
        self.assertEqual('PERSONAL_LEAVE', self.conn.execute("SELECT status FROM smart_classroom_checkin_students WHERE student_number='S1'").fetchone()[0])
        self.assertEqual('EARLY', self.conn.execute('SELECT status FROM login_attendance_records WHERE student_id=1').fetchone()[0])

    def test_closed_collection_policy_allows_explicit_later_correction(self):
        self.conn.execute('INSERT INTO login_attendance_policies(class_offering_id,closed_at_ms,updated_at_ms) VALUES(10,99,99)'); self.conn.commit()
        saved = self.save(status='SICK_LEAVE')
        self.assertEqual('confirmed', self.process(saved['push'])['state'])

    def test_void_round_cannot_push_even_existing_school_record(self):
        saved = self.save(status='SICK_LEAVE')
        self.conn.execute("UPDATE login_attendance_rounds SET state='void',is_current=0 WHERE id=31"); self.conn.commit()
        self.assertEqual('blocked', self.process(saved['push'])['state'])
        mini.change_student.assert_not_awaited()

    def test_not_started_preflight_is_not_cached_or_reported_as_saved(self):
        link = self.conn.execute('SELECT slot_json FROM smart_attendance_session_links').fetchone()
        slot = json.loads(link['slot_json']); slot['remote_checkin_id'] = None
        self.conn.execute('UPDATE smart_attendance_session_links SET slot_json=?', (json.dumps(slot),)); self.conn.commit()
        original = self.record; self.record = None
        mini.read_slot.reset_mock()
        first = self.client.post(self.path+'/push/check', headers=self.headers, json={}).json()
        self.assertFalse(first['gate']['allowed'])
        self.assertEqual('school_not_started', first['gate']['code'])
        self.assertNotIn('已保存', first['gate']['message'])
        self.assertFalse(first['local_saved'])
        self.record = original
        ready = self.client.post(self.path+'/push/check', headers=self.headers, json={}).json()
        self.assertTrue(ready['gate']['allowed'])
        self.assertEqual(2, mini.read_slot.await_count)

    def test_legacy_list_pause_enqueue_and_reauthorize_keep_single_ledger_sealed(self):
        saved = self.save()
        mini.change_student.side_effect = httpx.ReadTimeout('synthetic unknown')
        self.process(saved['push'])
        run = self.conn.execute('SELECT * FROM smart_attendance_sync_runs').fetchone()
        self.assertEqual('student_push', ledger.list_runs(self.conn,self.user,31)[0]['mode'])
        before = dict(run)
        for action in (
            lambda: ledger.pause_run(self.conn,self.user,31,run['id'],run['revision']),
            lambda: ledger.enqueue_readback(self.conn,self.user,31,run['id'],'legacy-replay-key'),
            lambda: ledger.reauthorize_binding(self.conn,self.user,31,run['binding_id'],1),
        ):
            with self.assertRaises(Exception) as error:
                action()
            self.assertEqual(409,error.exception.status_code)
            self.conn.rollback()
        self.assertEqual(before, dict(self.conn.execute('SELECT * FROM smart_attendance_sync_runs').fetchone()))

    def test_same_account_new_credentials_read_unknown_without_reauthorizing_write(self):
        async def lost(client, rid, number, state):
            await self.change(client, rid, number, state)
            raise httpx.ReadTimeout('synthetic lost')
        mini.change_student.side_effect = lost
        saved = self.save(status='SICK_LEAVE')
        self.process(saved['push'])
        self.access = {**self.access, 'credential_generation':'g2'}
        self.assertEqual('confirmed', self.process(saved['push'])['state'])
        self.assertEqual(1, mini.change_student.await_count)
        self.assertEqual('g1', self.conn.execute('SELECT credential_generation FROM smart_attendance_student_corrections').fetchone()[0])
        self.assertEqual('g1', self.conn.execute('SELECT credential_generation FROM smart_attendance_sync_runs').fetchone()[0])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM smart_attendance_sync_events WHERE event_type='student_readback_current_credentials'").fetchone()[0])

    def test_new_credentials_cannot_send_unsent_old_authorization(self):
        saved = self.save(status='PERSONAL_LEAVE')
        self.access = {**self.access, 'credential_generation':'g2'}
        self.assertEqual('blocked', self.process(saved['push'])['state'])
        mini.change_student.assert_not_awaited()

    def test_other_account_cannot_read_back_unknown_original(self):
        mini.change_student.side_effect = httpx.ReadTimeout('synthetic unknown')
        saved = self.save()
        self.process(saved['push'])
        self.access = {**self.access, 'username':'different-account', 'credential_generation':'g2'}
        mini.read_slot.reset_mock()
        self.assertEqual('outcome_unknown', self.process(saved['push'])['state'])
        mini.read_slot.assert_not_awaited()

    def test_inactive_teacher_unknown_keeps_audit_without_school_io(self):
        mini.change_student.side_effect = httpx.ReadTimeout('synthetic unknown')
        saved = self.save()
        self.process(saved['push'])
        self.conn.execute('UPDATE teachers SET is_active=0 WHERE id=7'); self.conn.commit()
        mini.read_slot.reset_mock(); mini.change_student.reset_mock(); self.account.reset_mock()
        result = self.process(saved['push'])
        self.assertEqual('outcome_unknown', result['state'])
        self.assertEqual('teacher_inactive', result['code'])
        mini.read_slot.assert_not_awaited(); mini.change_student.assert_not_awaited(); self.account.assert_not_called()


if __name__ == '__main__':
    unittest.main()
