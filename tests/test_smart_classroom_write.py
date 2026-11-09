"""Synthetic SQLite + fake remote: no real teacher, student or school request."""
import copy
from contextlib import contextmanager
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.db.schema_smart_classroom_write import ensure_smart_classroom_write_schema
from classroom_app.db.schema_smart_classroom_auto_sync import ensure_smart_classroom_auto_sync_schema
from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
from classroom_app.services import smart_classroom_write_service as service
from classroom_app.services.attendance_report_service import sign_attendance_source_option


class SmartWriteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn=sqlite3.connect(":memory:",check_same_thread=False);self.conn.row_factory=sqlite3.Row;self.conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.conn.close)
        self.conn.executescript("CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER);INSERT INTO class_offerings VALUES(11,1);CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER);INSERT INTO class_offering_sessions VALUES(21,11);")
        ensure_smart_classroom_write_schema(self.conn)
        ensure_smart_classroom_auto_sync_schema(self.conn)
        self.conn.commit()
        self.user={"role":"teacher","id":1}
        self.context={"class_offering_id":11,"session_id":21,"round_id":31,"revision":1,"state":"published",
            "year":"2026-2027","term":1,"course_code":"E020007B2","session_date":"2026-11-09","weekday":0,"section_text":"2-3",
            "students":[{"student_id":1,"student_number":"0001","status":"PRESENT","revision":1,"manual_confirmed":0},
                        {"student_id":2,"student_number":"0002","status":"ABSENT","revision":1,"manual_confirmed":1}]}
        self.access={"credential_id":7,"credential_generation":"generation-1","platform_code":"gxufl_smart_classroom","username":"synthetic"}
        self.account_key=service.source_adapter.external_account_key(self.access)
        self.source={"academic_year":"2026-2027","academic_term":1,"remote_schedule_id":"opaque-group","course_code":"E020007B2","remote_class_id":"class-A","external_account_key":self.account_key}
        self.snapshot={"record":{"id":"remote-round","teacherScheduleId":"different-actual-slot","year":"2026-2027","semester":"1","courseId":"E020007B2","claId":"class-A","createTime":"2026-11-09 08:30:00","section":2},
                       "students":{"0001":"UNCHECKED","0002":"CHECKED","external-extra":"STUDY_TOUR"},"fetched_at":"2026-11-09T10:00:00"}
        self.remote=AsyncMock(side_effect=self.read_remote)
        self.addCleanup(patch.stopall)
        patch.object(service,"get_db_connection",self.database).start()
        patch.object(service,"get_configured_db_engine",return_value="sqlite").start()
        patch.object(service,"load_local_context",side_effect=self.local_context).start()
        patch.object(service,"load_read_context",side_effect=lambda conn,user,round_id,**kw:self.local_context(conn,user,round_id)).start()
        patch.object(service,"_account_access",side_effect=self.access_for_owner).start()
        patch.object(service,"read_remote_snapshot",self.remote).start()

    @contextmanager
    def database(self):
        try:yield self.conn
        except BaseException:self.conn.rollback();raise

    def local_context(self,conn,user,round_id):
        if user.get("role")!="teacher" or user.get("id")!=1 or str(round_id)!="31":raise HTTPException(403,"synthetic denied")
        return copy.deepcopy(self.context)

    def access_for_owner(self,conn,teacher_id,expected_key=None,expected_generation=None):
        if teacher_id!=1 or expected_key and expected_key!=self.account_key or expected_generation and expected_generation!=self.access["credential_generation"]:
            raise HTTPException(409,{"code":"needs_teacher_reauth"})
        return copy.deepcopy(self.access)

    async def read_remote(self,*args):
        self.assertFalse(self.conn.in_transaction,"Network must not retain a DB write transaction")
        return copy.deepcopy(self.snapshot)

    async def binding(self):
        return await service.bind_remote_round(self.user,31,sign_attendance_source_option(1,self.source),"remote-round")

    async def preview(self):
        binding=await self.binding()
        return await service.create_preview(self.user,31,binding["id"])

    async def test_handoff_diff_external_change_and_readback_conserve_all_people(self):
        preview=await self.preview()
        self.assertEqual(["needs_teacher_action","needs_teacher_action"],[r["state"] for r in preview["operations"]])
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM smart_attendance_write_bindings").fetchone()[0])
        with patch("classroom_app.services.scheduled_task_service.schedule_task",return_value=1) as schedule:
            service.enqueue_readback(self.conn,self.user,31,preview["id"],"same-key");self.conn.commit()
            service.enqueue_readback(self.conn,self.user,31,preview["id"],"same-key");self.conn.commit()
            self.assertEqual(1,schedule.call_count)
        self.snapshot["students"]["0001"]="LATE_OR_EARLY"
        conflict=await service.reconcile_run(self.user,31,preview["id"])
        self.assertEqual("conflict",conflict["state"])
        self.assertEqual("conflict",conflict["operations"][0]["state"])
        self.snapshot["students"].update({"0001":"CHECKED","0002":"UNCHECKED"})
        confirmed=await service.reconcile_run(self.user,31,preview["id"])
        self.assertEqual("confirmed",confirmed["state"])
        self.assertFalse(confirmed["full_remote_round_confirmed"])
        self.assertEqual("student_status_readback_only",confirmed["confirmation_scope"])
        self.assertEqual(2,len(confirmed["operations"]))
        self.assertEqual("STUDY_TOUR",self.snapshot["students"]["external-extra"])

    def test_all_five_remote_protected_states_and_unknown_values_are_never_writes(self):
        targets=service._targets(self.context)
        for status in service.PROTECTED_STATUSES:
            result=service.compare_students(targets,{"0001":status},{"0001":status})
            self.assertEqual("protected",result[0]["state"])
        result=service.compare_students(targets,{"0001":"NEW_UNKNOWN"},{"0001":"NEW_UNKNOWN"})
        self.assertEqual("blocked",result[0]["state"])
        for op in ("start","end","update_student"):
            service.require_write_capability(op)
        for op in ("save","update"):
            with self.assertRaises(HTTPException) as caught:service.require_write_capability(op)
            self.assertEqual("write_protocol_unverified",caught.exception.detail["code"])

    async def test_local_target_changes_revoke_preview_and_stale_run(self):
        preview=await self.preview()
        self.context["students"][0].update(status="SICK_LEAVE",revision=2)
        with patch("classroom_app.services.scheduled_task_service.schedule_task",side_effect=AssertionError("do not enqueue")):
            with self.assertRaises(HTTPException):service.enqueue_readback(self.conn,self.user,31,preview["id"],"request")
        result=await service.reconcile_run(self.user,31,preview["id"])
        self.assertEqual("superseded",result["state"])

    async def test_binding_identity_date_account_and_owner_are_not_guessed(self):
        bad={**self.source,"academic_term":2}
        with self.assertRaises(HTTPException):await service.bind_remote_round(self.user,31,sign_attendance_source_option(1,bad),"remote-round")
        with self.assertRaises(HTTPException):await service.bind_remote_round({"role":"teacher","id":2},31,sign_attendance_source_option(1,self.source),"remote-round")
        self.snapshot["record"]["createTime"]="2026-11-10 08:30:00"
        with self.assertRaises(HTTPException):await self.binding()
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM smart_attendance_write_bindings").fetchone()[0])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM smart_attendance_account_leases").fetchone()[0])

    async def test_generation_change_requires_same_account_reauthorization(self):
        preview=await self.preview();self.access["credential_generation"]="generation-2"
        result=await service.reconcile_run(self.user,31,preview["id"])
        self.assertEqual("needs_teacher_reauth",result["state"])
        binding=self.conn.execute("SELECT * FROM smart_attendance_write_bindings").fetchone()
        service.reauthorize_binding(self.conn,self.user,31,binding["id"],binding["revision"]);self.conn.commit()
        self.snapshot["students"].update({"0001":"CHECKED","0002":"UNCHECKED"})
        result=await service.reconcile_run(self.user,31,preview["id"])
        self.assertEqual("confirmed",result["state"])
        self.assertFalse(result["full_remote_round_confirmed"])

    async def test_pause_and_account_lease_block_parallel_work(self):
        preview=await self.preview()
        service.pause_run(self.conn,self.user,31,preview["id"],preview["revision"]);self.conn.commit()
        before=self.remote.await_count
        self.assertEqual("paused",(await service.reconcile_run(self.user,31,preview["id"]))["state"])
        self.assertEqual(before,self.remote.await_count)
        async with service._account_lease(self.account_key):
            with self.assertRaises(HTTPException) as caught:
                async with service._account_lease(self.account_key):self.fail("must not acquire twice")
            self.assertEqual(429,caught.exception.status_code)
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM smart_attendance_account_leases").fetchone()[0])

    def test_missing_unconfirmed_and_ambiguous_students_never_turn_into_absence(self):
        ctx=copy.deepcopy(self.context);ctx["students"][1]["manual_confirmed"]=0
        self.assertIsNone(service._targets(ctx)[1]["target_status"])
        ctx["students"][1]["student_number"]="0001"
        with self.assertRaises(HTTPException):service._targets(ctx)

    async def test_failed_readback_is_visible_and_retry_can_complete(self):
        preview=await self.preview()
        self.remote.side_effect=TimeoutError("sensitive remote detail must not be stored")
        with self.assertRaises(TimeoutError):await service.reconcile_run(self.user,31,preview["id"])
        failed=service.get_run(self.user,31,preview["id"])
        self.assertEqual("failed",failed["state"])
        self.assertNotIn("sensitive",failed["message"])
        self.assertIsNone(failed["verified_at"])
        self.remote.side_effect=self.read_remote
        self.snapshot["students"].update({"0001":"CHECKED","0002":"UNCHECKED"})
        self.assertEqual("confirmed",(await service.reconcile_run(self.user,31,preview["id"]))["state"])

    async def test_unknown_submission_is_reconciled_even_after_void_and_cannot_be_hidden(self):
        preview=await self.preview()
        self.conn.execute("UPDATE smart_attendance_sync_runs SET state='outcome_unknown' WHERE id=?",(preview["id"],));self.conn.commit()
        with self.assertRaises(HTTPException):service.pause_run(self.conn,self.user,31,preview["id"],preview["revision"])
        self.context.update(state="void",is_current=False,revision=2)
        result=await service.reconcile_run(self.user,31,preview["id"])
        self.assertEqual("outcome_unknown",result["state"])
        self.access["credential_generation"]="generation-2"
        self.assertEqual("outcome_unknown",(await service.reconcile_run(self.user,31,preview["id"]))["state"])
        binding=self.conn.execute("SELECT * FROM smart_attendance_write_bindings").fetchone()
        service.reauthorize_binding(self.conn,self.user,31,binding["id"],binding["revision"]);self.conn.commit()
        self.snapshot["students"].update({"0001":"CHECKED","0002":"UNCHECKED"})
        self.assertEqual("confirmed",(await service.reconcile_run(self.user,31,preview["id"]))["state"])

    async def test_expired_previews_do_not_permanently_exhaust_live_limit(self):
        preview=await self.preview()
        self.conn.execute("UPDATE smart_attendance_sync_runs SET created_at='2000-01-01T00:00:00.000000' WHERE id=?",(preview["id"],));self.conn.commit()
        binding=self.conn.execute("SELECT id FROM smart_attendance_write_bindings").fetchone()[0]
        newer=await service.create_preview(self.user,31,binding)
        self.assertEqual("preview",newer["state"])
        self.assertEqual("superseded",service.get_run(self.user,31,preview["id"])["state"])

    async def test_real_scheduler_dispatch_passes_envelope_to_readback_handler(self):
        from classroom_app.services import scheduled_task_service as scheduler
        from classroom_app.services import smart_classroom_write_jobs as jobs
        preview=await self.preview()
        task={"task_kind":service.TASK_KIND,"payload_json":json.dumps({"teacher_id":1,"round_id":31,"run_id":preview["id"]})}
        with patch.dict(scheduler._HANDLERS,{service.TASK_KIND:jobs.handle_smart_attendance_readback}), patch("classroom_app.database.get_db_connection",self.database):
            result=await scheduler._dispatch_task(task)
        self.assertEqual(preview["id"],result["run_id"])
        self.assertEqual("needs_teacher_action",result["state"])

    async def test_archived_binding_keeps_history_but_rejects_remote_operations(self):
        preview=await self.preview()
        self.conn.execute("UPDATE smart_attendance_write_bindings SET class_offering_id=NULL,session_id=NULL,state='paused' WHERE id=?",(preview["binding_id"],));self.conn.commit()
        before=self.remote.await_count
        values=service.list_bindings(self.conn,self.user,31)
        self.assertEqual(preview["binding_id"],values[0]["id"])
        self.assertEqual(preview["id"],service.get_run(self.user,31,preview["id"])["id"])
        with self.assertRaises(HTTPException) as caught:
            await service.create_preview(self.user,31,preview["binding_id"])
        self.assertEqual(409,caught.exception.status_code)
        with self.assertRaises(HTTPException):
            service.reauthorize_binding(self.conn,self.user,31,preview["binding_id"],1)
        self.assertEqual(before,self.remote.await_count)


