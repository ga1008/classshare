import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app import config
from classroom_app.db.connection import get_db_connection
from classroom_app.db.schema import init_database
from classroom_app.services.classroom_interaction_service import (
    clear_my_help_signal,
    load_interaction_snapshot,
    set_help_signal,
)
from classroom_app.services.resource_access_service import ensure_classroom_access


class ClassroomInteractionSnapshotQueryTests(unittest.TestCase):
    offering = 410
    owner = {"role": "teacher", "id": 101}
    course_owner = {"role": "teacher", "id": 102}
    super_admin = {"role": "teacher", "id": 103}
    primary = {"role": "student", "id": 501, "name": "Primary"}
    linked = {"role": "student", "id": 502, "name": "Linked"}
    outsider = {"role": "student", "id": 503}
    withdrawn = {"role": "student", "id": 504}

    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory(prefix="interaction-snapshot-"))
        self.enterContext(patch.object(config, "DB_PATH", Path(folder) / "classroom.db"))
        init_database()
        self.conn = self.enterContext(closing(get_db_connection()))
        self.conn.executescript("""
            INSERT INTO teachers (id, name, email, hashed_password, is_super_admin) VALUES
                (101, 'Owner', 'owner@example.invalid', 'x', 0),
                (102, 'Course owner', 'course@example.invalid', 'x', 0),
                (103, 'Administrator', 'admin@example.invalid', 'x', 1);
            INSERT INTO classes (id, name, created_by_teacher_id) VALUES
                (201, 'Primary class', 101), (202, 'Linked class', 101), (203, 'Other class', 102);
            INSERT INTO courses (id, name, created_by_teacher_id) VALUES (301, 'Synthetic course', 102);
            INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (410, 201, 301, 101);
            INSERT INTO class_offering_class_links (offering_id, class_id, teacher_id) VALUES (410, 202, 101);
            INSERT INTO students (id, student_id_number, name, class_id, enrollment_status) VALUES
                (501, 'S501', 'Primary', 201, 'active'), (502, 'S502', 'Linked', 202, 'active'),
                (503, 'S503', 'Outside', 203, 'active'), (504, 'S504', 'Withdrawn', 202, 'withdrawn');
            """)

    def traced_snapshot(self, user, select_count):
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            snapshot = load_interaction_snapshot(self.conn, self.offering, user)
        finally:
            self.conn.set_trace_callback(None)
        self.assertTrue(all(sql.strip().startswith("SELECT ") for sql in statements), statements)
        self.assertEqual(select_count, len(statements), statements)
        self.assertFalse(self.conn.in_transaction)
        return json.loads(json.dumps(snapshot, ensure_ascii=False))

    def test_empty_snapshot_uses_three_student_reads_and_keeps_public_shape(self):
        snapshot = self.traced_snapshot(self.primary, 3)
        self.assertEqual({
            "classroom": {"id": 410, "course_name": "Synthetic course", "class_name": "Primary class"},
            "role": "student",
            "can_create": False,
            "summary": {
                "active_activity_count": 0, "recent_activity_count": 0, "response_count": 0,
                "open_question_count": 0, "active_signal_count": 0, "signal_counts": {},
            },
            "active_activities": [], "recent_activities": [], "signals": [], "my_signal": None,
            "signal_options": [
                {"key": "hand", "label": "举手"}, {"key": "help", "label": "求助"},
                {"key": "slow", "label": "跟不上"}, {"key": "done", "label": "已完成"},
            ],
            "limits": {"max_active_activities": 6, "max_options": 8, "max_questions_per_activity": 30},
        }, snapshot)
        owner = self.traced_snapshot(self.owner, 3)
        self.assertEqual(dict(snapshot, role="teacher", can_create=True), owner)
        self.assertEqual(owner, self.traced_snapshot(self.super_admin, 4))

    def test_populated_snapshot_keeps_role_visibility_and_uses_one_signal_read(self):
        self.conn.executescript("""
            INSERT INTO classroom_live_activities
                (id, class_offering_id, kind, title, status, show_results, created_by_teacher_id) VALUES
                (601, 410, 'quiz', 'Closed quiz', 'closed', 'teacher_only', 101),
                (602, 410, 'qna', 'Open questions', 'active', 'after_submit', 101);
            INSERT INTO classroom_live_options (id, activity_id, label, is_correct) VALUES (701, 601, 'Correct', 1);
            INSERT INTO classroom_live_responses (activity_id, student_id, option_id, is_anonymous, updated_at)
                VALUES (601, 502, 701, 1, '2026-11-05T08:00:00');
            INSERT INTO classroom_live_questions
                (activity_id, class_offering_id, student_id, display_name, question_text, is_anonymous, status) VALUES
                (602, 410, 502, 'Linked', 'Visible question', 1, 'open'),
                (602, 410, 502, 'Linked', 'Hidden question', 0, 'hidden');
            INSERT INTO classroom_live_help_signals
                (id, class_offering_id, student_id, display_name, signal_type, message, status, updated_at) VALUES
                (801, 410, 502, 'Linked', 'hand', 'Later timestamp', 'active', '2026-11-05T08:05:00'),
                (802, 410, 502, 'Linked', 'help', 'Higher active id', 'active', '2026-11-05T08:04:00'),
                (803, 410, 501, 'Primary', 'hand', 'Other private message', 'active', '2026-11-05T08:03:00'),
                (804, 410, 502, 'Linked', 'slow', 'Inactive', 'cancelled', '2026-11-05T08:06:00');
            """)
        student = self.traced_snapshot(self.linked, 8)
        teacher = self.traced_snapshot(self.owner, 7)
        self.assertEqual(student["summary"], teacher["summary"])
        self.assertEqual(3, student["summary"]["active_signal_count"])
        self.assertEqual({"hand": 2, "help": 1}, student["summary"]["signal_counts"])
        self.assertEqual([], student["signals"])
        self.assertNotIn("Other private message", json.dumps(student))
        self.assertEqual((802, 502, "Higher active id", True, False), tuple(
            student["my_signal"][key] for key in ("id", "student_id", "message", "is_mine", "can_resolve")
        ))
        self.assertIsNone(teacher["my_signal"])
        self.assertEqual([803, 802, 801], [item["id"] for item in teacher["signals"]])
        self.assertTrue(all(item["can_resolve"] for item in teacher["signals"]))

        student_quiz, = student["recent_activities"]
        teacher_quiz, = teacher["recent_activities"]
        self.assertFalse(student_quiz["can_show_results"])
        self.assertNotIn("response_count", student_quiz["options"][0])
        self.assertNotIn("is_correct", student_quiz["options"][0])
        self.assertTrue(student_quiz["my_response"]["is_correct"])
        self.assertTrue(teacher_quiz["options"][0]["is_correct"])
        self.assertEqual(1, teacher_quiz["options"][0]["response_count"])
        self.assertEqual([{"rank": 1, "student_name": "匿名同学", "answered_at": "08:00:00"}], student_quiz["leaderboard"])
        self.assertEqual(student_quiz["leaderboard"], teacher_quiz["leaderboard"])

        for snapshot, is_teacher in ((student, False), (teacher, True)):
            question, = snapshot["active_activities"][0]["questions"]
            self.assertEqual("Visible question", question["question_text"])
            self.assertEqual("匿名同学", question["display_name"])
            self.assertEqual(not is_teacher, question["is_mine"])
            self.assertEqual(is_teacher, question["can_resolve"])

    def test_membership_and_teacher_access_keep_row_shape_and_error_contract(self):
        expected = ensure_classroom_access(self.conn, self.offering, self.owner)
        self.assertIsInstance(expected, sqlite3.Row)
        for user in (self.primary, self.linked, self.super_admin):
            with self.subTest(user=user):
                row = ensure_classroom_access(self.conn, self.offering, user)
                self.assertEqual(expected, row)
        self.conn.execute("UPDATE teachers SET is_active = 0 WHERE id = 103")
        self.conn.commit()
        for user, offering, status, detail in (
            (None, self.offering, 401, "Not authenticated"),
            ({"role": "student", "id": "invalid"}, self.offering, 403, "Invalid user"),
            (self.outsider, 999, 404, "Classroom not found"),
            (self.outsider, self.offering, 403, "Permission denied"),
            (self.withdrawn, self.offering, 403, "Permission denied"),
            (self.course_owner, self.offering, 403, "Permission denied"),
            (self.super_admin, self.offering, 403, "Permission denied"),
        ):
            with self.subTest(user=user, offering=offering):
                with self.assertRaises(HTTPException) as denied:
                    ensure_classroom_access(self.conn, offering, user)
                self.assertEqual((status, detail), (denied.exception.status_code, denied.exception.detail))

    def test_snapshot_reads_own_signal_writes_and_rollback_keeps_other_connection_unchanged(self):
        observer = self.enterContext(closing(get_db_connection()))
        original = load_interaction_snapshot(observer, self.offering, self.linked)
        signal = set_help_signal(self.conn, self.offering, self.linked, {"signal_type": "hand"})
        pending = load_interaction_snapshot(self.conn, self.offering, self.linked)
        self.assertEqual(signal, pending["my_signal"])
        self.assertEqual({"hand": 1}, pending["summary"]["signal_counts"])
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(original, load_interaction_snapshot(observer, self.offering, self.linked))
        self.conn.commit()
        self.assertEqual(pending, load_interaction_snapshot(observer, self.offering, self.linked))

        self.assertEqual(1, clear_my_help_signal(self.conn, self.offering, self.linked))
        self.assertEqual(original, load_interaction_snapshot(self.conn, self.offering, self.linked))
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(pending, load_interaction_snapshot(observer, self.offering, self.linked))

        self.conn.execute("DELETE FROM class_offering_class_links WHERE offering_id = 410 AND class_id = 202")
        with self.assertRaises(HTTPException) as denied:
            load_interaction_snapshot(self.conn, self.offering, self.linked)
        self.assertEqual(403, denied.exception.status_code)
        self.assertEqual(pending, load_interaction_snapshot(observer, self.offering, self.linked))
        self.conn.rollback()
