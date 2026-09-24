"""Account isolation, assessment policy and asynchronous assistant contracts."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from classroom_app.db.schema_ai_workspace import ensure_ai_workspace_schema, ai_workspace_schema_statements
from classroom_app.services import ai_workspace_service as store
from classroom_app.services.ai_workspace_policy import ai_workspace_policy, ensure_ai_workspace_access
from classroom_app.services.ai_workspace_stream import durable_stream, _active_tasks
from classroom_app.routers import ai as routes


TEACHER = {'id': 1, 'role': 'teacher', 'name': '教师甲'}
STUDENT = {'id': 1, 'role': 'student', 'name': '学生甲'}


def request(path='/', referer=''):
    return Request({'type': 'http', 'method': 'GET', 'path': path, 'headers': [(b'referer', referer.encode())]})


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lanshare-workspace-test-')
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE classroom_behavior_profiles(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT,profile_summary TEXT,hidden_premise_prompt TEXT,created_at TEXT);
                CREATE TABLE ai_psychology_profiles(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT,profile_summary TEXT,hidden_premise_prompt TEXT,created_at TEXT);
                CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER);
                CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER);
                CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
                CREATE TABLE assignments(id TEXT PRIMARY KEY,class_offering_id INTEGER,exam_paper_id TEXT,status TEXT,availability_mode TEXT,
                                         starts_at TEXT,due_at TEXT,closed_at TEXT,auto_close INTEGER DEFAULT 1,assessment_kind TEXT);
                CREATE TABLE submissions(assignment_id TEXT,student_pk_id INTEGER,is_absence_score INTEGER DEFAULT 0);
                CREATE TABLE learning_stage_exam_attempts(assignment_id TEXT,student_id INTEGER);
                INSERT INTO students VALUES(1,11),(2,22);
                INSERT INTO class_offerings VALUES(10,11),(20,22);
            ''')
            ensure_ai_workspace_schema(conn, engine='sqlite')
            ensure_ai_workspace_schema(conn, engine='sqlite')
        self.db_patch = patch.object(store, 'get_db_connection', self.connect)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

    def connect(self):
        conn = sqlite3.connect(self.db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(conn.close)
        return conn

    def session(self, user=TEACHER):
        with self.connect() as conn:
            return store.create_session(conn, user)

    def begin(self, session, request_id='req-1', user=TEACHER, message='我的问题'):
        with self.connect() as conn:
            return store.begin_request(conn, session['session_uuid'], user, request_id, message,
                                       [{'name': 'private.txt', 'type': 'text'}], {'message': message})

    def finish(self, state, user=TEACHER, success=True, answer='回答'):
        with self.connect() as conn:
            return store.finish_request(conn, state['session_id'], state['request_id'], user, answer, success=success)

    def test_owner_and_role_isolation_including_attachments(self):
        session = self.session()
        state = self.begin(session)
        self.finish(state)
        for foreign in (STUDENT, {**TEACHER, 'id': 2}):
            with self.connect() as conn:
                self.assertEqual(store.list_sessions(conn, foreign), [])
                with self.assertRaises(HTTPException) as error:
                    store.load_history(conn, session['session_uuid'], foreign)
                self.assertEqual(error.exception.status_code, 403)
                with self.assertRaises(HTTPException):
                    store.begin_request(conn, session['session_uuid'], foreign, 'hack', '', [], {})
        with self.connect() as conn:
            history = store.load_history(conn, session['session_uuid'], TEACHER)
        self.assertEqual(history['messages'][0]['attachments'][0]['name'], 'private.txt')
        self.assertFalse(history['pending'])

    def test_idempotency_failure_retry_and_cross_tab_lock(self):
        session = self.session()
        other = self.session()
        state = self.begin(session)
        self.assertTrue(self.begin(session)['pending'])
        with self.assertRaises(HTTPException):
            self.begin(other)
        with self.assertRaises(HTTPException):
            self.begin(session, message='不同的消息')
        self.finish(state, success=False)
        state = self.begin(session)
        self.finish(state)
        self.assertFalse(self.finish(state))
        self.assertTrue(self.begin(session)['replay'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT completed_rounds FROM ai_workspace_profile_states').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_messages').fetchone()[0], 2)

    def test_profile_four_round_claim_is_single_and_failure_backoff_preserves_count(self):
        session = self.session()
        for number in range(3):
            self.finish(self.begin(session, f'req-{number}'))
        self.assertEqual(store.claim_due_profile_candidates(), [])
        self.finish(self.begin(session, 'fourth'))
        claimed = store.claim_due_profile_candidates()
        self.assertEqual(len(claimed), 1)
        self.assertEqual(store.claim_due_profile_candidates(), [])
        with patch('classroom_app.services.psych_profile_service.load_explicit_user_profile', return_value={'name': '教师甲'}), \
             patch('classroom_app.services.ai_gateway_service.ai_gateway_post', new=AsyncMock(side_effect=RuntimeError('test'))):
            asyncio.run(store.run_profile_candidate(claimed[0]))
        self.assertEqual(store.claim_due_profile_candidates(), [])
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM ai_workspace_profile_states').fetchone()
        self.assertEqual((row['completed_rounds'], row['profiled_rounds']), (4, 0))
        self.assertIsNone(row['claim_token'])

    def test_successful_profile_injects_only_owner_and_keeps_explicit_profile_untouched(self):
        session = self.session()
        for number in range(4):
            self.finish(self.begin(session, f'req-{number}'))
        claimed = store.claim_due_profile_candidates()[0]
        response = httpx.Response(200, request=httpx.Request('POST', 'http://test.invalid'), json={
            'status': 'success', 'response_json': {'hidden_premise_prompt': '先给一个小例子', 'profile_summary': '喜欢动手'}})
        gateway = AsyncMock(return_value=response)
        with patch('classroom_app.services.psych_profile_service.load_explicit_user_profile', return_value={'name': '教师甲', 'description': '用户主动写的简介'}), \
             patch('classroom_app.services.ai_gateway_service.ai_gateway_post', new=gateway):
            asyncio.run(store.run_profile_candidate(claimed))
        gateway.assert_awaited_once()
        self.assertEqual(gateway.call_args.kwargs['json_payload']['task_priority'], 'background')
        with self.connect() as conn:
            profile = store.load_personal_hidden_profile(conn, 1, 'teacher')
            self.assertEqual(profile['hidden_premise_prompt'], '先给一个小例子')
            self.assertIsNone(store.load_personal_hidden_profile(conn, 1, 'student'))
        self.assertEqual(store.claim_due_profile_candidates(), [])

    def test_personal_profile_uses_latest_across_classes_without_cross_role_leak(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO classroom_behavior_profiles VALUES(1,1,'teacher','教师旧摘要','短句','2026-01-01')")
            conn.execute("INSERT INTO ai_psychology_profiles VALUES(1,1,'student','学生私密','学生专属','2026-12-01')")
            self.assertEqual(store.load_personal_hidden_profile(conn, 1, 'teacher')['hidden_premise_prompt'], '短句')

    def test_student_path_context_and_referer_are_independent_guards(self):
        self.assertFalse(ai_workspace_policy(STUDENT, request('/exam/take/1'))['allowed'])
        self.assertFalse(ai_workspace_policy(STUDENT, request('/assignment/1'))['allowed'])
        self.assertFalse(ai_workspace_policy(STUDENT, request('/submission/1'))['allowed'])
        self.assertTrue(ai_workspace_policy(TEACHER, request('/exam/take/1'))['allowed'])
        cases = [('/assignment/1', '', ''), ('/submission/1', '', ''), ('/', '/api/submissions/1', ''),
                 ('/', '/exam/take/1', ''), ('/', '', '{"page":"exam_take"}'),
                 ('/', '', '当前URL: /exam/take/1'), ('/', '', '{"page_path":"/assignment/1"}')]
        with self.connect() as conn:
            for path, referer, context in cases:
                with self.subTest(path=path, referer=referer, context=context), self.assertRaises(HTTPException):
                    ensure_ai_workspace_access(conn, STUDENT, request('/api/ai/workspace-chat', referer), page_path=path, extra_context=context)
            ensure_ai_workspace_access(conn, STUDENT, request('/api/ai/workspace-chat'), page_path='/student/dashboard')

    def test_submission_page_cannot_create_read_or_send_assistant_requests(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: STUDENT
        session = self.session(STUDENT)
        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes.ai_client, 'stream') as upstream, TestClient(app) as client:
            headers = {'referer': 'http://testserver/submission/123'}
            for method, path, kwargs in (
                ('get', '/api/ai/workspace/sessions', {}),
                ('post', '/api/ai/workspace/session/new', {}),
                ('get', '/api/ai/workspace/history/'+session['session_uuid'], {}),
                ('post', '/api/ai/workspace-chat', {'data': {'session_uuid': session['session_uuid'], 'message': '问题', 'page_path': '/'}}),
            ):
                with self.subTest(path=path):
                    self.assertEqual(getattr(client, method)(path, headers=headers, **kwargs).status_code, 403)
            upstream.assert_not_called()

    def test_real_active_exam_blocks_spoofed_page_but_not_other_class_or_submitted(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO assignments(id,class_offering_id,exam_paper_id,status,availability_mode,starts_at,due_at) VALUES('1',20,'paper','published','countdown','2020-01-01','2099-01-01')")
            ensure_ai_workspace_access(conn, STUDENT, request('/'))
            conn.execute('INSERT INTO class_offering_class_links VALUES(20,11)')
            with self.assertRaises(HTTPException):
                ensure_ai_workspace_access(conn, STUDENT, request('/'), page_path='/student/dashboard')
            ensure_ai_workspace_access(conn, TEACHER, request('/'))
            conn.execute("INSERT INTO submissions VALUES('1',1,0)")
            ensure_ai_workspace_access(conn, STUDENT, request('/'))

    def test_postgres_schema_has_real_workspace_fk_and_no_classroom_zero(self):
        text = '\n'.join(ai_workspace_schema_statements('postgres'))
        self.assertIn('BIGINT GENERATED BY DEFAULT AS IDENTITY', text)
        self.assertIn('REFERENCES ai_workspace_sessions(id)', text)
        self.assertNotIn('class_offering_id', text)

    def test_stream_disconnect_finishes_producer_and_does_not_spawn_duplicate(self):
        async def scenario():
            finished = asyncio.Event()
            release = asyncio.Event()
            async def source():
                yield 'first'
                await release.wait()
                finished.set()
                yield 'second'
            stream = durable_stream(source(), lambda event, **kwargs: event)
            self.assertEqual(await anext(stream), 'first')
            self.assertEqual(len(_active_tasks), 1)
            await stream.aclose()
            release.set()
            await asyncio.wait_for(finished.wait(), 1)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(len(_active_tasks), 0)
        asyncio.run(scenario())

    def test_api_history_personal_premise_replay_and_upstream_failure(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: TEACHER
        calls = []

        class Response:
            is_success = True
            async def aiter_lines(self):
                for event in ({'event':'answer_delta','delta':'好的，我们从例子开始。'}, {'event':'done'}):
                    yield json.dumps(event, ensure_ascii=False)

        @asynccontextmanager
        async def stream(*args, **kwargs):
            calls.append(kwargs['json'])
            if kwargs['json']['new_message'] == 'failure':
                raise httpx.ConnectError('test-only failure')
            yield Response()

        async def no_retrieval(*args):
            if False:
                yield ''

        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes, 'build_user_knowledge_block', return_value='自己的资料'), \
             patch.object(routes, 'load_explicit_user_profile', return_value={'nickname': '小老师'}), \
             patch.object(routes, '_gongwen_retrieval_events', no_retrieval), \
             patch.object(routes, '_platform_data_retrieval_events', no_retrieval), \
             patch.object(routes.ai_client, 'stream', stream), TestClient(app) as client:
            with self.connect() as conn:
                conn.execute("INSERT INTO ai_psychology_profiles VALUES(1,1,'teacher','私密摘要','先给一个小例子','2026-01-01')")
            created = client.post('/api/ai/workspace/session/new')
            self.assertEqual(created.status_code, 200, created.text)
            session = created.json()['session']
            data = {'session_uuid': session['session_uuid'], 'request_id': 'api-1', 'message': '第一问'}
            reply = client.post('/api/ai/workspace-chat', data=data)
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertIn('先给一个小例子', calls[0]['system_prompt'])
            self.assertNotIn('私密摘要', reply.text)
            history = client.get('/api/ai/workspace/history/'+session['session_uuid']).json()
            self.assertEqual(len(history['messages']), 2)
            self.assertFalse(history['pending'])
            self.assertEqual(client.post('/api/ai/workspace-chat', data=data).status_code, 200)
            self.assertEqual(len(calls), 1)
            data.update(request_id='api-2', message='第二问')
            self.assertEqual(client.post('/api/ai/workspace-chat', data=data).status_code, 200)
            self.assertEqual([item['role'] for item in calls[1]['messages']], ['user','assistant'])
            data.update(request_id='api-failure', message='failure')
            failure = client.post('/api/ai/workspace-chat', data=data)
            self.assertIn('error', failure.text)
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT completed_rounds FROM ai_workspace_profile_states').fetchone()[0], 2)
            history = client.get('/api/ai/workspace/history/'+session['session_uuid']).json()
            self.assertFalse(history['pending'])
            self.assertEqual(history['messages'][-1]['status'], 'failed')
            app.dependency_overrides[routes.get_current_user] = lambda: {**TEACHER, 'id': 2}
            self.assertEqual(client.get('/api/ai/workspace/history/'+session['session_uuid']).status_code, 403)

    def test_actual_route_disconnect_saves_answer_then_history_recovers(self):
        session = self.session()
        async def scenario():
            release = asyncio.Event()
            class Response:
                is_success = True
                async def aiter_lines(self):
                    await release.wait()
                    yield json.dumps({'event': 'answer_delta', 'delta': '切页后的完整回复'})
                    yield json.dumps({'event': 'done'})
            @asynccontextmanager
            async def upstream(*args, **kwargs):
                yield Response()
            async def no_retrieval(*args):
                if False:
                    yield ''
            with patch.object(routes, 'get_db_connection', self.connect), \
                 patch.object(routes, 'build_user_knowledge_block', return_value=''), \
                 patch.object(routes, 'load_explicit_user_profile', return_value={}), \
                 patch.object(routes, '_gongwen_retrieval_events', no_retrieval), \
                 patch.object(routes, '_platform_data_retrieval_events', no_retrieval), \
                 patch.object(routes.ai_client, 'stream', upstream):
                response = await routes.handle_ai_workspace_chat(request('/api/ai/workspace-chat'), files=[], message='问题',
                    user=TEACHER, deep_thinking=False, context_prompt_extra='', session_uuid=session['session_uuid'],
                    request_id='survives-navigation', page_path='/')
                self.assertIn('stream_start', await anext(response.body_iterator))
                await response.body_iterator.aclose()
                with self.connect() as conn:
                    self.assertTrue(store.load_history(conn, session['session_uuid'], TEACHER)['pending'])
                release.set()
                await asyncio.gather(*list(_active_tasks))
                with self.connect() as conn:
                    history = store.load_history(conn, session['session_uuid'], TEACHER)
                self.assertFalse(history['pending'])
                self.assertEqual(history['messages'][-1]['final_answer'], '切页后的完整回复')
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
