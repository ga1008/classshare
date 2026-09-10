"""Actual student form routes, DB commits and confined task-file uploads."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from contextlib import contextmanager
from unittest.mock import patch
import uuid

from fastapi import FastAPI, HTTPException

from tests.test_agent_platform_writes import PlatformWriteFixture
from classroom_app.db import schema_ai_jobs, schema_study_group_scheme
from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.routers.homework_parts import common, drafts, submissions
from classroom_app.services import agent_platform_request_service as requests
from classroom_app.services import agent_platform_multipart_service as multipart
from classroom_app.services.assignment_creation_service import create_assignment_record


class AgentSubmissionRequestsTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / 'agent' / 'tasks' / '11'
        self.workspace.mkdir(parents=True)
        (self.workspace / 'answer.txt').write_bytes(b'Synthetic answer attachment')
        self.guarded('classroom_app.services.agent_platform_multipart_service.AGENT_TASK_WORKSPACE_ROOT', self.root / 'agent')
        self.guarded('classroom_app.db.schema_ai_jobs._SCHEMA_READY_ENGINES', set())
        self.guarded('classroom_app.db.schema_study_group_scheme._SCHEMA_READY', False)
        schema_ai_jobs.ensure_ai_job_schema(self.conn, engine='sqlite')
        schema_study_group_scheme.ensure_study_group_scheme_schema(self.conn)
        ensure_agent_platform_requests_schema(self.conn)
        for module in (common, drafts, submissions):
            self.guarded(module.__name__ + '.get_db_connection', self.connection)
            self.guarded(module.__name__ + '._build_submission_storage_dir', lambda course, assignment, student: self.root / 'submissions' / str(student) / str(assignment))
            self.guarded(module.__name__ + '._build_submission_draft_storage_dir', lambda course, assignment, student: self.root / 'drafts' / str(student) / str(assignment))
        for target in ('classroom_app.database.get_db_connection', 'classroom_app.dependencies.get_db_connection',
                       'classroom_app.services.agent_platform_request_service.get_db_connection'):
            self.guarded(target, self.connection)
        self.guarded('classroom_app.routers.homework_parts.submissions.record_behavior_event', lambda **kwargs: None)
        self.assignment = str(create_assignment_record(self.conn, teacher_id=7, course_id=20,
            data={'title': 'Student upload fixture', 'class_offering_id': 40, 'status': 'published', 'grading_mode': 'manual'})['id'])
        self.conn.commit()
        self.bearer = self.token(self.student, scopes=['platform:read', 'platform:write'])
        self.teacher_bearer = self.token(self.teacher, scopes=['platform:read', 'platform:write'])
        self.app = FastAPI()
        self.app.include_router(drafts.router, prefix='/api')
        self.app.include_router(submissions.router, prefix='/api')

    def guarded(self, *args):
        guard = patch(*args)
        guard.start()
        self.addCleanup(guard.stop)

    def call(self, action, *, operation_id=None, token=None, **kwargs):
        return asyncio.run(requests.dispatch_platform_request(self.app, token or self.bearer,
            'http.assignment.' + action, operation_id or str(uuid.uuid4()), path_params={'assignment_id': self.assignment}, **kwargs))

    def test_draft_file_submit_and_withdraw_follow_original_student_workflow(self):
        original = self.call('draft.get')['result']['data']
        self.assertFalse(original['exists'])
        body = {'answers_json': {'answer': 'Synthetic answer'}, 'expected_submission_version': original['submission_version'], 'current_page': 2}
        operation_id = str(uuid.uuid4())
        saved = self.call('draft.save', operation_id=operation_id, body=body, files=[{'path': 'answer.txt'}])
        self.assertEqual('observed_http_result', saved['status'], saved)
        self.assertEqual(1, self.count('submission_draft_files'))
        replay = self.call('draft.save', operation_id=operation_id, body=body, files=[{'path': 'answer.txt'}])
        self.assertEqual(saved, replay)
        self.assertEqual(1, self.count('submission_draft_files'))
        latest = self.call('draft.get')['result']['data']
        submitted = self.call('submit', body={'answers_json': {'answer': 'Synthetic final answer'}, 'use_server_draft': True,
                                             'expected_submission_version': latest['submission_version']})
        self.assertEqual('observed_http_result', submitted['status'], submitted)
        self.assertEqual(1, self.count('submissions'))
        self.assertEqual(1, self.count('submission_files'))
        row = self.conn.execute('SELECT * FROM submissions').fetchone()
        self.assertEqual((7, 'student', 'submitted'), (row['student_pk_id'], row['submitted_by_role'], row['status']))
        self.assertIn('Synthetic final answer', row['answers_json'])
        stored = self.conn.execute('SELECT stored_path FROM submission_files').fetchone()[0]
        self.assertEqual(b'Synthetic answer attachment', Path(stored).read_bytes())
        result = self.call('withdraw')
        self.assertEqual('observed_http_result', result['status'], result)
        self.assertEqual(0, self.count('submissions'))
        self.assertEqual(0, self.count('submission_files'))
        self.assertFalse(Path(stored).exists())
        self.assertTrue((self.workspace / 'answer.txt').exists())

    def test_role_foreign_membership_and_stale_submission_version_are_enforced(self):
        denied = self.call('draft.get', token=self.teacher_bearer)
        self.assertEqual(403, denied['result']['http_status'])
        stale = self.call('draft.save', body={'answers_json': {'answer': 'stale'}, 'expected_submission_version': 'stale-round'})
        self.assertEqual(409, stale['result']['http_status'])
        self.assertEqual(0, self.count('submission_drafts'))
        self.conn.execute('UPDATE class_offerings SET class_id=999 WHERE id=40')
        self.conn.commit()
        denied = self.call('submit', body={'answers_json': {'answer': 'outsider'}, 'expected_submission_version': 'unsubmitted'})
        self.assertEqual(403, denied['result']['http_status'])
        self.assertEqual(0, self.count('submissions'))

    def test_task_path_and_changed_file_are_rejected_before_durable_admission(self):
        for ref in ({'path': '../secret.txt'}, {'path': 'BRIDGE.md'}, {'path': 'answer.txt', 'parent_task_id': 10},
                    {'path': 'answer.txt', 'sha256': '0' * 64}):
            with self.subTest(ref=ref), self.assertRaises(HTTPException):
                self.call('draft.save', body={'expected_submission_version': 'unsubmitted'}, files=[ref])
        self.assertEqual(0, self.count('agent_platform_requests'))
        self.assertEqual(0, self.count('submission_drafts'))

    def test_database_commit_failure_restores_submission_files_and_keeps_unknown_receipt(self):
        database = self.conn
        class FailingCommit:
            armed = False
            def __getattr__(self, name):
                return getattr(database, name)
            def execute(self, statement, parameters=()):
                result = database.execute(statement, parameters)
                if 'insert into submission_files' in ' '.join(statement.lower().split()):
                    self.armed = True
                return result
            def commit(self):
                if self.armed:
                    raise sqlite3.OperationalError('Synthetic unavailable commit')
                database.commit()
        @contextmanager
        def failing_connection():
            try:
                yield FailingCommit()
            except BaseException:
                database.rollback()
                raise
        with patch.object(submissions, 'get_db_connection', failing_connection):
            result = self.call('submit', body={'answers_json': {'answer': 'will roll back'}, 'expected_submission_version': 'unsubmitted'},
                               files=[{'path': 'answer.txt'}])
        self.assertEqual('uncertain', result['status'])
        self.assertEqual(0, self.count('submissions'))
        self.assertEqual(0, self.count('submission_files'))
        self.assertEqual(1, self.count('agent_platform_requests'))
        self.assertTrue(result['host_execution_finished'])
        self.assertFalse(list((self.root / 'submissions').rglob('answer.txt')))
        self.assertTrue((self.workspace / 'answer.txt').exists())
