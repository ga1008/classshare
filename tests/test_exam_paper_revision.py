"""K9 exam revision contracts. Memory/owned temporary SQLite only, no HTTP."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import copy
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from classroom_app.routers.homework_parts import exam_papers as route
from classroom_app.routers.ui_parts import exam_pages
from classroom_app.schemas.homework_contracts import ExamPaperDetailResponse
from classroom_app.services import exam_paper_management_service as domain
from classroom_app.services.exam_json_service import EXAM_JSON_TEMPLATE
from tests.test_agent_platform_writes import PlatformWriteFixture, Request


class ExamPaperRevisionTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        self.questions = copy.deepcopy(EXAM_JSON_TEMPLATE)
        self.questions.pop('title', None); self.questions.pop('description', None)
        self.payload = {'title': 'Original', 'description': 'Original description', 'questions': self.questions,
                        'config': {'allow_student_ai': False}, 'scope_level': 'private', 'status': 'draft'}
        self.paper_id = domain.create_exam_paper_record(self.conn, teacher_id=7, data=self.payload)['paper_id']
        self.conn.commit()
        guard = patch.object(route, 'get_db_connection', self.connection)
        guard.start(); self.addCleanup(guard.stop)

    def paper(self):
        return dict(self.conn.execute('SELECT * FROM exam_papers WHERE id=?', (self.paper_id,)).fetchone())

    def get(self):
        return asyncio.run(route.get_exam_paper(self.paper_id, user=self.teacher))

    def save(self, payload, user=None):
        return asyncio.run(route.update_exam_paper(self.paper_id, Request(payload), user=user or self.teacher))

    def assert_conflict(self, payload):
        before = self.paper()
        with self.assertRaises(HTTPException) as caught:
            self.save(payload)
        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual('revision_conflict', caught.exception.detail['code'])
        self.assertEqual(before, self.paper())
        self.assertFalse(self.conn.in_transaction)

    def test_get_ssr_and_response_model_share_unenriched_whole_row_revision(self):
        expected = domain.exam_paper_revision(self.paper())
        response = self.get()
        self.assertEqual(expected, response['paper']['revision'])
        self.assertEqual(expected, ExamPaperDetailResponse.model_validate(response).model_dump()['paper']['revision'])
        with patch.object(exam_pages, 'get_db_connection', self.connection), \
                patch.object(exam_pages.templates, 'TemplateResponse', side_effect=lambda request, template, context: context):
            context = asyncio.run(exam_pages.exam_editor_page(SimpleNamespace(), self.paper_id, user=self.teacher))
        self.assertEqual(expected, context['paper']['revision'])
        # GET response enrichment must not feed back into the stored-row hash.
        self.assertEqual(expected, self.get()['paper']['revision'])

    def test_matching_save_returns_new_revision_stale_save_rolls_back_and_next_revision_saves(self):
        initial = self.get()['paper']['revision']
        saved = self.save({**self.payload, 'title': 'First save', 'expected_revision': initial})
        self.assertNotEqual(initial, saved['revision'])
        self.assertEqual(self.get()['paper']['revision'], saved['revision'])
        self.assert_conflict({**self.payload, 'title': 'Stale overwrite', 'expected_revision': initial})
        next_save = self.save({**self.payload, 'title': 'Second save', 'expected_revision': saved['revision']})
        self.assertEqual('Second save', self.paper()['title'])
        self.assertEqual(next_save['revision'], self.get()['paper']['revision'])

    def test_omission_is_legacy_compatible_but_explicit_null_and_malformed_tokens_do_not_bypass(self):
        result = self.save({**self.payload, 'title': 'Legacy client'})
        self.assertEqual('Legacy client', self.paper()['title']); self.assertEqual(len(result['revision']), 64)
        before = self.paper()
        for value in (None, '', True, 0, [], {}, 'f' * 63, 'g' * 64):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                self.save({**self.payload, 'expected_revision': value})
            self.assertEqual(400, caught.exception.status_code)
            self.assertEqual(before, self.paper())

    def test_side_updates_invalidate_revision_even_when_updated_at_does_not_change(self):
        for column, value in [('title', 'Side title'), ('description', 'Side description'),
                              ('questions_json', '{"pages":[]}'), ('exam_config_json', '{"limit":1}'),
                              ('status', 'ready'), ('tags_json', '["new tag"]'), ('scope_level', 'department'),
                              ('school_code', 'other'), ('school_name', 'Other'), ('college', 'Other'), ('department', 'Other')]:
            with self.subTest(column=column):
                revision = self.get()['paper']['revision']; timestamp = self.paper()['updated_at']
                self.conn.execute(f'UPDATE exam_papers SET {column}=? WHERE id=?', (value, self.paper_id)); self.conn.commit()
                self.assertEqual(timestamp, self.paper()['updated_at'])
                self.assert_conflict({**self.payload, 'expected_revision': revision})

    def test_version_read_and_check_happen_after_lock_and_before_scoring_or_writes(self):
        revision = self.get()['paper']['revision']; events = []
        def lock(conn, paper_id):
            events.append('lock'); domain.lock_exam_paper(conn, paper_id)
            # Simulate a completed earlier writer becoming visible at lock acquisition.
            conn.execute('UPDATE exam_papers SET tags_json=? WHERE id=?', ('["locked state"]', paper_id))
        def read(conn, paper_id, teacher_id, **options):
            self.assertTrue(conn.in_transaction); events.append('read')
            return domain._get_exam_paper_for_teacher(conn, paper_id, teacher_id, **options)
        with patch.object(route, 'lock_exam_paper', lock), patch.object(route, '_get_exam_paper_for_teacher', read), \
                patch.object(route, 'normalize_exam_scoring_payload') as normalize:
            self.assert_conflict({**self.payload, 'expected_revision': revision})
            normalize.assert_not_called()
        self.assertEqual(['lock', 'read'], events)

    def test_permission_and_existing_answer_guards_remain_in_force(self):
        revision = self.get()['paper']['revision']
        with self.assertRaises(HTTPException) as caught:
            self.save({**self.payload, 'expected_revision': revision}, {'role': 'teacher', 'id': 8})
        self.assertEqual(403, caught.exception.status_code)
        for guard_name in ('_count_exam_submissions', '_count_exam_drafts'):
            before = self.paper()
            with patch.object(route, guard_name, return_value=1), self.assertRaises(HTTPException) as blocked:
                self.save({**self.payload, 'description': 'Changed scoring input', 'expected_revision': revision})
            self.assertEqual(409, blocked.exception.status_code)
            self.assertIsInstance(blocked.exception.detail, str)
            self.assertIn('已有学生提交或草稿', blocked.exception.detail)
            self.assertEqual(before, self.paper())

    def test_existing_answers_still_allow_unchanged_scoring_inputs_and_metadata_save(self):
        revision = self.get()['paper']['revision']
        with patch.object(route, '_count_exam_submissions', return_value=1) as submissions, \
                patch.object(route, '_count_exam_drafts', return_value=1) as drafts:
            result = self.save({**self.payload, 'title': 'Metadata only', 'config': {'allow_student_ai': True}, 'expected_revision': revision})
        submissions.assert_not_called(); drafts.assert_not_called()
        self.assertEqual('Metadata only', self.paper()['title'])
        self.assertEqual(result['revision'], self.get()['paper']['revision'])

    def test_two_connections_with_same_revision_commit_once_and_conflict_once(self):
        revision = self.get()['paper']['revision']
        with tempfile.TemporaryDirectory(prefix='lanshare-exam-revision-') as folder:
            database = Path(folder) / 'synthetic.db'
            with closing(sqlite3.connect(database)) as destination:
                self.conn.backup(destination)
            barrier = threading.Barrier(2)
            @contextmanager
            def connection():
                conn = sqlite3.connect(database, timeout=10); conn.row_factory = sqlite3.Row
                try:
                    with conn:
                        yield conn
                finally:
                    conn.close()
            def lock(conn, paper_id):
                barrier.wait(timeout=10); domain.lock_exam_paper(conn, paper_id)
            def writer(title):
                try:
                    return self.save({**self.payload, 'title': title, 'expected_revision': revision})
                except HTTPException as error:
                    return {'status_code': error.status_code, 'detail': error.detail}
            with patch.object(route, 'get_db_connection', connection), patch.object(route, 'lock_exam_paper', lock), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(writer, title) for title in ('Writer A', 'Writer B')]
                results = [future.result(timeout=20) for future in futures]
            successes = [value for value in results if value.get('status') == 'success']
            conflicts = [value for value in results if value.get('status_code') == 409]
            self.assertEqual(1, len(successes)); self.assertEqual(1, len(conflicts))
            self.assertEqual('revision_conflict', conflicts[0]['detail']['code'])
            with closing(sqlite3.connect(database)) as final:
                final.row_factory = sqlite3.Row
                row = final.execute('SELECT * FROM exam_papers WHERE id=?', (self.paper_id,)).fetchone()
                self.assertEqual(successes[0]['revision'], domain.exam_paper_revision(row))
                self.assertIn(row['title'], ('Writer A', 'Writer B'))
