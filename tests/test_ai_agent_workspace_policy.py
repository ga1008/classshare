"""Agent endpoints share the assistant assessment boundary before task work."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers import agent_tasks as routes
from tests import test_ai_workspace as fixture

STUDENT, TEACHER = fixture.STUDENT, fixture.TEACHER


class AgentWorkspacePolicyTests(unittest.TestCase):
    setUp = fixture.WorkspaceTests.setUp
    connect = fixture.WorkspaceTests.connect

    def client(self, user):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: {**user, 'session_id': 'synthetic-session'}
        return TestClient(app)

    def assert_blocked_actions(self, client, referer):
        for path in ('', '/composer', '/1/follow-up', '/1/retry', '/1/questions/q/answer'):
            with self.subTest(path=path, referer=referer):
                response = client.post('/api/agent-tasks' + path, json={'instruction': 'question', 'answers': []},
                                       headers={'referer': referer})
                self.assertEqual(response.status_code, 403, response.text)

    def test_assessment_referers_guard_every_action_before_actor_or_task_work(self):
        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes, 'resolve_agent_actor') as actor, self.client(STUDENT) as client:
            for path in ('/assignment/1', '/exam/take/1', '/submission/1'):
                self.assert_blocked_actions(client, 'http://testserver' + path)
            actor.assert_not_called()

    def test_real_active_exam_blocks_harmless_referrer_for_every_action(self):
        with self.connect() as conn:
            conn.execute("""INSERT INTO assignments(id,class_offering_id,exam_paper_id,status,availability_mode,
                starts_at,due_at,assessment_kind) VALUES('exam',10,'paper','published','countdown',
                '2020-01-01T00:00:00','2099-01-01T00:00:00','final')""")
        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes, 'resolve_agent_actor') as actor, self.client(STUDENT) as client:
            self.assert_blocked_actions(client, 'http://testserver/')
            actor.assert_not_called()

    def test_client_assessment_context_cannot_override_harmless_source(self):
        actor = SimpleNamespace(as_user=lambda: STUDENT)
        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes, 'resolve_agent_actor', return_value=actor), \
             patch.object(routes, 'AGENT_TASKS_ENABLED', True), \
             patch.object(routes, 'create_agent_task') as create, \
             patch.object(routes, 'get_agent_task') as get_task, \
             patch.object(routes, 'create_retry_task') as retry, self.client(STUDENT) as client:
            for endpoint in ('', '/composer', '/1/follow-up', '/1/retry'):
                for path in ('/assignment/1', '/exam/take/1', '/submission/1'):
                    response = client.post('/api/agent-tasks' + endpoint,
                        json={'instruction': 'question', 'page_context': {'page': {'path': path}}},
                        headers={'referer': 'http://testserver/'})
                    self.assertEqual(response.status_code, 403, response.text)
            create.assert_not_called()
            get_task.assert_not_called()
            retry.assert_not_called()

    def test_teacher_assessment_context_is_allowed_and_students_never_get_agent(self):
        with patch.object(routes, 'get_db_connection', self.connect),              patch.object(routes, 'create_agent_task') as create, self.client(STUDENT) as client:
            response = client.post('/api/agent-tasks', json={'instruction': 'question', 'page_context': {'page': {'path': '/classroom/10'}}},
                                   headers={'referer': 'http://testserver/classroom/10'})
            self.assertEqual(response.status_code, 403, response.text)  # Agent is teacher-only; students keep AI chat
            create.assert_not_called()
        for user, path in ((TEACHER, '/assignment/1'),):
            with self.subTest(user=user), patch.object(routes, 'get_db_connection', self.connect), \
                 patch.object(routes, 'resolve_agent_actor', return_value=SimpleNamespace(as_user=lambda: user)), \
                 patch.object(routes, 'AGENT_TASKS_ENABLED', True), \
                 patch.object(routes, 'create_agent_task', return_value={'id': 1}) as create, \
                 patch.object(routes, 'generate_agent_task_title', new=AsyncMock()), self.client(user) as client:
                response = client.post('/api/agent-tasks', json={'instruction': 'question', 'page_context': {'page': {'path': path}}},
                                       headers={'referer': 'http://testserver' + path})
                self.assertEqual(response.status_code, 200, response.text)
                create.assert_called_once()
