"""Teacher exam permissions are verified against current records, not page flags."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from classroom_app.db.schema_ai_workspace import ensure_ai_workspace_schema
from classroom_app.routers import ai as routes
from classroom_app.services import ai_workspace_service as store
from classroom_app.services.assessment_ai_access import (
    exam_assistance_scope, student_exam_ai_access, without_question_ai_permissions,
)
from classroom_app.services.ai_workspace_policy import ai_workspace_policy, ensure_ai_workspace_access


STUDENT = {'id': 1, 'role': 'student', 'name': '合成学生'}


def request(path='/api/ai/workspace-chat', referer=''):
    return Request({'type': 'http', 'method': 'GET', 'path': path,
                    'headers': [(b'referer', referer.encode())]})


class AssessmentAiAccessTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='assessment-ai-test-'))
        self.db = Path(directory) / 'isolated.db'
        self.questions = {'pages': [{'questions': [
            {'id': 'allowed', 'type': 'analysis', 'text': 'ALLOWED_QUESTION', 'allow_ai': True,
             'answer': 'TEACHER_SECRET', 'options': ['ALLOWED_OPTION'], 'grading_guidance': 'SECRET_RUBRIC'},
            {'id': 'forbidden', 'type': 'text', 'text': 'FORBIDDEN_QUESTION',
             'blanks': [{'id': '1', 'answer': 'SECRET_BLANK'}]},
        ]}]}
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE classroom_behavior_profiles(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT,profile_summary TEXT,hidden_premise_prompt TEXT,created_at TEXT);
                CREATE TABLE ai_psychology_profiles(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT,profile_summary TEXT,hidden_premise_prompt TEXT,created_at TEXT);
                CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER,enrollment_status TEXT DEFAULT 'active');
                CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER);
                CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
                CREATE TABLE assignments(id TEXT PRIMARY KEY,class_offering_id INTEGER,exam_paper_id TEXT,status TEXT,
                    availability_mode TEXT,starts_at TEXT,due_at TEXT,closed_at TEXT,auto_close INTEGER DEFAULT 1,assessment_kind TEXT);
                CREATE TABLE submissions(id INTEGER PRIMARY KEY,assignment_id TEXT,student_pk_id INTEGER,status TEXT,
                    is_absence_score INTEGER DEFAULT 0,resubmission_allowed INTEGER DEFAULT 0,resubmission_due_at TEXT);
                CREATE TABLE learning_stage_exam_attempts(id INTEGER PRIMARY KEY,assignment_id TEXT,student_id INTEGER);
                CREATE TABLE exam_papers(id TEXT PRIMARY KEY,title TEXT,questions_json TEXT,exam_config_json TEXT);
                INSERT INTO students(id,class_id) VALUES(1,11),(2,22);
                INSERT INTO class_offerings VALUES(10,11),(20,22);
                INSERT INTO assignments(id,class_offering_id,exam_paper_id,status,availability_mode)
                    VALUES('exam',10,'paper','published','permanent');
            ''')
            conn.execute('INSERT INTO exam_papers VALUES(?,?,?,?)', ('paper', '考试', json.dumps(self.questions), '{}'))
            ensure_ai_workspace_schema(conn, engine='sqlite')
            from classroom_app.services.assignment_student_window_service import active_window
            active_window(conn, 'exam', 1)
        self.enterContext(patch.object(store, 'get_db_connection', self.connect))

    def connect(self):
        conn = sqlite3.connect(self.db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def access(self, conn, **kwargs):
        return ensure_ai_workspace_access(conn, STUDENT, request(referer='/exam/take/exam'), **kwargs)

    def test_scope_has_only_authorized_question_text_and_options_never_grading_or_blank_answers(self):
        with self.connect() as conn:
            scope = self.access(conn)
        self.assertEqual(scope['question_ids'], ['allowed'])
        self.assertFalse(scope['whole_paper'])
        self.assertIn('ALLOWED_QUESTION', scope['context'])
        self.assertIn('ALLOWED_OPTION', scope['context'])
        for text in ('FORBIDDEN_QUESTION', 'TEACHER_SECRET', 'SECRET_RUBRIC', 'SECRET_BLANK'):
            self.assertNotIn(text, scope['context'])

    def test_assignment_id_case_is_preserved_when_resolving_the_source(self):
        with self.connect() as conn:
            conn.execute("UPDATE assignments SET id='ExamCase'")
            scope = ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/exam/take/ExamCase')
            self.assertEqual(scope['assignment_id'], 'ExamCase')
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/exam/take/examcase')

    def test_exam_document_navigation_uses_current_route_but_api_rejects_conflicting_sources(self):
        with self.connect() as conn:
            scope = ensure_ai_workspace_access(conn, STUDENT, request('/exam/take/exam', '/submission/1'))
            self.assertTrue(scope['allowed'])
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request(referer='/submission/1'), page_path='/exam/take/exam')

    def test_canonical_false_overrides_old_alias_and_whole_paper_can_authorize_all(self):
        paper = {'questions_json': self.questions, 'exam_config_json': {'allow_student_ai': True}}
        self.assertEqual(exam_assistance_scope(paper)['question_ids'], ['allowed', 'forbidden'])
        paper['exam_config_json'] = {'allow_student_ai': False, 'student_ai_enabled': True}
        paper['questions_json']['pages'][0]['questions'][0].update(allow_ai=False, ai_allowed=True)
        self.assertFalse(exam_assistance_scope(paper)['allowed'])
        self.assertFalse(exam_assistance_scope({'questions_json': {'pages': None}})['allowed'])

    def test_template_mount_requires_server_verified_matching_user_and_path(self):
        req = request('/exam/take/exam')
        self.assertFalse(ai_workspace_policy(STUDENT, req)['allowed'])
        with self.connect() as conn:
            req.state.exam_ai_access = self.access(conn)
        self.assertTrue(ai_workspace_policy(STUDENT, req)['allowed'])
        self.assertFalse(ai_workspace_policy({**STUDENT, 'id': 2}, req)['allowed'])
        req.state.exam_ai_access['page_path'] = '/exam/take/other'
        self.assertFalse(ai_workspace_policy(STUDENT, req)['allowed'])

    def test_fake_flags_wrong_assignment_student_and_multiple_sources_cannot_authorize(self):
        with self.connect() as conn:
            for page_path, referer, context in (
                ('/exam/take/missing', '', '{"assessmentAi":{"allowed":true}}'),
                ('/exam/take/exam', '/exam/take/missing', ''),
                ('/student/dashboard', '/submission/1', '{"allow_ai":true}'),
                ('/', '', '{"page":"exam_take","allow_ai":true}'),
            ):
                with self.subTest(page_path=page_path, referer=referer), self.assertRaises(HTTPException):
                    ensure_ai_workspace_access(conn, STUDENT, request(referer=referer),
                                               page_path=page_path, extra_context=context)
            self.assertFalse(student_exam_ai_access(conn, {**STUDENT, 'id': 2}, 'exam')['allowed'])
            # No Referer is required: a real assignment source still undergoes full database authorization.
            self.assertTrue(ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/exam/take/exam')['allowed'])
            conn.execute("UPDATE exam_papers SET questions_json='{}'")
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/exam/take/exam',
                                           extra_context='{"assessmentAi":{"allowed":true}}')

    def test_submission_deadline_unpublished_and_revoked_permission_are_rechecked(self):
        with self.connect() as conn:
            self.assertTrue(self.access(conn)['allowed'])
            conn.execute("INSERT INTO submissions VALUES(1,'exam',1,'submitted',0,0,NULL)")
            with self.assertRaises(HTTPException): self.access(conn)
            conn.execute("UPDATE submissions SET resubmission_allowed=1,resubmission_due_at='2099-01-01'")
            self.assertTrue(self.access(conn)['allowed'])
            conn.execute("UPDATE submissions SET resubmission_due_at='2000-01-01'")
            with self.assertRaises(HTTPException): self.access(conn)
            conn.execute('DELETE FROM submissions')
            conn.execute("UPDATE assignments SET availability_mode='deadline',due_at='2000-01-01'")
            with self.assertRaises(HTTPException): self.access(conn)
            conn.execute("UPDATE assignments SET due_at='2099-01-01',status='new'")
            with self.assertRaises(HTTPException): self.access(conn)
            conn.execute("UPDATE assignments SET status='published',availability_mode='permanent'")
            conn.execute("UPDATE exam_papers SET questions_json='{}'")
            with self.assertRaises(HTTPException): self.access(conn)

    def test_personal_late_window_is_student_scoped(self):
        from classroom_app.services.assignment_student_window_service import ensure_student_window_schema
        with self.connect() as conn:
            conn.execute("UPDATE assignments SET status='closed'")
            ensure_student_window_schema(conn)
            # The schema cache can be warm from another temporary database.
            from classroom_app.services.assignment_student_window_service import active_window
            active_window(conn, 'exam', 1)
            conn.execute("INSERT INTO assignment_student_windows(assignment_id,student_pk_id,opens_at,due_at) VALUES('exam',1,'2000-01-01','2099-01-01')")
            self.assertTrue(self.access(conn)['allowed'])
            conn.execute("UPDATE assignment_student_windows SET status='revoked'")
            with self.assertRaises(HTTPException): self.access(conn)

    def test_active_timed_exam_is_not_a_cross_tab_bypass(self):
        with self.connect() as conn:
            conn.execute("UPDATE assignments SET availability_mode='countdown',starts_at='2000-01-01',due_at='2099-01-01'")
            self.assertTrue(self.access(conn)['allowed'])
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/student/dashboard')
            conn.execute("INSERT INTO assignments(id,class_offering_id,exam_paper_id,status,availability_mode,starts_at,due_at) VALUES('other',10,'paper','published','countdown','2000-01-01','2099-01-01')")
            with self.assertRaises(HTTPException): self.access(conn)
            conn.execute("INSERT INTO submissions VALUES(1,'other',1,'submitted',0,0,NULL)")
            self.assertTrue(self.access(conn)['allowed'])

    def test_returned_exam_and_personal_window_block_cross_tab_chat(self):
        with self.connect() as conn:
            conn.execute("UPDATE assignments SET availability_mode='countdown',status='closed',due_at='2000-01-01'")
            conn.execute("INSERT INTO submissions VALUES(1,'exam',1,'submitted',0,1,'2099-01-01')")
            self.assertTrue(self.access(conn)['allowed'])
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/student/dashboard')
            conn.execute("UPDATE submissions SET resubmission_due_at='2000-01-01'")
            ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/student/dashboard')
            conn.execute('DELETE FROM submissions')
            conn.execute("INSERT INTO assignment_student_windows(assignment_id,student_pk_id,opens_at,due_at) VALUES('exam',1,'2000-01-01','2099-01-01')")
            self.assertTrue(self.access(conn)['allowed'])
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/student/dashboard')
            conn.execute("UPDATE assignment_student_windows SET status='revoked'")
            ensure_ai_workspace_access(conn, STUDENT, request(), page_path='/student/dashboard')

    def test_only_permission_metadata_is_exempt_from_grading_lock_comparison(self):
        original = self.questions
        changed = json.loads(json.dumps(original))
        changed['pages'][0]['questions'][0]['allow_ai'] = False
        changed['pages'][0]['questions'][1]['allow_student_ai'] = True
        self.assertEqual(without_question_ai_permissions(original), without_question_ai_permissions(changed))
        self.assertTrue(original['pages'][0]['questions'][0]['allow_ai'])
        changed['pages'][0]['questions'][0]['answer'] = 'altered'
        self.assertNotEqual(without_question_ai_permissions(original), without_question_ai_permissions(changed))

    def test_real_workspace_request_ignores_forged_page_text_and_rechecks_revocation(self):
        app = FastAPI(); app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: STUDENT
        calls = []

        class Response:
            is_success = True
            async def aiter_lines(self):
                yield json.dumps({'event': 'answer_delta', 'delta': '允许题目的学习提示'})
                yield json.dumps({'event': 'done'})

        @asynccontextmanager
        async def stream(*args, **kwargs):
            calls.append(kwargs['json']); yield Response()

        async def no_retrieval(*args, **kwargs):
            if False: yield ''

        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes, 'build_user_knowledge_block', return_value=''), \
             patch.object(routes, 'load_explicit_user_profile', return_value={}), \
             patch.object(store, 'load_personal_hidden_profile', return_value=None), \
             patch.object(routes, '_gongwen_retrieval_events', no_retrieval), \
             patch.object(routes, '_platform_data_retrieval_events', no_retrieval), \
             patch.object(routes, '_material_knowledge_retrieval_events', no_retrieval), \
             patch.object(routes, '_classroom_action_proposal_events', no_retrieval), \
             patch.object(routes.ai_client, 'stream', stream), TestClient(app) as client:
            headers = {'referer': 'http://testserver/exam/take/exam'}
            created = client.post('/api/ai/workspace/session/new', headers=headers)
            self.assertEqual(created.status_code, 200, created.text)
            session = created.json()['session']['session_uuid']
            ordinary = {'session_uuid': session, 'request_id': 'ordinary', 'message': 'OLD_UNAUTHORIZED_CONTEXT'}
            self.assertEqual(client.post('/api/ai/workspace-chat', data=ordinary).status_code, 200)
            # A request admitted outside the exam cannot be replayed inside it.
            self.assertEqual(client.post('/api/ai/workspace-chat', data={**ordinary, 'page_path': '/exam/take/exam'}, headers=headers).status_code, 409)
            payload = {'session_uuid': session, 'request_id': 'test-1', 'message': '解释允许的题目',
                       'page_path': '/exam/take/exam', 'page_text': 'CLIENT_FORBIDDEN_FULL_PAPER',
                       'context_prompt_extra': 'CLIENT_FORGED_CONTEXT'}
            response = client.post('/api/ai/workspace-chat', data=payload, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]['messages'], [])
            prompt = calls[1]['system_prompt']
            self.assertIn('ALLOWED_QUESTION', prompt)
            for secret in ('CLIENT_FORBIDDEN', 'CLIENT_FORGED', 'FORBIDDEN_QUESTION', 'TEACHER_SECRET', 'SECRET_BLANK'):
                self.assertNotIn(secret, prompt)
            self.assertEqual(client.post('/api/ai/workspace-chat', data={**payload, 'request_id': 'same-scope-followup', 'message': '继续说明'}, headers=headers).status_code, 200)
            self.assertEqual([item['role'] for item in calls[2]['messages']], ['user', 'assistant'])
            self.assertNotIn('OLD_UNAUTHORIZED_CONTEXT', json.dumps(calls[2]['messages']))
            with self.connect() as conn:
                conn.execute('UPDATE exam_papers SET exam_config_json=?', ('{"allow_student_ai":true}',))
            # Even in the same paper, changing which questions are authorized
            # changes the history/replay boundary.
            self.assertEqual(client.post('/api/ai/workspace-chat', data=payload, headers=headers).status_code, 409)
            self.assertEqual(client.post('/api/ai/workspace-chat', data={**payload, 'request_id': 'changed-scope'}, headers=headers).status_code, 200)
            self.assertEqual(calls[3]['messages'], [])
            with self.connect() as conn:
                conn.execute("UPDATE exam_papers SET questions_json='{}'")
            for method, path, values in (
                ('get', '/api/ai/workspace/sessions', {}),
                ('get', '/api/ai/workspace/history/' + session, {}),
                ('post', '/api/ai/workspace-chat', {'data': {**payload, 'request_id': 'test-2'}}),
            ):
                response = getattr(client, method)(path, headers=headers, **values)
                self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(len(calls), 4)

    def test_context_only_source_survives_upload_preparation_and_revocation_is_denied(self):
        app = FastAPI(); app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: STUDENT
        prepare_uploads = routes._prepare_chat_uploads

        async def revoke_during_upload(*args, **kwargs):
            with self.connect() as conn:
                conn.execute("UPDATE exam_papers SET questions_json='{}'")
            return await prepare_uploads(*args, **kwargs)

        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes.ai_client, 'stream') as upstream, TestClient(app) as client:
            created = client.post('/api/ai/workspace/session/new', headers={'referer': '/exam/take/exam'})
            session = created.json()['session']['session_uuid']
            with patch.object(routes, '_prepare_chat_uploads', revoke_during_upload):
                response = client.post('/api/ai/workspace-chat', data={
                    'session_uuid': session, 'request_id': 'context-only', 'message': '允许的提问',
                    'context_prompt_extra': '{"page_path":"/exam/take/exam"}',
                })
            self.assertEqual(response.status_code, 403, response.text)
            upstream.assert_not_called()


if __name__ == '__main__':
    unittest.main()
