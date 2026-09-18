"""Orchestration integration tests: real SQLite + real adapter/core, mocked HTTP."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from classroom_app import config, database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.db.schema import init_database
from classroom_app.db.schema_academic_schedule_predictions import ensure_academic_schedule_prediction_schema
from classroom_app.services import academic_schedule_prediction_service as core
from classroom_app.services import academic_schedule_sync_service as sync
from classroom_app.services.academic_calendar_sync_service import CalendarAlignment
from classroom_app.services.semester_identity_service import SemesterIdentity


class SchoolTransport:
    """Synthetic school contract. No credential or real personal data is used."""

    def __init__(self, *, first_calendar_status=200, calendar_year="2026-2027", class_term="3", on_call=None):
        self.calls = []
        self.first_calendar_status = first_calendar_status
        self.calendar_year = calendar_year
        self.class_term = class_term
        self.on_call = on_call

    def __call__(self, request):
        self.calls.append(request)
        if self.on_call:
            response = self.on_call(request)
            if response is not None:
                return response
        path = request.url.path
        if path.endswith("cxTtksqIndex.html"):
            fields = {"xnm": "2026", "xqm": "3", "pkxnm": "2026", "pkxqm": "3"}
            page = '<input id="xnm" value=""/><div id="searchForm">' + ''.join(
                f'<input type="hidden" name="{name}" id="{name}" value="{value}"/>' for name, value in fields.items()
            ) + '</div><input id="xqm" value=""/>'
            return httpx.Response(200, text=page)
        if path.endswith("xlgl_cxXlIndex.html") or path.endswith("index_initMenu.html"):
            if path.endswith("xlgl_cxXlIndex.html") and self.first_calendar_status != 200:
                return httpx.Response(self.first_calendar_status)
            return httpx.Response(200, text=f'<h2>{self.calendar_year}学年第1学期 (2026-08-31至2027-01-10)</h2>')
        form = {key: values[0] for key, values in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        if path.endswith("cxTtksqList.html"):
            rows = [{
                "jxb_id": "SYNTHETIC-CLASS", "jxbmc": "示例教学班", "jxbzc": "示例行政班",
                "kch": "SYNTHETIC-COURSE", "kch_id": "SYNTHETIC-INTERNAL", "kcmc": "示例课程",
                "sksj": "星期一第2-3节{1-2周}", "jxdd": "示例教室A",
                "xnm": form["xnm"], "xqm": self.class_term, "xnmmc": "2026-2027", "xqmmc": "1",
            }]
        elif path.endswith("cxTtksqjgList.html"):
            rows = []
        else:
            raise AssertionError(f"Unexpected school endpoint: {path}")
        return httpx.Response(200, json={"items": rows, "currentPage": int(form["queryModel.currentPage"]),
                                        "totalPage": 1 if rows else 0, "totalResult": len(rows)})


class AcademicScheduleSyncTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.original_engine = config.DB_ENGINE
        cls.original_path = config.DB_PATH
        cls.original_database_path = database.DB_PATH
        config.DB_ENGINE = "sqlite"
        cls.seed_path = Path(cls.directory.name) / "seed.db"
        config.DB_PATH = cls.seed_path
        database.DB_PATH = cls.seed_path
        init_database()
        with database.get_db_connection() as conn:
            ensure_academic_schedule_prediction_schema(conn)
            cls.teacher_id = execute_insert_returning_id(
                conn, "INSERT INTO teachers (name,email,hashed_password) VALUES (?,?,?)",
                ("示例教师一", "schedule-one@example.test", "unused-test-value"),
            )
            cls.other_teacher_id = execute_insert_returning_id(
                conn, "INSERT INTO teachers (name,email,hashed_password) VALUES (?,?,?)",
                ("示例教师二", "schedule-two@example.test", "unused-test-value"),
            )
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        config.DB_ENGINE = cls.original_engine
        config.DB_PATH = cls.original_path
        database.DB_PATH = cls.original_database_path
        cls.directory.cleanup()

    def setUp(self):
        self.db_path = Path(self.directory.name) / f"{self._testMethodName}.db"
        with closing(sqlite3.connect(self.seed_path)) as source, closing(sqlite3.connect(self.db_path)) as destination:
            source.backup(destination)
        config.DB_PATH = self.db_path
        database.DB_PATH = self.db_path
        self.school = SchoolTransport()
        self.auth_calls = 0
        self.auth_exception = None

        @asynccontextmanager
        async def authenticated(_credential):
            self.auth_calls += 1
            if self.auth_exception:
                raise self.auth_exception
            async with httpx.AsyncClient(base_url="https://school.test", transport=httpx.MockTransport(self.school)) as client:
                yield client, SimpleNamespace(school_code="gxufl"), {"status": "verified"}

        self.credential_patch = patch.object(sync, "load_teacher_academic_access_method", return_value={"school_code": "gxufl"})
        self.auth_patch = patch.object(sync, "open_authenticated_academic_client", side_effect=authenticated)
        self.credential_patch.start()
        self.auth_patch.start()
        self.addCleanup(self.credential_patch.stop)
        self.addCleanup(self.auth_patch.stop)

    def semester(self, *, owner=None, name="2026-2027第一学期", start="2026-08-31", end="2027-01-10", school_code="gxufl"):
        with database.get_db_connection() as conn:
            semester_id = execute_insert_returning_id(
                conn,
                "INSERT INTO academic_semesters (teacher_id,school_code,name,start_date,end_date,week_count) VALUES (?,?,?,?,?,?)",
                (owner or self.teacher_id, school_code, name, start, end, 19),
            )
            conn.commit()
        return semester_id

    def state(self):
        with database.get_db_connection() as conn:
            row = conn.execute("SELECT * FROM teacher_academic_schedule_sync_state WHERE teacher_id=?", (self.teacher_id,)).fetchone()
            return dict(row) if row else None

    def snapshot_row(self, semester_id):
        with database.get_db_connection() as conn:
            row = conn.execute("SELECT * FROM teacher_academic_schedule_snapshots WHERE teacher_id=? AND semester_id=?",
                               (self.teacher_id, semester_id)).fetchone()
            return dict(row) if row else None

    async def successful_existing_sync(self):
        semester_id = self.semester()
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "success", result)
        return semester_id, self.snapshot_row(semester_id)

    async def test_explicit_term_uses_same_scope_for_adapter_and_published_core(self):
        semester_id = self.semester()
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1", semester_id=semester_id)
        self.assertEqual((result["status"], result["year"], result["term"], result["semester_id"]), ("success", "2026-2027", "1", semester_id))
        self.assertEqual(result["official_count"], 2)
        self.assertEqual(result["predicted_count"], 0)
        self.assertEqual(self.auth_calls, 1)
        self.assertEqual(self.state()["lease_token"], "")
        self.assertEqual(self.state()["status"], "ready")
        self.assertFalse(any(call.method == "GET" for call in self.school.calls))
        stored = json.loads(self.snapshot_row(semester_id)["snapshot_json"])
        self.assertEqual({row["date"] for row in stored["official"]}, {"2026-08-31", "2026-09-07"})
        self.assertEqual(stored["official"][0]["weekday"], 1)
        for call in self.school.calls:
            form = parse_qs(call.content.decode(), keep_blank_values=True)
            self.assertEqual((form["xnm"], form["xqm"]), (["2026"], ["3"]))

    async def test_current_discovery_creates_real_semester_and_all_calendar_days(self):
        result = await sync.sync_teacher_academic_schedule(self.teacher_id)
        self.assertEqual(result["status"], "success", result)
        with database.get_db_connection() as conn:
            semester = dict(conn.execute("SELECT * FROM academic_semesters WHERE id=?", (result["semester_id"],)).fetchone())
            days = [dict(row) for row in conn.execute("SELECT * FROM academic_semester_calendar_days WHERE semester_id=? ORDER BY date", (result["semester_id"],))]
        self.assertEqual((semester["name"], semester["start_date"], semester["end_date"], semester["week_count"]),
                         ("2026-2027第一学期", "2026-08-31", "2027-01-10", 19))
        self.assertEqual(len(days), 133)
        self.assertEqual((days[0]["date"], days[0]["week_index"], days[0]["weekday"]), ("2026-08-31", 1, 0))
        self.assertEqual((days[-1]["date"], days[-1]["week_index"]), ("2027-01-10", 19))
        self.assertFalse(any(day["day_type"] in {"holiday", "workday"} for day in days))
        self.assertTrue(self.snapshot_row(result["semester_id"]))
        self.assertTrue(any(call.url.path.endswith("cxTtksqIndex.html") for call in self.school.calls))

    async def test_captured_detail_field_contract_flows_from_adapter_into_core(self):
        semester_id = self.semester()
        request_row = {
            "ttk_id": "SYNTHETIC-REQUEST", "ttk_lsh": "SYNTHETIC-SERIAL", "jxb_id": "SYNTHETIC-CLASS",
            "jxbmc": "示例教学班", "jxbzc": "示例行政班", "kch": "SYNTHETIC-COURSE", "kcmc": "示例课程",
            "xnm": "2026", "xqm": "3", "shzt": "1", "tklxdm": "01", "tkyy": "示例调整原因",
        }
        detail = {
            "ttkxx_id": "SYNTHETIC-DETAIL", "ttk_lsh": "SYNTHETIC-SERIAL", "jxb_id": "SYNTHETIC-CLASS",
            "xnm": "2026", "xqm": "3", "tklxdm": "01", "tkqrq": "2026-08-31", "yzcd": "1周",
            "yxqj": "一", "yjc": "第2-3节", "ycd_id": "SYNTHETIC-ROOM-A", "ycdmc": "示例教室A",
            "tkhrq": "2026-09-08", "xzcd": "2周", "xxqj": "二", "xjc": "第4-5节",
            "xcd_id": "SYNTHETIC-ROOM-B", "xcdmc": "示例教室B", "jc": "110000000000", "zcd": "100000000000",
        }

        def application_response(request):
            if request.url.path.endswith("cxTtksqjgList.html"):
                return httpx.Response(200, json={"items": [request_row], "currentPage": 1, "totalPage": 1, "totalResult": 1})
            if request.url.path.endswith("cxShxxView.html"):
                return httpx.Response(200, text='<input id="ttk_id" value="SYNTHETIC-REQUEST"/>'
                                     '<input id="tklxdm_sub" value="01"/><script>var modelList = '
                                     + json.dumps([detail], ensure_ascii=False) + ';</script>')
            return None

        self.school.on_call = application_response
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, semester_id=semester_id)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["request_count"], 1)
        record = json.loads(self.snapshot_row(semester_id)["snapshot_json"])["requests"][0]
        self.assertEqual((record["request_id"], record["serial"], record["status"], record["raw_status"], record["kind"]),
                         ("SYNTHETIC-REQUEST", "SYNTHETIC-SERIAL", "pending", "1", "move"))
        self.assertEqual(record["details"][0]["original"]["sections"], [2, 3])
        self.assertEqual(record["details"][0]["proposed"]["date"], "2026-09-08")
        self.assertEqual(record["details"][0]["proposed"]["weekday"], 2)

    async def test_new_explicit_term_initializes_without_default_discovery(self):
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "success", result)
        self.assertFalse(any(call.url.path.endswith("cxTtksqIndex.html") for call in self.school.calls))
        calendar_query = next(call for call in self.school.calls if "xlgl_" in call.url.path)
        self.assertEqual((calendar_query.url.params["xnm"], calendar_query.url.params["xqm"]), ("2026", "3"))

    async def test_explicit_id_mismatched_term_or_school_fails_before_network_and_lease(self):
        local = self.semester()
        wrong_school = self.semester(owner=self.other_teacher_id, school_code="other-school")
        for params in [dict(semester_id=local, year="2025-2026", term="1"), dict(semester_id=wrong_school),
                       dict(year="2026-2027", term="12"), dict(year="2026-2027", term=""), dict(semester_id=999999)]:
            with self.subTest(params=params):
                result = await sync.sync_teacher_academic_schedule(self.teacher_id, **params)
                self.assertEqual(result["status"], "invalid_semester", result)
        self.assertEqual(self.auth_calls, 0)
        self.assertIsNone(self.state())

    async def test_wrong_remote_term_keeps_previous_complete_snapshot_and_releases_lease(self):
        semester_id, original = await self.successful_existing_sync()
        self.school.class_term = "12"
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, semester_id=semester_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("学年学期", result["message"])
        self.assertEqual(self.snapshot_row(semester_id), original)
        self.assertEqual(self.state()["lease_token"], "")
        self.assertEqual(self.state()["status"], "failed")

    async def test_http_fetch_failure_keeps_previous_complete_snapshot(self):
        semester_id, original = await self.successful_existing_sync()
        self.school.on_call = lambda request: httpx.Response(503) if request.url.path.endswith("cxTtksqList.html") else None
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, semester_id=semester_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.snapshot_row(semester_id), original)
        self.assertEqual(self.state()["lease_token"], "")

    async def test_busy_lease_prevents_network_and_retains_other_workers_token(self):
        self.semester()
        with database.get_db_connection() as conn:
            lease = core.claim_schedule_sync(conn, self.teacher_id)
            conn.commit()
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "busy")
        self.assertEqual(self.auth_calls, 0)
        self.assertEqual(self.state()["lease_token"], lease["token"])

    async def test_unexpected_login_exception_releases_owned_lease(self):
        semester_id, original = await self.successful_existing_sync()
        self.auth_exception = RuntimeError("synthetic context error")
        with self.assertLogs(sync.logger, level="ERROR"):
            result = await sync.sync_teacher_academic_schedule(self.teacher_id, semester_id=semester_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.snapshot_row(semester_id), original)
        self.assertEqual(self.state()["lease_token"], "")
        self.assertEqual(self.state()["status"], "failed")

    async def test_old_token_cannot_publish_or_release_a_newer_workers_lease(self):
        semester_id, original = await self.successful_existing_sync()
        newer_token = "SYNTHETIC-NEW-WORKER"

        def takeover(request):
            if request.url.path.endswith("cxTtksqjgList.html"):
                with database.get_db_connection() as conn:
                    conn.execute("UPDATE teacher_academic_schedule_sync_state SET lease_token=?,lease_expires_at=? WHERE teacher_id=?",
                                 (newer_token, (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), self.teacher_id))
                    conn.commit()
            return None

        self.school.on_call = takeover
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, semester_id=semester_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("同步锁", result["message"])
        self.assertEqual(self.snapshot_row(semester_id), original)
        self.assertEqual(self.state()["lease_token"], newer_token)

    async def test_fetch_failure_does_not_create_new_semester(self):
        self.school.class_term = "12"
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "failed")
        with database.get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM academic_semesters WHERE name=?", ("2026-2027第一学期",)).fetchone()[0], 0)

    async def test_publication_failure_rolls_back_real_semester_and_day_initialization(self):
        with patch.object(core, "reconcile_and_publish_snapshot", side_effect=ValueError("synthetic invalid snapshot")), \
             patch.object(sync, "_initialize_semester", wraps=sync._initialize_semester) as initialize:
            result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(initialize.call_count, 1)
        with database.get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM academic_semesters WHERE name=?", ("2026-2027第一学期",)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM academic_semester_calendar_days WHERE teacher_id=?", (self.teacher_id,)).fetchone()[0], 0)
        self.assertEqual(self.state()["lease_token"], "")

    async def test_calendar_http_denial_falls_back_to_official_homepage(self):
        self.school.first_calendar_status = 403
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "success", result)
        self.assertTrue(any(call.url.path.endswith("index_initMenu.html") for call in self.school.calls))

    async def test_wrong_calendar_term_cannot_initialize_selected_term(self):
        self.school.calendar_year = "2025-2026"
        result = await sync.sync_teacher_academic_schedule(self.teacher_id, year="2026-2027", term="1")
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("官方起止日期", result["message"])
        self.assertFalse(any(call.method == "POST" for call in self.school.calls))

    def test_same_school_creation_reuses_existing_semester_without_duplicate_calendar(self):
        alignment = CalendarAlignment(name="2026-2027第一学期", start_date="2026-08-31", end_date="2027-01-10", week_count=19)
        with database.get_db_connection() as conn:
            first = sync._initialize_semester(conn, self.teacher_id, SemesterIdentity(2026, 1), alignment, [])
            conn.commit()
        with database.get_db_connection() as conn:
            second = sync._initialize_semester(conn, self.other_teacher_id, SemesterIdentity(2026, 1), alignment, [])
            conn.commit()
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM academic_semester_calendar_days WHERE semester_id=?", (first["id"],)).fetchone()[0], 133)

    def test_concurrent_semester_with_different_dates_is_not_silently_reused(self):
        self.semester(owner=self.other_teacher_id, start="2026-09-07")
        alignment = CalendarAlignment(name="2026-2027第一学期", start_date="2026-08-31", end_date="2027-01-10", week_count=19)
        with database.get_db_connection() as conn:
            with self.assertRaisesRegex(ValueError, "不同的校历日期"):
                sync._initialize_semester(conn, self.teacher_id, SemesterIdentity(2026, 1), alignment, [])
            conn.rollback()


if __name__ == "__main__":
    unittest.main()
