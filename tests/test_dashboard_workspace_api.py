"""Isolated HTTP contract tests: real identity/membership logic, no app startup.

Only token decoding and the connection factory are replaced. Every fixture is
synthetic and in-memory; the route, identity checks, discovery SQL and workspace
queries are the same ones used by a logged-in request.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.routers.ui_parts import dashboard


SCHEMA = """
CREATE TABLE teachers(id INTEGER,name TEXT,is_active INTEGER);
INSERT INTO teachers VALUES(1,'任课教师',1),(2,'非任课教师',1),(3,'停用教师',0);
CREATE TABLE students(id INTEGER,class_id INTEGER,enrollment_status TEXT);
INSERT INTO students VALUES(7,1,'active'),(8,2,'active'),(9,1,'suspended'),(10,3,'active'),(11,1,'active');
CREATE TABLE classes(id INTEGER,name TEXT,description TEXT,department TEXT,created_by_teacher_id INTEGER);
INSERT INTO classes VALUES(1,'主班','','',1),(2,'合班','','',1),(3,'其他班','','',2);
CREATE TABLE courses(id INTEGER,name TEXT,description TEXT,credits INTEGER,department TEXT);
INSERT INTO courses VALUES(11,'网络','','2',''),(22,'其他课程','','2','');
CREATE TABLE class_offerings(id INTEGER,class_id INTEGER,course_id INTEGER,teacher_id INTEGER,semester TEXT,semester_id INTEGER,
    schedule_info TEXT,created_at TEXT,first_class_date TEXT,weekly_schedule_json TEXT,home_learning_material_id INTEGER);
INSERT INTO class_offerings VALUES(1,1,11,1,'2026秋',1,'','2026-09-01',NULL,'[]',NULL),
    (2,3,22,2,'2026秋',1,'','2026-09-01',NULL,'[]',NULL);
CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
INSERT INTO class_offering_class_links VALUES(1,2);
CREATE TABLE assignments(id INTEGER,course_id INTEGER,class_offering_id INTEGER,title TEXT,status TEXT,availability_mode TEXT,
    auto_close INTEGER,due_at TEXT,starts_at TEXT,exam_paper_id INTEGER);
INSERT INTO assignments VALUES(1,11,1,'公开任务','published','permanent',0,NULL,NULL,NULL),
    (2,22,2,'其他班任务','published','permanent',0,NULL,NULL,NULL),
    (3,11,1,'教师草稿','new','permanent',0,NULL,NULL,NULL),
    (4,11,1,'本人试炼题','published','permanent',0,NULL,NULL,1),
    (5,11,1,'同学秘密试炼题','published','permanent',0,NULL,NULL,1);
CREATE TABLE submissions(id INTEGER,assignment_id INTEGER,student_pk_id INTEGER,status TEXT,resubmission_allowed INTEGER,
    resubmission_due_at TEXT,is_absence_score INTEGER);
CREATE TABLE learning_stage_exam_attempts(id INTEGER,assignment_id INTEGER,class_offering_id INTEGER,student_id INTEGER,status TEXT,stage_key TEXT);
INSERT INTO learning_stage_exam_attempts VALUES(1,4,1,7,'generated','foundation'),(2,5,1,11,'generated','enlightenment');
CREATE TABLE class_offering_sessions(id INTEGER,class_offering_id INTEGER,title TEXT,session_date TEXT,academic_section_text TEXT,
    order_index INTEGER,learning_material_id INTEGER);
INSERT INTO class_offering_sessions VALUES(1,1,'公开课次','2026-09-05','4-5',1,NULL),(2,2,'其他班课次','2026-09-05','1-2',1,NULL);
CREATE TABLE teacher_academic_course_exam_items(id INTEGER,class_offering_id INTEGER,sync_status TEXT);
CREATE TABLE classroom_todos(id INTEGER,class_offering_id INTEGER,owner_role TEXT,owner_user_pk INTEGER,deleted_at TEXT,title TEXT,
    metadata_json TEXT,completed_at TEXT,due_at TEXT,start_at TEXT,notes TEXT);
