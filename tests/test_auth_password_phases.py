"""Password CPU isolation and fresh authority checks on an in-memory auth app."""
import asyncio
from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

import httpx

from classroom_app import dependencies
from classroom_app.app import database_busy_exception_handler
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.routers.ui_parts import auth, common
from tests import test_auth_form_fallback as harness


class PasswordLoginPhaseTests(harness.AuthFormFallbackTests):
    def setUp(self):
        super().setUp()
        self.checkouts = 0
        self.checkout_lock = threading.Lock()
        # The production pool lends distinct connections. This fixture reuses
        # one SQLite connection, whose transaction state is shared by threads.
        self.fixture_connection_lock = threading.RLock()
        self.stack.enter_context(patch.object(auth, "get_db_connection", self.tracked_connection))
        self.stack.enter_context(patch.object(common, "get_db_connection", self.tracked_connection))
        self.app.add_exception_handler(DatabaseBusyError, database_busy_exception_handler)

    @contextmanager
    def tracked_connection(self):
        with self.fixture_connection_lock:
            with self.checkout_lock:
                self.checkouts += 1
            try:
                with self.connection() as conn:
                    yield conn
            finally:
                with self.checkout_lock:
                    self.checkouts -= 1

    def test_student_json_native_and_teacher_verify_without_holding_a_connection(self):
        original = dependencies.verify_password
        observed = []

        def verify(password, encoded):
            observed.append((self.checkouts, threading.current_thread().name))
            return original(password, encoded)

        with patch.object(dependencies, "verify_password", verify):
            self.assertEqual(200, self.post_student("/api/student/login/password").status_code)
            self.assertEqual(303, self.post_student().status_code)
            self.assertEqual(303, self.client.post("/teacher/login", data={
                "email": "teacher@example.test", "password": self.password,
            }).status_code)
        self.assertEqual(3, len(observed))
        self.assertTrue(all(count == 0 and name.startswith("password-verify") for count, name in observed), observed)
        self.assertEqual(0, self.checkouts)

    def test_student_password_or_status_change_during_hash_cannot_issue_a_session(self):
        original = dependencies.verify_password
        for sql, status in (("UPDATE students SET hashed_password = 'changed'", 409),
                            ("UPDATE students SET enrollment_status = 'suspended'", 403),
                            ("UPDATE students SET password_reset_required = 1", 409)):
            with self.subTest(sql=sql):
                self.conn.execute("UPDATE students SET hashed_password = ?, enrollment_status = 'active', password_reset_required = 0", (self.hashed_password,))
                self.conn.commit()

                def verify(password, encoded):
                    self.assertEqual(0, self.checkouts)
                    result = original(password, encoded)
                    self.conn.execute(sql)
                    self.conn.commit()
                    return result

                with patch.object(dependencies, "verify_password", verify):
                    response = self.post_student("/api/student/login/password")
                self.assertEqual(status, response.status_code)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(0, self.count("user_sessions"))
                self.assertEqual(0, self.count("student_login_audit_logs"))

    def test_teacher_deactivation_during_hash_prevents_stale_login(self):
        original = dependencies.verify_password

        def verify(password, encoded):
            result = original(password, encoded)
            self.conn.execute("UPDATE teachers SET is_active = 0")
            self.conn.commit()
            return result

        with patch.object(dependencies, "verify_password", verify):
            response = self.client.post("/teacher/login", data={"email": "teacher@example.test", "password": self.password})
        self.assertEqual(409, response.status_code)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(0, self.count("user_sessions"))

    def test_session_write_failure_rolls_back_audit_and_preserves_previous_login(self):
        self.post_student()
        before = dict(self.conn.execute("SELECT * FROM user_sessions").fetchone())
        cookie = self.client.cookies.get("access_token")
        save = dependencies.save_user_session

        def fail_after_write(**kwargs):
            save(**kwargs)
            raise DatabaseBusyError("Synthetic failure after session INSERT")

        with patch.object(dependencies, "save_user_session", fail_after_write):
            response = self.post_student("/api/student/login/password")
        self.assertEqual(503, response.status_code)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(before, dict(self.conn.execute("SELECT * FROM user_sessions").fetchone()))
        self.assertEqual(1, self.count("student_login_audit_logs"))
        self.assertEqual(cookie, self.client.cookies.get("access_token"))
        self.assertEqual(200, self.client.get("/protected").status_code)

    def test_saturated_password_pool_leaves_asgi_and_default_db_threads_available(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        state_lock = threading.Lock()
        state = {"started": 0, "finished": 0}
        original = dependencies.verify_password

        def wait_for_hash(password, encoded):
            with state_lock:
                state["started"] += 1
                if state["started"] == 4:
                    entered.set()
            release.wait(2)
            result = original(password, encoded)
            with state_lock:
                state["finished"] += 1
                if state["finished"] == 4:
                    finished.set()
            return result

        @self.app.get("/live")
        async def live():
            return {"ok": True}

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://synthetic.test") as client:
                tasks = [asyncio.create_task(client.post("/api/student/login/password", data={
                    "identifier": "S7", "password": self.password,
                })) for _ in range(4)]
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1),
                                    (dict(state), [(task.done(), repr(task.exception()) if task.done() and not task.cancelled() else None,
                                                    (task.result().status_code, task.result().text[:200]) if task.done() and not task.cancelled() and task.exception() is None else None)
                                                   for task in tasks]))
                    self.assertEqual(0, self.checkouts)
                    self.assertTrue(all(not task.done() for task in tasks))
                    response = await asyncio.wait_for(client.get("/live"), 0.5)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(0, self.checkouts)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    release.set()
                    self.assertTrue(await asyncio.to_thread(finished.wait, 2), dict(state))

        with patch.object(dependencies, "verify_password", wait_for_hash):
            asyncio.run(scenario())
        self.assertEqual(0, self.count("user_sessions"))
        self.assertEqual(0, self.count("student_login_audit_logs"))


def load_tests(loader, standard_tests, pattern):
    names = [name for name in loader.getTestCaseNames(PasswordLoginPhaseTests) if name in PasswordLoginPhaseTests.__dict__]
    return unittest.TestSuite(PasswordLoginPhaseTests(name) for name in names)
