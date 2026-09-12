from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

import fitz
import httpx

from classroom_app.services import attendance_report_parser_service as parser
from classroom_app.services import smart_classroom_attendance_adapter as source
from classroom_app.services import smart_classroom_checkin_sync_service as legacy


def extracted(values=None):
    values = values or ["出勤", "缺课", "病假", "事假", "迟到或早退", ""]
    return {"page_count": 1, "missing_pages": [], "title": "2025-2026学年第2学期 测试课程",
            "blocks": [{"page": 1, "method": "vector_grid", "headers": [f"03-0{i+1} 08:00" for i in range(6)],
                        "rows": [{"sequence": "1", "student_number": "０００１２３", "name": "测试甲", "class_name": "测试班",
                                  "values": values, "boxes": [[i*10, 20, i*10+10, 30] for i in range(6)], "bbox": [0,20,60,30]}]}]}


def manifest():
    return {"schedule": {"year": "2025-2026", "semester": "2", "course": "测试课程"},
            "checkins": [{"id": f"event-{i}", "createTime": f"2026-03-0{i+1} 08:00:27"} for i in range(6)]}


def make_grid_pdf(path):
    doc = fitz.open()
    for number in range(2):
        page = doc.new_page(width=650, height=300)
        page.insert_text((20,30), "2025-2026学年第2学期 测试课程", fontname="china-s", fontsize=10)
        labels = ["序号", "班级", "姓名", "学号", *[f"03-0{i+1}\n08:00" for i in range(6)]]
        data = [str(number+1), "测试班", "测试甲", f"00000{number+1}", "出勤", "缺课", "病假", "事假", "迟到或早退", "出勤"]
        for x in range(11):
            page.draw_line((20+x*60,60), (20+x*60,140))
        for y in range(3):
            page.draw_line((20,60+y*40), (620,60+y*40))
        for row, values in enumerate((labels,data)):
            for col, text in enumerate(values):
                for line, part in enumerate(text.splitlines()):
                    page.insert_text((23+col*60, 78+row*40+line*10), part, fontsize=7, fontname="china-s")
        page.insert_text((100,150), "watermark noise", fontsize=18, morph=(fitz.Point(100,150),fitz.Matrix(45)))
    doc.save(path)
    doc.close()


class AttendanceParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_business_context_and_actual_model_metadata(self):
        from classroom_app import core
        from classroom_app.services.ai_model_policy import AIBusinessContext
        def handler(request):
            payload=json.loads(request.content)
            context=AIBusinessContext.from_mapping(payload["business_context"])
            self.assertEqual(context.operation,"document")
            self.assertEqual(payload["tools"],[])
            return httpx.Response(200,json={"status":"success","response_json":{"cells":[]},"execution_metadata":{"provider":"synthetic","model":"actual-synthetic-model","usage":{"total_tokens":17}}})
        async with httpx.AsyncClient(base_url="https://synthetic.invalid",transport=httpx.MockTransport(handler)) as client:
            with patch.object(core,"ai_client",client):
                result,model=await parser._gateway_json("system","prompt",teacher_id=1)
        self.assertEqual(model,"actual-synthetic-model")
        self.assertEqual(result["_execution_metadata"]["usage"]["total_tokens"],17)

    def test_unknown_does_not_become_absent_and_keeps_text_identity(self):
        result = parser.build_attendance_candidate(extracted(), manifest())
        self.assertEqual(result["students"][0]["student_number"], "000123")
        self.assertEqual(result["cells"][-1]["normalized_status"], "UNKNOWN")
        self.assertEqual(result["validation"]["unknown_count"],1)
        self.assertFalse(result["validation"]["can_confirm"])
        self.assertFalse(result["ai_used"])

    def test_actual_vector_grid_ignores_rotated_watermark_and_merges_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"grid.pdf"
            make_grid_pdf(path)
            result=parser.parse_attendance_pdf(path,manifest())
        self.assertEqual((len(result["students"]),len(result["sessions"]),len(result["cells"])),(2,6,12))
        self.assertEqual(Counter(c["normalized_status"] for c in result["cells"]),{"CHECKED":4,"UNCHECKED":2,"SICK_LEAVE":2,"PERSONAL_LEAVE":2,"LATE_OR_EARLY":2})
        self.assertEqual(result["validation"]["blockers"],[])
        self.assertEqual(result["coverage"]["processed_pages"],[1,2])

    def test_missing_page_duplicate_minute_and_wrong_term_are_blockers(self):
        raw=extracted()
        raw.update(page_count=2,missing_pages=[2],title="2025-2026第1学期 测试课程")
        raw["blocks"][0]["headers"][1]=raw["blocks"][0]["headers"][0]
        result=parser.build_attendance_candidate(raw,manifest())
        codes={b["code"] for b in result["validation"]["blockers"]}
        self.assertTrue({"page_coverage","duplicate_time_header","source_term_mismatch"} <= codes)

    def test_api_disagreement_retains_pdf_evidence_for_review(self):
        meta=manifest()
        meta["details"]=[{"id":"event-0","stuList":[{"no":"000123","status":"UNCHECKED"}]}]
        result=parser.build_attendance_candidate(extracted(),meta)
        self.assertEqual(result["cells"][0]["normalized_status"],"CHECKED")
        self.assertEqual(result["cells"][0]["api_status"],"UNCHECKED")
        self.assertEqual(result["cells"][0]["quality_state"],"conflict")

    async def test_ai_requires_every_cell_and_preserves_disagreements(self):
        async def ai(system,prompt,**kwargs):
            cells=json.loads(prompt.split("输入：\n")[1])
            return {"cells":[{**c,"normalized_status":"CHECKED"} for c in cells]},"test-model"
        with patch.object(parser,"_extract_grid_pages",return_value=extracted()):
            result=await parser.analyze_attendance_pdf("unused.pdf",manifest(),teacher_id=1,ai_chat=ai)
        self.assertTrue(result["ai_used"])
        self.assertEqual(result["ai_coverage"]["processed_pages"],[1])
        self.assertEqual(result["validation"]["conflict_count"],5)
        self.assertEqual(result["cells"][-1]["normalized_status"],"UNKNOWN")

    async def test_partial_ai_response_does_not_count_as_completed(self):
        async def ai(*args,**kwargs): return {"cells":[]},"test-model"
        with patch.object(parser,"_extract_grid_pages",return_value=extracted()):
            result=await parser.analyze_attendance_pdf("unused.pdf",manifest(),teacher_id=1,ai_chat=ai)
        self.assertFalse(result["ai_used"])
        self.assertEqual(result["ai_coverage"]["processed_cells"],0)
        self.assertIn("ai_incomplete",[b["code"] for b in result["validation"]["blockers"]])


class AttendanceSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pause=patch.object(source,"REQUEST_INTERVAL",0)
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def test_term_and_account_identity_are_not_credential_row_identity(self):
        self.assertEqual(source.validate_source_term("2025-2026",2),("2025-2026","2"))
        for year,term in (("2025-2027",2),("2025-2026",12),("",1),("2025-2026",3)):
            with self.assertRaises(source.AttendanceSourceError): source.validate_source_term(year,term)
        self.assertNotEqual(source.external_account_key({"username":"a","credential_id":1}),source.external_account_key({"username":"b","credential_id":1}))

    async def test_schedule_query_has_explicit_term_and_rejects_other_term(self):
        def handler(request):
            self.assertEqual(parse_qs(request.content.decode()),{"year":["2025-2026"],"semester":["2"]})
            return httpx.Response(200,json=[{"id":"opaque","year":"2025-2026","semester":"1"}])
        async with httpx.AsyncClient(base_url="https://source.test",transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(source.AttendanceSourceError,"其他学期"):
                await source.fetch_source_schedules(client,"2025-2026","2")

    async def test_full_pagination_and_duplicate_records(self):
        seen=[]
        def handler(request):
            page=int(parse_qs(request.content.decode())["page"][0]);seen.append(page)
            return httpx.Response(200,json={"list":[{"id":page,"teacherScheduleId":"opaque"}],"pageNumber":page,"totalRow":2,"totalPage":2})
        async with httpx.AsyncClient(base_url="https://source.test",transport=httpx.MockTransport(handler)) as client:
            rows=await source.fetch_source_checkins(client,"opaque")
        self.assertEqual(seen,[1,2]);self.assertEqual(len(rows),2)
        def duplicate(request):
            page=int(parse_qs(request.content.decode())["page"][0])
            return httpx.Response(200,json={"list":[{"id":1}],"pageNumber":page,"totalRow":2,"totalPage":2})
        async with httpx.AsyncClient(base_url="https://source.test",transport=httpx.MockTransport(duplicate)) as client:
            with self.assertRaisesRegex(source.AttendanceSourceError,"重复"):
                await source.fetch_source_checkins(client,"opaque")

    async def test_teaching_group_can_contain_different_timetable_ids(self):
        schedule={"id":"representative","year":"2025-2026","semester":"2","courseId":"TEST","claId":"CLASS-A"}
        def handler(request):
            return httpx.Response(200,json={"pageNumber":1,"totalRow":2,"totalPage":1,"list":[{**schedule,"id":i,"teacherScheduleId":f"timetable-{i}"} for i in (1,2)]})
        async with httpx.AsyncClient(base_url="https://synthetic.invalid",transport=httpx.MockTransport(handler)) as client:
            rows=await source.fetch_source_checkins(client,"representative",expected_schedule=schedule)
            self.assertEqual(len(rows),2)
            with self.assertRaises(source.AttendanceSourceError):
                await source.fetch_source_checkins(client,"representative",expected_schedule={**schedule,"claId":"OTHER-CLASS"})

    async def test_download_only_sends_schedule_and_validates_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf=Path(folder)/"fixture.pdf";make_grid_pdf(pdf)
            def handler(request):
                self.assertEqual(parse_qs(request.content.decode()),{"teacherScheduleId":["opaque"]})
                return httpx.Response(200,content=pdf.read_bytes(),headers={"content-type":"application/pdf","content-disposition":"attachment;filename=../../original.pdf"})
            with patch.object(source,"DATA_DIR",folder):
                async with httpx.AsyncClient(base_url="https://source.test",transport=httpx.MockTransport(handler)) as client:
                    path,name=await source._download_pdf(client,"opaque")
                self.assertEqual(name,"original.pdf");self.assertEqual(source.validate_pdf_file(path)["page_count"],2)
                path.unlink()

    async def test_retry_after_and_non_pdf_fail_without_retained_partial_file(self):
        source_response=httpx.Response(429,headers={"Retry-After":"120"})
        with self.assertRaises(source.AttendanceSourceError) as caught: source._check_response(source_response)
        self.assertEqual(caught.exception.retry_after,120)
        with tempfile.TemporaryDirectory() as folder,patch.object(source,"DATA_DIR",folder):
            async with httpx.AsyncClient(base_url="https://source.test",transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b"<html>login</html>",headers={"content-type":"application/pdf"}))) as client:
                with self.assertRaises(source.AttendanceSourceError):await source._download_pdf(client,"opaque")
            self.assertEqual(list(Path(folder).rglob("*.pdf")),[])


class LegacyAttendanceMatchingTests(unittest.TestCase):
    def candidate(self,**changes):
        return replace(legacy.OfferingCandidate(1,1,"测试班","测试课程","TEST","教学班-0001","2025-2026第二学期","2025-2026-2","","",set()),**changes)

    def test_year_digit_does_not_match_wrong_term_and_ties_do_not_choose_larger_id(self):
        candidate=self.candidate(semester_name="2025-2026第一学期",semester_text="2025-2026-1")
        self.assertFalse(legacy._term_matches(candidate,"2025-2026","2"))
        schedule={"courseId":"TEST","course":"测试课程","claName":"教学班-0001","year":"2025-2026","semester":2}
        self.assertIsNone(legacy._match_offering(schedule,[self.candidate(),self.candidate(id=2)])[0])
        self.assertIsNone(legacy._match_offering({**schedule,"claName":"教学班-0005"},[self.candidate()])[0])
        self.assertEqual(legacy._match_offering(schedule,[self.candidate()])[0].id,1)

    def test_same_day_ambiguous_sessions_remain_unmapped(self):
        sessions=[{"id":i,"session_date":"2026-03-01","academic_section_text":"1-2"} for i in (1,2)]
        self.assertIsNone(legacy._match_session(sessions,{"createTime":"2026-03-01 08:00:00","section":1},{})[0])
        self.assertIsNone(legacy._match_session(sessions,{},{} )[0])
