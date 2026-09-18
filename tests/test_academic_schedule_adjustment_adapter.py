import json
import math
import unittest
from urllib.parse import parse_qs
from unittest.mock import patch

import httpx

from classroom_app.services import academic_schedule_adjustment_adapter as adapter


SEMESTER = {
    "name": "2026-2027第一学期", "start_date": "2026-08-31", "end_date": "2027-01-10", "week_count": 19,
}


def class_row(**overrides):
    result = {
        "jxb_id": "TEST-CLASS-A", "jxbmc": "测试教学班A", "jxbzc": "测试行政班A、测试行政班B",
        "kch": "TEST-CODE-A", "kch_id": "TEST-INTERNAL-A", "kcmc": "示例课程A",
        "sksj": "星期六第6-7节{1-3周(单),4-16周};星期日第4-5节{1-16周}",
        "jxdd": "测试楼A101;测试楼A101", "xnm": "2026", "xqm": "3", "xnmmc": "2026-2027", "xqmmc": "1",
        "userModel": {"unrelated": "must not be retained"}, "password": "SYNTHETIC-DO-NOT-RETAIN",
    }
    result.update(overrides)
    return result


def request_row(number=1, **overrides):
    result = {
        **class_row(), "ttk_id": f"TEST-REQUEST-{number}", "ttk_lsh": f"TEST-SERIAL-{number}",
        "shzt": "1", "tklxdm": "01", "tklxmc": "调课", "sqtjsj": "2026-09-18 10:00:00", "tkyy": "测试原因",
    }
    result.update(overrides)
    return result


def detail_row(request, number=1, **overrides):
    result = {
        "ttkxx_id": f"TEST-DETAIL-{number}", "ttk_lsh": request["ttk_lsh"], "jxb_id": request["jxb_id"],
        "xnm": "2026", "xqm": "3", "tklxdm": request["tklxdm"],
        "tkqrq": "2026-09-19", "yzcd": "3周", "yxqj": "六", "yjc": "第6-7节",
        "ycd_id": "TEST-ROOM-A", "ycdmc": "测试楼A101", "yjgh": "TEST-TEACHER",
        "tkhrq": "2026-09-22", "xzcd": "4周", "xxqj": "二", "xjc": "第10-11节",
        "xcd_id": "TEST-ROOM-B", "xcdmc": "测试楼B202", "xjgh": "TEST-TEACHER", "tkyy": "测试原因",
        # Opaque bitmasks must not become period/week values.
        "jc": "110000000000", "zcd": "11111111111111111111", "contact": "SYNTHETIC-DO-NOT-RETAIN",
    }
    if request["tklxdm"] == "03":
        result.update({key: "" for key in adapter._TARGET_FIELDS})
    result.update(overrides)
    return result


def detail_html(request, rows=None, *, request_id=None, kind=None, expression=None):
    rows = [detail_row(request)] if rows is None else rows
    value = json.dumps(rows, ensure_ascii=False) if expression is None else expression
    return (
        f'<input type="hidden" name="ttk_id" id="ttk_id" value="{request_id or request["ttk_id"]}"/>'
        f'<input type="hidden" value="{kind or request["tklxdm"]}" id="tklxdm_sub"/>'
        f'<script>var modelList = {value}; var unrelated = {{}};</script>'
    )


def term_html(**overrides):
    fields = {"xnm": "2026", "xqm": "3", "pkxnm": "2026", "pkxqm": "3"}
    fields.update(overrides)
    content = "".join(f'<input type="hidden" name="{key}" id="{key}" value="{value}"/>' for key, value in fields.items())
    return '<input id="xnm" name="xnm" value=""/><div id="searchForm" style="display:none">' + content + '</div><input id="xqm" name="xqm" value=""/>'


