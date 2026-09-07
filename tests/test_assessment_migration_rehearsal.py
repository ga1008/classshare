import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.assessment_migration_rehearsal import apply_assessment_migrations, file_digest, rehearse_sqlite


def make_backup(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE assignments(id INTEGER PRIMARY KEY, title TEXT, status TEXT,
                ordinary_grade_kind_override TEXT, requirements_md TEXT, rubric_md TEXT);
            CREATE TABLE submissions(id INTEGER PRIMARY KEY, assignment_id INTEGER, student_pk_id INTEGER,
                score REAL, feedback_md TEXT, answers_json TEXT, status TEXT, is_absence_score INTEGER,
                grading_attempt_fingerprint TEXT, late_policy_snapshot_json TEXT);
            CREATE TABLE submission_files(id INTEGER PRIMARY KEY, submission_id INTEGER REFERENCES submissions(id), relative_path TEXT, original_filename TEXT);
            CREATE TABLE submission_grade_revisions(id INTEGER PRIMARY KEY, submission_id INTEGER,
                revision_hash TEXT, revision_no INTEGER, status TEXT, score REAL, feedback_md TEXT,
                quality_audit_json TEXT, provenance_json TEXT, created_at TEXT, activated_at TEXT, superseded_at TEXT);
            CREATE TABLE material_ai_import_records(id INTEGER PRIMARY KEY, export_payload_json TEXT, docx_bytes BLOB);
            INSERT INTO assignments VALUES (1,'历史期末考试','closed','assignment','原题目','原量表'), (2,'课堂练习','published',NULL,'正文','量表');
            INSERT INTO submissions VALUES (10,1,8,82.5,'人工改分','{"q":"截图答案"}','grading',0,'old-token','{"penalty":5}'),
                (11,1,9,0,'缺交0','','graded',1,NULL,'{}'), (12,2,8,NULL,'待处理','{}','grading_review',0,NULL,'{}');
            INSERT INTO submission_files VALUES (1,10,'answer.png','原截图.png');
            INSERT INTO submission_grade_revisions VALUES (1,10,'hash',1,'active',82.5,'人工评语','{}','{"source":"manual"}','old','old',NULL);
            INSERT INTO material_ai_import_records VALUES (1,'{"formula":"平时×0.4+期末×0.6","final_score":79}',X'000AFF');
        """)


class AssessmentMigrationRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backup = self.root / "offline-backup.sqlite"
        self.working = self.root / "working.sqlite"
        self.files = self.root / "attachments"
        self.files.mkdir()
        (self.files / "answer.png").write_bytes(b"offline attachment bytes")
        make_backup(self.backup)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_additive_migration_twice_preserves_old_grades_materials_and_backup(self):
        source_hash = file_digest(self.backup)
        result = rehearse_sqlite(backup=self.backup, working_copy=self.working, attachment_root=self.files)
        self.assertEqual("ok", result["status"])
        self.assertEqual(source_hash, file_digest(self.backup))
        self.assertTrue(result["database_preservation_passed"])
        self.assertTrue(result["attachments"]["verified"])
        self.assertFalse(result["deployment_gate_complete"])
        self.assertEqual([], result["idempotency_differences"])
        self.assertEqual(2, len(result["stages"]))
        self.assertEqual(3, result["before"]["tables"]["submissions"]["row_count"])
        self.assertIn("assessment_kind", result["added_columns"]["assignments"])
        with closing(sqlite3.connect(self.working)) as conn:
            self.assertEqual([(None, "assignment"), (None, None)], conn.execute("SELECT assessment_kind, ordinary_grade_kind_override FROM assignments ORDER BY id").fetchall())
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("截图答案", rendered)
        self.assertNotIn("原截图.png", rendered)
        self.assertNotIn("人工评语", rendered)

    def test_old_score_mutation_is_a_failed_gate_even_if_second_run_is_idempotent(self):
        def destructive(conn):
            apply_assessment_migrations(conn)
            conn.execute("UPDATE submissions SET score=99 WHERE id=10")
        result = rehearse_sqlite(backup=self.backup, working_copy=self.working, migrate=destructive)
        self.assertEqual("failed", result["status"])
        self.assertIn("table:submissions", result["stages"][0]["old_field_differences"])
        self.assertEqual([], result["idempotency_differences"])

    def test_second_run_new_row_is_detected_even_when_all_old_fields_are_unchanged(self):
        def non_idempotent(conn):
            apply_assessment_migrations(conn)
            conn.execute("CREATE TABLE IF NOT EXISTS accidental_extra_rows(value INTEGER)")
            conn.execute("INSERT INTO accidental_extra_rows VALUES (1)")
        result = rehearse_sqlite(backup=self.backup, working_copy=self.working, migrate=non_idempotent)
        self.assertEqual("failed", result["status"])
        self.assertEqual([], result["stages"][0]["old_field_differences"])
        self.assertIn("table:accidental_extra_rows", result["idempotency_differences"])

    def test_existing_destination_is_never_overwritten_and_missing_files_are_explicit(self):
        self.working.write_bytes(b"keep me")
        with self.assertRaises(ValueError):
            rehearse_sqlite(backup=self.backup, working_copy=self.working)
        self.assertEqual(b"keep me", self.working.read_bytes())
        with closing(sqlite3.connect(self.backup)) as conn:
            conn.execute("UPDATE submission_files SET relative_path='../outside-secret.txt'")
            conn.commit()
        result = rehearse_sqlite(backup=self.backup, working_copy=self.root / "another.sqlite", attachment_root=self.files)
        self.assertEqual("failed", result["status"])
        self.assertEqual(1, result["attachments"]["before"]["outside_root"])

    def test_omitting_attachment_root_does_not_claim_files_or_postgres_verified(self):
        result = rehearse_sqlite(backup=self.backup, working_copy=self.working)
        self.assertEqual("ok", result["status"])
        self.assertFalse(result["attachments"]["verified"])
        self.assertEqual("not_requested", result["attachments"]["after"]["status"])
        self.assertFalse(result["deployment_gate_complete"])
        self.assertTrue(any("PostgreSQL" in gate for gate in result["remaining_gates"]))


if __name__ == "__main__":
    unittest.main()
