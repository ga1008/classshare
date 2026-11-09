"""Imported attendance approval transactions on synthetic SQLite; no remote calls."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_user
from classroom_app.routers import smart_attendance_appeals as routes
from classroom_app.services import approval_workflow_schema as schema, approval_workflow_service as workflow
from classroom_app.services import attendance_dashboard_service as dashboard, smart_attendance_appeal_service as service
from classroom_app.services.approval_request_types import smart_attendance_appeal as appeal


class SmartAttendanceAppealTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch.object(schema, "get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch.object(schema, "_SCHEMA_READY", False))
        self.enterContext(patch.object(service, "get_configured_db_engine", return_value="sqlite"))
        self.conn.executescript("""
            CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT,is_super_admin INTEGER,is_active INTEGER DEFAULT 1);
            INSERT INTO teachers VALUES(1,'Synthetic original',0,1),(2,'Synthetic current',0,1),(3,'Synthetic admin',1,1);
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER,closed INTEGER DEFAULT 0);
            INSERT INTO class_offerings VALUES(11,1,0);
            CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,student_id_number TEXT);
            INSERT INTO students VALUES(21,'Synthetic student','S21'),(22,'Other synthetic','S22');
            CREATE TABLE smart_classroom_checkin_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,session_id INTEGER,match_status TEXT);
            INSERT INTO smart_classroom_checkin_sessions VALUES(31,11,41,'matched');
            CREATE TABLE smart_classroom_checkin_students(id INTEGER PRIMARY KEY,checkin_session_id INTEGER,student_id INTEGER,
                class_offering_id INTEGER,session_id INTEGER,local_match_status TEXT,status TEXT,synced_at TEXT);
            INSERT INTO smart_classroom_checkin_students VALUES(51,31,21,11,41,'matched','UNCHECKED','2026-11-09');
            CREATE TABLE attendance_reports(id INTEGER PRIMARY KEY,confirmed_parse_run_id INTEGER,deleted_at TEXT);
            CREATE TABLE attendance_report_versions(id INTEGER PRIMARY KEY,report_id INTEGER);
            CREATE TABLE attendance_parse_runs(id INTEGER PRIMARY KEY,source_version_id INTEGER,mapped_offering_id INTEGER,state TEXT);
            CREATE TABLE attendance_report_students(id INTEGER PRIMARY KEY,parse_run_id INTEGER,local_student_id INTEGER,identity_state TEXT);
            CREATE TABLE attendance_report_sessions(id INTEGER PRIMARY KEY,parse_run_id INTEGER,local_session_id INTEGER,mapping_state TEXT);
            CREATE TABLE attendance_report_cells(id INTEGER PRIMARY KEY,parse_run_id INTEGER,student_row_id INTEGER,
                session_column_id INTEGER,normalized_status TEXT,quality_state TEXT);
            INSERT INTO attendance_reports VALUES(61,63,NULL);
            INSERT INTO attendance_report_versions VALUES(62,61);
            INSERT INTO attendance_parse_runs VALUES(63,62,11,'confirmed');
            INSERT INTO attendance_report_students VALUES(64,63,21,'matched');
            INSERT INTO attendance_report_sessions VALUES(65,63,41,'matched');
            INSERT INTO attendance_report_cells VALUES(66,63,64,65,'UNCHECKED','verified');
        """)
        schema.ensure_approval_workflow_schema(self.conn)
        self.conn.commit()
        self.teacher = {"role": "teacher", "id": 1, "name": "Synthetic original"}
        self.current_teacher = {"role": "teacher", "id": 2, "name": "Synthetic current"}
        self.student = {"role": "student", "id": 21, "name": "Synthetic student"}
        self.selected = "api:51"
        self.ended = service.now_ms() - 60_000
        self.enterContext(patch.object(dashboard, "load_student_session_fact", side_effect=self.selected_fact))
        self.notifications = []
        self.enterContext(patch.object(workflow, "_notify", side_effect=self.notify))

    def selected_fact(self, conn, offering_id, session_id, user, *, student_id=None):
        # The dashboard owns source selection; this boundary supplies selected
        # descriptors from real fixture rows, never an arbitrary client status.
        if self.selected == "api:51":
            row = conn.execute("SELECT * FROM smart_classroom_checkin_students WHERE id=51").fetchone()
            source, run = "smart_synced", 31
            status = row["status"] if row else None
        else:
            row = conn.execute("SELECT * FROM attendance_report_cells WHERE id=66").fetchone()
            source, run = "confirmed_pdf", 63
            status = row["normalized_status"] if row else None
        if not row: return None
        owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=11").fetchone()
        fingerprint = hashlib.sha256(f"{self.selected}:11:41:21:{run}:{status}".encode()).hexdigest()
        return {"source": source, "source_key": self.selected, "source_fingerprint": fingerprint, "source_id": row["id"],
            "source_run_id": run, "class_offering_id": 11, "session_id": 41, "student_id": 21, "teacher_id": owner[0],
            "source_status": status, "status": "PRESENT" if status == "CHECKED" else "ABSENT",
            "ended_at_ms": self.ended, "appeal_days": 7}

    def notify(self, conn, **kwargs):
        self.notifications.append(kwargs)
        return len(kwargs["recipients"])

    def fact(self):
        return service.current_fact(self.conn, self.selected, self.student)

    def create(self):
        request = workflow.create_request(self.conn, self.student, request_type=appeal.KEY, subject_id=self.selected,
            reason="Please check this synthetic record", payload={"source_fingerprint": self.fact()["source_fingerprint"]})
        self.conn.commit()
        return request

    def decide(self, request, *, reviewer=None, decision="approve", status="PRESENT", fingerprint=None):
        result = workflow.decide_request(self.conn, reviewer or self.teacher, request["id"], decision=decision,
            note="Synthetic reviewed explanation", decision_payload={"status": status,
                "source_fingerprint": fingerprint or self.fact()["source_fingerprint"]})
        self.conn.commit()
        return result

    def overlay(self):
        return service.load_smart_appeal_states(self.conn, [self.fact()], viewer=self.student)[self.selected]

    def test_approved_correction_preserves_source_and_survives_later_rejection(self):
        before = dict(self.conn.execute("SELECT * FROM smart_classroom_checkin_students").fetchone())
        request = self.create()
        self.assertEqual([{"role": "teacher", "id": 1}], self.notifications[0]["recipients"])
        self.assertFalse(self.overlay()["can_appeal"])
        with self.assertRaises(workflow.ApprovalWorkflowError): self.create()
        self.conn.rollback()
        self.decide(request)
        overlay = self.overlay()
        self.assertEqual("PRESENT", overlay["effective_status"])
        self.assertTrue(overlay["correction"]["local_only"])
        self.assertEqual("not_written", overlay["correction"]["remote_write_status"])
        self.assertEqual(before, dict(self.conn.execute("SELECT * FROM smart_classroom_checkin_students").fetchone()))
        self.assertIn("尚未写回智慧课堂", self.notifications[-1]["body"])
        later = self.create()
        self.decide(later, decision="reject")
        self.assertEqual("rejected", self.overlay()["appeal"]["status"])
        self.assertEqual("PRESENT", self.overlay()["effective_status"])
        self.assertEqual(4, self.conn.execute("SELECT COUNT(*) FROM approval_request_events").fetchone()[0])

    def test_refresh_is_stable_but_changed_fact_retains_stale_correction(self):
        request = self.create(); self.decide(request, status="SICK_LEAVE")
        self.conn.execute("UPDATE smart_classroom_checkin_students SET synced_at='later-sync'"); self.conn.commit()
        self.assertFalse(self.overlay()["correction"]["stale"])
        self.conn.execute("UPDATE smart_classroom_checkin_students SET status='CHECKED'"); self.conn.commit()
        overlay = self.overlay()
        self.assertEqual("PENDING", overlay["effective_status"])
        self.assertTrue(overlay["correction"]["stale"])
        self.assertEqual("SICK_LEAVE", overlay["correction"]["status"])
        self.assertEqual("CHECKED", self.fact()["source_status"])
        payload = json.loads(workflow._load_request_row(self.conn, request["id"])["payload_json"])
        payload["history_only"] = True
        self.conn.execute("UPDATE approval_requests SET payload_json=? WHERE id=?", (json.dumps(payload), request["id"])); self.conn.commit()
        self.assertIsNone(self.overlay()["correction"])

    def test_only_current_teacher_can_read_remind_and_decide_after_transfer(self):
        request = self.create()
        self.conn.execute("UPDATE class_offerings SET teacher_id=2"); self.conn.commit()
        self.assertEqual([], workflow.list_requests(self.conn, self.teacher))
        self.assertEqual(0, workflow.count_pending_requests(self.conn, self.teacher))
        self.assertEqual(1, workflow.count_pending_requests(self.conn, self.current_teacher))
        with self.assertRaises(workflow.ApprovalWorkflowError): workflow.get_request(self.conn, self.teacher, request["id"])
        self.assertTrue(workflow.get_request(self.conn, self.current_teacher, request["id"])["can_decide"])
        self.decide(request, reviewer=self.current_teacher)
        self.assertEqual("PRESENT", self.overlay()["effective_status"])

    def test_source_change_blocks_approval_but_missing_source_remains_rejectable(self):
        request = self.create(); fingerprint = self.fact()["source_fingerprint"]
        self.conn.execute("UPDATE smart_classroom_checkin_students SET status='CHECKED'"); self.conn.commit()
        with self.assertRaises(workflow.ApprovalWorkflowError): self.decide(request, fingerprint=fingerprint)
        self.conn.rollback()
        self.assertEqual("pending", workflow._load_request_row(self.conn, request["id"])["status"])
        self.conn.execute("DELETE FROM smart_classroom_checkin_students"); self.conn.commit()
        detail = workflow.get_request(self.conn, self.teacher, request["id"])["detail"]["attendance"]
        self.assertFalse(detail["current_source_available"])
        self.assertEqual("UNCHECKED", detail["source_snapshot"]["source_status"])
        result = workflow.decide_request(self.conn, self.teacher, request["id"], decision="reject", note="Source withdrawn; reviewed history")
        self.conn.commit()
        self.assertEqual("rejected", result["status"])

    def test_other_identity_unmatched_unconfirmed_future_and_expired_are_denied(self):
        other = {**self.student, "id": 22}
        with self.assertRaises(workflow.ApprovalWorkflowError): service.current_fact(self.conn, self.selected, other)
        self.conn.execute("UPDATE smart_classroom_checkin_students SET local_match_status='remote_only'"); self.conn.commit()
        with self.assertRaises(workflow.ApprovalWorkflowError): self.create()
        self.conn.rollback()
        self.conn.execute("UPDATE smart_classroom_checkin_students SET local_match_status='matched'"); self.conn.commit()
        self.ended = service.now_ms() + 60_000
        self.assertFalse(self.overlay()["can_appeal"])
        self.ended = service.now_ms() - 8 * 86400_000
        self.assertFalse(self.overlay()["can_appeal"])
        self.selected = "report:66"
        self.conn.execute("UPDATE attendance_parse_runs SET state='validated'"); self.conn.commit()
        with self.assertRaises(workflow.ApprovalWorkflowError): self.create()

    def test_confirmed_pdf_closed_course_appeal_is_local_and_accepted_case_does_not_expire(self):
        self.selected = "report:66"
        self.conn.execute("UPDATE class_offerings SET closed=1"); self.conn.commit()
        original = dict(self.conn.execute("SELECT * FROM attendance_report_cells").fetchone())
        request = self.create()
        self.conn.execute("UPDATE approval_requests SET created_at='2020-01-01T00:00:00' WHERE id=?", (request["id"],)); self.conn.commit()
        result = workflow.remind_stale_requests(self.conn); self.conn.commit()
        self.assertEqual(0, result["expired"])
        self.decide(request, status="PERSONAL_LEAVE")
        self.assertEqual(original, dict(self.conn.execute("SELECT * FROM attendance_report_cells").fetchone()))
        self.assertEqual("PERSONAL_LEAVE", self.overlay()["effective_status"])

    def test_http_privacy_same_origin_and_untrusted_student_identity(self):
        app = FastAPI(); app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: self.student
        @contextmanager
        def connect():
            try:
                yield self.conn
            except BaseException:
                self.conn.rollback(); raise
        with patch.object(routes, "get_db_connection", side_effect=connect), TestClient(app) as client:
            url = "/api/classrooms/11/smart-attendance/records/api:51"
            response = client.get(url)
            self.assertEqual(200, response.status_code)
            self.assertEqual("private, no-store", response.headers["cache-control"])
            self.assertEqual("Cookie, Authorization", response.headers["vary"])
            body = {"reason": "Synthetic verify", "source_fingerprint": response.json()["source_fingerprint"]}
            blocked = client.post(url + "/appeals", json=body, headers={"Origin": "https://other.example.invalid"})
            self.assertEqual(403, blocked.status_code)
            accepted = client.post(url + "/appeals", json={**body, "student_id": 22}, headers={"Origin": "http://testserver"})
            self.assertEqual(200, accepted.status_code, accepted.text)
            stored = workflow._load_request_row(self.conn, accepted.json()["request"]["id"])
            self.assertEqual(21, stored["applicant_user_pk"])


class SmartAttendanceDashboardIntegrationTests(unittest.TestCase):
    def test_real_dashboard_source_approval_and_repeat_sync_roundtrip(self):
        from classroom_app.db.postgres_indexes import _build_in_memory_sqlite_schema
        from classroom_app.db.schema_attendance_reports import ensure_attendance_report_schema
        from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
        from classroom_app.db.schema_retake import ensure_retake_schema
        from classroom_app.db.schema_offering_class_links import ensure_offering_class_links_schema
        from classroom_app.services import login_attendance_service as local
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch.object(schema, "get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch.object(schema, "_SCHEMA_READY", False))
        self.enterContext(patch.object(service, "get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch.object(local, "get_configured_db_engine", return_value="sqlite"))
        conn = _build_in_memory_sqlite_schema()
        self.addCleanup(conn.close)
        ensure_attendance_report_schema(conn, engine="sqlite")
        ensure_login_attendance_schema(conn, engine="sqlite")
        ensure_retake_schema(conn, engine="sqlite", force=True)
        ensure_offering_class_links_schema(conn, engine="sqlite", force=True)
        schema.ensure_approval_workflow_schema(conn)
        conn.executescript("""
            INSERT INTO teachers(id,name,email,hashed_password) VALUES(1,'Synthetic teacher','appeal@example.invalid','x');
            INSERT INTO classes(id,name,created_by_teacher_id) VALUES(2,'Synthetic class',1);
            INSERT INTO courses(id,name,created_by_teacher_id) VALUES(3,'Synthetic course',1);
            INSERT INTO class_offerings(id,class_id,course_id,teacher_id) VALUES(11,2,3,1);
            INSERT INTO students(id,student_id_number,name,class_id) VALUES(21,'S21','Synthetic student',2);
            INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,academic_section_text)
                VALUES(41,11,1,'Synthetic lesson','2026-11-09',0,'2,3');
        """)
        stamp = (datetime.now() - timedelta(minutes=1)).isoformat()
        conn.execute("""INSERT INTO smart_classroom_checkin_sessions(id,teacher_id,class_offering_id,session_id,
            platform_code,remote_checkin_id,match_status,total_count,checkin_time,stop_time,synced_at)
            VALUES(31,1,11,41,'gxufl_smart_classroom','synthetic-remote','matched',1,?,?,?)""", (stamp, stamp, stamp))
        conn.execute("""INSERT INTO smart_classroom_checkin_students(id,checkin_session_id,teacher_id,class_offering_id,
            session_id,student_id,student_number,student_name,status,status_label,local_match_status,synced_at)
            VALUES(51,31,1,11,41,21,'S21','Synthetic student','UNCHECKED','缺课','matched',?)""", (stamp,))
        conn.commit()
        student = {"role": "student", "id": 21, "name": "Synthetic student"}
        teacher = {"role": "teacher", "id": 1, "name": "Synthetic teacher"}
        before = dashboard.dashboard(conn, 11, student)
        self.assertEqual("ABSENT", before["sessions"][0]["status"])
        fact = service.current_fact(conn, "api:51", student)
        self.assertEqual(before["sessions"][0]["source_fingerprint"], fact["source_fingerprint"])
        with patch.object(workflow, "_notify", return_value=1):
            request = workflow.create_request(conn, student, request_type=appeal.KEY, subject_id="api:51",
                reason="Synthetic real-source review", payload={"source_fingerprint": fact["source_fingerprint"]})
            conn.commit()
            detail = workflow.get_request(conn, teacher, request["id"])["detail"]["attendance"]
            workflow.decide_request(conn, teacher, request["id"], decision="approve", note="Synthetic confirmation",
                decision_payload={"status": "PRESENT", "source_fingerprint": detail["source_fingerprint"]})
            conn.commit()
        personal = dashboard.dashboard(conn, 11, student)
        classroom = dashboard.dashboard(conn, 11, teacher)
        self.assertEqual("PRESENT", personal["sessions"][0]["status"])
        self.assertEqual(100.0, personal["summary"]["attendance_rate"])
        self.assertEqual(personal["summary"]["attendance_rate"], classroom["summary"]["attendance_rate"])
        self.assertEqual("UNCHECKED", conn.execute("SELECT status FROM smart_classroom_checkin_students WHERE id=51").fetchone()[0])
        conn.execute("UPDATE smart_classroom_checkin_sessions SET synced_at='2026-11-10T12:00:00' WHERE id=31")
        conn.execute("UPDATE smart_classroom_checkin_students SET synced_at='2026-11-10T12:00:00' WHERE id=51")
        conn.commit()
        refreshed = dashboard.dashboard(conn, 11, student)
        self.assertEqual(fact["source_fingerprint"], refreshed["sessions"][0]["source_fingerprint"])
        self.assertEqual("PRESENT", refreshed["sessions"][0]["status"])
        self.assertFalse(refreshed["sessions"][0]["correction"]["stale"])


if __name__ == "__main__":
    unittest.main()
