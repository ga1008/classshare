"""Fault injection on the in-memory auth app, with no lifespan or real DB."""
from datetime import datetime, timedelta, timezone
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from classroom_app import dependencies
from classroom_app.app import database_busy_exception_handler
from classroom_app.db import sessions
from classroom_app.db.errors import DatabaseBusyError, DatabaseConnectionError
from classroom_app.services import remember_login_service as remember
from classroom_app.services.remember_login_middleware import remember_login_middleware
from tests import test_remember_login as harness


class AuthTransientFailureTests(harness.RememberLoginTests):
    def setUp(self):
        super().setUp()
        self.app.add_exception_handler(DatabaseBusyError, database_busy_exception_handler)
        self.app.add_exception_handler(DatabaseConnectionError, database_busy_exception_handler)

        @self.app.get("/api/auth-fixture/resource")
        def forbidden(user=Depends(dependencies.get_current_user)):
            raise HTTPException(403, "Synthetic private resource")

        @self.app.get("/api/auth-fixture/domain-credential")
        def domain_credential(user=Depends(dependencies.get_current_user)):
            raise HTTPException(401, "Synthetic integration credential expired")

    def snapshot(self):
        return {
            "sessions": [dict(row) for row in self.conn.execute("SELECT * FROM user_sessions")],
            "devices": self.rows(),
        }

    def assert_unavailable(self, response):
        self.assertEqual(503, response.status_code)
        self.assertEqual("3", response.headers["retry-after"])
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("location", response.headers)
        if response.headers["content-type"].startswith("application/json"):
            self.assertEqual("service_unavailable", response.json()["error"]["code"])
            self.assertNotIn("redirect_to", response.json())

    def test_identity_checkout_failure_preserves_session_and_every_device(self):
        for role in ("student", "teacher"):
            for failure in (DatabaseBusyError, DatabaseConnectionError):
                with self.subTest(role=role, failure=failure.__name__):
                    self.client.cookies.clear()
                    if role == "student":
                        self.login_student()
                    else:
                        self.assertEqual(303, self.client.post("/teacher/login", data={
                            "email": "teacher@example.test", "password": self.password,
                            "remember_me": "1", "next": "/protected",
                        }).status_code)
                    before = self.snapshot()
                    cookie = self.client.cookies.get("access_token")
                    dependencies._drop_identity_cache_for_user("7", role)
                    with patch.object(dependencies, "get_db_connection", side_effect=failure("Synthetic timeout")):
                        response = self.client.get("/api/protected")
                    self.assert_unavailable(response)
                    self.assertEqual(before, self.snapshot())
                    self.assertEqual(cookie, self.client.cookies.get("access_token"))
                    self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_html_identity_timeout_keeps_cookie_and_allows_recovery(self):
        self.login_student()
        before = self.snapshot()
        dependencies._drop_identity_cache_for_user("7", "student")
        with patch.object(dependencies, "get_db_connection", side_effect=DatabaseBusyError("Synthetic timeout")):
            response = self.client.get("/protected", headers={"accept": "text/html"})
        self.assert_unavailable(response)
        self.assertEqual("error.html", response.template.name)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(200, self.client.get("/protected").status_code)

    def test_unexpected_identity_error_does_not_invalidate_account(self):
        self.login_student()
        before = self.snapshot()
        dependencies._drop_identity_cache_for_user("7", "student")
        with patch.object(dependencies, "get_db_connection", side_effect=RuntimeError("Synthetic code defect")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic code defect"):
                self.client.get("/api/protected")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_disabled_or_removed_identity_still_revokes_session_and_devices(self):
        for sql, role in (("UPDATE students SET enrollment_status = 'suspended'", "student"),
                          ("DELETE FROM students", "student"),
                          ("UPDATE teachers SET is_active = 0", "teacher")):
            with self.subTest(sql=sql):
                self.conn.execute("UPDATE students SET enrollment_status = 'active'")
                self.conn.execute("UPDATE teachers SET is_active = 1")
                if not self.conn.execute("SELECT 1 FROM students").fetchone():
                    self.conn.execute("INSERT INTO students VALUES(7,'Synthetic student','S7',30,?,0,'active')", (self.hashed_password,))
                self.conn.commit()
                self.client.cookies.clear()
                if role == "student":
                    self.login_student()
                else:
                    self.assertEqual(303, self.client.post("/teacher/login", data={
                        "email": "teacher@example.test", "password": self.password, "remember_me": "1",
                    }).status_code)
                self.conn.execute(sql)
                self.conn.commit()
                dependencies._drop_identity_cache_for_user("7", role)
                response = self.client.get("/api/protected")
                self.assertEqual(401, response.status_code)
                self.assertEqual("login_required", response.json()["error"]["code"])
                self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE role = ?", (role,)).fetchone()[0])
                self.assertTrue(all(row["revoked_at"] for row in self.rows() if row["role"] == role))

    def test_resource_403_and_domain_401_keep_current_page_session(self):
        self.login_student()
        before = self.snapshot()
        for path, status in (("/api/auth-fixture/resource", 403), ("/api/auth-fixture/domain-credential", 401)):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(status, response.status_code)
                self.assertNotIn("redirect_to", response.json())
                self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_late_401_cannot_clear_a_newer_browser_login(self):
        self.login_student()
        old = self.client.cookies.get("access_token")
        self.login_student()
        current = self.client.cookies.get("access_token")
        before = self.snapshot()
        response = self.client.get("/api/protected", headers={"cookie": f"access_token={old}"})
        self.assertEqual(401, response.status_code)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(current, self.client.cookies.get("access_token"))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_remember_checkout_timeout_keeps_credentials_for_next_request(self):
        self.login_student()
        old = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(old)
        before = self.snapshot()
        with patch.object(remember, "get_db_connection", side_effect=DatabaseBusyError("Synthetic pool timeout")):
            response = self.client.get("/api/protected")
        self.assert_unavailable(response)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(old, self.client.cookies.get(remember.REMEMBER_COOKIE))
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_live_session_check_timeout_in_outer_middleware_is_retryable(self):
        self.login_student()
        before = self.snapshot()
        dependencies.active_sessions.clear()
        with patch.object(dependencies, "get_user_session", side_effect=DatabaseConnectionError("Synthetic disconnect")):
            response = self.client.get("/api/protected")
        self.assert_unavailable(response)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_refresh_write_failure_rolls_back_session_and_validator_together(self):
        self.login_student()
        old = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(old)
        before = self.snapshot()

        class FailRotation(harness._SharedConnection):
            def __enter__(self):
                return self

            def execute(self, sql, *args):
                if "SET prev_validator_hash = validator_hash" in sql:
                    raise DatabaseBusyError("Synthetic statement timeout after session write")
                return self._conn.execute(sql, *args)

        with patch.object(remember, "get_db_connection", lambda: FailRotation(self.conn)):
            response = self.client.get("/api/protected")
        self.assert_unavailable(response)
        self.assertEqual(before, self.snapshot())
        self.assertEqual({}, dependencies.active_sessions)
        self.assertEqual(old, self.client.cookies.get(remember.REMEMBER_COOKIE))
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_rotation_grace_without_local_result_preserves_rotated_session(self):
        self.login_student()
        old = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(old)
        response = self.client.get("/api/protected")
        current_access = response.cookies.get("access_token")
        current_remember = response.cookies.get(remember.REMEMBER_COOKIE)
        before = self.snapshot()
        remember._RECENT_REFRESHES.clear()
        self.use_only_remember_cookie(old)
        self.assert_unavailable(self.client.get("/api/protected"))
        self.assertEqual(before, self.snapshot())
        self.client.cookies.set("access_token", current_access)
        self.client.cookies.set(remember.REMEMBER_COOKIE, current_remember)
        self.assertEqual(200, self.client.get("/api/protected").status_code)

    def test_concurrently_revoked_rotation_does_not_commit_a_new_session(self):
        self.login_student()
        old = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(old)
        before = self.snapshot()

        class RevokedBeforeUpdate(harness._SharedConnection):
            def __enter__(self):
                return self

            def execute(self, sql, *args):
                if "SET prev_validator_hash = validator_hash" in sql:
                    return SimpleNamespace(rowcount=0)
                return self._conn.execute(sql, *args)

        with patch.object(remember, "get_db_connection", lambda: RevokedBeforeUpdate(self.conn)):
            self.assert_unavailable(self.client.get("/api/protected"))
        self.assertEqual(before, self.snapshot())
        self.assertEqual({}, dependencies.active_sessions)

    def test_late_remember_response_cannot_overwrite_a_newer_password_login(self):
        for status in (200, 401):
            with self.subTest(status=status):
                self.login_student()
                old = self.client.cookies.get(remember.REMEMBER_COOKIE)
                self.expire_session()
                request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/api/protected",
                                   "query_string": b"", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
                                   "headers": [(b"cookie", f"ls_remember={old}".encode())]})
                newer = []

                async def new_login_finishes_before_this_response(_request):
                    newer.append(dependencies.create_access_token(
                        {"id": 7, "role": "student", "name": "Synthetic"}, "127.0.0.1"))
                    return JSONResponse({"status": "synthetic"}, status_code=status)

                response = asyncio.run(remember_login_middleware(request, new_login_finishes_before_this_response))
                self.assertNotIn("set-cookie", response.headers)
                self.assertIsNotNone(dependencies.verify_token(newer[0], "127.0.0.1"))

    def test_delayed_login_cannot_remember_a_session_revoked_by_credentials_change(self):
        self.login_student()
        token = self.client.cookies.get("access_token")
        dependencies.invalidate_session_for_user("7", "student")
        before = self.snapshot()
        request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/student/login",
                           "query_string": b"", "server": ("testserver", 80), "headers": []})
        response = JSONResponse({"status": "synthetic"})
        remember.update_remember_after_login(request, response, token, True)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(before, self.snapshot())

    def test_mp_bearer_database_failure_is_unavailable_without_session_loss(self):
        self.login_student()
        before = self.snapshot()
        self.client.cookies.clear()
        with patch.object(dependencies, "get_db_connection", side_effect=DatabaseBusyError("Synthetic bearer timeout")):
            response = self.client.get("/api/protected", headers={"authorization": "Bearer synthetic"})
        self.assert_unavailable(response)
        self.assertEqual(before, self.snapshot())

    def test_expired_session_cleanup_cannot_delete_its_concurrent_replacement(self):
        self.login_student()
        self.conn.execute("UPDATE user_sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
        self.conn.commit()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        class ReplaceAfterRead(harness._SharedConnection):
            def __enter__(self):
                return self

            def execute(self, sql, *args):
                if sql.startswith("DELETE FROM user_sessions"):
                    self._conn.execute("UPDATE user_sessions SET session_id = 'replacement', expires_at = ?", (future,))
                return self._conn.execute(sql, *args)

        with patch.object(sessions, "get_db_connection", lambda: ReplaceAfterRead(self.conn)):
            self.assertIsNone(sessions.get_user_session("student:7"))
        row = self.conn.execute("SELECT session_id, expires_at FROM user_sessions").fetchone()
        self.assertEqual(("replacement", future), tuple(row))


def load_tests(loader, standard_tests, pattern):
    names = [name for name in loader.getTestCaseNames(AuthTransientFailureTests) if name in AuthTransientFailureTests.__dict__]
    return unittest.TestSuite(AuthTransientFailureTests(name) for name in names)
