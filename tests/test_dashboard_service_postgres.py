from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from classroom_app.services.dashboard_service import (
    _build_student_continue_action,
    _dashboard_course_visual,
    _dashboard_notice_text,
    _query_scalar,
    _student_cockpit_day_shape,
    _student_cockpit_greeting,
    _teacher_today_login_count_sql,
)


class _FakePostgresScalarCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakePostgresScalarConnection:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return _FakePostgresScalarCursor(self.row)


class DashboardServicePostgresTests(unittest.TestCase):
    def test_query_scalar_accepts_postgres_dict_count_row(self):
        conn = _FakePostgresScalarConnection({"row_count": "12"})

        value = _query_scalar(conn, "SELECT COUNT(*) AS row_count FROM students", ())

        self.assertEqual(12, value)

    def test_query_scalar_accepts_sqlite_tuple_row(self):
        conn = _FakePostgresScalarConnection((7,))

        value = _query_scalar(conn, "SELECT COUNT(*) FROM students", ())

        self.assertEqual(7, value)

    def test_teacher_today_login_count_sql_uses_postgres_date_cast(self):
        with patch("classroom_app.services.dashboard_service.get_configured_db_engine", return_value="postgres"):
            sql = _teacher_today_login_count_sql()

        self.assertIn("logged_at::date = CURRENT_DATE", sql)
        self.assertNotIn("date('now'", sql)

    def test_student_continue_action_prefers_recent_activity(self):
        action = _build_student_continue_action([
            {"id": 1, "course_name": "旧课堂", "last_activity_sort": 10, "pending_count": 3},
            {"id": 2, "course_name": "最近课堂", "last_activity_sort": 20, "pending_count": 0},
        ])

        self.assertEqual("/classroom/2", action["href"])
        self.assertEqual("继续学习", action["label"])
        self.assertIn("最近课堂", action["subtitle"])

    def test_student_continue_action_falls_back_to_pending_then_anchor(self):
        pending = _build_student_continue_action([
            {"id": 5, "course_name": "待完成课堂", "last_activity_sort": 0, "pending_count": 2},
        ])
        empty = _build_student_continue_action([])

        self.assertEqual("/classroom/5", pending["href"])
        self.assertEqual("#dashboard-class-list", empty["href"])
        self.assertEqual("查看课堂", empty["label"])

    def test_student_cockpit_greeting_uses_time_segment(self):
        self.assertEqual("早上好，小林", _student_cockpit_greeting(datetime(2026, 6, 12, 8), "小林"))
        self.assertEqual("中午好，小林", _student_cockpit_greeting(datetime(2026, 6, 12, 12), "小林"))
        self.assertEqual("下午好，小林", _student_cockpit_greeting(datetime(2026, 6, 12, 15), "小林"))
        self.assertEqual("晚上好，小林", _student_cockpit_greeting(datetime(2026, 6, 12, 22), "小林"))

    def test_student_cockpit_day_shape_counts_today_items(self):
        now = datetime(2026, 6, 12, 22, 30)
        shape = _student_cockpit_day_shape([
            {"kind": "lesson", "start_at": "2026-06-12 09:00:00"},
            {"kind": "assignment", "due_at": "2026-06-12 23:59:00"},
            {"kind": "assignment", "due_at": "2026-06-13 23:59:00"},
        ], now=now, open_count=2)

        self.assertIn("今天还有 1 节课", shape)
        self.assertIn("1 项截止", shape)
        self.assertIn("夜深了", shape)

    def test_dashboard_course_visual_is_stable_and_known(self):
        first = _dashboard_course_visual(42)
        second = _dashboard_course_visual(42)

        self.assertEqual(first, second)
        self.assertIn(first["tone"], {"indigo", "teal", "sky", "amber", "rose", "violet", "emerald", "slate"})
        self.assertIn(first["pattern"], {"grid", "dots", "diagonal", "rings"})

    def test_dashboard_notice_text_prefers_rank_notice_message(self):
        text = _dashboard_notice_text({
            "tier": "summit",
            "title": "Top",
            "message": "Readable rank message",
            "rank": 1,
        })

        self.assertEqual("Readable rank message", text)
        self.assertNotIn("{", text)
        self.assertEqual("fallback", _dashboard_notice_text({"rank": 1}, fallback="fallback"))


if __name__ == "__main__":
    unittest.main()
