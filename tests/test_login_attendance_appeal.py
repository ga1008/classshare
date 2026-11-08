"""Actual approval/local-attendance transactions, synthetic SQLite only."""
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
from classroom_app.services import approval_workflow_schema as schema
from classroom_app.services import approval_workflow_service as workflow
from classroom_app.services import login_attendance_service as attendance
from classroom_app.services.approval_request_types import login_attendance_appeal as appeal


class AttendanceAppealTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(":memory:");self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT,is_super_admin INTEGER DEFAULT 0);"
            "INSERT INTO teachers VALUES(1,'Original',0),(2,'Current',0),(3,'Admin',1);"
            "ALTER TABLE teachers ADD COLUMN is_active INTEGER DEFAULT 1;"
            "CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER);INSERT INTO class_offerings VALUES(11,1);"
            "CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER);INSERT INTO class_offering_sessions VALUES(21,11);")
        self.addCleanup(patch.stopall)
        patch.object(schema,"get_configured_db_engine",return_value="sqlite").start()
        patch.object(schema,"_SCHEMA_READY",False).start()
        patch.object(attendance,"get_configured_db_engine",return_value="sqlite").start()
        patch.object(appeal,"get_configured_db_engine",return_value="sqlite").start()
        ensure_login_attendance_schema(self.conn,engine="sqlite")
        schema.ensure_approval_workflow_schema(self.conn)
        self.now=attendance.now_ms()
        ctx={"class_offering_id":11,"session_id":21,"course_name":"Synthetic","year":"2026-2027","term":1}
        self.conn.execute("INSERT INTO login_attendance_rounds(id,class_offering_id,session_id,teacher_id,request_key,state,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,ended_at_ms,next_evaluation_ms,policy_json,context_json) VALUES(31,11,21,1,'synthetic','published',?,?,?,?,?,?,?,?,?,?)",
            (self.now-90*60000,self.now-60000,self.now-100*60000,self.now,self.now,self.now-90*60000,self.now-60000,self.now,json.dumps({"appeal_days":7}),json.dumps(ctx)))
        for sid in (1,2):
            self.conn.execute("INSERT INTO login_attendance_records(id,round_id,student_id,student_number,name,updated_at_ms) VALUES(?,31,?,?,?,?)",
                (sid,sid,"SYN"+str(sid),"Synthetic "+str(sid),self.now))
        self.conn.commit()
        self.teacher={"role":"teacher","id":1,"name":"Original"}
        self.current={"role":"teacher","id":2,"name":"Current"}
        self.student={"role":"student","id":1,"name":"Synthetic 1"}
        self.notifications=[]
        patch.object(workflow,"_notify",side_effect=self.capture).start()

    def capture(self,conn,**kwargs):
        self.notifications.append(kwargs)
        return len(kwargs["recipients"])

    def create(self):
        result=workflow.create_request(self.conn,self.student,request_type=appeal.KEY,subject_id=1,reason="Please verify")
        self.conn.commit();return result

    def test_create_holds_domain_lock_before_prepare_and_creates_durable_attention(self):
        with patch.object(attendance,"load_round",wraps=attendance.load_round) as load:
            request=self.create()
        self.assertTrue(any(call.kwargs.get("lock") for call in load.call_args_list))
        row=self.conn.execute("SELECT * FROM login_attendance_attention WHERE request_id=?",(request["id"],)).fetchone()
        self.assertEqual((31,1,1),(row["round_id"],row["student_id"],row["teacher_id"]))
        self.assertEqual([{ "role":"teacher","id":1}],self.notifications[0]["recipients"])
        with self.assertRaises(workflow.ApprovalWorkflowError):self.create()
        self.conn.rollback()
        self.assertEqual(1,self.conn.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0])

    def test_locked_round_recheck_rejects_creation_and_decision_after_replacement(self):
        original=attendance.load_round
        def replaced(conn,round_id,user=None,*,lock=False):
            row=original(conn,round_id,user,lock=lock)
            return {**row,"is_current":0} if lock else row
        with patch.object(attendance,"load_round",side_effect=replaced):
            with self.assertRaises(workflow.ApprovalWorkflowError):self.create()
        self.conn.rollback()
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0])
        request=self.create()
        with patch.object(attendance,"load_round",side_effect=replaced):
            with self.assertRaises(workflow.ApprovalWorkflowError):
                workflow.decide_request(self.conn,self.teacher,request["id"],decision="approve")
        self.conn.rollback()
        self.assertEqual("pending",workflow._load_request_row(self.conn,request["id"])["status"])
        self.assertEqual("PENDING",self.conn.execute("SELECT status FROM login_attendance_records WHERE id=1").fetchone()[0])

    def test_teacher_transfer_changes_inbox_count_detail_and_decision_authority(self):
        request=self.create()
        self.conn.execute("UPDATE class_offerings SET teacher_id=2 WHERE id=11");self.conn.commit()
        self.assertEqual([],workflow.list_requests(self.conn,self.teacher))
        self.assertEqual(0,workflow.count_pending_requests(self.conn,self.teacher))
        with self.assertRaises(workflow.ApprovalWorkflowError):workflow.get_request(self.conn,self.teacher,request["id"])
        incoming=workflow.list_requests(self.conn,self.current)
        self.assertEqual([request["id"]],[r["id"] for r in incoming])
        self.assertTrue(incoming[0]["can_decide"])
        self.assertEqual(1,workflow.count_pending_requests(self.conn,self.current))
        self.assertTrue(workflow.get_request(self.conn,self.current,request["id"])["can_decide"])
        # An administrator who used to own the course cannot bypass its
        # stricter attendance domain authorization through scope=all.
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=1");self.conn.commit()
        self.assertEqual([],workflow.list_requests(self.conn,self.teacher,scope="all"))
        result=workflow.decide_request(self.conn,self.current,request["id"],decision="approve",decision_payload={"status":"PRESENT"})
        self.conn.commit()
        self.assertEqual("approved",result["status"])
        self.assertEqual("PRESENT",self.conn.execute("SELECT status FROM login_attendance_records WHERE id=1").fetchone()[0])
        self.assertIsNotNone(self.conn.execute("SELECT resolved_at_ms FROM login_attendance_attention").fetchone()[0])

    def test_current_teacher_gets_one_reminder_and_accepted_case_never_expires(self):
        request=self.create();self.notifications.clear()
        self.conn.execute("UPDATE class_offerings SET teacher_id=2 WHERE id=11")
        self.conn.execute("UPDATE approval_requests SET created_at=? WHERE id=?",((datetime.now()-timedelta(days=100)).isoformat(),request["id"]))
        self.conn.commit()
        first=workflow.remind_stale_requests(self.conn);self.conn.commit()
        second=workflow.remind_stale_requests(self.conn);self.conn.commit()
        self.assertEqual({"reminded":1,"expired":0},first)
        self.assertEqual({"reminded":0,"expired":0},second)
        self.assertEqual([{ "role":"teacher","id":2}],self.notifications[0]["recipients"])
        self.assertEqual("pending",workflow._load_request_row(self.conn,request["id"])["status"])

    def test_other_student_and_live_void_or_archived_round_are_denied(self):
        with self.assertRaises(workflow.ApprovalWorkflowError):
            workflow.create_request(self.conn,{"role":"student","id":2},request_type=appeal.KEY,subject_id=1,reason="Not my record")
        self.conn.rollback()
        for state in ("scheduled","running","void"):
            self.conn.execute("UPDATE login_attendance_rounds SET state=? WHERE id=31",(state,));self.conn.commit()
            with self.assertRaises(workflow.ApprovalWorkflowError):self.create()
            self.conn.rollback()
        self.conn.execute("UPDATE login_attendance_rounds SET class_offering_id=NULL WHERE id=31");self.conn.commit()
        self.assertFalse(appeal.authorize(self.conn,{"subject_id":"1"},self.teacher))

    def test_merged_history_link_keeps_exact_round_and_deleted_history_is_read_only(self):
        request=self.create()
        original=workflow._load_request_row(self.conn,request["id"])
        payload=json.loads(original["payload_json"]);payload["history_only"]=True
        moved={**original,"class_offering_id":12,"payload_json":json.dumps(payload)}
        teacher_link=appeal.inbox_link(moved,"teacher")
        self.assertIn("/classroom/12?attendance_id=31",teacher_link)
        self.assertIn("history_only=1",teacher_link)
        self.assertNotIn("session_id",teacher_link)
        fallback="/profile?section=notifications#profile-message-center"
        self.assertEqual(fallback,appeal.inbox_link(moved,"student"))
        self.conn.execute("UPDATE login_attendance_rounds SET class_offering_id=NULL,state='void' WHERE id=31");self.conn.commit()
        terminal={**original,"class_offering_id":None,"status":"cancelled"}
        self.assertEqual(fallback,appeal.inbox_link(terminal,"teacher"))
        self.assertTrue(appeal.authorize(self.conn,terminal,self.teacher))
        self.assertFalse(appeal.authorize(self.conn,terminal,self.current))
        with self.assertRaises(workflow.ApprovalWorkflowError):appeal.before_decision(self.conn,terminal,self.teacher,{})


if __name__=="__main__":unittest.main()
