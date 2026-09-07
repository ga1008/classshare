"""Real in-memory SQL for classification, scope and notification privacy."""
import asyncio
import json
import unittest
from contextlib import nullcontext
from datetime import datetime
from unittest.mock import patch

from tests import test_score_projection_service as fixtures
from classroom_app.routers.homework_parts import assignments
from classroom_app.services import dashboard_service as dashboard, message_center_service as notifications
from classroom_app.services.dashboard_workspace_service import assignment_workspace_source, normalize_workspace_item
from classroom_app.services.dashboard_calendar_service import calendar_item
from classroom_app.services.global_search_service import _search_assignments
from classroom_app.services import todo_service, calendar_feed_service


class AssessmentSecondarySurfacesTests(unittest.TestCase):
    def setUp(self):
        fixtures.ScoreProjectionTests.setUp(self)
        self.conn.executescript("""
            ALTER TABLE courses ADD COLUMN created_by_teacher_id INTEGER DEFAULT 7;
            ALTER TABLE courses ADD COLUMN department TEXT DEFAULT '系';
            ALTER TABLE class_offerings ADD COLUMN course_id INTEGER DEFAULT 1;
            ALTER TABLE class_offerings ADD COLUMN teacher_id INTEGER DEFAULT 7;
            ALTER TABLE class_offerings ADD COLUMN class_id INTEGER DEFAULT 1;
            ALTER TABLE class_offerings ADD COLUMN semester TEXT;
            UPDATE class_offerings SET semester='秋季' WHERE id=1;
            UPDATE class_offerings SET semester='春季' WHERE id=2;
            CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT, department TEXT);
            CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, class_id INTEGER, enrollment_status TEXT);
            CREATE TABLE teachers(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offering_class_links(offering_id INTEGER, class_id INTEGER);
            INSERT INTO teachers VALUES (7, '教师');
            INSERT INTO classes VALUES (1, '一班', '系');
            INSERT INTO students VALUES (1, '学生', 1, 'active'), (2, '同学', 1, 'active');
        """)

    def tearDown(self):
        fixtures.ScoreProjectionTests.tearDown(self)

    def add(self, *args, **kwargs):
        sid = fixtures.ScoreProjectionTests.add(self, *args, **kwargs)
        self.conn.execute("UPDATE assignments SET status='published', created_at='2026-09-07', availability_mode='always_open' WHERE id=?", (args[0],))
        return sid

    def test_search_does_not_infer_kind_from_paper_and_excludes_personal_and_other_terms(self):
        self.add(1, 80, kind="homework")
        self.add(2, 90, kind="final")
        self.add(3, 100, kind=None)
        self.add(4, 70, kind="midterm", offering=2)
        self.conn.execute("UPDATE assignments SET exam_paper_id='paper' WHERE id=1")
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES (1, 3)")
        rows = _search_assignments(self.conn, [1], "%任务%", role="student")
        self.assertEqual({"/assignment/1", "/assignment/2"}, {r["link_url"] for r in rows})
        rows = {r["assessment_kind"]: r for r in rows}
        self.assertIn("平时作业", rows["homework"]["subtitle"])
        self.assertIn("秋季", rows["homework"]["subtitle"])
        self.assertEqual("exam_paper", rows["homework"]["answer_mode"])
        self.assertEqual("submission", rows["final"]["answer_mode"])
        self.assertTrue(all(r["kind"] == "assignment" for r in rows.values()))

    def test_workspace_calendar_preserves_format_keys_but_displays_formal_classification(self):
        now = datetime(2026, 9, 7, 10)
        row = {"id": 1, "offering_id": 10, "status": "published", "title": "任务", "availability_mode": "always_open",
               "exam_paper_id": "p", "assessment_kind": "homework", "assessment_kind_version": 2,
               "assessment_kind_source": "teacher_edit", "semester_id": 8, "semester_name": "秋季"}
        source = assignment_workspace_source(row, now=now, role="student")
        item = normalize_workspace_item(source, now=now)
        calendar = calendar_item(item, source=source)
        self.assertEqual("exam_task", item["kind"])
        self.assertEqual("平时作业", item["type_label"])
        self.assertEqual("teacher_edit", item["classification_source"])
        self.assertEqual("exam_paper", calendar["answer_mode"])
        self.assertEqual(8, calendar["semester_id"])
        self.assertEqual("homework", item["agenda_data"]["assessment_kind"])
        academic = normalize_workspace_item({"kind": "academic_exam", "id": 2}, now=now)
        self.assertEqual("exam", academic["kind"])
        self.assertNotIn("assessment_kind", academic)
        stage = normalize_workspace_item({**source, "kind": "stage", "assessment_kind": "midterm"}, now=now)
        self.assertIsNone(stage["assessment_kind"])
        self.assertEqual("个人阶段试炼", stage["type_label"])

    def test_dashboard_counts_exclude_trials_keep_zero_unsubmitted_and_count_each_paper(self):
        self.add(1, 0, absence=1, kind="homework")
        self.add(2, None, status="submitted", kind="midterm")
        self.add(3, None, status="submitted", kind="final")
        self.add(4, 99, kind=None)
        self.add(5, 20, kind="homework", offering=2)
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES (1, 4)")
        student = dashboard._load_student_assignment_stats(self.conn, [1, 2], 1)
        self.assertEqual((3, 1, 2), (student[1]["assignment_count"], student[1]["pending_count"], student[1]["submitted_count"]))
        self.assertEqual((1, 1, 1), tuple(student[1][f"{kind}_count"] for kind in ("homework", "midterm", "final")))
        self.assertEqual(1, student[2]["assignment_count"])
        self.assertEqual(2, dashboard._load_teacher_pending_submission_stats(self.conn, [1])[1]["pending_review_count"])
        self.assertEqual(3, dashboard._load_teacher_assignment_stats(self.conn, [1])[1]["assignment_count"])
        priorities = dashboard._load_student_priority_items(self.conn, 1)
        self.assertEqual(["/assignment/1"], [r["href"] for r in priorities])

    def test_course_statistics_use_effective_grades_and_keep_semesters_separate(self):
        self.add(1, 80, status="grading", kind="homework")
        self.add(1, 0, student=2, absence=1, kind="homework")
        self.add(2, None, status="grading_review", kind="final")
        self.add(3, 100, kind=None)
        self.add(4, 20, kind="homework", offering=2)
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES (1, 3)")
        with patch.object(assignments, "get_db_connection", lambda: nullcontext(self.conn)):
            result = asyncio.run(assignments.get_course_assignment_stats(1, user={"id": 7}))
            filtered = asyncio.run(assignments.get_course_assignment_stats(1, user={"id": 7}, assessment_kind="homework", semester_id=2))
        rows = {r["assignment_id"]: r for r in result["assignments"]}
        self.assertEqual({1, 2, 4}, set(rows))
        self.assertEqual((40, 2, 1, 1, 50), (rows[1]["avg_score"], rows[1]["graded"], rows[1]["regrading"], rows[1]["absence"], rows[1]["pass_rate"]))
        self.assertIsNone(rows[2]["avg_score"])
        self.assertIsNone(rows[2]["max_score"])
        self.assertIsNone(rows[2]["pass_rate"])
        self.assertEqual(1, rows[2]["grading_review"])
        self.assertEqual([4], [r["assignment_id"] for r in filtered["assignments"]])
        self.assertNotIn("facts", json.dumps(result))
        self.assertNotIn("旧评语", json.dumps(result, ensure_ascii=False))
        self.assertEqual(3, len(result["categories"]))

    def test_publish_and_due_notices_use_current_kind_and_keep_dedupe_references(self):
        self.add(1, 0, absence=1, kind="midterm")
        self.add(2, 100, kind=None)
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES (1, 2)")
        captured = []
        with patch.object(notifications, "_insert_notification_if_allowed", side_effect=lambda conn, payload, **kw: captured.append(payload) or True):
            self.assertEqual(2, notifications.create_assignment_published_notifications(self.conn, 1))
            self.assertEqual(0, notifications.create_assignment_published_notifications(self.conn, 2))
            self.assertEqual(2, notifications.create_assignment_due_reminder_notifications(self.conn, 1, window_label="day", window_display="明天"))
        self.assertTrue(all("期中测验" in p["title"] for p in captured))
        self.assertEqual({"1:day"}, {p["ref_id"] for p in captured if p["ref_type"] == "assignment_due"})
        self.assertTrue(all(json.loads(p["metadata_json"])["assessment_kind"] == "midterm" and json.loads(p["metadata_json"])["semester_id"] == 1 for p in captured))

    def test_hidden_group_grade_never_leaks_in_notice_metadata_body_or_wechat(self):
        sid = self.add(1, 90, kind="final")
        fixtures.ScoreProjectionTests.bind(self, 1, sid, revealed=0)
        captured = []
        with patch.object(notifications, "_load_student_support_profile", return_value=""), \
             patch.object(notifications, "_insert_notification_if_allowed", side_effect=lambda conn, payload, **kw: captured.append(payload) or True), \
             patch("classroom_app.services.wechat_mp_subscribe_service.send_subscribe_message") as wechat:
            notifications.create_student_grading_notification(self.conn, sid, actor_role="teacher")
        self.assertEqual(1, len(captured))
        metadata = json.loads(captured[0]["metadata_json"])
        self.assertIsNone(metadata["score"])
        self.assertEqual("group_pending", metadata["grade_display_state"])
        self.assertNotIn("90", captured[0]["body_preview"])
        self.assertNotIn("旧评语", json.dumps(captured, ensure_ascii=False))
        self.assertIn("期末测验", captured[0]["title"])
        wechat.assert_not_called()

    def test_legacy_todo_and_ical_keep_classification_semester_and_absence_pending(self):
        self.add(1, 0, absence=1, kind="final")
        self.conn.execute("CREATE TABLE exam_papers (id TEXT PRIMARY KEY, title TEXT)")
        self.conn.execute("UPDATE assignments SET due_at='2026-09-08T10:00:00' WHERE id=1")
        now = datetime(2026, 9, 7, 10)
        items = todo_service._assignment_items(self.conn, class_offering_id=1, user={"id": 1, "role": "student"}, now=now)
        self.assertEqual(1, len(items))
        self.assertEqual("期末测验截止", items[0]["subtitle"])
        self.assertEqual("submission", items[0]["answer_mode"])
        self.assertFalse(items[0]["metadata"]["is_exam"])
        self.assertFalse(items[0]["is_completed"])
        with patch.object(calendar_feed_service, "china_now", return_value=now), \
             patch.object(calendar_feed_service, "build_classroom_todo_overview", return_value={"items": items}):
            feed = calendar_feed_service.build_ics_for_user(self.conn, role="student", user_pk=1)
        self.assertIn("期末测验截止", feed.replace("\r\n ", ""))
        self.assertIn("秋季", feed)


if __name__ == "__main__":
    unittest.main()
