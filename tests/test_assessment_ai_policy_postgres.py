"""Native type/lifecycle regression on an explicitly owned offline PG cluster.

Ordinary test_backend runs skip this case. A native rehearsal owner supplies
ASSESSMENT_REHEARSAL_TEST_CLUSTER and ASSESSMENT_REHEARSAL_TEST_PORT; the existing
offline connector checks physical data_directory, loopback listener and port.
Only a freshly created unique schema is changed and removed on completion.
"""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import os
from pathlib import Path
from types import SimpleNamespace
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.services.ai_workspace_policy import ensure_ai_workspace_access
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class AssessmentAiPolicyPostgresTests(unittest.TestCase):
    def setUp(self):
        self.raw = self.enterContext(connect_offline(
            cluster_dir=Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"]),
            port=int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"]),
            database="lanshare_assessment_rehearsal"))
        self.schema = "ai_policy_test_" + uuid4().hex
        self.raw.execute(f'CREATE SCHEMA "{self.schema}"')
        self.raw.commit()
        self.addCleanup(self._drop_owned_schema)
        self.raw.execute(f'SET search_path TO "{self.schema}"')
        self.raw.row_factory = sqlite_compatible_dict_row
        self.conn = LanSharePostgresConnection(self.raw)
        # The different identifier types are intentional, matching the exporter:
        # numeric foreign keys widen to BIGINT; old windows have TEXT without FK.
        self.raw.execute("""
            CREATE TABLE assignments(id BIGINT PRIMARY KEY,class_offering_id BIGINT,exam_paper_id TEXT,
                availability_mode TEXT,status TEXT,assessment_kind TEXT,starts_at TEXT,due_at TEXT);
            CREATE TABLE students(id BIGINT PRIMARY KEY,class_id BIGINT);
            CREATE TABLE class_offerings(id BIGINT PRIMARY KEY,class_id BIGINT);
            CREATE TABLE class_offering_class_links(offering_id BIGINT,class_id BIGINT);
            CREATE TABLE submissions(assignment_id BIGINT,student_pk_id BIGINT,status TEXT,is_absence_score INTEGER,
                resubmission_allowed INTEGER,resubmission_due_at TEXT);
            CREATE TABLE assignment_student_windows(assignment_id TEXT,student_pk_id INTEGER,status TEXT,due_at TEXT,
                PRIMARY KEY(assignment_id,student_pk_id));
            CREATE TABLE learning_stage_exam_attempts(assignment_id BIGINT,student_id BIGINT);
            INSERT INTO students VALUES(1,1);
            INSERT INTO class_offerings VALUES(1,1);
        """)
        self.user = {"id": 1, "role": "student"}
        self.request = SimpleNamespace(url=SimpleNamespace(path="/student"), headers={}, method="GET", state=SimpleNamespace())
        self.future = (datetime.now() + timedelta(hours=1)).isoformat()
        self.past = (datetime.now() - timedelta(hours=1)).isoformat()

    def _drop_owned_schema(self):
        self.raw.rollback()
        self.raw.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.raw.commit()

    def _assert_access(self, label, *, denied=False, request=None):
        with self.subTest(label=label):
            if denied:
                with self.assertRaises(HTTPException) as failure:
                    ensure_ai_workspace_access(self.conn, self.user, request or self.request)
                self.assertEqual(403, failure.exception.status_code)
            else:
                ensure_ai_workspace_access(self.conn, self.user, request or self.request)

    def test_legacy_text_window_ids_and_current_exam_lifecycle(self):
        self._assert_access("empty legacy schema")
        self.conn.execute("INSERT INTO assignments VALUES(124,1,'paper','permanent','published','homework',?,?)",
                          (self.past, self.future))
        self.conn.execute("INSERT INTO assignment_student_windows VALUES('legacy-CaseSensitive',1,'active',?)", (self.future,))
        self._assert_access("ordinary homework with nonnumeric window IDs")
        self.conn.execute("UPDATE assignments SET availability_mode='countdown',assessment_kind='final'")
        self._assert_access("timed exam blocks other tabs", denied=True)
        self.conn.execute("INSERT INTO submissions VALUES(124,1,'submitted',0,0,NULL)")
        self._assert_access("submitted exam releases other tabs")
        self.conn.execute("UPDATE assignments SET status='closed'")
        self.conn.execute("UPDATE submissions SET resubmission_allowed=1,resubmission_due_at=?", (self.future,))
        self._assert_access("closed returned exam blocks other tabs", denied=True)
        self.conn.execute("UPDATE submissions SET resubmission_due_at=?", (self.past,))
        self._assert_access("expired returned window")
        self.conn.execute("DELETE FROM submissions")
        self.conn.execute("INSERT INTO assignment_student_windows VALUES('124',1,'active',?)", (self.future,))
        self._assert_access("closed exam personal window", denied=True)
        self.conn.execute("UPDATE assignment_student_windows SET due_at=? WHERE assignment_id='124'", (self.past,))
        self._assert_access("expired personal window")
        self.conn.execute("UPDATE assignments SET status='published'")
        request = SimpleNamespace(url=SimpleNamespace(path="/exam/take/124"), headers={}, method="GET", state=SimpleNamespace())
        with patch("classroom_app.services.assessment_ai_access.student_exam_ai_access",
                   return_value={"allowed": True, "assignment_id": "124"}):
            self._assert_access("authorized current exam page", request=request)
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES(124,2)")
        self._assert_access("another student's individual exam is excluded")

    def test_expiry_waits_for_real_writer_and_rechecks_resubmit_or_return_token(self):
        from classroom_app.services import submission_write_guard as guard
        from classroom_app.services import group_assignment_service as groups
        from classroom_app.services.resubmission_expiry_service import restore_lapsed_resubmission
        self.raw.execute("""
            ALTER TABLE assignments ADD COLUMN title TEXT;
            ALTER TABLE assignments ADD COLUMN grading_mode TEXT;
            ALTER TABLE class_offerings ADD COLUMN teacher_id BIGINT;
            ALTER TABLE submissions ADD COLUMN id BIGINT PRIMARY KEY;
            ALTER TABLE submissions ADD COLUMN returned_at TEXT;
            ALTER TABLE submissions ADD COLUMN answers_json TEXT;
            CREATE TABLE assignment_group_bindings(assignment_id BIGINT,scheme_id BIGINT,status TEXT);
            CREATE TABLE study_groups(id BIGINT,scheme_id BIGINT);
            CREATE TABLE study_group_members(group_id BIGINT,student_id BIGINT,status TEXT);
            CREATE TABLE group_assignment_member_results(group_id BIGINT,assignment_id BIGINT,student_pk_id BIGINT);
            CREATE TABLE submission_drafts(id BIGINT,assignment_id BIGINT,student_pk_id BIGINT,answers_json TEXT);
            INSERT INTO assignments(id,class_offering_id,title) VALUES(124,1,'Synthetic return');
            INSERT INTO submissions(id,assignment_id,student_pk_id,resubmission_allowed,returned_at,answers_json)
                VALUES(99,124,1,1,'old-token','old answer');
            INSERT INTO submission_drafts VALUES(88,124,1,'student editing');
        """)
        self.raw.commit()

        def expire(entered):
            with connect_offline(cluster_dir=Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"]),
                                 port=int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"]),
                                 database="lanshare_assessment_rehearsal") as raw:
                raw.row_factory = sqlite_compatible_dict_row
                raw.execute(f'SET search_path TO "{self.schema}"')
                adapter = LanSharePostgresConnection(raw)
                class ObservedConnection:
                    def execute(self, statement, params=()):
                        if "pg_advisory_xact_lock" in statement:
                            entered.set()
                        return adapter.execute(statement, params)
                result = restore_lapsed_resubmission(ObservedConnection(),
                                                    {"submission_id": 99, "returned_at": "old-token"})
                raw.commit()
                return result

        with patch.object(guard, "get_configured_db_engine", return_value="postgres"), \
             patch.object(groups, "get_configured_db_engine", return_value="postgres"):
            for returned_again in (False, True):
                with self.subTest(returned_again=returned_again):
                    self.conn.execute("UPDATE submissions SET resubmission_allowed=1,returned_at='old-token'")
                    self.raw.commit()
                    guard.lock_submission_writer(self.conn, 124, 1)
                    entered = Event()
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(expire, entered)
                        try:
                            self.assertTrue(entered.wait(timeout=3), "expiry never used the writer lock")
                            with self.assertRaises(FutureTimeout):
                                future.result(timeout=0.15)
                            self.conn.execute("UPDATE submissions SET resubmission_allowed=?,returned_at='new-token',answers_json='new answer'",
                                              (1 if returned_again else 0,))
                            self.raw.commit()
                        finally:
                            self.raw.rollback()
                        self.assertEqual("skipped: returned again in a later round" if returned_again
                                         else "skipped: already resubmitted or closed", future.result(timeout=5))
                    self.assertEqual("new answer", self.conn.execute("SELECT answers_json FROM submissions").fetchone()[0])
                    self.assertEqual("student editing", self.conn.execute("SELECT answers_json FROM submission_drafts").fetchone()[0])
