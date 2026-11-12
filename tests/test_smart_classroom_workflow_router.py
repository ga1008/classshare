"""Real workflow HTTP, local schemas and school attendance cache; fake school only."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
import copy
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_materials_integrations import ensure_materials_integrations_schema
from classroom_app.db.schema_offering_class_links import ensure_offering_class_links_schema
from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema
from classroom_app.db.schema_smart_classroom_write import ensure_smart_classroom_write_schema
from classroom_app.db.schema_smart_classroom_auto_sync import ensure_smart_classroom_auto_sync_schema
from classroom_app.routers import smart_classroom_write as routes
from classroom_app.services import smart_classroom_workflow_service as flow
from classroom_app.services import smart_classroom_write_service as ledger
from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_miniapp_adapter as mini


class WorkflowRouterTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        ensure_foundation_schema(self.conn)
        ensure_assignment_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        ensure_materials_integrations_schema(self.conn)
        ensure_offering_class_links_schema(self.conn, force=True, engine="sqlite")
        ensure_login_attendance_schema(self.conn, engine="sqlite")
        ensure_smart_classroom_write_schema(self.conn, engine="sqlite")
        ensure_smart_classroom_auto_sync_schema(self.conn, engine="sqlite")
        self.conn.executescript("""
            CREATE TABLE classroom_retake_students(class_offering_id INTEGER,student_id INTEGER,status TEXT);
            INSERT INTO teachers(id,name,email,hashed_password,is_active) VALUES(7,'Teacher','teacher@example.test','synthetic',1),(8,'Other','other@example.test','synthetic',1);
            INSERT INTO classes(id,name,created_by_teacher_id) VALUES(1,'Synthetic class',7);
            INSERT INTO courses(id,name,academic_course_code,created_by_teacher_id) VALUES(1,'Synthetic course','E1',7);
            INSERT INTO class_offerings(id,class_id,course_id,teacher_id,semester) VALUES(10,1,1,7,'2026-2027学年第1学期');
            INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code)
              VALUES(21,10,1,'Synthetic lesson','2026-11-09',0,11,'2-3','E1');
            INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) VALUES(1,'S1','One',1,'active'),(2,'S2','Two',1,'active');
        """)
        self.conn.commit()
        self.user = {"id": 7, "role": "teacher"}
        self.access = {"credential_id": 7, "credential_generation": "g1", "username": "synthetic", "platform_code": "gxufl_smart_classroom"}
        self.remote = {"S1": "UNCHECKED", "S2": "CHECKED"}
        self.record = {"id": "remote", "teacherScheduleId": "schedule", "isStop": True, "year": "2026-2027", "semester": 1,
                       "courseId": "E1", "claId": "class", "week": 11, "dayOfWeek": 1, "section": 2, "createTime": "2026-11-09 08:30:00", "updateTime": "2035-01-01 08:00:00"}
        for module in (routes, flow, ledger, auto):
            self.enterContext(patch.object(module, "get_db_connection", self.database))
        self.enterContext(patch.object(ledger, "get_configured_db_engine", return_value="sqlite"))
        self.account = self.enterContext(patch.object(ledger, "load_teacher_smart_classroom_access_method", side_effect=lambda *args: self.access))
        for module in (flow, auto):
            self.enterContext(patch.object(module, "open_authenticated_smart_classroom_client", self.authenticated))
        self.enterContext(patch.object(auto, "_grant", Mock()))
        self.enterContext(patch.object(auto, "schedule_due_auto_sync", Mock()))
        self.enterContext(patch.object(flow, "_ai_assist", AsyncMock(return_value={"status": "unavailable", "decision": None})))
        self.enterContext(patch.object(ledger.source_adapter, "fetch_source_schedules", AsyncMock(return_value=[{"id": "schedule", "courseId": "E1", "course": "Synthetic course", "claId": "class", "claName": "Synthetic class"}])))
        self.enterContext(patch.object(mini, "list_schedules", AsyncMock(return_value={"year": "2026-2027", "semester": 1,
            "items": [{"id": "schedule", "courseCode": "E1", "sections": "2-3", "xqj": 1, "weeks": [11]}]})))
        self.enterContext(patch.object(mini, "read_slot", AsyncMock(side_effect=self.read_slot)))
        self.enterContext(patch.object(mini, "read_records", AsyncMock(side_effect=lambda *args: copy.deepcopy(self.remote))))
        self.enterContext(patch.object(mini, "change_student", AsyncMock(side_effect=self.change)))
        app = FastAPI()
        app.include_router(routes.router)
        app.include_router(routes.session_router)
        app.dependency_overrides[routes.get_current_teacher] = lambda: self.user
        self.client = self.enterContext(TestClient(app, base_url="https://platform.test"))
        self.base = "/api/classrooms/10/login-attendance/sessions/21/external"
        self.headers = {"Origin": "https://platform.test"}

    @contextmanager
    def database(self):
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    @asynccontextmanager
    async def authenticated(self, *args, **kwargs):
        self.assertFalse(self.conn.in_transaction)
        yield object(), {}, {}

    async def read_slot(self, *args):
        self.assertFalse(self.conn.in_transaction)
        return {"record": copy.deepcopy(self.record), "students": copy.deepcopy(self.remote), "can_checkin": True}

    async def change(self, client, checkin, number, status):
        self.assertFalse(self.conn.in_transaction)
        imported = self.conn.execute("SELECT status FROM smart_classroom_checkin_students WHERE student_number=?", (number,)).fetchone()
        self.assertEqual("UNCHECKED", imported["status"], "School snapshot must be persisted before sending corrections")
        self.remote[number] = status

    def seed_round(self):
        ctx = flow._lesson(self.conn, self.user, 10, 21)
        self.conn.execute("""INSERT INTO login_attendance_rounds(id,class_offering_id,session_id,teacher_id,request_key,state,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,next_evaluation_ms,policy_json,context_json,ended_at_ms)
            VALUES(31,10,21,7,'seed-round','published',1,2,1,2,3,1,3,'{}',?,2)""", (json.dumps(ctx),))
        for sid, number, status in ((1, "S1", "PRESENT"), (2, "S2", "ABSENT")):
            self.conn.execute("INSERT INTO login_attendance_records(round_id,student_id,student_number,name,status,manual_confirmed,decided_at_ms,updated_at_ms) VALUES(31,?,?,?,?,1,2,2)", (sid, number, number, status))
        self.conn.commit()

    def test_real_match_dispatch_import_readback_and_local_fact_preservation(self):
        initial = self.client.get(self.base + "/workflow")
        self.assertEqual(200, initial.status_code)
        self.assertEqual("no-store", initial.headers["cache-control"])
        self.assertFalse(initial.json()["can_sync"])
        matched = self.client.post(self.base + "/match", headers=self.headers, json={})
        self.assertEqual(200, matched.status_code, matched.text)
        self.assertEqual("matched", matched.json()["binding"]["state"])
        self.seed_round()
        payload = {"idempotency_key": "http-teacher-click", "expected_binding_revision": matched.json()["binding"]["revision"]}
        response = self.client.post("/api/classrooms/10/login-attendance/rounds/31/external/sync", headers=self.headers, json=payload)
        self.assertEqual(202, response.status_code, response.text)
        result = asyncio.run(auto.process_auto_sync(7, 31))
        self.assertEqual("confirmed", result["state"])
        self.assertEqual({"S1": "CHECKED", "S2": "CHECKED"}, self.remote)
        self.assertEqual(["CHECKED", "CHECKED"], [r[0] for r in self.conn.execute("SELECT status FROM smart_classroom_checkin_students ORDER BY student_number")])
        self.assertEqual(2, self.conn.execute("SELECT checked_count FROM smart_classroom_checkin_sessions").fetchone()[0])
        self.assertEqual(["PRESENT", "ABSENT"], [r[0] for r in self.conn.execute("SELECT status FROM login_attendance_records ORDER BY student_number")])
        repeated = self.client.post("/api/classrooms/10/login-attendance/rounds/31/external/sync", headers=self.headers, json=payload)
        self.assertEqual(202, repeated.status_code)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM smart_attendance_sync_runs").fetchone()[0])

    def test_origin_owner_missing_credentials_and_unmatched_guard(self):
        self.assertEqual(403, self.client.post(self.base + "/match", headers={"Origin": "https://other.test"}, json={}).status_code)
        self.user = {"id": 8, "role": "teacher"}
        self.assertEqual(403, self.client.get(self.base + "/workflow").status_code)
        self.assertEqual(403, self.client.post(self.base + "/match", headers=self.headers, json={}).status_code)
        self.user = {"id": 7, "role": "teacher"}
        self.access = None
        missing = self.client.post(self.base + "/match", headers=self.headers, json={})
        self.assertEqual("missing_credential", missing.json()["detail"]["code"])
        self.seed_round()
        guarded = self.client.post("/api/classrooms/10/login-attendance/rounds/31/external/sync", headers=self.headers,
            json={"idempotency_key": "unmatched-click", "expected_binding_revision": 1})
        self.assertEqual("lesson_unmatched", guarded.json()["detail"]["code"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM login_attendance_records").fetchone()[0])
