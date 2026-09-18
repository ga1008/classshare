"""HTTP identity, selection, validation and cache boundaries for JWXT sync."""
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch, sentinel

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_user, get_current_user_optional
from classroom_app.routers.manage_parts import integrations


class AcademicScheduleSyncAPITests(unittest.TestCase):
    URL = "/api/manage/academic/course-schedule/academic-sync"

    def setUp(self):
        self.user = {"id": 73, "role": "teacher"}
        self.app = FastAPI()
        self.app.include_router(integrations.router, prefix="/api/manage")
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.sync = AsyncMock(return_value={
            "status": "success", "message": "同步完成", "year": "2026-2027",
            "term": "1", "semester_id": 55,
        })
        self.overview = Mock(return_value={
            "filters": {"selected_year": "2026-2027", "selected_term": "1"},
            "semester_id": 55,
        })

        @contextmanager
        def db_context():
            yield sentinel.connection

        self.db = Mock(side_effect=db_context)
        for target, replacement in (
            ("classroom_app.services.academic_schedule_sync_service.sync_teacher_academic_schedule", self.sync),
            ("classroom_app.routers.manage_parts.integrations.build_teacher_course_schedule_overview", self.overview),
            ("classroom_app.routers.manage_parts.integrations.get_db_connection", self.db),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_current_discovery_uses_authenticated_teacher_and_actual_returned_term(self):
        response = self.client.post(self.URL, json={"teacher_id": 999})
        self.assertEqual(response.status_code, 200)
        self.sync.assert_awaited_once_with(73, year="", term="", semester_id=None)
        self.overview.assert_called_once_with(
            sentinel.connection, 73, year="2026-2027", term="1", course="", class_label="",
        )
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.json()["overview"], self.overview.return_value)
        self.assertEqual(response.json()["result"]["semester_id"], 55)

    def test_explicit_selection_and_filters_are_trimmed_and_returned_selection_drives_overview(self):
        # The service resolves canonical year/term; the overview uses that result.
        response = self.client.post(self.URL, json={
            "teacher_id": 999, "year": " 2026 ", "term": " 第一学期 ",
            "semester_id": "55", "course": " 示例课程 ", "class_label": " 示例班 ",
        })
        self.assertEqual(response.status_code, 200)
        self.sync.assert_awaited_once_with(73, year="2026", term="第一学期", semester_id=55)
        self.overview.assert_called_once_with(
            sentinel.connection, 73, year="2026-2027", term="1",
            course="示例课程", class_label="示例班",
        )

    def test_non_teacher_roles_cannot_call_service(self):
        for role in ("student", "admin", "assistant", ""):
            with self.subTest(role=role):
                self.user = {"id": 73, "role": role}
                response = self.client.post(self.URL, json={"teacher_id": 73})
                self.assertEqual(response.status_code, 403)
        self.sync.assert_not_awaited()
        self.db.assert_not_called()

    def test_unauthenticated_request_is_rejected(self):
        del self.app.dependency_overrides[get_current_user]
        self.app.dependency_overrides[get_current_user_optional] = lambda: None
        response = self.client.post(self.URL, json={})
        self.assertEqual(response.status_code, 401)
        self.sync.assert_not_awaited()

    def test_invalid_semester_ids_fail_before_sync(self):
        invalid_ids = (True, False, 0, -1, "0", "-1", 1.5, "1.5", "abc", {}, [],
                       "²", "١", 2 ** 63, "9" * 5000)
        for value in invalid_ids:
            with self.subTest(value=repr(value)[:40]):
                response = self.client.post(self.URL, json={"semester_id": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "学期编号无效。")
        self.sync.assert_not_awaited()
        self.overview.assert_not_called()

    def test_invalid_json_body_fails_before_sync(self):
        for body in ("{", "[]", '"not an object"', "null"):
            with self.subTest(body=body):
                response = self.client.post(self.URL, content=body, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 400)
        self.sync.assert_not_awaited()

    def test_failed_and_busy_sync_do_not_build_or_read_an_overview(self):
        for status in ("failed", "busy"):
            with self.subTest(status=status):
                self.sync.return_value = {"status": status, "message": "保留已同步数据。"}
                response = self.client.post(self.URL, json={"semester_id": 55})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                payload = response.json()
                self.assertEqual(payload["status"], status)
                self.assertIsNone(payload["overview"])
                self.assertEqual(payload["result"], self.sync.return_value)
        self.db.assert_not_called()
        self.overview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
