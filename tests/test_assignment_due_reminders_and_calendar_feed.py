"""作业截止临期提醒 + 个人日历订阅 feed 的单元测试（sqlite）。"""

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services import scheduled_task_handlers as handlers
from classroom_app.services.assignment_reminder_service import (
    ASSIGNMENT_DUE_REMINDER_TASK_KIND,
    cancel_assignment_due_reminders,
    sync_assignment_due_reminders,
)
from classroom_app.services.calendar_feed_service import (
    build_ics_for_user,
    get_or_create_feed_token,
    reset_feed_token,
    resolve_feed_token,
)
from classroom_app.services.scheduled_task_service import ensure_scheduler_schema
from classroom_app.services.academic_service import china_now


def _now():
    """与服务侧同锚点（中国时间），测试机本地时区无关。"""
    return china_now().replace(tzinfo=None)

TEACHER_ID = 907
CLASS_ID = 901
COURSE_ID = 901
OFFERING_ID = 901
ASSIGNMENT_ID = 9001


def _seed_base(conn):
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TEACHER_ID, "Teacher", "teacher907@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute(
        "INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)",
        (CLASS_ID, "测试班级", TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)",
        (COURSE_ID, "计算机网络", TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        (9101, "S9101", "Alice", CLASS_ID),
    )
    conn.execute(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        (9102, "S9102", "Bob", CLASS_ID),
    )


