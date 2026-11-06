"""The capacity poll protocol against actual isolated SQLite/ASGI services."""
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi import FastAPI, HTTPException, Request

from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.dependencies import get_current_user
from classroom_app.routers import polls
from tests.sqlite_database_fixture import isolated_sqlite_database
from tools.capacity_classroom_write_probe import exercise_poll, PollProbeFailure, require_finished_soak


class PostSoakGuardTests(unittest.TestCase):
    def test_running_or_unconfirmed_soak_cannot_start_logins(self):
        with tempfile.TemporaryDirectory(prefix="capacity-poll-guard-") as folder:
            runtime = Path(folder)
            (runtime / "capacity-report.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
            with self.assertRaises(PollProbeFailure):
                require_finished_soak(runtime, True)
            (runtime / "capacity-report.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            with self.assertRaises(PollProbeFailure):
                require_finished_soak(runtime, False)
            require_finished_soak(runtime, True)


class ClassroomPollProtocolTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        with database.get_db_connection() as conn:
            cls.teacher_id = execute_insert_returning_id(conn, "INSERT INTO teachers(name,email,hashed_password) VALUES('Synthetic poll teacher','poll@example.invalid','synthetic')", ())
            cls.class_id = execute_insert_returning_id(conn, "INSERT INTO classes(name,created_by_teacher_id) VALUES('Synthetic poll cohort',?)", (cls.teacher_id,))
            other_class = execute_insert_returning_id(conn, "INSERT INTO classes(name,created_by_teacher_id) VALUES('Synthetic nonmember cohort',?)", (cls.teacher_id,))
            course = execute_insert_returning_id(conn, "INSERT INTO courses(name,created_by_teacher_id) VALUES('Synthetic poll course',?)", (cls.teacher_id,))
            cls.offering = execute_insert_returning_id(conn, "INSERT INTO class_offerings(class_id,course_id,teacher_id) VALUES(?,?,?)", (cls.class_id, course, cls.teacher_id))
            cls.users = {"teacher": {"id": cls.teacher_id, "role": "teacher", "name": "Synthetic poll teacher"}}
            cls.students = []
            for index in range(202):
                student = execute_insert_returning_id(conn, "INSERT INTO students(student_id_number,name,class_id,hashed_password) VALUES(?,?,?,'synthetic')",
                    (f"POLL-SYNTHETIC-{index:04d}", f"Synthetic poll student {index}", other_class if index == 201 else cls.class_id))
                token = f"student-{index}"
                cls.users[token] = {"id": student, "role": "student", "name": f"Synthetic poll student {index}"}
                if index < 200:
                    cls.students.append((student, token))

    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(polls.router)
        def current_user(request: Request):
            user = self.users.get(request.cookies.get("access_token"))
            if user is None:
                raise HTTPException(401)
            return user
        # Native execution uses real logins. This test isolates the HTTP/domain
        # protocol only; actual SQL access checks, votes and status writes remain.
        app.dependency_overrides[get_current_user] = current_user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://poll-protocol.test")
        self.addAsyncCleanup(self.client.aclose)

    async def exercise(self, client, report):
        return await exercise_poll(client, offering_id=self.offering, teacher_token="teacher", students=self.students,
                                   reserve_token="student-200", outsider_token="student-201", report=report)

    async def test_200_real_service_ballots_match_receipts_repeats_acl_and_closure(self):
        report, choices = await self.exercise(self.client, {})
        self.assertEqual("passed", report["status"])
        self.assertEqual((200, 200, 200), (report["matching_vote_receipts"], report["duplicate_rejections"], report["matching_member_reads"]))
        self.assertEqual([], report["errors"])
        with database.get_db_connection() as conn:
            ballots = conn.execute("SELECT voter_id,change_count FROM poll_ballots WHERE poll_id=?", (report["poll_id"],)).fetchall()
            votes = conn.execute("SELECT voter_id,option_id FROM poll_votes WHERE poll_id=?", (report["poll_id"],)).fetchall()
            self.assertEqual(200, len(ballots))
            self.assertTrue(all(row["change_count"] == 0 for row in ballots))
            self.assertEqual(choices, {row["voter_id"]: row["option_id"] for row in votes})

    async def test_success_status_with_wrong_personal_receipt_cannot_pass(self):
        client = self.client
        class WrongReceipt:
            async def request(_self, method, route, **kwargs):
                response = await client.request(method, route, **kwargs)
                if route.endswith("/vote") and response.status_code == 200:
                    payload = response.json()
                    payload["poll"]["my_option_ids"] = []
                    return httpx.Response(200, json=payload)
                return response
        report = {}
        with self.assertRaises(PollProbeFailure):
            await self.exercise(WrongReceipt(), report)
        self.assertEqual("failed", report["status"])
        with database.get_db_connection() as conn:
            self.assertEqual("closed", conn.execute("SELECT status FROM polls WHERE id=?", (report["poll_id"],)).fetchone()[0])
            self.assertEqual(200, conn.execute("SELECT COUNT(*) FROM poll_ballots WHERE poll_id=?", (report["poll_id"],)).fetchone()[0])
