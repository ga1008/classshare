"""Real ASGI routes/services with isolated DB and synthetic authenticated users."""
from contextlib import contextmanager
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_user
from classroom_app.routers import attendance_reports as routes
from classroom_app.services import attendance_report_service as service
from tests import test_attendance_report_service as fixtures


class AttendanceReportsRouterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AttendanceReportServiceTests(methodName="test_denominator_unknown_conflict_and_zero_are_not_zero_marks")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.current_user = self.fixture.user.copy()

        @contextmanager
        def isolated_connection():
            try:
                yield self.fixture.conn
            except Exception:
                self.fixture.conn.rollback()
                raise
            finally:
                # Read-side locks and failed commands cannot leak to next request.
                if self.fixture.conn.in_transaction:
                    self.fixture.conn.rollback()

        patcher = patch.object(routes, "get_db_connection", isolated_connection)
        patcher.start(); self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(routes.router)

        def user_session():
            if self.current_user is None:
                raise HTTPException(401, "Not authenticated")
            return self.current_user

        app.dependency_overrides[get_current_user] = user_session
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.prefix = "/api/attendance-reports"

    def test_authentication_teacher_owner_and_student_boundaries(self):
        export = self.fixture._export()
        path = f"{self.prefix}/{export['report_id']}"
        self.assertEqual(self.client.get(path).status_code, 200)
        self.current_user = {"id": 2, "role": "teacher"}
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(self.prefix).json()["total"], 0)
        self.assertEqual(self.client.get(f"{self.prefix}/jobs/{export['job_id']}").status_code, 404)
        self.current_user = {"id": 1, "role": "student"}
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post(f"{self.prefix}/source-options/refresh", json={"year": "2025-2026", "term": 2}).status_code, 403)
        self.current_user = None
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_remote_options_are_signed_and_forged_binding_is_rejected(self):
        source = self.fixture.source
        remote = {"external_account_key": source["external_account_key"], "credential_id": 50,
                  "platform_code": source["platform_code"], "school_code": "gxufl",
                  "items": [{"id": "opaque-new", "course": "合成新课程", "courseId": "SYN-NEW", "claId": "synthetic-class", "claName": "合成教学班"}]}
        with patch("classroom_app.services.smart_classroom_attendance_adapter.list_attendance_source_options", new=AsyncMock(return_value=remote)) as adapter:
            response = self.client.post(f"{self.prefix}/source-options/refresh", json={"year": "2025-2026", "term": 2, "class_offering_id": 10})
        self.assertEqual(response.status_code, 200)
        adapter.assert_awaited_once_with(1, "2025-2026", 2)
        option = response.json()["items"][0]
        self.assertTrue(option["source_token"])
        self.assertEqual(option["academic_term"], 2)
        payload = {"source_token": option["source_token"], "class_offering_id": 10}
        forged = self.client.post(f"{self.prefix}/source-bindings", json={**payload, "source_token": payload["source_token"] + "forged"})
        self.assertEqual(forged.status_code, 409)
        created = self.client.post(f"{self.prefix}/source-bindings", json=payload)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["binding"]["course_code"], "SYN-NEW")
        self.current_user = {"id": 2, "role": "teacher"}
        self.assertEqual(self.client.post(f"{self.prefix}/source-bindings", json={"source_token": option["source_token"]}).status_code, 409)

    def test_native_pdf_get_head_range_and_cross_version_404(self):
        export, _ = self.fixture._cached()
        source_path = f"{self.prefix}/{export['report_id']}/versions/{export['source_version_id']}/source.pdf"
        full = self.client.get(source_path)
        self.assertEqual(full.status_code, 200)
        self.assertTrue(full.content.startswith(b"%PDF"))
        self.assertEqual(full.headers["cache-control"], "private, no-store")
        self.assertEqual(full.headers["x-content-type-options"], "nosniff")
        head = self.client.head(source_path)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(int(head.headers["content-length"]), len(full.content))
        partial = self.client.get(source_path, headers={"Range": "bytes=0-3"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"%PDF")
        self.assertTrue(partial.headers["content-range"].startswith("bytes 0-3/"))
        second_binding = self.fixture._binding({**self.fixture.source, "remote_schedule_id": "opaque-other"})
        second = self.fixture._export(binding=second_binding)
        wrong = f"{self.prefix}/{second['report_id']}/versions/{export['source_version_id']}/source.pdf"
        self.assertEqual(self.client.get(wrong).status_code, 404)
        self.assertEqual(self.client.get(f"{self.prefix}/999999/versions/999999/source.pdf").status_code, 404)
        self.current_user = {"id": 2, "role": "teacher"}
        self.assertEqual(self.client.get(source_path, headers={"Range": "bytes=0-3"}).status_code, 404)

    def test_filters_pagination_facets_and_durable_cancel(self):
        self.fixture._cached()
        b2 = self.fixture._binding({**self.fixture.source, "remote_schedule_id": "opaque-b", "course_name": "筛选合成课程", "course_code": "SYN-2"})
        second = self.fixture._export(binding=b2)
        b3 = self.fixture._binding({**self.fixture.source, "remote_schedule_id": "opaque-year", "academic_year": "2024-2025", "course_code": "SYN-3"}, offering=None)
        self.fixture._export(binding=b3)
        listing = self.client.get(self.prefix, params={"year": "2025-2026", "page": 2, "page_size": 1, "sort": "course_asc"})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total"], 2)
        self.assertEqual(len(listing.json()["items"]), 1)
        self.assertEqual(self.client.get(self.prefix, params={"q": "筛选合成"}).json()["total"], 1)
        self.assertEqual(self.client.get(self.prefix, params={"status": "cached"}).json()["total"], 1)
        options = self.client.get(f"{self.prefix}/options", params={"year": "2025-2026", "course": "SYN-2"}).json()
        self.assertEqual(options["years"], ["2025-2026", "2024-2025"])
        self.assertEqual({item["value"] for item in options["courses"]}, {"SYN-1", "SYN-2"})
        self.assertEqual([item["value"] for item in options["teaching_classes"]], [b2["id"]])
        cancel = self.client.post(f"{self.prefix}/jobs/{second['job_id']}/cancel")
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()["job"]["status"], "cancelled")
        status = self.client.get(f"{self.prefix}/jobs/{second['job_id']}").json()["job"]
        self.assertEqual(status["status"], "cancelled")
        self.assertNotIn("payload_json", status)

    def test_matrix_paging_quality_and_cross_report_run_404(self):
        export, parsed, _ = self.fixture._parsed(unknown=True)
        run_path = f"{self.prefix}/{export['report_id']}/runs/{parsed['parse_run_id']}"
        students = self.client.get(run_path + "/students", params={"quality_state": "unknown", "page_size": 1}).json()
        self.assertEqual(students["total"], 1)
        self.assertEqual(students["items"][0]["student_number"], "000123")
        sessions = self.client.get(run_path + "/sessions", params={"page": 2, "page_size": 1}).json()
        self.assertEqual(sessions["total"], 2)
        cells = self.client.get(run_path + "/cells", params={"student_ids": students["items"][0]["id"], "session_ids": sessions["items"][0]["id"]}).json()
        self.assertEqual(cells["total"], 1)
        self.assertEqual(cells["items"][0]["column_index"], 2)
        second_binding = self.fixture._binding({**self.fixture.source, "remote_schedule_id": "opaque-second"})
        second = self.fixture._export(binding=second_binding)
        wrong = f"{self.prefix}/{second['report_id']}/runs/{parsed['parse_run_id']}/students"
        self.assertEqual(self.client.get(wrong).status_code, 404)

    def test_review_confirm_cas_delete_restore_and_historical_reads(self):
        export, parsed, _ = self.fixture._parsed(unknown=True)
        report_path = f"{self.prefix}/{export['report_id']}"
        detail = self.client.get(report_path).json()
        run = detail["runs"][0]
        run_path = report_path + f"/runs/{run['id']}"
        cells = self.client.get(run_path + "/cells").json()["items"]
        cell = next(c for c in cells if c["normalized_status"] == "UNKNOWN")
        change = {"target_type": "cell", "target_id": cell["id"], "changes": {"normalized_status": "CHECKED"}, "reason": "合成测试逐格核验", "expected_revision": run["revision"]}
        reviewed = self.client.patch(run_path + "/review", json=change)
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(self.client.patch(run_path + "/review", json=change).status_code, 409)
        confirmed = self.client.post(run_path + "/confirm", json={"expected_run_revision": reviewed.json()["run"]["revision"], "expected_report_revision": detail["report"]["revision"]})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["run"]["state"], "confirmed")
        selection = self.client.post(f"{self.prefix}/source-bindings/{detail['binding']['id']}/grade-source", json={"expected_revision": detail["binding"]["revision"]})
        self.assertEqual(selection.status_code, 200)
        self.assertTrue(selection.json()["binding"]["is_grade_source"])
        revisions = {"expected_revision": confirmed.json()["report_revision"]}
        deleted = self.client.request("DELETE", report_path, json=revisions)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(self.prefix).json()["total"], 0)
        self.assertEqual(self.client.get(self.prefix, params={"deleted": 1}).json()["total"], 1)
        historical = self.client.get(report_path).json()
        self.assertTrue(historical["report"]["deleted_at"])
        self.assertEqual(self.client.get(run_path + "/students").status_code, 200)
        self.assertEqual(self.client.get(report_path + f"/versions/{export['source_version_id']}/source.pdf").status_code, 200)
        self.assertNotEqual(self.client.patch(run_path + "/review", json=change).status_code, 200)
        restored = self.client.post(report_path + "/restore", json={"expected_revision": historical["report"]["revision"]})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(self.client.get(self.prefix).json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
