"""Actual C proposal HTTP, shared teaching domain and atomic receipts."""
import json
import sqlite3
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app import config
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.routers import agent_tasks
from tests.test_offering_merge_service import OfferingMergeServiceTests


class TeachingConfirmationHTTPTests(OfferingMergeServiceTests):
    def setUp(self):
        super().setUp()
        self.path = config.DB_PATH
        self.actor = {'role':'teacher','id':self.teacher_id,'session_id':'teaching-confirmation'}
        with self.connection() as conn:
            self.task_id = execute_insert_returning_id(conn,"""INSERT INTO agent_tasks(task_uuid,teacher_id,actor_role,actor_id,
                teacher_name,task_type,title,private_instruction,status) VALUES('teaching-confirmation',?,'teacher',?,'Synthetic','general','Review','Review','failed')""",
                (self.teacher_id,self.teacher_id))
            conn.execute("INSERT INTO user_sessions(session_user_key,session_id,user_id,role,expires_at) VALUES(?,'teaching-confirmation',?,'teacher','2033-05-19T00:00:00+00:00')",
                (f'teacher:{self.teacher_id}',str(self.teacher_id)))
            self.empty_class = self._insert_class(conn,'待删空班')
            self.empty_course = execute_insert_returning_id(conn,"INSERT INTO courses(name,created_by_teacher_id) VALUES('待删空课程',?)",(self.teacher_id,))
            conn.commit()
        self.enterContext(patch.object(agent_tasks,'get_db_connection',self.connection))
        self.app = FastAPI()
        self.app.include_router(agent_tasks.router)
        self.app.dependency_overrides[agent_tasks.get_current_user] = lambda:self.actor
        self.client = TestClient(self.app,raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.url = f'/api/agent-tasks/{self.task_id}/actions/0'

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path,timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            yield conn
        finally:
            conn.close()

    def sql(self,sql,params=()):
        with self.connection() as conn:
            rows = conn.execute(sql,params).fetchall()
            conn.commit()
            return rows

    def proposal(self, action, params):
        self.sql('UPDATE agent_tasks SET result_detail_json=? WHERE id=?',
            (json.dumps({'proposed_actions':[{'action':action,'params':params}]}),self.task_id))
        return self.preview()

    def preview(self):
        response = self.client.post(self.url+'/preview',json={})
        self.assertEqual(200,response.status_code,response.text)
        return response.json()

    def declaration(self,preview):
        review = preview['confirmation_review']
        return {'params':preview['params'],'confirmation_token':preview['confirmation_token'],
            'confirmation_inputs':{'accepted_warning_codes':[item['code'] for item in review['warnings']],
                'confirmation_note':'已逐项核对影响和保留范围','confirmation_text':review['expected_confirmation_text']}}

    def merge_proposal(self):
        return self.proposal('merge_class_offerings',{'target_offering_id':self.target_id,'source_offering_ids':[self.source_id]})

    def test_empty_class_delete_replay_records_one_receipt_and_changed_declaration_conflicts(self):
        request = self.declaration(self.proposal('delete_empty_class',{'class_id':self.empty_class}))
        first = self.client.post(self.url+'/execute',json=request)
        self.assertEqual(200,first.status_code,first.text)
        self.assertEqual([],self.sql('SELECT id FROM classes WHERE id=?',(self.empty_class,)))
        self.assertTrue(self.client.post(self.url+'/execute',json=request).json()['replayed'])
        self.assertEqual(1,self.sql('SELECT COUNT(*) FROM agent_action_executions')[0][0])
        request['confirmation_inputs']['confirmation_note']='A different declaration'
        self.assertEqual(409,self.client.post(self.url+'/execute',json=request).status_code)

    def test_unreferenced_course_requires_manually_entered_name(self):
        request = self.declaration(self.proposal('delete_unreferenced_course',{'course_id':self.empty_course}))
        request['confirmation_inputs']['confirmation_text']='Wrong course'
        self.assertEqual(400,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertTrue(self.sql('SELECT id FROM courses WHERE id=?',(self.empty_course,)))
        request['confirmation_inputs']['confirmation_text']='待删空课程'
        result = self.client.post(self.url+'/execute',json=request)
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual([],self.sql('SELECT id FROM courses WHERE id=?',(self.empty_course,)))
        self.assertTrue(self.sql('SELECT id FROM courses WHERE id=?',(self.course_id,)))

    def test_occupied_class_cannot_be_deleted_and_reasons_come_from_live_business(self):
        preview = self.proposal('delete_empty_class',{'class_id':self.class_a})
        self.assertFalse(preview['confirmation_review']['can_execute'])
        self.assertTrue(preview['confirmation_review']['blockers'])
        result = self.client.post(self.url+'/execute',json=self.declaration(preview))
        self.assertEqual(409,result.status_code,result.text)
        self.assertTrue(self.sql('SELECT id FROM classes WHERE id=?',(self.class_a,)))
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM agent_action_executions')[0][0])

    def test_new_student_after_preview_invalidates_empty_class_confirmation(self):
        request = self.declaration(self.proposal('delete_empty_class',{'class_id':self.empty_class}))
        with self.connection() as conn:
            self._insert_student(conn,'newly-enrolled','New student',self.empty_class)
            conn.commit()
        self.assertEqual(409,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertTrue(self.sql('SELECT id FROM classes WHERE id=?',(self.empty_class,)))
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM agent_action_executions')[0][0])

    def test_merge_preserves_assignments_submissions_material_binding_and_archive(self):
        request = self.declaration(self.merge_proposal())
        first = self.client.post(self.url+'/execute',json=request)
        self.assertEqual(200,first.status_code,first.text)
        self.assertEqual([],self.sql('SELECT id FROM class_offerings WHERE id=?',(self.source_id,)))
        self.assertEqual(2,self.sql('SELECT COUNT(*) FROM assignments WHERE class_offering_id=?',(self.target_id,))[0][0])
        self.assertEqual(1,self.sql('SELECT COUNT(*) FROM submissions WHERE assignment_id=?',(self.source_assignment,))[0][0])
        binding = self.sql('SELECT class_offering_id,session_id FROM class_offering_learning_materials WHERE material_id=?',(self.material_id,))[0]
        self.assertEqual((self.target_id,self.target_session),tuple(binding))
        self.assertEqual(1,self.sql('SELECT COUNT(*) FROM offering_merge_archives')[0][0])
        self.assertTrue(self.client.post(self.url+'/execute',json=request).json()['replayed'])

    def test_same_count_content_change_requires_new_merge_review(self):
        old = self.merge_proposal()
        self.sql("UPDATE assignments SET title='Updated assignment' WHERE id=?",(self.source_assignment,))
        self.assertEqual(409,self.client.post(self.url+'/execute',json=self.declaration(old)).status_code)
        current = self.preview()
        self.assertNotEqual(old['params']['expected_review_hash'],current['params']['expected_review_hash'])
        result = self.client.post(self.url+'/execute',json=self.declaration(current))
        self.assertEqual(200,result.status_code,result.text)

    def test_merge_receipt_failure_rolls_back_source_deletion_and_archive(self):
        request = self.declaration(self.merge_proposal())
        with patch('classroom_app.services.agent_operation_service.complete_user_agent_operation',side_effect=RuntimeError('Synthetic failure')):
            self.assertEqual(500,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertTrue(self.sql('SELECT id FROM class_offerings WHERE id=?',(self.source_id,)))
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM offering_merge_archives')[0][0])
        self.assertEqual(0,self.sql('SELECT COUNT(*) FROM agent_action_executions')[0][0])

    def test_student_and_revoked_session_cannot_execute_teaching_confirmation(self):
        request = self.declaration(self.merge_proposal())
        self.actor = {'role':'student','id':self.student_a,'session_id':'student-session'}
        self.assertIn(self.client.post(self.url+'/execute',json=request).status_code,(403,404))
        self.actor = {'role':'teacher','id':self.teacher_id,'session_id':'teaching-confirmation'}
        self.sql('DELETE FROM user_sessions WHERE session_user_key=?',(f'teacher:{self.teacher_id}',))
        self.assertEqual(401,self.client.post(self.url+'/execute',json=request).status_code)
        self.assertTrue(self.sql('SELECT id FROM class_offerings WHERE id=?',(self.source_id,)))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(TeachingConfirmationHTTPTests(name)
        for name,value in TeachingConfirmationHTTPTests.__dict__.items()
        if name.startswith('test_') and callable(value))
