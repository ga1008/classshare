"""D3 on the existing isolated, in-memory, no-lifespan auth test app."""
import os
import unittest
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import Depends, HTTPException
from jose import jwt

from classroom_app import dependencies
from classroom_app.core import templates
from tests.test_auth_form_fallback import AuthFormFallbackTests


class SessionRecoveryTests(AuthFormFallbackTests):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict(os.environ, {"LANSHARE_LQ_FAMILIES": "centered"}))
        # Source-only SSR contract: a new module has deliberately not entered
        # root's official asset graph yet. Browser gates will assert that graph.
        self.stack.enter_context(patch.dict(templates.env.globals, {"asset_url": lambda name: "/static/" + name}))

        @self.app.api_route("/dashboard", methods=["GET", "HEAD", "POST"])
        @self.app.api_route("/student/work", methods=["GET", "HEAD"])
        @self.app.api_route("/manage/session-check", methods=["GET", "HEAD"])
        @self.app.get("/download/session-check")
        def protected(user=Depends(dependencies.get_current_user)):
            return {"role": user["role"]}

        @self.app.get("/api/manage/session-check")
        def teacher_api(user=Depends(dependencies.get_current_teacher)):
            return {"role": user["role"]}

        @self.app.get("/teacher/login/")
        def forced_auth_loop():
            raise HTTPException(401, "synthetic authentication failure")

    def get_recovery(self, path="/dashboard?tab=files&item=2", token="invalid-synthetic-token", **kwargs):
        self.client.cookies.clear()
        self.client.cookies.set("access_token", token)
        return self.client.get(path, headers={"accept": "text/html", **kwargs.pop("headers", {})}, **kwargs)

    def assert_recovery(self, response, expected_next):
        self.assertEqual(401, response.status_code)
        self.assertEqual("session_expired.html", response.template.name)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertNotIn("location", response.headers)
        self.assertIsNone(response.context["user_info"])
        for key in ("session_login_url", "session_alternate_login_url"):
            if response.context[key]:
                parsed = urlsplit(response.context[key])
                self.assertEqual("", parsed.netloc)
                self.assertIn(parsed.path, ("/teacher/login", "/student/login"))
                self.assertEqual([expected_next], parse_qs(parsed.query)["next"])

    def test_bad_cookie_shared_html_is_401_no_store_with_two_safe_role_links(self):
        response = self.get_recovery()
        self.assert_recovery(response, "/dashboard?tab=files&item=2")
        self.assertIn("学生重新登录", response.text)
        self.assertIn("教师重新登录", response.text)
        self.assertFalse(response.context["session_auto_redirect"])
        self.assertEqual(0, self.count("user_sessions"))
        self.assertEqual(0, self.count("student_login_audit_logs"))

    def test_required_role_routes_keep_the_same_protected_query_and_one_link(self):
        for route, role in (("/manage/session-check", "teacher"), ("/student/work", "student")):
            with self.subTest(role=role):
                response = self.get_recovery(route + "?view=files&item=3")
                self.assert_recovery(response, route + "?view=files&item=3")
                self.assertTrue(response.context["session_login_url"].startswith(f"/{role}/login?"))
                self.assertIsNone(response.context["session_alternate_login_url"])
                self.assertTrue(response.context["session_auto_redirect"])

    def test_missing_cookie_flag_off_post_non_html_and_fetch_keep_existing_redirect(self):
        self.client.cookies.clear()
        self.assertEqual(303, self.client.get("/dashboard", headers={"accept": "text/html"}).status_code)
        with patch.dict(os.environ, {"LANSHARE_LQ_FAMILIES": ""}):
            self.assertEqual(303, self.get_recovery().status_code)
        for headers in ({"accept": "application/json"}, {"accept": "text/html;q=0.0000"},
                        {"accept": "text/html;q=invalid"},
                        {"sec-fetch-dest": "empty"}, {"x-requested-with": "XMLHttpRequest"}):
            with self.subTest(headers=headers):
                self.assertEqual(303, self.get_recovery(headers=headers).status_code)
        self.client.cookies.set("access_token", "invalid")
        self.assertEqual(303, self.client.post("/dashboard", headers={"accept": "text/html"}).status_code)

    def test_head_has_recovery_status_cookie_headers_and_no_response_body(self):
        self.client.cookies.set("access_token", "invalid")
        response = self.client.head("/manage/session-check?section=mine", headers={"accept": "text/html"})
        self.assert_recovery(response, "/manage/session-check?section=mine")
        self.assertEqual(b"", response.content)

    def test_empty_and_expired_cookie_are_unavailable_without_trusting_expired_role(self):
        expired = jwt.encode({"user_id": "7", "role": "teacher", "exp": 1}, dependencies.SECRET_KEY, algorithm=dependencies.ALGORITHM)
        for token in ("", expired):
            with self.subTest(kind="empty" if not token else "expired"):
                response = self.get_recovery(token=token)
                self.assert_recovery(response, "/dashboard?tab=files&item=2")
                self.assertTrue(response.context["session_login_url"].startswith("/student/login?"))
                self.assertTrue(response.context["session_alternate_login_url"].startswith("/teacher/login?"))
                if token:
                    self.assertNotIn(token, response.text)

    def test_replaced_browser_recovery_does_not_invalidate_the_new_legitimate_session(self):
        self.post_student()
        old = self.client.cookies["access_token"]
        self.post_student()
        new = self.client.cookies["access_token"]
        before = dict(self.conn.execute("SELECT * FROM user_sessions").fetchone())
        response = self.get_recovery(token=old)
        self.assert_recovery(response, "/dashboard?tab=files&item=2")
        self.assertEqual(before, dict(self.conn.execute("SELECT * FROM user_sessions").fetchone()))
        self.client.cookies.clear()
        self.client.cookies.set("access_token", new)
        self.assertEqual({"role": "student"}, self.client.get("/dashboard").json())
        self.assertEqual(2, self.count("student_login_audit_logs"))

    def test_api_401_envelope_and_authenticated_role_403_stay_unchanged(self):
        self.client.cookies.set("access_token", "invalid")
        response = self.client.get("/api/protected", headers={"accept": "text/html", "referer": "http://testserver/dashboard?scope=mine"})
        self.assertEqual(401, response.status_code)
        self.assertEqual("login_required", response.json()["error"]["code"])
        self.assertEqual(["/dashboard?scope=mine"], parse_qs(urlsplit(response.json()["redirect_to"]).query)["next"])
        self.client.cookies.clear()
        self.post_student()
        before = self.client.cookies["access_token"]
        response = self.client.get("/manage/auth-fixture", headers={"accept": "text/html"})
        self.assertEqual(303, response.status_code)
        self.assertTrue(response.headers["location"].startswith("/auth/forbidden?"))
        self.assertEqual(before, self.client.cookies["access_token"])
        response = self.client.get("/api/manage/session-check", headers={"accept": "text/html"})
        self.assertEqual(403, response.status_code)
        self.assertEqual("permission_denied", response.json()["error"]["code"])
        self.assertEqual("teacher", response.json()["required_role"])
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(200, self.client.get("/protected").status_code)

    def test_unsafe_referers_and_auth_loop_do_not_become_recovery_return_targets(self):
        for referer in ("https://external.invalid/secret", "http://testserver/\\evil.invalid/path",
                        "http://testserver/teacher/login/?next=/secret", "http://testserver/bad\x7fpath"):
            with self.subTest(referer=repr(referer)):
                response = self.get_recovery("/download/session-check", headers={"referer": referer})
                self.assert_recovery(response, "/dashboard")
        response = self.get_recovery("/teacher/login/")
        self.assert_recovery(response, "/dashboard")

    def test_no_script_markup_promises_no_automatic_navigation_and_never_deletes_cookie(self):
        class Nodes(HTMLParser):
            def __init__(self, html):
                super().__init__(); self.items = []; self.feed(html)

            def handle_starttag(self, tag, attrs):
                self.items.append((tag, dict(attrs)))

        response = self.get_recovery("/student/work?source=unavailable")
        nodes = Nodes(response.text).items
        note = next(attrs for _, attrs in nodes if "data-session-countdown" in attrs)
        self.assertIn("hidden", note)
        self.assertEqual("true", note["data-auto-redirect"])
        primary = next(attrs for _, attrs in nodes if "data-lq-session-login" in attrs)
        self.assertEqual(response.context["session_login_url"], primary["href"])
        self.assertNotIn("document.cookie", response.text)
        self.assertTrue(any(attrs.get("type") == "module" and "session_expired.js" in attrs.get("src", "") for _, attrs in nodes))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SessionRecoveryTests(name) for name in SessionRecoveryTests.__dict__ if name.startswith("test_"))
