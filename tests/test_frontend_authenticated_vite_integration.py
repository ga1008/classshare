import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.database import get_db_connection
from classroom_app.dependencies import get_current_user
from classroom_app.frontend_assets import VITE_MANIFEST_PATH
from classroom_app.routers import ui as ui_router
from classroom_app.routers.ui_parts import assignment_pages as ui_assignment_pages
from classroom_app.routers.ui_parts import classroom as ui_classroom


REQUIRED_AUTHENTICATED_ISLANDS = (
    "frontend/src/islands/app-shell.tsx",
    "frontend/src/islands/blog-topbar-sync.tsx",
    "frontend/src/islands/message-center-sync.tsx",
    "frontend/src/islands/student-security-sync.tsx",
)

REQUIRED_PAGE_ISLANDS = (
    "frontend/src/islands/assignment-authoring-sync.tsx",
    "frontend/src/islands/assignment-submit-sync.tsx",
    "frontend/src/islands/assignment-task-board-sync.tsx",
    "frontend/src/islands/classroom-page.tsx",
    "frontend/src/islands/classroom-activity-workspace-sync.tsx",
    "frontend/src/islands/exam-assign-sync.tsx",
    "frontend/src/islands/learning-progress-sync.tsx",
    "frontend/src/islands/material-learning-path-sync.tsx",
    "frontend/src/islands/materials-manage-page.tsx",
    "frontend/src/islands/message-center-page.tsx",
    "frontend/src/islands/message-center-workspace-sync.tsx",
    "frontend/src/islands/resource-workspace-sync.tsx",
    "frontend/src/islands/submission-jump-nav.tsx",
)


def _load_first_active_teacher() -> dict | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, email, nickname, is_super_admin
            FROM teachers
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY COALESCE(is_super_admin, 0) DESC, id ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "email": str(row["email"] or ""),
        "nickname": str(row["nickname"] or ""),
        "role": "teacher",
        "is_super_admin": int(row["is_super_admin"] or 0),
    }


def _load_student_assignment_form_fixture() -> tuple[dict, str] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                a.id AS assignment_id,
                s.id AS student_pk_id,
                s.name AS student_name,
                s.student_id_number,
                s.class_id
            FROM assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN students s ON s.class_id = o.class_id
            WHERE a.status = 'published'
              AND (a.exam_paper_id IS NULL OR a.exam_paper_id = '')
              AND COALESCE(s.enrollment_status, 'active') = 'active'
              AND NOT EXISTS (
                  SELECT 1
                  FROM submissions sub
                  WHERE sub.assignment_id = a.id
                    AND sub.student_pk_id = s.id
                    AND COALESCE(sub.is_absence_score, 0) = 0
              )
            ORDER BY a.created_at DESC, s.id ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    student = {
        "id": int(row["student_pk_id"]),
        "name": str(row["student_name"] or ""),
        "username": str(row["student_id_number"] or row["student_pk_id"]),
        "student_id_number": str(row["student_id_number"] or ""),
        "class_id": int(row["class_id"] or 0),
        "role": "student",
    }
    return student, str(row["assignment_id"])


def _load_teacher_submission_detail_fixture() -> tuple[dict, int] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                s.id AS submission_id,
                t.id AS teacher_id,
                t.name,
                t.email,
                t.nickname,
                t.is_super_admin
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN teachers t ON t.id = COALESCE(o.teacher_id, c.created_by_teacher_id)
            WHERE COALESCE(s.is_absence_score, 0) = 0
              AND COALESCE(s.answers_json, '') <> ''
              AND NOT EXISTS (
                  SELECT 1
                  FROM learning_stage_exam_attempts lsea
                  WHERE lsea.assignment_id = a.id
              )
            ORDER BY s.id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    teacher = {
        "id": int(row["teacher_id"]),
        "name": str(row["name"] or ""),
        "email": str(row["email"] or ""),
        "nickname": str(row["nickname"] or ""),
        "role": "teacher",
        "is_super_admin": int(row["is_super_admin"] or 0),
    }
    return teacher, int(row["submission_id"])


def _load_teacher_assignment_workbench_fixture() -> tuple[dict, str] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                a.id AS assignment_id,
                t.id AS teacher_id,
                t.name,
                t.email,
                t.nickname,
                t.is_super_admin
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN teachers t ON t.id = COALESCE(o.teacher_id, c.created_by_teacher_id)
            WHERE NOT EXISTS (
                SELECT 1
                FROM learning_stage_exam_attempts lsea
                WHERE lsea.assignment_id = a.id
            )
            ORDER BY a.created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    teacher = {
        "id": int(row["teacher_id"]),
        "name": str(row["name"] or ""),
        "email": str(row["email"] or ""),
        "nickname": str(row["nickname"] or ""),
        "role": "teacher",
        "is_super_admin": int(row["is_super_admin"] or 0),
    }
    return teacher, str(row["assignment_id"])


