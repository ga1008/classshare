"""Synthetic transports for the observed N214510 contract; no real DB or JWXT."""
import copy
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from classroom_app.services import academic_teacher_timetable_adapter as adapter

INDEX_HTML = """<select id="xnm"><option value="2026">2026-2027</option></select>
<select id="xqm"><option value="3">1</option><option value="12">2</option></select>
<select id="xqh_id"><option value="1">测试校区</option></select>
<select id="jg_id"><option value="04">测试学院</option></select>
<select id="kkbm_id"><option value="04">测试学院</option></select>
<select id="zcm"><option value="013">讲师</option></select>"""


def teacher(no="001"):
    return {"jgh": no, "xm": "同名教师", "jgh_id": "opaque-" + no, "jg_id": "04", "jgmc": "测试学院",
            "xnm": "2026", "xqm": "3", "xqh_id": "1", "zcmc": "讲师"}


def schedule(no="001"):
    return {"kbList": [{"jgh": no, "xnm": "2026", "xqm": "3", "kch": "CS101", "kcmc": "合成课程",
                       "jxb_id": "C1", "jxbmc": "课程-001", "jxbzc": "合成班", "xqj": "3",
                       "jc": "2-3节", "jcs": "2-3", "zcd": "1-4周,6-12周(双)", "cdmc": "测试教室",
                       "xkrs": "35", "kcxszc": "理论:16,实践:16", "zxs": "32", "xf": "2",
                       "zzmm": "UNRELATED-PERSONNEL-FIELD"}],
            "sjkList": [{"qtkcgs": "合成安全教育/1-12周/安全教育-01/合成班"}],
            "bjsjkList": [], "jxhjkcList": []}


class TimetableAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.rows = [teacher()]
        self.payload = schedule()
        self.locked = "false"
        self.page_size = 20
        self.list_override = None
        self.response_override = None
        self.delay = patch.object(adapter, "REQUEST_INTERVAL", 0)
        self.delay.start()
        self.addCleanup(self.delay.stop)

    async def fetch(self, *, exact_teacher_no=None, **filters):
        def handle(request):
            if request.method == "GET":
                return httpx.Response(200, text=INDEX_HTML)
            data = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
            self.calls.append((request.url.path, data))
            if request.url.path.endswith("cxJskbdyList.html"):
                if self.list_override is not None:
                    payload = self.list_override
                else:
                    page = int(data["queryModel.currentPage"])
                    payload = {"items": self.rows[(page - 1) * self.page_size: page * self.page_size],
                               "totalCount": len(self.rows), "totalPage": (len(self.rows) + self.page_size - 1) // self.page_size,
                               "currentPage": page}
                return httpx.Response(200, json=payload)
            if request.url.path.endswith("cxXnxqsfkz.html"):
                return httpx.Response(200, json=self.locked)
            if self.response_override:
                return self.response_override
            body = copy.deepcopy(self.payload)
            if len(self.rows) > 1:
                body["kbList"][0]["jgh"] = data["jgh"]
            return httpx.Response(200, json=body)
        async with httpx.AsyncClient(base_url="https://academic.synthetic.invalid",
                                     transport=httpx.MockTransport(handle)) as client:
            return await adapter.fetch_teacher_timetables(client, xnm="2026", xqm="3",
                                                          filters={"q": "同名教师", **filters},
                                                          exact_teacher_no=exact_teacher_no)

    async def test_complete_term_keeps_other_courses_and_academic_details(self):
        result = await self.fetch(college_id="04", campus_id="1")
        person = result["teachers"][0]
        self.assertEqual(person["teacher_no"], "001")
        self.assertEqual(person["lessons"][0]["weeks"], [1, 2, 3, 4, 6, 8, 10, 12])
        self.assertEqual(person["lessons"][0]["sections"], [2, 3])
        self.assertEqual(person["lessons"][0]["class_label"], "合成班")
        self.assertEqual(person["lessons"][0]["course_hour_text"], "理论:16,实践:16")
        self.assertNotIn("UNRELATED-PERSONNEL-FIELD", str(person["lessons"]))
        self.assertEqual(len(person["unscheduled_courses"]), 1)
        self.assertEqual(person["schedule_payload"], self.payload)
        detail = self.calls[-1][1]
        self.assertEqual(detail["kkbm_id"], "")
        self.assertEqual(detail["zxszjjs"], "false")
        self.assertEqual(detail["jgh_id"], "opaque-001")

    async def test_same_names_on_multiple_pages_keep_both_employee_numbers(self):
        self.rows = [teacher("001"), teacher("002")]
        self.page_size = 1
        result = await self.fetch()
        self.assertEqual([t["teacher_no"] for t in result["teachers"]], ["001", "002"])
        self.assertEqual(len(self.calls), 6)

    async def test_valid_empty_does_not_fetch_a_timetable(self):
        self.rows = []
        result = await self.fetch()
        self.assertEqual(result["teachers"], [])
        self.assertEqual(len(self.calls), 1)

    async def test_exact_employee_sync_filters_before_detail_requests(self):
        self.rows = [teacher("1001"), teacher("001"), teacher("0010")]
        self.page_size = 1
        result = await self.fetch(exact_teacher_no="001", college_id="outdated", campus_id="outdated")
        self.assertEqual(["001"], [p["teacher_no"] for p in result["teachers"]])
        details = [data for path, data in self.calls if path.endswith("cxJsKb.html") or path.endswith("cxXnxqsfkz.html")]
        self.assertEqual(["001", "001"], [d["jgh"] for d in details])
        self.assertEqual("opaque-001", details[-1]["jgh_id"])
        self.assertEqual("001", self.calls[0][1]["jgh"])
        self.assertEqual("", self.calls[0][1]["kkbm_id"])
        self.assertEqual("", self.calls[0][1]["xqh_id"])

    async def test_exact_missing_employee_is_not_a_successful_empty_result(self):
        for rows in ([], [teacher("1001")], [teacher("0010")], [teacher("001"), teacher("001")]):
            with self.subTest(rows=rows):
                self.calls = []
                self.rows = rows
                with self.assertRaises(adapter.TeacherTimetableSourceError):
                    await self.fetch(exact_teacher_no="001")
                self.assertFalse(any(p.endswith("cxJsKb.html") for p, _ in self.calls))

    async def test_invalid_and_partial_lists_never_become_empty_cache(self):
        for value in ({}, {"items": []}, {"items": [], "totalCount": 1, "totalPage": 1, "currentPage": 1},
                      {"items": [], "totalCount": 21, "totalPage": 2, "currentPage": 1},
                      {"items": [teacher()], "totalCount": 1, "totalPage": 1, "currentPage": 2},
                      {"items": [teacher(), teacher()], "totalCount": 2, "totalPage": 1, "currentPage": 1}):
            with self.subTest(value=value):
                self.list_override = value
                with self.assertRaises(adapter.TeacherTimetableSourceError):
                    await self.fetch()

    async def test_denied_term_stops_before_timetable_request(self):
        self.locked = "true"
        with self.assertRaises(adapter.TeacherTimetableSourceError):
            await self.fetch()
        self.assertFalse(any(p.endswith("cxJsKb.html") for p, _ in self.calls))

    async def test_wrong_teacher_term_or_malformed_course_aborts(self):
        for key, value in (("jgh", "002"), ("xqm", "12"), ("xnm", "2025"),
                           ("zcd", ""), ("zcd", "0-3周"), ("zcd", "1-61周"),
                           ("zcd", "4-1周"), ("zcd", "1-3周待安排"),
                           ("xqj", "8"), ("jcs", "未知节次")):
            with self.subTest(key=key):
                self.payload = schedule()
                self.payload["kbList"][0][key] = value
                with self.assertRaises(adapter.TeacherTimetableSourceError):
                    await self.fetch()

    async def test_truncated_empty_timetable_is_not_a_complete_snapshot(self):
        for missing in ("sjkList", "bjsjkList", "jxhjkcList"):
            with self.subTest(missing=missing):
                self.payload = {"kbList": [], "sjkList": [], "bjsjkList": [], "jxhjkcList": []}
                del self.payload[missing]
                with self.assertRaises(adapter.TeacherTimetableSourceError):
                    await self.fetch()

    async def test_complete_empty_timetable_is_valid(self):
        self.payload = {"kbList": [], "sjkList": [], "bjsjkList": [], "jxhjkcList": []}
        person = (await self.fetch())["teachers"][0]
        self.assertEqual([], person["lessons"])
        self.assertEqual([], person["unscheduled_courses"])
        self.assertEqual(self.payload, person["schedule_payload"])

    async def test_optional_top_level_identity_is_validated_even_for_empty_timetable(self):
        empty = {"kbList": [], "sjkList": [], "bjsjkList": [], "jxhjkcList": []}
        self.payload = {**empty, "jgh": "001", "xnm": "2026", "xqm": "3"}
        self.assertEqual([], (await self.fetch())["teachers"][0]["lessons"])
        for key, wrong in (("jgh", "002"), ("xnm", "2025"), ("xqm", "12")):
            with self.subTest(key=key):
                self.payload = {**empty, key: wrong}
                with self.assertRaises(adapter.TeacherTimetableSourceError):
                    await self.fetch()

    async def test_login_html_and_redirect_cannot_overwrite_cache(self):
        for response in (httpx.Response(302, headers={"location": "/xtgl/login_slogin.html"}),
                         httpx.Response(200, text="<html>login</html>"),
                         httpx.Response(200, json={"success": False, "kbList": []})):
            self.response_override = response
            with self.assertRaises(adapter.TeacherTimetableSourceError):
                await self.fetch()

    async def test_unknown_filter_is_rejected_before_query(self):
        with self.assertRaises(adapter.TeacherTimetableSourceError):
            await self.fetch(college_id="unknown")
        self.assertEqual(self.calls, [])

    def test_discontinuous_sections_are_separate_meetings(self):
        payload = schedule()
        payload["kbList"][0]["jcs"] = "2-3,6-7"
        lessons, _ = adapter.normalize_timetable(payload, teacher_no="001", xnm="2026", xqm="3")
        self.assertEqual([i["sections"] for i in lessons], [[2, 3], [6, 7]])
