"""Revocation/cache publication gates over the isolated in-memory auth fixture."""
from unittest.mock import patch
import asyncio
import unittest
from starlette.requests import Request
from starlette.responses import JSONResponse

from classroom_app import dependencies
from classroom_app.app import database_busy_exception_handler
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.routers.ui_parts import auth
from classroom_app.services import account_credentials_service as credentials
from classroom_app.services import remember_login_service as remember
from classroom_app.services.remember_login_middleware import remember_login_middleware
from tests.test_remember_login import RememberLoginTests


class AuthCacheFenceTests(RememberLoginTests):
    def token(self):
        return self.client.cookies.get("access_token")

    def test_remember_commit_then_revocation_cannot_publish_or_set_late_cookies(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        publish = dependencies._publish_committed_access_token

        def revoke_then_publish(token, generation):
            dependencies.invalidate_session_for_user("7", "student")
            return publish(token, generation)

        with patch.object(dependencies, "_publish_committed_access_token", side_effect=revoke_then_publish):
            outcome = remember.refresh_from_remember_cookie(cookie, "127.0.0.1", "synthetic gate")
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 0)
        self.assertIsNone(dependencies.verify_token(outcome.access_token))
        self.assertEqual(dependencies.active_sessions, {})
        self.use_only_remember_cookie(cookie)
        response = self.client.get("/api/protected")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", response.headers)

    def test_cold_read_which_finishes_after_revocation_cannot_restore_old_session(self):
        self.login_student()
        token = self.token()
        dependencies.active_sessions.clear()
        read = dependencies.get_user_session

        def read_then_revoke(key):
            snapshot = read(key)
            dependencies.invalidate_session_for_user("7", "student")
            return snapshot

        with patch.object(dependencies, "get_user_session", side_effect=read_then_revoke):
            self.assertIsNone(dependencies.verify_token(token))
        self.assertEqual(dependencies.active_sessions, {})
        self.assertIsNone(dependencies.verify_token(token))

    def test_caller_owned_uncommitted_revocation_keeps_cold_reads_out_of_cache(self):
        self.login_student()
        token = self.token()
        old = dict(self.conn.execute("SELECT * FROM user_sessions").fetchone())
        dependencies.invalidate_session_for_user("7", "student", conn=self.conn, revoke_remembered=False)
        # Another READ COMMITTED connection may still see the previous row
        # while this caller's DELETE has not committed. It must not warm cache.
        with patch.object(dependencies, "get_user_session", return_value=old):
            self.assertIsNotNone(dependencies.verify_token(token))
        self.assertEqual(dependencies.active_sessions, {})
        self.conn.commit()
        self.assertIsNone(dependencies.verify_token(token))

    def test_rolled_back_revocation_preserves_session_and_new_login_restores_hot_reads(self):
        self.login_student()
        token = self.token()
        dependencies.invalidate_session_for_user("7", "student", conn=self.conn, revoke_remembered=False)
        self.conn.rollback()
        self.assertIsNotNone(dependencies.verify_token(token))
        self.assertEqual(dependencies.active_sessions, {})
        self.login_student()
        with patch.object(dependencies, "get_user_session", side_effect=AssertionError("fresh committed login must be warm")):
            self.assertIsNotNone(dependencies.verify_token(self.token()))

    def test_ordinary_token_issuance_cannot_publish_after_later_revocation(self):
        self.login_student()
        cache = dependencies._cache_session_snapshot
        fired = False

        def revoke_then_cache(key, snapshot, **options):
            nonlocal fired
            if options.get("committed") and not fired:
                fired = True
                dependencies.invalidate_session_for_user("7", "student")
            return cache(key, snapshot, **options)

        with patch.object(dependencies, "_cache_session_snapshot", side_effect=revoke_then_cache):
            token = dependencies.create_access_token({"id": 7, "role": "student", "name": "synthetic"}, "127.0.0.1")
        self.assertTrue(fired)
        self.assertIsNone(dependencies.verify_token(token))
        self.assertEqual(dependencies.active_sessions, {})

    def test_reverse_login_publication_keeps_only_latest_committed_session(self):
        tokens, generations = [], []
        for _ in range(2):
            generations.append(dependencies._begin_session_cache_write("7", "student"))
            tokens.append(dependencies.create_access_token({"id": 7, "role": "student"}, "127.0.0.1", conn=self.conn,
                                                          cache_generation=generations[-1]))
            self.conn.commit()
        self.assertTrue(dependencies._publish_committed_access_token(tokens[1], generations[1]))
        self.assertFalse(dependencies._publish_committed_access_token(tokens[0], generations[0]))
        self.assertIsNone(dependencies.verify_token(tokens[0]))
        self.assertIsNotNone(dependencies.verify_token(tokens[1]))

    def test_admin_session_list_cannot_repopulate_revoked_authorization_cache(self):
        self.login_student()
        token = self.token()
        old = dependencies.list_active_sessions()
        dependencies.invalidate_session_for_user("7", "student")
        with patch.object(dependencies, "list_user_sessions", return_value=old):
            self.assertEqual(dependencies.list_active_sessions(), old)
        self.assertEqual(dependencies.active_sessions, {})
        self.assertIsNone(dependencies.verify_token(token))

    def test_remember_rereads_credential_after_actor_lock_wait(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        lock = credentials.lock_actor_authorization_transition

        def revoked_while_waiting(conn, **owner):
            lock(conn, **owner)
            remember.revoke_remember_tokens(conn, "7", "student", "synthetic_lock_gate")
            conn.commit()

        with patch.object(credentials, "lock_actor_authorization_transition", side_effect=revoked_while_waiting):
            outcome = remember.refresh_from_remember_cookie(cookie, "127.0.0.1", "synthetic gate")
        self.assertEqual(outcome.kind, "clear")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 0)

    def test_failed_account_revocation_propagates_and_rolls_back_remember_and_session(self):
        self.login_student()
        token = self.token()
        before = self.rows()
        with patch.object(dependencies, "delete_user_sessions", side_effect=DatabaseBusyError("synthetic delete failure")):
            with self.assertRaises(DatabaseBusyError):
                dependencies.invalidate_session_for_user("7", "student")
        self.assertEqual(self.rows(), before)
        self.assertIsNotNone(dependencies.verify_token(token))
        with patch.object(remember, "revoke_remember_tokens", side_effect=DatabaseBusyError("synthetic revoke failure")):
            with self.assertRaises(DatabaseBusyError):
                dependencies.invalidate_session_for_user("7", "student")
        self.assertEqual(self.rows(), before)
        self.assertIsNotNone(dependencies.verify_token(token))

    def test_logout_is_atomic_and_failed_revoke_cannot_claim_success_or_clear_cookies(self):
        self.app.add_exception_handler(DatabaseBusyError, database_busy_exception_handler)
        self.login_student()
        token, cookie = self.token(), self.client.cookies.get(remember.REMEMBER_COOKIE)
        before = self.rows()
        with patch.object(auth, "_revoke_remember_selector", side_effect=DatabaseBusyError("synthetic logout failure")):
            response = self.client.get("/logout")
        self.assertEqual(response.status_code, 503)
        self.assertIn("退出尚未完成", response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("location", response.headers)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.token(), token)
        self.assertEqual(self.client.cookies.get(remember.REMEMBER_COOKIE), cookie)
        self.assertIsNotNone(dependencies.verify_token(token))

        forget = auth.forget_this_device
        def assert_open_transaction(request, *, conn=None):
            self.assertIsNotNone(conn)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 0)
            forget(request, conn=conn)
        with patch.object(auth, "forget_this_device", side_effect=assert_open_transaction):
            response = self.client.get("/logout")
        self.assertEqual(response.status_code, 303)
        self.assertIn("access_token=", " ".join(response.headers.get_list("set-cookie")))
        self.assertIn("ls_remember=", " ".join(response.headers.get_list("set-cookie")))
        self.assertIsNone(dependencies.verify_token(token))
        self.assertEqual(self.rows()[0]["revoke_reason"], "logout")

    def test_later_password_session_wins_while_remember_waits_for_actor_lock(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        lock = credentials.lock_actor_authorization_transition
        newer = []

        def new_login_before_lock(conn, **owner):
            if not newer:
                newer.append(None)
                newer[0] = dependencies.create_access_token({"id": 7, "role": "student"}, "127.0.0.1")
            lock(conn, **owner)

        with patch.object(credentials, "lock_actor_authorization_transition", side_effect=new_login_before_lock):
            outcome = remember.refresh_from_remember_cookie(cookie, "127.0.0.1", "synthetic gate")
        self.assertEqual(outcome.kind, remember.NOTICE_CONFLICT)
        self.assertIsNotNone(dependencies.verify_token(newer[0]))

    def test_expired_access_logout_removes_inflight_refresh_and_never_sets_late_cookie(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/api/protected",
                           "query_string": b"", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
                           "headers": [(b"cookie", f"ls_remember={cookie}".encode())]})
        rotated = []

        async def logout_before_refresh_response(_request):
            rotated.append(self.conn.execute("SELECT session_id FROM user_sessions").fetchone()[0])
            self.assertEqual(self.client.get("/logout").status_code, 303)
            return JSONResponse({"synthetic": True})

        response = asyncio.run(remember_login_middleware(request, logout_before_refresh_response))
        self.assertEqual(len(rotated), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 0)
        self.assertEqual(self.rows()[0]["revoke_reason"], "logout")
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(dependencies.active_sessions, {})

    def test_remember_only_logout_preserves_another_devices_newer_session(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        other = dependencies.create_access_token({"id": 7, "role": "student"}, "127.0.0.1")
        session_id = dependencies.decode_token_payload(other)["session_id"]
        remember.issue_remember_token(self.conn, user_id=7, role="student", session_id=session_id)
        self.conn.commit()
        # The first browser retains its now-unusable access and its own device.
        self.assertEqual(self.client.get("/logout").status_code, 303)
        with patch.object(dependencies, "get_user_session", side_effect=AssertionError("other device cache must remain warm")):
            self.assertIsNotNone(dependencies.verify_token(other))
        self.assertEqual(self.rows()[0]["revoke_reason"], "logout")
        self.assertEqual(self.rows()[1]["revoked_at"], "")
        self.assertNotEqual(remember.parse_remember_cookie(cookie)[0], self.rows()[1]["selector"])

    def test_invalid_remember_validator_cannot_log_out_its_claimed_owner(self):
        self.login_student()
        token = self.token()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        before = self.rows()
        selector = remember.parse_remember_cookie(cookie)[0]
        self.client.cookies.clear()
        self.client.cookies.set(remember.REMEMBER_COOKIE, selector + ".invalid-validator")
        self.assertEqual(self.client.get("/logout").status_code, 303)
        self.assertEqual(self.rows(), before)
        self.assertIsNotNone(dependencies.verify_token(token))


def load_tests(loader, standard_tests, pattern):
    return unittest.TestSuite(AuthCacheFenceTests(name) for name in loader.getTestCaseNames(AuthCacheFenceTests)
                              if name in AuthCacheFenceTests.__dict__)