INSERT INTO classroom_todos VALUES(1,1,'student',7,NULL,'本人待办','{}',NULL,NULL,NULL,''),
    (2,1,'student',11,NULL,'同学秘密待办','{}',NULL,NULL,NULL,''),
    (3,2,'student',7,NULL,'未授权班私有待办','{}',NULL,NULL,NULL,''),
    (4,1,'teacher',1,NULL,'任课教师私有待办','{}',NULL,NULL,NULL,'');
CREATE TABLE polls(id INTEGER,title TEXT,status TEXT,deadline_at TEXT,owner_role TEXT,owner_user_pk INTEGER,audience_scope TEXT);
INSERT INTO polls VALUES(1,'全班投票','active',NULL,'teacher',1,'class'),(2,'他人私密投票','active',NULL,'student',11,'custom'),
    (3,'本人受邀投票','active',NULL,'student',11,'custom'),(4,'他人投票草稿','draft',NULL,'student',11,'class');
CREATE TABLE poll_assignments(poll_id INTEGER,class_offering_id INTEGER);
INSERT INTO poll_assignments VALUES(1,1),(2,1),(3,1),(4,1);
CREATE TABLE poll_ballots(id INTEGER,poll_id INTEGER,voter_id INTEGER);
CREATE TABLE poll_participants(poll_id INTEGER,student_id INTEGER);
INSERT INTO poll_participants VALUES(2,11),(3,7);
CREATE TABLE teacher_calendar_events(id INTEGER,teacher_id INTEGER,source_type TEXT,status TEXT,deleted_at TEXT,title TEXT,
    starts_at TEXT,due_at TEXT,ends_at TEXT);
CREATE TABLE student_password_reset_requests(id INTEGER,class_id INTEGER,teacher_id INTEGER,status TEXT);
CREATE TABLE course_materials(id INTEGER,name TEXT,node_type TEXT);
CREATE TABLE course_material_assignments(class_offering_id INTEGER,material_id INTEGER);
CREATE TABLE learning_material_progress(class_offering_id INTEGER,material_id INTEGER,student_id INTEGER,completed INTEGER,
    max_scroll_ratio REAL,active_seconds INTEGER,accumulated_seconds INTEGER,last_viewed_at TEXT,updated_at TEXT);
