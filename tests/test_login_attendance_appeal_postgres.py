"""Opt-in native races inside an exclusively created synthetic database.

Uses the offline rehearsal's physical data_directory verification. No product
DATABASE_URL, arbitrary DSN, production schema, or existing business table is
ever used. The cluster must already have been explicitly started by its owner.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.services import approval_workflow_schema as schema
from classroom_app.services import approval_workflow_service as workflow
from classroom_app.services import login_attendance_service as attendance
from classroom_app.services.approval_request_types import login_attendance_appeal as appeal
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),"Requires an explicitly owned offline PostgreSQL cluster")
class AttendanceAppealPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster=Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port=int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database="lanshare_assessment_rehearsal_attendance_"+uuid4().hex[:16]
        cls.admin=connect_offline(cluster_dir=cls.cluster,port=cls.port,database="lanshare_assessment_rehearsal")
        cls.admin.autocommit=True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:cls.admin.close()

    @contextmanager
    def connection(self):
        raw=connect_offline(cluster_dir=self.cluster,port=self.port,database=self.database)
        raw.row_factory=sqlite_compatible_dict_row
        conn=LanSharePostgresConnection(raw)
        try:
            conn.execute("SET lock_timeout='5s'")
            conn.execute("SET statement_timeout='8s'")
            yield conn
        except BaseException:
            conn.rollback();raise
        finally:conn.close()

    def setUp(self):
        self.addCleanup(patch.stopall)
        for target in ("classroom_app.db.connection.get_configured_db_engine",
                       "classroom_app.services.approval_workflow_schema.get_configured_db_engine",
                       "classroom_app.services.login_attendance_service.get_configured_db_engine",
                       "classroom_app.services.approval_request_types.login_attendance_appeal.get_configured_db_engine"):
            patch(target,return_value="postgres").start()
        patch.object(schema,"_SCHEMA_READY",False).start()
        patch.object(workflow,"_notify",return_value=1).start()
        self.teacher={"role":"teacher","id":1,"name":"Synthetic"}
        self.student={"role":"student","id":1,"name":"Synthetic"}
        now=attendance.now_ms()
        with self.connection() as conn:
            conn.execute("DROP SCHEMA public CASCADE");conn.execute("CREATE SCHEMA public")
            conn.execute("CREATE TABLE teachers(id BIGINT PRIMARY KEY,name TEXT,is_super_admin INTEGER DEFAULT 0,is_active INTEGER DEFAULT 1)")
            conn.execute("INSERT INTO teachers(id,name) VALUES(1,'Synthetic owner'),(2,'Synthetic next owner')")
            conn.execute("CREATE TABLE class_offerings(id BIGINT PRIMARY KEY,teacher_id BIGINT)")
            conn.execute("INSERT INTO class_offerings VALUES(11,1)")
            conn.execute("CREATE TABLE class_offering_sessions(id BIGINT PRIMARY KEY,class_offering_id BIGINT)")
            conn.execute("INSERT INTO class_offering_sessions VALUES(21,11)")
            ensure_login_attendance_schema(conn,engine="postgres")
            schema.ensure_approval_workflow_schema(conn)
            conn.execute("INSERT INTO login_attendance_rounds(id,class_offering_id,session_id,teacher_id,request_key,state,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,ended_at_ms,next_evaluation_ms,policy_json,context_json) VALUES(31,11,21,1,'synthetic','published',?,?,?,?,?,?,?,?,?,?)",
                (now-5400000,now-60000,now-6000000,now,now,now-5400000,now-60000,now,json.dumps({"appeal_days":7}),json.dumps({"class_offering_id":11,"session_id":21})))
            conn.execute("INSERT INTO login_attendance_records(id,round_id,student_id,student_number,name,updated_at_ms) VALUES(1,31,1,'SYN-1','Synthetic',?)",(now,))
            conn.commit()

    class Probe:
        def __init__(self,conn,entered):self.conn,self.entered=conn,entered
        def __getattr__(self,name):return getattr(self.conn,name)
        def execute(self,statement,params=()):
            if "FROM class_offerings WHERE id=" in statement and "FOR NO KEY UPDATE" in statement:
                self.entered.set()
            return self.conn.execute(statement,params)

    def create(self,entered):
        with self.connection() as conn:
            try:
                result=workflow.create_request(self.Probe(conn,entered),self.student,request_type=appeal.KEY,subject_id=1,reason="Synthetic verification")
                conn.commit();return result
            except workflow.ApprovalWorkflowError as exc:
                conn.rollback();return exc.status_code

    def test_native_parent_fence_does_not_block_recorder_key_share(self):
        with self.connection() as owner:
            attendance.require_teacher(owner,11,self.teacher,lock=True)
            with ThreadPoolExecutor(max_workers=1) as pool:
                def intake():
                    with self.connection() as conn:
                        conn.execute("SELECT id FROM class_offerings WHERE id=11 FOR KEY SHARE")
                        conn.commit();return True
                self.assertTrue(pool.submit(intake).result(timeout=2))
            owner.rollback()

    def test_native_creation_waits_for_void_and_rechecks_before_insert(self):
        entered=Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connection() as owner:
                attendance.require_teacher(owner,11,self.teacher,lock=True)
                future=pool.submit(self.create,entered)
                try:
                    self.assertTrue(entered.wait(2))
                    with self.assertRaises(FutureTimeout):future.result(timeout=.15)
                    attendance.void_round(owner,31,self.teacher,{"revision":1,"reason":"Synthetic concurrent void"})
                finally:owner.commit()
            self.assertEqual(409,future.result(timeout=4))
        with self.connection() as conn:
            self.assertEqual(0,conn.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0])
            self.assertEqual(0,conn.execute("SELECT COUNT(*) FROM login_attendance_attention").fetchone()[0])

    def test_native_transfer_during_approval_wait_cannot_authorize_old_teacher(self):
        request=self.create(Event())
        entered=Event()
        def decide():
            with self.connection() as conn:
                try:
                    result=workflow.decide_request(self.Probe(conn,entered),self.teacher,request["id"],decision="approve")
                    conn.commit();return result
                except (workflow.ApprovalWorkflowError,HTTPException) as exc:
                    conn.rollback();return exc.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connection() as owner:
                attendance.require_teacher(owner,11,self.teacher,lock=True)
                future=pool.submit(decide)
                try:
                    self.assertTrue(entered.wait(2))
                    with self.assertRaises(FutureTimeout):future.result(timeout=.15)
                    owner.execute("UPDATE class_offerings SET teacher_id=2 WHERE id=11")
                finally:owner.commit()
            self.assertEqual(403,future.result(timeout=4))
        with self.connection() as conn:
            self.assertEqual("pending",workflow._load_request_row(conn,request["id"])["status"])
            self.assertEqual("PENDING",conn.execute("SELECT status FROM login_attendance_records WHERE id=1").fetchone()[0])


if __name__=="__main__":unittest.main()
