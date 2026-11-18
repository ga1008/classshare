"""Local round, original views and trusted decision clocks for school reconciliation."""
from unittest import TestCase

from classroom_app.services import login_attendance_service as local
from classroom_app.services import login_attendance_school_register_service as register
import test_login_attendance_calendar as calendar


class SchoolRegisterTests(TestCase):
    database = calendar.TeacherCalendarTests.database
    smart = calendar.TeacherCalendarTests.smart
    create = calendar.TeacherCalendarTests.create

    def setUp(self):
        calendar.TeacherCalendarTests.setUp(self)
        self.checkin = "synthetic-21-2026-11-08T08:30:00"

    def snapshot(self, statuses, *, stopped=True, stamp="2026-11-08T12:00:00"):
        return {"record":{"id":self.checkin, "isStop":stopped, "updateTime":stamp},
                "students":{f"S{i}": value for i, value in statuses}}

    def reconcile(self, snapshot, ids=None):
        result = register.reconcile_snapshot(self.conn, self.user, 10, 21, snapshot, ids)
        self.conn.commit()
        return result

    def test_fills_real_register_skipping_missing_and_exempt_and_keeps_school_states(self):
        school = ((1,"UNCHECKED"),(2,"LATE_OR_EARLY"),(3,"STUDY_TOUR"),(6,"UNCHECKED"))
        self.smart(students=school)
        snapshot = self.snapshot(school)
        result = self.reconcile(snapshot)
        rid = result["round_id"]
        self.assertTrue(result["created"])
        self.assertIn(1, result["imported_ids"])
        self.assertNotIn(6, result["imported_ids"])
        view = local.round_view(self.conn, rid, self.user)
        rows = {r["student_id"]:r for r in view["records"]}
        self.assertEqual(("published","school_register"), (view["state"],view["context"]["time_source"]))
        self.assertEqual(("ABSENT",False,"UNCHECKED"), (rows[1]["status"],rows[1]["manual_confirmed"],rows[1]["school_status"]))
        self.assertEqual(("LATE_OR_EARLY","迟到或早退"), (rows[2]["status"],rows[2]["status_label"]))
        self.assertEqual(("STUDY_TOUR","研学"), (rows[3]["status"],rows[3]["status_label"]))
        self.assertEqual("PENDING", rows[4]["status"])
        self.assertEqual(1, view["summary"]["counts"]["STUDY_TOUR"])
        own = local.student_view(self.conn, 10, 21, {"role":"student","id":1})
        self.assertEqual(("ABSENT","已同步智慧课堂点名"), (own["status"],own["message"]))
        local.evaluate_round(self.conn, rid)
        self.assertEqual([],self.reconcile(snapshot)["imported_ids"])
        self.assertEqual(view["revision"], local.load_round(self.conn,rid)["revision"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM login_attendance_rounds").fetchone()[0])
        self.assertEqual(len(result["imported_ids"]), self.conn.execute(
            "SELECT COUNT(*) FROM login_attendance_audit WHERE action='school_decision_imported'").fetchone()[0])

    def test_local_correction_after_read_survives_unrelated_round_update_then_new_individual_edit_wins(self):
        school = ((1,"UNCHECKED"),(2,"CHECKED"))
        self.smart(students=school)
        snapshot = self.snapshot(school)
        rid = self.reconcile(snapshot)["round_id"]
        rows = {r["student_id"]:r for r in local._records(self.conn, rid)}
        local.change_records(self.conn,rid,self.user,{"reason":"教师确认到场","records":[
            {"student_id":1,"status":"PRESENT","revision":rows[1]["revision"]}]},include_round=False)
        snapshot["record"]["updateTime"] = "2026-11-09T14:00:00"
        self.assertEqual([], self.reconcile(snapshot)["imported_ids"])
        ctx = local.load_attendance_sync_context(self.conn,self.user,rid)
        student = next(r for r in ctx["students"] if r["student_id"]==1)
        self.assertEqual(("PRESENT",None), (student["status"],student["school_status"]))
        self.assertEqual("local_newer", register.decision_direction(student,ctx,snapshot))
        snapshot["student_times"] = {"S1": local.rules.to_ms("2026-11-09T13:45:00")}
        snapshot["students"]["S1"] = "SICK_LEAVE"
        self.assertEqual([1], self.reconcile(snapshot)["imported_ids"])
        record = local.student_view(self.conn,10,21,{"role":"student","id":1})
        self.assertEqual(("SICK_LEAVE","病假"), (record["status"],record["status_label"]))

    def test_running_school_never_makes_an_absence_and_subset_cannot_change_other_people(self):
        school = ((1,"UNCHECKED"),(2,"CHECKED"))
        self.smart(students=school)
        snapshot = self.snapshot(school,stopped=False)
        self.assertIsNone(self.reconcile(snapshot,[999])["round_id"])
        result=self.reconcile(snapshot,[1])
        self.assertTrue(result["created"])
        self.assertEqual([],result["imported_ids"])
        self.assertEqual([2],self.reconcile(snapshot,[2])["imported_ids"])
        snapshot["record"]["isStop"]=True
        self.assertEqual([1],self.reconcile(snapshot,[1])["imported_ids"])

    def test_equal_or_unknown_edit_times_keep_an_existing_difference(self):
        school = ((1,"UNCHECKED"),)
        self.smart(students=school)
        rid = self.reconcile(self.snapshot(school))["round_id"]
        competing=self.snapshot(((1,"CHECKED"),))
        self.assertEqual([],self.reconcile(competing)["imported_ids"])
        competing["record"].pop("updateTime")
        self.assertEqual([],self.reconcile(competing)["imported_ids"])
        self.assertEqual("ABSENT",local._records(self.conn,rid)[0]["status"])

    def test_school_creation_is_a_lower_bound_on_the_decision_not_a_last_modified_timestamp(self):
        snapshot = self.snapshot(((1,"CHECKED"),), stamp=None)
        snapshot["record"]["createTime"] = "2026-11-09T09:30:00"
        row = {"student_number":"S1","manual_confirmed":True,"decided_at_ms":local.rules.to_ms("2026-11-09T09:00:00")}
        self.assertEqual("school_newer",register.decision_direction(row,{},snapshot))
        row["decided_at_ms"] = local.rules.to_ms("2026-11-09T10:00:00")
        self.assertEqual("unknown",register.decision_direction(row,{},snapshot))

    def test_final_readback_only_observes_agreement_without_creating_or_changing_a_register(self):
        school = ((1,"CHECKED"),(2,"UNCHECKED"))
        self.smart(students=school)
        snapshot = self.snapshot(school)
        observe = lambda: register.reconcile_snapshot(self.conn,self.user,10,21,snapshot,observe_only=True)
        self.assertIsNone(observe()["round_id"])
        self.assertFalse(self.conn.execute("SELECT 1 FROM login_attendance_rounds").fetchone())
        rid = self.reconcile(snapshot,[1])["round_id"]
        first = local._records(self.conn,rid)[0]
        local.change_records(self.conn,rid,self.user,{"reason":"教师确认缺勤","records":[
            {"student_id":1,"status":"ABSENT","revision":first["revision"]}]},include_round=False)
        snapshot["students"]["S1"]="UNCHECKED"
        self.assertEqual([],observe()["imported_ids"])
        rows={r["student_id"]:r for r in local.load_attendance_sync_context(self.conn,self.user,rid)["students"]}
        self.assertTrue(rows[1]["manual_confirmed"])
        self.assertEqual("UNCHECKED",rows[1]["school_baseline"]["status"])
        self.assertEqual(("PENDING",{}),(rows[2]["status"],rows[2]["school_baseline"]))
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM login_attendance_audit WHERE action='school_decision_imported'").fetchone()[0])
