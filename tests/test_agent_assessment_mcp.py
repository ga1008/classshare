"""Native MCP calls traverse actual mounted teacher assessment read handlers."""
import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_agent_platform_writes import PlatformWriteFixture
from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.routers import agent_bridge
from classroom_app.routers.homework_parts import grading, exam_papers
from classroom_app.services.assignment_creation_service import create_assignment_record
from classroom_app.services.exam_paper_management_service import create_exam_paper_record


class AssessmentMcpTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        ensure_agent_request_budget_schema(self.conn)
        self.assignment = create_assignment_record(self.conn, teacher_id=7, course_id=20,
            data={'title': 'Synthetic assessment', 'class_offering_id': 40, 'rubric_md': 'Reviewed rubric'})['id']
        self.paper = create_exam_paper_record(self.conn, teacher_id=7,
            data={'title': 'Synthetic paper', 'questions': {'pages': []}, 'scope_level': 'private'})['paper_id']
        self.conn.execute("INSERT INTO submissions(id,assignment_id,student_pk_id,student_name,status,answers_json,submitted_at) VALUES(80,?,7,'Student 7','submitted','answer','2026-09-10')", (self.assignment,))
        for pk in range(1, 53):
            self.conn.execute("INSERT INTO submission_files(id,submission_id,original_filename,mime_type,file_size,stored_path) VALUES(?,80,?,'text/plain',3,'server-private-path')", (pk, 'answer-' + str(pk) + '.txt'))
        self.conn.commit()
        self.tokens = {role: self.token(user, scopes=['platform:read']) for role, user in [('teacher', self.teacher), ('student', self.student)]}
        for name in ('classroom_app.database.get_db_connection', 'classroom_app.dependencies.get_db_connection', 'classroom_app.routers.agent_bridge.get_db_connection',
                     'classroom_app.services.agent_platform_broker.get_db_connection', 'classroom_app.services.agent_gateway_budget.get_db_connection',
                     'classroom_app.routers.homework_parts.grading.get_db_connection', 'classroom_app.routers.homework_parts.exam_papers.get_db_connection'):
            guard = patch(name, self.connection)
            guard.start()
            self.addCleanup(guard.stop)
        app = FastAPI()
        app.include_router(agent_bridge.router)
        app.include_router(grading.router, prefix='/api')
        app.include_router(exam_papers.router, prefix='/api')
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def call(self, name, arguments, role='teacher'):
        return self.client.post('/api/agent-bridge/mcp', headers={'Authorization': 'Bearer ' + self.tokens[role]},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})

    def test_all_review_keys_use_normal_handlers_and_keep_attachment_storage_paths_private(self):
        for key, path, query in [('submission.review', {'submission_id': 80}, {}),
                                 ('assignment.review_submissions', {'assignment_id': str(self.assignment)}, {}),
                                 ('submission.review_files', {'submission_id': 80}, {'limit': 50, 'offset': 50}),
                                 ('exam.catalog', {}, {}), ('exam.review', {'paper_id': self.paper}, {})]:
            with self.subTest(key=key):
                response = self.call('platform_read', {'operation_key': key, 'path_params': path, 'query_params': query})
                self.assertEqual(200, response.status_code, response.text)
                value = response.json()['result']
                self.assertFalse(value['isError'], value)
                text = value['content'][0]['text']
                self.assertNotIn('server-private-path', text)
                data = json.loads(text)['data']
                if key == 'submission.review':
                    self.assertEqual('answer', data['submission']['answers_json'])
                    self.assertEqual('Reviewed rubric', data['assignment']['rubric_md'])
                    self.assertEqual(50, len(data['files']))
                    self.assertTrue(data['files_has_more'])
                if key == 'submission.review_files':
                    self.assertEqual([51, 52], [item['submission_file_id'] for item in data['items']])
                    self.assertFalse(data['has_more'])

    def test_human_confirmation_catalog_and_teacher_only_reads_do_not_grant_student_access(self):
        response = self.call('platform_capabilities', {'keys': ['publish_classroom_grades']})
        value = json.loads(response.json()['result']['content'][0]['text'])
        self.assertEqual([], value['writes']['actions'])
        self.assertEqual('user_confirmation', value['user_input_actions'][0]['execution_mode'])
        self.assertFalse(value['user_input_actions'][0]['executable'])
        response = self.call('platform_read', {'operation_key': 'submission.review', 'path_params': {'submission_id': 80}}, role='student')
        self.assertTrue(response.json()['result']['isError'])
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        self.conn.commit()
        response = self.call('platform_read', {'operation_key': 'exam.review', 'path_params': {'paper_id': self.paper}})
        self.assertEqual(401, response.status_code)
