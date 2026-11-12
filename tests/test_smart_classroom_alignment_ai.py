"""AI protocol diagnostics and neutral missing-metadata course evidence."""
import json
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx

from classroom_app.services import smart_classroom_workflow_service as flow
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from tests.test_smart_classroom_workflow_router import WorkflowRouterTests


class AlignmentAIProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, result=None, error=None):
        from classroom_app.core import ai_client
        with patch.object(ai_client,'post',AsyncMock(return_value=result,side_effect=error)):
            return await flow._ai_assist('Synthetic course evidence',{'allowed_decisions':['course-safe','uncertain']})

    async def test_official_json_response_preserves_allowed_decision_and_reason(self):
        response=httpx.Response(200,json={'status':'success','response_json':{'decision':'course-safe','reason':'代码与课程名一致；缺失学分为中性。'}},request=httpx.Request('POST','https://synthetic.invalid/api/ai/chat'))
        result=await self.call(response)
        self.assertEqual(('assisted','course-safe','ok'),(result['status'],result['decision'],result['code']))
        self.assertIn('中性',result['reason'])

    async def test_uncertain_reason_is_observable(self):
        response=httpx.Response(200,json={'status':'success','response_json':{'decision':'uncertain','reason':'存在两门同样可能的课程。'}},request=httpx.Request('POST','https://synthetic.invalid/api/ai/chat'))
        result=await self.call(response)
        self.assertEqual('uncertain',result['decision'])
        self.assertEqual('存在两门同样可能的课程。',result['reason'])

    async def test_failure_diagnostics_are_finite_and_never_expose_provider_body(self):
        request=httpx.Request('POST','https://synthetic.invalid/api/ai/chat')
        cases=[(httpx.Response(503,text='provider-secret-password',request=request),None,'http_error'),
               (None,httpx.ReadTimeout('provider-secret-password'),'timeout'),
               (httpx.Response(200,json={'status':'success','response_json':{'decision':'untrusted-id','reason':'provider-secret-password'}},request=request),None,'invalid_decision'),
               (httpx.Response(200,json=['provider-secret-password'],request=request),None,'invalid_response'),
               (None,RuntimeError('provider-secret-password'),'service_error')]
        for response,error,code in cases:
            with self.subTest(code=code):
                result=await self.call(response,error)
                self.assertEqual(code,result['code'])
                self.assertIsNone(result['decision'])
                self.assertNotIn('provider-secret-password',json.dumps(result))


class AlignmentAIEvidenceTests(WorkflowRouterTests):
    def test_allowed_roster_difference_and_missing_school_metadata_reach_ai_as_neutral(self):
        # Same course code/name/count; local 64h/4 credits, school unknown;
        # a two-way difference of two is already accepted by the hard gate.
        for sid in range(3,43):
            self.conn.execute("INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) VALUES(?,?,?,1,'active')",(sid,'S'+str(sid),'Synthetic'))
        self.conn.execute('UPDATE courses SET total_hours=64,credits=4 WHERE id=1')
        first=date(2026,8,31)
        self.conn.execute("UPDATE class_offering_sessions SET session_date=?,week_index=1 WHERE id=21",(first.isoformat(),))
        for index in range(1,30):
            week,day=index//2+1,2 if index%2 else 0
            when=first+timedelta(weeks=week-1,days=day)
            self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code) VALUES(?,10,?,'Synthetic',?,?,?,'2-3','E1')",(21+index,index+1,when.isoformat(),day,week))
        self.conn.commit()
        self.remote={'S'+str(i):'CHECKED' for i in range(1,41)}
        self.record=None
        mini.list_schedules.return_value['items']=[{'id':'schedule','courseCode':'E1','sections':'2-3','xqj':1,'weeks':list(range(1,16))},
                                                  {'id':'schedule-wed','courseCode':'E1','sections':'2-3','xqj':3,'weeks':list(range(1,16))}]
        self.groups.return_value[0].update(total_hours=None,credits=None)
        seen=[]
        def ai(task,facts):
            seen.append((task,facts))
            self.assertIn('正常退学/转入情况',task)
            self.assertIn('名单相差2人',task)
            self.assertIn('属于中性信息',task)
            self.assertIn('任一路充分证据',task)
            candidate=facts['candidates'][0]
            self.assertEqual(2,candidate['roster']['difference_count'])
            self.assertTrue(candidate['roster']['accepted'])
            self.assertIsNone(candidate['target']['total_hours'])
            self.assertIsNone(candidate['target']['credits'])
            self.assertNotIn('reasons',candidate,'Review warnings must not bias the AI evidence input')
            self.assertEqual(facts['local_course']['session_count'],candidate['target']['session_count'])
            self.assertEqual(30,candidate['target']['session_count'])
            return {'status':'assisted','decision':candidate['id'],'code':'ok','reason':'代码、名称与完整课次数一致；允许的名单差及未知学时学分不构成冲突。'}
        flow._ai_assist.side_effect=ai
        response=self.client.post(self.base+'/match',headers=self.headers,json={})
        self.assertEqual(200,response.status_code,response.text)
        state=response.json()
        self.assertEqual(1,len(seen))
        self.assertEqual('matched',state['binding']['state'])
        self.assertEqual('ok',state['alignment']['course']['ai']['code'])
        stored=json.loads(self.conn.execute('SELECT alignment_json FROM smart_attendance_course_links').fetchone()[0])
        self.assertEqual(state['alignment']['course']['ai'],stored['ai'])

    def test_unavailable_ai_reason_persists_for_existing_review_ui(self):
        self.groups.return_value[0].update(total_hours=None,credits=None)
        flow._ai_assist.return_value={'status':'unavailable','decision':None,'code':'timeout','reason':'AI暂时未响应；本地记录已保留。'}
        response=self.client.post(self.base+'/match',headers=self.headers,json={})
        self.assertEqual(200,response.status_code,response.text)
        course=response.json()['alignment']['course']
        self.assertEqual('review',course['state'])
        self.assertEqual('timeout',course['ai']['code'])
        self.assertIn('AI暂时未响应',course['message'])
