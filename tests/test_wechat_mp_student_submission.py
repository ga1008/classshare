"""Student reopen projection and transactional submit guards; no application DB access."""
from __future__ import annotations

import os
import asyncio
import unittest
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi import HTTPException

from classroom_app.routers.mp.tasks import _submission_action_state
from classroom_app.services.submission_write_guard import (
    lock_submission_writer, submission_write_version, verify_submission_write, draft_matches_submission_round,
)


def returned_row(**overrides):
    return {"id": 1, "assignment_id": "42", "student_pk_id": 1,
            "submitted_at": "2026-09-08T10:00:00", "returned_at": "2026-09-08T11:00:00",
            "resubmission_allowed": 1, "is_absence_score": 0,
            "resubmission_due_at": (datetime.now() + timedelta(hours=2)).isoformat(), **overrides}


class StudentActionProjectionTests(unittest.TestCase):
    def test_personal_reopen_overrides_global_close_but_expired_reopen_does_not(self):
        now = datetime.now()
        row = returned_row(resubmission_due_at=(now + timedelta(minutes=2)).isoformat())
        view = _submission_action_state({"is_accepting_submissions": False}, row, now=now)
        self.assertTrue(view["can_submit"])
        self.assertEqual(view["answer_remaining_seconds"], 120)
        self.assertEqual(view["submission_version"], submission_write_version(row))
        self.assertEqual(view["server_now_ms"], int(now.timestamp() * 1000))
        expired = _submission_action_state({"is_accepting_submissions": True}, row, now=now + timedelta(hours=3))
        self.assertFalse(expired["can_submit"])
        self.assertEqual(expired["answer_remaining_seconds"], 0)

    def test_invalid_reopen_and_absence_zero_match_submit_permissions(self):
        now = datetime.now()
        invalid = returned_row(resubmission_due_at="invalid")
        self.assertFalse(_submission_action_state({"is_accepting_submissions": True}, invalid, now=now)["can_submit"])
        absence = returned_row(is_absence_score=1)
        self.assertFalse(_submission_action_state({"is_accepting_submissions": False}, absence, now=now)["can_submit"])
        self.assertTrue(_submission_action_state({"is_accepting_submissions": True}, absence, now=now)["can_submit"])

    def test_draft_round_changes_when_teacher_returns_again(self):
        now = datetime.now()
        one = returned_row()
        two = {**one, "returned_at": "2026-09-08T12:00:00"}
        self.assertNotEqual(_submission_action_state({}, one, now=now)["draft_revision"],
                            _submission_action_state({}, two, now=now)["draft_revision"])


class SubmissionGuardTests(unittest.TestCase):
    def test_draft_round_rejects_pre_return_and_same_second_legacy_drafts(self):
        row = returned_row(returned_at="2026-09-08T11:00:00")
        self.assertFalse(draft_matches_submission_round({"server_updated_at": "2026-09-08T10:59:59"}, row))
        self.assertFalse(draft_matches_submission_round({"server_updated_at": "2026-09-08T11:00:00.900"}, row))
        self.assertTrue(draft_matches_submission_round({"server_updated_at": "2026-09-08T11:00:01"}, row))
        self.assertFalse(draft_matches_submission_round({"server_updated_at": "invalid"}, row))

    def test_first_submission_and_old_web_client_remain_supported(self):
        verify_submission_write(None, None, actor_role="student")
        verify_submission_write(None, None, actor_role="student", client_version="unsubmitted")

    def test_completed_replacement_is_rejected_before_file_switch(self):
        original = returned_row()
        verify_submission_write(original, original, actor_role="student", client_version=submission_write_version(original))
        complete = {**original, "resubmission_allowed": 0, "returned_at": None, "submitted_at": "2026-09-08T12:30:00"}
        with self.assertRaises(HTTPException) as caught:
            verify_submission_write(complete, original, actor_role="student")
        self.assertEqual(caught.exception.status_code, 409)

    def test_new_return_round_rejects_stale_client_even_when_open(self):
        first = returned_row()
        second = {**first, "returned_at": "2026-09-08T12:00:00"}
        with self.assertRaises(HTTPException):
            verify_submission_write(second, second, actor_role="student", client_version=submission_write_version(first))

    def test_reopen_expiry_is_checked_again_inside_transaction(self):
        expired = returned_row(resubmission_due_at="2000-01-01T00:00:00")
        with self.assertRaises(HTTPException):
            verify_submission_write(expired, expired, actor_role="student")


