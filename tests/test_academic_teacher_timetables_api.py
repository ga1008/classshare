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
        self.single_sync = AsyncMock(return_value={"status": "success", "teacher": {"teacher_no": "000123"}, "overview": {}})
        self.detail = Mock(return_value={"status": "success", "overview": {}})
        @contextmanager
        def db():
            yield sentinel.conn
        for name, replacement in [("query_teacher_timetables", self.query),
                                  ("sync_teacher_timetable", self.single_sync),
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
        self.assertEqual(403, self.client.post("/api/manage/academic/teachers/000123/sync", json={}).status_code)
        del self.app.dependency_overrides[get_current_user]
        self.app.dependency_overrides[get_current_user_optional] = lambda: None
        self.assertEqual(401, self.client.post("/api/manage/academic/teachers/query", json={}).status_code)
        self.assertEqual(401, self.client.post("/api/manage/academic/teachers/000123/sync", json={}).status_code)
        self.query.assert_not_awaited()
        self.single_sync.assert_not_awaited()
        self.detail.assert_not_called()

    def test_detail_uses_authenticated_actor_and_keeps_employee_number_text(self):
        response = self.client.get("/api/manage/academic/teachers/000123/timetable?year=2026-2027&term=1&teacher_id=999")
        self.assertEqual(200, response.status_code)
        self.detail.assert_called_once_with(sentinel.conn, 73, "000123", year="2026-2027", term="1")

    def test_employee_number_with_slash_preserves_exact_identity_and_static_routes(self):
        response = self.client.get("/api/manage/academic/teachers/00%2F0123/timetable?year=2026-2027&term=1")
        self.assertEqual(200, response.status_code)
        self.detail.assert_called_once_with(sentinel.conn, 73, "00/0123", year="2026-2027", term="1")
        body = {"year": "2026-2027", "term": "1"}
        response = self.client.post("/api/manage/academic/teachers/00%2F0123/sync", json=body)
        self.assertEqual(200, response.status_code)
        self.single_sync.assert_awaited_once_with(73, "00/0123", body)
        response = self.client.post("/api/manage/academic/teachers/query", json={"q": "00/0123"})
        self.assertEqual(200, response.status_code)
        self.query.assert_awaited_once_with(73, {"q": "00/0123"}, force_sync=False)

    def test_single_sync_keeps_actor_employee_number_and_safe_errors(self):
        body = {"year": "2026-2027", "term": "1", "teacher_id": 999, "teacher_no": "000456"}
        response = self.client.post("/api/manage/academic/teachers/000123/sync", json=body)
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers["cache-control"])
        self.single_sync.assert_awaited_once_with(73, "000123", body)
        self.single_sync.side_effect = TimetableQueryError("当前账号正在同步。", status="busy", http_status=409)
        response = self.client.post("/api/manage/academic/teachers/000123/sync", json=body)
        self.assertEqual(409, response.status_code)
        self.assertEqual("private, no-store", response.headers["cache-control"])

    def test_expected_errors_preserve_status_and_safe_message(self):
        for status, code in [("forbidden", 403), ("busy", 409), ("failed", 502)]:
            self.query.side_effect = TimetableQueryError("安全错误消息", status=status, http_status=code)
            response = self.client.post("/api/manage/academic/teachers/query", json={})
            self.assertEqual(code, response.status_code)
            self.assertEqual(status, response.json()["status"])
            self.assertEqual("安全错误消息", response.json()["message"])

    def test_invalid_json_is_rejected_before_service(self):
        for endpoint in ("query", "000123/sync"):
            response = self.client.post("/api/manage/academic/teachers/" + endpoint, content="{",
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(400, response.status_code)
        self.query.assert_not_awaited()
        self.single_sync.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
