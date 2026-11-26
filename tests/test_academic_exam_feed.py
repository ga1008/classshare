import asyncio
import sqlite3
import unittest
from contextlib import asynccontextmanager, nullcontext
from datetime import date
from urllib.parse import parse_qs
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from classroom_app.services import academic_course_exam_sync_service as exams
from classroom_app.services import academic_invigilation_sync_service as duties
from classroom_app.services import academic_exam_feed as feed
from classroom_app.services import academic_exam_audience as audience


SEMESTER = {"id": 7, "name": "2026-2027第一学期"}
SOURCES = (
    (exams, exams._fetch_teacher_course_exams, exams.sync_current_teacher_course_exams_from_academic_system, "_persist_course_exams"),
    (duties, duties._fetch_teacher_invigilations, duties.sync_current_teacher_invigilations_from_academic_system, "_persist_invigilations"),
)


def grid(rows, page=1, pages=1, count=None):
    return {"currentPage": page, "totalPage": pages, "totalResult": len(rows) if count is None else count, "items": rows}


def row(key):
    return {"pkvalue": key, "xnm": "2026", "xqm": "3", "kcmc": "CODE/合成课程", "kssj": "2026-12-5（周六）9:00–11:00"}


class AcademicExamFeedTests(unittest.IsolatedAsyncioTestCase):
    def client(self, responses, forms):
        def respond(request):
            if "doType=query" not in request.url.query.decode():
                return httpx.Response(200, json=[])
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            forms.append(form)
            self.assertEqual((form["xnm"], form["xqm"]), (["2026"], ["3"]))
            self.assertEqual(form["ksrq"], [""])
            response = responses[int(form["queryModel.currentPage"][0]) - 1]
            return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
        return httpx.AsyncClient(base_url="https://school.invalid", transport=httpx.MockTransport(respond))

    async def test_full_current_term_pagination_then_explicit_empty(self):
        for module, fetch, _, _ in SOURCES:
            with self.subTest(source=module.__name__):
                forms = []
                pages = [grid([row("A")], pages=2, count=2), grid([row("B")], page=2, pages=2, count=2)]
                async with self.client(pages, forms) as client:
                    result, sources, terms = await fetch(client, SEMESTER)
                self.assertEqual([form["queryModel.currentPage"] for form in forms], [["1"], ["2"]])
                self.assertEqual([item.starts_at for item in result], ["2026-12-05T09:00"] * 2)
                self.assertEqual(terms, [{"xnm": "2026", "xqm": "3"}])
                self.assertEqual(sources[-1]["total_result"], 2)
                async with self.client([grid([], pages=0)], []) as client:
                    result, _, terms = await fetch(client, SEMESTER)
                self.assertEqual((result, terms), ([], [{"xnm": "2026", "xqm": "3"}]))

    async def test_failed_and_truncated_queries_never_enter_persistence(self):
        first = grid([row("A")], pages=2, count=2)
        cases = {
            "http": [httpx.Response(503, json=grid([]))],
            "login": [httpx.Response(200, text="<html>登录</html>")],
            "unconfirmed empty": [{"items": []}],
            "error with empty grid": [dict(grid([]), success=False)],
            "wrong semester": [grid([dict(row("A"), xqm="12")])],
            "wrong uppercase semester": [grid([{k.upper(): v for k, v in dict(row("A"), xqm="12").items()}])],
            "broken row": [grid([None])],
            "unexpected row": [grid([{"message": "身份过期"}])],
            "invalid metadata": [dict(grid([]), totalResult=-1)],
            "missing page": [first, grid([], page=2, pages=2, count=2)],
            "repeated key": [first, grid([row("A")], page=2, pages=2, count=2)],
            "wrong page": [first, grid([row("B")], page=1, pages=2, count=2)],
            "changed count": [first, grid([row("B")], page=2, pages=2, count=3)],
            "short total": [grid([row("A")], count=2)],
            "page limit": [grid([row("A")], pages=101, count=101)],
        }
        for module, _, sync, persist_name in SOURCES:
            for name, pages in cases.items():
                with self.subTest(source=module.__name__, failure=name):
                    @asynccontextmanager
                    async def authenticated(_credential):
                        async with self.client(pages, []) as client:
                            yield client, None, {}

                    with (
                        patch.object(module, "get_db_connection", return_value=nullcontext(None)),
                        patch.object(module, "load_teacher_academic_access_method", return_value={"id": 1}),
                        patch.object(module, "load_current_exam_semester", return_value=SEMESTER),
                        patch.object(module, "open_authenticated_academic_client", authenticated),
                        patch.object(module, persist_name) as persist,
                    ):
                        result = await sync(42)
                    self.assertNotEqual(result["status"], "success")
                    persist.assert_not_called()

    async def test_newer_empty_snapshot_prevents_an_older_inflight_response_from_publishing(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE system_settings(key TEXT PRIMARY KEY,value,updated_at)")
        for module, _, sync, persist_name in SOURCES:
            source = "course_exam" if module is exams else "invigilation"
            table = f"teacher_academic_{source}_items"
            conn.execute(f"CREATE TABLE {table}(teacher_id,school_code,academic_year,academic_term,sync_status)")
            waiting = asyncio.Event()
            release = asyncio.Event()
            persisted = []

            @asynccontextmanager
            async def authenticated(_credential):
                async def school(request):
                    if "doType=query" not in request.url.query.decode():
                        return httpx.Response(200, json=[])
                    if waiting.is_set():
                        return httpx.Response(200, json=grid([]))
                    waiting.set()
                    await release.wait()
                    return httpx.Response(200, json=grid([row("old")]))
                async with httpx.AsyncClient(base_url="https://school.invalid", transport=httpx.MockTransport(school)) as client:
                    yield client, SimpleNamespace(school_name="Synthetic"), {}

            def persist(_conn, **kwargs):
                persisted.append(len(kwargs["items"]))
                return dict.fromkeys([f"{source}_count", "created_count", "updated_count", "matched_offering_count",
                                      "event_created_count", "event_updated_count", "student_notification_count",
                                      "notification_count", "teacher_notification_count", "stale_count"], 0)

            clock = iter(["2099-11-01T00:00:01+00:00","2099-11-01T00:00:02+00:00"])
            with (
                patch.object(module, "get_db_connection", return_value=nullcontext(conn)),
                patch.object(module, "load_teacher_academic_access_method", return_value={"id": 1}),
                patch.object(module, "load_current_exam_semester", return_value=SEMESTER),
                patch.object(module, "open_authenticated_academic_client", authenticated),
                patch.object(module, "exam_snapshot_start", side_effect=clock),
                patch.object(module, persist_name, persist),
            ):
                old = asyncio.create_task(sync(42))
                await asyncio.wait_for(waiting.wait(), timeout=3)
                try:
                    latest = await sync(42)
                finally:
                    release.set()
                previous = await old
            self.assertEqual(latest["status"], "success")
            self.assertTrue(previous["snapshot_superseded"])
            self.assertEqual(persisted, [0])
            conn.rollback()
            claim = dict(teacher_id=42, semester=SEMESTER, term={"xnm":"2026","xqm":"3"}, source=source)
            self.assertIsNone(feed.superseding_exam_snapshot(conn, **claim, started_at="2099-11-01T00:00:04+00:00"))
            conn.rollback()  # The failed publication must not invalidate a subsequent successful read.
            self.assertIsNone(feed.superseding_exam_snapshot(conn, **claim, started_at="2099-11-01T00:00:03+00:00"))
            conn.commit()

    def test_date_precision_without_inventing_start_time(self):
        samples = [
            ("2026年12月5日 (8：05～10：00)", ("2026-12-05", "2026-12-05T08:05", "2026-12-05T10:00")),
            ("2026/12/31 (23:00-0:30)", ("2026-12-31", "2026-12-31T23:00", "2027-01-01T00:30")),
            ("2026-12-05 时间待定", ("2026-12-05", "", "")),
            ("2026-11-31", ("", "", "")),
        ]
        for value, expected in samples:
            self.assertEqual(feed.parse_exam_time(value)[:3], expected)

    def test_conflicting_teaching_section_never_matches_by_course_code_alone(self):
        item = exams._item_from_row(dict(row("A"), jxbmc="课程-0002"), term_params={}, source_url="")
        context = {"class_offering_id": 1, "academic_course_code": "CODE", "academic_teaching_class_name": "课程-0001"}
        self.assertEqual(exams._attach_best_offering(item, [context]), 0)
        self.assertIsNone(item.class_offering_id)
        item.raw_json["jxb_id"] = "section-2"
        context.update(academic_teaching_class_id="section-1", academic_teaching_class_name="课程-0002")
        self.assertEqual(exams._attach_best_offering(item, [context]), 0)
        context.update(academic_teaching_class_id="section-2")
        self.assertGreater(exams._attach_best_offering(item, [context]), 0)
        self.assertEqual(item.class_offering_id, 1)

    def test_current_school_and_date_scope(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE academic_semesters(id, teacher_id, school_code, start_date, end_date, updated_at)")
        conn.executemany("INSERT INTO academic_semesters VALUES (?, ?, ?, ?, ?, ?)", [
            (7, 42, "gxufl", "2026-09-01", "2027-01-15", 1),
            (8, 42, "gxufl", "2025-09-01", "2026-01-15", 3),
            (9, 1, "gxufl", "2026-09-01", "2027-01-15", 2),
            (10, 42, "other", "2026-09-01", "2027-01-15", 4),
        ])
        with patch.object(feed, "load_teacher_org_scope", return_value={"school_code": "gxufl"}):
            self.assertEqual(feed.load_current_exam_semester(conn, 42, date(2026, 9, 1))["id"], 7)
            self.assertEqual(feed.load_current_exam_semester(conn, 3, date(2027, 1, 15))["id"], 9)
            self.assertIsNone(feed.load_current_exam_semester(conn, 42, date(2027, 1, 16)))

    def test_merged_class_sittings_and_parallel_rooms(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE class_offerings(id INTEGER, class_id INTEGER);
            CREATE TABLE class_offering_class_links(offering_id INTEGER, class_id INTEGER);
            CREATE TABLE classes(id INTEGER, name TEXT, academic_class_name TEXT, academic_class_code TEXT);
            INSERT INTO class_offerings VALUES (42, 1);
            INSERT INTO class_offering_class_links VALUES (42, 2);
            INSERT INTO classes VALUES (1, '合班一', '合成01班', 'SYNTH-1'), (2, '合班二', '合成010班', 'SYNTH-10');
        """)
        sitting = {"class_offering_id": 42, "starts_at": "2099-11-11T09:00", "campus": "甲校区"}
        rows = [
            dict(sitting, location="301", class_composition="SYNTH-1", ends_at="2099-11-11T10:00"),
            dict(sitting, location="302", class_composition="合成０１班", ends_at="2099-11-11T11:30"),
            dict(sitting, location="303", class_composition="合成010班"),
            dict(sitting, location="304", class_composition="不存在班"),
            dict(sitting, location="305", class_composition="", starts_at="2099-11-11T14:00"),
        ]
        rules = audience.course_exam_student_rules(conn, rows)
        self.assertEqual([r.get("student_class_ids") for r in rules], [[1], [1], [2], [], None])
        self.assertTrue(audience.student_allowed(rules[0], 1))
        self.assertFalse(audience.student_allowed(rules[0], 2))
        self.assertTrue(audience.student_allowed(rules[4], 2))
        for r in rules[:2]:
            self.assertEqual(audience.room_options_for_student(r, 1), ["甲校区 301", "甲校区 302"])
        self.assertEqual(audience.room_options_for_student(rules[2], 2), [])
        self.assertEqual(audience.room_options_for_student(rules[4], 1), [])
        self.assertEqual(audience.student_room_group(rows[0]), audience.student_room_group(rows[1]))
        self.assertNotEqual(audience.student_room_group(rows[0]), audience.student_room_group(rows[4]))
        visible = audience.student_exam_rows(conn, rows, 1)
        self.assertEqual([row["location"] for row in visible], ["301", "305"])
        self.assertIn("个人考场请核对教务座位名单", audience.student_exam_location(visible[0]))
        self.assertEqual(visible[0]["student_room_options"], ["甲校区 301", "甲校区 302"])
        self.assertEqual(visible[0]["ends_at"], "2099-11-11T11:30")
        self.assertIn("10:00", visible[0]["student_room_times"])
        self.assertIn("11:30", visible[0]["student_room_times"])
        self.assertEqual([row["location"] for row in audience.student_exam_rows(conn, rows, 2)], ["303", "305"])
        self.assertNotIn("student_room_options", rows[0])
