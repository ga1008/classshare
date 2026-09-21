"""Native recovery adapters with real templates and an owned in-memory fixture.

Run through tools/test_backend.py. No lifespan, socket, external DB or mail.
The first-package fixture provides strict missed-connector guards; its tests
remain in their original module and are not rerun through this subclass.
"""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from jose import jwt

from tests import test_auth_form_fallback as base
from classroom_app import dependencies
from classroom_app.routers.ui_parts import auth
from classroom_app.services import account_credentials_service as credentials


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.items = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))

    def tagged(self, tag):
        return [attrs for name, attrs in self.items if name == tag]


class NativeAuthFlowTests(base.AuthFormFallbackTests):
    next_url = "/protected?tab=files&item=2"

    def setUp(self):
        super().setUp()
        self.conn.executescript("""
            ALTER TABLE classes ADD COLUMN department TEXT;
            UPDATE classes SET name='网工2601班',department='网络工程系';
            ALTER TABLE students ADD COLUMN password_updated_at TEXT;
            CREATE TABLE student_password_reset_requests (
                id INTEGER PRIMARY KEY, student_id INTEGER, class_id INTEGER, teacher_id INTEGER, status TEXT,
                request_name TEXT, request_student_id_number TEXT, request_class_name TEXT,
                requester_ip TEXT, requester_user_agent TEXT, requester_device_type TEXT,
                requester_os_name TEXT, requester_browser_name TEXT, requester_device_label TEXT,
                submitted_at TEXT, reviewed_at TEXT, completed_at TEXT);
            CREATE TABLE agent_tasks(id INTEGER PRIMARY KEY,actor_role TEXT,actor_id INTEGER,status TEXT);
            CREATE TABLE agent_task_delegations(actor_role TEXT,actor_id INTEGER,status TEXT,revoked_at INTEGER,revoke_reason TEXT);
            CREATE TABLE agent_persistent_authorizations(actor_role TEXT,actor_id INTEGER,status TEXT,revoked_at INTEGER,revoke_reason TEXT);
            INSERT INTO agent_task_delegations(actor_role,actor_id,status) VALUES('student',7,'active'),('teacher',7,'active');
            INSERT INTO agent_persistent_authorizations(actor_role,actor_id,status) VALUES('student',7,'active'),('teacher',7,'active');
        """)
        self.notification = self.stack.enter_context(patch.object(auth, "create_password_reset_request_notification"))
        self.prepare = self.stack.enter_context(patch.object(credentials, "prepare_credentials_change", wraps=credentials.prepare_credentials_change))
        self.identity = {"name": "Synthetic student", "student_id_number": "S7", "next": self.next_url}

    def unset_password(self):
        self.conn.execute("UPDATE students SET hashed_password=NULL")
        self.conn.commit()

    def identity_form(self):
        response = self.client.post("/student/login/identity", data=self.identity)
        self.assertEqual(200, response.status_code)
        self.assertEqual("setup", response.context["auth_step"])
        return response

    def setup_form(self, token, **overrides):
        return self.client.post("/student/password/setup", data={
            "setup_token": token, "password": self.password, "confirm_password": self.password, **overrides,
        })

    def assert_native(self, response, step, status=200):
        self.assertEqual(status, response.status_code)
        self.assertEqual("student_auth_flow_v4.html", response.template.name)
        self.assertEqual(step, response.context["auth_step"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual("no-referrer", response.headers["referrer-policy"])
        self.assertNotIn(self.password, response.text)
        self.assertNotIn("password", response.context)
        self.assertNotIn("confirm_password", response.context)
        self.assertTrue(all(not node.get("value") for node in Elements(response.text).tagged("input") if node.get("type") == "password"))
        self.assertNotIn("access_token=", response.headers.get("set-cookie", ""))

    def assert_unmodified_credentials(self):
        self.assertEqual(0, self.count("student_login_audit_logs"))
        self.assertEqual(0, self.count("user_sessions"))
        self.assertTrue(all(row[0] == "active" for row in self.conn.execute("SELECT status FROM agent_task_delegations")))

    def test_flow_gets_render_real_native_forms_for_both_families_without_authentication(self):
        for enabled in (False, True):
            with patch.dict(auth.templates.env.globals, {"lq_family_enabled": lambda _: enabled}):
                for path, step in (("/student/login/identity", "identity"), ("/student/password/forgot", "forgot")):
                    response = self.client.get(path, params={"next": self.next_url, "setup_token": "untrusted", "success": "true"})
                    self.assert_native(response, step)
                    self.assertEqual(self.next_url, response.context["next_url"])
                    self.assertNotIn("untrusted", response.text)
                    nodes = Elements(response.text)
                    self.assertEqual([{"action": path, "method": "post"}], nodes.tagged("form"))
                    self.assertFalse(any("student_login.js" in node.get("src", "") for node in nodes.tagged("script")))
                    self.assertEqual(enabled, "lq-login-card" in response.text)
        self.assert_unmodified_credentials()

    def test_login_has_real_fallback_links_and_post_actions_with_safe_next(self):
        response = self.client.get("/student/login", params={"next": self.next_url})
        nodes = Elements(response.text)
        links = {node.get("id"): node for node in nodes.tagged("a")}
        for key, path in (("first-login-switch", "/student/login/identity"), ("forgot-password-trigger", "/student/password/forgot")):
            target = urlsplit(links[key]["href"])
            self.assertEqual(path, target.path)
            self.assertEqual([self.next_url], parse_qs(target.query)["next"])
        forms = {node["id"]: node for node in nodes.tagged("form")}
        for key, path in (("student-identity-login-form", "/student/login/identity"), ("student-password-setup-form", "/student/password/setup"), ("student-forgot-password-form", "/student/password/forgot")):
            self.assertEqual((path, "post"), (forms[key]["action"], forms[key]["method"]))

    def test_identity_missing_wrong_inactive_and_already_set_preserve_status_and_input(self):
        for fields, status in (({}, 400), ({**self.identity, "name": '<img src=x onerror="bad">'}, 400), (self.identity, 409)):
            response = self.client.post("/student/login/identity", data=fields)
            self.assert_native(response, "identity", status)
            self.assertEqual(fields.get("name", ""), response.context["auth_name"])
            self.assertNotIn("<img src=x", response.text)
        self.unset_password()
        self.conn.execute("UPDATE students SET enrollment_status='suspended'")
        self.conn.commit()
        self.assert_native(self.client.post("/student/login/identity", data=self.identity), "identity", 403)
        self.assert_unmodified_credentials()

    def test_json_identity_keeps_shape_and_shares_native_orchestration(self):
        self.unset_password()
        with patch.object(auth, "_perform_student_identity_login", wraps=auth._perform_student_identity_login) as perform:
            html = self.identity_form()
            api = self.client.post("/api/student/login/identity", data=self.identity)
            self.assertEqual(2, perform.call_count)
        self.assertEqual({"status", "message", "setup_token", "flow_type", "password_policy_hint", "student"}, set(api.json()))
        self.assertEqual(api.json()["student"], html.context["auth_student"])
        self.assertEqual("no-store", api.headers["cache-control"])
        self.assert_unmodified_credentials()

    def test_first_setup_redirects_once_and_replay_cannot_write_or_create_another_session(self):
        self.unset_password()
        identity = self.identity_form()
        token = identity.context["setup_token"]
        self.assertEqual(self.next_url, auth.decode_password_setup_token(token)["next"])
        response = self.setup_form(token)
        self.assertEqual(303, response.status_code)
        self.assertEqual(self.next_url, response.headers["location"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("cultivation_reveal=1", response.headers["set-cookie"])
        self.assertEqual(1, self.count("student_login_audit_logs"))
        self.assertEqual(1, self.session_save.call_count)
        self.prepare.assert_called_once_with(self.conn, role="student", user_id=7)
        for table in ("agent_task_delegations", "agent_persistent_authorizations"):
            self.assertEqual([("student", "revoked"), ("teacher", "active")], [tuple(row) for row in self.conn.execute(f"SELECT actor_role,status FROM {table}")])
        self.assertTrue(dependencies.verify_password(self.password, self.conn.execute("SELECT hashed_password FROM students").fetchone()[0]))
        self.assertEqual("first_time_setup", self.conn.execute("SELECT login_method FROM student_login_audit_logs").fetchone()[0])
        replay = self.setup_form(token)
        self.assert_native(replay, "identity", 400)
        self.assertNotIn(token, replay.text)
        self.assertEqual(1, self.session_save.call_count)
        self.assertEqual(1, self.count("student_login_audit_logs"))

    def test_json_setup_preserves_response_and_uses_shared_credential_workflow(self):
        self.unset_password()
        token = self.identity_form().context["setup_token"]
        with patch.object(auth, "_perform_student_password_setup", wraps=auth._perform_student_password_setup) as perform:
            response = self.client.post("/api/student/password/setup", data={"setup_token": token, "password": self.password, "confirm_password": self.password})
            perform.assert_called_once()
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status", "message", "redirect_to", "login_count", "cultivation_profile", "login_tip"}, set(response.json()))
        self.assertEqual(self.next_url, response.json()["redirect_to"])
        self.assertEqual(1, response.json()["login_count"])
        self.assertEqual(1, self.session_save.call_count)
        self.prepare.assert_called_once()

    def test_setup_input_errors_keep_verified_post_token_but_never_password_values(self):
        self.unset_password()
        token = self.identity_form().context["setup_token"]
        for fields in ({"password": "", "confirm_password": ""}, {"password": "short", "confirm_password": "short"}, {"confirm_password": "different"}):
            response = self.setup_form(token, **fields)
            self.assert_native(response, "setup", 400)
            self.assertEqual(token, response.context["setup_token"])
            self.assertEqual(self.next_url, response.context["next_url"])
            self.assertFalse(any(token in node.get("href", "") for node in Elements(response.text).tagged("a")))
        self.prepare.assert_not_called()
        self.assert_unmodified_credentials()

    def test_invalid_expired_wrong_purpose_and_missing_token_return_to_identity(self):
        self.unset_password()
        self.assert_native(self.client.post("/student/password/setup", data={}), "identity", 400)
        expired = jwt.encode({"purpose": "student_password_setup", "student_id": 7, "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, dependencies.SECRET_KEY, algorithm=dependencies.ALGORITHM)
        wrong = jwt.encode({"purpose": "access", "student_id": 7}, dependencies.SECRET_KEY, algorithm=dependencies.ALGORITHM)
        for token in ("", "forged", expired, wrong):
            response = self.setup_form(token, next="//outside.invalid/path")
            self.assert_native(response, "identity", 400)
            self.assertEqual("", response.context["setup_token"])
            self.assertEqual("/dashboard", response.context["next_url"])
        self.assert_unmodified_credentials()

    def test_account_state_changed_after_identity_is_rechecked_under_credentials_lock(self):
        self.unset_password()
        token = self.identity_form().context["setup_token"]
        self.conn.execute("UPDATE students SET enrollment_status='suspended'")
        self.conn.commit()
        self.assert_native(self.setup_form(token), "identity", 403)
        self.prepare.assert_called_once()
        self.assert_unmodified_credentials()
        self.conn.execute("UPDATE students SET enrollment_status='active',hashed_password=?", (self.hashed_password,))
        self.conn.commit()
        self.assert_native(self.setup_form(token), "identity", 400)
        self.assertEqual(2, self.prepare.call_count)
        self.assert_unmodified_credentials()

    def test_forgot_native_submission_keeps_review_snapshot_and_duplicate_notification_guard(self):
        payload = {**self.identity, "class_name": "2601"}
        with patch.object(auth, "_perform_student_password_forgot", wraps=auth._perform_student_password_forgot) as perform:
            response = self.client.post("/student/password/forgot", data=payload)
            self.assert_native(response, "submitted")
            self.assertEqual([], Elements(response.text).tagged("form"))
            duplicate = self.client.post("/api/student/password/forgot", data=payload)
            self.assertEqual(400, duplicate.status_code)
            self.assertEqual({"detail", "error", "code"}, set(duplicate.json()))
            self.assertIn("等待教师审核", duplicate.json()["detail"])
            self.assertEqual(2, perform.call_count)
        row = self.conn.execute("SELECT * FROM student_password_reset_requests").fetchone()
        self.assertEqual(("pending", "网工2601班", 7), (row["status"], row["request_class_name"], row["teacher_id"]))
        self.notification.assert_called_once_with(self.conn, row["id"])
        self.assertEqual(1, self.count("student_password_reset_requests"))
        self.assertEqual(self.hashed_password, self.conn.execute("SELECT hashed_password FROM students").fetchone()[0])
        self.assert_native(self.client.post("/student/login/identity", data=self.identity), "identity", 409)
        self.assert_unmodified_credentials()

    def test_forgot_missing_invalid_long_and_inactive_fields_restore_safe_form(self):
        for fields in ({}, {**self.identity, "class_name": "2602"}, {**self.identity, "class_name": "x" * 101}, {**self.identity, "class_name": '<img src=x onerror="bad">'}):
            response = self.client.post("/student/password/forgot", data=fields)
            self.assert_native(response, "forgot", 400)
            self.assertEqual(fields.get("class_name", ""), response.context["auth_class_name"])
            self.assertNotIn("<img src=x", response.text)
        self.conn.execute("UPDATE students SET enrollment_status='suspended'")
        self.conn.commit()
        self.assert_native(self.client.post("/student/password/forgot", data={**self.identity, "class_name": "2601"}), "forgot", 403)
        self.assertEqual(0, self.count("student_password_reset_requests"))
        self.notification.assert_not_called()

    def test_unset_account_uses_identity_flow_and_unexpected_errors_are_not_masked(self):
        self.unset_password()
        response = self.client.post("/student/password/forgot", data={**self.identity, "class_name": "2601"})
        self.assert_native(response, "forgot", 400)
        self.assertIn("尚未设置密码", response.context["auth_error"])
        self.notification.assert_not_called()
        with patch.object(auth, "_perform_student_identity_login", side_effect=RuntimeError("Synthetic DB unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic DB unavailable"):
                self.client.post("/student/login/identity", data=self.identity)
        self.assert_unmodified_credentials()

    def test_native_approved_reset_completes_original_request_and_revokes_old_session(self):
        self.post_student()
        previous_session = self.conn.execute("SELECT session_id FROM user_sessions").fetchone()[0]
        self.conn.execute("INSERT INTO student_password_reset_requests(id,student_id,status,reviewed_at) VALUES(9,7,'approved','2026-09-21')")
        self.conn.execute("UPDATE students SET password_reset_required=1")
        self.conn.commit()
        duplicate = self.client.post("/student/password/forgot", data={**self.identity, "class_name": "2601"})
        self.assert_native(duplicate, "forgot", 400)
        self.assertIn("教师已通过", duplicate.context["auth_error"])
        self.notification.assert_not_called()
        token = self.identity_form().context["setup_token"]
        self.assertEqual(9, auth.decode_password_setup_token(token)["reset_request_id"])
        response = self.setup_form(token)
        self.assertEqual(303, response.status_code)
        self.assertEqual("completed", self.conn.execute("SELECT status FROM student_password_reset_requests").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT password_reset_required FROM students").fetchone()[0])
        self.assertNotEqual(previous_session, self.conn.execute("SELECT session_id FROM user_sessions").fetchone()[0])
        self.assertEqual("password_reset_setup", self.conn.execute("SELECT login_method FROM student_login_audit_logs ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(2, self.session_save.call_count)

    def test_notification_failure_rolls_back_request_and_is_not_a_fake_success(self):
        self.notification.side_effect = RuntimeError("Synthetic notification unavailable")
        with self.assertRaisesRegex(RuntimeError, "Synthetic notification unavailable"):
            self.client.post("/student/password/forgot", data={**self.identity, "class_name": "2601"})
        self.assertEqual(0, self.count("student_password_reset_requests"))
        self.assert_unmodified_credentials()

    def test_setup_audit_failure_rolls_back_password_and_revocation_without_session(self):
        self.unset_password()
        token = self.identity_form().context["setup_token"]
        with patch.object(auth, "record_student_login", side_effect=RuntimeError("Synthetic audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic audit unavailable"):
                self.setup_form(token)
        self.assertIsNone(self.conn.execute("SELECT hashed_password FROM students").fetchone()[0])
        self.assert_unmodified_credentials()

    def test_new_auth_paths_are_excluded_from_safe_return_paths(self):
        for path in ("/student/login/identity", "/student/password/setup", "/student/password/forgot"):
            for suffix in ("", "/", "/?next=/protected"):
                self.assertEqual("/dashboard", dependencies.sanitize_next_path(path + suffix))

    def test_native_heading_and_autofocus_are_unique_for_each_ssr_state_and_family(self):
        self.unset_password()
        for enabled in (False, True):
            with patch.dict(auth.templates.env.globals, {"lq_family_enabled": lambda _: enabled}):
                token = self.identity_form().context["setup_token"]
                responses = [
                    (self.client.get("/student/login/identity"), "auth-name"),
                    (self.client.get("/student/password/forgot"), "auth-name"),
                    (self.identity_form(), "auth-password"),
                    (self.client.post("/student/login/identity", data={}), "auth-flow-feedback"),
                    (self.client.post("/student/password/forgot", data={}), "auth-flow-feedback"),
                    (self.setup_form(token, password="short", confirm_password="short"), "auth-flow-feedback"),
                ]
                for response, focus_id in responses:
                    nodes = Elements(response.text)
                    self.assertEqual(1, len(nodes.tagged("h1")))
                    self.assertEqual([focus_id], [node.get("id") for _, node in nodes.items if "autofocus" in node])
                    if focus_id == "auth-flow-feedback":
                        feedback = next(node for _, node in nodes.items if node.get("id") == focus_id)
                        self.assertEqual(("alert", "-1"), (feedback["role"], feedback["tabindex"]))
                        self.assertEqual(focus_id, nodes.tagged("form")[0]["aria-describedby"])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(NativeAuthFlowTests(name) for name in NativeAuthFlowTests.__dict__ if name.startswith("test_"))