class MockSchool:
    def __init__(self, *, classes=None, requests=None, detail_pages=None, change_response=None, entry=None):
        self.classes = [class_row()] if classes is None else classes
        self.requests = [request_row()] if requests is None else requests
        self.detail_pages = detail_pages or {}
        self.change_response = change_response
        self.entry = term_html() if entry is None else entry
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        form = {key: value[0] for key, value in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        if path.endswith("cxTtksqIndex.html"):
            response = httpx.Response(200, text=self.entry, headers={"content-type": "text/html"})
        elif path.endswith("cxShxxView.html"):
            record = next(row for row in self.requests if row["ttk_id"] == request.url.params["ttk_id"])
            page = self.detail_pages.get(record["ttk_id"], detail_html(record))
            response = httpx.Response(200, text=page, headers={"content-type": "text/html"})
        else:
            rows = self.classes if path.endswith("cxTtksqList.html") else self.requests
            size, page = int(form["queryModel.showCount"]), int(form["queryModel.currentPage"])
            payload = {"items": rows[(page - 1) * size:page * size], "currentPage": page,
                       "totalPage": math.ceil(len(rows) / size), "totalResult": len(rows)}
            response = httpx.Response(200, json=payload)
        return self.change_response(request, form, response) if self.change_response else response

    def client(self):
        return httpx.AsyncClient(base_url="https://school.test", transport=httpx.MockTransport(self))


class AdjustmentSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_all_states_and_multi_row_details_are_preserved(self):
        requests = [request_row(index, shzt=state) for index, state in enumerate(["0", "1", "2", "3", "4", "5", "99"], 1)]
        classes = [class_row(), class_row(jxb_id="TEST-CLASS-B")]
        details = [detail_row(requests[0]), detail_row(requests[0], 2, tkqrq="2026-09-20", yxqj="日", yjc="第4-5节")]
        school = MockSchool(classes=classes, requests=requests, detail_pages={requests[0]["ttk_id"]: detail_html(requests[0], details)})
        with patch.object(adapter, "PAGE_SIZE", 2):
            async with school.client() as client:
                result = await adapter.fetch_adjustment_snapshot(client, SEMESTER)
        self.assertEqual([row["status"] for row in result["requests"]], ["draft", "pending", "pending", "approved", "returned", "rejected", "unknown"])
        self.assertEqual(len(result["requests"][0]["details"]), 2)
        point = result["requests"][0]["details"][0]["original"]
        self.assertEqual((point["date"], point["week"], point["weekday"], point["sections"]), ("2026-09-19", 3, 6, [6, 7]))
        self.assertEqual(len([call for call in school.calls if "jgList" in call.url.path]), 4)
        self.assertTrue(any(row["status"] == "warning" for row in result["source_summary"]))
        self.assertEqual(set(result["teaching_classes"][0]), set(adapter.CLASS_FIELDS))
        self.assertNotIn("SYNTHETIC-DO-NOT-RETAIN", json.dumps(result))
        for call in school.calls:
            self.assertEqual(call.method, "POST")
            if "ShxxView" in call.url.path:
                self.assertEqual(call.content, b"")
                self.assertEqual(call.url.params["xqm"], "3")
                self.assertEqual(call.url.params["xnm"], "2026")
            else:
                form = parse_qs(call.content.decode(), keep_blank_values=True)
                self.assertEqual(form["xnm"], ["2026"])
                self.assertEqual(form["xqm"], ["3"])

    async def test_cancellation_has_no_target_and_unknown_type_is_not_inferred(self):
        records = [request_row(1, tklxdm="03"), request_row(2, tklxdm="88")]
        school = MockSchool(requests=records)
        async with school.client() as client:
            result = await adapter.fetch_adjustment_snapshot(client, SEMESTER)
        self.assertEqual(result["requests"][0]["kind"], "cancel")
        self.assertIsNone(result["requests"][0]["details"][0]["proposed"])
        self.assertEqual(result["requests"][1]["kind"], "unknown")
        self.assertEqual(result["source_summary"][-1]["status"], "warning")

    async def test_room_only_move_preserves_same_time_and_different_room(self):
        record = request_row()
        detail = detail_row(record, tkhrq="2026-09-19", xzcd="3周", xxqj="六", xjc="第6-7节")
        school = MockSchool(detail_pages={record["ttk_id"]: detail_html(record, [detail])})
        async with school.client() as client:
            result = await adapter.fetch_adjustment_snapshot(client, SEMESTER)
        change = result["requests"][0]["details"][0]
        self.assertEqual(change["original"]["date"], change["proposed"]["date"])
        self.assertNotEqual(change["original"]["room"], change["proposed"]["room"])

    async def test_explicit_empty_pagination_is_distinct_from_missing_response(self):
        school = MockSchool(classes=[], requests=[])
        async with school.client() as client:
            result = await adapter.fetch_adjustment_snapshot(client, SEMESTER)
        self.assertEqual(result["teaching_classes"], [])
        self.assertEqual(result["requests"], [])
        self.assertEqual(len(result["source_summary"]), 2)

    async def test_wrong_or_missing_term_in_list_is_rejected(self):
        for override in [{"xqm": "12"}, {"xnm": ""}]:
            with self.subTest(override=override):
                school = MockSchool(classes=[class_row(**override)])
                async with school.client() as client:
                    with self.assertRaisesRegex(ValueError, "学年学期"):
                        await adapter.fetch_adjustment_snapshot(client, SEMESTER)

    async def test_target_term_cannot_be_inferred_from_dates_alone(self):
        school = MockSchool()
        async with school.client() as client:
            with self.assertRaisesRegex(ValueError, "猜测"):
                await adapter.fetch_adjustment_snapshot(client, {**SEMESTER, "name": ""})
        self.assertEqual(school.calls, [])

    async def test_pagination_failure_never_returns_partial_snapshot(self):
        def mutation(kind):
            def change(request, form, response):
                if "jgList" not in request.url.path:
                    return response
                value = response.json()
                page = int(form["queryModel.currentPage"])
                if kind == "bad_page" and page == 2:
                    value["currentPage"] = 1
                elif kind == "repeated_rows" and page == 2:
                    value["items"] = [request_row(1)]
                elif kind == "missing_page" and page == 2:
                    value["items"] = []
                elif kind == "changed_total" and page == 2:
                    value["totalResult"] = 8
                elif kind == "short_total":
                    value["totalResult"], value["totalPage"] = 4, 2
                elif kind == "over_limit":
                    value["totalPage"] = adapter.MAX_PAGES + 1
                elif kind == "missing_total":
                    value.pop("totalResult")
                return httpx.Response(200, json=value)
            return change
        for kind in ["bad_page", "repeated_rows", "missing_page", "changed_total", "short_total", "over_limit", "missing_total"]:
            with self.subTest(kind=kind), patch.object(adapter, "PAGE_SIZE", 2):
                school = MockSchool(requests=[request_row(i) for i in range(1, 4)], change_response=mutation(kind))
                async with school.client() as client:
                    with self.assertRaises(ValueError):
                        await adapter.fetch_adjustment_snapshot(client, SEMESTER)

    async def test_login_html_redirect_and_http_error_are_not_empty_success(self):
        for response in [httpx.Response(200, text='<html><input name="mm"/>登录</html>'), httpx.Response(302, headers={"location": "/xtgl/login_slogin.html"}), httpx.Response(503)]:
            with self.subTest(status=response.status_code):
                school = MockSchool(change_response=lambda *_: response)
                async with school.client() as client:
                    with self.assertRaises(ValueError):
                        await adapter.fetch_adjustment_snapshot(client, SEMESTER)

    async def test_detail_identity_term_and_calendar_mismatches_fail_closed(self):
        record = request_row()
        bad_rows = [
            {"jxb_id": "OTHER-CLASS"}, {"xqm": "12"}, {"ttk_lsh": "OTHER-SERIAL"},
            {"tklxdm": "03"}, {"ttkxx_id": ""}, {"tkqrq": "2026-09-20"},
            {"yzcd": "4周"}, {"yjc": "110000000000"}, {"yxqj": "未知"},
            {"tkqrq": "2026-02-30"}, {"tkhrq": "2027-02-01"}, {"xzcd": ""},
        ]
        for overrides in bad_rows:
            with self.subTest(overrides=overrides):
                page = detail_html(record, [detail_row(record, **overrides)])
                school = MockSchool(detail_pages={record["ttk_id"]: page})
                async with school.client() as client:
                    with self.assertRaises(ValueError):
                        await adapter.fetch_adjustment_snapshot(client, SEMESTER)

    async def test_detail_request_marker_or_duplicate_detail_is_rejected(self):
        record = request_row()
        pages = [detail_html(record, request_id="OTHER-REQUEST"), detail_html(record, kind="03"),
                 detail_html(record, [detail_row(record), detail_row(record)]), detail_html(record, [])]
        for page in pages:
            with self.subTest(page=page[:60]):
                school = MockSchool(detail_pages={record["ttk_id"]: page})
                async with school.client() as client:
                    with self.assertRaises(ValueError):
                        await adapter.fetch_adjustment_snapshot(client, SEMESTER)

    async def test_model_list_javascript_is_never_evaluated(self):
        record = request_row()
        valid = json.dumps([detail_row(record)])
        for expression in ["makeSchedule()", valid + ".map(enrich)", "[{bad: 'javascript'}]", valid.replace('"xnm": "2026"', '"xnm": "2026", "xnm": "2025"')]:
            with self.subTest(expression=expression[:50]):
                school = MockSchool(detail_pages={record["ttk_id"]: detail_html(record, expression=expression)})
                async with school.client() as client:
                    with self.assertRaises(ValueError):
                        await adapter.fetch_adjustment_snapshot(client, SEMESTER)

    async def test_cancel_target_and_move_without_target_are_rejected(self):
        for kind in ["01", "03"]:
            record = request_row(tklxdm=kind)
            detail = detail_row(record)
            if kind == "01":
                detail.update({key: "" for key in adapter._TARGET_FIELDS})
            else:
                detail["tkhrq"] = "2026-09-22"
            school = MockSchool(requests=[record], detail_pages={record["ttk_id"]: detail_html(record, [detail])})
            async with school.client() as client:
                with self.assertRaises(ValueError):
                    await adapter.fetch_adjustment_snapshot(client, SEMESTER)


class OfficialOccurrenceTests(unittest.TestCase):
    def test_odd_even_disjoint_periods_and_rooms_expand_exactly(self):
        rows = [class_row(sksj="星期六第2-3,6-7节{1-3周(单),4-6周(双)};星期日第4-7节{2周}", jxdd="测试楼A101;测试楼B202")]
        result = adapter.build_official_occurrences(rows, SEMESTER)
        self.assertEqual(len(result), 9)
        self.assertEqual({r["week"] for r in result if r["weekday"] == 6}, {1, 3, 4, 6})
        self.assertEqual({tuple(r["sections"]) for r in result if r["weekday"] == 6}, {(2, 3), (6, 7)})
        contiguous = next(r for r in result if r["weekday"] == 7)
        self.assertEqual(contiguous["sections"], [4, 5, 6, 7])
        self.assertEqual(contiguous["date"], "2026-09-13")
        self.assertEqual(contiguous["room"], "测试楼B202")
        self.assertEqual(contiguous["class_label"], "测试行政班A、测试行政班B")

    def test_internal_course_id_does_not_become_official_course_code(self):
        result = adapter.build_official_occurrences([class_row(kch="")], SEMESTER)
        self.assertTrue(result)
        self.assertEqual({row["course_code"] for row in result}, {""})

    def test_malformed_or_partial_schedule_is_rejected_instead_of_defaulted(self):
        cases = [
            {"sksj": ""}, {"sksj": "星期一第4-5节"}, {"sksj": "星期一第4-5节{1-22周}"},
            {"sksj": "星期一第5-4节{1周}"}, {"sksj": "星期一第2-3,3-4节{1周}"},
            {"sksj": "星期一第2-3节{1-3周,未知}"}, {"sksj": "星期一第2-3节{1周}未知尾文"},
            {"jxdd": "测试教室1;测试教室2;测试教室3"}, {"jxdd": ""},
            {"xqm": "12"}, {"xnm": ""}, {"jxb_id": ""},
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    adapter.build_official_occurrences([class_row(**case)], SEMESTER)

    def test_duplicate_classes_or_rules_are_rejected(self):
        for rows in [[class_row(), class_row()], [class_row(sksj="星期一第2-3节{1周};星期一第2-3节{1周}")]]:
            with self.assertRaises(ValueError):
                adapter.build_official_occurrences(rows, SEMESTER)


class DiscoverTermTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_verified_search_form_hidden_fields_are_read(self):
        school = MockSchool()
        async with school.client() as client:
            result = await adapter.discover_current_term(client)
        self.assertEqual(result, {"xnm": "2026", "xqm": "3", "academic_year": "2026-2027", "academic_term": 1, "name": "2026-2027第一学期"})
        self.assertEqual(len(school.calls), 1)
        self.assertEqual(school.calls[0].method, "GET")

    async def test_absent_conflicting_ordinal_or_duplicate_scope_is_not_guessed(self):
        for page in ["<html>登录</html>", term_html(xqm="12"), term_html(xqm="1", pkxqm="1"), term_html(xnm=""), term_html() + term_html()]:
            with self.subTest(page=page[:60]):
                school = MockSchool(entry=page)
                async with school.client() as client:
                    with self.assertRaises(ValueError):
                        await adapter.discover_current_term(client)


if __name__ == "__main__":
    unittest.main()
