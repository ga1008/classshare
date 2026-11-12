"""Individual corrections cannot overlap new whole-class authorizations."""
import unittest
from unittest.mock import Mock

from fastapi import HTTPException
from tests import test_smart_classroom_workflow as fixture
from classroom_app.services import smart_classroom_workflow_service as workflow
from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_alignment_service as alignment
from classroom_app.services import smart_classroom_miniapp_adapter as mini


class IndividualPushInterlockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixture.WorkflowTests('test_match_before_round_and_fail_keeps_local_round')
        # The composed fixture does not own an asyncio runner. Register its
        # database and patch cleanups on this running test instead.
        self.f.addCleanup=self.addCleanup
        self.f.setUp()

    def active(self,state='queued'):
        self.f.conn.execute("INSERT INTO smart_attendance_student_corrections(id,owner_teacher_id,request_key,request_hash,"
            "class_offering_id,session_id,origin_scope_json,local_round_id,student_id,student_number,local_revision,target_status,"
            "mode,state,created_at,updated_at) VALUES('individual',1,'individual-key','hash',1,2,'{}','local',1,'001',1,'SICK_LEAVE','push',?,'now','now')",(state,))
        self.f.conn.commit()

    async def test_workflow_disables_batch_and_rejects_new_dispatch_before_network(self):
        initial=await self.f.bind_lesson()
        self.active('outcome_unknown')
        self.assertFalse(workflow.workflow(self.f.conn,self.f.user,1,2)['can_sync'])
        mini.read_slot.reset_mock()
        payload={'idempotency_key':'new-batch','expected_binding_revision':initial['binding']['revision']}
        with self.assertRaises(HTTPException) as error:
            await workflow.dispatch_sync(self.f.user,1,'local',payload)
        self.assertEqual('student_push_busy',error.exception.detail['code'])
        mini.read_slot.assert_not_awaited()
        self.assertEqual(0,self.f.conn.execute('SELECT COUNT(*) FROM smart_attendance_workflow_requests').fetchone()[0])
        with self.assertRaises(HTTPException):alignment._busy(self.f.conn,1,2)

    async def test_new_configuration_retry_and_reauthorization_are_blocked(self):
        self.active()
        self.f.patch(auto,'_decode_option',Mock(side_effect=[self.f.source,{**self.f.slot,'kind':'smart_attendance_slot'}]))
        with self.assertRaises(HTTPException) as error:
            await auto.configure_auto_sync(self.f.user,'local',{'enabled':True,'acknowledge_session_takeover':True,
                'idempotency_key':'different-key','source_token':'source','slot_token':'slot','expected_revision':1})
        self.assertEqual('student_push_busy',error.exception.detail['code'])
        with self.assertRaises(HTTPException):auto.retry_auto_sync(self.f.conn,self.f.user,'local',1)
        self.f.conn.rollback()
        with self.assertRaises(HTTPException):await auto.reauthorize_auto_sync(self.f.user,'local',1)
        self.assertEqual(1,self.f.plan()['revision'])
        self.assertEqual([],self.f.writes)

    async def test_old_batch_receipt_can_be_read_without_reauthorizing(self):
        initial=await self.f.bind_lesson()
        payload={'idempotency_key':'completed-original-click','expected_binding_revision':initial['binding']['revision']}
        workflow.enqueue_sync(self.f.conn,self.f.user,1,'local',payload)
        self.f.conn.commit()
        before=dict(self.f.conn.execute('SELECT * FROM smart_attendance_auto_sync').fetchone())
        self.active()
        result=workflow.enqueue_sync(self.f.conn,self.f.user,1,'local',payload)
        self.assertFalse(result['can_sync'])
        self.assertEqual(before,dict(self.f.conn.execute('SELECT * FROM smart_attendance_auto_sync').fetchone()))
        self.assertEqual(1,self.f.conn.execute('SELECT COUNT(*) FROM smart_attendance_workflow_requests').fetchone()[0])
