"""Free-room contracts using synthetic SQLite and httpx transports only."""
import asyncio
import sqlite3
import unittest
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from classroom_app.db import schema_schedule_availability
from classroom_app.services import academic_classroom_sync_service as rooms
from classroom_app.services import academic_availability_sync_service as availability


class FreeRoomTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        schema_schedule_availability.reset_schema_ready_for_tests()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY,school_code TEXT,school_name TEXT,college TEXT,department TEXT)")
        self.conn.execute("INSERT INTO teachers VALUES(1,'gxufl','Synthetic','',''),(2,'other','Other','','')")
        self.conn.execute("CREATE TABLE academic_semesters(id INTEGER,teacher_id INTEGER)")
        columns = ["id INTEGER PRIMARY KEY", "teacher_id INTEGER", "school_code TEXT", "source TEXT DEFAULT 'gxufl_jwxt'",
                   "sync_status TEXT DEFAULT 'active'", "synced_at TEXT DEFAULT ''"]
        numeric = {"seat_count", "scheduling_seat_count", "exam_seat_count", "is_schedulable", "is_borrowable", "is_exam_schedulable", "conflict_ignored"}
        for name in rooms.AcademicTeachingPlace.__dataclass_fields__:
            columns.append(name + (" INTEGER DEFAULT 0" if name in numeric else " TEXT DEFAULT ''"))
        self.conn.execute("CREATE TABLE teacher_academic_teaching_places(" + ",".join(columns) + ")")
        for row in [(1, 1, "gxufl", "B310", "3"), (2, 2, "other", "OTHER", "1")]:
            self.conn.execute("""INSERT INTO teacher_academic_teaching_places
                (id,teacher_id,school_code,place_id,place_key,room_code,room_name,room_full_name,campus_id,room_type_id)
                VALUES(?,?,?,?,?,'B310','知新楼\tB　310','知新楼\tB　310',?,'03')""", (*row[:4], row[3], row[4]))

        @contextmanager
        def db():
            yield self.conn

        for target in (rooms, availability):
            p = patch.object(target, "get_db_connection", db)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(rooms, "load_teacher_academic_access_method", return_value={"school_code": "gxufl", "synthetic": True})
        p.start(); self.addCleanup(p.stop)
        self.calls = []

    def upstream(self, responder):
        def handle(request):
            if request.method == "POST":
                form = {key: values[0] for key, values in parse_qs(request.content.decode(), keep_blank_values=True).items()}
                self.calls.append(form)
                payload = responder(form)
                return payload if isinstance(payload, httpx.Response) else httpx.Response(200, json=payload)
            return httpx.Response(200, text="<html></html>")

        @asynccontextmanager
        async def opened(_credential):
            async with httpx.AsyncClient(base_url="https://academic.synthetic.invalid", transport=httpx.MockTransport(handle)) as client:
                yield client, SimpleNamespace(base_url=client.base_url, school_code="gxufl"), {}
        return patch.object(rooms, "open_authenticated_academic_client", opened)

    def filters(self, **extra):
        return {"xnm": "2026", "xqm": "3", "weeks": [6], "weekday": [4], "sections": [2, 3],
                "recommendations": False, **extra}

    async def query(self, **extra):
        return await rooms.query_free_classrooms_from_academic_system(1, self.filters(**extra))

    async def editor(self, **extra):
        return await availability.search_free_rooms(1, year="2026-2027", term="1", week=6, weekday=4,
                                                     sections=[2, 3], room_id="B310", **extra)

    def test_portable_compact_search_and_school_scope(self):
        for text in ("B310", "知新楼 B310", "知新楼\tB　310"):
            self.assertEqual(rooms.count_teacher_teaching_places(self.conn, 1, search=text), 1)
            self.assertEqual([row["place_id"] for row in rooms.load_teacher_teaching_places(self.conn, 1, search=text)], ["B310"])
        self.assertIsNone(rooms.load_teacher_teaching_place_by_key(self.conn, 1, place_id="OTHER"))

    async def test_explicit_all_type_duplicate_slots_and_second_page(self):
        with self.upstream(lambda _: {"items": [{"cd_id": "R3", "cdmc": "第三间"}], "totalResult": 3}):
            result = await self.query(cdlb_id="", weeks=[6, 6], sections=[2, 3, 2], page=2, page_size=2)
        self.assertEqual(result["status"], "success")
        self.assertEqual((result["total_page"], result["has_more"]), (2, False))
        self.assertEqual((self.calls[0]["cdlb_id"], self.calls[0]["zcd"], self.calls[0]["jcd"]), ("", "32", "6"))
        self.assertEqual(self.calls[0]["queryModel.currentPage"], "2")
        self.assertEqual(rooms._free_room_form(self.filters(), {"xnm": "2026", "xqm": "3"})["cdlb_id"], "05")

    async def test_invalid_slot_is_not_silently_subsetted_or_queried(self):
        for invalid in ({"sections": [2, 21]}, {"weeks": [-6]}, {"weekday": [0, 4]}, {"sections": "2-bad-3"},
                        {"jcd": -6}, {"sections": [True, 2]}, {"jcd": 2, "sections": [2, 3]}):
            with self.subTest(invalid=invalid):
                self.assertEqual((await self.query(**invalid))["status"], "invalid")
        self.assertFalse(self.calls)

    async def test_other_teachers_semester_is_invalid_not_current_term_fallback(self):
        result = await rooms.query_free_classrooms_from_academic_system(1, {"semester_id": 99, "weeks": [6], "weekday": [4], "sections": [2, 3]})
        self.assertEqual(result["status"], "invalid")

    async def test_malformed_error_or_html_never_success_empty(self):
        cases = [{"error": "upstream denied"}, {"items": []}, {"items": [], "success": False}, {"items": [{}], "totalResult": 1},
                 {"items": [], "totalResult": 3}, {"items": [], "totalResult": "invalid"},
                 httpx.Response(200, text="<html>login</html>")]
        for payload in cases:
            with self.subTest(payload=repr(payload)), self.upstream(lambda _, p=payload: p):
                result = await self.query()
                self.assertEqual(result["status"], "academic_unavailable")
                self.assertEqual(result["items"], [])

    async def test_empty_is_valid_and_login_redirect_is_not(self):
        with self.upstream(lambda _: {"items": [], "totalResult": 0, "totalPage": 0}):
            self.assertEqual((await self.query())["status"], "success")
        with self.upstream(lambda _: httpx.Response(302, headers={"location": "/xtgl/login_slogin.html"})), patch.object(rooms.asyncio, "sleep", return_value=None):
            self.assertEqual((await self.query())["status"], "academic_session_expired")

    async def test_filtered_page_absence_checks_exact_campus_room_and_preserves_all_sections(self):
        def reply(form):
            if form.get("cd_id"):
                self.assertEqual((form["cd_id"], form["xqh_id"], form["cdlb_id"], form["cdmc"], form["lh"], form["jcd"]),
                                 ("B310", "3", "", "", "", "6"))
                return {"items": [{"cd_id": "B310", "cdmc": "B310"}], "totalResult": 1}
            return {"items": [{"cd_id": "OTHER"}], "totalResult": 500, "totalPage": 13}
        with self.upstream(reply):
            result = await self.editor(keyword="A101", building="A", room_type="05", page=2)
        self.assertEqual(result["room_status"], "free")
        self.assertTrue(result["has_more"])
        self.assertEqual(self.calls[0]["queryModel.currentPage"], "2")
        self.assertEqual(self.calls[0]["xqh_id"], "3")
        check = self.conn.execute("SELECT * FROM academic_room_slot_checks").fetchone()
        self.assertEqual((check["school_code"], check["academic_year"], check["academic_term"], check["sections_json"]),
                         ("gxufl", "2026-2027", "1", "[2, 3]"))

    async def test_only_exact_valid_empty_establishes_busy(self):
        with self.upstream(lambda f: {"items": [] if f.get("cd_id") else [{"cd_id": "X"}], "totalResult": 0 if f.get("cd_id") else 1}):
            result = await self.editor()
        self.assertEqual(result["room_status"], "busy")
        self.assertEqual(self.conn.execute("SELECT status FROM academic_room_slot_checks").fetchone()[0], "busy")

    async def test_target_failure_keeps_unknown_without_cache(self):
        with self.upstream(lambda f: {"error": "denied"} if f.get("cd_id") else {"items": [{"cd_id": "X"}], "totalResult": 1}):
            result = await self.editor()
        self.assertEqual((result["status"], result["room_status"]), ("success", "unknown"))
        self.assertTrue(result["room_status_message"])
        self.assertFalse(self.conn.execute("SELECT name FROM sqlite_master WHERE name='academic_room_slot_checks'").fetchone())

    async def test_first_page_match_does_not_make_second_request(self):
        with self.upstream(lambda _: {"items": [{"cd_id": "B310", "cdmc": "B310"}], "totalResult": 1}):
            self.assertEqual((await self.editor())["room_status"], "free")
        self.assertEqual(len(self.calls), 1)

    async def test_outside_scope_room_is_unknown_and_never_cached(self):
        with self.upstream(lambda _: {"items": [{"cd_id": "OTHER"}], "totalResult": 1}):
            result = await availability.search_free_rooms(1, year="2026-2027", term="1", week=6, weekday=4,
                                                         sections=[2, 3], room_id="OTHER")
        self.assertEqual(result["room_status"], "unknown")
        self.assertEqual(len(self.calls), 1)

    def test_missing_names_and_distinct_ids_cannot_match(self):
        self.assertFalse(availability._room_matches({"place_id": "A101"}, "B310", ""))
        self.assertFalse(availability._room_matches({"place_id": "A101", "room_name": "B310"}, "B310", "B310"))

    async def test_options_invalid_semester_has_same_invalid_contract(self):
        result = await rooms.load_free_classroom_options_from_academic_system(1, semester_id=99)
        self.assertEqual(result["status"], "invalid")

    async def test_query_rejects_invalid_pagination_without_network(self):
        for value in (0, -1, "1.5", "n/a", True):
            self.assertEqual((await self.query(page=value))["status"], "invalid")

    async def test_manage_routes_map_invalid_to_400_and_upstream_to_502(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        from classroom_app.routers.manage_parts import classes_courses_classes as routes

        async def receive():
            return {"type": "http.request", "body": b'{}'}
        request = Request({"type": "http", "query_string": b'semester_id=99'}, receive)
        with patch.object(routes, "load_free_classroom_options_from_academic_system", return_value={"status": "invalid", "message": "invalid term"}):
            with self.assertRaises(HTTPException) as raised:
                await routes.api_load_free_classroom_options(request, user={"id": 1})
            self.assertEqual(raised.exception.status_code, 400)
        for status, code in (("invalid", 400), ("academic_unavailable", 502)):
            with patch.object(routes, "query_free_classrooms_from_academic_system", return_value={"status": status, "message": status}):
                with self.assertRaises(HTTPException) as raised:
                    await routes.api_query_free_classrooms(request, user={"id": 1})
                self.assertEqual(raised.exception.status_code, code)

    async def test_editor_rejects_incomplete_out_of_range_periods(self):
        from fastapi import HTTPException
        from classroom_app.routers.manage_parts import schedule_editor as routes
        for sections in ("2,21", "0,2", "-1,2", "2,bad"):
            with self.assertRaises(HTTPException) as raised:
                await routes.api_schedule_editor_free_rooms(year="2026-2027", term="1", week=6, weekday=4,
                                                            sections=sections, user={"id": 1})
            self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