def _load_teacher_classroom_fixture() -> tuple[dict, int] | None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                o.id AS class_offering_id,
                t.id AS teacher_id,
                t.name,
                t.email,
                t.nickname,
                t.is_super_admin
            FROM class_offerings o
            JOIN teachers t ON t.id = o.teacher_id
            WHERE COALESCE(t.is_active, 1) = 1
            ORDER BY o.id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    teacher = {
        "id": int(row["teacher_id"]),
        "name": str(row["name"] or ""),
        "email": str(row["email"] or ""),
        "nickname": str(row["nickname"] or ""),
        "role": "teacher",
        "is_super_admin": int(row["is_super_admin"] or 0),
    }
    return teacher, int(row["class_offering_id"])


@contextmanager
def _authenticated_client(user: dict):
    previous_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        if previous_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous_override


class AuthenticatedViteIslandIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not VITE_MANIFEST_PATH.is_file():
            self.skipTest("Vite manifest is missing; run npm run build before authenticated frontend integration tests.")
        self.manifest = json.loads(VITE_MANIFEST_PATH.read_text(encoding="utf-8"))
        missing_entries = [entry for entry in REQUIRED_AUTHENTICATED_ISLANDS if entry not in self.manifest]
        if missing_entries:
            self.fail(f"Vite manifest is missing authenticated island entries: {missing_entries}")
        missing_page_entries = [entry for entry in REQUIRED_PAGE_ISLANDS if entry not in self.manifest]
        if missing_page_entries:
            self.fail(f"Vite manifest is missing page island entries: {missing_page_entries}")

        self.teacher = _load_first_active_teacher()
        if self.teacher is None:
            self.skipTest("No active teacher is available for authenticated frontend integration smoke test.")

    def test_dashboard_injects_authenticated_vite_islands_and_status_endpoints_work(self):
        with _authenticated_client(self.teacher) as client:
            dashboard_response = client.get("/dashboard", follow_redirects=False)
            self.assertEqual(200, dashboard_response.status_code)
            html = dashboard_response.text

            self.assertIn('data-lanshare-island="app-shell"', html)
            self.assertIn('data-lanshare-island="message-center-sync"', html)
            self.assertIn('data-lanshare-island="blog-topbar-sync"', html)
            self.assertIn('data-lanshare-island="student-security-sync"', html)
            self.assertIn("message-center-sync", html)
            self.assertIn("blog-topbar-sync", html)
            self.assertIn("student-security-sync", html)
            self.assertIn("/static/js/message_center_bell.js", html)
            self.assertNotIn("login_required", html)

            blog_summary = client.get("/api/blog/summary")
            self.assertEqual(200, blog_summary.status_code)
            self.assertIn("today_new_count", blog_summary.json()["summary"])

            message_summary = client.get("/api/message-center/summary")
            self.assertEqual(200, message_summary.status_code)
            payload = message_summary.json()
            self.assertIn("unread_total", payload["summary"])
            self.assertIn("latest_unread", payload)

    def test_student_assignment_form_injects_submit_sync_without_removing_legacy_submission_flow(self):
        fixture = _load_student_assignment_form_fixture()
        if fixture is None:
            self.skipTest("No published unsubmitted non-exam assignment is available for student submit island smoke test.")
        student, assignment_id = fixture

        with (
            patch.object(ui_router, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_assignment_pages, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_router, "record_behavior_event", lambda *args, **kwargs: None),
            patch.object(ui_assignment_pages, "record_behavior_event", lambda *args, **kwargs: None),
            _authenticated_client(student) as client,
        ):
            response = client.get(f"/assignment/{assignment_id}", follow_redirects=False)

        self.assertEqual(200, response.status_code)
        html = response.text
        self.assertIn('data-lanshare-island="assignment-submit-sync"', html)
        self.assertIn("assignment-submit-sync", html)
        self.assertIn("/static/js/submission_upload.js", html)
        self.assertIn("fetch(`/api/assignments/${ASSIGNMENT_ID}/submit`", html)
        self.assertIn("fetch(`/api/assignments/${ASSIGNMENT_ID}/withdraw`", html)

    def test_submission_detail_injects_jump_nav_island_and_keeps_review_actions(self):
        fixture = _load_teacher_submission_detail_fixture()
        if fixture is None:
            self.skipTest("No teacher-accessible submission with answers is available for submission detail island smoke test.")
        teacher, submission_id = fixture

        with (
            patch.object(ui_router, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_assignment_pages, "close_overdue_assignments", lambda conn: 0),
            _authenticated_client(teacher) as client,
        ):
            response = client.get(f"/submission/{submission_id}", follow_redirects=False)

        self.assertEqual(200, response.status_code)
        html = response.text
        self.assertIn('data-lanshare-island="submission-jump-nav"', html)
        self.assertIn("data-submission-jump-nav-payload", html)
        self.assertIn("submission-jump-nav", html)
        self.assertIn("renderAnswers()", html)
        self.assertIn("initSubmissionPreview()", html)
        self.assertIn("fetch(`/api/submissions/${submissionId}/grade`", html)
        self.assertIn("fetch(`/api/submissions/${submissionId}/regrade`", html)

    def test_teacher_assignment_detail_keeps_bulk_actions_without_duplicate_workbench(self):
        fixture = _load_teacher_assignment_workbench_fixture()
        if fixture is None:
            self.skipTest("No teacher-accessible assignment is available for teacher workbench smoke test.")
        teacher, assignment_id = fixture

        with (
            patch.object(ui_router, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_assignment_pages, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_router, "record_behavior_event", lambda *args, **kwargs: None),
            patch.object(ui_assignment_pages, "record_behavior_event", lambda *args, **kwargs: None),
            _authenticated_client(teacher) as client,
        ):
            response = client.get(f"/assignment/{assignment_id}", follow_redirects=False)

        self.assertEqual(200, response.status_code)
        html = response.text
        self.assertNotIn('data-lanshare-island="teacher-submission-workbench-sync"', html)
        self.assertNotIn("teacher-submission-workbench-sync", html)
        self.assertNotIn("lanshare:teacher-submission-workbench-change", html)
        self.assertIn("window.refreshSubmissions = async function()", html)
        self.assertIn("window.aiGradeAll = async function()", html)
        self.assertIn("window.zeroUnsubmittedScores = async function()", html)
        self.assertIn("window.openWithdrawModalForSelected = function()", html)

    def test_classroom_main_keeps_floating_nav_without_removing_legacy_modules(self):
        fixture = _load_teacher_classroom_fixture()
        if fixture is None:
            self.skipTest("No teacher-accessible classroom is available for classroom workspace nav smoke test.")
        teacher, class_offering_id = fixture

        with (
            patch.object(ui_router, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_classroom, "close_overdue_assignments", lambda conn: 0),
            patch.object(ui_router, "maybe_enqueue_teacher_daily_checkin_sync", lambda *args, **kwargs: None),
            patch.object(ui_classroom, "maybe_enqueue_teacher_daily_checkin_sync", lambda *args, **kwargs: None),
            patch.object(ui_router, "record_behavior_event", lambda *args, **kwargs: None),
            patch.object(ui_classroom, "record_behavior_event", lambda *args, **kwargs: None),
            patch.object(ui_router, "schedule_discussion_mood_refresh_soon", lambda *args, **kwargs: None),
            patch.object(ui_classroom, "schedule_discussion_mood_refresh_soon", lambda *args, **kwargs: None),
            _authenticated_client(teacher) as client,
        ):
            response = client.get(f"/classroom/{class_offering_id}", follow_redirects=False)

        self.assertEqual(200, response.status_code)
        html = response.text
        self.assertIn('data-lanshare-island="classroom-page"', html)
        self.assertNotIn('data-lanshare-island="classroom-workspace-nav-sync"', html)
        self.assertIn('data-lanshare-island="assignment-task-board-sync"', html)
        self.assertIn('data-lanshare-island="classroom-activity-workspace-sync"', html)
        self.assertIn('data-lanshare-island="resource-workspace-sync"', html)
        self.assertIn('data-lanshare-island="material-learning-path-sync"', html)
        if 'id="learning-progress-modal"' in html:
            self.assertIn('data-lanshare-island="learning-progress-sync"', html)
        self.assertIn('data-lanshare-island="assignment-authoring-sync"', html)
        self.assertIn('data-lanshare-island="exam-assign-sync"', html)
        self.assertNotIn("classroom-workspace-nav-sync", html)
        self.assertIn("assignment-task-board-sync", html)
        self.assertIn("classroom-activity-workspace-sync", html)
        self.assertIn("resource-workspace-sync", html)
        self.assertIn("material-learning-path-sync", html)
        if 'id="learning-progress-modal"' in html:
            self.assertIn("learning-progress-sync", html)
            self.assertIn("data-learning-modal-open", html)
        self.assertIn("assignment-authoring-sync", html)
        self.assertIn("exam-assign-sync", html)
        self.assertIn("classroom-page", html)
        self.assertIn("data-workspace-nav", html)
        self.assertIn('id="assignment-panel"', html)
        self.assertIn("assignment-board", html)
        if "assignment-card-unified" in html:
            self.assertIn("data-assignment-task-card", html)
            self.assertIn("data-assignment-kind=", html)
            self.assertIn("data-assignment-status-key=", html)
            self.assertIn("data-assignment-stage-label=", html)
        self.assertIn('id="materials-panel"', html)
        self.assertIn('id="classroom-materials-list"', html)
        self.assertIn('id="classroom-materials-refresh-btn"', html)
        self.assertIn('id="classroom-materials-breadcrumbs"', html)
        self.assertIn('id="classroom-materials-selection-count"', html)
        self.assertIn('id="discussion-room"', html)
        self.assertIn('id="interaction-panel"', html)
        self.assertIn('id="collaboration-panel"', html)
        self.assertIn('id="resources-panel"', html)
        self.assertIn('id="file-list-container"', html)
        self.assertIn('id="uploadZone"', html)
        self.assertIn('id="shared-file-modal"', html)
        self.assertIn('id="assignment-modal"', html)
        self.assertIn('id="assignment-learning-stage-key"', html)
        self.assertIn('id="assignment-late-submission-enabled"', html)
        self.assertIn("window.examApp.saveAssignment()", html)
        self.assertIn('id="exam-assign-modal"', html)
        self.assertIn('id="exam-learning-stage-key"', html)
        self.assertIn('id="exam-late-submission-enabled"', html)
        self.assertIn('id="exam-allowed-file-types"', html)
        self.assertIn("window.examApp.loadExamPapers()", html)
        self.assertIn("window.examApp.confirmExamAssign()", html)
        self.assertIn("data-classroom-activity-count", html)
        self.assertIn("data-classroom-activity-tab", html)
        self.assertNotIn("initClassroomPage()", html)
        self.assertNotIn("new ClassroomChat", html)
        self.assertNotIn("fileApp.init(window.APP_CONFIG)", html)
        self.assertNotIn("materialsApp.init(window.APP_CONFIG)", html)
        self.assertNotIn("examApp.init(window.APP_CONFIG)", html)

    def test_message_center_route_keeps_redirect_and_lands_on_page_island_without_direct_legacy_script(self):
        with _authenticated_client(self.teacher) as client:
            response = client.get("/message-center", follow_redirects=False)

        self.assertEqual(303, response.status_code)
        location = response.headers.get("location", "")
        self.assertIn("/profile?section=notifications", location)
        self.assertTrue(location.endswith("#profile-message-center"))

        with _authenticated_client(self.teacher) as client:
            response = client.get(location.split("#", 1)[0], follow_redirects=False)

        self.assertEqual(200, response.status_code)
        html = response.text
        self.assertIn('data-lanshare-island="message-center-page"', html)
        self.assertIn("message-center-page", html)
        self.assertIn('data-lanshare-island="message-center-workspace-sync"', html)
        self.assertIn("message-center-workspace-sync", html)
        self.assertNotIn("/static/js/message_center.js", html)
        self.assertIn('id="message-center-mark-read"', html)
        self.assertIn('id="message-center-feed"', html)
        self.assertIn('id="message-center-compose-form"', html)
        self.assertIn('data-md-insert="bold"', html)

    def test_manage_materials_injects_page_island_without_direct_legacy_script(self):
        with _authenticated_client(self.teacher) as client:
            response = client.get("/manage/materials", follow_redirects=False)

        if response.status_code != 200:
            self.skipTest(f"Authenticated teacher cannot access /manage/materials in this fixture: {response.status_code}")

        html = response.text
        self.assertIn('data-lanshare-island="materials-manage-page"', html)
        self.assertIn("materials-manage-page", html)
        self.assertNotIn("/static/js/materials_manage.js", html)
        self.assertIn("window.MATERIALS_MANAGE_CONFIG", html)
        self.assertIn('data-testid="p03-materials-list"', html)
        self.assertIn('data-testid="p03-materials-refresh"', html)
        self.assertIn('data-testid="p03-materials-file-input"', html)

    def test_profile_message_center_injects_page_and_workspace_islands_without_direct_legacy_script(self):
        with _authenticated_client(self.teacher) as client:
            response = client.get("/profile?section=notifications#profile-message-center", follow_redirects=False)

        self.assertEqual(200, response.status_code)
        html = response.text
        self.assertIn('data-lanshare-island="message-center-page"', html)
        self.assertIn("message-center-page", html)
        self.assertIn('data-lanshare-island="message-center-workspace-sync"', html)
        self.assertIn("message-center-workspace-sync", html)
        self.assertNotIn("/static/js/message_center.js", html)
        self.assertIn('id="message-center-mark-read"', html)
        self.assertIn('id="message-center-feed"', html)
        self.assertIn('id="message-center-compose-form"', html)
        self.assertIn('data-md-insert="bold"', html)


if __name__ == "__main__":
    unittest.main()
