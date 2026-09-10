"""Ordinary Web review/confirmation against an isolated real complete SQLite schema."""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.routers.manage_parts import classes_courses_classes as classes
from classroom_app.routers.manage_parts import classes_courses_courses as courses
from classroom_app.routers.manage_parts import classes_courses_offerings as offerings
from classroom_app.services import teaching_lifecycle_service as lifecycle
from classroom_app.services import lesson_plan_service
from tests.test_agent_teaching_confirmation import TeachingConfirmationHTTPTests as Fixture


class TeachingLifecycleHTTPTests(Fixture):
    def setUp(self):
        super().setUp()
        original_connect = sqlite3.connect
        def isolated_connect(database, *args, **kwargs):
            if Path(database).resolve() != Path(self.path).resolve():
                raise AssertionError('Unexpected database connector outside the synthetic fixture')
            return original_connect(database, *args, **kwargs)
        self.enterContext(patch('sqlite3.connect', side_effect=isolated_connect))
        self.app = FastAPI()
        for module in (classes, courses, offerings):
            self.enterContext(patch.object(module, 'get_db_connection', self.connection))
            self.app.include_router(module.router, prefix='/api/manage')
            def teacher():
                if self.actor['role'] != 'teacher':
                    raise HTTPException(403, 'Teacher required')
                return self.actor
            self.app.dependency_overrides[module.get_current_teacher] = teacher
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def review(self, kind='classes', resource_id=None):
        resource_id = resource_id or self.empty_class
        response = self.client.get(f'/api/manage/{kind}/{resource_id}/delete-impact')
        self.assertEqual(200, response.status_code, response.text)
        return response.json()['review']

    def body(self, review):
        return {'expected_review_hash':review['review_hash'],
                'accepted_warning_codes':[warning['code'] for warning in review['warnings']],
                'confirmation_text':review['expected_confirmation_text'],
                'confirmation_note':'已核对当前内容与保留范围'}

    def test_delete_requires_review_then_current_manual_declaration(self):
        url = f'/api/manage/classes/{self.empty_class}'
        self.assertEqual(400, self.client.request('DELETE', url, json={}).status_code)
        review = self.review()
        body = self.body(review)
        body['confirmation_text'] = 'Wrong name'
        self.assertEqual(400, self.client.request('DELETE', url, json=body).status_code)
        self.assertTrue(self.sql('SELECT id FROM classes WHERE id=?', (self.empty_class,)))
        body['confirmation_text'] = review['expected_confirmation_text']
        result = self.client.request('DELETE', url, json=body)
        self.assertEqual(200, result.status_code, result.text)
        self.assertFalse(self.sql('SELECT id FROM classes WHERE id=?', (self.empty_class,)))
        self.assertEqual(0, self.sql('SELECT COUNT(*) FROM agent_action_executions')[0][0])

    def test_stale_review_and_new_student_block_normal_delete(self):
        body = self.body(self.review())
        with self.connection() as conn:
            self._insert_student(conn, 'new-after-review', 'New', self.empty_class)
            conn.commit()
        result = self.client.request('DELETE', f'/api/manage/classes/{self.empty_class}', json=body)
        self.assertEqual(409, result.status_code, result.text)
        self.assertFalse(self.review()['can_execute'])
        self.assertTrue(self.sql('SELECT id FROM students WHERE class_id=?', (self.empty_class,)))

    def test_course_delete_preserves_other_course_and_rejects_other_actor(self):
        body = self.body(self.review('courses', self.empty_course))
        actor = dict(self.actor)
        self.actor = {'role':'student', 'id':self.teacher_id}
        self.assertEqual(403, self.client.request('DELETE', f'/api/manage/courses/{self.empty_course}', json=body).status_code)
        self.actor = {'role':'teacher', 'id':999999}
        self.assertEqual(403, self.client.get(f'/api/manage/courses/{self.empty_course}/delete-impact').status_code)
        self.actor = actor
        result = self.client.request('DELETE', f'/api/manage/courses/{self.empty_course}', json=body)
        self.assertEqual(200, result.status_code, result.text)
        self.assertTrue(self.sql('SELECT id FROM courses WHERE id=?', (self.course_id,)))

    def test_normal_merge_requires_hash_and_rejects_changed_same_count_content(self):
        selection = {'target_offering_id':self.target_id, 'source_offering_ids':[self.source_id]}
        path = '/api/manage/class_offerings/merge/'
        response = self.client.post(path+'preview', json=selection)
        self.assertEqual(200, response.status_code, response.text)
        preview = response.json()['preview']
        body = {**selection, 'confirm_class_name':preview['target']['class_name'], 'acknowledged_irreversible':True}
        self.assertEqual(400, self.client.post(path+'execute', json=body).status_code)
        body['expected_review_hash'] = preview['review_hash']
        self.sql("UPDATE assignments SET title='Changed after review' WHERE id=?", (self.source_assignment,))
        self.assertEqual(409, self.client.post(path+'execute', json=body).status_code)
        self.assertTrue(self.sql('SELECT id FROM class_offerings WHERE id=?', (self.source_id,)))
        body['expected_review_hash'] = self.client.post(path+'preview', json=selection).json()['preview']['review_hash']
        response = self.client.post(path+'execute', json=body)
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(self.sql('SELECT id FROM class_offerings WHERE id=?', (self.source_id,)))
        self.assertEqual(1, self.sql('SELECT COUNT(*) FROM offering_merge_archives')[0][0])
        self.assertTrue(self.sql('SELECT id FROM submissions WHERE assignment_id=?', (self.source_assignment,)))

    def test_confirmation_rejects_raw_oversize_and_strict_selection(self):
        body = self.body(self.review())
        body['confirmation_note'] = ' ' * 2000 + 'x'
        self.assertEqual(400, self.client.request('DELETE', f'/api/manage/classes/{self.empty_class}', json=body).status_code)
        for values in ([True], [self.source_id, self.source_id], list(range(20,32)), ['1']):
            with self.subTest(values=values):
                self.assertEqual(400, self.client.post('/api/manage/class_offerings/merge/preview', json={
                    'target_offering_id':self.target_id, 'source_offering_ids':values}).status_code)

    def test_json_mapping_candidate_rows_and_bytes_obey_review_limits(self):
        with self.connection() as conn:
            # Use the actual snapshot against a tiny independent mapping schema,
            # retaining all real policy and canonicalization code.
            conn.execute('CREATE TABLE synthetic_mapping_copy(id INTEGER,admin_class_ids_json TEXT)')
            conn.execute('INSERT INTO synthetic_mapping_copy VALUES(1,?)', (f'[{self.empty_class}0]',))
            conn.execute('INSERT INTO synthetic_mapping_copy VALUES(2,?)', (f'[{self.empty_class}]',))
            class MappingConnection:
                def execute(inner, sql, params=()):
                    return conn.execute(sql.replace('teacher_academic_teaching_class_mappings', 'synthetic_mapping_copy'), params)
            columns = {'teacher_academic_teaching_class_mappings':{'id','admin_class_ids_json'}}
            for limit in ({'MAX_REVIEW_ROWS':1}, {'MAX_REVIEW_BYTES':50}):
                with patch.object(lifecycle, '_columns', return_value=columns), patch.multiple(lifecycle, **limit):
                    with self.assertRaises(HTTPException) as failure:
                        lifecycle._snapshot(MappingConnection(), 'class', {'id':self.empty_class, 'name':'Empty'})
                    self.assertEqual(413, failure.exception.status_code)

    def test_logical_document_target_is_live_and_metadata_relink_is_atomic(self):
        with self.connection() as conn:
            with patch('classroom_app.db.schema_lesson_plans._SCHEMA_READY', False):
                lesson_plan_service.ensure_lesson_plan_schema(conn)
            plan = lesson_plan_service.create_lesson_plan(conn, teacher=self.actor, title='A plan',
                course_id=self.course_id, class_offering_id=self.source_id)
            conn.commit()
            with self.assertRaises(HTTPException):
                lesson_plan_service.update_attributes(conn, plan_id=plan, course_id=self.empty_course,
                    class_offering_id=self.source_id)
            conn.rollback()
            actual = conn.execute('SELECT course_id,class_offering_id FROM lesson_plans WHERE id=?', (plan,)).fetchone()
            self.assertEqual((self.course_id,self.source_id), tuple(actual))
            with self.assertRaises(HTTPException):
                lesson_plan_service.create_lesson_plan(conn, teacher=self.actor, title='Stale',
                    course_id=self.course_id, class_offering_id=999999)
            conn.rollback()
            self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM lesson_plans').fetchone()[0])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(TeachingLifecycleHTTPTests(name) for name, value in TeachingLifecycleHTTPTests.__dict__.items()
                             if name.startswith('test_') and callable(value))
