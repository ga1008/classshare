from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
from classroom_app.services.cultivation_alert_service import (
    append_cultivation_alert_support_note,
    build_cultivation_alert_private_message,
    generate_cultivation_alerts,
    handle_cultivation_alert,
    list_cultivation_alerts,
)
from classroom_app.services.message_center_service import create_private_message


class CultivationAlertServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_cultivation_progress_schema(self.conn, engine="sqlite")
        self.conn.execute(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT,
                username TEXT,
                is_active INTEGER DEFAULT 1
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT,
                created_by_teacher_id INTEGER
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                sect_name TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                course_name TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                name TEXT,
                student_id_number TEXT,
                enrollment_status TEXT DEFAULT 'active'
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE student_shared_teacher_notes (
                student_id INTEGER PRIMARY KEY,
                note_text TEXT NOT NULL DEFAULT '',
                created_by_teacher_id INTEGER,
                updated_by_teacher_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        ensure_classroom_activity_schema(self.conn)
        self.conn.execute(
            """
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY,
                course_id INTEGER,
                title TEXT,
                status TEXT DEFAULT 'published',
                class_offering_id INTEGER,
                due_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT,
                student_pk_id INTEGER,
                student_name TEXT,
                status TEXT,
                submitted_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS classroom_behavior_states (
                class_offering_id INTEGER,
                user_pk INTEGER,
                user_role TEXT,
                total_activity_count INTEGER DEFAULT 0,
                last_event_at TEXT,
                online_accumulated_seconds INTEGER DEFAULT 0,
                focus_total_seconds INTEGER DEFAULT 0
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS classroom_behavior_events (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                user_pk INTEGER,
                user_role TEXT,
                action_type TEXT,
                summary_text TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                user_id TEXT,
                user_role TEXT,
                message TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                status TEXT,
                generated_at TEXT,
                submitted_at TEXT,
                graded_at TEXT
            )
            """
        )
        self.conn.execute("INSERT INTO teachers (id, name, username) VALUES (30, 'Teacher Wang', 'wang')")
        self.conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (10, 'Class A', 30)")
        self.conn.execute("INSERT INTO courses (id, name, sect_name) VALUES (20, 'Course', 'Sect')")
        self.conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id, course_name) VALUES (1, 10, 20, 30, 'Course')")
        self.conn.executemany(
            "INSERT INTO students (id, class_id, name, student_id_number, enrollment_status) VALUES (?, 10, ?, ?, 'active')",
            [(2, "Alice", "S002"), (3, "Bob", "S003")],
        )
        self.conn.executemany(
            "INSERT INTO assignments (id, course_id, title, status, class_offering_id, due_at) VALUES (?, 20, ?, 'published', 1, ?)",
            [(100, "Task 1", "2026-06-13T09:00:00"), (101, "Task 2", "2026-06-20T09:00:00")],
        )
        self.conn.executemany(
            "INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, status, submitted_at) VALUES (?, ?, 3, 'Bob', 'submitted', '2026-06-10T08:00:00')",
            [(200, "100"), (201, "101")],
        )
        self.conn.executemany(
            """
            INSERT INTO classroom_behavior_states (
                class_offering_id, user_pk, user_role, total_activity_count, last_event_at, online_accumulated_seconds
            )
            VALUES (1, ?, 'student', ?, ?, ?)
            """,
            [
                (2, 1, "2026-06-01T08:00:00", 60),
                (3, 5, "2026-06-11T08:00:00", 600),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO cultivation_weekly_snapshots (
                class_offering_id, student_id, week_start, week_end, score,
                progress_percent, components_json, level_key, snapshot_source, created_at
            )
            VALUES (1, 2, ?, ?, ?, 50, '{}', 'mortal', 'test', '2026-06-12T08:00:00')
            """,
            [
                ("2026-06-08", "2026-06-14", 12.0),
                ("2026-06-01", "2026-06-07", 12.0),
                ("2026-05-25", "2026-05-31", 12.0),
            ],
        )
        self.conn.executemany(
            "INSERT INTO learning_stage_exam_attempts (class_offering_id, student_id, stage_key, status, generated_at) VALUES (1, 2, 'foundation', 'failed', ?)",
            [("2026-06-09T08:00:00",), ("2026-06-10T08:00:00",)],
        )

    def tearDown(self):
        self.conn.close()

    def test_generate_alerts_creates_ranked_rule_results(self):
        result = generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-12T08:00:00")

        self.assertGreaterEqual(result["created_or_updated"], 4)
        alerts = list_cultivation_alerts(self.conn, 1)
        rule_keys = {alert["rule_key"] for alert in alerts}
        self.assertIn("pending_assignment_due_soon", rule_keys)
        self.assertIn("low_task_completion", rule_keys)
        self.assertIn("zero_growth_two_weeks", rule_keys)
        self.assertIn("no_activity_7d", rule_keys)
        self.assertIn("stage_exam_failed_twice", rule_keys)
        self.assertEqual("L3", alerts[0]["severity"])

    def test_handled_alert_is_suppressed_during_cooldown(self):
        generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-12T08:00:00")
        alert = next(item for item in list_cultivation_alerts(self.conn, 1) if item["rule_key"] == "no_activity_7d")

        handled = handle_cultivation_alert(
            self.conn,
            alert_id=alert["id"],
            teacher_id=30,
            action="handled",
            note="Contacted student.",
        )
        self.assertEqual("handled", handled["status"])
        second = generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-13T08:00:00")
        active_rules = {item["rule_key"] for item in list_cultivation_alerts(self.conn, 1)}

        self.assertNotIn("no_activity_7d", active_rules)
        self.assertGreaterEqual(second["suppressed"], 1)

    def test_snoozed_alert_stays_hidden_until_snooze_expires(self):
        generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-12T08:00:00")
        alert = next(item for item in list_cultivation_alerts(self.conn, 1) if item["rule_key"] == "pending_assignment_due_soon")

        snoozed = handle_cultivation_alert(
            self.conn,
            alert_id=alert["id"],
            teacher_id=30,
            action="snoozed",
            snooze_days=7,
        )
        self.assertEqual("snoozed", snoozed["status"])
        generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-13T08:00:00")
        active_rules = {item["rule_key"] for item in list_cultivation_alerts(self.conn, 1)}

        self.assertNotIn("pending_assignment_due_soon", active_rules)

    def test_alert_private_message_uses_existing_message_center_flow(self):
        generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-12T08:00:00")
        alert = next(item for item in list_cultivation_alerts(self.conn, 1) if item["rule_key"] == "no_activity_7d")
        content = build_cultivation_alert_private_message(alert)

        with patch("classroom_app.services.message_center_service.get_configured_db_engine", return_value="sqlite"):
            result = create_private_message(
                self.conn,
                {"id": 30, "role": "teacher", "name": "Teacher Wang"},
                contact_identity=f"student:{alert['student_id']}",
                class_offering_id=1,
                content=content,
            )

        self.assertEqual("student:2|teacher:30|scope:1", result["conversation_key"])
        message = self.conn.execute("SELECT * FROM private_messages WHERE id = ?", (result["message"]["id"],)).fetchone()
        self.assertIsNotNone(message)
        self.assertEqual("student:2", message["recipient_identity"])
        self.assertIn("这不是批评", message["content"])
        notification = self.conn.execute(
            """
            SELECT *
            FROM message_center_notifications
            WHERE recipient_identity = 'student:2'
              AND category = 'private_message'
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(notification)
        self.assertEqual(str(message["id"]), notification["ref_id"])

    def test_alert_support_note_appends_without_clobbering_existing_note(self):
        generate_cultivation_alerts(self.conn, class_offering_id=1, now="2026-06-12T08:00:00")
        alert = next(item for item in list_cultivation_alerts(self.conn, 1) if item["rule_key"] == "low_task_completion")
        self.conn.execute(
            """
            INSERT INTO student_shared_teacher_notes (
                student_id, note_text, created_by_teacher_id, updated_by_teacher_id, created_at, updated_at
            )
            VALUES (2, '已有观察：先从短任务恢复节奏。', 30, 30, '2026-06-10T08:00:00', '2026-06-10T08:00:00')
            """
        )

        note = append_cultivation_alert_support_note(
            self.conn,
            alert=alert,
            teacher_id=30,
            now_text="2026-06-12T09:30:00",
        )

        self.assertTrue(note["has_note"])
        self.assertIn("已有观察", note["note_text"])
        self.assertIn("修为预警", note["note_text"])
        self.assertIn(alert["title"], note["note_text"])
        self.assertLessEqual(len(note["note_text"]), 2400)
        self.assertEqual(30, note["updated_by_teacher_id"])


if __name__ == "__main__":
    unittest.main()
