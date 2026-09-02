"""小程序课堂 tab 聚合 build_live_overview 的单测（sqlite 最小 schema）。"""

import sqlite3
import unittest

from classroom_app.routers.mp.classroom import build_live_overview


def _seed(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT, description TEXT, credits REAL, department TEXT);
        CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT, description TEXT, department TEXT);
        CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER, enrollment_status TEXT);
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER, teacher_id INTEGER,
            semester TEXT, semester_id INTEGER, schedule_info TEXT, first_class_date TEXT,
            weekly_schedule_json TEXT, created_at TEXT
        );
        CREATE TABLE class_offering_class_links (offering_id INTEGER, class_id INTEGER);
        CREATE TABLE classroom_live_activities (id INTEGER PRIMARY KEY, class_offering_id INTEGER, status TEXT);
        CREATE TABLE classroom_live_help_signals (
            id INTEGER PRIMARY KEY, class_offering_id INTEGER, student_id INTEGER,
            signal_type TEXT, status TEXT
        );
        INSERT INTO teachers VALUES (1, '张老师');
        INSERT INTO courses VALUES (1, '计算机网络', '', 3, '信息学院'), (2, 'Python', '', 2, '信息学院');
        INSERT INTO classes VALUES (10, '电信2501', '', '信息学院'), (11, '软工2501', '', '信息学院');
        INSERT INTO students VALUES (100, 10, 'active'), (101, 11, 'active');
        INSERT INTO class_offerings VALUES
            (1, 10, 1, 1, '2025-2026第一学期', NULL, '', '', '', '2026-08-01'),
            (2, 10, 2, 1, '2025-2026第一学期', NULL, '', '', '', '2026-08-02');
        -- offering 2 合班挂链软工2501
        INSERT INTO class_offering_class_links VALUES (2, 11);
        INSERT INTO classroom_live_activities VALUES (1, 1, 'active'), (2, 1, 'closed'), (3, 2, 'active');
        INSERT INTO classroom_live_help_signals VALUES (1, 1, 100, 'hand', 'active'), (2, 1, 101, 'help', 'cleared');
        """
    )


def _seed_polls(conn: sqlite3.Connection) -> None:
    import classroom_app.db.schema_polls as schema_polls

    # 进程级 ready 标志可能已被导入链上的 init_database 置位，内存库需重建
    schema_polls._SCHEMA_READY = False
    schema_polls.ensure_poll_schema(conn)
    schema_polls._SCHEMA_READY = False
    conn.execute(
        """
        INSERT INTO polls (id, title, status, deadline_at, owner_role, owner_user_pk, created_at, updated_at)
        VALUES (1, '课间投票', 'active', '', 'teacher', 1, '2026-08-31', '2026-08-31'),
               (2, '过期投票', 'active', '2020-01-01 00:00:00', 'teacher', 1, '2026-08-31', '2026-08-31'),
               (3, '草稿', 'draft', '', 'teacher', 1, '2026-08-31', '2026-08-31')
        """
    )
    conn.execute(
        "INSERT INTO poll_assignments (poll_id, class_offering_id) VALUES (1, 1), (2, 1), (3, 2)"
    )


class BuildLiveOverviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _seed(self.conn)
        _seed_polls(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_teacher_overview_counts_and_live_sort(self):
        data = build_live_overview(self.conn, {"id": 1, "role": "teacher", "name": "张老师"})
        by_id = {item["id"]: item for item in data["offerings"]}
        self.assertEqual(set(by_id), {1, 2})
        # offering 1：1 个进行中互动（closed 不算）、1 个进行中举手（cleared 不算）
        self.assertEqual(by_id[1]["active_activity_count"], 1)
        self.assertEqual(by_id[1]["active_signal_count"], 1)
        self.assertTrue(by_id[1]["is_live"])
        # 过期投票（deadline 已过）与草稿都不计入
        self.assertEqual(by_id[1]["active_poll_count"], 1)
        self.assertEqual(by_id[2]["active_poll_count"], 0)
        # 合班：offering 2 学生数应含挂链班级
        self.assertEqual(by_id[2]["student_count"], 2)
        self.assertEqual(data["live_count"], 2)

    def test_student_sees_membership_offerings_and_my_signal(self):
        # 软工2501 的学生只通过挂链看到 offering 2
        data = build_live_overview(self.conn, {"id": 101, "role": "student", "name": "小李"})
        self.assertEqual([item["id"] for item in data["offerings"]], [2])
        self.assertEqual(data["offerings"][0]["teacher_name"], "张老师")
        self.assertEqual(data["offerings"][0]["active_signal_count"], 0)

        mine = build_live_overview(self.conn, {"id": 100, "role": "student", "name": "小王"})
        by_id = {item["id"]: item for item in mine["offerings"]}
        self.assertEqual(by_id[1]["my_signal"], "hand")
        self.assertEqual(by_id[2]["my_signal"], "")

    def test_no_offerings(self):
        data = build_live_overview(self.conn, {"id": 999, "role": "student", "name": "x"})
        self.assertEqual(data, {"role": "student", "offerings": [], "live_count": 0})


if __name__ == "__main__":
    unittest.main()
