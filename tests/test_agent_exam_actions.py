"""Exam authoring/assignment use the normal service and one Agent transaction."""
import asyncio
import copy
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from tests.test_agent_platform_writes import PlatformWriteFixture, Request
from classroom_app.routers.homework_parts import exam_papers
from classroom_app.services import exam_paper_management_service as exams
from classroom_app.services import agent_platform_write_service as writes
from classroom_app.services.agent_action_registry import validate_action_params
from classroom_app.services.exam_json_service import EXAM_JSON_TEMPLATE


class AgentExamActionTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        self.questions = copy.deepcopy(EXAM_JSON_TEMPLATE)
        self.questions.pop('title', None)
        self.questions.pop('description', None)

    def create(self, token=None, **extra):
        result = writes.dispatch_write(self.conn, token or self.token(), 'create-exam', 'create_exam_paper',
            {'title': 'Synthetic exam', 'questions': self.questions, 'description': 'Review me', 'config': {'allow_student_ai': False}, **extra})
        self.conn.commit()
        return result['result']['paper_id']

    def assignment_params(self, paper_id, **extra):
        review = exams.get_exam_review(self.conn, paper_id=paper_id, teacher_id=7)
        return {'paper_id': paper_id, 'expected_paper_revision': review['paper_revision'],
                'class_offering_id': 40, 'assessment_kind': 'final', **extra}

    def test_create_edit_and_review_keep_native_questions_owner_scope_and_versions(self):
        token = self.token()
        paper_id = self.create(token)
        initial = exams.get_exam_review(self.conn, paper_id=paper_id, teacher_id=7)
        self.assertEqual('private', initial['paper']['scope_level'])
        self.assertEqual(1, self.count('exam_papers'))
        payload = {'paper_id': paper_id, 'expected_paper_revision': initial['paper_revision'],
                   'title': 'Edited exam', 'questions': self.questions}
        result = writes.dispatch_write(self.conn, token, 'edit-exam', 'update_unassigned_exam_paper', payload)
        self.conn.commit()
        content = result['result']['content']
        self.assertEqual('Review me', content['description'])
        self.assertEqual({'allow_student_ai': False}, content['config'])
        self.assertNotEqual(initial['paper_revision'], result['result']['paper_revision'])
        self.assertEqual(100, content['questions']['grading']['total_score'])
        self.assertTrue(writes.dispatch_write(self.conn, token, 'edit-exam', 'update_unassigned_exam_paper', payload)['replayed'])
        with self.assertRaises(HTTPException) as stale:
            writes.dispatch_write(self.conn, token, 'different-edit', 'update_unassigned_exam_paper', payload)
        self.assertEqual(409, stale.exception.status_code)
        self.conn.rollback()
        self.assertEqual('Edited exam', self.conn.execute('SELECT title FROM exam_papers').fetchone()[0])

    def test_assignment_classification_rubric_reminders_and_receipt_commit_or_rollback_together(self):
        token = self.token()
        paper_id = self.create(token)
        payload = self.assignment_params(paper_id, availability_mode='deadline', auto_close=True,
            due_at=(datetime.now() + timedelta(days=4)).isoformat())
        before_events = self.count('agent_action_executions')
        before_tasks = self.count('scheduled_tasks')
        first = writes.dispatch_write(self.conn, token, 'assign-exam', 'assign_exam_paper', payload)
        assignment_id = first['result']['assignment_id']
        row = dict(self.conn.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone())
        self.assertEqual(('final', 'ai', 'published'), (row['assessment_kind'], row['grading_mode'], row['status']))
        self.assertIn('评分', row['rubric_md'])
        self.assertEqual(1, self.count('assignment_classification_revisions'))
        reminders = self.conn.execute("SELECT dedupe_key FROM scheduled_tasks WHERE task_kind='assignment_due_reminder'").fetchall()
        self.assertEqual(2, len(reminders))
        self.conn.rollback()
        self.assertEqual((0, 0, before_events, before_tasks), (self.count('assignments'), self.count('assignment_classification_revisions'), self.count('agent_action_executions'), self.count('scheduled_tasks')))
        writes.dispatch_write(self.conn, token, 'assign-exam', 'assign_exam_paper', payload)
        self.conn.commit()
        self.assertTrue(writes.dispatch_write(self.conn, token, 'assign-exam', 'assign_exam_paper', payload)['replayed'])
        self.assertEqual(1, self.count('assignments'))
        self.assertEqual(1, self.count('assignment_classification_revisions'))
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE task_kind='assignment_due_reminder'").fetchone()[0])
        with self.assertRaises(HTTPException) as duplicate:
            writes.dispatch_write(self.conn, token, 'repeat-assignment', 'assign_exam_paper', self.assignment_params(paper_id))
        self.assertEqual(409, duplicate.exception.status_code)
        self.conn.rollback()

    def test_incomplete_rubric_and_foreign_class_never_produce_assignment_or_receipt(self):
        token = self.token()
        paper_id = self.create(token, questions={'pages': [{'questions': [{'type': 'text', 'text': 'No grading'}]}]})
        for payload in (self.assignment_params(paper_id), self.assignment_params(paper_id, class_offering_id=999)):
            with self.assertRaises(HTTPException) as error:
                writes.dispatch_write(self.conn, token, 'incomplete', 'assign_exam_paper', payload)
            self.assertIn(error.exception.status_code, (400, 404))
            self.conn.rollback()
        self.assertEqual(0, self.count('assignments'))
        self.assertEqual(1, self.count('agent_action_executions'))

    def test_assigned_paper_requires_new_version_even_without_any_student_response(self):
        token = self.token()
        paper_id = self.create(token)
        writes.dispatch_write(self.conn, token, 'assign', 'assign_exam_paper', self.assignment_params(paper_id))
        self.conn.commit()
        revision = exams.get_exam_review(self.conn, paper_id=paper_id, teacher_id=7)['paper_revision']
        with self.assertRaises(HTTPException) as blocked:
            writes.dispatch_write(self.conn, token, 'edit-assigned', 'update_unassigned_exam_paper',
                {'paper_id': paper_id, 'expected_paper_revision': revision, 'title': 'Replacement', 'questions': self.questions})
        self.assertEqual(409, blocked.exception.status_code)
        self.conn.rollback()
        self.assertEqual('Synthetic exam', self.conn.execute('SELECT title FROM exam_papers').fetchone()[0])
        self.assertEqual(0, self.count('submissions'))

    def test_normal_web_create_content_assign_use_shared_real_policy_and_formal_kind(self):
        with patch.object(exam_papers, 'get_db_connection', self.connection), patch.object(exam_papers, 'close_overdue_assignments'), \
             patch.object(exam_papers, '_build_assignment_storage_dir', return_value=MagicMock()):
            result = asyncio.run(exam_papers.create_exam_paper(Request({'title': 'Web exam', 'questions': self.questions}), user=self.teacher))
            paper_id = result['paper_id']
            changed = asyncio.run(exam_papers.put_exam_paper_content(paper_id, Request({'title': 'Web edited', 'questions': self.questions}), user=self.teacher))
            self.assertEqual('Web edited', changed['content']['title'])
            assigned = asyncio.run(exam_papers.assign_exam_paper(paper_id, Request({'class_offering_id': 40, 'assessment_kind': 'homework'}), user=self.teacher))
        self.assertEqual('homework', assigned['assessment_kind'])
        self.assertEqual('ai', self.conn.execute('SELECT grading_mode FROM assignments').fetchone()[0])
        self.assertEqual(0, self.count('agent_action_executions'))

    def test_catalog_is_bounded_owner_scoped_and_search_treats_wildcards_literally(self):
        paper_id = self.create(title='Exam 100%_done')
        own = exams.list_exam_reviews(self.conn, teacher_id=7, q='%_', limit=1)
        self.assertEqual([paper_id], [item['id'] for item in own['papers']])
        self.assertNotIn('questions_json', own['papers'][0])
        self.assertEqual([], exams.list_exam_reviews(self.conn, teacher_id=8)['papers'])
        with self.assertRaises(HTTPException):
            exams.get_exam_review(self.conn, teacher_id=8, paper_id=paper_id)
        with self.assertRaises(HTTPException):
            exams.list_exam_reviews(self.conn, teacher_id=7, limit=100)
        with self.assertRaises(HTTPException):
            writes.dispatch_write(self.conn, self.token(self.student), 'student-create', 'create_exam_paper', {'title': 'No', 'questions': self.questions})

    def test_structured_question_bounds_fail_closed_without_silently_truncating(self):
        cycle = {}
        cycle['nested'] = cycle
        malformed = [True, [], {'bad': float('nan')}, {'bad': '\ud800'}, {'oversize': 'x' * 180001}, cycle]
        for value in malformed:
            with self.subTest(kind=type(value).__name__):
                clean, errors = validate_action_params('create_exam_paper', {'title': 'Synthetic', 'questions': value}, reject_unknown=True)
                self.assertTrue(errors)
                self.assertNotIn('questions', clean)
        valid, errors = validate_action_params('create_exam_paper', {'title': 'Synthetic', 'questions': self.questions}, reject_unknown=True)
        self.assertEqual([], errors)
        self.assertEqual(self.questions, valid['questions'])
        valid['questions']['grading']['total_score'] = 10
        self.assertEqual(100, self.questions['grading']['total_score'])
