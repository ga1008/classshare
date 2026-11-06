"""Actual transactional ballot rules on independent SQLite connections."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.routers import polls
from classroom_app.services import poll_service as service
from tests.sqlite_database_fixture import isolated_sqlite_database


class PollConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        with database.get_db_connection() as conn:
            teacher = execute_insert_returning_id(conn, "INSERT INTO teachers(name,email,hashed_password) VALUES('Synthetic','poll-lock@example.invalid','synthetic')", ())
            class_id = execute_insert_returning_id(conn, "INSERT INTO classes(name,created_by_teacher_id) VALUES('Synthetic poll locking',?)", (teacher,))
            course_id = execute_insert_returning_id(conn, "INSERT INTO courses(name,created_by_teacher_id) VALUES('Synthetic poll locking',?)", (teacher,))
            cls.offering = execute_insert_returning_id(conn, "INSERT INTO class_offerings(class_id,course_id,teacher_id) VALUES(?,?,?)", (class_id, course_id, teacher))
            cls.teacher = {"id": teacher, "role": "teacher", "name": "Synthetic"}
            cls.students = []
            for index in range(3):
                student = execute_insert_returning_id(conn, "INSERT INTO students(name,student_id_number,class_id) VALUES(?,?,?)", (f"Synthetic {index}", f"POLL-LOCK-{index}", class_id))
                cls.students.append({"id": student, "role": "student", "name": f"Synthetic {index}"})

    def create(self, **fields):
        with database.get_db_connection() as conn:
            return service.create_poll(conn, self.teacher,
                {"title": "Synthetic poll", "options": ["One", "Two"], "status": "active", **fields},
                origin=service.ORIGIN_CLASSROOM, class_offering_id=self.offering)

    def vote(self, poll, actor=None, option=0):
        try:
            with database.get_db_connection() as conn:
                return service.vote(conn, poll["id"], actor or self.students[0], [poll["options"][option]["id"]])
        except HTTPException as exc:
            return exc.status_code

    def race(self, poll):
        barrier = threading.Barrier(8)
        def cast(index):
            barrier.wait(3)
            return self.vote(poll, option=index % 2)
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(cast, range(8)))

    def test_same_actor_first_votes_have_one_ballot_and_reject_repeats(self):
        poll = self.create()
        results = self.race(poll)
        self.assertEqual(1, sum(isinstance(value, dict) for value in results))
        self.assertEqual(7, results.count(400))
        with database.get_db_connection() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM poll_ballots WHERE poll_id=?", (poll["id"],)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM poll_votes WHERE poll_id=?", (poll["id"],)).fetchone()[0])

    def test_parallel_changes_cannot_exceed_one_change_budget(self):
        poll = self.create(allow_change=True, max_changes=1)
        self.assertIsInstance(self.vote(poll), dict)
        results = self.race(poll)
        self.assertEqual(1, sum(isinstance(value, dict) for value in results))
        self.assertEqual(7, results.count(400))
        with database.get_db_connection() as conn:
            self.assertEqual(1, conn.execute("SELECT change_count FROM poll_ballots WHERE poll_id=?", (poll["id"],)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM poll_votes WHERE poll_id=?", (poll["id"],)).fetchone()[0])

    def test_service_reserves_sqlite_transaction_and_never_commits_its_caller(self):
        poll = self.create()
        with database.get_db_connection() as conn:
            statements = []
            conn.set_trace_callback(statements.append)
            service.vote(conn, poll["id"], self.students[0], [poll["options"][0]["id"]])
            conn.set_trace_callback(None)
            self.assertIn("BEGIN IMMEDIATE", statements)
            self.assertFalse(any(statement.startswith("COMMIT") for statement in statements))
            self.assertTrue(conn.in_transaction)
            conn.rollback()
        with database.get_db_connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM poll_ballots WHERE poll_id=?", (poll["id"],)).fetchone()[0])

    def test_retake_lookup_is_read_only_and_new_confirmed_decisions_apply(self):
        poll = self.create()
        with database.get_db_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS classroom_retake_students")
        self.assertIsInstance(self.vote(poll), dict)
        with database.get_db_connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='classroom_retake_students'").fetchone())
            conn.execute("CREATE TABLE classroom_retake_students(class_offering_id INTEGER,student_id INTEGER,status TEXT)")
            conn.execute("INSERT INTO classroom_retake_students VALUES(?,?,'confirmed')", (self.offering, self.students[1]["id"]))
        self.assertEqual(403, self.vote(poll, self.students[1]))
        with database.get_db_connection() as conn:
            conn.execute("UPDATE classroom_retake_students SET status='suggested'")
        self.assertIsInstance(self.vote(poll, self.students[1]), dict)

    def test_optional_notification_failure_rolls_back_only_notification(self):
        def fail(conn, _poll_id):
            conn.execute("UPDATE polls SET title='must roll back optional hook'")
            raise sqlite3.OperationalError("synthetic notification failure")
        with database.get_db_connection() as conn:
            poll = service.create_poll(conn, self.teacher, {"title": "Synthetic preserved", "options": ["One", "Two"], "status": "active"},
                                       origin=service.ORIGIN_CLASSROOM, class_offering_id=self.offering)
            with patch("classroom_app.services.message_center_service.create_poll_published_notifications", side_effect=fail):
                polls._notify_if_active(conn, poll)
            self.assertEqual(1, conn.execute("SELECT 1").fetchone()[0])
        with database.get_db_connection() as conn:
            self.assertEqual("Synthetic preserved", conn.execute("SELECT title FROM polls WHERE id=?", (poll["id"],)).fetchone()[0])
