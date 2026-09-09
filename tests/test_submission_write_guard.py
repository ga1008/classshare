"""Shared Web mutation routes preserve submission, grade and file boundaries."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from classroom_app import config
from classroom_app.routers.homework_parts import common, drafts, submissions
from classroom_app.services.grading_revision_service import activate_submission_grade_revision
from classroom_app.services.submission_write_guard import submission_write_version
from tests.test_mp_grade_safety import SCHEMA


class SubmissionMutationGuardTests(unittest.TestCase):
    engine = "sqlite"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-submission-write-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "synthetic.sqlite"
        self.files = self.root / "submission"
        self.files.mkdir()
        with self.connection() as conn:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    if self.engine == "postgres":
                        statement = statement.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                        statement = statement.replace("CREATE TABLE submission_files(id INTEGER PRIMARY KEY", "CREATE TABLE submission_files(id BIGSERIAL PRIMARY KEY")
                    conn.execute(statement)
            for field in ("stored_path", "file_ext", "file_hash"):
                conn.execute(f"ALTER TABLE submission_files ADD COLUMN {field} TEXT")
            conn.execute("CREATE TABLE submission_drafts(id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER)")
            for field, field_type in (("availability_mode", "TEXT"), ("starts_at", "TEXT"), ("duration_minutes", "INTEGER"), ("auto_close", "INTEGER")):
                conn.execute(f"ALTER TABLE assignments ADD COLUMN {field} {field_type}")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(config, "DB_ENGINE", self.engine))
        for module in (common, drafts, submissions):
            self.stack.enter_context(patch.object(module, "get_db_connection", self.connection))
        self.stack.enter_context(patch.object(submissions, "_build_submission_storage_dir", return_value=self.files))
        self.stack.enter_context(patch.object(submissions, "record_behavior_event", return_value=None))
        self.stack.enter_context(patch.object(submissions, "close_overdue_assignments", return_value=None))

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add_file(self, file_id=1, name="answer.txt", contents=b"old answer"):
        path = self.files / name
        path.write_bytes(contents)
        with self.connection() as conn:
            conn.execute("""INSERT INTO submission_files(id,submission_id,original_filename,relative_path,
                stored_path,mime_type,file_size,file_ext,file_hash) VALUES(?,1,?,?,?,'text/plain',?,'.txt','hash')""",
                         (file_id, name, name, str(path), len(contents)))
        return path

    def delete_file(self, file_id=1):
        return asyncio.run(submissions.delete_submission_file(file_id, user={"id": 10}))

    def add_upload(self, filename="answer.txt", contents=b"new upload"):
        upload = UploadFile(io.BytesIO(contents), filename=filename, size=len(contents))
        return asyncio.run(submissions.add_submission_files(1, manifest="", queue_ai="0", files=[upload], user={"id": 10}))

    def seed_old_grade(self):
        with self.connection() as conn:
            submission = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
            activate_submission_grade_revision(conn, submission=submission, data={"source": "manual"}, score=80, feedback_md="old")
            conn.execute("INSERT INTO ai_jobs(id,status,lease_token) VALUES(11,'running','old lease')")
            conn.execute("""UPDATE submissions SET status='graded',score=80,feedback_md='old',
                resubmission_allowed=1,grading_job_id=11,score_before_late_penalty=90,
                late_penalty_points=10,late_score_cap_applied=1 WHERE id=1""")
            conn.execute("""INSERT INTO group_assignment_member_results(id,assignment_id,group_id,
                student_pk_id,submission_id,work_score,final_score,revealed) VALUES(1,'1',1,1,1,80,78,1),(2,'1',1,2,2,90,88,1)""")

    def test_attachment_edit_retires_old_grade_job_and_group_results(self):
        self.add_file()
        self.seed_old_grade()
        self.delete_file()
        with self.connection() as conn:
            latest = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
            for field in ("score", "active_grade_revision_id", "grading_job_id", "grading_revision_hash", "score_before_late_penalty"):
                self.assertIsNone(latest[field], field)
            self.assertEqual("submitted", latest["status"])
            self.assertEqual(0, latest["late_penalty_points"])
            self.assertEqual("superseded", conn.execute("SELECT status FROM submission_grade_revisions WHERE submission_id=1").fetchone()[0])
            self.assertEqual("superseded", conn.execute("SELECT status FROM ai_jobs WHERE id=11").fetchone()[0])
            result = conn.execute("SELECT * FROM group_assignment_member_results WHERE student_pk_id=1").fetchone()
            self.assertIsNone(result["work_score"])
            self.assertEqual(0, result["revealed"])
            teammate = conn.execute("SELECT * FROM group_assignment_member_results WHERE student_pk_id=2").fetchone()
            self.assertEqual(90, teammate["work_score"])
            self.assertEqual(0, teammate["revealed"])

    def test_delayed_file_cleanup_preserves_new_upload_at_same_path(self):
        path = self.add_file()
        discard = submissions._discard_quarantined_submission_paths
        def cleanup(retired):
            path.write_bytes(b"next submission")
            discard(retired)
        with patch.object(submissions, "_discard_quarantined_submission_paths", cleanup):
            self.delete_file()
        self.assertEqual(b"next submission", path.read_bytes())
        self.assertEqual([], list(self.files.glob("*.__retired__*")))

    def test_delayed_submission_cleanup_preserves_next_submission_directory(self):
        self.add_file()
        self.seed_old_grade()
        discard = submissions._discard_quarantined_submission_paths
        def cleanup(retired):
            self.files.mkdir()
            (self.files / "answer.txt").write_bytes(b"next submission")
            discard(retired)
        with patch.object(submissions, "_discard_quarantined_submission_paths", cleanup):
            result = asyncio.run(submissions.return_submission(1, user={"id": 10}))
        self.assertEqual(1, result["deleted_submission_id"])
        self.assertEqual(b"next submission", (self.files / "answer.txt").read_bytes())
        with self.connection() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM submissions WHERE id=1").fetchone())
            self.assertIsNone(conn.execute("SELECT work_score FROM group_assignment_member_results WHERE student_pk_id=1").fetchone()[0])

    def test_failed_commit_restores_file_and_database(self):
        path = self.add_file()
        connection = self.connection
        class FailingCommit:
            def __init__(self, conn):
                self.conn = conn
            def __getattr__(self, name):
                return getattr(self.conn, name)
            def commit(self):
                raise RuntimeError("synthetic commit failure")
        @contextmanager
        def failing_connection():
            with connection() as conn:
                yield FailingCommit(conn)
        with patch.object(submissions, "get_db_connection", failing_connection):
            with self.assertRaisesRegex(RuntimeError, "synthetic commit failure"):
                self.delete_file()
        self.assertEqual(b"old answer", path.read_bytes())
        with self.connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM submission_files WHERE id=1").fetchone())

    def test_stale_teacher_upload_cannot_modify_replacement(self):
        path = self.add_file()
        store = submissions.store_submission_files
        async def upload_then_replace(*args, **kwargs):
            result = await store(*args, **kwargs)
            with self.connection() as conn:
                conn.execute("UPDATE submissions SET submitted_at='new submission',answers_json='new answer' WHERE id=1")
            return result
        with patch.object(submissions, "store_submission_files", upload_then_replace):
            with self.assertRaises(HTTPException) as error:
                self.add_upload()
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual(b"old answer", path.read_bytes())
        self.assertEqual(["answer.txt"], [item.name for item in self.files.iterdir()])
        with self.connection() as conn:
            self.assertEqual("new answer", conn.execute("SELECT answers_json FROM submissions WHERE id=1").fetchone()[0])

    def test_simultaneous_attachment_deletions_do_not_restore_stale_answer_references(self):
        self.add_file(1, "first.txt")
        self.add_file(2, "second.txt")
        with self.connection() as conn:
            conn.execute("UPDATE submissions SET answers_json=? WHERE id=1", (json.dumps({"answers": [{
                "question_id": "q1", "answer": "text", "attachments": [{"relative_path": "first.txt"}, {"relative_path": "second.txt"}]
            }]}),))
        barrier = Barrier(2)
        begin = submissions.begin_immediate_transaction
        def begin_together(conn):
            barrier.wait(timeout=5)
            begin(conn)
        with patch.object(submissions, "begin_immediate_transaction", begin_together), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.delete_file, [1, 2]))
        self.assertEqual(2, len(results))
        with self.connection() as conn:
            answer = json.loads(conn.execute("SELECT answers_json FROM submissions WHERE id=1").fetchone()[0])
            self.assertEqual([], answer["answers"][0]["attachments"])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM submission_files").fetchone()[0])

    def test_simultaneous_same_name_uploads_keep_both_files(self):
        barrier = Barrier(2)
        begin = submissions.begin_immediate_transaction
        def begin_together(conn):
            barrier.wait(timeout=5)
            begin(conn)
        with patch.object(submissions, "begin_immediate_transaction", begin_together), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda content: self.add_upload(contents=content), [b"first upload", b"second upload"]))
        self.assertEqual([1, 1], [result["added_count"] for result in results])
        self.assertEqual({b"first upload", b"second upload"}, {path.read_bytes() for path in self.files.iterdir()})
        with self.connection() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM submission_files").fetchone()[0])

    def test_teacher_reopen_retires_old_ledger_before_any_resubmission(self):
        from tests.test_mp_grade_safety import JsonRequest
        self.seed_old_grade()
        result = asyncio.run(submissions.teacher_withdraw_submissions("1", JsonRequest({"submission_ids": [1]}), user={"id": 10}))
        self.assertEqual(1, result["updated_count"])
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM submissions WHERE id=1").fetchone()
            self.assertEqual(1, row["resubmission_allowed"])
            self.assertIsNone(row["active_grade_revision_id"])
            self.assertIsNone(row["grading_job_id"])
            self.assertEqual("superseded", conn.execute("SELECT status FROM submission_grade_revisions WHERE submission_id=1").fetchone()[0])
            self.assertIsNone(conn.execute("SELECT work_score FROM group_assignment_member_results WHERE student_pk_id=1").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT revealed FROM group_assignment_member_results WHERE student_pk_id=2").fetchone()[0])

    def test_student_withdraw_cleanup_preserves_new_round_directory(self):
        self.add_file()
        discard = submissions._discard_quarantined_submission_paths
        def cleanup(retired):
            self.files.mkdir()
            (self.files / "answer.txt").write_bytes(b"next round")
            discard(retired)
        with patch.object(submissions, "_discard_quarantined_submission_paths", cleanup):
            result = asyncio.run(submissions.withdraw_submission("1", user={"id": 1}))
        self.assertEqual("success", result["status"])
        self.assertEqual(b"next round", (self.files / "answer.txt").read_bytes())

    def test_student_withdraw_rechecks_grade_after_writer_lock(self):
        path = self.add_file()
        lock = submissions.lock_submission_writer
        def teacher_graded(conn, assignment_id, student_id):
            lock(conn, assignment_id, student_id)
            conn.execute("UPDATE submissions SET status='graded',score=95 WHERE id=1")
        with patch.object(submissions, "lock_submission_writer", teacher_graded):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(submissions.withdraw_submission("1", user={"id": 1}))
        self.assertEqual(400, error.exception.status_code)
        self.assertEqual(b"old answer", path.read_bytes())

    def check_old_draft_round(self, upload):
        with self.connection() as conn:
            conn.execute("UPDATE submissions SET returned_at='2026-09-09T10:00:00',resubmission_allowed=1,resubmission_due_at='2099-01-01T00:00:00' WHERE id=1")
            previous = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
            conn.execute("UPDATE submissions SET returned_at='2026-09-09T11:00:00' WHERE id=1")
        for module in (common, drafts):
            self.stack.enter_context(patch.object(module, "close_overdue_assignments", return_value=None))
            self.stack.enter_context(patch.object(module, "enrich_assignment_runtime_view", side_effect=dict))
        self.stack.enter_context(patch.object(common, "student_can_access_assignment", return_value=True))
        self.stack.enter_context(patch.object(drafts, "_build_submission_draft_storage_dir", return_value=self.root / "draft"))
        files = [UploadFile(io.BytesIO(b"stale file"), filename="stale.txt", size=10)] if upload else []
        with self.assertRaises(HTTPException) as error:
            asyncio.run(drafts.save_assignment_draft("1", answers_json='{"answer":"old round"}', current_page=0,
                client_updated_at="", replace_question_ids="[]", manifest="", files=files, user={"id": 1},
                expected_submission_version=submission_write_version(previous)))
        self.assertEqual(409, error.exception.status_code)
        with self.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM submission_drafts").fetchone()[0])
        self.assertFalse((self.root / "draft").exists())
        self.assertEqual([], list(self.root.glob("*.__staging__*")))

    def test_old_round_text_draft_is_rejected(self):
        self.check_old_draft_round(False)

    def test_old_round_upload_draft_is_rejected(self):
        self.check_old_draft_round(True)

    def test_draft_get_serializes_the_current_submission_round(self):
        from classroom_app.schemas.homework_contracts import AssignmentDraftResponse
        with self.connection() as conn:
            conn.execute("UPDATE submissions SET resubmission_allowed=1,returned_at='2026-09-09T10:00:00',resubmission_due_at='2099-01-01T00:00:00' WHERE id=1")
            current = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
        with patch.object(drafts, "close_overdue_assignments", return_value=None), \
             patch.object(drafts, "enrich_assignment_runtime_view", side_effect=dict), \
             patch.object(common, "student_can_access_assignment", return_value=True):
            payload = drafts.get_assignment_draft("1", user={"id": 1})
        serialized = AssignmentDraftResponse.model_validate(payload).model_dump(exclude_unset=True)
        self.assertEqual(submission_write_version(current), serialized["submission_version"])
        self.assertFalse(serialized["exists"])


@unittest.skipUnless(os.environ.get("MP_PHASE1_POSTGRES_TEACHER_DSN"), "Requires an explicit isolated PostgreSQL cluster")
class NativePostgresSubmissionMutationGuardTests(SubmissionMutationGuardTests):
    engine = "postgres"

    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        self.dsn = os.environ["MP_PHASE1_POSTGRES_TEACHER_DSN"]
        info = conninfo_to_dict(self.dsn)
        if info.get("host") != "127.0.0.1" or info.get("port") in {None, "5432"} or info.get("dbname") != "lanshare_miniapp_phase1":
            raise ValueError("Only the isolated local phase1 database is allowed")
        self.schema = "submission_write_" + uuid4().hex[:12]
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        super().setUp()

    def drop_schema(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    @contextmanager
    def connection(self):
        import psycopg
        from psycopg import sql
        from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
        with psycopg.connect(self.dsn, row_factory=sqlite_compatible_dict_row) as raw:
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            raw.execute("SET lock_timeout TO '5s'")
            yield LanSharePostgresConnection(raw)


if __name__ == "__main__":
    unittest.main()
