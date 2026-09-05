"""Student deck contract against real identity, membership and session SQL."""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.routers.ui_parts import dashboard
from classroom_app.services.student_course_schedule_service import build_student_course_schedule_overview


SCHEMA = """
CREATE TABLE teachers(id INTEGER,name TEXT,is_active INTEGER);
INSERT INTO teachers VALUES(1,'教师甲',1),(2,'教师乙',1);
CREATE TABLE students(id INTEGER,class_id INTEGER,enrollment_status TEXT);
INSERT INTO students VALUES(7,1,'active'),(8,2,'active'),(9,1,'suspended'),(10,3,'active'),(11,4,'active');
CREATE TABLE classes(id INTEGER,name TEXT,description TEXT);
INSERT INTO classes VALUES(1,'主班',''),(2,'合班',''),(3,'其他班',''),(4,'无课班','');
CREATE TABLE courses(id INTEGER,name TEXT,description TEXT,credits INTEGER);
INSERT INTO courses VALUES(1,'网络','',2),(2,'其他课程','',2);
CREATE TABLE class_offerings(id INTEGER,class_id INTEGER,course_id INTEGER,teacher_id INTEGER,semester TEXT,semester_id INTEGER,
    combined_class_names TEXT);
INSERT INTO class_offerings VALUES(1,1,1,1,'2026-2027第一学期',1,'主班、合班'),
    (2,3,2,2,'2026-2027第一学期',2,'其他班');
CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
INSERT INTO class_offering_class_links VALUES(1,2);
CREATE TABLE academic_semesters(id INTEGER,teacher_id INTEGER,school_code TEXT,school_name TEXT,name TEXT,
    start_date TEXT,end_date TEXT,week_count INTEGER,calendar_sync_status TEXT,calendar_sync_at TEXT,
    calendar_sync_message TEXT,calendar_source_summary_json TEXT,created_at TEXT,updated_at TEXT);
INSERT INTO academic_semesters VALUES(1,1,'test','测试学校','2026-2027第一学期','2026-08-31','2027-01-15',20,'','','','[]','',''),
    (2,2,'other','其他学校','2026-2027第一学期','2026-08-31','2027-01-15',20,'','','','[]','',''),
    (3,2,'other','其他学校','2030-2031第一学期','2030-08-26','2031-01-10',20,'','','','[]','','');
CREATE TABLE academic_semester_calendar_days(semester_id INTEGER,date TEXT,week_index INTEGER,weekday INTEGER,
    day_type TEXT,label TEXT,source TEXT,source_url TEXT,confidence REAL,metadata_json TEXT);
CREATE TABLE class_offering_sessions(id INTEGER,class_offering_id INTEGER,session_date TEXT,order_index INTEGER,
    academic_section_text TEXT,academic_location TEXT,schedule_metadata_json TEXT,schedule_status TEXT,
    week_index INTEGER,weekday INTEGER);
INSERT INTO class_offering_sessions VALUES(1,1,'2026-09-04',1,'4-5','知新楼B310','{}','scheduled',1,4),
    (2,2,'2026-09-04',1,'2-3','别班秘密教室','{}','scheduled',1,4),
    (3,1,'2026-09-12',2,'6-7','调课教室','{}','scheduled',1,0),
    (4,1,'2026-09-18',3,'4-5','已取消教室','{}','cancelled',3,4),
    (5,1,'2026-09-25',4,'','待定教室','{}','scheduled',4,4);
ALTER TABLE class_offerings ADD COLUMN schedule_info TEXT DEFAULT '';
ALTER TABLE class_offerings ADD COLUMN created_at TEXT DEFAULT '';
"""


class StudentCourseScheduleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.user = {"id": 7, "name": "测试学生", "role": "student"}
        app = FastAPI()
        app.include_router(dashboard.router)
        app.dependency_overrides[dependencies.get_current_user_optional] = lambda: self.user
        self.client = TestClient(app)

        @contextmanager
        def connection():
            yield self.conn

        for target, replacement in (
            ("classroom_app.routers.ui_parts.dashboard.get_db_connection", connection),
            ("classroom_app.dependencies.get_db_connection", connection),
            ("classroom_app.dependencies._identity_cache_is_valid", lambda *_: False),
            ("classroom_app.dependencies._cache_valid_identity", lambda *_: None),
            ("classroom_app.dependencies.invalidate_session_for_user", lambda *_: None),
            ("classroom_app.services.student_course_schedule_service.china_now", lambda: datetime(2026, 9, 5, 10)),
        ):
            mock = patch(target, replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.client.close)
        self.addCleanup(self.conn.close)

    def get(self, **params):
        response = self.client.get("/api/dashboard/course-schedule/overview", params=params)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["overview"]

    def test_real_dates_authorized_scope_and_cancelled_sessions(self):
        result = self.get()
        self.assertEqual(("2026-2027", "1", 1), (
            result["selected_term"]["year"], result["selected_term"]["term"], result["selected_term"]["focus_week"],
        ))
        self.assertEqual(["2026-2027第一学期"], [entry["label"] for entry in result["terms"]])
        lessons = [lesson for week in result["weeks"] for lesson in week["lessons"]]
        self.assertEqual([1, 3], [lesson["id"] for lesson in lessons])
        self.assertEqual({1}, {lesson["class_offering_id"] for lesson in lessons})
        self.assertEqual({"/classroom/1"}, {lesson["classroom_url"] for lesson in lessons})
        self.assertTrue(all(not lesson["create_url"] for lesson in lessons))
        self.assertEqual((2, 6), (result["weeks"][1]["week_index"], result["weeks"][1]["lessons"][0]["weekday"]))
        self.assertEqual([6, 7], result["weeks"][1]["lessons"][0]["sections"])
        self.assertEqual(1, result["summary"]["unpositioned_count"])
        self.assertEqual(4, result["summary"]["total_hours"])
        self.assertNotIn("秘密", str(result))
        self.assertNotIn("已取消", str(result))

    def test_merged_class_and_membership_revocation_are_live(self):
        self.user = {"id": 8, "role": "student"}
        self.assertTrue(self.get()["has_data"])
        self.conn.execute("DELETE FROM class_offering_class_links WHERE offering_id=1 AND class_id=2")
        self.assertEqual([], self.get()["terms"])
        self.user = {"id": 7, "role": "student"}
        self.assertTrue(self.get()["has_data"])

    def test_no_enrollment_foreign_semester_and_malformed_queries_do_not_leak(self):
        for params in ({"year": "2030-2031", "term": "1"}, {"year": "' OR 1=1--", "term": "1"}, {"term": "1"}):
            result = self.get(**params)
            self.assertEqual([], result["weeks"])
            self.assertIsNone(result["selected_term"])
            self.assertNotIn("其他学校", str(result))
        self.user = {"id": 11, "role": "student"}
        self.assertEqual([], self.get()["weeks"])
        self.assertEqual([], self.get()["terms"])
        self.assertEqual(422, self.client.get("/api/dashboard/course-schedule/overview", params={"year": "x" * 33}).status_code)

    def test_identity_and_role_fail_closed(self):
        for user, expected in (
            (None, 401), ({"id": 9, "role": "student"}, 403), ({"id": 999, "role": "student"}, 403),
            ({"id": 1, "role": "teacher"}, 403), ({"id": 7, "role": "admin"}, 403),
        ):
            with self.subTest(user=user):
                self.user = user
                response = self.client.get("/api/dashboard/course-schedule/overview")
                self.assertEqual(expected, response.status_code, response.text)

    def test_other_students_receive_only_their_platform_offerings(self):
        self.user = {"id": 10, "role": "student"}
        result = self.get()
        lessons = [lesson for week in result["weeks"] for lesson in week["lessons"]]
        self.assertEqual([2], [lesson["class_offering_id"] for lesson in lessons])
        self.assertNotIn("主班", str(result))
        self.assertNotIn("2030", str(result))

    def test_cross_teacher_same_term_is_combined_without_n_plus_one_or_writes(self):
        # Twenty additional authorized offerings, across two teachers, one real term.
        for oid in range(10, 30):
            self.conn.execute("INSERT INTO class_offerings(id,class_id,course_id,teacher_id,semester,semester_id,combined_class_names) VALUES(?,1,1,2,'2026-2027第1学期',2,'主班')", (oid,))
            self.conn.execute("INSERT INTO class_offering_sessions VALUES(?,?, '2026-09-05',1,'8-9','本班教室','{}','scheduled',1,5)", (oid, oid))
        self.conn.commit()
        statements = []
        self.conn.execute("PRAGMA query_only = ON")
        self.conn.set_trace_callback(statements.append)
        result = build_student_course_schedule_overview(self.conn, 7, now=datetime(2026, 9, 5, 10))
        self.conn.set_trace_callback(None)
        self.assertEqual(4, len(statements), statements)
        self.assertTrue(all(query.lstrip().upper().startswith("SELECT") for query in statements))
        lessons = [lesson for week in result["weeks"] for lesson in week["lessons"]]
        self.assertEqual({1, *range(10, 30)}, {lesson["class_offering_id"] for lesson in lessons})
        self.assertEqual(1, len(result["terms"]))

    def test_custom_semester_and_missing_time_do_not_fabricate_a_period(self):
        self.conn.execute("UPDATE academic_semesters SET name='校内实践' WHERE id=1")
        self.conn.execute("UPDATE class_offerings SET semester='校内实践' WHERE id=1")
        result = self.get(year="semester-1", term="0")
        self.assertEqual("校内实践", result["selected_term"]["label"])
        self.assertEqual(1, result["summary"]["unpositioned_count"])
        self.conn.execute("UPDATE class_offering_sessions SET schedule_metadata_json='{\"section_text\":\"10-11\"}' WHERE id=5")
        result = self.get()
        lesson = next(lesson for week in result["weeks"] for lesson in week["lessons"] if lesson["id"] == 5)
        self.assertEqual([10, 11], lesson["sections"])
        self.assertEqual(0, result["summary"]["unpositioned_count"])

    def test_explicit_ended_term_focuses_its_last_week(self):
        result = build_student_course_schedule_overview(self.conn, 7, year="2026-2027", term="1", now=datetime(2027, 2, 1))
        self.assertEqual("ended", result["selected_term"]["status"])
        self.assertEqual(len(result["weeks"]), result["selected_term"]["focus_week"])
        self.assertFalse(any(week["is_current"] for week in result["weeks"]))


if __name__ == "__main__":
    unittest.main()
