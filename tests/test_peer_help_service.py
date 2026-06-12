from __future__ import annotations

import json
import sqlite3
import unittest

from classroom_app.services.peer_help_service import mark_chat_message_useful


def _build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            role TEXT DEFAULT 'teacher',
            is_super_admin INTEGER DEFAULT 0
        );
        CREATE TABLE classes (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            class_id INTEGER,
            course_id INTEGER,
            teacher_id INTEGER
        );
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            class_id INTEGER,
            name TEXT,
            enrollment_status TEXT DEFAULT 'active'
        );
        CREATE TABLE chat_logs (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            user_id TEXT,
            user_name TEXT,
            user_role TEXT,
            message TEXT,
            quote_message_id INTEGER,
            logged_at TEXT,
            timestamp TEXT
        );
        CREATE TABLE classroom_behavior_events (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            user_pk INTEGER,
            user_role TEXT,
            display_name TEXT,
            action_type TEXT,
            summary_text TEXT,
            payload_json TEXT,
            created_at TEXT
        );
        CREATE TABLE classroom_behavior_states (
            class_offering_id INTEGER,
            user_pk INTEGER,
            user_role TEXT,
            total_activity_count INTEGER NOT NULL DEFAULT 0,
            last_event_at TEXT,
            last_page_key TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (class_offering_id, user_pk, user_role)
        );
        """
    )
    conn.execute("INSERT INTO teachers (id, name) VALUES (1, 'Teacher')")
    conn.execute("INSERT INTO classes (id, name) VALUES (1, 'Class')")
    conn.execute("INSERT INTO courses (id, name) VALUES (1, 'Course')")
    conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (1, 1, 1, 1)")
    conn.execute("INSERT INTO students (id, class_id, name) VALUES (7, 1, 'Asker')")
    conn.execute("INSERT INTO students (id, class_id, name) VALUES (8, 1, 'Helper')")
    conn.execute(
        """
        INSERT INTO chat_logs (
            id, class_offering_id, user_id, user_name, user_role, message, logged_at, timestamp
        )
        VALUES (101, 1, '7', 'Asker', 'student', 'How do I solve this?', '2026-06-09T09:00:00', '09:00')
        """
    )
    conn.execute(
        """
        INSERT INTO chat_logs (
            id, class_offering_id, user_id, user_name, user_role, message, quote_message_id, logged_at, timestamp
        )
        VALUES (102, 1, '8', 'Helper', 'student', 'Use the course formula step by step.', 101, '2026-06-09T09:02:00', '09:02')
        """
    )
    conn.execute(
        """
        INSERT INTO chat_logs (
            id, class_offering_id, user_id, user_name, user_role, message, quote_message_id, logged_at, timestamp
        )
        VALUES (103, 1, '8', 'Helper', 'student', 'Here is another example.', 101, '2026-06-09T09:03:00', '09:03')
        """
    )
    conn.commit()
    return conn


class PeerHelpServiceTests(unittest.TestCase):
    def test_student_can_mark_reply_to_own_question_once_per_pair_day(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        asker = {"id": 7, "role": "student", "name": "Asker"}

        first = mark_chat_message_useful(conn, 1, 102, asker)
        self.assertTrue(first["counted"])
        event = conn.execute("SELECT * FROM classroom_behavior_events WHERE id = ?", (first["event_id"],)).fetchone()
        self.assertEqual(event["user_pk"], 8)
        self.assertEqual(event["action_type"], "peer_help")
        payload = json.loads(event["payload_json"])
        self.assertEqual(payload["marked_by_user_pk"], 7)
        self.assertEqual(payload["helper_student_id"], 8)

        second = mark_chat_message_useful(conn, 1, 103, asker)
        self.assertFalse(second["counted"])
        self.assertEqual(second["reason"], "pair_daily_limit")
        total = conn.execute("SELECT COUNT(*) AS total FROM classroom_behavior_events").fetchone()["total"]
        self.assertEqual(total, 1)

    def test_teacher_can_mark_student_reply_without_quote(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        conn.execute(
            """
            INSERT INTO chat_logs (
                id, class_offering_id, user_id, user_name, user_role, message, logged_at, timestamp
            )
            VALUES (104, 1, '8', 'Helper', 'student', 'I can explain this to everyone.', '2026-06-09T09:04:00', '09:04')
            """
        )
        teacher = {"id": 1, "role": "teacher", "name": "Teacher"}

        result = mark_chat_message_useful(conn, 1, 104, teacher)
        self.assertTrue(result["counted"])

        duplicate = mark_chat_message_useful(conn, 1, 104, teacher)
        self.assertFalse(duplicate["counted"])
        self.assertEqual(duplicate["reason"], "duplicate_message")


if __name__ == "__main__":
    unittest.main()