class ActualLocalContextTests(unittest.TestCase):
    """Exercise the real local adapter, including the retake exemption scope."""
    def test_actual_adapter_enforces_teacher_and_preserves_historical_readback(self):
        with sqlite3.connect(":memory:") as conn:
            self.addCleanup(conn.close)
            conn.row_factory=sqlite3.Row
            conn.executescript("CREATE TABLE teachers(id INTEGER PRIMARY KEY,is_active INTEGER);INSERT INTO teachers VALUES(1,1);"
                "CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER);INSERT INTO class_offerings VALUES(11,1);"
                "CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER);INSERT INTO class_offering_sessions VALUES(21,11);")
            ensure_login_attendance_schema(conn,engine="sqlite")
            ctx={"class_offering_id":11,"session_id":21,"year":"2026-2027","term":1,"course_code":"SYN-ONLY"}
            conn.execute("INSERT INTO login_attendance_rounds(id,class_offering_id,session_id,teacher_id,request_key,state,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,next_evaluation_ms,policy_json,context_json) VALUES(31,11,21,1,'synthetic','published',1,2,1,2,3,1,4,'{}',?)",(json.dumps(ctx),))
            for sid,expected,manual in ((1,1,0),(2,0,0),(3,0,1)):
                conn.execute("INSERT INTO login_attendance_records(round_id,student_id,student_number,name,expected,manual_confirmed,status,updated_at_ms) VALUES(31,?,?,?,?,?,'PRESENT',1)",(sid,str(sid),"synthetic",expected,manual))
            with patch("classroom_app.services.login_attendance_service.get_configured_db_engine",return_value="sqlite"):
                result=service.load_local_context(conn,{"role":"teacher","id":1},31)
                self.assertEqual([1,3],[r["student_id"] for r in result["students"]])
                self.assertEqual([2],result["exempt_students"])
                self.assertEqual([1,3],[r["student_id"] for r in service._targets(result)])
                with self.assertRaises(HTTPException):service.load_local_context(conn,{"role":"teacher","id":2},31)
                with self.assertRaises(HTTPException):service.load_local_context(conn,{"role":"student","id":1},31)
                conn.execute("UPDATE login_attendance_rounds SET state='void',is_current=0 WHERE id=31")
                with self.assertRaises(HTTPException):service.load_local_context(conn,{"role":"teacher","id":1},31)
                history=service.load_read_context(conn,{"role":"teacher","id":1},31)
                self.assertEqual("void",history["state"])
                self.assertFalse(history["is_current"])
                conn.execute("INSERT INTO class_offerings VALUES(12,1)")
                conn.execute("UPDATE login_attendance_rounds SET class_offering_id=12,session_id=NULL WHERE id=31")
                moved=service.load_read_context(conn,{"role":"teacher","id":1},31)
                self.assertEqual(12,moved["class_offering_id"])
                self.assertIsNone(moved["session_id"])
                conn.execute("UPDATE login_attendance_rounds SET class_offering_id=NULL WHERE id=31")
                self.assertIsNone(service.load_read_context(conn,{"role":"teacher","id":1},31)["class_offering_id"])


if __name__=="__main__":unittest.main()
