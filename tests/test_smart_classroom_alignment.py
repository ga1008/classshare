"""Alignment evidence, hierarchical confirmation and whole-course integration."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.services import smart_classroom_alignment as rules
from classroom_app.services import smart_classroom_alignment_service as alignment
from classroom_app.services import smart_classroom_workflow_service as flow
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from classroom_app.services.smart_classroom_attendance_adapter import AttendanceSourceError
from tests.test_smart_classroom_workflow_router import WorkflowRouterTests


class AlignmentEvidenceTests(unittest.TestCase):
    def test_symmetric_difference_and_majority_are_both_required(self):
        self.assertFalse(rules.roster_evidence(['a'], ['b'])['accepted'])
        self.assertFalse(rules.roster_evidence(['a','b'], ['a'])['accepted'])
        evidence = rules.roster_evidence(range(10), range(2,12))
        self.assertEqual(4, evidence['difference_count'])
        self.assertTrue(evidence['accepted'])
        self.assertFalse(rules.roster_evidence(range(10), range(3,12))['accepted'])

    def test_missing_zero_metadata_never_deterministic(self):
        info = {'course_code':'E1','academic_year':'2026-2027','academic_term':1,'session_count':0,'total_hours':0,'credits':0}
        evidence = rules.roster_evidence(['1','2'],['1','2'])
        self.assertFalse(rules.deterministic_course(info, info, evidence))
        info.update(session_count=16,total_hours=32,credits=2)
        self.assertTrue(rules.deterministic_course(info, info, evidence))

    def test_full_schedule_expands_in_order_and_rejects_incomplete(self):
        rows = [{'id':'a','xqj':1,'sections':'2-3','weeks':[3,1,2]}]
        actual, complete = rules.occurrences(rows)
        self.assertTrue(complete)
        self.assertEqual([1,2,3], [r['week'] for r in actual])
        self.assertEqual([1,2,3], [r['order_index'] for r in actual])
        self.assertEqual(([],False), rules.occurrences([{'id':'a','xqj':1,'sections':'2-3'}]))
        self.assertFalse(rules.exact_session({'week':1,'day_of_week':1,'sections':[2,3],'order_index':2},actual[0]))


class AlignmentIntegrationTests(WorkflowRouterTests):
    def match(self):
        response = self.client.post(self.base + '/match', headers=self.headers, json={})
        self.assertEqual(200,response.status_code,response.text)
        return response.json()

    def confirmation(self, state, scope, candidate=None, key='synthetic-confirm-key'):
        return {'scope':scope,'candidate_id':(candidate or state['alignment'][scope]['candidates'][0])['id'],
                'expected_course_revision':state['alignment']['course']['revision'],
                'expected_binding_revision':state['binding']['revision'],'idempotency_key':key}

    def confirm(self, payload):
        return self.client.post(self.base + '/confirm', headers=self.headers,json=payload)

    def add_students(self):
        for i in range(3,11):
            self.conn.execute("INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) VALUES(?,?,?,1,'active')",(i,f'S{i}',f'Synthetic {i}'))
        self.conn.commit()
        self.remote = {f'S{i}':'UNCHECKED' for i in range(1,11)}

    def test_minor_two_way_roster_difference_binds_without_ai(self):
        self.add_students()
        self.remote.pop('S1'); self.remote.pop('S2')
        self.remote.update(R1='CHECKED',R2='CHECKED')
        state = self.match()
        self.assertEqual('matched',state['binding']['state'])
        evidence = state['alignment']['course']['candidates'][0]['evidence']
        self.assertEqual(4,evidence['roster']['difference_count'])
        self.assertEqual('deterministic',evidence['method'])
        flow._ai_assist.assert_not_awaited()

    def test_unrelated_bad_school_candidate_is_isolated_as_diagnostic_card(self):
        self.groups.return_value.insert(0,{'id':'other-row','courseId':'OTHER','course':'Unrelated synthetic course','claId':'other-class','claName':'Other class','credits':2,'total_hours':2})
        mini.list_schedules.return_value['items'].append({'id':'other-row','courseCode':'OTHER','sections':'2-3','xqj':1,'weeks':[11]})
        async def discover(*args):
            if args[2]['course_code']=='OTHER':
                raise AttendanceSourceError('round_identity_changed','学校返回的名单不属于所选教学班。')
            return await self.read_slot(*args)
        with patch.object(mini,'read_alignment_slot',AsyncMock(side_effect=discover)):
            state=self.match()
        self.assertEqual('matched',state['binding']['state'])
        invalid=next(c for c in state['alignment']['course']['candidates'] if c['target']['course_code']=='OTHER')
        self.assertFalse(invalid['eligible'])
        self.assertEqual('round_identity_changed',invalid['evidence']['read_error']['code'])

    def test_unreadable_same_named_candidate_cannot_create_false_unique_match(self):
        self.groups.return_value.insert(0,{'id':'other-row','courseId':'OTHER','course':'Synthetic course','claId':'other-class','claName':'Other class','credits':2,'total_hours':2})
        mini.list_schedules.return_value['items'].append({'id':'other-row','courseCode':'OTHER','sections':'2-3','xqj':1,'weeks':[11]})
        async def discover(*args):
            if args[2]['course_code']=='OTHER':
                raise AttendanceSourceError('round_identity_changed','学校返回的名单不属于所选教学班。')
            return await self.read_slot(*args)
        with patch.object(mini,'read_alignment_slot',AsyncMock(side_effect=discover)):
            state=self.match()
        self.assertEqual('review',state['alignment']['course']['state'])
        self.assertFalse(state['can_sync'])
        flow._ai_assist.assert_not_awaited()

    def test_large_difference_needs_explicit_course_confirmation_and_receipt(self):
        self.add_students()
        self.remote.pop('S1'); self.remote.pop('S2'); self.remote.pop('S3')
        self.remote.update(R1='CHECKED',R2='CHECKED')
        state = self.match()
        self.assertEqual('review',state['alignment']['course']['state'])
        self.assertFalse(state['can_sync'])
        payload = self.confirmation(state,'course')
        response = self.confirm(payload)
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual('matched',response.json()['binding']['state'])
        source = json.loads(self.conn.execute('SELECT source_json FROM smart_attendance_course_links').fetchone()[0])
        self.assertEqual('manual',source['course_alignment_method'])
        self.assertTrue(source['remote_roster_fingerprint'])
        calls = self.groups.await_count
        repeated = self.confirm(payload)
        self.assertEqual(200,repeated.status_code)
        self.assertEqual(calls,self.groups.await_count)
        self.assertEqual(1,self.conn.execute('SELECT count(*) FROM smart_attendance_alignment_requests').fetchone()[0])

    def test_ai_can_select_course_alias_but_not_disjoint_roster(self):
        self.groups.return_value[0].update(courseId='ALIAS',course='别名课程')
        mini.list_schedules.return_value['items'][0]['courseCode']='ALIAS'
        flow._ai_assist.side_effect=lambda task,facts:{'status':'assisted','decision':facts['allowed_decisions'][1],'reason':'代码不同但名称别名及课表证据一致'}
        state=self.match()
        self.assertEqual('matched',state['binding']['state'])
        self.assertEqual('ai',state['alignment']['course']['candidates'][0]['evidence']['method'])
        self.remote={'different':'CHECKED'}
        state=self.match()
        self.assertNotEqual('matched',state['binding']['state'])
        self.assertFalse(state['alignment']['course']['candidates'][0]['eligible'])
        self.assertEqual(409,self.confirm(self.confirmation(state,'course')).status_code)

    def test_full_week_day_sections_and_order_all_needed_manual_slot_is_explicit(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-11-16',week_index=12 WHERE id=21")
        self.conn.commit()
        state=self.match()
        self.assertEqual('matched',state['alignment']['course']['state'])
        self.assertEqual('review',state['binding']['state'])
        payload=self.confirmation(state,'session')
        response=self.confirm(payload)
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual('matched',response.json()['binding']['state'])
        slot=json.loads(self.conn.execute('SELECT slot_json FROM smart_attendance_session_links').fetchone()[0])
        self.assertEqual(11,slot['week'])
        self.assertEqual(12,slot['local_session_identity']['week'])
        self.assertEqual('manual',slot['alignment_method'])

    def test_whole_semester_reads_once_then_changed_order_is_stale(self):
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code) VALUES(22,10,2,'Later','2026-11-16',0,12,'2-3','E1')")
        self.conn.commit()
        mini.list_schedules.return_value['items'][0]['weeks']=[11,12]
        result=asyncio.run(flow.align_offering(self.user,10,background=True))
        self.assertEqual('matched',result['status'])
        self.assertEqual(2,self.conn.execute("SELECT count(*) FROM smart_attendance_session_links WHERE state='matched'").fetchone()[0])
        self.assertEqual(1,self.groups.await_count)
        self.assertEqual(1,mini.list_schedules.await_count)
        self.assertEqual(1,mini.read_slot.await_count)
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code) VALUES(23,10,0,'Earlier','2026-11-02',0,10,'2-3','E1')")
        self.conn.commit()
        state=self.client.get(self.base+'/workflow').json()
        self.assertEqual('stale',state['binding']['state'])
        self.assertTrue(state['alignment']['needs_review'])
        source=json.loads(self.conn.execute('SELECT source_json FROM smart_attendance_session_links WHERE session_id=21').fetchone()[0])
        slot=json.loads(self.conn.execute('SELECT slot_json FROM smart_attendance_session_links WHERE session_id=21').fetchone()[0])
        with self.assertRaises(HTTPException):
            flow.assert_current_alignment(self.conn,self.user,10,21,source,slot)

    def test_confirm_rejects_stale_version_expiry_new_roster_and_foreign_key(self):
        self.groups.return_value[0]['credits']=None
        state=self.match()
        payload=self.confirmation(state,'course')
        bad={**payload,'expected_course_revision':0}
        self.assertEqual(409,self.confirm(bad).status_code)
        self.remote.update(S3='CHECKED')
        self.assertEqual(409,self.confirm(payload).status_code)
        self.remote.pop('S3')
        data=json.loads(self.conn.execute('SELECT alignment_json FROM smart_attendance_course_links').fetchone()[0])
        data['expires_at_ms']=1
        self.conn.execute('UPDATE smart_attendance_course_links SET alignment_json=?',(json.dumps(data),))
        self.conn.commit()
        self.assertEqual(409,self.confirm(payload).status_code)
        self.assertEqual(0,self.conn.execute('SELECT count(*) FROM smart_attendance_alignment_requests').fetchone()[0])

    def test_legacy_session_schema_upgrades_twice_without_dropping_links(self):
        from classroom_app.db.schema_smart_classroom_auto_sync import ensure_smart_classroom_auto_sync_schema
        ensure_smart_classroom_auto_sync_schema(self.conn,engine='sqlite')
        ensure_smart_classroom_auto_sync_schema(self.conn,engine='sqlite')
        self.assertIn('course_link_revision',{r[1] for r in self.conn.execute('PRAGMA table_info(smart_attendance_session_links)')})

    def test_new_semester_different_representative_id_needs_no_rollcall_history(self):
        self.groups.return_value[0]['id']='representative'
        self.record=None
        with patch.object(mini,'read_history',AsyncMock(return_value=[])) as history:
            state=self.match()
            self.assertEqual('matched',state['binding']['state'])
            history.assert_not_awaited()

    def test_background_transfer_preserves_previous_teacher_binding(self):
        self.match()
        self.conn.execute('UPDATE class_offerings SET teacher_id=8 WHERE id=10')
        self.conn.commit()
        result=asyncio.run(flow.align_offering({'id':8,'role':'teacher'},10,background=True))
        self.assertEqual('preserved',result['status'])
        self.assertEqual((7,'stale'),tuple(self.conn.execute('SELECT owner_teacher_id,state FROM smart_attendance_course_links').fetchone()))

    def test_local_change_before_confirmation_rejects_old_cards(self):
        self.groups.return_value[0]['credits']=None
        state=self.match()
        payload=self.confirmation(state,'course')
        self.conn.execute("UPDATE courses SET name='Changed local title' WHERE id=1")
        self.conn.commit()
        response=self.confirm(payload)
        self.assertEqual(409,response.status_code)
        self.assertIn('本地课程',response.text)

    def test_single_lesson_refresh_preserves_other_manual_slot(self):
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code) VALUES(22,10,2,'Later','2026-11-23',0,13,'2-3','E1')")
        self.conn.commit()
        mini.list_schedules.return_value['items'][0]['weeks']=[11,12]
        state=self.match()
        old=self.base
        self.base='/api/classrooms/10/login-attendance/sessions/22/external'
        state=self.client.get(self.base+'/workflow').json()
        option=next(c for c in state['alignment']['session']['candidates'] if c['target']['week']==12)
        # The selected slot is read afresh on confirmation, so the synthetic
        # school response must now represent that exact slot.
        self.record=None
        response=self.confirm(self.confirmation(state,'session',option))
        self.assertEqual(200,response.status_code,response.text)
        saved=tuple(self.conn.execute('SELECT revision,slot_json FROM smart_attendance_session_links WHERE session_id=22').fetchone())
        self.base=old
        self.conn.execute('UPDATE courses SET total_hours=4 WHERE id=1')
        self.conn.commit()
        self.match()
        self.assertEqual(saved,tuple(self.conn.execute('SELECT revision,slot_json FROM smart_attendance_session_links WHERE session_id=22').fetchone()))

    def test_dispatch_refreshes_future_slot_and_only_requests_location_for_new_round(self):
        state=self.match()
        self.seed_round()
        with self.database() as conn:
            row=conn.execute('SELECT slot_json FROM smart_attendance_session_links WHERE session_id=21').fetchone()
            slot=json.loads(row[0]);slot.update(remote_checkin_id=None,can_create=False)
            conn.execute('UPDATE smart_attendance_session_links SET slot_json=? WHERE session_id=21',(json.dumps(slot),))
        payload={'idempotency_key':'preflight-no-location','expected_binding_revision':state['binding']['revision']}
        url='/api/classrooms/10/login-attendance/rounds/31/external/sync'
        self.record=None
        response=self.client.post(url,headers=self.headers,json=payload)
        self.assertEqual(409,response.status_code,response.text)
        self.assertEqual('location_required',response.json()['detail']['code'])
        self.assertEqual(0,self.conn.execute('SELECT count(*) FROM smart_attendance_workflow_requests').fetchone()[0])
        self.assertEqual(0,self.conn.execute('SELECT count(*) FROM smart_attendance_auto_sync').fetchone()[0])
        self.record={'id':'school-already-created','teacherScheduleId':'schedule','isStop':True,'year':'2026-2027','semester':1,'courseId':'E1','claId':'class','week':11,'dayOfWeek':1,'section':2}
        response=self.client.post(url,headers=self.headers,json=payload)
        self.assertEqual(202,response.status_code,response.text)
        self.assertEqual('school-already-created',self.conn.execute('SELECT remote_checkin_id FROM smart_attendance_auto_sync').fetchone()[0])

    def test_duplicate_school_slot_is_not_a_confirmable_lesson(self):
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code) VALUES(22,10,2,'Later','2026-11-16',0,12,'2-3','E1')")
        self.conn.commit()
        flow._ai_assist.side_effect=lambda task,facts:{'status':'assisted','decision':facts['allowed_decisions'][1]}
        self.match()
        self.base='/api/classrooms/10/login-attendance/sessions/22/external'
        state=self.client.get(self.base+'/workflow').json()
        self.assertFalse(state['alignment']['session']['candidates'][0]['eligible'])
        response=self.confirm(self.confirmation(state,'session'))
        self.assertEqual(409,response.status_code)
