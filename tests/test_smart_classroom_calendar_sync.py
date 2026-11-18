"""Semester preview/authorize/readback contracts with a real schema and synthetic school."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_calendar_sync_service as calendar
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from tests.test_smart_classroom_workflow_router import WorkflowRouterFixture


class CalendarSyncTests(WorkflowRouterFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(calendar, 'get_db_connection', self.database))

    def align(self):
        result = self.client.post('/api/classrooms/10/login-attendance/calendar/align', headers=self.headers)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual('matched', result.json()['status'])

    def preview(self, ids=None):
        res = self.client.post(self.base + '/calendar-preview', json={'student_ids': ids}, headers=self.headers)
        self.assertEqual(200, res.status_code, res.text)
        return res.json()

    def dispatch(self, token, key='calendar-click-1'):
        return self.client.post(self.base + '/calendar-sync', headers=self.headers,
                                json={'preview_token': token, 'idempotency_key': key})

    def create(self, token, key='calendar-create-1', **overrides):
        return self.client.post(self.base+'/calendar-create', headers=self.headers, json={
            'preview_token': token, 'idempotency_key': key, 'authorize_create': True,
            'location': {'latitude': 1.2, 'longitude': 2.3, 'captured_at_ms': int(time.time()*1000)}, **overrides})

    def test_four_local_school_states_and_no_creation(self):
        self.assertEqual('alignment_required', self.preview()['state'])
        saved = self.record
        self.record = None
        self.align()
        self.assertEqual('empty', self.preview()['state'])
        self.record = saved
        self.assertEqual('school_only', self.preview([1])['state'])
        self.assertEqual('UNCHECKED', self.preview([1])['rows'][0]['remote_status'])
        self.assertEqual('partial', self.preview([999])['state'])
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM login_attendance_rounds').fetchone()[0])
        self.assertEqual(2, self.conn.execute('SELECT count(*) FROM smart_classroom_checkin_students').fetchone()[0])
        self.seed_round()
        self.record = None
        self.assertEqual('school_missing', self.preview([1])['state'])
        self.record = {**saved, 'isStop': False}
        self.assertEqual('local_pending', self.preview([1])['state'])
        self.record = saved
        self.assertEqual('ready', self.preview()['state'])
        mini.change_student.assert_not_awaited()

    def test_scope_is_immutable_and_double_submit_returns_same_job(self):
        self.align()
        self.seed_round()
        p = self.preview([1])
        self.assertEqual(['needs_teacher_action'], [r['state'] for r in p['rows']])
        token = p['preview_token']
        for _ in range(2):
            res = self.dispatch(token)
            self.assertEqual(202, res.status_code, res.text)
            self.assertEqual('queued', res.json()['state'])
        self.assertEqual(409, self.dispatch(self.preview()['preview_token']).status_code)
        self.assertEqual(409, self.dispatch(self.preview()['preview_token'], 'separate-scope').status_code)
        self.assertEqual(1, self.conn.execute('SELECT COUNT(*) FROM smart_attendance_workflow_requests').fetchone()[0])
        asyncio.run(auto.process_auto_sync(7, 31))
        self.assertEqual({'S1': 'CHECKED', 'S2': 'CHECKED'}, self.remote)
        self.assertEqual('CHECKED', self.conn.execute("SELECT status FROM smart_classroom_checkin_students "
                         "WHERE student_number='S1'").fetchone()[0])
        status = self.client.get(self.base+'/calendar-sync/calendar-click-1').json()
        self.assertEqual(('confirmed', {'confirmed': 1}), (status['state'], status['counts']))
        run_id = self.conn.execute('SELECT id FROM smart_attendance_sync_runs').fetchone()[0]
        old_base = '/api/classrooms/10/login-attendance/rounds/31/external/runs/'
        self.assertEqual('auto_sync', self.client.get(old_base+run_id).json()['mode'])
        self.assertEqual([1], [r[0] for r in self.conn.execute('SELECT student_id FROM smart_attendance_sync_operations')])
        self.assertEqual('confirmed', self.dispatch(token).json()['state'])
        self.assertEqual('partial', self.preview([2])['state'])
        self.assertEqual('confirmed', self.preview([1])['state'])

    def test_stale_tokens_and_changed_school_status_do_not_overwrite(self):
        self.align()
        self.seed_round()
        token = self.preview([1])['preview_token']
        self.conn.execute('UPDATE login_attendance_rounds SET revision=revision+1 WHERE id=31')
        self.conn.commit()
        self.assertEqual(409, self.dispatch(token).status_code)
        token = self.preview([1])['preview_token']
        res = self.dispatch(token)
        self.assertEqual(202, res.status_code, res.text)
        self.remote['S1'] = 'LATE_OR_EARLY'
        self.assertEqual('conflict', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        mini.change_student.assert_not_awaited()
        self.assertEqual('LATE_OR_EARLY', self.remote['S1'])

    def test_creation_requires_authorized_whole_class_location_and_no_school_round(self):
        school_record, self.record = self.record, None
        self.align()
        self.seed_round()
        self.assertEqual('school_missing', self.preview([1, 2])['state'])
        offer = self.preview()
        self.assertEqual('create_available', offer['state'])
        token = offer['preview_token']
        self.assertEqual(422, self.create(token, authorize_create=False).status_code)
        self.assertEqual(422, self.create(token, location={'latitude': 1., 'longitude': 1., 'captured_at_ms': 1}).status_code)
        self.assertEqual(409, self.dispatch(token).status_code)
        self.record = school_record
        self.assertEqual(409, self.create(token).status_code)
        self.record = None
        self.assertEqual(202, self.create(token).status_code)
        self.record = school_record
        self.assertEqual('conflict', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        mini.change_student.assert_not_awaited()
        self.assertIsNone(self.conn.execute('SELECT 1 FROM smart_attendance_write_bindings').fetchone())

    def test_creation_uses_verified_new_round_and_never_duplicates_replayed_request(self):
        school_record, self.record = self.record, None
        self.align()
        self.seed_round()
        token = self.preview()['preview_token']
        def start(*args):
            self.record = {**school_record, 'id': 'created-round', 'isStop': False}
            self.remote = {'S1': 'UNCHECKED', 'S2': 'UNCHECKED'}
            return self.record
        history = AsyncMock(side_effect=lambda *a: [self.record.copy()] if self.record else [])
        self.enterContext(patch.object(mini, 'read_history', history))
        starts = self.enterContext(patch.object(mini, 'start_checkin', AsyncMock(side_effect=start)))
        stop = self.enterContext(patch.object(mini, 'stop_checkin', AsyncMock(side_effect=lambda *a: self.record.update(isStop=True))))
        self.assertEqual(202, self.create(token).status_code)
        self.assertEqual('confirmed', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.assertEqual({'S1': 'CHECKED', 'S2': 'UNCHECKED'}, self.remote)
        self.assertEqual('confirmed', self.create(token).json()['state'])
        self.assertEqual(1, starts.await_count)
        self.assertEqual(1, stop.await_count)
        self.assertEqual({'confirmed': 2}, self.client.get(self.base+'/calendar-sync/calendar-create-1').json()['counts'])

    def test_lost_create_acknowledgment_never_adopts_independent_round_on_readback(self):
        school_record, self.record = self.record, None
        self.align()
        self.seed_round()
        self.enterContext(patch.object(mini, 'read_history', AsyncMock(return_value=[])))
        starts = self.enterContext(patch.object(mini, 'start_checkin', AsyncMock(side_effect=TimeoutError())))
        token = self.preview()['preview_token']
        self.assertEqual(202, self.create(token).status_code)
        self.assertEqual('outcome_unknown', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.record = school_record
        checked = self.client.post(self.base+'/calendar-sync/calendar-create-1/readback', headers=self.headers)
        self.assertEqual('outcome_unknown', checked.json()['state'])
        self.assertEqual('outcome_unknown', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.assertEqual(1, starts.await_count)
        mini.change_student.assert_not_awaited()

    def test_semester_alignment_preserves_verified_manual_lesson(self):
        self.align()
        slot = json.loads(self.conn.execute('SELECT slot_json FROM smart_attendance_session_links').fetchone()[0])
        slot['alignment_method'] = 'manual'
        self.conn.execute('UPDATE smart_attendance_session_links SET slot_json=?', (json.dumps(slot),))
        self.conn.commit()
        self.align()
        self.assertEqual('manual', json.loads(self.conn.execute(
            'SELECT slot_json FROM smart_attendance_session_links').fetchone()[0])['alignment_method'])

    def test_unlisted_students_and_tampered_payload_remain_scoped(self):
        self.align()
        self.seed_round()
        self.assertEqual('no_local_record', self.preview([989])['rows'][0]['state'])
        p = self.preview([1, 989])
        self.assertEqual({'needs_teacher_action': 1, 'no_local_record': 1}, p['counts'])
        token = p['preview_token']
        self.assertEqual(409, self.dispatch(token+'bad').status_code)
        self.assertEqual(202, self.dispatch(token).status_code)
        asyncio.run(auto.process_auto_sync(7, 31))
        outcome = self.client.get(self.base+'/calendar-sync/calendar-click-1').json()
        self.assertEqual('partial', outcome['state'])
        self.assertEqual({'confirmed': 1, 'no_local_record': 1}, outcome['counts'])

    def test_unknown_write_is_never_replayed_on_retry_and_active_school_is_not_stopped(self):
        self.align()
        self.seed_round()
        token = self.preview([1])['preview_token']
        self.assertEqual(202, self.dispatch(token).status_code)
        self.record['isStop'] = False
        stop = self.enterContext(patch.object(mini, 'stop_checkin', AsyncMock()))
        self.assertEqual('conflict', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        stop.assert_not_awaited()
        self.record['isStop'] = True
        token = self.preview([1])['preview_token']
        self.assertEqual(202, self.dispatch(token, 'second-attempt').status_code)
        mini.change_student.side_effect = TimeoutError('synthetic disconnect')
        self.assertEqual('outcome_unknown', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.assertEqual('outcome_unknown', self.dispatch(token, 'second-attempt').json()['state'])
        self.assertEqual('outcome_unknown', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.assertEqual(1, mini.change_student.await_count)

        self.remote['S1'] = 'CHECKED'  # The school finally reflects the timed-out request.
        recovered = self.client.post(self.base+'/calendar-sync/second-attempt/readback', headers=self.headers)
        self.assertEqual(200, recovered.status_code, recovered.text)
        self.assertEqual('confirmed', recovered.json()['state'])
        self.assertEqual('CHECKED', self.conn.execute("SELECT status FROM smart_classroom_checkin_students "
                         "WHERE student_number='S1'").fetchone()[0])
        self.assertEqual(1, mini.change_student.await_count)

    def test_changed_credentials_allow_only_readback_of_an_existing_unknown_write(self):
        self.align()
        self.seed_round()
        self.conn.execute("UPDATE login_attendance_records SET status='PRESENT'")
        self.conn.commit()
        self.remote['S2'] = 'UNCHECKED'
        token = self.preview()['preview_token']
        self.assertEqual(202, self.dispatch(token).status_code)
        mini.change_student.side_effect = TimeoutError('synthetic uncertain result')
        self.assertEqual('outcome_unknown', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.remote['S1'] = 'CHECKED'
        self.access['credential_generation'] = 'g2'
        self.conn.execute("UPDATE smart_attendance_auto_sync SET state='needs_teacher_reauth'")
        self.conn.commit()
        result = self.client.post(self.base+'/calendar-sync/calendar-click-1/readback', headers=self.headers)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual('paused', result.json()['state'])
        self.assertEqual('UNCHECKED', self.remote['S2'])
        self.assertEqual(1, mini.change_student.await_count)

    def test_replaced_receipt_retains_original_readback_counts(self):
        self.align()
        self.seed_round()
        token = self.preview([1, 989])['preview_token']
        self.assertEqual(202, self.dispatch(token).status_code)
        self.assertEqual('confirmed', asyncio.run(auto.process_auto_sync(7, 31))['state'])
        self.remote['S1'] = 'UNCHECKED'
        next_token = self.preview([1])['preview_token']
        self.assertEqual(202, self.dispatch(next_token, 'subsequent-run').status_code)
        historical = self.client.get(self.base+'/calendar-sync/calendar-click-1').json()
        self.assertTrue(historical['historical'])
        self.assertEqual(('partial', {'confirmed': 1, 'no_local_record': 1}), (historical['state'], historical['counts']))

    def test_same_date_two_lessons_pin_separate_students_and_remote_rounds(self):
        self.conn.execute("""INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,
            week_index,academic_section_text,academic_course_code) VALUES(22,10,2,'Second','2026-11-09',0,11,'4-5','E1')""")
        self.conn.commit()
        mini.list_schedules.return_value['items'].append({'id': 'second', 'courseCode': 'E1', 'sections': '4-5',
                                                          'xqj': 1, 'weeks': [11]})
        rosters = {'schedule': {'S1': 'UNCHECKED', 'S2': 'UNCHECKED'}, 'second': {'S1': 'UNCHECKED', 'S2': 'UNCHECKED'}}
        def slot_read(_client, slot, source, expected=None):
            remote_key = slot['schedule_id']
            record = {**self.record, 'id': remote_key, 'teacherScheduleId': remote_key, 'week': slot['week'],
                      'dayOfWeek': slot['day_of_week'], 'section': slot['section']}
            payload = {'data': {'id': remote_key, 'courseCode': 'E1', 'sections': slot['sections']},
                       'year': source['academic_year'], 'semester': source['academic_term'], 'checkinCourse': record,
                       'stuList': [{'no': k, 'status': v} for k, v in rosters[remote_key].items()]}
            return mini.validate_view(payload, slot, source, expected)
        mini.read_slot.side_effect = slot_read
        mini.read_records.side_effect = lambda _client, rid: dict(rosters[rid])
        mini.change_student.side_effect = lambda _client, rid, s, state: rosters[rid].update({s: state})
        self.align()
        self.seed_round()
        self.seed_round(22, 32)
        self.conn.execute("UPDATE login_attendance_records SET status='PRESENT' WHERE student_id=2")
        self.conn.commit()
        first = self.preview([1, 2])
        self.base = self.base.replace('/21/', '/22/')
        second = self.preview([2])
        self.assertEqual(409, self.dispatch(first['preview_token']).status_code)
        self.assertEqual(202, self.dispatch(second['preview_token'], 'second-lesson').status_code)
        self.base = self.base.replace('/22/', '/21/')
        self.assertEqual(202, self.dispatch(first['preview_token'], 'first-lesson').status_code)
        for rid in (31, 32):
            self.assertEqual('confirmed', asyncio.run(auto.process_auto_sync(7, rid))['state'])
        self.assertEqual({'schedule': {'S1': 'CHECKED', 'S2': 'CHECKED'},
                          'second': {'S1': 'UNCHECKED', 'S2': 'CHECKED'}}, rosters)
        self.assertEqual([(21,'schedule'), (22,'second')], [tuple(r) for r in self.conn.execute(
            'SELECT session_id,remote_checkin_id FROM smart_classroom_checkin_sessions ORDER BY session_id')])
