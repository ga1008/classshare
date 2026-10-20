"""HTTP authentication and read/sync intent cannot be supplied by the caller."""
from contextlib import contextmanager
import unittest
from unittest.mock import AsyncMock, Mock, patch, sentinel

from fastapi import FastAPI
from fastapi.testclient import TestClient
from classroom_app.dependencies import get_current_user, get_current_user_optional
from classroom_app.routers.manage_parts import academic_teachers as api
from classroom_app.services.academic_teacher_timetable_service import TimetableQueryError


class TeacherTimetableAPITests(unittest.TestCase):
    def setUp(self):
        self.user = {"id": 73, "role": "teacher"}
        self.app = FastAPI()
        self.app.include_router(api.router, prefix="/api/manage")
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.query = AsyncMock(return_value={"status": "success", "items": []})
        self.detail = Mock(return_value={"status": "success", "overview": {}})
        @contextmanager
        def db():
            yield sentinel.conn
        for name, replacement in [("query_teacher_timetables", self.query),
                                  ("load_teacher_timetable", self.detail), ("get_db_connection", db)]:
            p = patch.object(api, name, replacement)
            p.start()
            self.addCleanup(p.stop)

    def test_query_and_sync_keep_server_controlled_intent_and_actor(self):
        for endpoint, expected in [("query", False), ("sync", True)]:
            body = {"teacher_id": 999, "q": "教师", "force_sync": not expected}
            response = self.client.post("/api/manage/academic/teachers/" + endpoint, json=body)
            self.assertEqual(200, response.status_code)
            self.assertEqual("private, no-store", response.headers["cache-control"])
            self.query.assert_awaited_with(73, body, force_sync=expected)

    def test_student_and_anonymous_are_rejected(self):
        self.user = {"id": 2, "role": "student"}
        self.assertEqual(403, self.client.post("/api/manage/academic/teachers/query", json={}).status_code)
        self.assertEqual(403, self.client.get("/api/manage/academic/teachers/000123/timetable?year=2026-2027&term=1").status_code)
        del self.app.dependency_overrides[get_current_user]
        self.app.dependency_overrides[get_current_user_optional] = lambda: None
        self.assertEqual(401, self.client.post("/api/manage/academic/teachers/query", json={}).status_code)
        self.query.assert_not_awaited()
        self.detail.assert_not_called()

    def test_detail_uses_authenticated_actor_and_keeps_employee_number_text(self):
        response = self.client.get("/api/manage/academic/teachers/000123/timetable?year=2026-2027&term=1&teacher_id=999")
        self.assertEqual(200, response.status_code)
        self.detail.assert_called_once_with(sentinel.conn, 73, "000123", year="2026-2027", term="1")

    def test_expected_errors_preserve_status_and_safe_message(self):
        for status, code in [("forbidden", 403), ("busy", 409), ("failed", 502)]:
            self.query.side_effect = TimetableQueryError("安全错误消息", status=status, http_status=code)
            response = self.client.post("/api/manage/academic/teachers/query", json={})
            self.assertEqual(code, response.status_code)
            self.assertEqual(status, response.json()["status"])
            self.assertEqual("安全错误消息", response.json()["message"])

    def test_invalid_json_is_rejected_before_service(self):
        response = self.client.post("/api/manage/academic/teachers/query", content="{",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(400, response.status_code)
        self.query.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
