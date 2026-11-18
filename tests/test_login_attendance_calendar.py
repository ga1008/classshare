"""Exercise the teacher calendar through its HTTP routes and isolated SQLite ledger."""
from unittest import TestCase
from unittest.mock import patch
from datetime import date, timedelta

from classroom_app.services import login_attendance_service as attendance
from classroom_app.services.smart_classroom_checkin_sync_service import _upsert_checkin_students
import test_login_attendance_router as fixture
import test_attendance_dashboard as statistics_fixture


class TeacherCalendarTests(TestCase):
    database = fixture.AttendanceRouterTests.database
    create = fixture.AttendanceRouterTests.create
    smart = statistics_fixture.AttendanceDashboardTests.smart
    confirmed_pdf = statistics_fixture.AttendanceDashboardTests.confirmed_pdf

    def setUp(self):
        statistics_fixture.AttendanceDashboardTests.setUp(self)
        self.conn.executescript("""
            ALTER TABLE class_offerings ADD COLUMN semester_id INTEGER DEFAULT 2;
            ALTER TABLE class_offering_sessions ADD COLUMN schedule_note TEXT DEFAULT '';
            ALTER TABLE class_offering_sessions ADD COLUMN order_index INTEGER DEFAULT 1;
            CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,name TEXT,start_date TEXT,end_date TEXT);
            INSERT INTO academic_semesters VALUES(2,'2026年秋季学期','2026-09-01','2027-01-11');
            CREATE TABLE academic_semester_calendar_days(semester_id INTEGER,date TEXT,week_index INTEGER,weekday INTEGER,
                day_type TEXT,label TEXT,source TEXT,source_url TEXT,confidence REAL, metadata_json TEXT);
            CREATE TABLE academic_schedule_session_bindings(teacher_id INTEGER,semester_id INTEGER,session_id INTEGER,
                class_offering_id INTEGER,original_json TEXT,current_json TEXT);
            INSERT INTO academic_schedule_session_bindings VALUES
                (7,2,21,10,'{"date":"2026-11-06","sections":[2,3]}','{"date":"2026-11-08","sections":[2,3]}');
            INSERT INTO academic_semester_calendar_days VALUES(2,'2026-11-07',10,6,'workday','校历调课','school','',1,
                '{"makeup_for_date":"2026-11-06"}');
            INSERT INTO class_offering_sessions(id,class_offering_id,title,session_date,week_index,schedule_status) VALUES
                (23,10,'Other term','2026-07-01',0,'normal'),(24,11,'Other class','2026-11-08',10,'normal'),
                (25,10,'Future','2026-11-13',11,'normal'),(26,10,'Cancelled','2026-11-01',10,'cancelled');
            UPDATE class_offering_sessions SET schedule_note='实际调至周日' WHERE id=21;
        """)
        self.conn.commit()
        self.enterContext(patch("classroom_app.services.national_holiday_service.cached_national_lookup", return_value={}))
        self.path = self.base + "/calendar"
        self.mock_now.return_value = attendance.rules.to_ms("2026-11-09T09:00:00")

    def get(self, suffix=""):
        result = self.client.get(self.path + suffix)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual("private, no-store", result.headers["Cache-Control"])
        return result.json()

    def save(self, session_id=21, *, round_id=None, records=None, key="calendar-fixture", **kwargs):
        return self.client.patch(self.path + f"/sessions/{session_id}", headers=kwargs.get("headers", self.headers),
                                 json={"round_id": round_id, "records": records or [self.change(1, "ABSENT")],
                                       "reason": "教师核对课堂记录", "idempotency_key": key,
                                       "expected_slot_revision": kwargs.get("slot_revision")})

    @staticmethod
    def change(student_id, status, revision=0):
        return {"student_id": student_id, "status": status, "revision": revision}

    def test_semester_calendar_keeps_all_slots_holiday_and_cached_evidence_separate(self):
        self.smart(session_id=22, students=((5, "CHECKED"),))
        view = self.get()
        self.assertEqual((6, 4), (len(view["students"]), len(view["sessions"])))
        self.assertEqual(("2026-09-01", "2027-01-11"), (view["calendar"]["start"], view["calendar"]["end"]))
        days = {d["date"]: d for d in view["calendar"]["days"]}
        self.assertEqual("2026-11-06", days["2026-11-07"]["makeup_for_date"])
        same_day = [s for s in view["sessions"] if s["date"] == "2026-11-08"]
        self.assertEqual([21, 22], [s["session_id"] for s in same_day])
        self.assertEqual(["2,3", "4,5"], [s["section_text"] for s in same_day])
        self.assertEqual([False, True], [s["school_cached"] for s in same_day])
        self.assertEqual("实际调至周日", same_day[0]["schedule_note"])
        self.assertEqual("调自 2026-11-06 第2、3节", same_day[0]["schedule_status_label"])
        self.assertTrue(all(s["round_id"] is None and s["counts"]["ABSENT"] == 0 for s in same_day))
        self.assertEqual("smart_synced", same_day[1]["source"])
        self.assertEqual(1, same_day[1]["counts"]["PRESENT"])
        school_only = self.get("/sessions/22?student_id=5")["records"][0]
        self.assertEqual(("PRESENT", "PENDING", 0), (school_only["effective_status"], school_only["status"], school_only["revision"]))
        self.assertEqual([0, 0, 0, 0, 0, 0], [r["revision"] for r in self.get("/sessions/21")["records"]])
        personal = self.get("?student_id=1")
        self.assertTrue(all(s["status"] is None for s in personal["sessions"]))

    def test_manual_creation_replay_and_correction_never_manufacture_evidence(self):
        response = self.save()
        self.assertEqual(200, response.status_code, response.text)
        receipt = response.json()
        self.assertEqual(receipt, self.save().json())
        self.assertEqual(409, self.save(records=[self.change(2, "PRESENT")]).status_code)
        rid = receipt["round_id"]
        self.assertEqual("published", attendance.load_round(self.conn, rid)["state"])
        self.assertEqual({"EXEMPT": 1, "ABSENT": 1, "PENDING": 4},
                         {k: v for k, v in self.get("/sessions/21")["session"]["counts"].items() if v})
        self.assertEqual("ABSENT", next(s for s in self.get("?student_id=1")["sessions"] if s["session_id"] == 21)["status"])
        attendance.evaluate_round(self.conn, rid)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM login_attendance_evaluations WHERE round_id=?", (rid,)).fetchone())
        view = attendance.round_view(self.conn, rid, self.user)
        self.assertFalse(view["settlement_gate"]["can_start"])
        self.assertEqual("manual_register", view["settlement_gate"]["code"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM smart_attendance_auto_sync").fetchone())
        self.assertEqual(200, self.save(round_id=rid, records=[self.change(1, "LATE", 2), self.change(2, "PRESENT", 1)],
                                        key="second-operation").status_code)
        self.assertEqual(["LATE", "PRESENT"], [r["status"] for r in self.get("/sessions/21")["records"][:2]])
        self.assertEqual(3, self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='manual_decision'").fetchone()[0])

    def test_scope_future_cancellation_stale_version_and_atomic_rollback(self):
        for sid, code in ((23, 404), (24, 404), (25, 409), (26, 409)):
            self.assertEqual(code, self.save(sid).status_code)
        self.assertEqual(422, self.save(records=[self.change(9, "ABSENT")]).status_code)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM login_attendance_rounds").fetchone())
        self.assertEqual(403, self.save(headers={"Origin": "https://elsewhere.test"}).status_code)
        self.assertEqual(404, self.client.get(self.path + "?student_id=9").status_code)
        rid = self.save().json()["round_id"]
        self.assertEqual(409, self.save(round_id=rid, records=[self.change(2, "PRESENT", 1), self.change(1, "EARLY", 1)],
                                        key="stale-version-key").status_code)
        records = self.get("/sessions/21")["records"]
        self.assertEqual(["ABSENT", "PENDING"], [r["status"] for r in records[:2]])
        self.user = {"role": "teacher", "id": 8}
        self.assertEqual(403, self.client.get(self.path).status_code)
        self.assertEqual(403, self.save().status_code)

    def test_deleted_or_changed_roster_preserves_personal_historical_counts(self):
        self.save()
        self.conn.execute("UPDATE students SET enrollment_status='inactive' WHERE id=1")
        self.conn.commit()
        individual = self.get("?student_id=1")
        lessons = {s["session_id"]: s for s in individual["sessions"]}
        self.assertEqual("ABSENT", lessons[21]["status"])
        self.assertTrue(lessons[21]["can_edit"])
        self.assertFalse(lessons[22]["can_edit"])
        self.assertEqual([], self.get("/sessions/22?student_id=1")["records"])

    def test_school_and_pdf_facts_never_double_count_and_preserve_local_precedence(self):
        self.smart(students=((1, "CHECKED"), (2, "CHECKED")))
        self.confirmed_pdf(((2, "UNCHECKED"),))
        session = self.get("/sessions/21")["session"]
        self.assertEqual((1, 1), (session["counts"]["PRESENT"], session["counts"]["ABSENT"]))
        self.assertTrue(session["recorded"])
        self.assertEqual("mixed", session["source"])
        self.assertEqual(200, self.save(records=[self.change(2, "SICK_LEAVE")]).status_code)
        personal = self.get("/sessions/21?student_id=2")["records"][0]
        self.assertEqual(("local", "SICK_LEAVE"), (personal["source"], personal["effective_status"]))
        self.assertEqual("PRESENT", self.get("/sessions/21?student_id=1")["records"][0]["effective_status"])

    def test_school_only_historical_student_can_be_explicitly_added_without_copying_the_cohort(self):
        self.smart(students=((1, "CHECKED"), (2, "UNCHECKED")))
        self.conn.execute("UPDATE students SET enrollment_status='inactive' WHERE id IN (1,2)")
        self.conn.commit()
        self.assertTrue(self.get("/sessions/21?student_id=1")["records"][0]["editable"])
        self.assertEqual(200, self.save(records=[self.change(1, "LATE")]).status_code)
        second = self.get("/sessions/21?student_id=2")["records"][0]
        self.assertEqual(("PENDING", "ABSENT", False), (second["status"], second["effective_status"], second["manual_confirmed"]))

    def test_school_readback_preserves_only_proven_members_of_this_historical_lesson(self):
        checkin = self.smart(students=((1, "CHECKED"), (2, "CHECKED"), (3, "CHECKED")))
        stamp = "2026-11-09T08:00:00"
        self.conn.execute("UPDATE students SET enrollment_status='inactive' WHERE id IN (1,2,3)")
        self.conn.execute("UPDATE students SET student_id_number='changed-number' WHERE id=2")
        self.conn.execute("UPDATE smart_classroom_checkin_students SET session_id=22 WHERE student_id=3")
        self.conn.execute("UPDATE smart_classroom_checkin_sessions SET synced_at=? WHERE id=?", (stamp, checkin))
        _upsert_checkin_students(self.conn, checkin_session_id=checkin, teacher_id=7, class_offering_id=10, session_id=21,
                                 student_rows=[{"no": f"S{i}", "name": str(i), "status": "UNCHECKED"} for i in (1, 2, 3)],
                                 synced_at=stamp)
        self.conn.commit()
        self.assertEqual([1, None, None], [r[0] for r in self.conn.execute(
            "SELECT student_id FROM smart_classroom_checkin_students ORDER BY student_number")])
        self.assertEqual("ABSENT", self.get("/sessions/21?student_id=1")["records"][0]["effective_status"])

    def test_legacy_semester_without_calendar_skips_invalid_and_out_of_term_sessions(self):
        self.conn.execute("UPDATE class_offerings SET semester_id=NULL WHERE id=10")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-02-31' WHERE id=26")
        self.conn.commit()
        result = self.get()
        self.assertEqual({21, 22, 25}, {s["session_id"] for s in result["sessions"]})
        self.assertIn("课次日期", result["calendar"]["scope_note"])
        self.assertEqual(404, self.save(23).status_code)
        self.assertEqual(404, self.save(26).status_code)

    def test_batch_decision_revokes_old_scopes_but_preserves_submitted_unknown_and_other_students(self):
        rid = self.save().json()["round_id"]
        self.conn.execute("""INSERT INTO smart_attendance_auto_sync(local_round_id,owner_teacher_id,class_offering_id,session_id,
            external_account_key,credential_generation,source_json,slot_json,enabled,state,request_key,created_at,updated_at)
            VALUES(?,7,10,21,'synthetic-account','0','{}','{}',1,'pending','old-class','now','now')""", (str(rid),))
        self.conn.executemany("""INSERT INTO smart_attendance_student_corrections(id,owner_teacher_id,request_key,request_hash,
            class_offering_id,session_id,origin_scope_json,local_round_id,student_id,student_number,local_revision,target_status,
            mode,state,created_at,updated_at) VALUES(?,7,?,'hash',10,21,'{}',?,?,?,1,'PRESENT','push',?,'now','now')""",
            [(str(sid), str(sid), str(rid), sid, f"S{sid}", state)
             for sid, state in ((1,"queued"), (2,"outcome_unknown"), (3,"queued"))])
        self.conn.commit()
        response = self.save(round_id=rid, records=[self.change(1, "LATE", 2), self.change(2, "SICK_LEAVE", 1)],
                             key="two-manual-changes")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual((0, "awaiting_push"), tuple(self.conn.execute("SELECT enabled,state FROM smart_attendance_auto_sync").fetchone()))
        self.assertEqual(["superseded", "outcome_unknown", "queued"], [s[0] for s in self.conn.execute(
            "SELECT state FROM smart_attendance_student_corrections ORDER BY student_id")])

    def test_school_running_on_a_past_lesson_cannot_turn_unchecked_into_confirmed_absence(self):
        checkin = self.smart(students=((1, "UNCHECKED"),), ended=None, synced="2026-11-08T12:00:00")
        self.conn.execute("""UPDATE smart_classroom_checkin_sessions SET metadata_json='{"record":{"isStop":false}}'
                             WHERE id=?""", (checkin,))
        self.conn.commit()
        row = self.get("/sessions/21?student_id=1")["records"][0]
        self.assertEqual("PENDING", row["effective_status"])
        self.conn.execute("""UPDATE smart_classroom_checkin_sessions SET metadata_json='{"record":{"isStop":true}}'
                             WHERE id=?""", (checkin,))
        self.conn.commit()
        row = self.get("/sessions/21?student_id=1")["records"][0]
        self.assertEqual("ABSENT", row["effective_status"])

    def test_200_students_and_32_published_lessons_use_bounded_school_free_queries(self):
        self.conn.executemany("INSERT INTO students VALUES(?,?,?,1,'active')",
                              [(sid, f"Student {sid}", f"S{sid}") for sid in range(11, 205)])
        roster = attendance._roster(self.conn, 10)
        self.assertEqual(200, len(roster))
        for index in range(32):
            sid = 100 + index
            day = (date(2026, 9, 1) + timedelta(days=2 * index)).isoformat()
            self.conn.execute("""INSERT INTO class_offering_sessions(id,class_offering_id,title,session_date,week_index,
                schedule_status,order_index) VALUES(?,10,'Class',?,1,'scheduled',?)""", (sid, day, index + 10))
            rid = self.conn.execute("""INSERT INTO login_attendance_rounds(class_offering_id,session_id,teacher_id,request_key,
                state,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,next_evaluation_ms,
                policy_json,context_json) VALUES(10,?,7,?,'published',1,2,1,2,2,2,2,'{}','{}')""",
                (sid, f"lesson-{sid}")).lastrowid
            attendance.seed_records(self.conn, rid, roster, 2)
        self.conn.execute("UPDATE login_attendance_records SET status='PRESENT' WHERE expected=1")
        self.conn.commit()
        reads = []
        self.conn.set_trace_callback(lambda sql: reads.append(sql) if sql.lstrip().lower().startswith(("select", "with")) else None)
        try:
            result = self.get()
        finally:
            self.conn.set_trace_callback(None)
        self.assertLessEqual(len(reads), 20)
        published = [s for s in result["sessions"] if s["recorded"]]
        self.assertEqual(32, len(published))
        self.assertTrue(all(s["counts"]["PRESENT"] == 199 and s["counts"]["EXEMPT"] == 1 for s in published))

    def test_move_between_open_and_save_rejects_the_old_slot_and_preserves_a_completed_receipt(self):
        first = self.get("/sessions/21")["session"]["slot_revision"]
        self.conn.execute("UPDATE class_offering_sessions SET academic_section_text='4,5' WHERE id=21")
        self.conn.commit()
        self.assertEqual(409, self.save(slot_revision=first).status_code)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM login_attendance_rounds").fetchone())
        second = self.get("/sessions/21")["session"]["slot_revision"]
        self.assertNotEqual(first, second)
        saved = self.save(slot_revision=second)
        self.assertEqual(200, saved.status_code)
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-11-07' WHERE id=21")
        self.conn.commit()
        self.assertEqual(saved.json(), self.save(slot_revision=second).json())
        stale = self.save(round_id=saved.json()["round_id"], key="after-second-move", slot_revision=second,
                          records=[self.change(1, "LATE", 2)])
        self.assertEqual(409, stale.status_code)
        self.assertEqual("ABSENT", self.get("/sessions/21?student_id=1")["records"][0]["status"])