class SubmissionFileRetirementTests(unittest.TestCase):
    def test_delayed_cleanup_preserves_a_new_file_at_the_same_path(self):
        from classroom_app.routers.homework_parts import common
        with TemporaryDirectory(prefix="mp-retire-file-") as temp:
            original = Path(temp) / "answer.png"
            original.write_bytes(b"old")
            retired = common._quarantine_submission_paths([str(original)])
            self.assertFalse(original.exists())
            original.write_bytes(b"new upload")  # Next writer after commit.
            common._discard_quarantined_submission_paths(retired)
            self.assertEqual(original.read_bytes(), b"new upload")
            self.assertFalse(retired[0][1].exists())

    def test_consumed_draft_cleanup_preserves_the_next_round_directory(self):
        from classroom_app.routers.homework_parts import common
        with TemporaryDirectory(prefix="mp-retire-tree-") as temp:
            original = Path(temp) / "draft"
            original.mkdir()
            (original / "answer.png").write_bytes(b"round one")
            retired = common._quarantine_submission_paths([str(original)])
            original.mkdir()
            (original / "answer.png").write_bytes(b"round two")
            common._discard_quarantined_submission_paths(retired)
            self.assertEqual((original / "answer.png").read_bytes(), b"round two")
            self.assertFalse(retired[0][1].exists())

    def test_rollback_restores_the_original_file_and_directory(self):
        from classroom_app.routers.homework_parts import common
        with TemporaryDirectory(prefix="mp-retire-rollback-") as temp:
            file = Path(temp) / "answer.png"
            folder = Path(temp) / "draft"
            folder.mkdir()
            file.write_bytes(b"file")
            (folder / "answer.png").write_bytes(b"draft")
            retired = common._quarantine_submission_paths([str(file), str(folder)])
            common._restore_quarantined_submission_paths(retired)
            self.assertEqual(file.read_bytes(), b"file")
            self.assertEqual((folder / "answer.png").read_bytes(), b"draft")
            self.assertTrue(all(not path.exists() for _, path in retired))

    def test_a_replacement_uploaded_in_the_same_transaction_is_kept(self):
        from classroom_app.routers.homework_parts import common
        with TemporaryDirectory(prefix="mp-retire-keep-") as temp:
            original = Path(temp) / "answer.png"
            original.write_bytes(b"replacement")
            self.assertEqual(common._quarantine_submission_paths([str(original)], keep_paths={str(original)}), [])
            self.assertEqual(original.read_bytes(), b"replacement")