"""


class DashboardWorkspaceAPITests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.user = {"id": 7, "name": "测试学生", "role": "student"}
        self.app = FastAPI()
        self.app.include_router(dashboard.router)
        self.app.dependency_overrides[dependencies.get_current_user_optional] = lambda: self.user
        self.client = TestClient(self.app)

        @contextmanager
        def connection():
            yield self.conn

        for target, replacement in (
            ("classroom_app.routers.ui_parts.dashboard.get_db_connection", connection),
            ("classroom_app.dependencies.get_db_connection", connection),
            ("classroom_app.dependencies._identity_cache_is_valid", lambda *_: False),
            ("classroom_app.dependencies._cache_valid_identity", lambda *_: None),
            ("classroom_app.dependencies.invalidate_session_for_user", lambda *_: None),
        ):
            mock = patch(target, replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.client.close)
        self.addCleanup(self.conn.close)

    def get(self, **params):
        response = self.client.get("/api/dashboard/workspace", params=params)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("success", response.json()["status"])
        return response.json()["workspace"]

    def test_main_class_student_has_only_own_manual_trial_and_poll_audience(self):
        result = self.get()
        titles = {i["title"] for i in result["all_items"]}
        self.assertTrue({"公开任务", "公开课次", "本人待办", "全班投票", "本人受邀投票"} <= titles)
        self.assertTrue({"其他班任务", "教师草稿", "同学秘密待办", "未授权班私有待办", "他人私密投票", "他人投票草稿", "任课教师私有待办"}.isdisjoint(titles))
        self.assertEqual([1], [i["source_id"] for i in result["all_items"] if i["kind"] == "stage"])
        self.assertEqual([1], [o["id"] for o in result["offering_options"]])
        self.assertEqual(2, result["offering_summaries"]["1"]["pending_task_count"])

    def test_merged_class_membership_uses_real_discovery_query(self):
        self.user = {"id": 8, "name": "合班学生", "role": "student"}
        result = self.get()
        self.assertEqual([1], [o["id"] for o in result["offering_options"]])
        self.assertTrue({"公开任务", "公开课次", "全班投票"} <= {i["title"] for i in result["all_items"]})
        self.assertFalse(any(i["kind"] in {"manual", "stage"} for i in result["all_items"]))
        self.assertNotIn("本人受邀投票", str(result))

    def test_inactive_student_and_teacher_are_denied_by_identity_dependency(self):
        for user_id, role in ((9, "student"), (3, "teacher")):
            self.user = {"id": user_id, "name": "已停用", "role": role}
            response = self.client.get("/api/dashboard/workspace")
            self.assertEqual(403, response.status_code)
            self.assertNotIn("workspace", response.json())

    def test_non_owner_teacher_cannot_select_foreign_offering(self):
        self.user = {"id": 2, "name": "另一教师", "role": "teacher"}
        result = self.get(offering_id=1)
        self.assertEqual([], result["all_items"])
        self.assertEqual(0, result["filtered_total"])
        self.assertEqual([2], [o["id"] for o in result["offering_options"]])
        self.assertNotIn("任课教师私有待办", str(result))
        self.assertNotIn("他人私密投票", str(result))

    def test_role_and_login_failures_return_json_not_empty_success(self):
        for user, status in ((None, 401), ({"id": 7, "role": "admin"}, 403)):
            self.user = user
            response = self.client.get("/api/dashboard/workspace")
            self.assertEqual(status, response.status_code)
            self.assertIn("detail", response.json())

    def test_large_result_pages_have_exact_total_no_missing_or_duplicate_keys(self):
        self.conn.executemany("INSERT INTO classroom_todos VALUES(?,1,'student',7,NULL,?,'{}',NULL,NULL,NULL,'')",
                              ((i, f"分页事项 {i}") for i in range(100, 245)))
        expected = 146  # 145 fixture rows plus the existing owner todo.
        first = self.get(kind="manual", limit=100)
        second = self.get(kind="manual", offset=100, limit=100)
        self.assertEqual(expected, first["filtered_total"])
        self.assertEqual(expected, second["filtered_total"])
        self.assertEqual((100, 46), (len(first["all_items"]), len(second["all_items"])))
        keys = [i["key"] for page in (first, second) for i in page["all_items"]]
        self.assertEqual(expected, len(set(keys)))
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        filtered = self.get(kind="manual", q="分页事项 1", offering_ids="1", limit=100)
        self.assertEqual(100, filtered["filtered_total"])

    def test_invalid_filters_rejected_and_valid_intersection_preserves_scope(self):
        for params in ({"limit": 101}, {"offset": -1}, {"kind": "unknown"}, {"offering_ids": "1,nope"}, {"date_scope": "today_or_secret"}):
            response = self.client.get("/api/dashboard/workspace", params=params)
            self.assertEqual(422, response.status_code)
        result = self.get(offering_id=2, offering_ids="1")
        self.assertEqual([], result["all_items"])
        self.assertEqual(0, result["filtered_total"])

    def test_cursor_reaches_end_beyond_legacy_offset_limit(self):
        self.conn.executemany("INSERT INTO classroom_todos VALUES(?,1,'student',7,NULL,?,'{}',NULL,NULL,NULL,'')",
                              ((i, f"大集合 {i}") for i in range(30000, 40245)))
        expected = sorted(["manual:1:1", *(f"manual:{i}:1" for i in range(30000, 40245))])[10000:]
        page = self.get(kind="manual", offset=10000, limit=100)
        keys = [i["key"] for i in page["all_items"]]
        while page["has_more"]:
            self.assertTrue(page["next_cursor"])
            page = self.get(kind="manual", cursor=page["next_cursor"], limit=100)
            keys.extend(i["key"] for i in page["all_items"])
        self.assertEqual(10246, page["filtered_total"])
        self.assertEqual(10200, page["offset"])
        self.assertEqual(expected, keys)
        self.assertIsNone(page["next_cursor"])

    def test_cursor_is_bound_to_actor_filters_and_time_boundary(self):
        from datetime import datetime
        with patch("classroom_app.services.dashboard_workspace_service.china_now", return_value=datetime(2026, 9, 5, 10)):
            page = self.get(limit=1)
            token = page["next_cursor"]
            second = self.get(cursor=token, limit=1)
            self.assertNotEqual(page["all_items"][0]["key"], second["all_items"][0]["key"])
            for params in ({"cursor": token + "tampered"}, {"cursor": token, "kind": "manual"}):
                self.assertEqual(400, self.client.get("/api/dashboard/workspace", params=params).status_code)
            self.user = {"id": 8, "name": "合班学生", "role": "student"}
            self.assertEqual(400, self.client.get("/api/dashboard/workspace", params={"cursor": token}).status_code)
        self.user = {"id": 7, "name": "测试学生", "role": "student"}
        with patch("classroom_app.services.dashboard_workspace_service.china_now", return_value=datetime(2026, 9, 6)):
            self.assertEqual(409, self.client.get("/api/dashboard/workspace", params={"cursor": token}).status_code)

    def test_valid_cursor_cannot_keep_access_after_merged_membership_is_revoked(self):
        self.user = {"id": 8, "name": "合班学生", "role": "student"}
        page = self.get(limit=1)
        self.assertTrue(page["next_cursor"])
        self.conn.execute("DELETE FROM class_offering_class_links WHERE offering_id=1 AND class_id=2")
        result = self.get(cursor=page["next_cursor"], limit=1)
        self.assertEqual([], result["all_items"])
        self.assertEqual([], result["focus_items"])
        self.assertEqual(0, result["total"])

    def test_endpoint_does_not_run_dashboard_side_effects_or_any_writes(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        with patch("classroom_app.routers.ui_parts.dashboard.build_dashboard_context", side_effect=AssertionError("full builder must not run")):
            self.get()
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements))

    def prepare_calendar(self):
        self.conn.executescript("""
            ALTER TABLE teachers ADD COLUMN school_code TEXT DEFAULT 'test';
            ALTER TABLE teachers ADD COLUMN school_name TEXT DEFAULT '测试学校';
            ALTER TABLE teachers ADD COLUMN college TEXT DEFAULT '';
            ALTER TABLE teachers ADD COLUMN department TEXT DEFAULT '';
            CREATE TABLE academic_semesters(id INTEGER, teacher_id INTEGER, school_code TEXT, school_name TEXT, name TEXT,
                start_date TEXT, end_date TEXT, week_count INTEGER, calendar_sync_status TEXT, calendar_sync_at TEXT,
                calendar_sync_message TEXT, calendar_source_summary_json TEXT, created_at TEXT, updated_at TEXT);
            INSERT INTO academic_semesters VALUES(1,1,'test','测试学校','2026秋','2026-09-01','2027-01-15',20,'','','','[]','','');
            CREATE TABLE academic_semester_calendar_days(semester_id INTEGER,date TEXT,week_index INTEGER,weekday INTEGER,
                day_type TEXT,label TEXT,source TEXT,source_url TEXT,confidence REAL,metadata_json TEXT);
            ALTER TABLE assignments ADD COLUMN late_submission_enabled INTEGER DEFAULT 0;
            ALTER TABLE assignments ADD COLUMN late_submission_until TEXT;
            UPDATE assignments SET availability_mode='countdown',auto_close=1,due_at='2026-09-04T23:59',
                late_submission_enabled=1,late_submission_until='2026-09-07T23:59' WHERE id=1;
            UPDATE class_offering_sessions SET session_date='2026-09-04' WHERE id=1;
        """)

    def calendar(self):
        from datetime import datetime
        with patch("classroom_app.services.academic_service.china_now", return_value=datetime(2026, 9, 5, 10)):
            response = self.client.get("/api/dashboard/calendar")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["calendar"]

    def test_calendar_uses_current_late_deadline_and_schedule_history_from_real_source(self):
        from datetime import datetime
        self.prepare_calendar()
        calendar = self.calendar()
        overview = calendar["semesters"][0]["todo_overview"]
        items = {i["workspace_key"]: i for i in overview["items"]}
        with patch("classroom_app.services.dashboard_workspace_service.china_now", return_value=datetime(2026, 9, 5, 10)):
            workspace = self.get()
        for item in workspace["all_items"]:
            row = items[item["key"]]
            self.assertEqual((item["status"], item["is_actionable"], item["is_completed"], item["effective_due_at"]),
                             (row["status"], row["is_actionable"], row["is_completed"], row["effective_due_at"]))
        late = items["assignment:1:1"]
        self.assertEqual("补交截止 9月7日 23:59", late["deadline_label"])
        self.assertEqual("2026-09-07T23:59:00+08:00", late["due_at"])
        self.assertEqual("2026-09-07", late["effective_start_date"])
        lesson = items["lesson:1:1"]
        self.assertTrue(lesson["is_schedule"] and lesson["date_only"])
        self.assertFalse(lesson["is_completed"] or lesson["is_actionable"])
        self.assertEqual("已结束", lesson["status_label"])
        self.assertEqual("4-5", lesson["time_label"])
        self.assertTrue(all(lesson[k] == "" for k in ("due_at", "due_time_label", "deadline_label", "effective_due_at", "effective_start_at")))
        self.assertEqual(workspace["pending_total"], overview["summary"]["open_count"])
        self.assertTrue(all(i["canonical_workspace"] for w in overview["weeks"] for i in w["todos"]))

    def test_calendar_reads_all_rows_once_and_exceeds_workspace_page_cap(self):
        self.prepare_calendar()
        self.conn.executemany("INSERT INTO classroom_todos VALUES(?,1,'student',7,NULL,?,'{}',NULL,'2026-09-08T14:00',NULL,'')",
                              ((i, f"日历事项 {i}") for i in range(100, 245)))
        trace = []
        self.conn.set_trace_callback(trace.append)
        with patch("classroom_app.routers.ui_parts.dashboard.build_dashboard_context", side_effect=AssertionError("no full context")):
            calendar = self.calendar()
        rows = calendar["semesters"][0]["todo_overview"]["items"]
        self.assertEqual(146, sum(i["is_manual"] for i in rows))
        self.assertEqual(len(rows), len({i["workspace_key"] for i in rows}))
        self.assertEqual(1, sum("SELECT * FROM classroom_todos WHERE owner_role" in sql for sql in trace))
        self.assertTrue(all(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in trace))
        self.assertNotIn("同学秘密", str(calendar))
        self.assertNotIn("他人私密投票", str(calendar))

    def test_calendar_merged_membership_and_revocation_apply_to_semesters_and_facts(self):
        self.prepare_calendar()
        self.user = {"id": 8, "name": "合班学生", "role": "student"}
        calendar = self.calendar()
        self.assertEqual([1], [s["id"] for s in calendar["semesters"]])
        self.assertEqual({1}, {i["class_offering_id"] for i in calendar["semesters"][0]["todo_overview"]["items"]})
        self.assertNotIn("本人待办", str(calendar))
        self.conn.execute("DELETE FROM class_offering_class_links WHERE class_id=2")
        self.assertEqual([], self.calendar()["semesters"])

    def test_calendar_private_teacher_todos_and_role_failures_keep_authorization(self):
        self.prepare_calendar()
        self.conn.execute("UPDATE classroom_todos SET class_offering_id=NULL,due_at='2026-09-06T09:00' WHERE id=4")
        self.user = {"id": 1, "name": "教师", "role": "teacher"}
        calendar = self.calendar()
        todo = next(i for i in calendar["semesters"][0]["todo_overview"]["items"] if i["source_type"] == "manual")
        self.assertEqual((4, 0, True), (todo["source_id"], todo["class_offering_id"], todo["can_complete"]))
        self.user = {"id": 2, "name": "另一教师", "role": "teacher"}
        self.assertNotIn("任课教师私有待办", str(self.calendar()))
        for user, code in ((None, 401), ({"id": 9, "role": "student"}, 403), ({"id": 7, "role": "admin"}, 403)):
            self.user = user
            self.assertEqual(code, self.client.get("/api/dashboard/calendar").status_code)

    def test_calendar_and_workspace_share_submission_gate_for_future_start(self):
        from datetime import datetime
        self.prepare_calendar()
        self.conn.execute("UPDATE assignments SET starts_at='2026-09-06T09:00',due_at='2026-09-07T12:00' WHERE id=1")
        with patch("classroom_app.services.dashboard_workspace_service.china_now", return_value=datetime(2026, 9, 5, 10)):
            row = next(i for i in self.get()["all_items"] if i["key"] == "assignment:1:1")
        # The existing published/countdown policy checks due_at, not starts_at.
        self.assertTrue(row["is_actionable"])
        self.assertNotEqual("not_started", row["status"])
        calendar_row = next(i for i in self.calendar()["semesters"][0]["todo_overview"]["items"] if i["workspace_key"] == row["key"])
        self.assertEqual(row["is_actionable"], calendar_row["is_actionable"])

    def test_calendar_exact_exam_end_and_aware_time_keep_schedule_semantics(self):
        self.prepare_calendar()
        self.conn.executescript("""
            ALTER TABLE teacher_academic_course_exam_items ADD COLUMN starts_at TEXT;
            ALTER TABLE teacher_academic_course_exam_items ADD COLUMN ends_at TEXT;
            ALTER TABLE teacher_academic_course_exam_items ADD COLUMN course_name TEXT;
            ALTER TABLE teacher_academic_course_exam_items ADD COLUMN location TEXT;
            INSERT INTO teacher_academic_course_exam_items VALUES(1,1,'active','2026-09-05T01:00:00Z','2026-09-05T02:00:00Z','网络考试','B310');
        """)
        row = next(i for i in self.calendar()["semesters"][0]["todo_overview"]["items"] if i["source_type"] == "academic_exam")
        self.assertEqual("09:00–10:00", row["time_label"])
        self.assertEqual("2026-09-05T10:00:00+08:00", row["effective_end_at"])
        self.assertEqual("B310", row["location"])
        self.assertEqual("past", row["status"])
        self.assertFalse(row["is_actionable"] or row["is_completed"])
        self.assertEqual("", row["deadline_label"])


class DashboardMiniappCompatibilityTests(unittest.TestCase):
    def test_existing_home_envelope_values_override_and_agenda_cap_are_unchanged(self):
        from classroom_app.routers.mp import home
        stats = [{"label": "待完成", "value": 99, "note": "保留提示"},
                 {"label": "已提交", "value": 98}, {"label": "课程", "value": 2}]
        focus = {"title": "旧聚焦", "items": [{"href": "/classroom/1", "count": 3}]}
        agenda = [{"kind": "todo", "title": f"旧事项 {i}", "status": "upcoming", "starts_at": f"2026-09-{(i % 20) + 1:02}T10:00"} for i in range(45)]
        context = {"dashboard_stats": stats, "dashboard_focus": focus, "dashboard_agenda_events": agenda,
                   "dashboard_workspace": {"focus_items": [{"title": "web专用不得混入"}], "total": 1000}}
        conn = Mock()

        @contextmanager
        def connection():
            yield conn

        for role in ("student", "teacher"):
            with self.subTest(role=role), patch.object(home, "get_db_connection", connection), \
                    patch.object(home, "build_dashboard_context", return_value=context) as builder, \
                    patch.object(home, "load_student_task_buckets", return_value={"pending": [1, 2], "completed": [3]}):
                user = {"id": 7, "name": "契约测试", "role": role}
                result = home.mp_home(user=user)
                expected_stats = [{"label": "待完成", "value": 2, "note": "保留提示"},
                                  {"label": "已提交", "value": 1}, {"label": "课程", "value": 2}] if role == "student" else stats
                self.assertEqual({"success": True, "data": {"role": role, "user": {"id": 7, "name": "契约测试"},
                                  "stats": expected_stats, "focus": focus, "agenda": agenda[:40]}, "error": None}, result)
                builder.assert_called_once_with(conn, user)
                self.assertNotIn("workspace", str(result))


if __name__ == "__main__":
    unittest.main()
