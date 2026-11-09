"""Bounded synthetic outbox recovery; no business database or external IO."""
import copy
from contextlib import asynccontextmanager, contextmanager
import json
import sqlite3
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.db.schema_smart_classroom_auto_sync import ensure_smart_classroom_auto_sync_schema
from classroom_app.db.schema_smart_classroom_write import ensure_smart_classroom_write_schema
from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from classroom_app.services import smart_classroom_write_service as ledger


class AutoEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("CREATE TABLE class_offerings(id INTEGER PRIMARY KEY);INSERT INTO class_offerings VALUES(1);"
            "CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY);INSERT INTO class_offering_sessions VALUES(2);"
            "CREATE TABLE login_attendance_rounds(id TEXT PRIMARY KEY,state TEXT,is_current INTEGER);INSERT INTO login_attendance_rounds VALUES('local','published',1);")
        ensure_smart_classroom_write_schema(self.conn)
        ensure_smart_classroom_auto_sync_schema(self.conn)
        self.user = {"id": 1, "role": "teacher"}
        self.access = {"credential_id": 1, "credential_generation": "g1", "username": "synthetic", "platform_code": "gxufl_smart_classroom"}
        self.account = ledger.source_adapter.external_account_key(self.access)
        self.source = {"remote_schedule_id": "schedule", "external_account_key": self.account, "course_code": "SYNTHETIC", "remote_class_id": "class", "academic_year": "2026-2027", "academic_term": 1}
        self.slot = {"local_round_id": "local", "source_id": "schedule", "external_account_key": self.account,
            "schedule_id": "schedule", "session_date": "2026-11-09", "week": 11, "day_of_week": 1, "sections": [2, 3], "section": 2}
        self.ctx = {"round_id": "local", "class_offering_id": 1, "session_id": 2, "revision": 1, "state": "published",
            "is_current": True, "year": "2026-2027", "term": 1, "course_code": "SYNTHETIC", "session_date": "2026-11-09",
            "weekday": 0, "week_index": 11, "section_text": "2-3", "students": [
                {"student_id": 1, "student_number": "001", "status": "PRESENT", "revision": 1},
                {"student_id": 2, "student_number": "002", "status": "ABSENT", "revision": 1, "manual_confirmed": 0}]}
        self.record = {"id": "remote", "teacherScheduleId": "schedule", "isStop": True}
        self.remote = {"001": "UNCHECKED", "002": "CHECKED", "extra": "STUDY_TOUR"}
        self.writes = []
        self.fail_after_write = False
        self.fail_before_write = False
        self.conn.execute("""INSERT INTO smart_attendance_auto_sync(local_round_id,owner_teacher_id,class_offering_id,session_id,
            external_account_key,credential_generation,source_json,slot_json,remote_checkin_id,enabled,state,request_key,created_at,updated_at)
            VALUES('local',1,1,2,?,'g1',?,?,'remote',1,'pending','request','now','now')""", (self.account, json.dumps(self.source), json.dumps(self.slot)))
        self.conn.commit()
        self.addCleanup(patch.stopall)
        for module in (auto, ledger):
            patch.object(module, "get_db_connection", self.database).start()
        patch.object(ledger, "get_configured_db_engine", return_value="sqlite").start()
        patch.object(ledger, "load_read_context", side_effect=lambda *args, **kwargs: copy.deepcopy(self.ctx)).start()
        patch.object(ledger, "_account_access", side_effect=lambda *args, **kwargs: copy.deepcopy(self.access)).start()
        patch.object(auto, "open_authenticated_smart_classroom_client", self.authenticated).start()
        patch.object(auto, "schedule_due_auto_sync").start()
        patch.object(mini, "read_slot", AsyncMock(side_effect=self.read_slot)).start()
        patch.object(mini, "read_records", AsyncMock(side_effect=self.read_records)).start()
        patch.object(mini, "change_student", AsyncMock(side_effect=self.change_student)).start()
        patch.object(mini, "start_checkin", AsyncMock(side_effect=AssertionError("no create"))).start()
        patch.object(mini, "stop_checkin", AsyncMock(side_effect=AssertionError("no stop"))).start()

    @contextmanager
    def database(self):
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback()
            raise

    @asynccontextmanager
    async def authenticated(self, *args, **kwargs):
        yield object(), {}, {}

    def no_transaction(self):
        self.assertFalse(self.conn.in_transaction, "External IO must not retain the DB write transaction")

    async def read_slot(self, *args):
        self.no_transaction()
        return {"record": copy.deepcopy(self.record), "students": copy.deepcopy(self.remote), "can_checkin": True}

    async def read_records(self, *args):
        self.no_transaction()
        return copy.deepcopy(self.remote)

    async def change_student(self, client, remote_id, number, status):
        self.no_transaction()
        op = self.conn.execute("SELECT o.state FROM smart_attendance_sync_operations o JOIN smart_attendance_auto_sync p ON p.last_run_id=o.run_id WHERE o.student_number=?", (number,)).fetchone()
        self.assertEqual("outcome_unknown", op["state"], "Intent must survive process failure before external write")
        self.writes.append((number, status))
        if self.fail_before_write:
            raise TimeoutError("synthetic timeout")
        self.remote[number] = status
        if self.fail_after_write:
            raise TimeoutError("synthetic response lost")
        return {"state": "ok"}

    def plan(self):
        return dict(self.conn.execute("SELECT * FROM smart_attendance_auto_sync").fetchone())

    async def test_published_only_partial_and_external_extras_preserved(self):
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("partial", result["state"])
        self.assertEqual([("001", "CHECKED")], self.writes)
        self.assertEqual("CHECKED", self.remote["002"])
        self.assertEqual("STUDY_TOUR", self.remote["extra"])
        await auto.process_auto_sync(1, "local")
        self.assertEqual(1, len(self.writes))

    async def test_response_lost_recovers_by_read_without_replaying_write(self):
        self.fail_after_write = True
        self.assertEqual("outcome_unknown", (await auto.process_auto_sync(1, "local"))["state"])
        self.fail_after_write = False
        self.assertEqual("partial", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual(1, len(self.writes))

    async def test_unknown_request_not_observed_is_never_blindly_retried(self):
        self.fail_before_write = True
        await auto.process_auto_sync(1, "local")
        self.fail_before_write = False
        self.assertEqual("outcome_unknown", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual(1, len(self.writes))

    async def test_void_after_submission_allows_readback_but_no_other_writes(self):
        self.fail_after_write = True
        await auto.process_auto_sync(1, "local")
        self.ctx.update(state="void", is_current=False)
        self.conn.execute("UPDATE login_attendance_rounds SET state='void',is_current=0")
        self.conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state='outcome_unknown'")
        self.conn.commit()
        self.fail_after_write = False
        await auto.process_auto_sync(1, "local")
        self.assertEqual("void", self.plan()["state"])
        self.assertEqual(1, len(self.writes))

    async def test_settling_never_pushes_students_and_protected_school_status_survives(self):
        self.ctx["state"] = "settling"
        self.assertEqual("waiting_settlement", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual([], self.writes)
        self.ctx["state"] = "published"
        self.remote["001"] = "SICK_LEAVE"
        await auto.process_auto_sync(1, "local")
        self.assertEqual([], self.writes)
        self.assertEqual("SICK_LEAVE", self.remote["001"])

    async def test_paused_unknown_resolves_without_sending_remaining_students(self):
        self.ctx['students'][1].update(status='PRESENT')
        self.remote['002']='UNCHECKED'
        self.fail_after_write=True
        await auto.process_auto_sync(1,'local')
        self.conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state='outcome_unknown',revision=revision+1")
        self.conn.commit()
        self.fail_after_write=False
        await auto.process_auto_sync(1,'local')
        self.assertEqual('paused',self.plan()['state'])
        self.assertEqual('paused',self.conn.execute('SELECT state FROM smart_attendance_sync_runs').fetchone()[0])
        self.assertEqual('UNCHECKED',self.remote['002'])
        self.assertEqual(1,len(self.writes))

    async def test_corrected_published_version_gets_new_run(self):
        await auto.process_auto_sync(1, "local")
        self.ctx["revision"] = 2
        self.ctx["students"][0].update(status="SICK_LEAVE", revision=2)
        await auto.process_auto_sync(1, "local")
        self.assertEqual([("001", "CHECKED"), ("001", "SICK_LEAVE")], self.writes)
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM smart_attendance_sync_runs").fetchone()[0])

    async def test_external_change_after_confirmation_is_not_overwritten(self):
        await auto.process_auto_sync(1, "local")
        self.remote["001"] = "LATE_OR_EARLY"
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("conflict", result["state"])
        self.assertEqual(1, len(self.writes))

    def test_stale_worker_state_cannot_overwrite_pause_or_submit_unlogged_intent(self):
        old = self.plan()
        self.conn.execute("UPDATE smart_attendance_auto_sync SET revision=2,enabled=0,state='paused'")
        self.conn.commit()
        with self.assertRaises(HTTPException):
            auto._plan_state(old, "outcome_unknown", intent_json=json.dumps({"operation": "start"}))
        self.assertEqual("paused", self.plan()["state"])

    async def test_each_chunk_is_bounded_and_restart_continues_pending_only(self):
        self.ctx["students"] = [{"student_id": i, "student_number": f"N{i}", "status": "PRESENT", "revision": 1} for i in range(1, 8)]
        self.remote = {f"N{i}": "UNCHECKED" for i in range(1, 8)}
        with patch.object(auto, "BATCH_SIZE", 3):
            self.assertEqual("running", (await auto.process_auto_sync(1, "local"))["state"])
            self.assertEqual(3, len(self.writes))
            await auto.process_auto_sync(1, "local")
            self.assertEqual(6, len(self.writes))
            self.assertEqual("confirmed", (await auto.process_auto_sync(1, "local"))["state"])
            self.assertEqual(7, len(self.writes))

    async def test_create_bind_stop_publish_full_lifecycle(self):
        self.ctx["state"] = "running"
        self.record = None
        self.conn.execute("UPDATE smart_attendance_auto_sync SET remote_checkin_id=NULL,intent_json=?", (json.dumps({"allow_create": True,
            "location": {"latitude": 22, "longitude": 108, "captured_at_ms": time.time() * 1000}}),))
        self.conn.commit()

        async def history(*args):
            self.no_transaction()
            return [copy.deepcopy(self.record)] if self.record else []

        async def start(*args):
            self.no_transaction()
            self.assertEqual("outcome_unknown", self.plan()["state"])
            self.assertEqual("start", json.loads(self.plan()["intent_json"])["operation"])
            self.record = {"id": "created", "teacherScheduleId": "schedule", "isStop": False}
            return copy.deepcopy(self.record)

        async def stop(client, remote_id):
            self.no_transaction()
            self.assertEqual("created", remote_id)
            self.assertEqual("stop", json.loads(self.plan()["intent_json"])["operation"])
            self.record["isStop"] = True
            return {"state": "ok"}

        with patch.object(mini, "read_history", AsyncMock(side_effect=history)), patch.object(mini, "start_checkin", AsyncMock(side_effect=start)) as starts, patch.object(mini, "stop_checkin", AsyncMock(side_effect=stop)) as stops:
            self.assertEqual("waiting_local_end", (await auto.process_auto_sync(1, "local"))["state"])
            self.assertTrue(self.plan()["binding_id"])
            self.assertEqual("created", self.plan()["remote_checkin_id"])
            self.ctx["state"] = "settling"
            self.assertEqual("waiting_settlement", (await auto.process_auto_sync(1, "local"))["state"])
            self.assertEqual([], self.writes)
            self.ctx["state"] = "published"
            self.assertEqual("partial", (await auto.process_auto_sync(1, "local"))["state"])
            self.assertEqual(1, starts.await_count)
            self.assertEqual(1, stops.await_count)
            self.assertEqual([("001", "CHECKED")], self.writes)


class MiniAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_contract_uses_raw_form_and_distinct_start_response(self):
        import httpx
        requests = []
        def respond(request):
            requests.append(request)
            if request.url.path.endswith("startCheckin"):
                return httpx.Response(200, json={"id": "R", "teacherScheduleId": "S", "week": 2, "dayOfWeek": 7, "section": 8})
            return httpx.Response(200, json={"state": "ok"})
        async with httpx.AsyncClient(base_url="https://edu-api.gxufl.com/api", transport=httpx.MockTransport(respond)) as client:
            with patch.object(mini, "REQUEST_INTERVAL", 0):
                record = await mini.start_checkin(client, {"schedule_id": "S", "week": 2, "day_of_week": 7, "section": 8}, {"latitude": 20, "longitude": 110})
                await mini.change_student(client, "R", "001", "CHECKED")
                await mini.stop_checkin(client, "R")
        self.assertEqual("R", record["id"])
        self.assertIn(b"username=001", requests[1].content)
        self.assertIn("application/x-www-form-urlencoded", requests[1].headers["content-type"])

    def test_duplicate_remote_numbers_and_invalid_location_fail_closed(self):
        with self.assertRaises(ValueError):
            mini.student_map([{"no": "001", "status": "CHECKED"}, {"no": "001", "status": "UNCHECKED"}])
        for value in (None, {"latitude": float("nan"), "longitude": 110, "captured_at_ms": 0}):
            with self.assertRaises(HTTPException):
                auto._location(value)
