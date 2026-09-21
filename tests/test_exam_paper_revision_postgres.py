"""K9 Web CAS on the existing exclusively created offline PostgreSQL fixture.

Opt-in, cluster identity checks and fresh-database ownership are inherited from
AgentExamPostgresTests. No app DSN, HTTP, lifespan or production schema changes.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers.homework_parts import exam_papers as route
from classroom_app.services import exam_paper_management_service as domain
from tests.test_agent_exam_postgres import AgentExamPostgresTests as OfflineExamFixture


class Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class ExamPaperRevisionPostgresTests(OfflineExamFixture):
    def setUp(self):
        super().setUp()
        self.teacher = {'id': 7, 'role': 'teacher'}
        guard = patch.object(route, 'get_db_connection', self.connection)
        guard.start()
        self.addCleanup(guard.stop)

    def _stored_paper(self):
        return dict(self.conn.execute('SELECT * FROM exam_papers WHERE id=?', (self.paper_id,)).fetchone())

    def _web_paper(self):
        return asyncio.run(route.get_exam_paper(self.paper_id, user=self.teacher))['paper']

    def _web_save(self, title, revision):
        try:
            result = asyncio.run(route.update_exam_paper(self.paper_id,
                Request({'title': title, 'expected_revision': revision}), user=self.teacher))
            return 200, result
        except HTTPException as error:
            # The route's real connection context has rolled back and closed
            # before the exception is captured here.
            return error.status_code, error.detail

    def test_native_web_same_revision_commits_once_and_returns_one_conflict(self):
        revision = self._web_paper()['revision']
        self.assertEqual(domain.exam_paper_revision(self._stored_paper()), revision)
        self.conn.commit()
        entered = threading.Barrier(2)

        def concurrent_lock(conn, paper_id):
            entered.wait(timeout=5)
            domain.lock_exam_paper(conn, paper_id)

        # Synchronize before, never instead of, the actual SELECT FOR UPDATE.
        with patch.object(route, 'lock_exam_paper', concurrent_lock), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [(title, pool.submit(self._web_save, title, revision))
                       for title in ('Native writer A', 'Native writer B')]
            results = [(title, future.result(timeout=10)) for title, future in futures]
        successes = [(title, body) for title, (status, body) in results if status == 200]
        conflicts = [body for _, (status, body) in results if status == 409]
        self.assertEqual(1, len(successes), results)
        self.assertEqual(1, len(conflicts), results)
        self.assertEqual('revision_conflict', conflicts[0]['code'])
        winner_title, response = successes[0]
        stored = self._stored_paper()
        self.assertEqual(winner_title, stored['title'])
        self.assertNotEqual(revision, response['revision'])
        self.assertEqual(domain.exam_paper_revision(stored), response['revision'])
        self.assertEqual(response['revision'], self._web_paper()['revision'])

    def test_native_web_rechecks_side_edit_after_wait_without_timestamp_change(self):
        before = self._stored_paper()
        revision = self._web_paper()['revision']
        # A legacy tag write takes PostgreSQL's actual row lock implicitly.
        # It deliberately leaves updated_at untouched, so timestamp-only CAS
        # or a revision read before waiting would wrongly accept the PUT.
        self.conn.execute('UPDATE exam_papers SET tags_json=? WHERE id=?', ('["Native side edit"]', self.paper_id))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._web_save, 'Must not replace the paper', revision)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            status, detail = future.result(timeout=10)
        self.assertEqual(409, status)
        self.assertEqual('revision_conflict', detail['code'])
        expected = {**before, 'tags_json': '["Native side edit"]'}
        self.assertEqual(expected, self._stored_paper())
        self.assertEqual(before['updated_at'], expected['updated_at'])
        self.assertEqual(domain.exam_paper_revision(expected), self._web_paper()['revision'])

    def test_native_web_waiter_can_save_after_side_edit_rolls_back(self):
        before = self._stored_paper()
        revision = self._web_paper()['revision']
        self.conn.execute('UPDATE exam_papers SET tags_json=? WHERE id=?', ('["Rolled back edit"]', self.paper_id))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._web_save, 'Saved after rollback', revision)
            try:
                self._assert_native_waiting(future)
            finally:
                self.conn.rollback()
            status, response = future.result(timeout=10)
        self.assertEqual(200, status)
        stored = self._stored_paper()
        self.assertEqual('Saved after rollback', stored['title'])
        self.assertEqual(before['tags_json'], stored['tags_json'])
        self.assertNotEqual(revision, response['revision'])
        self.assertEqual(domain.exam_paper_revision(stored), response['revision'])
        self.assertEqual(response['revision'], self._web_paper()['revision'])


def load_tests(loader, tests, pattern):
    # Execute only the three K9 cases; inherited Agent cases keep their own gate.
    return unittest.TestSuite(ExamPaperRevisionPostgresTests(name)
        for name, value in ExamPaperRevisionPostgresTests.__dict__.items()
        if name.startswith('test_') and callable(value))
