"""Real two-connection disclosure/revocation ordering on a synthetic PG DB.

The Linux probe separately checks the exact filesystem publication functions
using the actual runner UID. These tests exercise the real shared DB authority
guard; they do not claim to be a combined DSH task integration test.
"""
from concurrent.futures import ThreadPoolExecutor
import unittest

from fastapi import HTTPException
from classroom_app.services.agent_delegation_service import (
    issue_task_delegation, revoke_persistent_authorization, verify_task_delegation,
)
from classroom_app.services.agent_platform_download_service import _lock_disclosure_authority
from tests.test_agent_authority_postgres import AgentAuthorityPostgresTests, NOW


class DownloadAuthorityPostgresTests(AgentAuthorityPostgresTests):
    def setUp(self):
        super().setUp()
        self.token = self.issued['token']

    def grant(self, conn):
        return verify_task_delegation(conn, self.token, purpose='tools', now=NOW)

    def check_after_wait(self, grant):
        with self.connection() as conn:
            try:
                _lock_disclosure_authority(conn, grant)
                self.grant(conn)
                return 200
            except HTTPException as exc:
                conn.rollback()
                return exc.status_code

    def persistent(self):
        authority = self._persistent(self.conn)
        self.token = issue_task_delegation(self.conn, task_id=10,
            attempt_id=self.attempt['id'], fencing_token=self.attempt['fencing_token'],
            purpose='tools', scopes=['platform:read'], persistent_authorization_id=authority['id'], now=NOW)['token']
        self.conn.commit()
        return authority

    def test_session_removed_before_disclosure_wait_denies_after_commit(self):
        grant = self.grant(self.conn)
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.check_after_wait, grant)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(401, future.result(timeout=5))

    def test_session_logout_waits_until_authorized_disclosure_releases_lock(self):
        grant = self.grant(self.conn)
        _lock_disclosure_authority(self.conn, grant)
        def logout():
            with self.connection() as conn:
                return conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'").rowcount
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(logout)
            try:
                self._assert_native_waiting(future)
                self.assertEqual(grant.actor.key, self.grant(self.conn).actor.key)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(1, future.result(timeout=5))
        with self.assertRaises(HTTPException) as caught:
            self.grant(self.conn)
        self.assertEqual(401, caught.exception.status_code)

    def test_persistent_revocation_before_disclosure_has_no_lock_order_cycle(self):
        authority = self.persistent()
        grant = self.grant(self.conn)
        revoke_persistent_authorization(self.conn, authorization_id=authority['id'], actor_role='teacher', actor_id=7, now=NOW)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.check_after_wait, grant)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertEqual(401, future.result(timeout=5))

    def test_persistent_revocation_waits_for_disclosure_then_blocks_the_next_read(self):
        authority = self.persistent()
        grant = self.grant(self.conn)
        _lock_disclosure_authority(self.conn, grant)
        def revoke():
            with self.connection() as conn:
                revoke_persistent_authorization(conn, authorization_id=authority['id'], actor_role='teacher', actor_id=7, now=NOW)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(revoke)
            try:
                self._assert_native_waiting(future)
                self.assertEqual(grant.actor.key, self.grant(self.conn).actor.key)
                self.conn.commit()
            finally:
                self.conn.rollback()
            future.result(timeout=5)
        with self.assertRaises(HTTPException) as caught:
            self.grant(self.conn)
        self.assertEqual(401, caught.exception.status_code)

    def test_task_cancellation_that_commits_first_denies_disclosure(self):
        grant = self.grant(self.conn)
        self.conn.execute("UPDATE agent_tasks SET cancel_requested_at='synthetic cancel' WHERE id=10")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.check_after_wait, grant)
            try:
                self._assert_native_waiting(future)
                self.conn.commit()
            finally:
                self.conn.rollback()
            self.assertIn(future.result(timeout=5), (401, 409))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DownloadAuthorityPostgresTests(name)
        for name, value in DownloadAuthorityPostgresTests.__dict__.items()
        if name.startswith('test_') and callable(value))
