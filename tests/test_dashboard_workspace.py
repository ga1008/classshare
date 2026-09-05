from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

from classroom_app.services.dashboard_workspace_service import (
    assignment_workspace_source,
    build_dashboard_workspace,
    load_dashboard_workspace,
    local_datetime,
    normalize_workspace_item,
)


NOW = datetime(2026, 9, 5, 10)
USER = {"id": 7, "role": "student", "name": "测试同学"}
OFFERINGS = [{"id": 1, "course_id": 11, "course_name": "网络", "class_name": "一班"}]


def todo(source_id=1, **kwargs):
    return {"source_type": "manual", "source_id": source_id, "is_manual": True, "class_offering_id": 1, "title": f"待办 {source_id}", **kwargs}


def assignment(source_id=10, **kwargs):
    return {"id": source_id, "offering_id": 1, "title": "作业", "status": "published", "availability_mode": "deadline", "auto_close": 1, "due_at": "2026-09-05T12:00", **kwargs}


class DashboardWorkspaceSemanticsTests(unittest.TestCase):
    def test_d22_supplement_boundary_has_no_gap_and_uses_the_same_penalty_snapshot(self):
        from classroom_app.services.late_submission_policy import assignment_late_window_accepts, build_late_submission_snapshot, apply_late_policy_to_score
        row = assignment(due_at="2026-09-05T10:00:00", late_submission_enabled=1,
                         late_submission_until="2026-09-05T11:00:00", late_penalty_strategy="fixed", late_penalty_points=5, late_score_cap=80)
        self.assertTrue(assignment_late_window_accepts(row, now_dt=NOW))
        self.assertFalse(assignment_late_window_accepts(row, now_dt=datetime(2026, 9, 5, 11)))
        snapshot = build_late_submission_snapshot(row, "2026-09-05T10:00:00")
        self.assertTrue(snapshot["is_late_submission"])
        result = apply_late_policy_to_score(90, submission={"late_policy_snapshot_json": snapshot}, assignment=row)
        self.assertTrue(result["applied"])
        self.assertEqual(80, result["final_score"])

    def workspace(self, sources, **kwargs):
        return build_dashboard_workspace(user=USER, offerings=OFFERINGS, sources=sources, now=NOW, **kwargs)

    def test_p02_streaming_page_reuses_each_source_order_key(self):
        from classroom_app.services import dashboard_workspace_service as service
        sources = [todo(index, due_at="2026-09-05T10:20:00") for index in range(1000)]
        with patch.object(service, "workspace_sort_key", wraps=service.workspace_sort_key) as keys:
            result = self.workspace(iter(sources), limit=20)
        self.assertEqual(1000, result["total"])
        self.assertEqual(20, len(result["all_items"]))
        self.assertEqual([item["key"] for item in result["all_items"][:3]], [item["key"] for item in result["focus_items"]])
        self.assertTrue(result["next_cursor"])
        # An extra key for the cursor is constant; growing focus candidates
        # must not re-parse eight retained rows for each new source event.
        self.assertLessEqual(keys.call_count, len(sources) + 1)

    def test_d01_unfinished_overdue_does_not_disappear(self):
        result = self.workspace([todo(due_at="2026-09-04T23:00")])
        item = result["all_items"][0]
        self.assertEqual("overdue", item["date_bucket"])
        self.assertFalse(item["is_completed"])
        self.assertTrue(item["is_actionable"])
        self.assertEqual(item["key"], result["focus_items"][0]["key"])

    def test_d02_past_class_is_not_completed_task(self):
        item = normalize_workspace_item({"source_type": "lesson", "source_id": 9, "starts_at": "2026-09-04", "is_completed": True}, now=NOW)
        self.assertEqual("past", item["status"])
        self.assertFalse(item["is_completed"])
        self.assertFalse(item["is_actionable"])
        self.assertEqual("history", item["date_bucket"])

    def test_d03_lesson_does_not_become_submission_deadline(self):
        result = self.workspace([{"source_type": "lesson", "source_id": 9, "start_at": "2026-09-10T00:00", "due_at": "2026-09-10T00:00"}])
        item = result["all_items"][0]
        self.assertEqual("class", item["kind"])
        self.assertEqual("", item["due_at"])
        self.assertEqual("", item["effective_due_at"])
        self.assertFalse(item["has_hard_deadline"])
        self.assertEqual(0, result["today_due_count"])
        self.assertNotIn("截止", item["time_label"])

    def test_d04_natural_week_differs_from_rolling_seven_days(self):
        source = todo(due_at="2026-09-07T08:00+08:00")
        sunday = normalize_workspace_item(source, now=datetime(2026, 9, 6, 23, 59))
        monday = normalize_workspace_item(source, now=datetime(2026, 9, 7))
        self.assertFalse(sunday["is_this_week"])
        self.assertTrue(sunday["is_next_seven_days"])
        self.assertTrue(monday["is_this_week"])
        self.assertEqual("today", monday["date_bucket"])

    def test_d04_aware_timestamp_converts_to_shanghai(self):
        self.assertEqual(datetime(2026, 9, 7), local_datetime("2026-09-06T16:00:00Z"))
        self.assertEqual(datetime(2026, 9, 7), local_datetime("2026-09-07T00:00:00+08:00"))
        self.assertEqual(datetime(2026, 9, 7), local_datetime("2026-09-07"))
        item = normalize_workspace_item(todo(due_at="2026-09-06T16:00:00Z"), now=datetime(2026, 9, 7))
        self.assertEqual("today", item["date_bucket"])
        self.assertTrue(item["due_at"].endswith("+08:00"))

    def test_same_task_window_is_identical_for_naive_space_z_and_offset(self):
        items = []
        for due in ("2026-09-05T10:15:00", "2026-09-05 10:15:00", "2026-09-05T02:15:00Z", "2026-09-05T10:15:00+08:00"):
            items.append(normalize_workspace_item(assignment_workspace_source(assignment(due_at=due), now=NOW, role="student"), now=NOW))
        for item in items[1:]:
            self.assertEqual(items[0], item)

    def test_teacher_publication_and_student_resubmission_cross_exact_boundaries(self):
        row = assignment(due_at="2026-09-05T10:05", submission_id=9, submission_status="graded", resubmission_allowed=1, resubmission_due_at="2026-09-05T10:10")
        teacher_before = normalize_workspace_item(assignment_workspace_source(row, now=NOW, role="teacher"), now=NOW)
        teacher_after = normalize_workspace_item(assignment_workspace_source(row, now=datetime(2026, 9, 5, 10, 5), role="teacher"), now=datetime(2026, 9, 5, 10, 5))
        self.assertEqual("已发布", teacher_before["status_label"])
        self.assertEqual("已关闭", teacher_after["status_label"])
        self.assertFalse(teacher_after["is_completed"])
        for moment, expected in ((datetime(2026, 9, 5, 10, 9, 59), True), (datetime(2026, 9, 5, 10, 10), False)):
            student = normalize_workspace_item(assignment_workspace_source(row, now=moment, role="student"), now=moment)
            self.assertEqual(expected, student["is_actionable"])
            self.assertFalse(student["is_completed"])

    def test_d14_yesterdays_streak_does_not_claim_activity_today(self):
        from classroom_app.services.dashboard_service import _build_student_cockpit
        args = dict(offerings=[], priority_items=[], cultivation_profile={}, todo_items=[], continue_material=None,
                    review_summary=None, pending_total=0, submitted_total=0, unread_total=0, now=NOW)
        for active, text in ((False, "昨天有记录，今天继续"), (True, "今天也来了，继续保持")):
            result = _build_student_cockpit(**args, streak_info={"current_streak": 2, "longest_streak": 2, "active_today": active})
            stat = next(i for i in result["stats"] if i["label"] == "连续学习")
            self.assertEqual(text, stat["hint"])

    def test_d05_no_date_todo_has_no_sentinel_or_false_deadline(self):
        result = self.workspace([todo()])
        self.assertEqual("undated", result["all_items"][0]["date_bucket"])
        self.assertNotIn("9999", str(result["all_items"]))
        self.assertEqual(1, result["pending_total"])

    def test_d06_does_not_claim_today_completed_from_deadline(self):
        result = self.workspace([todo(is_completed=True, due_at="2026-09-05T12:00", completed_at="2026-09-04T10:00")])
        self.assertNotIn("today_completed", result)
        self.assertEqual("history", result["all_items"][0]["date_bucket"])

    def test_teacher_closed_assignment_is_history_without_claiming_completion(self):
        item = normalize_workspace_item(assignment_workspace_source(assignment(status="closed"), now=NOW, role="teacher"), now=NOW)
        self.assertEqual("history", item["date_bucket"])
        self.assertFalse(item["is_completed"])
        self.assertFalse(item["is_actionable"])

    def test_d07_d21_dedup_before_limit_and_stable_tie_break(self):
        a, b, c = [todo(i, title="同名事项", due_at="2026-09-05T12:00") for i in (1, 2, 3)]
        first = self.workspace([a, a, a, b, c])
        second = self.workspace([c, b, a])
        self.assertEqual(3, first["total"])
        self.assertEqual(3, len(first["focus_items"]))
        self.assertEqual([item["key"] for item in first["focus_items"]], [item["key"] for item in second["focus_items"]])

    def test_d08_pagination_uses_full_filtered_count(self):
        sources = (todo(i, due_at="2026-09-05T12:00") for i in range(145))
        first = self.workspace(sources, limit=100)
        second = self.workspace((todo(i, due_at="2026-09-05T12:00") for i in range(145)), offset=100, limit=100)
        self.assertEqual(145, first["total"])
        self.assertEqual(145, second["filtered_total"])
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual(100, len(first["all_items"]))
        self.assertEqual(45, len(second["all_items"]))
        self.assertFalse({i["key"] for i in first["all_items"]} & {i["key"] for i in second["all_items"]})

    def test_d15_finite_late_window_uses_current_window(self):
        row = assignment(due_at="2026-09-04T12:00", late_submission_enabled=1, late_submission_until="2026-09-05T10:15")
        source = assignment_workspace_source(row, now=NOW, role="student")
        result = self.workspace([source])
        item = result["focus_items"][0]
        self.assertEqual("late", item["status"])
        self.assertEqual("2026-09-05T10:15:00+08:00", item["effective_due_at"])
        self.assertEqual("overdue", item["date_bucket"])
        self.assertEqual(1, result["urgent_total"])

    def test_d15_expired_resubmission_is_not_actionable(self):
        row = assignment(submission_id=9, submission_status="graded", resubmission_allowed=1, resubmission_due_at="2026-09-05T09:00")
        item = normalize_workspace_item(assignment_workspace_source(row, now=NOW, role="student"), now=NOW)
        self.assertFalse(item["is_actionable"])
        self.assertFalse(item["is_completed"])
        self.assertEqual("重交已结束", item["status_label"])

    def test_d15_resubmission_overrides_original_closed_window(self):
        row = assignment(status="closed", due_at="2026-09-01", submission_id=9, resubmission_allowed=1, resubmission_due_at="2026-09-05T12:00")
        item = normalize_workspace_item(assignment_workspace_source(row, now=NOW, role="student"), now=NOW)
        self.assertTrue(item["is_actionable"])
        self.assertEqual("returned", item["status"])
        self.assertEqual("2026-09-05T12:00:00+08:00", item["effective_due_at"])

    def test_d16_date_only_lesson_is_never_in_progress(self):
        item = normalize_workspace_item({"source_type": "lesson", "source_id": 1, "starts_at": "2026-09-05T00:00", "section_label": "第 4-5 节"}, now=NOW)
        self.assertEqual("today", item["status"])
        self.assertEqual("第 4-5 节", item["time_label"])
        self.assertFalse(item["is_in_progress"])

    def test_d19_hard_deadline_beats_running_poll(self):
        task = assignment_workspace_source(assignment(due_at="2026-09-05T10:05"), now=NOW, role="student")
        result = self.workspace([{"kind": "poll", "source_id": 9, "due_at": "2026-09-05T10:01", "is_in_progress": True, "is_actionable": True, "title": "进行中投票"}, task])
        self.assertEqual("assignment", result["focus_items"][0]["kind"])

    def test_d20_today_lesson_beats_unlimited_late_and_undated_high_priority(self):
        late = assignment_workspace_source(assignment(due_at="2026-09-01T10:00", late_submission_enabled=1), now=NOW, role="student")
        result = self.workspace([late, todo(2, priority="high"), {"source_type": "lesson", "source_id": 1, "starts_at": "2026-09-05"}])
        self.assertEqual("class", result["focus_items"][0]["kind"])
        self.assertEqual(3, result["total"])
        self.assertEqual("", next(i for i in result["all_items"] if i["kind"] == "assignment")["effective_due_at"])

    def test_h06_resume_only_with_personal_reading_record(self):
        material = {"material_id": 1, "class_offering_id": 1, "material_name": "第一课", "href": "/materials/view/1"}
        self.assertEqual([], self.workspace([], continue_material=material)["focus_items"])
        result = self.workspace([], continue_material={**material, "last_viewed_at": "2026-09-04T10:00"})
        self.assertEqual("继续阅读", result["focus_items"][0]["action_label"])

    def test_h07_course_filter_is_intersection_and_does_not_change_total(self):
        result = self.workspace([todo(1), todo(2, class_offering_id=2)], offering_id=2, offering_ids={1}, limit=3)
        self.assertEqual(2, result["total"])
        self.assertEqual(0, result["filtered_total"])
        self.assertEqual([], result["all_items"])

    def test_d22_transition_includes_urgency_boundary(self):
        source = assignment_workspace_source(assignment(due_at="2026-09-05T11:00"), now=NOW, role="student")
        self.assertEqual("2026-09-05T10:30:00+08:00", self.workspace([source])["next_transition_at"])

    def test_manual_payload_preserves_existing_controller_fields(self):
        result = self.workspace([todo(3, notes="个人备注", priority="high", reminder_enabled=True, reminder_lead_minutes=30)])
        data = result["all_items"][0]["agenda_data"]
        self.assertTrue(data["is_manual"])
        self.assertEqual(3, data["todo_id"])
        self.assertEqual(1, data["class_offering_id"])
        self.assertEqual("个人备注", data["notes"])
        self.assertEqual(30, data["reminder_lead_minutes"])


class DashboardWorkspaceReadTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE class_offerings(id INTEGER, course_id INTEGER);
            INSERT INTO class_offerings VALUES(1,11),(2,22);
            CREATE TABLE assignments(id INTEGER, course_id INTEGER, class_offering_id INTEGER, title TEXT, status TEXT, availability_mode TEXT, auto_close INTEGER, due_at TEXT, starts_at TEXT, exam_paper_id INTEGER);
            INSERT INTO assignments VALUES(1,11,1,'永久任务','published','permanent',0,NULL,NULL,NULL),(2,22,2,'其他班任务','published','permanent',0,NULL,NULL,NULL),(3,11,1,'草稿','new','permanent',0,NULL,NULL,NULL);
            CREATE TABLE submissions(id INTEGER, assignment_id INTEGER, student_pk_id INTEGER, status TEXT, resubmission_allowed INTEGER, resubmission_due_at TEXT, is_absence_score INTEGER);
            CREATE TABLE learning_stage_exam_attempts(id INTEGER, assignment_id INTEGER, class_offering_id INTEGER, student_id INTEGER, status TEXT, stage_key TEXT);
            CREATE TABLE class_offering_sessions(id INTEGER, class_offering_id INTEGER, title TEXT, session_date TEXT, academic_section_text TEXT);
            INSERT INTO class_offering_sessions VALUES(1,1,'今天的课','2026-09-05','4-5'),(2,2,'其他班课','2026-09-05','1-2');
            CREATE TABLE teacher_academic_course_exam_items(id INTEGER, class_offering_id INTEGER, sync_status TEXT);
            CREATE TABLE classroom_todos(id INTEGER, class_offering_id INTEGER, owner_role TEXT, owner_user_pk INTEGER, deleted_at TEXT, title TEXT, metadata_json TEXT, completed_at TEXT, due_at TEXT, start_at TEXT, notes TEXT);
            INSERT INTO classroom_todos VALUES(1,1,'student',7,NULL,'我的待办','{}',NULL,NULL,NULL,''),(2,1,'student',8,NULL,'同学的秘密','{}',NULL,NULL,NULL,''),(3,2,'student',7,NULL,'未授权课堂事项','{}',NULL,NULL,NULL,'');
        """)

    def tearDown(self):
        self.conn.close()

    def test_full_sources_include_permanent_assignment_but_not_other_users(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        result = load_dashboard_workspace(self.conn, user=USER, offerings=OFFERINGS, continue_material={}, now=NOW)
        titles = {item["title"] for item in result["all_items"]}
        self.assertEqual({"永久任务", "今天的课", "我的待办"}, titles)
        self.assertTrue(all(query.lstrip().upper().startswith("SELECT") for query in statements))
        self.assertEqual(3, result["total"])

    def test_unrelated_personal_trial_never_enters_payload(self):
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES(1,10,1,8,'generated','peer-secret')")
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES(2,11,1,7,'generated','mine')")
        result = load_dashboard_workspace(self.conn, user=USER, offerings=OFFERINGS, continue_material={}, now=NOW)
        self.assertNotIn("peer-secret", str(result))
        trials = [i for i in result["all_items"] if i["kind"] == "stage"]
        self.assertEqual(1, len(trials))
        self.assertEqual(2, trials[0]["source_id"])
        self.assertFalse(trials[0]["is_actionable"])

    def test_trial_respects_its_assignment_window(self):
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES(2,1,1,7,'generated','foundation')")
        self.conn.execute("UPDATE assignments SET status='closed' WHERE id=1")
        result = load_dashboard_workspace(self.conn, user=USER, offerings=OFFERINGS, continue_material={}, now=NOW)
        trial = next(i for i in result["all_items"] if i["kind"] == "stage")
        self.assertFalse(trial["is_actionable"])
        self.assertNotIn("foundation", trial["title"])

    def test_poll_custom_audience_draft_and_personal_ballot_boundaries(self):
        self.conn.executescript("""
            CREATE TABLE polls(id INTEGER, title TEXT, status TEXT, deadline_at TEXT, owner_role TEXT, owner_user_pk INTEGER, audience_scope TEXT);
            CREATE TABLE poll_assignments(poll_id INTEGER, class_offering_id INTEGER);
            CREATE TABLE poll_ballots(id INTEGER, poll_id INTEGER, voter_id INTEGER);
            CREATE TABLE poll_participants(poll_id INTEGER, student_id INTEGER);
            INSERT INTO polls VALUES(1,'全班投票','active',NULL,'teacher',1,'class'),(2,'秘密投票','active',NULL,'student',8,'custom'),
                (3,'同学草稿','draft',NULL,'student',8,'class'),(4,'我的投票','active',NULL,'teacher',1,'custom');
            INSERT INTO poll_assignments VALUES(1,1),(2,1),(3,1),(4,1);
            INSERT INTO poll_participants VALUES(2,8),(4,7);
            INSERT INTO poll_ballots VALUES(1,1,7),(2,4,8);
        """)
        result = load_dashboard_workspace(self.conn, user=USER, offerings=OFFERINGS, continue_material={}, now=NOW)
        polls = {i["title"]: i for i in result["all_items"] if i["kind"] == "poll"}
        self.assertEqual({"全班投票", "我的投票"}, set(polls))
        self.assertTrue(polls["全班投票"]["is_completed"])
        self.assertFalse(polls["全班投票"]["is_actionable"])
        self.assertTrue(polls["我的投票"]["is_actionable"])

    def test_teacher_todos_calendar_and_work_counts_are_owned(self):
        self.conn.executescript("""
            ALTER TABLE class_offerings ADD COLUMN class_id INTEGER;
            UPDATE class_offerings SET class_id=id;
            CREATE TABLE students(id INTEGER,class_id INTEGER);
            INSERT INTO students VALUES(7,1),(8,1),(9,1),(10,1),(11,2);
            CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
            INSERT INTO classroom_todos VALUES(4,99,'teacher',9,NULL,'我的独立待办','{}',NULL,NULL,NULL,''),
                (5,1,'teacher',10,NULL,'其他教师秘密','{}',NULL,NULL,NULL,'');
            INSERT INTO submissions VALUES(1,1,7,'submitted',0,NULL,0),(2,1,8,'submitted',1,NULL,0),
                (3,1,9,'grading',0,NULL,0),(4,1,10,'submitted',0,NULL,1);
            INSERT INTO assignments VALUES(6,11,1,'同一学生第二份','published','permanent',0,NULL,NULL,NULL);
            INSERT INTO submissions VALUES(5,6,7,'submitted',0,NULL,0),(6,6,11,'submitted',0,NULL,0);
            CREATE TABLE teacher_calendar_events(id INTEGER, teacher_id INTEGER, source_type TEXT, status TEXT, deleted_at TEXT, title TEXT, starts_at TEXT, due_at TEXT, ends_at TEXT);
            INSERT INTO teacher_calendar_events VALUES(1,9,'manual','active',NULL,'教研会议',NULL,NULL,NULL),
                (2,10,'academic_invigilation','active',NULL,'他人监考',NULL,NULL,NULL);
            CREATE TABLE classes(id INTEGER,created_by_teacher_id INTEGER);
            INSERT INTO classes VALUES(1,9),(2,10);
            CREATE TABLE student_password_reset_requests(id INTEGER,class_id INTEGER,teacher_id INTEGER,status TEXT);
            INSERT INTO student_password_reset_requests VALUES(1,1,9,'pending'),(2,2,9,'pending'),(3,1,10,'pending');
        """)
        result = load_dashboard_workspace(self.conn, user={"id": 9, "role": "teacher"}, offerings=OFFERINGS, now=NOW)
        items = {i["title"]: i for i in result["all_items"]}
        self.assertNotIn("其他教师秘密", items)
        self.assertNotIn("他人监考", items)
        self.assertEqual(0, items["我的独立待办"]["offering_id"])
        self.assertEqual("teacher_work", items["教研会议"]["kind"])
        self.assertIn("2 份作业待批改", items)
        self.assertEqual(2, result["offering_summaries"]["1"]["pending_review_count"])
        self.assertIn("1 项密码申请待审核", items)

    def test_web_projection_is_opt_in_and_miniapp_context_default_is_unchanged(self):
        from classroom_app.services.dashboard_service import build_dashboard_context
        with patch("classroom_app.services.dashboard_service._build_student_dashboard_context", return_value={"legacy": True}) as legacy:
            self.assertEqual({"legacy": True}, build_dashboard_context(None, USER))
            self.assertFalse(legacy.call_args.kwargs["include_workspace"])
            build_dashboard_context(None, USER, include_workspace=True)
            self.assertTrue(legacy.call_args.kwargs["include_workspace"])


class DashboardContinueMaterialTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE class_offerings(id INTEGER,course_id INTEGER,class_id INTEGER,home_learning_material_id INTEGER);
            CREATE TABLE courses(id INTEGER,name TEXT);
            CREATE TABLE classes(id INTEGER,name TEXT);
            CREATE TABLE course_materials(id INTEGER,name TEXT,node_type TEXT);
            CREATE TABLE class_offering_sessions(id INTEGER,class_offering_id INTEGER,order_index INTEGER,learning_material_id INTEGER);
            CREATE TABLE course_material_assignments(class_offering_id INTEGER,material_id INTEGER);
            CREATE TABLE learning_material_progress(class_offering_id INTEGER,material_id INTEGER,student_id INTEGER,completed INTEGER,max_scroll_ratio REAL,active_seconds INTEGER,accumulated_seconds INTEGER,last_viewed_at TEXT,updated_at TEXT);
            INSERT INTO class_offerings VALUES(1,11,1,NULL),(2,22,2,NULL);
            INSERT INTO courses VALUES(11,'网络'),(22,'其他课程');
            INSERT INTO classes VALUES(1,'一班'),(2,'二班');
            INSERT INTO course_materials VALUES(1,'尚未读过的主材料','file'),(2,'本人读过的次材料','file'),(3,'其他课程材料','file');
            INSERT INTO class_offering_sessions VALUES(1,1,1,1),(2,2,1,3);
            INSERT INTO learning_material_progress VALUES(1,2,7,0,0.4,80,90,'2026-09-04T10:00','2026-09-04T10:00'),
                (1,1,8,0,0.5,90,90,'2026-09-05T10:00','2026-09-05T10:00'),
                (2,3,7,0,0.5,90,90,'2026-09-05T10:00','2026-09-05T10:00');
        """)

    def tearDown(self):
        self.conn.close()

    def test_secondary_binding_reads_only_own_authorized_progress(self):
        from classroom_app.services.dashboard_service import _load_student_continue_material
        self.conn.executescript("""
            CREATE TABLE class_offering_learning_materials(class_offering_id INTEGER,session_id INTEGER,material_id INTEGER);
            INSERT INTO class_offering_learning_materials VALUES(1,1,1),(1,1,2),(2,2,3);
        """)
        statements = []
        self.conn.set_trace_callback(statements.append)
        item = _load_student_continue_material(self.conn, student_id=7, offering_ids=[1], include_multiple=True, require_read=True)
        self.assertEqual(2, item["material_id"])
        self.assertEqual("/materials/view/2?class_offering_id=1&session_id=1", item["href"])
        self.assertTrue(all(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements))

    def test_old_schema_falls_back_without_ddl_or_unread_resume(self):
        from classroom_app.services.dashboard_service import _load_student_continue_material
        item = _load_student_continue_material(self.conn, student_id=7, offering_ids=[1], include_multiple=True, require_read=True)
        self.assertIsNone(item)
        # Default legacy selection remains available for the existing MP path.
        legacy = _load_student_continue_material(self.conn, student_id=7, offering_ids=[1])
        self.assertEqual(1, legacy["material_id"])
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='class_offering_learning_materials'").fetchone())


if __name__ == "__main__":
    unittest.main()
