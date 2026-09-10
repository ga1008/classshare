"""Manual grade route and miniapp projections on an isolated synthetic database."""

import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from classroom_app import config
from classroom_app.routers.homework_parts import grading, common
from classroom_app.routers.mp import teacher
from classroom_app.services.submission_grade_guard_service import (
    lock_submission_for_manual_grade, submission_review_revision,
)
from classroom_app.services.grading_revision_service import retire_submission_grade_for_replacement
from classroom_app.services import group_assignment_service as groups
from classroom_app.services import submission_grading_service as grading_domain
from classroom_app.services.score_projection_service import load_submission_score_facts

_real_record_member_work_score = groups.record_member_work_score


SCHEMA = """
CREATE TABLE courses(id INTEGER PRIMARY KEY, name TEXT, created_by_teacher_id INTEGER);
CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER, teacher_id INTEGER);
CREATE TABLE class_offering_class_links(offering_id INTEGER, class_id INTEGER);
CREATE TABLE students(id INTEGER PRIMARY KEY, class_id INTEGER, name TEXT, student_id_number TEXT, enrollment_status TEXT);
CREATE TABLE assignments(id TEXT PRIMARY KEY, title TEXT, status TEXT, created_at TEXT, course_id INTEGER,
 class_offering_id INTEGER, exam_paper_id TEXT, due_at TEXT, allowed_file_types_json TEXT,
 assessment_kind TEXT, assessment_kind_version INTEGER, assessment_kind_source TEXT,
 late_submission_enabled INTEGER, late_submission_until TEXT, late_penalty_strategy TEXT,
 late_penalty_interval_hours REAL, late_penalty_points REAL, late_penalty_min_score REAL, late_score_cap REAL);
CREATE TABLE learning_stage_exam_attempts(id INTEGER PRIMARY KEY, assignment_id TEXT);
CREATE TABLE submissions(id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER, status TEXT,
 score REAL, feedback_md TEXT, submitted_at TEXT, answers_json TEXT, is_late_submission INTEGER,
 is_absence_score INTEGER, resubmission_allowed INTEGER, score_before_late_penalty REAL,
 late_penalty_points REAL, late_score_cap_applied INTEGER, late_policy_snapshot_json TEXT,
 grading_started_at TEXT, grading_attempt_fingerprint TEXT, grading_revision_hash TEXT,
 grading_job_id INTEGER, active_grade_revision_id INTEGER, resubmission_due_at TEXT,
 returned_at TEXT, returned_by_teacher_id INTEGER, returned_reason TEXT);
ALTER TABLE submissions ADD COLUMN student_name TEXT;
ALTER TABLE submissions ADD COLUMN started_at TEXT;
ALTER TABLE submissions ADD COLUMN late_by_seconds INTEGER;
ALTER TABLE submissions ADD COLUMN submitted_by_role TEXT;
ALTER TABLE submissions ADD COLUMN submitted_by_teacher_id INTEGER;
ALTER TABLE submissions ADD COLUMN submission_channel TEXT;
ALTER TABLE submissions ADD COLUMN absence_scored_at TEXT;
ALTER TABLE submissions ADD COLUMN absence_scored_by_teacher_id INTEGER;
CREATE TABLE submission_files(id INTEGER PRIMARY KEY, submission_id INTEGER, original_filename TEXT,
 relative_path TEXT, mime_type TEXT, file_size INTEGER);
CREATE TABLE ai_jobs(id INTEGER PRIMARY KEY, status TEXT, lease_token TEXT, lease_expires_at TEXT,
 locked_at TEXT, locked_by TEXT, updated_at TEXT, finished_at TEXT, result_id INTEGER);
CREATE TABLE submission_grade_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER,
 ai_job_id INTEGER, ai_result_id INTEGER, revision_hash TEXT, revision_no INTEGER, status TEXT,
 score REAL, feedback_md TEXT, quality_audit_json TEXT, provenance_json TEXT,
 created_at TEXT, activated_at TEXT, superseded_at TEXT, UNIQUE(submission_id, revision_hash));
CREATE UNIQUE INDEX one_active_grade ON submission_grade_revisions(submission_id) WHERE status='active';
CREATE TABLE group_schemes(id INTEGER PRIMARY KEY, name TEXT, status TEXT, group_count INTEGER);
CREATE TABLE assignment_group_bindings(id INTEGER PRIMARY KEY, assignment_id TEXT, class_offering_id INTEGER,
 scheme_id INTEGER, status TEXT);
CREATE TABLE study_groups(id INTEGER PRIMARY KEY, name TEXT, scheme_id INTEGER, group_index INTEGER, class_offering_id INTEGER);
CREATE TABLE study_group_members(group_id INTEGER, student_id INTEGER, status TEXT, member_role TEXT);
CREATE TABLE group_assignment_member_results(id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT,
 class_offering_id INTEGER, group_id INTEGER, student_pk_id INTEGER, submission_id INTEGER, work_score REAL,
 peer_avg REAL, peer_review_count INTEGER, final_score REAL, revealed INTEGER, finalized_at TEXT,
 created_at TEXT, updated_at TEXT);
CREATE TABLE peer_reviews(id INTEGER PRIMARY KEY, group_id INTEGER, assignment_id TEXT, reviewer_student_id INTEGER,
 reviewee_student_id INTEGER, contribution_points INTEGER);
INSERT INTO courses VALUES(1,'Course',10);
INSERT INTO classes VALUES(1,'Primary'),(2,'Linked'),(3,'Other');
INSERT INTO class_offerings VALUES(1,1,1,10);
INSERT INTO class_offering_class_links VALUES(1,2);
INSERT INTO students VALUES(1,1,'A','01','active'),(2,2,'B','02','active'),
 (3,2,'Paused','03','suspended'),(4,3,'Other','04','active'),(5,2,'Absent','05','active'),
 (6,2,'Returned','06','active');
ALTER TABLE students ADD COLUMN avatar_file_hash TEXT;
INSERT INTO assignments(id,title,status,created_at,course_id,class_offering_id,assessment_kind,
 assessment_kind_version,assessment_kind_source) VALUES('1','Homework','published','2026-01-01',1,1,'homework',1,'manual');
INSERT INTO submissions(id,assignment_id,student_pk_id,status,score,feedback_md,submitted_at,answers_json,
 is_late_submission,is_absence_score,resubmission_allowed,late_penalty_points)
 VALUES(1,'1',1,'submitted',NULL,'A feedback','2026-01-02','{}',0,0,0,0),
 (2,'1',2,'graded',90,'B feedback','2026-01-02','{}',0,0,0,0),
 (3,'1',3,'graded',99,'paused','2026-01-02','{}',0,0,0,0),
 (4,'1',4,'graded',99,'other class','2026-01-02','{}',0,0,0,0),
 (5,'1',5,'graded',0,'absence','2026-01-02','{}',0,1,0,0),
 (6,'1',6,'submitted',NULL,'returned','2026-01-02','{}',0,0,1,0);
"""


class JsonRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class ManualGradeSafetyTests(unittest.TestCase):
    engine = "sqlite"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-grade-safety-")
        self.path = Path(self.temp.name) / "synthetic.sqlite"
        with self.connection() as conn:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                                 if self.engine == "postgres" else statement)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DB_ENGINE", self.engine))
        self.stack.enter_context(patch.object(grading, "get_db_connection", self.connection))
        self.stack.enter_context(patch.object(teacher, "get_db_connection", self.connection))
        # No user notifications / learning snapshots leave the synthetic fixture.
        for name in ("create_student_grading_notification", "handle_stage_exam_grading_complete",
                     "handle_assignment_stage_grading_complete", "refresh_student_learning_state"):
            self.stack.enter_context(patch.object(grading_domain, name, Mock()))
        self.stack.enter_context(patch("classroom_app.services.group_assignment_service.record_member_work_score", Mock()))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def review(self, sid=1):
        return teacher.mp_teacher_submission_review(sid, user={"id": 10})["data"]["submission"]

    def grade(self, body, sid=1, teacher_id=10):
        return asyncio.run(grading.grade_submission(sid, JsonRequest(body), user={"id": teacher_id}))

    def count_revisions(self):
        with self.connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM submission_grade_revisions").fetchone()[0]

    def test_late_80_minus_10_remains_70_after_repeated_feedback_edits(self):
        snapshot = {"is_late_submission": True, "late_penalty_strategy": "fixed", "late_penalty_points": 10}
        with self.connection() as conn:
            conn.execute("UPDATE submissions SET is_late_submission=1, late_policy_snapshot_json=? WHERE id=1",
                         (json.dumps(snapshot),))
        revision = self.review()["review_revision"]
        for feedback in ("first", "second", "third"):
            self.grade({"score": 80, "feedback_md": feedback, "expected_review_revision": revision})
            data = self.review()
            self.assertEqual(70, data["score"])
            self.assertEqual(80, data["score_before_late_penalty"])
            self.assertEqual(feedback, data["editable_feedback_md"])
            self.assertEqual(1, data["feedback_md"].count("## 补交扣分"))
            self.assertNotEqual(revision, data["review_revision"])
            revision = data["review_revision"]
        # Existing Web clients may send the generated footer back; it stays single.
        self.grade({"score": 80, "feedback_md": data["feedback_md"]})
        self.assertEqual(1, self.review()["feedback_md"].count("## 补交扣分"))

    def test_blank_invalid_and_nonfinite_scores_never_write_or_notify(self):
        for value in (None, "", " ", True, False, "NaN", float("inf"), -1, 101, [], {}):
            with self.subTest(value=value), self.assertRaises(HTTPException) as error:
                self.grade({"score": value})
            self.assertEqual(400, error.exception.status_code)
        self.assertEqual(0, self.count_revisions())
        grading_domain.create_student_grading_notification.assert_not_called()
        self.grade({"score": 0})
        self.assertEqual(0, self.review()["score"])

    def test_simultaneous_editors_one_success_one_conflict_one_ledger_revision(self):
        token = self.review()["review_revision"]
        barrier = threading.Barrier(2)

        def edit(score):
            barrier.wait(timeout=5)
            try:
                self.grade({"score": score, "feedback_md": str(score), "expected_review_revision": token})
                return 200
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, (70, 90)))
        self.assertEqual([200, 409], sorted(results))
        self.assertEqual(1, self.count_revisions())
        self.assertEqual(1, grading_domain.create_student_grading_notification.call_count)

    def test_stale_answer_after_resubmit_is_rejected_even_without_existing_grade_revision(self):
        token = self.review()["review_revision"]
        with self.connection() as conn:
            conn.execute("UPDATE submissions SET answers_json='new answer', submitted_at='2026-01-03' WHERE id=1")
        with self.assertRaises(HTTPException) as error:
            self.grade({"score": 80, "expected_review_revision": token})
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual(0, self.count_revisions())

    def test_returned_and_unauthorized_submissions_are_unchanged(self):
        for sid, uid, status in ((6, 10, 400), (1, 20, 403)):
            with self.assertRaises(HTTPException) as error:
                self.grade({"score": 80}, sid=sid, teacher_id=uid)
            self.assertEqual(status, error.exception.status_code)
        self.assertEqual(0, self.count_revisions())

    def test_manual_grade_supersedes_ai_and_keeps_ledger_provenance(self):
        with self.connection() as conn:
            conn.execute("INSERT INTO ai_jobs(id,status,lease_token) VALUES(11,'running','lease')")
            conn.execute("UPDATE submissions SET grading_job_id=11,status='grading' WHERE id=1")
        self.grade({"score": 80, "expected_review_revision": self.review()["review_revision"]})
        with self.connection() as conn:
            job = conn.execute("SELECT * FROM ai_jobs WHERE id=11").fetchone()
            revision = conn.execute("SELECT * FROM submission_grade_revisions WHERE submission_id=1").fetchone()
        self.assertEqual("superseded", job["status"])
        self.assertEqual("", job["lease_token"])
        self.assertEqual("manual", json.loads(revision["provenance_json"])["source"])
        self.assertEqual(10, json.loads(revision["provenance_json"])["actor_user_pk"])

    def test_primary_and_linked_roster_and_task_statistics_use_same_scope(self):
        result = teacher.mp_teacher_grading(1, user={"id": 10})["data"]
        task = teacher.mp_teacher_tasks(user={"id": 10})["data"]["tasks"][0]
        self.assertEqual([1, 2, 5, 6], [item["student_pk_id"] for item in result["entries"]])
        self.assertEqual(4, task["student_total"])
        self.assertEqual(4, result["stats"]["total_students"])
        for key, value in (("submitted_count", 3), ("graded_count", 1), ("pending_grade_count", 1)):
            self.assertEqual(value, task[key])
            self.assertEqual(value, result["stats"][key])

    def test_empty_active_roster_does_not_reintroduce_inactive_or_other_class_submissions(self):
        with self.connection() as conn:
            conn.execute("UPDATE students SET enrollment_status='suspended' WHERE class_id IN (1,2)")
        result = teacher.mp_teacher_grading(1, user={"id": 10})["data"]
        task = teacher.mp_teacher_tasks(user={"id": 10})["data"]["tasks"][0]
        self.assertEqual([], result["entries"])
        self.assertEqual(0, task["student_total"])
        for key in ("submitted_count", "graded_count", "pending_grade_count"):
            self.assertEqual(0, task[key])
            self.assertEqual(0, result["stats"][key])

    def test_ledger_failure_rolls_back_score_and_ai_supersede_together(self):
        with self.connection() as conn:
            conn.execute("INSERT INTO ai_jobs(id,status,lease_token) VALUES(11,'running','lease')")
            conn.execute("UPDATE submissions SET grading_job_id=11,status='grading' WHERE id=1")
        with patch.object(grading_domain, "activate_submission_grade_revision", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.grade({"score": 80, "expected_review_revision": self.review()["review_revision"]})
        with self.connection() as conn:
            job = conn.execute("SELECT * FROM ai_jobs WHERE id=11").fetchone()
        self.assertEqual("running", job["status"])
        self.assertEqual("lease", job["lease_token"])
        self.assertIsNone(self.review()["score"])
        self.assertEqual("grading", self.review()["status"])
        grading_domain.create_student_grading_notification.assert_not_called()

    def test_group_failure_propagates_and_rolls_back_grade_instead_of_success(self):
        with patch.object(groups, "record_member_work_score", side_effect=RuntimeError("group settlement failed")):
            with self.assertRaisesRegex(RuntimeError, "group settlement failed"):
                self.grade({"score": 80})
        self.assertIsNone(self.review()["score"])
        self.assertEqual(0, self.count_revisions())

    def replace_answer(self, conn, sid=1):
        previous = dict(conn.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone())
        retire_submission_grade_for_replacement(conn, previous)
        groups.invalidate_member_work_score(conn, assignment_id=previous["assignment_id"], student_pk_id=previous["student_pk_id"])
        conn.execute("""UPDATE submissions SET status='submitted',score=NULL,feedback_md=NULL,
            answers_json='new answer',resubmission_allowed=0,active_grade_revision_id=NULL,
            grading_job_id=NULL,grading_revision_hash=NULL WHERE id=?""", (sid,))

    def test_replacement_retires_previous_grade_and_ai_without_losing_history(self):
        self.grade({"score": 80, "feedback_md": "old answer feedback"})
        with self.connection() as conn:
            conn.execute("INSERT INTO ai_jobs(id,status,lease_token) VALUES(11,'running','lease')")
            conn.execute("UPDATE submissions SET grading_job_id=11,resubmission_allowed=1 WHERE id=1")
            self.replace_answer(conn)
            projected = load_submission_score_facts(conn, submission_ids=[1], student_view=True)[0]
            self.assertIsNone(projected["score"])
            self.assertEqual("", projected["feedback_md"])
            self.assertFalse(projected["has_effective_score"])
            self.assertEqual("pending", projected["grade_display_state"])
            old = conn.execute("SELECT * FROM submission_grade_revisions WHERE submission_id=1").fetchone()
            self.assertEqual("superseded", old["status"])
            self.assertEqual(80, old["score"])
            self.assertEqual("superseded", conn.execute("SELECT status FROM ai_jobs WHERE id=11").fetchone()[0])

    def test_actual_save_payload_retires_old_answer_grade_before_student_projection(self):
        self.grade({"score": 80, "feedback_md": "old answer feedback"})
        with self.connection() as conn:
            conn.execute("UPDATE submissions SET resubmission_allowed=1,resubmission_due_at=? WHERE id=1",
                         ((datetime.now() + timedelta(hours=1)).isoformat(),))
            conn.commit()
            assignment = dict(conn.execute("SELECT * FROM assignments WHERE id='1'").fetchone())
            student = dict(conn.execute("SELECT * FROM students WHERE id=1").fetchone())
            previous = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
            with patch.object(common, "_build_submission_storage_dir", return_value=Path(self.temp.name) / "synthetic_files"), \
                 patch.object(common, "refresh_student_learning_state", Mock()):
                result = asyncio.run(common._save_submission_payload(
                    conn, assignment=assignment, student=student, answers_json='{"answers":[{"question":"Q","answer":"new actual answer"}]}',
                    manifest="", files=[], actor_role="student", actor_user_pk=1, channel="online", existing_submission=previous,
                ))
            self.assertEqual(1, result["submission_id"])
            latest = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
            self.assertIsNone(latest["active_grade_revision_id"])
            self.assertIn("new actual answer", latest["answers_json"])
            self.assertEqual("pending", load_submission_score_facts(conn, submission_ids=[1], student_view=True)[0]["grade_display_state"])

    def test_group_replacement_withholds_results_and_refinalizes_from_new_work(self):
        with patch.object(groups, "ensure_study_group_scheme_schema", lambda _: None), patch.object(groups, "_notify_group_finalized", Mock()):
            with self.connection() as conn:
                conn.execute("INSERT INTO group_schemes VALUES(1,'Group Scheme','active',1)")
                conn.execute("INSERT INTO assignment_group_bindings VALUES(1,'1',1,1,'active')")
                conn.execute("INSERT INTO study_groups VALUES(1,'Group 1',1,1,1)")
                conn.execute("INSERT INTO study_group_members VALUES(1,1,'active','member'),(1,2,'active','member')")
                conn.execute("INSERT INTO peer_reviews VALUES(1,1,'1',1,2,16),(2,1,'1',2,1,18)")
            self.grade({"score": 80})
            self.grade({"score": 90}, sid=2)
            with self.connection() as conn:
                _real_record_member_work_score(conn, 1)
                _real_record_member_work_score(conn, 2)
                self.assertTrue(groups.get_student_display_state(conn, '1', 1)["revealed"])
                self.replace_answer(conn)
                for sid in (1, 2):
                    self.assertFalse(groups.get_student_display_state(conn, '1', sid)["revealed"])
                    self.assertIsNone(load_submission_score_facts(conn, submission_ids=[sid], student_view=True)[0]["score"])
                self.assertFalse(groups.try_finalize_group(conn, assignment_id='1', group_id=1)["finalized"])
                self.assertEqual(90, groups._load_member_result(conn, '1', 2)["work_score"])
            self.grade({"score": 100})
            with self.connection() as conn:
                _real_record_member_work_score(conn, 1)
                first = groups.get_student_display_state(conn, '1', 1)
                second = groups.get_student_display_state(conn, '1', 2)
                self.assertTrue(first["revealed"])
                self.assertTrue(second["revealed"])
                self.assertEqual(98, first["final_score"])
                self.assertEqual(88, second["final_score"])
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM peer_reviews").fetchone()[0])

    def test_postgres_uses_row_lock_and_no_sqlite_begin(self):
        conn = Mock()
        with patch.object(config, "DB_ENGINE", "postgres"):
            lock_submission_for_manual_grade(conn, 1)
        conn.execute.assert_called_once_with("SELECT id FROM submissions WHERE id = ? FOR UPDATE", (1,))


@unittest.skipUnless(os.environ.get("MP_PHASE1_POSTGRES_TEACHER_DSN"), "requires explicit isolated PostgreSQL cluster")
class NativePostgresGradeSafetyTests(ManualGradeSafetyTests):
    """Same real route suite with real PostgreSQL row locks and two connections."""

    engine = "postgres"

    def test_manual_grade_waits_for_rubric_editor_and_rechecks_assignment_revision(self):
        from classroom_app.services.assignment_management_service import load_assignment_row, assignment_revision
        with self.connection() as conn:
            conn.execute("ALTER TABLE assignments ADD COLUMN rubric_md TEXT")
            conn.execute("UPDATE assignments SET rubric_md='Old rubric' WHERE id='1'")
        with self.connection() as conn:
            reviewed = assignment_revision(load_assignment_row(conn, '1'))
        entered = threading.Event()
        def grade():
            entered.set()
            try:
                self.grade({'score': 80, 'expected_assignment_revision': reviewed})
                return 200
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connection() as editor:
                editor.execute("SELECT id FROM assignments WHERE id='1' FOR UPDATE").fetchone()
                editor.execute("UPDATE assignments SET rubric_md='New rubric' WHERE id='1'")
                future = pool.submit(grade)
                self.assertTrue(entered.wait(timeout=3))
                # Without assignment-first locking, the old rubric would be
                # accepted while the editor has an uncommitted new revision.
                with self.assertRaises(TimeoutError):
                    future.result(timeout=0.2)
            self.assertEqual(409, future.result(timeout=5))
        self.assertEqual(0, self.count_revisions())
        self.assertIsNone(self.review()['score'])

    def test_swallowed_postgres_error_never_becomes_a_successful_grade(self):
        def failing_hook(conn, *args, **kwargs):
            try:
                conn.execute("SELECT * FROM missing_table_for_grade_safety_probe")
            except Exception:
                pass  # Simulates an existing best-effort downstream hook.
        with patch.object(grading_domain, "create_student_grading_notification", failing_hook):
            with self.assertRaises(Exception):
                self.grade({"score": 80})
        self.assertIsNone(self.review()["score"])
        self.assertEqual(0, self.count_revisions())

    def test_group_grade_waits_before_submission_row_while_teammate_resubmits(self):
        from classroom_app.services.submission_write_guard import lock_submission_writer
        with patch.object(groups, "ensure_study_group_scheme_schema", lambda _: None), patch.object(groups, "_notify_group_finalized", Mock()):
            with self.connection() as conn:
                conn.execute("INSERT INTO group_schemes VALUES(1,'Group Scheme','active',1)")
                conn.execute("INSERT INTO assignment_group_bindings VALUES(1,'1',1,1,'active')")
                conn.execute("INSERT INTO study_groups VALUES(1,'Group 1',1,1,1)")
                conn.execute("INSERT INTO study_group_members VALUES(1,1,'active','member'),(1,2,'active','member')")
                conn.execute("INSERT INTO peer_reviews VALUES(1,1,'1',1,2,16),(2,1,'1',2,1,18)")
            self.grade({"score": 80})
            self.grade({"score": 90}, sid=2)
            with self.connection() as conn:
                _real_record_member_work_score(conn, 1)
                _real_record_member_work_score(conn, 2)
                conn.execute("UPDATE submissions SET resubmission_allowed=1,resubmission_due_at=? WHERE id=1",
                             ((datetime.now() + timedelta(hours=1)).isoformat(),))

            replacement_locked = threading.Event()
            release_replacement = threading.Event()
            grading_entered = threading.Event()
            grading_reached_row_lock = threading.Event()
            real_row_lock = grading_domain.lock_submission_for_manual_grade
            real_group_lock = grading_domain.lock_group_grading_for_submission

            def grade_group_lock(conn, sid):
                grading_entered.set()
                real_group_lock(conn, sid)

            def grade_row_lock(conn, sid):
                grading_reached_row_lock.set()
                real_row_lock(conn, sid)

            def replace():
                with self.connection() as conn:
                    assignment = dict(conn.execute("SELECT * FROM assignments WHERE id='1'").fetchone())
                    student = dict(conn.execute("SELECT * FROM students WHERE id=1").fetchone())
                    previous = dict(conn.execute("SELECT * FROM submissions WHERE id=1").fetchone())
                    lock_submission_writer(conn, '1', 1)
                    replacement_locked.set()
                    if not release_replacement.wait(5):
                        raise TimeoutError("test did not release replacement")
                    return asyncio.run(common._save_submission_payload(
                        conn, assignment=assignment, student=student, answers_json='{"answers":[{"question":"Q","answer":"new answer"}]}',
                        manifest="", files=[], actor_role="student", actor_user_pk=1, channel="online", existing_submission=previous,
                    ))

            with patch.object(grading_domain, "lock_group_grading_for_submission", grade_group_lock), \
                 patch.object(grading_domain, "lock_submission_for_manual_grade", grade_row_lock), \
                 patch.object(groups, "record_member_work_score", _real_record_member_work_score), \
                 patch.object(common, "_build_submission_storage_dir", return_value=Path(self.temp.name) / "concurrent_files"), \
                 patch.object(common, "refresh_student_learning_state", Mock()), \
                 ThreadPoolExecutor(max_workers=2) as pool:
                resubmission = pool.submit(replace)
                self.assertTrue(replacement_locked.wait(5))
                grading_result = pool.submit(self.grade, {"score": 95}, 2)
                try:
                    self.assertTrue(grading_entered.wait(5))
                    self.assertFalse(grading_reached_row_lock.wait(0.2), "grade must wait for group before taking B's row")
                finally:
                    release_replacement.set()
                self.assertEqual(1, resubmission.result(timeout=10)["submission_id"])
                self.assertEqual("success", grading_result.result(timeout=10)["status"])
            with self.connection() as conn:
                student_a = load_submission_score_facts(conn, submission_ids=[1], student_view=True)[0]
                self.assertIsNone(student_a["score"])
                self.assertEqual("new answer", json.loads(student_a["answers_json"])["answers"][0]["answer"])
                self.assertIsNone(groups._load_member_result(conn, '1', 1)["work_score"])
                self.assertEqual(95, groups._load_member_result(conn, '1', 2)["work_score"])
                self.assertFalse(groups.get_student_display_state(conn, '1', 1)["revealed"])

    def setUp(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        self.dsn = os.environ["MP_PHASE1_POSTGRES_TEACHER_DSN"]
        info = conninfo_to_dict(self.dsn)
        if (info.get("host") != "127.0.0.1" or info.get("port") == "5432"
                or info.get("dbname") != "lanshare_miniapp_phase1"):
            raise ValueError("Only the explicitly isolated phase1 local database is permitted")
        self.schema = f"mp_phase1_teacher_{os.getpid()}_{self._testMethodName}"
        self.schema = self.schema[:63]
        # Exclusive schema creation. Never replace another test's or application's data.
        with psycopg.connect(self.dsn) as conn:
            from psycopg import sql
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        super().setUp()

    @contextmanager
    def connection(self):
        import psycopg
        from psycopg import sql
        from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
        with psycopg.connect(self.dsn, row_factory=sqlite_compatible_dict_row) as raw:
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            raw.execute("SET lock_timeout TO '5s'")
            yield LanSharePostgresConnection(raw)

    def tearDown(self):
        super().tearDown()
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))


if __name__ == "__main__":
    unittest.main()
