"""Synthetic workflow transitions, exact matching and evidence-based merge."""
import copy
import json
import time
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from classroom_app.services import smart_classroom_workflow_service as workflow
from classroom_app.services import smart_classroom_session_service as sessions
from tests.test_smart_classroom_auto_sync_engine import AutoEngineTests
from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_write_service as ledger
from classroom_app.services import smart_classroom_miniapp_adapter as mini
from classroom_app.services import smart_classroom_alignment_service as alignment


class WorkflowTests(AutoEngineTests):
    def setUp(self):
        super().setUp()
        self.conn.execute("ALTER TABLE login_attendance_rounds ADD COLUMN session_id INTEGER")
        self.conn.execute("ALTER TABLE login_attendance_rounds ADD COLUMN class_offering_id INTEGER")
        self.conn.execute("UPDATE login_attendance_rounds SET session_id=2,class_offering_id=1")
        self.conn.commit()
        self.ctx.update(course_name="合成课程", ended_at_ms=2000000000000, closed=False)
        self.access["platform_code"] = "gxufl_smart_classroom"
        self.patch(workflow, "get_db_connection", self.database)
        self.patch(alignment, "get_db_connection", self.database)
        self.patch(alignment, 'offering_alignment_context', self.alignment_context)
        self.patch(workflow.local, 'require_teacher', Mock())
        self.patch(workflow, "_lesson", lambda *args, **kwargs: copy.deepcopy(self.ctx))
        self.patch(workflow, "open_authenticated_smart_classroom_client", self.authenticated)
        self.patch(mini, 'read_alignment_slot', mini.read_slot)
        self.patch(workflow.local, "_roster", Mock(return_value=[{"student_id_number": "001"}, {"student_id_number": "002"}]))
        self.ai = self.patch(workflow, "_ai_assist", AsyncMock(return_value={"status": "unavailable", "decision": None}))
        self.persist = self.patch(workflow, "persist_remote_snapshot", Mock())
        self.patch(auto, "_grant", Mock())
        self.group = {"id": "schedule", "courseId": "SYNTHETIC", "course": "合成课程", "claId": "class", "claName": "合成班", 'credits': 2, 'total_hours': 2}
        self.patch(ledger.source_adapter, "fetch_source_schedules", AsyncMock(return_value=[self.group]))
        self.patch(mini, "list_schedules", AsyncMock(return_value={"year": "2026-2027", "semester": 1,
            "items": [{"id": "schedule", "courseCode": "SYNTHETIC", "sections": "2-3", "xqj": 1, "weeks": [11]}]}))

    def alignment_context(self, conn, user, offering_id):
        numbers = {r['student_id_number'] for r in workflow.local._roster(conn, offering_id)}
        course = {'course_name': self.ctx['course_name'], 'course_code': self.ctx['course_code'], 'academic_year': self.ctx['year'], 'academic_term': self.ctx['term'], 'student_count': len(numbers), 'session_count': 1, 'credits': 2, 'total_hours': 2}
        session = {'id': 2, 'session_date': self.ctx['session_date'], 'week': self.ctx['week_index'], 'day_of_week': 1, 'sections': [2,3], 'order_index': 1}
        fp = ledger._hash({'course': course, 'numbers': sorted(numbers)})
        return {'course':course,'sessions':[session],'contexts':{2:copy.deepcopy(self.ctx)},'numbers':numbers,'access':self.access,'account_error':None,'course_fingerprint':fp,'fingerprint':ledger._hash([fp,session])}

    def patch(self, module, name, value):
        self.enterContext(patch.object(module, name, value))
        return value

    def manual(self):
        intent = {"manual_dispatch": True, "authorized_fingerprint": ledger.result_fingerprint(self.ctx), "allow_create": False}
        self.conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=?", (json.dumps(intent),))
        self.conn.commit()

    async def bind_lesson(self):
        self.conn.execute("DELETE FROM smart_attendance_auto_sync")
        self.conn.commit()
        return await workflow.match_lesson(self.user, 1, 2)

    async def test_match_before_round_and_fail_keeps_local_round(self):
        result = await self.bind_lesson()
        self.assertEqual("matched", result["binding"]["state"])
        self.assertTrue(result["can_sync"])
        self.assertEqual([], self.writes)
        self.remote.pop("002")
        result = await workflow.match_lesson(self.user, 1, 2)
        self.assertEqual("stale", result["binding"]["state"])
        self.assertFalse(result["can_sync"])
        self.assertEqual("published", self.conn.execute("SELECT state FROM login_attendance_rounds").fetchone()[0])

    async def test_course_code_alias_requires_complete_roster(self):
        self.ctx["course_code"] = "LOCAL-ALIAS"
        self.ai.side_effect = lambda task, facts: {'status':'assisted','decision':facts['allowed_decisions'][1]}
        result = await self.bind_lesson()
        self.assertEqual("matched", result["binding"]["state"])
        source = json.loads(self.conn.execute("SELECT source_json FROM smart_attendance_session_links").fetchone()[0])
        self.assertEqual("verified_course_alignment", source["course_match_evidence"])
        auto._validate_context_source(self.ctx, source)
        source["local_course_code"] = "different"
        source['local_course_codes'] = ['different']
        with self.assertRaises(HTTPException):
            auto._validate_context_source(self.ctx, source)

    async def test_ai_alias_is_only_candidate_then_actual_roster_is_verified(self):
        self.ctx.update(course_code="LOCAL-ALIAS", course_name="另一写法")
        self.ai.side_effect = lambda task, facts: {'status':'assisted','decision':facts['allowed_decisions'][1]}
        result = await self.bind_lesson()
        self.assertEqual("matched", result["binding"]["state"])
        self.assertEqual(1, self.ai.call_count)
        self.remote.pop("001")
        result = await workflow.match_lesson(self.user, 1, 2)
        self.assertEqual("stale", result["binding"]["state"])

    async def test_roster_changed_during_read_prevents_binding(self):
        self.patch(workflow.local, "_roster", Mock(side_effect=[[{"student_id_number": "001"}], [{"student_id_number": "001"}, {"student_id_number": "003"}]]))
        with self.assertRaisesRegex(HTTPException, "名单已变化"):
            await self.bind_lesson()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM smart_attendance_session_links").fetchone()[0])

    async def test_manual_push_receipt_and_new_revision_require_new_intent(self):
        binding = (await self.bind_lesson())["binding"]
        payload = {"idempotency_key": "teacher-click-1", "expected_binding_revision": binding["revision"]}
        first = workflow.enqueue_sync(self.conn, self.user, 1, "local", payload)
        self.conn.commit()
        repeated = workflow.enqueue_sync(self.conn, self.user, 1, "local", payload)
        self.assertEqual(first["plan"]["revision"], repeated["plan"]["revision"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM smart_attendance_workflow_requests").fetchone()[0])
        self.ctx["revision"] += 1
        with self.assertRaisesRegex(HTTPException, "旧请求标识"):
            workflow.enqueue_sync(self.conn, self.user, 1, "local", payload)
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("awaiting_push", result["state"])
        self.assertEqual([], self.writes)

    async def test_request_key_already_used_by_legacy_other_round_is_clean_conflict(self):
        binding = (await self.bind_lesson())["binding"]
        self.conn.execute("""INSERT INTO smart_attendance_auto_sync(local_round_id,owner_teacher_id,class_offering_id,session_id,
            external_account_key,credential_generation,source_json,slot_json,enabled,state,request_key,created_at,updated_at)
            VALUES('legacy-other',1,1,2,?,'g1',?,?,0,'confirmed','legacy-shared-key','now','now')""",
            (self.account, json.dumps(self.source), json.dumps(self.slot)))
        self.conn.commit()
        with self.assertRaises(HTTPException) as raised:
            workflow.enqueue_sync(self.conn, self.user, 1, "local", {"idempotency_key": "legacy-shared-key", "expected_binding_revision": binding["revision"]})
        self.assertEqual(409, raised.exception.status_code)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM smart_attendance_workflow_requests").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM smart_attendance_auto_sync WHERE local_round_id='local'").fetchone())

    async def test_remote_newer_preserves_attendance_and_imports_before_write(self):
        self.record["updateTime"] = "2035-01-01T00:00:00+08:00"
        self.ctx["students"][1].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.manual()
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("confirmed", result["state"])
        self.assertEqual([("001", "CHECKED")], self.writes)
        self.assertEqual("CHECKED", self.remote["002"])
        self.assertEqual(2, self.persist.call_count)

    async def test_evidence_refresh_time_does_not_make_local_newer(self):
        self.ctx["students"][1].update(status="LATE", updated_at_ms=9999999999999)
        targets, _ = workflow.comparison_targets(self.ctx, {"record": {"updateTime": "2035-01-01T00:00:00+08:00"}, "students": self.remote})
        self.assertEqual("CHECKED", targets[1]["target_status"])

    async def test_local_manual_newer_sends_verified_absence(self):
        self.record["updateTime"] = "2026-11-09T08:00:00+08:00"
        self.ctx["students"][1].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.manual()
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("confirmed", result["state"])
        self.assertIn(("002", "UNCHECKED"), self.writes)

    async def test_newer_explicit_correction_can_override_ordinary_leave(self):
        self.record["updateTime"] = "2026-11-09T08:00:00+08:00"
        self.remote["001"] = "SICK_LEAVE"
        self.ctx["students"][0].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.manual()
        await auto.process_auto_sync(1, "local")
        self.assertEqual("CHECKED", self.remote["001"])

    async def test_automatic_present_and_special_school_status_remain_protected(self):
        self.record["updateTime"] = "2026-11-09T08:00:00+08:00"
        self.remote["001"] = "SICK_LEAVE"
        self.manual()
        await auto.process_auto_sync(1, "local")
        self.assertEqual("SICK_LEAVE", self.remote["001"])
        self.ctx["revision"] += 1
        self.ctx["students"][0].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.remote["001"] = "STUDY_TOUR"
        self.manual()
        await auto.process_auto_sync(1, "local")
        self.assertEqual("STUDY_TOUR", self.remote["001"])

    async def test_leave_correction_does_not_override_changed_remote_baseline(self):
        self.record["updateTime"] = "2026-11-09T08:00:00+08:00"
        self.remote["001"] = "SICK_LEAVE"
        self.ctx["students"][0].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.manual()
        async def compare(*args):
            self.remote["001"] = "PERSONAL_LEAVE"
            return {"status": "unavailable", "decision": None}
        self.ai.side_effect = compare
        self.assertEqual("conflict", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual("PERSONAL_LEAVE", self.remote["001"])
        self.assertEqual([], self.writes)

    async def test_new_school_round_uses_all_six_local_states(self):
        statuses = ["PRESENT", "ABSENT", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE", "EARLY"]
        self.ctx["students"] = [{"student_id": i, "student_number": str(i), "status": status, "revision": 1, "manual_confirmed": 1, "decided_at_ms": 2000000000000} for i, status in enumerate(statuses, 1)]
        self.remote = {str(i): "UNCHECKED" for i in range(1, 7)}
        self.record = None
        self.manual()
        intent = json.loads(self.plan()["intent_json"])
        intent.update(allow_create=True, location={"latitude": 20., "longitude": 110., "captured_at_ms": int(time.time()*1000)})
        self.conn.execute("UPDATE smart_attendance_auto_sync SET remote_checkin_id=NULL,intent_json=?", (json.dumps(intent),))
        self.conn.commit()
        async def start(*args):
            self.record = {"id": "created", "teacherScheduleId": "schedule", "isStop": False, "updateTime": "2035-01-01T00:00:00+08:00"}
            return self.record
        async def stop(*args):
            self.record["isStop"] = True
        self.patch(mini, "read_history", AsyncMock(side_effect=lambda *args: [copy.deepcopy(self.record)] if self.record else []))
        self.patch(mini, "start_checkin", AsyncMock(side_effect=start))
        self.patch(mini, "stop_checkin", AsyncMock(side_effect=stop))
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("confirmed", result["state"])
        self.assertEqual([ledger.LOCAL_STATUS_MAP[s] for s in statuses], [self.remote[str(i)] for i in range(1, 7)])

    async def test_comparison_time_survives_batches_and_own_remote_writes(self):
        self.record["updateTime"] = "2026-11-09T08:00:00+08:00"
        self.ctx["students"][1].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.manual()
        self.patch(auto, "BATCH_SIZE", 1)
        self.assertEqual("running", (await auto.process_auto_sync(1, "local"))["state"])
        self.record["updateTime"] = "2035-01-01T00:00:00+08:00"
        self.assertEqual("confirmed", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual("UNCHECKED", self.remote["002"])
        self.assertEqual("2026-11-09T08:00:00+08:00", json.loads(self.plan()["intent_json"])["comparison_record"]["updateTime"])

    async def test_evidence_revision_does_not_cancel_authorized_decisions(self):
        self.record["updateTime"] = "2026-11-09T08:00:00+08:00"
        self.ctx["students"][1].update(manual_confirmed=1, decided_at_ms=2000000000000)
        self.manual()
        self.patch(auto, "BATCH_SIZE", 1)
        self.assertEqual("running", (await auto.process_auto_sync(1, "local"))["state"])
        for student in self.ctx["students"]:
            student["revision"] += 1
            student["updated_at_ms"] = 9999999999999
        self.assertEqual("confirmed", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual("UNCHECKED", self.remote["002"])

    async def test_expired_write_session_recovers_reads_and_continues(self):
        self.manual()
        original = self.change_student
        attempts = 0
        async def rejected(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sessions.SmartClassroomSessionError("session_refreshed", "synthetic expired")
            return await original(*args)
        self.patch(mini, "change_student", AsyncMock(side_effect=rejected))
        self.assertEqual("outcome_unknown", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual("partial", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual(2, attempts)
        self.assertEqual([("001", "CHECKED")], self.writes)

    async def test_auth_recovery_permission_cannot_replay_later_network_timeout(self):
        self.ctx["students"][1].update(status="PRESENT")
        self.remote["002"] = "UNCHECKED"
        self.manual()
        original = self.change_student
        attempts = []
        async def wire(client, remote_id, number, status):
            attempts.append(number)
            if attempts == ["001"]:
                raise sessions.SmartClassroomSessionError("session_refreshed", "synthetic expired")
            if number == "002":
                raise TimeoutError("ambiguous real network timeout")
            return await original(client, remote_id, number, status)
        self.patch(mini, "change_student", AsyncMock(side_effect=wire))
        await auto.process_auto_sync(1, "local")
        await auto.process_auto_sync(1, "local")
        self.assertEqual("outcome_unknown", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual(["001", "001", "002"], attempts)
        self.assertNotIn("auth_rejected_operation_id", json.loads(self.plan()["intent_json"]))

    async def test_expired_stop_recovers_by_reading_before_retry(self):
        self.record["isStop"] = False
        self.manual()
        calls = 0
        async def stop(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sessions.SmartClassroomSessionError("session_refreshed", "synthetic expired")
            self.record["isStop"] = True
        self.patch(mini, "stop_checkin", AsyncMock(side_effect=stop))
        self.assertEqual("outcome_unknown", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual("partial", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual(2, calls)

    async def test_expired_start_recovers_without_ambiguous_duplicate_creation(self):
        self.record = None
        self.manual()
        intent = json.loads(self.plan()["intent_json"])
        intent.update(allow_create=True, location={"latitude": 20., "longitude": 110., "captured_at_ms": int(time.time()*1000)})
        self.conn.execute("UPDATE smart_attendance_auto_sync SET remote_checkin_id=NULL,intent_json=?", (json.dumps(intent),))
        self.conn.commit()
        calls = 0
        async def start(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sessions.SmartClassroomSessionError("session_refreshed", "synthetic expired")
            self.record = {"id": "created", "teacherScheduleId": "schedule", "isStop": True}
            return self.record
        self.patch(mini, "read_history", AsyncMock(side_effect=lambda *args: [copy.deepcopy(self.record)] if self.record else []))
        self.patch(mini, "start_checkin", AsyncMock(side_effect=start))
        self.assertEqual("outcome_unknown", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual("partial", (await auto.process_auto_sync(1, "local"))["state"])
        self.assertEqual(2, calls)

    async def test_official_edit_during_ai_comparison_is_not_overwritten(self):
        self.manual()
        async def compare(*args):
            self.remote["001"] = "LATE_OR_EARLY"
            return {"status": "assisted", "decision": "unknown"}
        self.ai.side_effect = compare
        result = await auto.process_auto_sync(1, "local")
        self.assertEqual("conflict", result["state"])
        self.assertEqual([], self.writes)
