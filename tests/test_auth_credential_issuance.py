"""Deterministic credential/revocation interleavings on owned SQLite only.

ASGI transports never open a socket. The inherited fixture rejects every
unpatched database connector, including PostgreSQL, before any request runs.
"""
import asyncio
from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import JSONResponse

from classroom_app import dependencies
from classroom_app.app import database_busy_exception_handler
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.db import schema_wechat_mp
from classroom_app.routers import profile
from classroom_app.routers.ui_parts import auth, common
from classroom_app.services import account_credentials_service as credentials
from classroom_app.services import remember_login_service as remember
from classroom_app.services.remember_login_middleware import remember_login_middleware
from classroom_app.services.student_auth_service import build_password_setup_token
from classroom_app.services import wechat_mp_service as mp
from tests.test_auth_native_flows import NativeAuthFlowTests


class CredentialIssuanceBoundaryTests(NativeAuthFlowTests):
    new_password = "Changed-synthetic-password-2026!"

    def setUp(self):
        super().setUp()
        self.conn.execute("ALTER TABLE teachers ADD COLUMN password_updated_at TEXT")
        self.conn.commit()
        self.app.include_router(profile.router)
        self.app.add_exception_handler(DatabaseBusyError, database_busy_exception_handler)
        self.checkouts = 0
        self.db_on_event_loop = []
        self.fixture_lock = threading.RLock()
        for module in (auth, common, profile, dependencies, remember):
            self.stack.enter_context(patch.object(module, "get_db_connection", self.tracked_connection))
        self.stack.enter_context(patch.dict(remember._RECENT_REFRESHES, {}, clear=True))

    @contextmanager
    def tracked_connection(self):
        # Distinct production connections cannot share SQLite transaction state.
        # This single fixture connection serializes whole DB units, never hashes.
        with self.fixture_lock:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self.db_on_event_loop.append(threading.current_thread().name)
            self.checkouts += 1
            try:
                with self.connection() as conn:
                    yield conn
            finally:
                self.checkouts -= 1

    def sign_in(self, role="student", *, remembered=True):
        self.client.cookies.clear()
        response = (self.post_student(remember_me="1" if remembered else "0") if role == "student" else
                    self.client.post("/teacher/login", data={"email": "teacher@example.test",
                        "password": self.password, "remember_me": "1" if remembered else "0"}))
        self.assertEqual(303, response.status_code, response.text)
        return dependencies.decode_token_payload(self.client.cookies.get("access_token"))

    def submit_change(self, path="/api/student/password/change"):
        payload = {"current_password": self.password, "new_password": self.new_password,
                   "confirm_password": self.new_password}
        return (self.client.put(path, json=payload) if path == "/api/profile/password" else
                self.client.post(path, data=payload))

    def admin_revoke(self, role="student", *, reset=False, disable=False):
        # Real domain transaction, not a stubbed auth/cache answer. The gate
        # chooses its position relative to the request's CPU/commit boundaries.
        credentials.prepare_credentials_change(self.conn, role=role, user_id=7)
        credentials.credentials_changed(self.conn, role=role, user_id=7, invalidate_sessions=True)
        if reset:
            self.conn.execute("UPDATE students SET password_reset_required=1 WHERE id=7")
        if disable:
            self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=7")
        self.conn.commit()

    def active_devices(self, role="student"):
        return list(self.conn.execute("SELECT * FROM remember_login_tokens WHERE user_id='7' AND role=? AND revoked_at=''", (role,)))

    def mp_token(self, role):
        self.stack.enter_context(patch.object(schema_wechat_mp, "_SCHEMA_READY", False))
        if "school_code" not in {row[1] for row in self.conn.execute("PRAGMA table_info(students)")}:
            self.conn.execute("ALTER TABLE students ADD COLUMN school_code TEXT DEFAULT ''")
            self.conn.execute("ALTER TABLE students ADD COLUMN department TEXT DEFAULT ''")
        token = mp.issue_mp_session(self.conn, user_role=role, user_pk=7)
        self.conn.commit()
        return token

    def test_setup_session_insert_failure_rolls_back_password_audit_and_authority(self):
        self.unset_password()
        token = self.identity_form().context["setup_token"]
        save = dependencies.save_user_session

        def fail_after_session_insert(**kwargs):
            self.assertIs(kwargs["conn"], self.conn)
            self.assertTrue(self.conn.in_transaction)
            self.assertTrue(dependencies.verify_password(self.password,
                self.conn.execute("SELECT hashed_password FROM students WHERE id=7").fetchone()[0]))
            self.assertEqual(1, self.count("student_login_audit_logs"))
            save(**kwargs)
            raise DatabaseBusyError("Synthetic failure after setup session INSERT")

        with patch.object(dependencies, "save_user_session", side_effect=fail_after_session_insert):
            response = self.setup_form(token)
        self.assertEqual(503, response.status_code)
        self.assertNotIn("set-cookie", response.headers)
        self.assertIsNone(self.conn.execute("SELECT hashed_password FROM students WHERE id=7").fetchone()[0])
        self.assert_unmodified_credentials()

    def test_admin_reset_after_setup_commit_cannot_be_undone_by_response_or_cache(self):
        publish = dependencies._publish_committed_access_token
        for path in ("/student/password/setup", "/api/student/password/setup"):
            with self.subTest(path=path):
                self.client.cookies.clear()
                self.conn.execute("UPDATE students SET hashed_password=NULL,password_reset_required=0")
                self.conn.commit()
                token = self.identity_form().context["setup_token"]
                observed = []

                def revoke_after_committed_session(access_token, generation):
                    self.assertFalse(self.conn.in_transaction)
                    issued = dependencies.decode_token_payload(access_token)
                    self.assertEqual(issued["session_id"], self.conn.execute("SELECT session_id FROM user_sessions").fetchone()[0])
                    self.admin_revoke(reset=True)
                    observed.append(access_token)
                    return publish(access_token, generation)

                with patch.object(dependencies, "_publish_committed_access_token", side_effect=revoke_after_committed_session):
                    response = self.client.post(path, data={"setup_token": token, "password": self.password,
                                                           "confirm_password": self.password})
                self.assertEqual(303 if path.startswith("/student/") else 200, response.status_code)
                self.assertEqual(1, len(observed))
                # A response may arrive late, but its already-revoked cookie
                # cannot re-create server-side authorization.
                self.assertIsNone(dependencies.verify_token(response.cookies.get("access_token")))
                self.assertEqual(0, self.count("user_sessions"))
                self.assertEqual(1, self.conn.execute("SELECT password_reset_required FROM students").fetchone()[0])
                self.assertEqual(400, self.client.post("/api/student/password/setup", data={
                    "setup_token": token, "password": self.new_password, "confirm_password": self.new_password}).status_code)
                self.assertEqual(0, self.count("user_sessions"))

    def test_reset_token_for_older_approval_cannot_complete_a_newer_reset(self):
        self.conn.execute("UPDATE students SET password_reset_required=1")
        self.conn.execute("INSERT INTO student_password_reset_requests(id,student_id,status,reviewed_at) VALUES(1,7,'approved','2026-11-04')")
        old = build_password_setup_token(7, "/dashboard", "password_reset", reset_request_id=1)
        self.conn.execute("INSERT INTO student_password_reset_requests(id,student_id,status,reviewed_at) VALUES(2,7,'approved','2026-11-05')")
        self.conn.commit()
        response = self.setup_form(old)
        self.assert_native(response, "identity", 400)
        self.assertEqual(["approved", "approved"], [row[0] for row in self.conn.execute("SELECT status FROM student_password_reset_requests ORDER BY id")])
        self.assertEqual(self.hashed_password, self.conn.execute("SELECT hashed_password FROM students").fetchone()[0])
        self.assert_unmodified_credentials()
        current = build_password_setup_token(7, "/dashboard", "password_reset", reset_request_id=2)
        self.assertEqual(303, self.setup_form(current).status_code)
        self.assertEqual(["approved", "completed"], [row[0] for row in self.conn.execute("SELECT status FROM student_password_reset_requests ORDER BY id")])

    def test_setup_and_self_change_hashes_use_bounded_pool_without_database_checkout(self):
        hash_password = dependencies.get_password_hash
        verify = dependencies.verify_password
        observed = []

        def hash_without_connection(password):
            observed.append(("hash", self.checkouts, threading.current_thread().name))
            return hash_password(password)

        def verify_without_connection(password, encoded):
            observed.append(("verify", self.checkouts, threading.current_thread().name))
            return verify(password, encoded)

        self.unset_password()
        token = self.identity_form().context["setup_token"]
        with patch.object(dependencies, "get_password_hash", side_effect=hash_without_connection):
            self.assertEqual(303, self.setup_form(token).status_code)
        for role, path in (("student", "/api/student/password/change"), ("student", "/api/profile/password"),
                           ("teacher", "/api/profile/password")):
            with self.subTest(role=role, path=path):
                table = "students" if role == "student" else "teachers"
                self.conn.execute(f"UPDATE {table} SET hashed_password=?", (self.hashed_password,))
                self.conn.commit()
                self.sign_in(role)
                with patch.object(dependencies, "get_password_hash", side_effect=hash_without_connection), \
                     patch.object(dependencies, "verify_password", side_effect=verify_without_connection):
                    response = self.submit_change(path)
                self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(["hash", "verify", "hash", "verify", "hash", "verify", "hash"], [item[0] for item in observed])
        self.assertTrue(all(count == 0 and name.startswith("password-verify") for _, count, name in observed), observed)
        self.assertEqual([], self.db_on_event_loop)

    def test_each_self_change_keeps_only_current_device_and_original_session(self):
        for role, path in (("student", "/api/student/password/change"), ("student", "/api/profile/password"),
                           ("teacher", "/api/profile/password")):
            with self.subTest(role=role, path=path):
                table = "students" if role == "student" else "teachers"
                self.conn.execute(f"UPDATE {table} SET hashed_password=?", (self.hashed_password,))
                self.conn.commit()
                user = self.sign_in(role)
                before = self.client.cookies.get(remember.REMEMBER_COOKIE)
                previous = self.conn.execute("SELECT absolute_expires_at FROM remember_login_tokens WHERE selector=?",
                    (remember.parse_remember_cookie(before)[0],)).fetchone()
                remember.issue_remember_token(self.conn, user_id=7, role=role, session_id="other-device")
                self.conn.commit()
                response = self.submit_change(path)
                self.assertEqual(200, response.status_code, response.text)
                [device] = self.active_devices(role)
                self.assertEqual(user["session_id"], device["last_session_id"])
                self.assertEqual(previous["absolute_expires_at"], device["absolute_expires_at"])
                self.assertEqual(user["session_id"], self.conn.execute("SELECT session_id FROM user_sessions WHERE session_user_key=?", (f"{role}:7",)).fetchone()[0])
                self.assertNotEqual(before, response.cookies.get(remember.REMEMBER_COOKIE))
                self.assertIsNotNone(dependencies.verify_token(self.client.cookies.get("access_token")))
                self.assertTrue(all(row[0] for row in self.conn.execute("SELECT revoked_at FROM remember_login_tokens WHERE role=? AND selector<>?", (role, device["selector"]))))

    def test_revocation_which_wins_during_password_cpu_rejects_all_self_change_adapters(self):
        hash_password = dependencies.get_password_hash
        for role, path in (("student", "/api/student/password/change"), ("student", "/api/profile/password"),
                           ("teacher", "/api/profile/password")):
            with self.subTest(role=role, path=path):
                self.sign_in(role)
                entered = []

                def revoke_before_final_transaction(password):
                    self.assertEqual(0, self.checkouts)
                    encoded = hash_password(password)
                    self.admin_revoke(role)
                    entered.append(True)
                    return encoded

                with patch.object(dependencies, "get_password_hash", side_effect=revoke_before_final_transaction):
                    response = self.submit_change(path)
                self.assertEqual([True], entered)
                self.assertEqual(401, response.status_code, response.text)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual([], self.active_devices(role))
                self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key=?", (f"{role}:7",)).fetchone()[0])
                table = "students" if role == "student" else "teachers"
                self.assertEqual(self.hashed_password, self.conn.execute(f"SELECT hashed_password FROM {table} WHERE id=7").fetchone()[0])

    def test_late_cookie_after_password_change_commit_remains_revoked(self):
        self.sign_in()
        original_factory = auth.get_db_connection
        revoked = []

        @contextmanager
        def revoke_on_returned_connection():
            with original_factory() as conn:
                yield conn
                new_hash = conn.execute("SELECT hashed_password FROM students WHERE id=7").fetchone()[0]
                if new_hash != self.hashed_password and not revoked:
                    self.assertFalse(conn.in_transaction)
                    self.assertEqual(1, len(self.active_devices()))
                    self.admin_revoke()
                    revoked.append(True)

        with patch.object(auth, "get_db_connection", revoke_on_returned_connection):
            response = self.submit_change()
        self.assertEqual(200, response.status_code)
        self.assertEqual([True], revoked)
        self.assertTrue(response.cookies.get(remember.REMEMBER_COOKIE))
        self.assertEqual([], self.active_devices())
        self.assertEqual(0, self.count("user_sessions"))
        self.assertEqual("clear", remember.refresh_from_remember_cookie(
            response.cookies[remember.REMEMBER_COOKIE], "127.0.0.1", "synthetic gate").kind)

    def test_reset_deactivation_or_new_credentials_during_cpu_are_rechecked(self):
        hash_password = dependencies.get_password_hash
        cases = (("student", "reset", 400), ("teacher", "disable", 403), ("student", "credentials", 409))
        for role, transition, expected in cases:
            with self.subTest(role=role, transition=transition):
                self.conn.execute("UPDATE students SET password_reset_required=0,hashed_password=?", (self.hashed_password,))
                self.conn.execute("UPDATE teachers SET is_active=1")
                self.conn.commit()
                self.sign_in(role)

                def change_account_after_hash(password):
                    self.assertEqual(0, self.checkouts)
                    result = hash_password(password)
                    self.admin_revoke(role, reset=transition == "reset", disable=transition == "disable")
                    if transition == "credentials":
                        self.conn.execute("UPDATE students SET password_updated_at='newer-admin-revision'")
                        self.conn.commit()
                    return result

                with patch.object(dependencies, "get_password_hash", side_effect=change_account_after_hash):
                    response = self.submit_change("/api/profile/password")
                self.assertEqual(expected, response.status_code, response.text)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual([], self.active_devices(role))
                self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key=?", (f"{role}:7",)).fetchone()[0])

    def test_expired_access_auto_refresh_then_password_change_keeps_new_device_cookie(self):
        self.app.middleware("http")(remember_login_middleware)
        self.sign_in()
        previous = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.conn.execute("DELETE FROM user_sessions")
        self.conn.commit()
        dependencies.active_sessions.clear()
        response = self.submit_change()
        self.assertEqual(200, response.status_code, response.text)
        [active] = self.active_devices()
        cookie = response.cookies.get(remember.REMEMBER_COOKIE)
        self.assertTrue(cookie)
        self.assertNotEqual(previous, cookie)
        self.assertEqual(active["selector"], remember.parse_remember_cookie(cookie)[0])
        self.assertEqual(remember._hash_validator(remember.parse_remember_cookie(cookie)[1]), active["validator_hash"])
        self.assertEqual(1, sum(value.startswith(remember.REMEMBER_COOKIE + "=") for value in response.headers.get_list("set-cookie")))
        self.assertEqual(200, self.client.get("/protected").status_code)
        # The retained cookie must also work after the renewed access expires.
        self.conn.execute("DELETE FROM user_sessions")
        self.conn.commit()
        dependencies.active_sessions.clear()
        self.assertEqual(200, self.client.get("/protected").status_code)

    def test_standalone_late_reissue_requires_fresh_canonical_session_and_account(self):
        user = self.sign_in()
        remembered_until = self.active_devices()[0]["absolute_expires_at"]
        request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/profile/password",
                           "headers": [(b"cookie", f"access_token={self.client.cookies['access_token']}".encode())]})
        self.admin_revoke()
        response = JSONResponse({"ok": True})
        remember.reissue_for_current_device(request, response, user, remembered_until)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual([], self.active_devices())
        self.assertEqual(0, self.count("user_sessions"))

    def test_standalone_reissue_rejects_disabled_or_reset_account_even_with_matching_session(self):
        for role in ("student", "teacher"):
            with self.subTest(role=role):
                user = self.sign_in(role)
                remembered_until = self.active_devices(role)[-1]["absolute_expires_at"]
                request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/profile/password",
                    "headers": [(b"cookie", f"access_token={self.client.cookies['access_token']}".encode())]})
                self.conn.execute("UPDATE students SET password_reset_required=1" if role == "student" else "UPDATE teachers SET is_active=0")
                self.conn.commit()
                before = self.count("remember_login_tokens")
                response = JSONResponse({"ok": True})
                remember.reissue_for_current_device(request, response, user, remembered_until)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(before, self.count("remember_login_tokens"))

    def test_remember_insert_failure_rolls_back_password_devices_and_agent_revocation(self):
        self.sign_in()
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM remember_login_tokens")]
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        issue = remember.issue_remember_token

        def fail_after_device_insert(*args, **kwargs):
            issue(*args, **kwargs)
            raise DatabaseBusyError("Synthetic remember INSERT failure")

        with patch.object(remember, "issue_remember_token", side_effect=fail_after_device_insert):
            response = self.submit_change()
        self.assertEqual(503, response.status_code)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM remember_login_tokens")])
        self.assertEqual(cookie, self.client.cookies.get(remember.REMEMBER_COOKIE))
        self.assertEqual(self.hashed_password, self.conn.execute("SELECT hashed_password FROM students WHERE id=7").fetchone()[0])
        self.assertTrue(all(row[0] == "active" for row in self.conn.execute("SELECT status FROM agent_task_delegations")))
        self.assertIsNotNone(dependencies.verify_token(self.client.cookies.get("access_token")))

    def test_independent_mp_sessions_keep_password_change_without_issuing_web_session(self):
        for role in ("student", "teacher"):
            with self.subTest(role=role):
                token = self.mp_token(role)
                response = self.client.put("/api/profile/password", headers={"Authorization": "Bearer " + token}, json={
                    "current_password": self.password, "new_password": self.new_password, "confirm_password": self.new_password})
                self.assertEqual(200, response.status_code, response.text)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(0, self.count("user_sessions"))
                self.assertIsNotNone(mp.resolve_mp_session(self.conn, token))
                table = "students" if role == "student" else "teachers"
                self.assertTrue(dependencies.verify_password(self.new_password,
                    self.conn.execute(f"SELECT hashed_password FROM {table} WHERE id=7").fetchone()[0]))

    def test_mp_revocation_during_cpu_prevents_stale_authenticated_password_change(self):
        token = self.mp_token("student")
        hash_password = dependencies.get_password_hash

        def revoke_mp_after_hash(password):
            self.assertEqual(0, self.checkouts)
            result = hash_password(password)
            credentials.prepare_credentials_change(self.conn, role="student", user_id=7)
            self.assertTrue(mp.revoke_mp_session(self.conn, token))
            self.conn.commit()
            return result

        with patch.object(dependencies, "get_password_hash", side_effect=revoke_mp_after_hash):
            response = self.client.put("/api/profile/password", headers={"Authorization": "Bearer " + token}, json={
                "current_password": self.password, "new_password": self.new_password, "confirm_password": self.new_password})
        self.assertEqual(401, response.status_code, response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.hashed_password, self.conn.execute("SELECT hashed_password FROM students WHERE id=7").fetchone()[0])
        self.assertTrue(all(row[0] == "active" for row in self.conn.execute("SELECT status FROM agent_task_delegations")))
        self.assertEqual(0, self.count("user_sessions"))


def load_tests(loader, standard_tests, pattern):
    names = [name for name in loader.getTestCaseNames(CredentialIssuanceBoundaryTests)
             if name in CredentialIssuanceBoundaryTests.__dict__]
    return unittest.TestSuite(CredentialIssuanceBoundaryTests(name) for name in names)
