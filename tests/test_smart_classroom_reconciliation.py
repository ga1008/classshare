"""Teacher HTTP action, synthetic school, durable local register and actual outbox worker."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_calendar_sync_service as calendar
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from classroom_app.services import smart_classroom_reconciliation_service as reconcile
from tests.test_smart_classroom_workflow_router import WorkflowRouterFixture


class ReconciliationTests(WorkflowRouterFixture):
    def setUp(self):
        super().setUp()
        for module in (calendar, reconcile):
            self.enterContext(patch.object(module, "get_db_connection", self.database))
        self.times = {}
        self.record["updateTime"] = "2026-11-09 13:00:00"
        base_read = self.read_slot

        async def read(*args):
            return {**await base_read(*args), "student_times": dict(self.times)}
        mini.read_slot.side_effect = read

    def post(self, key="first-key", ids=None, **kwargs):
        return self.client.post(self.base+"/calendar-reconcile", headers=self.headers,
                                json={"idempotency_key": key, "student_ids": ids, **kwargs})

    def sync(self, *args, **kwargs):
        res = self.post(*args, **kwargs)
        self.assertEqual(200, res.status_code, res.text)
        return res.json()

    def test_school_only_creates_published_register_once_and_can_recover_after_disconnect(self):
        original_access, self.access = self.access, None
        self.assertFalse(self.client.get(self.base+"/workflow").json()["can_reconcile"])
        self.access = original_access
        self.assertTrue(self.client.get(self.base+"/workflow").json()["can_reconcile"])
        self.conn.execute("INSERT INTO students(id,name,student_id_number,class_id,enrollment_status) VALUES(3,'Three','S3',1,'active')")
        self.conn.commit()
        # The verified school roster remains authoritative, with one local-only student.
        self.client.post(self.base+"/match", headers=self.headers, json={})
        self.assertEqual("imported", self.sync(ids=[2])["state"])
        self.assertEqual([("S1","PENDING"), ("S2","PRESENT"), ("S3","PENDING")], [tuple(r) for r in self.conn.execute(
            "SELECT student_number,status FROM login_attendance_records ORDER BY student_id")])
        self.assertEqual(("published",1), tuple(self.conn.execute("SELECT state,is_current FROM login_attendance_rounds").fetchone()))
        replay = self.sync(ids=[2])
        self.assertEqual("imported", replay["state"])
        self.assertTrue(replay["has_receipt"])
        self.assertFalse(replay["receipt"])
        self.assertEqual(409, self.post(ids=[1]).status_code)
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM login_attendance_rounds").fetchone()[0])
        self.assertEqual(replay, self.client.get(self.base+"/calendar-sync/first-key").json())
        full = self.sync("full-class-key")
        self.assertEqual(("partial", {"imported":1,"confirmed":1,"not_enrolled":1}), (full["state"],full["counts"]))
        self.assertEqual("One", next(r["name"] for r in full["rows"] if r["student_id"]==1))
        mini.change_student.assert_not_awaited()

    def test_mixed_direction_uses_each_persons_clock_and_preserves_decision_replays(self):
        self.seed_round()
        self.conn.execute("UPDATE login_attendance_records SET decided_at_ms=1800000000000")
        self.conn.commit()
        self.times = {"S1": 1790000000000, "S2": 1810000000000}
        result = self.sync()
        self.assertEqual(("queued", True, 1), (result["state"], result["receipt"], result["imported_count"]))
        self.assertEqual("PRESENT", self.conn.execute("SELECT status FROM login_attendance_records WHERE student_id=2").fetchone()[0])
        self.assertEqual("queued", self.sync()["state"])
        self.assertEqual("confirmed", asyncio.run(auto.process_auto_sync(7, 31))["state"])
        self.assertEqual({"S1":"CHECKED","S2":"CHECKED"}, self.remote)
        self.assertEqual("confirmed", self.sync()["state"])
        self.assertEqual(1, mini.change_student.await_count)

    def test_tied_or_missing_times_do_not_downgrade_either_side(self):
        self.seed_round()
        self.record.pop("updateTime")
        self.record.pop("createTime")
        self.times = {"S1":2}
        result = self.sync()
        self.assertEqual("partial", result["state"])
        self.assertEqual({"conflict":2}, result["counts"])
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM smart_attendance_workflow_requests").fetchone()[0])
        mini.change_student.assert_not_awaited()

    def test_newer_school_edit_after_queue_is_not_overwritten(self):
        self.seed_round()
        self.times = {"S1":1}
        self.assertEqual("queued", self.sync(ids=[1])["state"])
        self.times["S1"] = 3
        self.assertEqual("conflict", asyncio.run(auto.process_auto_sync(7, 31))["state"])
        mini.change_student.assert_not_awaited()
        result = self.sync("updated-clock", [1])
        self.assertEqual("imported", result["state"])
        self.assertEqual("ABSENT",self.conn.execute("SELECT status FROM login_attendance_records WHERE student_id=1").fetchone()[0])

    def test_teacher_can_correct_an_older_official_special_status(self):
        self.seed_round()
        self.remote["S1"] = "PRACTICE"
        self.times["S1"] = 1
        mini.change_student.side_effect = lambda _client, _rid, number, state: self.remote.update({number:state})
        self.assertEqual("queued", self.sync(ids=[1])["state"])
        self.assertEqual("confirmed", asyncio.run(auto.process_auto_sync(7,31))["state"])
        receipt = self.client.get(self.base+"/calendar-sync/first-key").json()
        self.assertEqual(("confirmed","CHECKED"), (receipt["rows"][0]["state"], receipt["rows"][0]["remote_status"]))

    def test_confirmed_push_records_agreement_before_another_students_round_clock_change(self):
        self.seed_round()
        self.conn.execute("UPDATE login_attendance_records SET decided_at_ms=1800000000000 WHERE student_id=1")
        self.conn.commit()
        self.assertEqual("queued", self.sync(ids=[1])["state"])
        self.assertEqual("confirmed", asyncio.run(auto.process_auto_sync(7,31))["state"])
        first = self.conn.execute("SELECT revision,manual_confirmed,decided_at_ms,school_decision_json "
                                  "FROM login_attendance_records WHERE student_id=1").fetchone()
        self.assertEqual((1,1,1800000000000), tuple(first)[:3])
        self.assertEqual("CHECKED", json.loads(first["school_decision_json"])["status"])
        untouched = self.conn.execute("SELECT school_decision_json FROM login_attendance_records "
                                      "WHERE student_id=2").fetchone()[0]
        self.assertFalse(untouched)
        self.conn.execute("UPDATE login_attendance_records SET status='ABSENT',revision=revision+1,"
                          "decided_at_ms=1801000000000 WHERE student_id=1")
        self.conn.commit()
        self.remote["S2"] = "SICK_LEAVE"
        self.record["updateTime"] = "2035-01-01 08:00:00"
        mini.change_student.side_effect = lambda _client, _rid, number, state: self.remote.update({number:state})
        self.assertEqual("queued", self.sync("later-correction", [1])["state"])
        self.assertEqual("confirmed", asyncio.run(auto.process_auto_sync(7,31))["state"])
        self.assertEqual({"S1":"UNCHECKED","S2":"SICK_LEAVE"}, self.remote)

    def test_unknown_write_recovery_stays_read_only_on_repeat(self):
        self.seed_round()
        self.times["S1"] = 1
        self.assertEqual("queued", self.sync(ids=[1])["state"])
        mini.change_student.side_effect = TimeoutError("synthetic disconnect")
        self.assertEqual("outcome_unknown", asyncio.run(auto.process_auto_sync(7,31))["state"])
        self.assertEqual("outcome_unknown", self.sync(ids=[1])["state"])
        self.remote["S1"] = "CHECKED"
        replay = self.client.post(self.base+"/calendar-sync/first-key/readback", headers=self.headers).json()
        self.assertEqual("confirmed", replay["state"])
        self.assertEqual(1, mini.change_student.await_count)

    def test_empty_then_local_only_obtains_fresh_location_once_for_the_relevant_class(self):
        school, self.record = self.record, None
        self.assertEqual("empty", self.sync("empty-key")["state"])
        self.seed_round()
        self.assertEqual("empty", self.sync("empty-key")["state"])
        subset = self.sync("subset-key", [1])
        self.assertTrue(subset["requires_full_roster"])
        self.assertEqual("school_missing", subset["state"])
        self.assertEqual("location_required", self.sync()["state"])
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM smart_attendance_workflow_requests").fetchone()[0])
        def start(*args):
            self.record = {**school, "id":"created", "isStop":False}
            self.remote = {"S1":"UNCHECKED","S2":"UNCHECKED"}
            return self.record
        history = AsyncMock(side_effect=lambda *a: [self.record.copy()] if self.record else [])
        self.enterContext(patch.object(mini, "read_history", history))
        starts = self.enterContext(patch.object(mini, "start_checkin", AsyncMock(side_effect=start)))
        self.enterContext(patch.object(mini, "stop_checkin", AsyncMock(side_effect=lambda *a:self.record.update(isStop=True))))
        loc = {"latitude":1.25, "longitude":2.8, "captured_at_ms":int(time.time()*1000)}
        self.assertEqual("queued", self.sync(location=loc)["state"])
        self.assertEqual("confirmed",asyncio.run(auto.process_auto_sync(7, 31))["state"])
        self.assertEqual("confirmed",self.sync()["state"])
        self.assertEqual(1,starts.await_count)
        self.assertEqual({"S1":"CHECKED","S2":"UNCHECKED"}, self.remote)

    def test_create_excludes_implicit_school_absence_for_unconfirmed_student(self):
        self.record=None
        self.seed_round()
        self.conn.execute("UPDATE login_attendance_records SET status='PENDING',manual_confirmed=0 WHERE student_id=2")
        self.conn.commit()
        self.assertEqual("partial",self.sync()["state"])
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM smart_attendance_auto_sync").fetchone()[0])

    def test_backfill_http_to_new_school_record_and_verified_readback(self):
        from classroom_app.routers import login_attendance as routes
        from classroom_app.db.schema_behavior_inbox import ensure_behavior_inbox_schema
        from classroom_app.db.schema_academic_schedule_predictions import ensure_academic_schedule_prediction_schema
        from classroom_app.db.schema_attendance_reports import ensure_attendance_report_schema
        from classroom_app.services import login_attendance_service as local, approval_workflow_schema
        from classroom_app.services.login_attendance_access_service import record_access
        ensure_behavior_inbox_schema(self.conn, engine="sqlite")
        ensure_academic_schedule_prediction_schema(self.conn)
        ensure_attendance_report_schema(self.conn, engine="sqlite")
        self.enterContext(patch.object(approval_workflow_schema, "_SCHEMA_READY", False))
        approval_workflow_schema.ensure_approval_workflow_schema(self.conn)
        self.client.app.include_router(routes.router)
        self.enterContext(patch.object(routes, "get_db_connection", self.database))
        self.enterContext(patch.object(local, "now_ms", return_value=local.rules.to_ms("2026-11-09T12:30:00")))
        school, self.record = self.record, None
        record_access(self.conn, student_id=1, source="login", ip_address="192.0.2.7",
                      observed_at_ms=local.rules.to_ms("2026-11-09T08:31:00"))
        self.conn.commit()
        path = "/api/classrooms/10/login-attendance/calendar/sessions/21/backfill"
        preview = self.client.get(path)
        self.assertEqual(200, preview.status_code, preview.text)
        body = {"idempotency_key":"backfill-integration","sync_school":True,"revision":preview.json()["revision"],
                "overrides":[{"student_id":2,"status":"SICK_LEAVE"}],"reason":"Verified synthetic leave"}
        res = self.client.post(path, headers=self.headers,json=body)
        self.assertEqual(200,res.status_code,res.text)
        result = res.json()
        self.assertEqual((1,1,"location_required"),(result["automatic"],result["manual"],result["school"]["state"]))
        def start(*args):
            self.record = {**school,"id":"created","isStop":False}
            self.remote = {"S1":"UNCHECKED","S2":"UNCHECKED"}
            return self.record
        self.enterContext(patch.object(mini,"read_history",AsyncMock(side_effect=lambda *a:[self.record.copy()] if self.record else [])))
        starts = self.enterContext(patch.object(mini,"start_checkin",AsyncMock(side_effect=start)))
        self.enterContext(patch.object(mini,"stop_checkin",AsyncMock(side_effect=lambda *a:self.record.update(isStop=True))))
        self.assertEqual("queued", self.sync(result["school_key"],location={"latitude":1.25,"longitude":2.8,
                                                      "captured_at_ms":int(time.time()*1000)})["state"])
        self.assertEqual("confirmed",asyncio.run(auto.process_auto_sync(7,result["round_id"]))["state"])
        self.assertEqual("confirmed", self.client.post(path,headers=self.headers,json=body).json()["school"]["state"])
        self.assertEqual(1, starts.await_count)
        self.assertEqual({"S1":"CHECKED","S2":"SICK_LEAVE"},self.remote)

    def test_full_creation_counts_local_only_student_as_not_enrolled(self):
        school, self.record = self.record, None
        self.conn.execute("INSERT INTO students(id,name,student_id_number,class_id,enrollment_status) VALUES(3,'Three','S3',1,'active')")
        self.seed_round()
        self.conn.execute("INSERT INTO login_attendance_records(round_id,student_id,student_number,name,status,"
                          "manual_confirmed,decided_at_ms,updated_at_ms) VALUES(31,3,'S3','Three','PRESENT',1,2,2)")
        self.conn.commit()
        def start(*args):
            self.record = {**school, "id":"created", "isStop":False}
            self.remote = {"S1":"UNCHECKED", "S2":"UNCHECKED"}
            return self.record
        self.enterContext(patch.object(mini, "read_history", AsyncMock(side_effect=lambda *a:[self.record.copy()] if self.record else [])))
        self.enterContext(patch.object(mini, "start_checkin", AsyncMock(side_effect=start)))
        self.enterContext(patch.object(mini, "stop_checkin", AsyncMock(side_effect=lambda *a:self.record.update(isStop=True))))
        loc = {"latitude":1.25, "longitude":2.8, "captured_at_ms":int(time.time()*1000)}
        self.assertEqual("queued", self.sync(location=loc)["state"])
        asyncio.run(auto.process_auto_sync(7,31))
        result = self.client.get(self.base+"/calendar-sync/first-key").json()
        self.assertEqual(("partial", {"confirmed":2, "not_enrolled":1}), (result["state"],result["counts"]))

    def test_active_school_record_imports_checked_never_provisional_absence(self):
        self.record["isStop"] = False
        result = self.sync()
        self.assertEqual("partial", result["state"])
        local = dict(self.conn.execute("SELECT student_number,status FROM login_attendance_records"))
        self.assertEqual({"S1":"PENDING","S2":"PRESENT"}, local)
        mini.change_student.assert_not_awaited()
