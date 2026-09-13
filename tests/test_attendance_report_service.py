"""Isolated lifecycle tests: no production DB, accounts, network, or AI calls."""
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.db.schema_attendance_reports import ensure_attendance_report_schema, ATTENDANCE_REPORT_REQUIRED_COLUMNS
from classroom_app.services import attendance_report_service as service
from classroom_app.services.attendance_fact_service import summarize_attendance, load_confirmed_attendance_scores, load_confirmed_attendance_facts
from classroom_app.services.file_service import store_file_object_globally
from classroom_app.services.smart_classroom_attendance_adapter import external_account_key


class AttendanceReportServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(patch.stopall)
        patch("classroom_app.services.attendance_report_service.get_configured_db_engine", return_value="sqlite").start()
        patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite").start()
        patch("classroom_app.services.ai_durable_job_service.get_configured_db_engine", return_value="sqlite").start()
        patch("classroom_app.services.file_service.GLOBAL_FILES_DIR", Path(self.directory.name)).start()
        patch("classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS", ()).start()
        for flag in ("ATTENDANCE_ARCHIVE_ENABLED", "ATTENDANCE_PARSE_ENABLED", "ATTENDANCE_CONFIRMED_FACTS_ENABLED"):
            patch("classroom_app.config." + flag, True).start()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE teachers(id INTEGER PRIMARY KEY);
            INSERT INTO teachers VALUES(1),(2);
            CREATE TABLE submissions(id INTEGER PRIMARY KEY);
            CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,name TEXT);
            INSERT INTO academic_semesters VALUES(1,'2025-2026第二学期'),(2,'2025-2026第一学期');
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER,class_id INTEGER,semester TEXT,semester_id INTEGER);
            INSERT INTO class_offerings VALUES(10,1,20,'2025-2026第二学期',1),(11,1,20,'2025-2026第一学期',2),(12,2,21,'2025-2026第二学期',1);
            CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
            CREATE TABLE students(id INTEGER PRIMARY KEY,student_id_number TEXT,name TEXT,class_id INTEGER);
            INSERT INTO students VALUES(30,'000123','合成甲',20),(31,'90000000001','合成乙',20),(32,'000123','他班合成',21);
            CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,session_date TEXT,academic_section_text TEXT);
            INSERT INTO class_offering_sessions VALUES(40,10,'2026-03-09','1-2'),(41,10,'2026-03-16','1-2'),(42,12,'2026-03-09','1-2');
            CREATE TABLE teacher_smart_classroom_credentials(id INTEGER PRIMARY KEY,teacher_id INTEGER,enabled INTEGER,last_status TEXT,platform_code TEXT,username TEXT);
            INSERT INTO teacher_smart_classroom_credentials VALUES(50,1,1,'verified','gxufl_smart_classroom','synthetic-teacher');
        """)
        reset_ai_job_schema_guard_for_tests()
        self.addCleanup(reset_ai_job_schema_guard_for_tests)
        ensure_ai_job_schema(self.conn, engine="sqlite")
        ensure_attendance_report_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.user = {"id": 1, "role": "teacher"}
        self.source = {"academic_year": "2025-2026", "academic_term": 2, "remote_schedule_id": "opaque-a", "course_name": "合成课程", "course_code": "SYN-1",
                       "teaching_class_name": "合成教学班", "school_code": "gxufl", "platform_code": "gxufl_smart_classroom", "credential_id": 50,
                       "external_account_key": external_account_key({"username": "synthetic-teacher"})}
        self.binding = self._binding()

    def _binding(self, source=None, offering=10):
        result = service.save_attendance_binding(self.conn, self.user, source_token=service.sign_attendance_source_option(1, source or self.source), class_offering_id=offering)
        self.conn.commit()
        return result["binding"]

    def _export(self, key="export-one", binding=None):
        b = binding or self.binding
        result = service.enqueue_attendance_export(self.conn, self.user, binding_id=b["id"], expected_binding_revision=b["revision"], idempotency_key=key)
        self.conn.commit()
        return result

    def _claim(self, job_id, token="lease"):
        expires = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
        self.conn.execute("UPDATE ai_jobs SET status='running',lease_token=?,lease_expires_at=? WHERE id=?", (token, expires, job_id))
        self.conn.commit()

    def _cached(self, binding=None):
        export = self._export(binding=binding)
        self._claim(export["job_id"])
        stored = store_file_object_globally(io.BytesIO(b"%PDF-1.7\nsynthetic-archive-test\n"))
        source = {"file_hash": stored["hash"], "byte_size": stored["size"], "page_count": 1, "filename": "synthetic.pdf", "request_manifest": {}, "checkin_manifest": {}}
        parsed = service.cache_attendance_source(self.conn, export["job_id"], "lease", source)
        self.conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?", (export["job_id"],))
        self.conn.commit()
        return export, parsed

    def _result(self, unknown=False):
        students = [{"row_index": 1, "student_number": "000123", "source_name": "合成甲", "source_class_name": "合成班"}, {"row_index": 2, "student_number": "90000000001", "source_name": "合成乙", "source_class_name": "合成班"}]
        sessions = [{"column_index": 1, "source_header": "03-09 08:00", "source_datetime": "2026-03-09 08:00:00", "section": 1, "remote_checkin_id": "event-a"},
                    {"column_index": 2, "source_header": "03-16 08:00", "source_datetime": "2026-03-16 08:00:00", "section": 1, "remote_checkin_id": "event-b"}]
        cells = [{"row_index": row, "column_index": col, "normalized_status": "UNCHECKED" if row == col == 2 else "CHECKED", "raw_text": "出勤", "quality_state": "verified", "interpretation_method": "text+ai"} for row in (1, 2) for col in (1, 2)]
        if unknown:
            cells[0].update(normalized_status="UNKNOWN", quality_state="unknown")
        return {"students": students, "sessions": sessions, "cells": cells, "ai_used": True, "model_id": "synthetic-model", "coverage": {"processed_pages": [1]}, "ai_coverage": {"processed_pages": [1]}, "validation": {"blockers": [], "warnings": []}}

    def _parsed(self, unknown=False, binding=None, result=None):
        export, parsed = self._cached(binding=binding)
        self._claim(parsed["job_id"])
        saved = service.save_attendance_parse_result(self.conn, parsed["job_id"], "lease", result or self._result(unknown))
        self.conn.commit()
        return export, parsed, saved

    def _confirmed(self, binding=None):
        export, parsed, _ = self._parsed(binding=binding)
        run = self.conn.execute("SELECT revision FROM attendance_parse_runs WHERE id=?", (parsed["parse_run_id"],)).fetchone()
        report = service.get_attendance_report(self.conn, export["report_id"], self.user)
        service.confirm_attendance_run(self.conn, self.user, report_id=report["id"], run_id=parsed["parse_run_id"],
                                       expected_run_revision=run["revision"], expected_report_revision=report["revision"])
        self.conn.commit()
        return export, parsed

    def test_schema_contract_and_cross_run_foreign_keys(self):
        for table, required in ATTENDANCE_REPORT_REQUIRED_COLUMNS.items():
            actual = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            self.assertTrue(set(required) <= actual)
        export, parsed, _ = self._parsed()
        self.conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?", (parsed["job_id"],))
        second = service.enqueue_attendance_parse(self.conn, self.user, report_id=export["report_id"], source_version_id=export["source_version_id"], idempotency_key="run2")
        row = self.conn.execute("SELECT id FROM attendance_report_students WHERE parse_run_id=?", (parsed["parse_run_id"],)).fetchone()[0]
        col = self.conn.execute("SELECT id FROM attendance_report_sessions WHERE parse_run_id=?", (parsed["parse_run_id"],)).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO attendance_report_cells(parse_run_id,student_row_id,session_column_id) VALUES(?,?,?)", (second["parse_run_id"], row, col))

    def test_active_export_deduplication_and_exact_request_retry(self):
        first = self._export()
        second = self._export("different-tab")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["job_id"], self._export()["job_id"])
        self.conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?", (first["job_id"],))
        third = self._export("fresh-explicit-request")
        self.assertNotEqual(first["source_version_id"], third["source_version_id"])

    def test_unknown_review_cas_then_confirm_and_frozen_history(self):
        export, parsed, result = self._parsed(unknown=True)
        self.assertEqual(result["state"], "needs_review")
        run = dict(self.conn.execute("SELECT * FROM attendance_parse_runs WHERE id=?", (parsed["parse_run_id"],)).fetchone())
        report = service.get_attendance_report(self.conn, export["report_id"], self.user)
        with self.assertRaises(HTTPException):
            service.confirm_attendance_run(self.conn, self.user, report_id=report["id"], run_id=run["id"], expected_run_revision=run["revision"], expected_report_revision=report["revision"])
        cell = self.conn.execute("SELECT id FROM attendance_report_cells WHERE parse_run_id=? AND normalized_status='UNKNOWN'", (run["id"],)).fetchone()[0]
        change = dict(report_id=report["id"], run_id=run["id"], target_type="cell", target_id=cell, changes={"normalized_status": "CHECKED"}, reason="逐格核对原件", expected_revision=run["revision"])
        reviewed = service.review_attendance_run(self.conn, self.user, **change)
        with self.assertRaises(HTTPException) as conflict:
            service.review_attendance_run(self.conn, self.user, **change)
        self.assertEqual(conflict.exception.status_code, 409)
        confirmed = service.confirm_attendance_run(self.conn, self.user, report_id=report["id"], run_id=run["id"], expected_run_revision=reviewed["run"]["revision"], expected_report_revision=report["revision"])
        self.conn.commit()
        self.assertEqual(confirmed["run"]["state"], "confirmed")
        with self.assertRaises(HTTPException):
            service.save_attendance_parse_result(self.conn, parsed["job_id"], "lease", self._result())
        self.assertEqual(load_confirmed_attendance_scores(self.conn, class_offering_id=10, teacher_id=1), {30: 100.0, 31: 50.0})

    def test_worker_recovery_returns_published_result_without_reparse(self):
        export, parsed, _ = self._parsed()
        self._claim(parsed["job_id"], "replacement")
        done = service.completed_attendance_job_result(self.conn, parsed["job_id"], "replacement")
        self.assertTrue(done["completed"])
        self.assertEqual(done["state"], "validated")
        with self.assertRaises(HTTPException):
            service.load_attendance_job_context(self.conn, parsed["job_id"], "lease")

    def test_account_change_does_not_block_cached_reparse(self):
        export, parsed = self._cached()
        self.conn.execute("DELETE FROM teacher_smart_classroom_credentials")
        self.conn.commit()
        self._claim(parsed["job_id"])
        result = service.save_attendance_parse_result(self.conn, parsed["job_id"], "lease", self._result())
        self.assertEqual(result["state"], "validated")

    def test_tampered_source_wrong_term_and_cross_owner_are_rejected(self):
        token = service.sign_attendance_source_option(1, self.source)
        with self.assertRaises(HTTPException):
            service.save_attendance_binding(self.conn, self.user, source_token=token + "bad", class_offering_id=10)
        with self.assertRaises(HTTPException):
            self._binding(offering=11)
        self.conn.rollback()
        export = self._export()
        for user in ({"id": 2, "role": "teacher"}, {"id": 1, "role": "student"}):
            with self.assertRaises(HTTPException):
                service.attendance_report_detail(self.conn, user, export["report_id"])

    def test_soft_delete_cancels_worker_but_retains_authorized_original(self):
        export, parsed = self._cached()
        report = service.get_attendance_report(self.conn, export["report_id"], self.user)
        service.set_attendance_report_deleted(self.conn, self.user, report["id"], expected_revision=report["revision"], deleted=True)
        self.conn.commit()
        self.assertEqual(service.get_attendance_job(self.conn, parsed["job_id"], self.user)["job"]["status"], "cancelled")
        path, _ = service.attendance_source_file(self.conn, self.user, report["id"], export["source_version_id"])
        self.assertTrue(path.exists())
        self.assertEqual(service.list_attendance_reports(self.conn, self.user)["total"], 0)
        self.assertEqual(service.list_attendance_reports(self.conn, self.user, deleted=1)["total"], 1)

    def test_denominator_unknown_conflict_and_zero_are_not_zero_marks(self):
        summary = summarize_attendance([{"status": "CHECKED"}], expected_count=2)
        self.assertIsNone(summary["attendance_rate"])
        self.assertEqual(summary["known_attendance_rate"], 100)
        self.assertEqual(summary["completeness_rate"], 50)
        self.assertEqual(summarize_attendance([{"status": "CHECKED", "quality_state": "conflict"}])["unknown"], 1)
        self.assertIsNone(summarize_attendance([])["attendance_rate"])

    def test_parse_pause_keeps_cached_source_and_worker_recovery(self):
        with patch("classroom_app.config.ATTENDANCE_PARSE_ENABLED", False):
            export, cached = self._cached()
            self.assertIsNone(cached["parse_run_id"])
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM attendance_parse_runs").fetchone()[0], 0)
            self._claim(export["job_id"], "recovered")
            self.assertTrue(service.completed_attendance_job_result(self.conn, export["job_id"], "recovered")["completed"])
            with self.assertRaises(HTTPException):
                service.enqueue_attendance_parse(self.conn, self.user, report_id=export["report_id"], source_version_id=export["source_version_id"], idempotency_key="paused")
            self.assertTrue(service.attendance_source_file(self.conn, self.user, export["report_id"], export["source_version_id"])[0].exists())

    def test_archive_pause_preserves_reads_and_blocks_new_exports(self):
        export, _ = self._cached()
        with patch("classroom_app.config.ATTENDANCE_ARCHIVE_ENABLED", False):
            self.assertEqual(service.attendance_report_detail(self.conn, self.user, export["report_id"])["report"]["id"], export["report_id"])
            self.assertFalse(service.list_attendance_bindings(self.conn, self.user)["archive_enabled"])
            with self.assertRaises(HTTPException):
                self._export("disabled")
            with self.assertRaises(HTTPException):
                self._binding()

    def test_credential_switch_blocks_publication_before_file_binding(self):
        export = self._export()
        self._claim(export["job_id"])
        self.conn.execute("UPDATE teacher_smart_classroom_credentials SET username='different-account'")
        stored = store_file_object_globally(io.BytesIO(b"%PDF-1.7\nsynthetic-switched-account\n"))
        with self.assertRaises(HTTPException):
            service.cache_attendance_source(self.conn, export["job_id"], "lease", {"file_hash": stored["hash"], "byte_size": stored["size"], "page_count": 1})
        self.conn.rollback()
        version = self.conn.execute("SELECT * FROM attendance_report_versions WHERE id=?", (export["source_version_id"],)).fetchone()
        self.assertIsNone(version["source_file_hash"])

    def test_ai_failure_and_missing_page_remain_unconfirmable(self):
        result = self._result(); result["ai_used"] = False; result["ai_coverage"] = {"processed_pages": []}
        export, parsed, saved = self._parsed(result=result)
        self.assertEqual(saved["state"], "needs_review")
        run = self.conn.execute("SELECT * FROM attendance_parse_runs WHERE id=?", (parsed["parse_run_id"],)).fetchone()
        report = service.get_attendance_report(self.conn, export["report_id"], self.user)
        with self.assertRaises(HTTPException):
            service.confirm_attendance_run(self.conn, self.user, report_id=report["id"], run_id=run["id"], expected_run_revision=run["revision"], expected_report_revision=report["revision"])

    def test_multiple_sources_require_persisted_selection_and_cas(self):
        first, _ = self._confirmed()
        second_binding = self._binding({**self.source, "remote_schedule_id": "opaque-b"})
        self._confirmed(binding=second_binding)
        facts = load_confirmed_attendance_facts(self.conn, class_offering_id=10, teacher_id=1)
        self.assertFalse(facts["available"])
        selected = service.select_attendance_grade_source(self.conn, self.user, self.binding["id"], expected_revision=self.binding["revision"])
        self.conn.commit()
        self.assertTrue(selected["binding"]["is_grade_source"])
        self.assertEqual(load_confirmed_attendance_facts(self.conn, class_offering_id=10, teacher_id=1)["report_id"], first["report_id"])
        with self.assertRaises(HTTPException):
            service.select_attendance_grade_source(self.conn, self.user, self.binding["id"], expected_revision=self.binding["revision"])
        with patch("classroom_app.config.ATTENDANCE_CONFIRMED_FACTS_ENABLED", False):
            self.assertIsNone(load_confirmed_attendance_scores(self.conn, class_offering_id=10, teacher_id=1))

    def test_current_roster_missing_row_is_strict_and_duplicate_local_mapping_rejected(self):
        _, parsed = self._confirmed()
        self.conn.execute("INSERT INTO students VALUES(33,'90000000003','新合成学生',20)")
        scores = load_confirmed_attendance_scores(self.conn, class_offering_id=10, teacher_id=1)
        self.assertTrue(scores.strict)
        self.assertNotIn(33, scores)
        self.conn.execute("UPDATE attendance_report_students SET local_student_id=30 WHERE parse_run_id=?", (parsed["parse_run_id"],))
        with self.assertRaisesRegex(ValueError, "同一学生"):
            load_confirmed_attendance_scores(self.conn, class_offering_id=10, teacher_id=1)


if __name__ == "__main__":
    unittest.main()
