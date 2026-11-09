"""Semester attendance against real route and database projections on synthetic SQLite."""
from contextlib import closing
import json
import unittest
from unittest.mock import patch

from classroom_app.db.postgres_indexes import _build_in_memory_sqlite_schema
from classroom_app.db.schema_attendance_reports import ensure_attendance_report_schema
from classroom_app.services import attendance_dashboard_service as dashboard, login_attendance_service as local
from classroom_app.services import smart_attendance_appeal_service as appeals
from classroom_app.services.attendance_fact_service import load_confirmed_attendance_facts
import test_login_attendance_router as support


class AttendanceDashboardTests(unittest.TestCase):
    database = support.AttendanceRouterTests.database
    create = support.AttendanceRouterTests.create

    def setUp(self):
        support.AttendanceRouterTests.setUp(self)
        ensure_attendance_report_schema(self.conn, engine="sqlite")
        with closing(_build_in_memory_sqlite_schema()) as schema:
            for name in ("smart_classroom_checkin_sessions", "smart_classroom_checkin_students"):
                self.conn.execute(schema.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0])
        self.enterContext(patch.object(appeals, "now_ms", side_effect=local.now_ms))
        self.conn.commit()
        self.path = self.base + "/dashboard"

    def smart(self, session_id=21, students=((1, "CHECKED"),), *,
              checkin="2026-11-08T08:30:00", ended="2026-11-08T08:30:30", synced="2026-11-08T08:30:50"):
        checkin_id = self.conn.execute("""INSERT INTO smart_classroom_checkin_sessions
            (teacher_id,class_offering_id,session_id,platform_code,remote_checkin_id,checkin_time,stop_time,total_count,match_status,synced_at)
            VALUES(7,10,?,'gxufl_smart_classroom',?,?,?,?,'matched',?)""",
            (session_id, f"synthetic-{session_id}-{checkin}", checkin, ended, len(students), synced)).lastrowid
        self.conn.executemany("""INSERT INTO smart_classroom_checkin_students
            (checkin_session_id,teacher_id,class_offering_id,session_id,student_id,student_number,student_name,status,status_label,local_match_status,synced_at)
            VALUES(?,7,10,?,?,?,?,?,?,'matched',?)""",
            [(checkin_id, session_id, sid, f"S{sid}", f"Synthetic student {sid}", status, status, synced) for sid, status in students])
        self.conn.commit()
        return checkin_id

    def confirmed_pdf(self, students):
        binding = self.conn.execute("""INSERT INTO smart_attendance_source_bindings
            (owner_teacher_id,school_code,platform_code,external_account_key,remote_schedule_id,academic_year,academic_term,created_at,updated_at)
            VALUES(7,'gxufl','gxufl_smart_classroom','synthetic-account','synthetic-schedule','2026-2027',1,'2026-11-08','2026-11-08')""").lastrowid
        self.conn.execute("INSERT INTO smart_attendance_source_offerings(binding_id,class_offering_id) VALUES(?,10)", (binding,))
        report = self.conn.execute("INSERT INTO attendance_reports(binding_id,created_at,updated_at) VALUES(?,'2026-11-08','2026-11-08')", (binding,)).lastrowid
        version = self.conn.execute("""INSERT INTO attendance_report_versions(report_id,version_no,request_key,created_by,created_at,source_state)
            VALUES(?,1,'synthetic-pdf-version',7,'2026-11-08','cached')""", (report,)).lastrowid
        run = self.conn.execute("""INSERT INTO attendance_parse_runs
            (source_version_id,run_no,request_key,mapped_offering_id,binding_link_revision,state)
            VALUES(?,1,'synthetic-pdf-run',10,1,'confirmed')""", (version,)).lastrowid
        self.conn.execute("UPDATE attendance_reports SET confirmed_parse_run_id=? WHERE id=?", (run, report))
        lesson = self.conn.execute("""INSERT INTO attendance_report_sessions
            (parse_run_id,column_index,source_datetime,local_session_id,remote_checkin_id,mapping_state)
            VALUES(?,1,'2026-11-08T08:30:00',21,'synthetic-pdf-event','matched')""", (run,)).lastrowid
        for sid, status in students:
            student = self.conn.execute("""INSERT INTO attendance_report_students
                (parse_run_id,row_index,student_number,source_name,local_student_id,identity_state)
                VALUES(?,?,?,?,?,'matched')""", (run, sid, f"S{sid}", f"PDF student {sid}", sid)).lastrowid
            self.conn.execute("""INSERT INTO attendance_report_cells
                (parse_run_id,student_row_id,session_column_id,normalized_status,quality_state) VALUES(?,?,?,?,'verified')""",
                (run, student, lesson, status))
        self.conn.commit()

    def get(self, path=None):
        response = self.client.get(path or self.path)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        return response.json()

    def test_student_visibility_ignores_void_history_and_hides_unstarted_lessons(self):
        self.assertEqual({"visible": False}, self.get())
        self.assertEqual([], self.get(self.path + "/visibility")["session_ids"])
        round_row = self.create()
        self.assertEqual([21], self.get(self.path + "/visibility")["local_session_ids"])
        self.assertEqual("PENDING", self.get()["sessions"][0]["status"])
        local.void_round(self.conn, round_row["id"], self.user, {"revision": round_row["revision"], "reason": "Synthetic void"})
        self.conn.commit()
        self.assertEqual({"visible": False}, self.get())
        self.assertIsNone(self.get(self.base + "/sessions/21/me")["round"])
        self.assertEqual("void", self.get(self.base + "/history")["items"][0]["state"])

    def test_effective_source_precedence_pending_and_exempt_never_double_count(self):
        round_row = self.create()
        self.smart(students=((1, "UNCHECKED"),), checkin="2026-11-08T08:29:00")
        self.smart(students=((1, "CHECKED"), (2, "UNCHECKED"), (3, "UNCHECKED")))
        self.confirmed_pdf(((2, "CHECKED"), (3, "CHECKED")))
        record = next(row for row in round_row["records"] if row["student_id"] == 2)
        local.change_records(self.conn, round_row["id"], self.user, {"reason": "Verified sick leave", "records": [
            {"student_id": 2, "revision": record["revision"], "status": "SICK_LEAVE"}]})
        self.conn.commit()
        personal = self.get()
        self.assertNotIn("students", personal)
        self.assertEqual([("smart_synced", "PRESENT", "running")], [
            (row["source"], row["status"], row["local_state"]) for row in personal["sessions"]])
        self.assertEqual([], self.get(self.path + "/visibility")["local_session_ids"])
        self.identity = self.user
        teacher = self.get()
        self.assertEqual((5, 3, 2, 1), tuple(teacher["summary"][key] for key in ("expected", "confirmed", "pending", "exempt")))
        self.assertEqual(0, teacher["summary"]["counts"]["ABSENT"])
        self.assertEqual("mixed", teacher["sessions"][0]["source"])
        self.assertEqual(2, teacher["sessions"][0]["conflict_count"])
        self.assertEqual("not_started", teacher["sessions"][1]["state"])
        self.assertEqual("SICK_LEAVE", self.get(self.path + "/students/2")["sessions"][0]["status"])
        self.assertEqual("confirmed_pdf", self.get(self.path + "/students/3")["sessions"][0]["source"])
        grades = load_confirmed_attendance_facts(self.conn, class_offering_id=10, teacher_id=7, require_feature=False)
        self.assertTrue(grades["available"])
        self.assertEqual([100.0, 100.0], [item["summary"]["attendance_rate"] for item in grades["students"]])

    def test_remote_unfinished_absence_requires_a_new_sync_after_end(self):
        checkin = self.smart(students=((1, "UNCHECKED"),), ended="2026-11-08T10:00:00")
        initial = self.get()["sessions"][0]
        self.assertEqual(("running", "PENDING", False), (initial["state"], initial["status"], initial["can_appeal"]))
        self.mock_now.return_value = local.rules.to_ms("2026-11-08T10:05:00")
        self.assertEqual("PENDING", self.get()["sessions"][0]["status"])
        for table, id_field in (("smart_classroom_checkin_sessions", "id"), ("smart_classroom_checkin_students", "checkin_session_id")):
            self.conn.execute(f"UPDATE {table} SET synced_at='2026-11-08T10:01:00' WHERE {id_field}=?", (checkin,))
        self.conn.commit()
        finished = self.get()["sessions"][0]
        self.assertEqual(("synced", "ABSENT", True), (finished["state"], finished["status"], finished["can_appeal"]))
        fact = dashboard.load_student_session_fact(self.conn, 10, 21, self.identity, student_id=1)
        self.assertEqual(finished["source_fingerprint"], fact["source_fingerprint"])
        self.assertNotIn("remote_checkin_id", fact)

    def test_unrecognized_synced_status_is_visible_pending_and_can_be_reviewed(self):
        self.smart(students=((1, "SCHOOL_SPECIAL_STATUS"),))
        self.assertTrue(self.get(self.path + "/visibility")["visible"])
        result = self.get()
        self.assertEqual("PENDING", result["sessions"][0]["status"])
        self.assertTrue(result["sessions"][0]["can_appeal"])
        self.assertIsNone(result["summary"]["attendance_rate"])
        self.assertEqual(0, result["summary"]["counts"]["ABSENT"])

    def test_lesson_filter_and_appeals_share_the_same_authorized_facts(self):
        round_row = self.create()
        record = next(row for row in round_row["records"] if row["student_id"] == 1)
        self.smart(session_id=22, ended="2026-11-08T10:30:00", synced="2026-11-08T10:31:00")
        self.mock_now.return_value = local.rules.to_ms("2026-11-08T12:00:00")
        self.conn.execute("UPDATE login_attendance_rounds SET state='published' WHERE id=?", (round_row["id"],))
        self.conn.execute("""INSERT INTO approval_requests(request_type,applicant_role,applicant_user_pk,subject_id,class_offering_id)
            VALUES('login_attendance_appeal','student',1,?,10)""", (str(record["id"]),))
        self.conn.commit()
        self.identity = self.user
        self.assertEqual(1, self.get()["summary"]["pending_appeals"])
        lesson = self.get(self.path + "?session_id=22")
        self.assertEqual([22], [row["session_id"] for row in lesson["sessions"]])
        self.assertEqual(0, lesson["summary"]["pending_appeals"])
        self.assertEqual((1, 1, 1), tuple(lesson["summary"][key] for key in ("expected", "confirmed", "session_count")))
        self.assertEqual(404, self.client.get(self.path + "?session_id=123").status_code)
        self.identity = {"role": "teacher", "id": 8}
        self.assertEqual(403, self.client.get(self.path).status_code)
        self.identity = {"role": "student", "id": 9}
        self.assertEqual(403, self.client.get(self.path + "/visibility").status_code)

    def test_approved_school_correction_survives_automatic_local_presence(self):
        self.smart()
        imported = dashboard.load_student_session_fact(self.conn, 10, 21, self.identity)
        round_row = self.create()
        self.conn.execute("UPDATE login_attendance_rounds SET state='published' WHERE id=?", (round_row["id"],))
        self.conn.execute("UPDATE login_attendance_records SET status='PRESENT' WHERE round_id=? AND student_id=1", (round_row["id"],))
        self.conn.execute("""INSERT INTO approval_requests(request_type,status,subject_type,subject_id,applicant_role,
            applicant_user_pk,class_offering_id,payload_json,decision_payload_json,decided_at)
            VALUES('smart_attendance_appeal','approved','smart_attendance_source',?,'student',1,10,?,?,'2026-11-08T08:31:00')""",
            (imported["source_key"], json.dumps({"class_offering_id": 10, "session_id": 21, "student_id": 1}),
             json.dumps({"source_fingerprint": imported["source_fingerprint"], "status": "ABSENT"})))
        self.conn.commit()
        self.assertEqual("ABSENT", self.get()["sessions"][0]["status"])
        self.assertEqual(imported["source_key"], dashboard.load_student_session_fact(self.conn, 10, 21, self.identity)["source_key"])
        self.conn.execute("UPDATE smart_classroom_checkin_students SET status='SICK_LEAVE' WHERE student_id=1")
        self.conn.commit()
        self.assertEqual("PENDING", self.get()["sessions"][0]["status"])
        self.conn.execute("UPDATE login_attendance_records SET manual_confirmed=1 WHERE round_id=? AND student_id=1", (round_row["id"],))
        self.conn.commit()
        self.assertEqual("PRESENT", self.get()["sessions"][0]["status"])


if __name__ == "__main__":
    unittest.main()