@unittest.skipUnless(os.environ.get("MP_PHASE1_STUDENT_TEST_DSN"), "Set the explicit isolated PostgreSQL test DSN")
class PostgresSubmissionConcurrencyTests(unittest.TestCase):
    """Actual PostgreSQL connections and production SQL adapter; isolated schema only."""
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        from classroom_app.db.postgres import LanSharePostgresConnection
        cls.psycopg, cls.dict_row, cls.adapter = psycopg, dict_row, LanSharePostgresConnection
        cls.dsn = os.environ["MP_PHASE1_STUDENT_TEST_DSN"]
        target = urlparse(cls.dsn)
        if target.hostname not in {"127.0.0.1", "localhost", "::1"} or target.port in {None, 5432} or target.path != "/lanshare_miniapp_phase1":
            raise RuntimeError("Only the explicit loopback, non-default-port phase1 test database is allowed")
        cls.schema = "mp_phase1_student_" + uuid4().hex[:12]
        with cls.connect() as conn:
            if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = ?", (cls.schema,)).fetchone():
                raise RuntimeError("Test schema already exists; refusing to reuse it")
            conn.execute(f"CREATE SCHEMA {cls.schema}")
            conn.commit()
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.connect() as conn:
            conn.execute("CREATE TABLE submissions (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER, submitted_at TEXT, returned_at TEXT, resubmission_due_at TEXT, resubmission_allowed INTEGER, is_absence_score INTEGER, student_name TEXT, status TEXT, started_at TEXT, answers_json TEXT, is_late_submission INTEGER, late_by_seconds INTEGER, late_policy_snapshot_json TEXT, submitted_by_role TEXT, submitted_by_teacher_id INTEGER, submission_channel TEXT, returned_by_teacher_id INTEGER, returned_reason TEXT, absence_scored_at TEXT, absence_scored_by_teacher_id INTEGER)")
            conn.execute("CREATE TABLE assignments (id TEXT PRIMARY KEY, course_id INTEGER, status TEXT)")
            conn.execute("INSERT INTO assignments VALUES ('42', 1, 'published')")
            conn.execute("CREATE TABLE submission_drafts (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER, answers_json TEXT, current_page INTEGER, client_updated_at TEXT, server_updated_at TEXT, server_version INTEGER, status TEXT)")
            conn.execute("CREATE TABLE submission_draft_files (id INTEGER PRIMARY KEY, draft_id INTEGER, question_id TEXT, relative_path TEXT, stored_path TEXT, kind TEXT DEFAULT 'file', original_filename TEXT, mime_type TEXT, file_size INTEGER, file_ext TEXT, file_hash TEXT, created_at TEXT)")
            conn.execute("CREATE TABLE assignment_group_bindings (assignment_id TEXT, status TEXT, scheme_id INTEGER)")
            conn.execute("CREATE TABLE study_groups (id INTEGER, scheme_id INTEGER)")
            conn.execute("CREATE TABLE study_group_members (group_id INTEGER, student_id INTEGER, status TEXT)")
            conn.execute("CREATE TABLE group_assignment_member_results (assignment_id TEXT, student_pk_id INTEGER, group_id INTEGER)")
            conn.commit()

    @classmethod
    def connect(cls):
        conn = cls.adapter(cls.psycopg.connect(cls.dsn, row_factory=cls.dict_row, connect_timeout=5))
        conn.execute(f"SET search_path TO {cls.schema}, public")
        return conn

    @classmethod
    def cleanup_schema(cls):
        with cls.connect() as conn:
            conn.execute(f"DROP SCHEMA {cls.schema} CASCADE")
            conn.commit()

    def run_race(self, original):
        with self.connect() as conn:
            conn.execute("DELETE FROM submissions")
            if original:
                columns = list(original)
                conn.execute(f"INSERT INTO submissions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(original.values()))
            conn.commit()
        barrier = Barrier(2)

        def writer(index):
            with self.connect() as conn:
                before = conn.execute("SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?", ("42", 1)).fetchone()
                barrier.wait(timeout=10)
                lock_submission_writer(conn, "42", 1)
                current = conn.execute("SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?", ("42", 1)).fetchone()
                try:
                    verify_submission_write(current, before, actor_role="student", client_version=submission_write_version(before))
                except HTTPException as exc:
                    conn.rollback()
                    return exc.status_code
                if current:
                    conn.execute("UPDATE submissions SET submitted_at = ?, returned_at = NULL, resubmission_due_at = NULL, resubmission_allowed = 0, is_absence_score = 0 WHERE id = 1", (f"accepted-{index}",))
                else:
                    conn.execute("INSERT INTO submissions (id,assignment_id,student_pk_id,submitted_at,resubmission_allowed,is_absence_score) VALUES (1, '42', 1, ?, 0, 0)", (f"accepted-{index}",))
                conn.commit()
                return 200

        with patch("classroom_app.services.submission_write_guard.get_configured_db_engine", return_value="postgres"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(writer, [1, 2]))
        self.assertEqual(sorted(results), [200, 409])
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM submissions").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["resubmission_allowed"], 0)

    def test_concurrent_first_submissions_accept_exactly_one(self):
        self.run_race(None)

    def test_concurrent_returned_submissions_accept_exactly_one(self):
        self.run_race(returned_row())

    def test_concurrent_absence_replacements_accept_exactly_one(self):
        self.run_race(returned_row(is_absence_score=1, resubmission_allowed=0))

    def check_late_draft(self, *, upload: bool):
        from classroom_app.routers.homework_parts import common, drafts
        with self.connect() as conn:
            conn.execute("DELETE FROM submissions")
            conn.execute("DELETE FROM submission_draft_files")
            conn.execute("DELETE FROM submission_drafts")
            conn.commit()

        def submit_between_permission_check_and_lock(_conn):
            with self.connect() as other:
                other.execute("INSERT INTO submissions (id,assignment_id,student_pk_id,submitted_at,resubmission_allowed,is_absence_score) VALUES (1,'42',1,'accepted',0,0)")
                other.commit()

        with TemporaryDirectory(prefix="mp-phase1-draft-") as temp, ExitStack() as stack:
            stack.enter_context(patch.object(common, "get_db_connection", self.connect))
            stack.enter_context(patch.object(drafts, "get_db_connection", self.connect))
            for module in (common, drafts):
                stack.enter_context(patch.object(module, "close_overdue_assignments", lambda conn: None))
                stack.enter_context(patch.object(module, "enrich_assignment_runtime_view", dict))
            stack.enter_context(patch.object(common, "student_can_access_assignment", return_value=True))
            stack.enter_context(patch.object(common, "_ensure_accepting_submission", return_value=None))
            stack.enter_context(patch("classroom_app.services.submission_write_guard.get_configured_db_engine", return_value="postgres"))
            if upload:
                async def staged(*args, **kwargs):
                    return SimpleNamespace(stored_files=[], dropped_files=[])
                stack.enter_context(patch.object(drafts, "begin_immediate_transaction", submit_between_permission_check_and_lock))
                stack.enter_context(patch.object(drafts, "_validate_upload_entries", return_value=[]))
                stack.enter_context(patch.object(drafts, "_load_exam_attachment_policies", return_value={}))
                stack.enter_context(patch.object(drafts, "store_submission_files", staged))
                stack.enter_context(patch.object(drafts, "_build_submission_draft_storage_dir", return_value=Path(temp) / "draft"))
                operation = lambda: asyncio.run(drafts.save_assignment_draft("42", answers_json="{}", current_page=0,
                    client_updated_at="", replace_question_ids="[]", manifest="[]", files=[SimpleNamespace(filename="a.png")], user={"id": 1}))
            else:
                stack.enter_context(patch.object(common, "begin_immediate_transaction", submit_between_permission_check_and_lock))
                operation = lambda: common._save_assignment_draft_without_files_sync(assignment_id="42", student_pk_id=1,
                    answers_json="{}", current_page=0, client_updated_at="", replace_question_ids="[]")
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 409)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS count FROM submission_drafts").fetchone()["count"], 0)

    def test_text_draft_cannot_be_recreated_after_submission_commits(self):
        self.check_late_draft(upload=False)

    def test_uploaded_draft_cannot_be_recreated_after_submission_commits(self):
        self.check_late_draft(upload=True)

    def test_previous_round_server_draft_is_hidden_and_cleared_on_next_save(self):
        from classroom_app.routers.homework_parts import common
        with self.connect() as conn:
            conn.execute("DELETE FROM submissions")
            conn.execute("DELETE FROM submission_draft_files")
            conn.execute("DELETE FROM submission_drafts")
            conn.execute("INSERT INTO submissions (id,assignment_id,student_pk_id,submitted_at,returned_at,resubmission_due_at,resubmission_allowed,is_absence_score) VALUES (1,'42',1,'2026-09-08T10:00:00','2026-09-08T11:00:00','2099-01-01T00:00:00',1,0)")
            conn.execute("INSERT INTO submission_drafts (id,assignment_id,student_pk_id,answers_json,current_page,client_updated_at,server_updated_at,server_version,status) VALUES (10,'42',1,'old text',0,'','2026-09-08T10:00:00',1,'active')")
            conn.execute("INSERT INTO submission_draft_files (id,draft_id,question_id,relative_path,stored_path) VALUES (11,10,'q1','old.png','old.png')")
            conn.commit()
            self.assertIsNone(common._load_submission_draft(conn, "42", 1))
            common._ensure_submission_draft(conn, assignment_id="42", student_pk_id=1, answers_json="new text", current_page=0, client_updated_at="")
            conn.commit()
            self.assertEqual(common._load_submission_draft(conn, "42", 1)["answers_json"], "new text")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS count FROM submission_draft_files").fetchone()["count"], 0)

    def check_clear_cleanup_race(self, *, upload: bool):
        from classroom_app.routers.homework_parts import common, drafts
        with TemporaryDirectory(prefix="mp-phase1-clear-") as temp, ExitStack() as stack:
            original = Path(temp) / "draft" / "answer.png"
            original.parent.mkdir()
            original.write_bytes(b"old upload")
            with self.connect() as conn:
                conn.execute("DELETE FROM submissions")
                conn.execute("DELETE FROM submission_draft_files")
                conn.execute("DELETE FROM submission_drafts")
                conn.execute("INSERT INTO submission_drafts (id,assignment_id,student_pk_id,answers_json,current_page,client_updated_at,server_updated_at,server_version,status) VALUES (10,'42',1,'{}',0,'','2026-09-08T10:00:00',1,'active')")
                conn.execute("INSERT INTO submission_draft_files (id,draft_id,question_id,relative_path,stored_path,file_size) VALUES (11,10,'q1','answer.png',?,10)", (str(original),))
                conn.commit()
            discard = common._discard_quarantined_submission_paths

            def newer_upload_before_cleanup(retired):
                self.assertFalse(original.exists())
                # A different request commits the same path after clear's commit.
                original.write_bytes(b"new upload")
                with self.connect() as other:
                    other.execute("INSERT INTO submission_draft_files (id,draft_id,question_id,relative_path,stored_path,file_size) VALUES (12,10,'q1','answer.png',?,10)", (str(original),))
                    other.commit()
                discard(retired)

            for module in (common, drafts):
                stack.enter_context(patch.object(module, "get_db_connection", self.connect))
                stack.enter_context(patch.object(module, "close_overdue_assignments", lambda conn: None))
                stack.enter_context(patch.object(module, "enrich_assignment_runtime_view", dict))
                stack.enter_context(patch.object(module, "begin_immediate_transaction", lambda conn: None))
            stack.enter_context(patch.object(common, "student_can_access_assignment", return_value=True))
            stack.enter_context(patch.object(common, "_ensure_accepting_submission", return_value=None))
            stack.enter_context(patch("classroom_app.services.submission_write_guard.get_configured_db_engine", return_value="postgres"))
            if upload:
                async def staged(*args, **kwargs):
                    return SimpleNamespace(stored_files=[], dropped_files=[])
                stack.enter_context(patch.object(drafts, "_validate_upload_entries", return_value=[]))
                stack.enter_context(patch.object(drafts, "_load_exam_attachment_policies", return_value={}))
                stack.enter_context(patch.object(drafts, "store_submission_files", staged))
                stack.enter_context(patch.object(drafts, "_build_submission_draft_storage_dir", return_value=original.parent))
                stack.enter_context(patch.object(drafts, "_discard_quarantined_submission_paths", newer_upload_before_cleanup))
                asyncio.run(drafts.save_assignment_draft("42", answers_json="{}", current_page=0, client_updated_at="",
                    replace_question_ids='["q1"]', manifest="[]", files=[SimpleNamespace(filename="a.png")], user={"id": 1}))
            else:
                stack.enter_context(patch.object(common, "_discard_quarantined_submission_paths", newer_upload_before_cleanup))
                common._save_assignment_draft_without_files_sync(assignment_id="42", student_pk_id=1,
                    answers_json="{}", current_page=0, client_updated_at="", replace_question_ids='["q1"]')
            self.assertEqual(original.read_bytes(), b"new upload")
            with self.connect() as conn:
                self.assertEqual(conn.execute("SELECT id FROM submission_draft_files").fetchone()["id"], 12)

    def test_text_clear_late_cleanup_keeps_concurrent_same_path_upload(self):
        self.check_clear_cleanup_race(upload=False)

    def test_upload_clear_late_cleanup_keeps_concurrent_same_path_upload(self):
        self.check_clear_cleanup_race(upload=True)

    def check_submit_directory_retirement(self, *, abort: bool):
        from classroom_app.routers.homework_parts import common
        with TemporaryDirectory(prefix="mp-phase1-consume-") as temp, ExitStack() as stack:
            draft_dir = Path(temp) / "draft"
            draft_dir.mkdir()
            (draft_dir / "answer.png").write_bytes(b"previous round")
            with self.connect() as conn:
                conn.execute("DELETE FROM submissions")
                conn.execute("DELETE FROM submission_draft_files")
                conn.execute("DELETE FROM submission_drafts")
                conn.commit()

            async def stage(*args, **kwargs):
                return SimpleNamespace(stored_files=[], dropped_files=[])
            def insert(conn, sql, params):
                return conn.execute(sql + " RETURNING id", params).fetchone()["id"]
            discard = common._discard_quarantined_submission_paths
            def next_round_before_cleanup(retired):
                self.assertFalse(draft_dir.exists())
                draft_dir.mkdir()
                (draft_dir / "answer.png").write_bytes(b"next round")
                discard(retired)
            stack.enter_context(patch.object(common, "store_submission_files", stage))
            stack.enter_context(patch.object(common, "_load_exam_attachment_policies", return_value={}))
            stack.enter_context(patch.object(common, "_copy_submission_draft_files_to_staging", return_value=([], [])))
            stack.enter_context(patch.object(common, "_build_submission_storage_dir", return_value=Path(temp) / "submission"))
            stack.enter_context(patch.object(common, "_build_submission_draft_storage_dir", return_value=draft_dir))
            stack.enter_context(patch.object(common, "begin_immediate_transaction", lambda conn: None))
            stack.enter_context(patch.object(common, "insert_and_get_id", insert))
            stack.enter_context(patch("classroom_app.services.submission_write_guard.get_configured_db_engine", return_value="postgres"))
            if abort:
                stack.enter_context(patch.object(common, "refresh_student_learning_state", lambda conn, *a, **kw: conn.execute("SELECT 1 / 0")))
            else:
                stack.enter_context(patch.object(common, "_discard_quarantined_submission_paths", next_round_before_cleanup))
            with self.connect() as conn:
                operation = lambda: asyncio.run(common._save_submission_payload(conn,
                    assignment={"id": "42", "course_id": 1, "status": "published", "class_offering_id": 1 if abort else None},
                    student={"id": 1, "name": "synthetic"}, answers_json='{"answers":[{"question":"作答","answer":"answer"}]}',
                    manifest="", files=[], actor_role="student", actor_user_pk=1, channel="online", use_server_draft_files=True))
                if abort:
                    with self.assertRaises(HTTPException):
                        operation()
                else:
                    result = operation()
                    self.assertGreater(result["submission_id"], 0)
            expected = b"previous round" if abort else b"next round"
            self.assertEqual((draft_dir / "answer.png").read_bytes(), expected)
            with self.connect() as conn:
                count = conn.execute("SELECT COUNT(*) AS count FROM submissions").fetchone()["count"]
                self.assertEqual(count, 0 if abort else 1)

    def test_submit_late_cleanup_keeps_the_new_round_draft_directory(self):
        self.check_submit_directory_retirement(abort=False)

    def test_caught_sql_error_rolls_back_submission_and_restores_draft_directory(self):
        self.check_submit_directory_retirement(abort=True)
