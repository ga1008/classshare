"""Offline copies of the verified school response shape; no real identities."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from classroom_app.services import smart_classroom_miniapp_adapter as mini
from classroom_app.services.smart_classroom_attendance_adapter import AttendanceSourceError


class AlignmentAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.slot={'schedule_id':'listed-timetable-id','week':1,'day_of_week':2,'sections':[2,3],'section':2}
        self.source={'course_code':'SYNTHETIC','academic_year':'2026-2027','academic_term':1,'remote_class_id':'synthetic-class'}
        self.record={'id':'exact-checkin','teacherScheduleId':'historical-internal-id','week':1,'dayOfWeek':2,'section':2,
                     'year':'2026-2027','semester':'1','courseId':'SYNTHETIC','claId':'synthetic-class','isStop':True}
        self.payload={'year':'2026-2027','semester':1,'data':{'id':'listed-timetable-id','courseCode':'SYNTHETIC','sections':'2-3'},
                      'checkinCourse':self.record,'stuList':[{'no':'S1','name':'Synthetic one','status':'CHECKED'}],'isCanCheckin':True}
        self.history=[copy.deepcopy(self.record)]
        self.calls=[]
        async def request(client,operation,data):
            self.calls.append((operation,copy.deepcopy(data)))
            if operation=='view':return copy.deepcopy(self.payload)
            if operation=='viewCheckinCoursesCount':return copy.deepcopy(self.history)
            if operation=='viewCheckinCoursesRecords':return [{'no':'S1','status':'UNCHECKED'}]
            raise AssertionError('No school mutations are permitted')
        self.enterContext(patch.object(mini,'_post',AsyncMock(side_effect=request)))

    async def test_readonly_roster_discards_heterogeneous_record_id(self):
        snapshot=await mini.read_alignment_slot(object(),self.slot,self.source)
        self.assertIsNone(snapshot['record'])
        self.assertFalse(snapshot['can_checkin'])
        self.assertEqual({'S1':'CHECKED'},snapshot['students'])
        self.assertIn('已排除',snapshot['alignment_note'])
        self.assertEqual(['view'],[c[0] for c in self.calls])
        with self.assertRaises(AttendanceSourceError):
            mini.validate_view(self.payload,self.slot,self.source)

    async def test_exact_requested_history_proves_internal_id_alias(self):
        snapshot=await mini.read_slot(object(),self.slot,self.source,'exact-checkin')
        self.assertEqual('historical-internal-id',snapshot['record']['teacherScheduleId'])
        self.assertEqual('exact-checkin',snapshot['record']['id'])
        self.assertEqual({'S1':'UNCHECKED'},snapshot['students'])
        self.assertFalse(snapshot['can_checkin'])
        self.assertEqual('listed-timetable-id',snapshot['record_scope_evidence']['schedule_id'])
        self.assertEqual([('viewCheckinCoursesCount',{'teacherScheduleId':'listed-timetable-id'}),
                          ('viewCheckinCoursesRecords',{'checkinCourseId':'exact-checkin'})],self.calls[1:])

    async def test_other_scope_mismatch_never_uses_alias_fallback(self):
        for field,value in [('week',2),('dayOfWeek',3),('section',4),('year','2025-2026'),('semester',2),('courseId','OTHER'),('claId','other-class')]:
            with self.subTest(field=field):
                original=self.record[field];self.record[field]=value;self.calls.clear()
                with self.assertRaises(AttendanceSourceError):
                    await mini.read_slot(object(),self.slot,self.source)
                self.assertEqual(['view'],[c[0] for c in self.calls])
                self.record[field]=original

    async def test_course_discovery_still_rejects_different_class_or_schedule(self):
        self.record['claId']='different-class'
        with self.assertRaises(AttendanceSourceError):
            await mini.read_alignment_slot(object(),self.slot,self.source)
        self.record['claId']='synthetic-class'
        self.payload['data']['id']='different-schedule'
        with self.assertRaises(AttendanceSourceError):
            await mini.read_alignment_slot(object(),self.slot,self.source)

    async def test_history_requires_unique_complete_exact_identity_and_expected_id(self):
        for history in ([],[{**self.record,'id':'other-checkin'}],[{**self.record,'teacherScheduleId':'other-id'}],
                        [{k:v for k,v in self.record.items() if k!='claId'}],
                        [self.record,{**self.record,'id':'second-same-slot'}]):
            with self.subTest(history_size=len(history)):
                self.history=copy.deepcopy(history);self.calls.clear()
                with self.assertRaises(AttendanceSourceError):
                    await mini.read_slot(object(),self.slot,self.source)
                self.assertNotIn('viewCheckinCoursesRecords',[c[0] for c in self.calls])
        self.history=[copy.deepcopy(self.record)]
        with self.assertRaises(AttendanceSourceError) as raised:
            await mini.read_slot(object(),self.slot,self.source,'previous-checkin')
        self.assertEqual('round_replaced',raised.exception.code)

    async def test_original_exact_round_path_does_not_need_history(self):
        self.record['teacherScheduleId']='listed-timetable-id'
        snapshot=await mini.read_slot(object(),self.slot,self.source,'exact-checkin')
        self.assertEqual('exact-checkin',snapshot['record']['id'])
        self.assertEqual(['view'],[c[0] for c in self.calls])
