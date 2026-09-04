import sqlite3
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers.ui_parts import auth
from classroom_app.services.student_auth_service import (
    build_password_reset_class_hint,
    matches_password_reset_class,
)


class PasswordRecoveryClassTests(unittest.TestCase):
    def test_aliases_numbers_and_departments(self):
        aliases = ["网工2601", "网工2601班", "网络工程2601", "26级网工1班", "网工2601班（专升本）"]
        for actual in aliases:
            for fragment in [*aliases, "2601", "２６０１", " 网工 ", "网络工程系", "网工2026级01班"]:
                with self.subTest(actual=actual, fragment=fragment):
                    self.assertTrue(matches_password_reset_class(fragment, actual))
        self.assertTrue(matches_password_reset_class("网络", "网络工程2601班"))
        self.assertTrue(matches_password_reset_class("电信", "电子信息工程2601班"))
        self.assertTrue(matches_password_reset_class("计算机科学", "计科2601班"))
        self.assertTrue(matches_password_reset_class("网络", "2601班", "网络工程系"))

    def test_wrong_class_and_empty_or_wildcard_fragments_are_rejected(self):
        for fragment in ["", " ", "班", "系", "专升本", "（专升本）", "%", "_", "26%", "2602", "软工", "软工2601", "网工2602", "25级网工1班", "' OR 1=1 --"]:
            with self.subTest(fragment=fragment):
                self.assertFalse(matches_password_reset_class(fragment, "网工2601班（专升本）", "网络工程系"))

    def test_hint_masks_number_and_supports_grade_style(self):
        for actual in ["网工2601班（专升本）", "26级网工1班（专升本）", "网工２６０１班(专升本)"]:
            with self.subTest(actual=actual):
                self.assertEqual(build_password_reset_class_hint(actual), {"prefix": "网工", "suffix": "班（专升本）"})
        self.assertEqual(build_password_reset_class_hint("软件工程2601班"), {"prefix": "软件工程", "suffix": "班"})
        self.assertEqual(build_password_reset_class_hint("实验班"), {"prefix": "", "suffix": ""})


class PasswordRecoveryRouteTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT, department TEXT, created_by_teacher_id INTEGER);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, student_id_number TEXT UNIQUE,
                class_id INTEGER, hashed_password TEXT, password_reset_required INTEGER, enrollment_status TEXT);
            CREATE TABLE student_password_reset_requests (
                id INTEGER PRIMARY KEY, student_id INTEGER, class_id INTEGER, teacher_id INTEGER, status TEXT,
                request_name TEXT, request_student_id_number TEXT, request_class_name TEXT,
                requester_ip TEXT, requester_user_agent TEXT, requester_device_type TEXT,
                requester_os_name TEXT, requester_browser_name TEXT, requester_device_label TEXT, submitted_at TEXT
            );
            INSERT INTO classes VALUES (1, '网工2601班（专升本）', '网络工程系', 7);
            INSERT INTO students VALUES (1, '测试学生', 'TEST2601001', 1, 'existing-hash', 0, 'active');
        """)
        self.addCleanup(self.conn.close)
        self.db_patch = patch.object(auth, "get_db_connection", side_effect=lambda: nullcontext(self.conn))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.notification_patch = patch.object(auth, "create_password_reset_request_notification")
        self.notification = self.notification_patch.start()
        self.addCleanup(self.notification_patch.stop)
        app = FastAPI()
        app.include_router(auth.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.identity = {"name": "测试学生", "student_id_number": "TEST2601001"}

    def submit(self, fragment="2601", **identity):
        return self.client.post("/api/student/password/forgot", data={**self.identity, **identity, "class_name": fragment})

    def test_partial_request_preserves_snapshot_review_and_duplicate_guard(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        row = self.conn.execute("SELECT * FROM student_password_reset_requests").fetchone()
        self.assertEqual(row["request_class_name"], "网工2601班（专升本）")
        self.assertEqual((row["status"], row["teacher_id"]), ("pending", 7))
        student = self.conn.execute("SELECT * FROM students").fetchone()
        self.assertEqual((student["hashed_password"], student["password_reset_required"]), ("existing-hash", 0))
        self.notification.assert_called_once()
        self.assertIn("等待教师审核", self.submit("网络工程").json()["detail"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM student_password_reset_requests").fetchone()[0], 1)

    def test_wrong_identity_or_class_cannot_submit(self):
        for fragment, identity in [("2602", {}), ("2601", {"name": "别人"}), ("网工", {"student_id_number": "UNKNOWN"}), ("%", {})]:
            with self.subTest(fragment=fragment, identity=identity):
                self.assertEqual(self.submit(fragment, **identity).status_code, 400)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM student_password_reset_requests").fetchone()[0], 0)
        self.notification.assert_not_called()

    def test_hint_requires_both_identity_fields_and_never_returns_number(self):
        response = self.client.post("/api/student/password/forgot/class-hint", data=self.identity)
        self.assertEqual(response.json(), {"prefix": "网工", "suffix": "班（专升本）"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        unknown = self.client.post("/api/student/password/forgot/class-hint", data={**self.identity, "name": "别人"})
        self.assertEqual(unknown.json(), {"prefix": "", "suffix": ""})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM student_password_reset_requests").fetchone()[0], 0)

    def test_existing_account_guards_still_apply(self):
        self.conn.execute("UPDATE students SET hashed_password = NULL")
        self.assertIn("尚未设置密码", self.submit().json()["detail"])
        self.conn.execute("UPDATE students SET hashed_password = 'existing-hash', enrollment_status = 'suspended'")
        self.assertEqual(self.submit().status_code, 403)


if __name__ == "__main__":
    unittest.main()
