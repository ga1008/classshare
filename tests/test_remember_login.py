"""保持登录 on the in-memory auth harness: issue, silent re-login, rotation,
parallel grace, reuse detection, single-device conflict, logout scope,
account changes and device management. No network, no real database."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from classroom_app import dependencies
from classroom_app.routers import session as session_router
from classroom_app.services import remember_login_service as remember
from classroom_app.services.remember_login_middleware import remember_login_middleware
from tests import test_auth_form_fallback as _auth_harness

NEXT = "/protected?tab=files&item=2"


class _SharedConnection:
    """Like the app's connection: usable directly or as a context manager."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._conn.rollback()
        return False

    def close(self):
        """The shared in-memory database outlives each caller."""


class RememberLoginTests(_auth_harness.AuthFormFallbackTests):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict(os.environ, {
            "REMEMBER_LOGIN_ENABLED": "true", "REMEMBER_LOGIN_DAYS": "30",
            "REMEMBER_LOGIN_MAX_DAYS": "180", "REMEMBER_LOGIN_MAX_DEVICES": "5",
        }))
        shared = lambda *_args, **_kwargs: _SharedConnection(self.conn)  # noqa: E731
        for target in ("classroom_app.services.remember_login_service.get_db_connection",
                       "classroom_app.routers.session.get_db_connection",
                       "classroom_app.db.sessions.get_db_connection"):
            self.stack.enter_context(patch(target, shared))
        self.stack.enter_context(patch.dict(remember._RECENT_REFRESHES, {}, clear=True))
        self.app.middleware("http")(remember_login_middleware)
        self.app.include_router(session_router.router)

    # ------------------------------------------------------------ helpers
    def login_student(self, remember_me=True):
        data = {"remember_me": "1"} if remember_me else {}
        response = self.post_student(**data)
        self.assertEqual(303, response.status_code)
        return response

    def rows(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM remember_login_tokens ORDER BY id")]

    def expire_session(self):
        """Simulate the 24 h access session ending on the server."""
        self.conn.execute("DELETE FROM user_sessions")
        self.conn.commit()
        dependencies.active_sessions.clear()

    def use_only_remember_cookie(self, value):
        self.client.cookies.clear()
        self.client.cookies.set(remember.REMEMBER_COOKIE, value)

    # ------------------------------------------------------------ issue
    def test_ticked_login_issues_hashed_device_token(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.assertTrue(cookie)
        selector, validator = remember.parse_remember_cookie(cookie)
        [row] = self.rows()
        self.assertEqual(selector, row["selector"])
        self.assertNotEqual(validator, row["validator_hash"])
        self.assertEqual(remember._hash_validator(validator), row["validator_hash"])
        self.assertEqual(("7", "student"), (row["user_id"], row["role"]))
        self.assertTrue(row["last_session_id"])

    def test_unticked_login_issues_nothing_and_forgets_previous_device_token(self):
        self.login_student()
        old = self.client.cookies.get(remember.REMEMBER_COOKIE)
        response = self.login_student(remember_me=False)
        self.assertIn(f"{remember.REMEMBER_COOKIE}=", " ".join(response.headers.get_list("set-cookie")))
        [row] = self.rows()
        self.assertEqual(remember.parse_remember_cookie(old)[0], row["selector"])
        self.assertEqual("replaced_by_login", row["revoke_reason"])

    def test_teacher_login_can_also_be_remembered(self):
        response = self.client.post("/teacher/login", data={
            "email": "teacher@example.test", "password": self.password, "next": NEXT, "remember_me": "1"})
        self.assertEqual(303, response.status_code)
        self.assertEqual("teacher", self.rows()[0]["role"])

    # ------------------------------------------------------------ silent re-login
    def test_expired_session_is_silently_renewed_and_validator_rotated(self):
        self.login_student()
        old_cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(old_cookie)
        response = self.client.get("/protected")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"role": "student"}, response.json())
        self.assertIn("access_token=", " ".join(response.headers.get_list("set-cookie")))
        new_cookie = response.cookies.get(remember.REMEMBER_COOKIE)
        self.assertNotEqual(old_cookie, new_cookie)
        [row] = self.rows()
        self.assertEqual(remember._hash_validator(remember.parse_remember_cookie(old_cookie)[1]),
                         row["prev_validator_hash"])
        self.assertEqual(1, self.count("user_sessions"))

    def test_parallel_request_with_pre_rotation_cookie_gets_the_same_session(self):
        self.login_student()
        old_cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(old_cookie)
        first = self.client.get("/protected")
        first_token = first.cookies.get("access_token")
        self.use_only_remember_cookie(old_cookie)
        second = self.client.get("/protected")
        self.assertEqual(200, second.status_code)
        self.assertEqual(first_token, second.cookies.get("access_token"))
        self.assertEqual("", self.rows()[0]["revoked_at"])

    def test_live_session_needs_no_refresh(self):
        self.login_student()
        before = self.rows()[0]["validator_hash"]
        self.assertEqual(200, self.client.get("/protected").status_code)
        self.assertEqual(before, self.rows()[0]["validator_hash"])

    # ------------------------------------------------------------ safety
    def test_reused_old_validator_revokes_every_device_and_explains(self):
        self.login_student()
        old_cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        remember.issue_remember_token(self.conn, user_id=7, role="student", session_id="other")
        self.conn.commit()
        self.expire_session()
        self.use_only_remember_cookie(old_cookie)
        self.assertEqual(200, self.client.get("/protected").status_code)  # legit rotation
        remember._RECENT_REFRESHES.clear()
        long_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.conn.execute("UPDATE remember_login_tokens SET rotated_at = ?", (long_ago,))
        self.conn.commit()
        self.expire_session()
        self.use_only_remember_cookie(old_cookie)  # an attacker's copy
        response = self.client.get("/protected", headers={"accept": "text/html"})
        self.assertIn(response.status_code, (303, 401))
        self.assertTrue(all(row["revoked_at"] for row in self.rows()))
        self.assertIn("ls_remember_notice=security", " ".join(response.headers.get_list("set-cookie")))
        login_page = self.client.get("/student/login")
        self.assertIn("重复使用", login_page.text)

    def test_another_live_device_blocks_auto_login_instead_of_kicking_it(self):
        self.login_student()
        remembered = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.client.cookies.clear()
        self.login_student(remember_me=False)  # device B signs in with the password
        live_session = self.conn.execute("SELECT session_id FROM user_sessions").fetchone()[0]
        self.use_only_remember_cookie(remembered)
        response = self.client.get("/protected", headers={"accept": "text/html"})
        self.assertEqual(303, response.status_code)
        self.assertEqual(live_session, self.conn.execute("SELECT session_id FROM user_sessions").fetchone()[0])
        self.assertEqual("", self.rows()[0]["revoked_at"])
        page = self.client.get("/student/login")
        self.assertIn("另一台设备", page.text)

    def test_inactive_account_is_not_re_logged_in_and_tokens_are_revoked(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.conn.execute("UPDATE students SET enrollment_status = 'graduated'")
        self.conn.commit()
        self.use_only_remember_cookie(cookie)
        response = self.client.get("/protected")
        self.assertNotEqual(200, response.status_code)
        self.assertEqual("account_unavailable", self.rows()[0]["revoke_reason"])

    def test_expired_device_token_is_cleared(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.conn.execute("UPDATE remember_login_tokens SET expires_at = ?", (past,))
        self.conn.commit()
        self.expire_session()
        self.use_only_remember_cookie(cookie)
        self.assertNotEqual(200, self.client.get("/protected").status_code)
        self.assertEqual("expired", self.rows()[0]["revoke_reason"])

    def test_disabled_feature_ignores_cookie(self):
        self.login_student()
        cookie = self.client.cookies.get(remember.REMEMBER_COOKIE)
        self.expire_session()
        self.use_only_remember_cookie(cookie)
        with patch.dict(os.environ, {"REMEMBER_LOGIN_ENABLED": "false"}):
            self.assertNotEqual(200, self.client.get("/protected").status_code)

    # ------------------------------------------------------------ lifecycle
    def test_logout_forgets_only_this_device(self):
        self.login_student()
        remember.issue_remember_token(self.conn, user_id=7, role="student", session_id="elsewhere")
        self.conn.commit()
        response = self.client.get("/logout", headers={"sec-fetch-dest": "document", "sec-fetch-site": "same-origin"})
        self.assertEqual(303, response.status_code)
        reasons = [row["revoke_reason"] for row in self.rows()]
        self.assertEqual(["logout", ""], reasons)

    def test_account_level_invalidation_forgets_every_device(self):
        self.login_student()
        remember.issue_remember_token(self.conn, user_id=7, role="student", session_id="elsewhere")
        self.conn.commit()
        dependencies.invalidate_session_for_user("7", "student", revoke_remembered=False)
        self.assertTrue(all(not row["revoked_at"] for row in self.rows()))
        dependencies.invalidate_session_for_user("7", "student")
        self.assertTrue(all(row["revoked_at"] for row in self.rows()))

    def test_device_limit_keeps_the_most_recent(self):
        for index in range(7):
            remember.issue_remember_token(self.conn, user_id=7, role="student", session_id=f"s{index}")
        self.conn.commit()
        active = [row for row in self.rows() if not row["revoked_at"]]
        self.assertEqual(5, len(active))
        self.assertTrue(all(row["revoke_reason"] in ("", "device_limit") for row in self.rows()))

    # ------------------------------------------------------------ pages and device management
    def test_login_page_sends_a_signed_in_user_on(self):
        self.login_student()
        response = self.client.get("/student/login?next=/protected")
        self.assertEqual(303, response.status_code)
        self.assertEqual("/protected", response.headers["location"])
        self.assertEqual(200, self.client.get("/teacher/login").status_code)  # other role still sees its form

    def test_login_form_offers_the_opt_in_checkbox(self):
        page = self.client.get("/student/login")
        self.assertIn('name="remember_me"', page.text)
        self.assertIn("30 天内保持登录", page.text)
        with patch.dict(os.environ, {"REMEMBER_LOGIN_ENABLED": "false"}):
            self.assertNotIn('name="remember_me"', self.client.get("/student/login").text)

    def test_device_list_revoke_and_sign_out_everywhere(self):
        self.login_student()
        remember.issue_remember_token(self.conn, user_id=7, role="student", session_id="elsewhere",
                                      ip="203.0.113.9")
        self.conn.commit()
        listing = self.client.get("/api/session/remembered").json()
        self.assertEqual(2, len(listing["items"]))
        self.assertEqual(1, sum(1 for item in listing["items"] if item["current"]))
        other = next(item for item in listing["items"] if not item["current"])
        self.assertEqual("203.0.*.*", other["ip"])
        self.assertEqual(200, self.client.delete(f"/api/session/remembered/{other['id']}").status_code)
        self.assertEqual(404, self.client.delete(f"/api/session/remembered/{other['id']}").status_code)
        response = self.client.post("/api/session/remembered/revoke-all")
        self.assertEqual("/student/login", response.json()["redirect_to"])
        self.assertTrue(all(row["revoked_at"] for row in self.rows()))
        self.assertEqual(0, self.count("user_sessions"))

    def test_other_users_device_cannot_be_revoked(self):
        self.login_student()
        foreign = remember.issue_remember_token(self.conn, user_id=99, role="student", session_id="x")
        self.conn.commit()
        foreign_id = self.conn.execute(
            "SELECT id FROM remember_login_tokens WHERE selector = ?",
            (remember.parse_remember_cookie(foreign)[0],)).fetchone()[0]
        self.assertEqual(404, self.client.delete(f"/api/session/remembered/{foreign_id}").status_code)


class RememberCookieParsingTests(unittest.TestCase):
    def test_parse_rejects_malformed_values(self):
        for value in ("", "abc", ".x", "x.", "a.b;c", "a" * 300 + ".b", "a.b.c"):
            with self.subTest(value=value):
                self.assertIsNone(remember.parse_remember_cookie(value))
        self.assertEqual(("sel_1", "val-2"), remember.parse_remember_cookie("sel_1.val-2"))


def load_tests(loader, standard_tests, pattern):
    """Run only this module's tests, not the inherited harness tests again."""
    suite = unittest.TestSuite()
    for case in (RememberLoginTests, RememberCookieParsingTests):
        names = [name for name in loader.getTestCaseNames(case) if name in case.__dict__]
        suite.addTests(case(name) for name in names)
    return suite


if __name__ == "__main__":
    unittest.main()