def _seed_assignment(conn, *, due_at: str, status: str = "published"):
    conn.execute(
        """
        INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, due_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ASSIGNMENT_ID,
            COURSE_ID,
            OFFERING_ID,
            "第1章作业",
            "完成练习",
            status,
            due_at,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM scheduled_tasks WHERE task_kind = ?", (ASSIGNMENT_DUE_REMINDER_TASK_KIND,)),
        ("DELETE FROM message_center_notifications WHERE ref_type = 'assignment_due'", ()),
        ("DELETE FROM submissions WHERE assignment_id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM assignments WHERE id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM students WHERE id IN (9101, 9102)", ()),
        ("DELETE FROM class_offerings WHERE id = ?", (OFFERING_ID,)),
        ("DELETE FROM courses WHERE id = ?", (COURSE_ID,)),
        ("DELETE FROM classes WHERE id = ?", (CLASS_ID,)),
        ("DELETE FROM teachers WHERE id = ?", (TEACHER_ID,)),
        ("DELETE FROM calendar_feed_tokens WHERE user_pk IN (9101, 9102)", ()),
    ):
        try:
            conn.execute(sql, params)
        except Exception:
            pass


class AssignmentDueReminderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            ensure_scheduler_schema(conn)
            _cleanup(conn)
            _seed_base(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def _reminder_rows(self, conn):
        return conn.execute(
            "SELECT dedupe_key, run_at, status FROM scheduled_tasks WHERE task_kind = ? AND status = 'pending' ORDER BY dedupe_key",
            (ASSIGNMENT_DUE_REMINDER_TASK_KIND,),
        ).fetchall()

    def test_sync_arms_both_windows_for_far_future_due(self):
        due = (_now() + timedelta(days=3)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            _seed_assignment(conn, due_at=due)
            sync_assignment_due_reminders(
                conn, ASSIGNMENT_ID, status="published", due_at=due,
                class_offering_id=OFFERING_ID, title="第1章作业",
            )
            rows = self._reminder_rows(conn)
            conn.commit()
        keys = [row["dedupe_key"] for row in rows]
        self.assertEqual(
            keys,
            [
                f"assignment-due-reminder:{ASSIGNMENT_ID}:24h",
                f"assignment-due-reminder:{ASSIGNMENT_ID}:2h",
            ],
        )

    def test_sync_skips_past_window_and_draft_cancels(self):
        # 距截止 6 小时：24h 窗口已过，只应布防 2h。
        due = (_now() + timedelta(hours=6)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            _seed_assignment(conn, due_at=due)
            sync_assignment_due_reminders(
                conn, ASSIGNMENT_ID, status="published", due_at=due,
                class_offering_id=OFFERING_ID, title="第1章作业",
            )
            keys = [row["dedupe_key"] for row in self._reminder_rows(conn)]
            self.assertEqual(keys, [f"assignment-due-reminder:{ASSIGNMENT_ID}:2h"])

            # 改回草稿 → 全部取消。
            sync_assignment_due_reminders(
                conn, ASSIGNMENT_ID, status="new", due_at=due,
                class_offering_id=OFFERING_ID, title="第1章作业",
            )
            self.assertEqual(self._reminder_rows(conn), [])
            conn.commit()

    def test_cancel_removes_all(self):
        due = (_now() + timedelta(days=2)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            _seed_assignment(conn, due_at=due)
            sync_assignment_due_reminders(
                conn, ASSIGNMENT_ID, status="published", due_at=due,
                class_offering_id=OFFERING_ID, title="第1章作业",
            )
            cancel_assignment_due_reminders(conn, ASSIGNMENT_ID)
            self.assertEqual(self._reminder_rows(conn), [])
            conn.commit()

    def test_handler_notifies_only_non_submitters_and_dedupes(self):
        due = (_now() + timedelta(hours=20)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            _seed_assignment(conn, due_at=due)
            # Alice 已提交，Bob 未提交。
            conn.execute(
                """
                INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ASSIGNMENT_ID, 9101, "Alice", "submitted", _now().isoformat(timespec="seconds")),
            )
            conn.commit()

        task = {
            "payload": {
                "assignment_id": ASSIGNMENT_ID,
                "window": "24h",
                "due_at": due,
            }
        }
        result = handlers.handle_assignment_due_reminder(task)
        self.assertIn("notified=1", result)

        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT recipient_user_pk FROM message_center_notifications WHERE ref_type = 'assignment_due'",
            ).fetchall()
        self.assertEqual([int(row["recipient_user_pk"]) for row in rows], [9102])

        # 再跑一次：ref 去重，不重复打扰。
        result_again = handlers.handle_assignment_due_reminder(task)
        self.assertIn("notified=0", result_again)

    def test_handler_skips_when_deadline_changed(self):
        due = (_now() + timedelta(hours=20)).isoformat(timespec="seconds")
        stale_due = (_now() + timedelta(hours=8)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            _seed_assignment(conn, due_at=due)
            conn.commit()
        result = handlers.handle_assignment_due_reminder(
            {"payload": {"assignment_id": ASSIGNMENT_ID, "window": "24h", "due_at": stale_due}}
        )
        self.assertIn("deadline changed", result)


class CalendarFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            _seed_base(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_token_lifecycle(self):
        with get_db_connection() as conn:
            token1 = get_or_create_feed_token(conn, role="student", user_pk=9101)
            token_same = get_or_create_feed_token(conn, role="student", user_pk=9101)
            self.assertEqual(token1, token_same)

            resolved = resolve_feed_token(conn, token1)
            self.assertEqual(resolved, {"role": "student", "user_pk": 9101})

            token2 = reset_feed_token(conn, role="student", user_pk=9101)
            self.assertNotEqual(token1, token2)
            self.assertIsNone(resolve_feed_token(conn, token1))
            self.assertEqual(
                resolve_feed_token(conn, token2), {"role": "student", "user_pk": 9101}
            )
            self.assertIsNone(resolve_feed_token(conn, "no-such-token"))
            conn.commit()

    def test_ics_contains_pending_assignment_and_omits_submitted(self):
        due = (_now() + timedelta(days=5)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            _seed_assignment(conn, due_at=due)
            conn.commit()

        with get_db_connection() as conn:
            ics_pending = build_ics_for_user(conn, role="student", user_pk=9102)
        self.assertIn("BEGIN:VCALENDAR", ics_pending)
        self.assertIn("第1章作业", ics_pending.replace("\r\n ", ""))
        self.assertIn("计算机网络", ics_pending.replace("\r\n ", ""))

        # 提交后（已完成且不可重交）不再出现在 feed 里。
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ASSIGNMENT_ID, 9102, "Bob", "graded", _now().isoformat(timespec="seconds")),
            )
            conn.commit()
        with get_db_connection() as conn:
            ics_done = build_ics_for_user(conn, role="student", user_pk=9102)
        self.assertNotIn("第1章作业", ics_done.replace("\r\n ", ""))

    def test_ics_line_folding_preserves_utf8(self):
        from classroom_app.services.calendar_feed_service import _fold_line

        long_line = "SUMMARY:" + "计算机网络课程超长标题" * 10
        folded = _fold_line(long_line)
        for physical in folded.split("\r\n"):
            self.assertLessEqual(len(physical.encode("utf-8")), 75)
        # 去掉折叠后内容不变。
        self.assertEqual(folded.replace("\r\n ", ""), long_line)


if __name__ == "__main__":
    unittest.main()
