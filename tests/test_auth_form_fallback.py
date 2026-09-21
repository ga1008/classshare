"""Authentication adapters on a minimal ASGI app and an owned in-memory DB.

Run through tools/test_backend.py; no application lifespan, network or workers.
"""
from contextlib import contextmanager, ExitStack, redirect_stdout
from html.parser import HTMLParser
import io
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.app import (
    http_exception_handler, request_validation_exception_handler,
    unauthorized_exception_handler, forbidden_exception_handler,
)
from classroom_app.routers.ui_parts import auth, common


class AuthFormFallbackTests(unittest.TestCase):
    password = "Synthetic-password-2026!"

    @classmethod
    def setUpClass(cls):
        cls.hashed_password = dependencies.get_password_hash(cls.password)

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT, created_by_teacher_id INTEGER);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, student_id_number TEXT UNIQUE,
                class_id INTEGER, hashed_password TEXT, password_reset_required INTEGER, enrollment_status TEXT);
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, hashed_password TEXT, is_active INTEGER);
            CREATE TABLE student_login_audit_logs (id INTEGER PRIMARY KEY, student_id INTEGER,
                class_id INTEGER, class_name_snapshot TEXT, login_sequence INTEGER, login_method TEXT,
                identifier_type TEXT, identifier_value TEXT, ip_address TEXT, user_agent TEXT,
                device_type TEXT, os_name TEXT, browser_name TEXT, device_label TEXT, logged_at TEXT);
            CREATE TABLE user_sessions (session_user_key TEXT PRIMARY KEY, session_id TEXT, user_id TEXT,
                role TEXT, name TEXT, ip TEXT, last_login TEXT, expires_at TEXT, updated_at TEXT);
            INSERT INTO classes VALUES (30, 'Synthetic class', 7);
        """)
        self.conn.execute("INSERT INTO students VALUES(7,'Synthetic student','S7',30,?,0,'active')", (self.hashed_password,))
        self.conn.execute("INSERT INTO teachers VALUES(7,'Synthetic teacher','teacher@example.test',?,1)", (self.hashed_password,))
        self.conn.commit()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        for target in ("classroom_app.routers.ui_parts.auth.get_db_connection",
                       "classroom_app.routers.ui_parts.common.get_db_connection",
                       "classroom_app.dependencies.get_db_connection",
                       "classroom_app.db.sessions.get_db_connection"):
            self.stack.enter_context(patch(target, self.connection))
        for target in ("sqlite3.connect", "classroom_app.database.get_db_connection",
                       "classroom_app.db.connection.get_db_connection", "classroom_app.db.connection.connect_postgres"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Connection outside auth in-memory fixture")))
        self.stack.enter_context(patch.dict(dependencies.active_sessions, {}, clear=True))
        self.stack.enter_context(patch.dict(dependencies._identity_validation_cache, {}, clear=True))
        # The real templates are rendered; unauthenticated theme rendering and
        # decorative login tips must not contact unrelated application tables.
        self.stack.enter_context(patch.dict(auth.templates.env.globals, {
            "resolve_user_ui_preferences": lambda *_: {"enabled": False},
        }))
        self.stack.enter_context(patch.object(common, "build_student_global_cultivation_profile", return_value=None))
        self.stack.enter_context(patch.object(common, "build_login_tip_payload_for_student", return_value=None))
        self.perform = self.stack.enter_context(patch.object(auth, "_perform_student_password_login", wraps=common._perform_student_password_login))
        self.session_save = self.stack.enter_context(patch.object(dependencies, "save_user_session", wraps=dependencies.save_user_session))
        self.app = FastAPI()
        self.app.include_router(auth.router)
        self.app.add_exception_handler(HTTPException, http_exception_handler)
        self.app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
        self.app.add_exception_handler(401, unauthorized_exception_handler)
        self.app.add_exception_handler(403, forbidden_exception_handler)

        @self.app.get("/protected")
        def protected(user=Depends(dependencies.get_current_user)):
            return {"role": user["role"]}

        @self.app.get("/api/protected")
        def api_protected(user=Depends(dependencies.get_current_user)):
            return {"role": user["role"]}

        @self.app.get("/manage/auth-fixture")
        def teacher_only(user=Depends(dependencies.get_current_teacher)):
            return {"role": user["role"]}

        self.client = TestClient(self.app, follow_redirects=False)
        self.addCleanup(self.client.close)

    @contextmanager
    def connection(self):
        try:
            yield self.conn
        except Exception:
            self.conn.rollback()
            raise

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def post_student(self, path="/student/login", **overrides):
        return self.client.post(path, data={"identifier": "S7", "password": self.password,
                                         "next": "/protected?tab=files&item=2", **overrides})

    def assert_form_error(self, response, role, status, identifier):
        self.assertEqual(status, response.status_code)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(f"{role}_login_v4.html", response.template.name)
        context = response.context
        self.assertTrue(context["login_error"])
        self.assertEqual(identifier, context["login_identifier" if role == "student" else "login_email"])
        self.assertEqual("/protected?tab=files&item=2", context["next_url"])
        for key in ("teacher_entry_url", "student_entry_url"):
            self.assertEqual([context["next_url"]], parse_qs(urlsplit(context[key]).query)["next"])
        self.assertNotIn("password", context)
        self.assertNotIn(self.password, response.text)
        self.assertNotIn("access_token=", response.headers.get("set-cookie", ""))
        self.assertEqual(0, self.count("user_sessions"))
        self.assertEqual(0, self.count("student_login_audit_logs"))

    def test_student_html_and_json_share_one_authentication_and_one_login_write(self):
        for endpoint in ("/student/login", "/api/student/login/password"):
            with self.subTest(endpoint=endpoint):
                before = self.count("student_login_audit_logs")
                calls = self.session_save.call_count
                response = self.post_student(endpoint, identifier=" S7 ")
                self.assertEqual(303 if endpoint == "/student/login" else 200, response.status_code)
                target = response.headers["location"] if response.status_code == 303 else response.json()["redirect_to"]
                self.assertEqual("/protected?tab=files&item=2", target)
                self.assertEqual("no-store", response.headers["cache-control"])
                self.assertIn("HttpOnly", response.headers["set-cookie"])
                self.assertIn("SameSite=lax", response.headers["set-cookie"])
                self.assertEqual(before + 1, self.count("student_login_audit_logs"))
                self.assertEqual(calls + 1, self.session_save.call_count)
                self.assertEqual(1, self.count("user_sessions"))
                self.assertEqual("student", self.client.get(target).json()["role"])
        self.assertEqual(2, self.perform.call_count)
        row = self.conn.execute("SELECT * FROM student_login_audit_logs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(("password", "student_id_number", "S7"), (row["login_method"], row["identifier_type"], row["identifier_value"]))

    def test_student_bad_password_renders_form_without_creating_session(self):
        self.assert_form_error(self.post_student(password="incorrect"), "student", 400, "S7")
        self.assertEqual(1, self.perform.call_count)

    def test_error_forms_escape_preserved_identifiers_and_never_restore_password_fields(self):
        class Inputs(HTMLParser):
            def __init__(self, html):
                super().__init__(); self.inputs = []; self.feed(html)

            def handle_starttag(self, tag, attrs):
                if tag == "input":
                    self.inputs.append(dict(attrs))

        value = '\"><img src=x onerror=alert(1)>'
        for endpoint, field in (("/student/login", "identifier"), ("/teacher/login", "email")):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, data={field: value, "password": self.password})
                self.assertEqual(400, response.status_code)
                self.assertNotIn("<img src=x", response.text)
                inputs = Inputs(response.text).inputs
                self.assertEqual(value, next(item["value"] for item in inputs if item.get("name") == field))
                self.assertTrue(all(not item.get("value") for item in inputs if item.get("type") == "password"))
                self.assertEqual(0, self.count("user_sessions"))

    def test_student_missing_and_blank_fields_restore_form_without_authentication(self):
        for fields in ({}, {"identifier": "S7"}, {"password": self.password}, {"identifier": "   ", "password": self.password}):
            with self.subTest(fields=list(fields)):
                response = self.client.post("/student/login", data={**fields, "next": "/protected?tab=files&item=2"})
                self.assert_form_error(response, "student", 400, fields.get("identifier", "").strip())
        self.perform.assert_not_called()

    def test_student_identity_states_keep_400_403_409_with_original_messages(self):
        for changes, expected, message in (
            ({"hashed_password": None}, 400, "尚未设置密码"),
            ({"password_reset_required": 1}, 409, "教师已通过"),
            ({"enrollment_status": "suspended"}, 403, "暂不纳入课堂学习"),
        ):
            with self.subTest(changes=changes):
                self.conn.execute("UPDATE students SET hashed_password=?,password_reset_required=0,enrollment_status='active'", (self.hashed_password,))
                for column, value in changes.items():
                    self.conn.execute(f"UPDATE students SET {column}=?", (value,))
                self.conn.commit()
                response = self.post_student()
                self.assert_form_error(response, "student", expected, "S7")
                self.assertIn(message, response.context["login_error"])

    def test_duplicate_student_name_remains_error_and_student_number_still_works(self):
        self.conn.execute("INSERT INTO students VALUES(8,'Synthetic student','S8',30,?,0,'active')", (self.hashed_password,))
        self.conn.commit()
        response = self.post_student(identifier="Synthetic student")
        self.assert_form_error(response, "student", 400, "Synthetic student")
        self.assertIn("重名", response.context["login_error"])
        self.assertEqual(303, self.post_student().status_code)

    def test_legacy_identity_post_does_not_bypass_password_setup(self):
        response = self.client.post("/student/login", data={"name": "Synthetic student", "student_id_number": "S7", "next": "/protected?tab=files&item=2"})
        self.assert_form_error(response, "student", 400, "")
        self.assertIn("完成密码设置", response.context["login_error"])
        self.perform.assert_not_called()

    def test_teacher_wrong_missing_or_inactive_credentials_restore_email(self):
        for fields in ({}, {"email": "Teacher@Example.Test"}, {"email": "Teacher@Example.Test", "password": "incorrect"}):
            with self.subTest(fields=list(fields)):
                response = self.client.post("/teacher/login", data={**fields, "next": "/protected?tab=files&item=2"})
                self.assert_form_error(response, "teacher", 400, fields.get("email", ""))
        self.conn.execute("UPDATE teachers SET is_active=0")
        self.conn.commit()
        response = self.client.post("/teacher/login", data={"email": "teacher@example.test", "password": self.password, "next": "/protected?tab=files&item=2"})
        self.assert_form_error(response, "teacher", 400, "teacher@example.test")

    def test_teacher_success_retains_native_redirect_and_role_scoped_student_session(self):
        student = self.post_student()
        student_token = self.client.cookies["access_token"]
        response = self.client.post("/teacher/login", data={"email": " Teacher@Example.Test ", "password": self.password, "next": "/manage/auth-fixture?tab=mine"})
        self.assertEqual(303, response.status_code)
        self.assertEqual("/manage/auth-fixture?tab=mine", response.headers["location"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertIn("cultivation_reveal=1", response.headers["set-cookie"])
        self.assertEqual("teacher", self.client.get(response.headers["location"]).json()["role"])
        self.assertEqual(2, self.count("user_sessions"))
        self.assertEqual(1, self.count("student_login_audit_logs"))
        self.assertEqual("student", dependencies.verify_token(student_token, "testclient")["role"])
        self.assertEqual(2, self.session_save.call_count)
        self.assertEqual(303, student.status_code)

    def test_registration_is_closed_even_without_required_fields(self):
        for method, fields in (("get", None), ("post", {}), ("post", {"name": "Uncreated", "email": "new@example.test", "password": self.password})):
            with self.subTest(method=method, fields=bool(fields)):
                response = self.client.request(method, "/teacher/register", data=fields)
                self.assertEqual(403, response.status_code)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("超管", response.text)
                self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(1, self.count("teachers"))
        self.assertEqual(0, self.count("user_sessions"))

    def test_api_errors_and_validation_keep_existing_json_contract_and_no_store(self):
        for fields, expected in (({}, 422), ({"identifier": "S7", "password": "incorrect"}, 400)):
            response = self.client.post("/api/student/login/password", data=fields)
            self.assertEqual(expected, response.status_code)
            self.assertIn("detail", response.json())
            self.assertEqual("no-store", response.headers["cache-control"])
        # Preserve the global handler's API403 -> unauthenticated401 behavior.
        self.conn.execute("UPDATE students SET enrollment_status='suspended'")
        self.conn.commit()
        response = self.post_student("/api/student/login/password")
        self.assertEqual(401, response.status_code)
        self.assertIn("redirect_to", response.json())
        self.assertEqual("no-store", response.headers["cache-control"])

    def test_auth_post_validation_and_token_errors_are_uncached_without_changing_json(self):
        for endpoint in ("/api/student/login/identity", "/api/student/password/setup", "/api/student/password/forgot", "/api/student/password/forgot/class-hint"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, data={})
                self.assertEqual(422, response.status_code)
                self.assertEqual("no-store", response.headers["cache-control"])
                self.assertIn("detail", response.json())
        response = self.client.post("/api/student/password/setup", data={"setup_token": "invalid", "password": self.password, "confirm_password": self.password})
        self.assertEqual(400, response.status_code)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(0, self.count("user_sessions"))

    def test_html_and_api_401_preserve_safe_page_source_for_native_form_login(self):
        response = self.client.get("/protected?tab=files&item=2")
        self.assertEqual(303, response.status_code)
        login_url = response.headers["location"]
        login = self.client.get(login_url)
        self.assertEqual("/protected?tab=files&item=2", login.context["next_url"])
        self.assertEqual(303, self.post_student(next=login.context["next_url"]).status_code)
        self.assertEqual(200, self.client.get(login.context["next_url"]).status_code)
        self.client.cookies.clear()
        for referer, expected in (("http://testserver/protected?tab=files", "/protected?tab=files"),
                                  ("https://external.invalid/protected?tab=files", "/dashboard")):
            response = self.client.get("/api/protected", headers={"referer": referer})
            self.assertEqual(401, response.status_code)
            target = response.json()["redirect_to"]
            self.assertEqual([expected], parse_qs(urlsplit(target).query)["next"])
        teacher = self.client.get("/manage/auth-fixture?tab=mine")
        self.assertTrue(teacher.headers["location"].startswith("/teacher/login?"))

    def test_replaced_session_uses_existing_401_recovery_without_cross_role_access(self):
        self.post_student()
        old_token = self.client.cookies["access_token"]
        self.post_student()
        self.client.cookies.set("access_token", old_token, domain="testserver.local", path="/")
        response = self.client.get("/protected?tab=files")
        self.assertEqual(303, response.status_code)
        self.assertEqual(["/protected?tab=files"], parse_qs(urlsplit(response.headers["location"]).query)["next"])
        self.assertIn('access_token=""', response.headers["set-cookie"])
        self.post_student()
        denied = self.client.get("/manage/auth-fixture")
        self.assertEqual(303, denied.status_code)
        self.assertTrue(denied.headers["location"].startswith("/auth/forbidden?"))

    def test_unexpected_auth_failure_is_not_reported_as_bad_credentials(self):
        with patch.object(auth, "_perform_student_password_login", side_effect=RuntimeError("Synthetic unavailable database")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic unavailable database"):
                self.post_student()
        self.assertEqual(0, self.count("user_sessions"))


class SafeNextPathTests(unittest.TestCase):
    def test_external_backslash_and_every_ascii_control_are_rejected(self):
        for value in (None, "", "relative", "https://example.invalid/", "//example.invalid/", "/\\example.invalid/path",
                      "/path\\name", *[f"/path{chr(code)}tail" for code in [*range(32), 127]]):
            with self.subTest(value=repr(value)):
                self.assertFalse(dependencies.is_safe_local_path(value))
                self.assertEqual("/dashboard", dependencies.sanitize_next_path(value))

    def test_all_auth_paths_reject_trailing_slash_loops_but_valid_queries_remain(self):
        for value in dependencies._AUTH_PAGE_PATHS:
            for suffix in ("", "/", "///", "/?next=/protected"):
                with self.subTest(value=value + suffix):
                    self.assertEqual("/dashboard", dependencies.sanitize_next_path(value + suffix))
        self.assertEqual("/protected?filter=中文&item=0", dependencies.sanitize_next_path(" /protected?filter=中文&item=0#ignored "))
        self.assertEqual("/", dependencies.sanitize_next_path("/"))
        self.assertEqual("", dependencies.sanitize_next_path("//external.invalid", fallback=""))

    def test_url_parser_errors_fail_closed(self):
        with patch.object(dependencies, "urlsplit", side_effect=ValueError("Malformed URL")):
            self.assertFalse(dependencies.is_safe_local_path("/protected"))
            self.assertEqual("/dashboard", dependencies.sanitize_next_path("/protected"))

    def test_login_url_and_same_origin_referer_use_the_same_path_guard(self):
        target = dependencies.build_login_url("/student/login", "/\\example.invalid/path")
        self.assertEqual(["/dashboard"], parse_qs(urlsplit(target).query)["next"])
        for referer in ("http://testserver/\\example.invalid/path", "http://external.invalid/protected", "http://[malformed"):
            request = Request({"type": "http", "method": "GET", "scheme": "http", "server": ("testserver", 80),
                               "path": "/api/protected", "query_string": b"", "headers": [(b"referer", referer.encode())]})
            self.assertEqual("/dashboard", dependencies.get_auth_redirect_target(request))
