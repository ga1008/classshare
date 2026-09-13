"""Bounded synthetic edge cases. No live accounts, upstream sites, or paid AI calls."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import fitz
import httpx

from classroom_app.services import attendance_report_parser_service as parser
from classroom_app.services import smart_classroom_attendance_adapter as source


TITLE = "2025-2026学年第2学期 合成课程"


def manifest(columns=2, students=2):
    return {
        "schedule": {"id": "opaque-synthetic", "year": "2025-2026", "semester": "2", "course": "合成课程"},
        "checkins": [{"id": f"event-{col}", "createTime": f"2026-03-{col:02} 08:00:27"} for col in range(1, columns + 1)],
        "details": [{"id": f"event-{col}", "stuList": [{"no": f"{row:06}", "name": f"合成{row}", "status": "CHECKED"} for row in range(1, students + 1)]} for col in range(1, columns + 1)],
    }


def make_pdf(path, blocks, *, scanned=False):
    """Each block is (one-based student numbers, one-based date columns)."""
    doc = fitz.open()
    for students, columns in blocks:
        count = 4 + len(columns)
        page = doc.new_page(width=max(480, 20 + count * 60 + 20), height=300)
        page.insert_text((20, 30), TITLE, fontname="china-s", fontsize=10)
        headers = ["序号", "班级", "姓名", "学号", *[f"03-{col:02}\n08:00" for col in columns]]
        rows = [headers] + [[str(row), "合成班", f"合成{row}", f"{row:06}", *["出勤" for _ in columns]] for row in students]
        for col in range(count + 1):
            page.draw_line((20 + col * 60, 60), (20 + col * 60, 60 + len(rows) * 40))
        for row in range(len(rows) + 1):
            page.draw_line((20, 60 + row * 40), (20 + count * 60, 60 + row * 40))
        for row, cells in enumerate(rows):
            for col, value in enumerate(cells):
                for line, text in enumerate(value.splitlines()):
                    page.insert_text((23 + col * 60, 78 + row * 40 + line * 10), text, fontname="china-s", fontsize=7)
    if scanned:
        images = [(page.rect, page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).tobytes("png")) for page in doc]
        doc.close()
        doc = fitz.open()
        for rect, blob in images:
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, stream=blob)
    doc.save(path)
    doc.close()


def extracted(columns=2, students=2):
    return {
        "page_count": 1, "missing_pages": [], "title": TITLE,
        "blocks": [{"page": 1, "method": "vector_grid", "headers": [f"03-{col:02} 08:00" for col in range(1, columns + 1)],
                    "rows": [{"sequence": str(row), "student_number": f"{row:06}", "name": f"合成{row}", "class_name": "合成班",
                              "values": ["出勤"] * columns, "boxes": [[20 + col * 20, 60 + row * 20, 40 + col * 20, 80 + row * 20] for col in range(columns)], "bbox": [20, 60 + row * 20, 200, 80 + row * 20]} for row in range(1, students + 1)]}],
    }


async def echo_ai(system, prompt, **kwargs):
    cells = json.loads(prompt.split("输入：\n")[1])
    return {"cells": [{**cell, "normalized_status": "CHECKED"} for cell in cells]}, "synthetic-local-stub"


class AttendanceParserEdgeCases(unittest.IsolatedAsyncioTestCase):
    async def test_nine_page_vector_pdf_covers_every_page_and_student(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "nine-pages.pdf"
            make_pdf(pdf, [([row], [1, 2]) for row in range(1, 10)])
            result = await parser.analyze_attendance_pdf(pdf, manifest(2, 9), teacher_id=1, ai_chat=echo_ai)
        self.assertEqual(result["coverage"]["processed_pages"], list(range(1, 10)))
        self.assertEqual(result["ai_coverage"]["processed_pages"], list(range(1, 10)))
        self.assertEqual((len(result["students"]), len(result["sessions"]), len(result["cells"])), (9, 2, 18))
        self.assertTrue(result["validation"]["can_confirm"])

    async def test_horizontal_continuation_merges_student_identity_without_losing_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "horizontal-continuation.pdf"
            make_pdf(pdf, [([1, 2], [1, 2]), ([1, 2], [3, 4])])
            result = await parser.analyze_attendance_pdf(pdf, manifest(4, 2), teacher_id=1, ai_chat=echo_ai)
        self.assertEqual((len(result["students"]), len(result["sessions"]), len(result["cells"])), (2, 4, 8))
        self.assertEqual({(c["row_index"], c["column_index"]) for c in result["cells"]}, {(r, c) for r in (1, 2) for c in (1, 2, 3, 4)})
        self.assertTrue(result["validation"]["can_confirm"])

    async def test_scanned_page_invokes_vision_with_valid_coordinates_then_verifies_all_cells(self):
        calls = []
        async def ai(system, prompt, **kwargs):
            calls.append(bool(kwargs.get("images")))
            if kwargs.get("images"):
                self.assertTrue(kwargs["images"][0].startswith("data:image/png;base64,"))
                block = extracted()["blocks"][0]
                return {"title": TITLE, "headers": block["headers"], "rows": block["rows"]}, "synthetic-vision"
            return await echo_ai(system, prompt, **kwargs)
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "scanned.pdf"
            make_pdf(pdf, [([1, 2], [1, 2])], scanned=True)
            result = await parser.analyze_attendance_pdf(pdf, manifest(), teacher_id=1, ai_chat=ai)
        self.assertEqual(calls, [True, False])
        self.assertEqual(result["ai_coverage"]["vision_pages"], [1])
        self.assertEqual(result["ai_coverage"]["processed_cells"], 4)
        self.assertTrue(result["validation"]["can_confirm"])

    async def test_scanned_ai_omitted_student_or_column_is_blocked_by_source_manifest(self):
        for omission in ("student", "column"):
            with self.subTest(omission=omission), tempfile.TemporaryDirectory() as folder:
                pdf = Path(folder) / "scanned-omission.pdf"
                make_pdf(pdf, [([1, 2], [1, 2])], scanned=True)
                async def ai(system, prompt, **kwargs):
                    self.assertTrue(kwargs.get("images"), "Structural failure must not make more AI calls")
                    block = extracted()["blocks"][0]
                    if omission == "student":
                        block["rows"] = block["rows"][:1]
                    else:
                        block["headers"] = block["headers"][:1]
                        for row in block["rows"]:
                            row["values"], row["boxes"] = row["values"][:1], row["boxes"][:1]
                    return {"title": TITLE, "headers": block["headers"], "rows": block["rows"]}, "synthetic-vision"
                result = await parser.analyze_attendance_pdf(pdf, manifest(), teacher_id=1, ai_chat=ai)
                self.assertFalse(result["validation"]["can_confirm"])
                self.assertIn("source_roster_difference" if omission == "student" else "source_session_coverage", {b["code"] for b in result["validation"]["blockers"]})

    async def test_ai_dropped_or_reidentified_cell_cannot_shrink_candidate(self):
        for mutation in ("drop", "change_identity"):
            with self.subTest(mutation=mutation):
                async def ai(system, prompt, **kwargs):
                    payload, model = await echo_ai(system, prompt, **kwargs)
                    if mutation == "drop": payload["cells"].pop()
                    else: payload["cells"][0]["row_index"] = 500
                    return payload, model
                with patch.object(parser, "_extract_grid_pages", return_value=extracted()):
                    result = await parser.analyze_attendance_pdf("unused.pdf", manifest(), teacher_id=1, ai_chat=ai)
                self.assertEqual((len(result["students"]), len(result["sessions"]), len(result["cells"])), (2, 2, 4))
                self.assertEqual(result["ai_coverage"]["processed_cells"], 0)
                self.assertIn("ai_incomplete", {b["code"] for b in result["validation"]["blockers"]})

    def test_duplicate_minute_does_not_guess_remote_identity_across_horizontal_pages(self):
        raw = extracted(1, 2)
        raw["page_count"] = 2
        second = copy.deepcopy(raw["blocks"][0]); second["page"] = 2
        raw["blocks"].append(second)
        meta = manifest(2, 2)
        meta["checkins"][1]["createTime"] = "2026-03-01 08:00:59"
        result = parser.build_attendance_candidate(raw, meta)
        self.assertTrue(all(row["remote_checkin_id"] is None for row in result["sessions"]))
        self.assertFalse(result["validation"]["can_confirm"])
        self.assertIn("source_session_coverage", {b["code"] for b in result["validation"]["blockers"]})

    async def test_batch_limit_blocks_before_partial_paid_verification(self):
        calls = []
        async def ai(*args, **kwargs):
            calls.append(1)
            return await echo_ai(*args, **kwargs)
        with patch.object(parser, "_extract_grid_pages", return_value=extracted()), patch.object(parser, "MAX_AI_CALLS", 1), patch.object(parser, "AI_BLOCK_CELLS", 2):
            result = await parser.analyze_attendance_pdf("unused.pdf", manifest(), teacher_id=1, ai_chat=ai)
        self.assertEqual(calls, [])
        self.assertFalse(result["ai_used"])
        self.assertEqual(len(result["cells"]), 4)
        self.assertIn("ai_call_limit", {b["code"] for b in result["validation"]["blockers"]})


class AttendanceSourceEdgeCases(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pause = patch.object(source, "REQUEST_INTERVAL", 0)
        pause.start(); self.addCleanup(pause.stop)

    async def test_pagination_total_drift_and_conflicting_page_alias_are_rejected(self):
        for case in ("total_drift", "page_alias"):
            with self.subTest(case=case):
                def handler(request):
                    page = int(parse_qs(request.content.decode())["page"][0])
                    payload = {"list": [{"id": f"event-{page}", "teacherScheduleId": "opaque-synthetic"}], "totalRow": 2, "totalPage": 2}
                    if case == "total_drift": payload.update(pageNumber=page, totalRow=2 if page == 1 else 3)
                    else: payload.update(pageNum=9)  # Compatibility guard; keep verified pageNumber as primary.
                    return httpx.Response(200, json=payload)
                async with httpx.AsyncClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(source.AttendanceSourceError):
                        await source.fetch_source_checkins(client, "opaque-synthetic")

    def test_truncated_pdf_requiring_xref_repair_is_not_accepted_as_complete_original(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "truncated.pdf"
            make_pdf(pdf, [([1, 2], [1, 2])])
            payload = pdf.read_bytes()
            pdf.write_bytes(payload[:payload.rfind(b"startxref")])
            with fitz.open(pdf) as recovered:
                self.assertTrue(recovered.is_repaired)
            with self.assertRaises(source.AttendanceSourceError):
                source.validate_pdf_file(pdf)

    async def test_snapshot_rejects_checkin_from_wrong_term_even_with_same_schedule_id(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "source.pdf"
            make_pdf(pdf, [([1], [1])])
            binary = pdf.read_bytes()
            def handler(request):
                route = request.url.path.rsplit("/", 1)[-1]
                if route == "teacherScheduleList": return httpx.Response(200, json=[{"id": "opaque-synthetic", "year": "2025-2026", "semester": "2", "course": "合成课程"}])
                if route == "page": return httpx.Response(200, json={"pageNumber": 1, "totalRow": 1, "totalPage": 1, "list": [{"id": "event-1", "teacherScheduleId": "opaque-synthetic", "year": "2024-2025", "semester": "1", "createTime": "2026-03-01 08:00:27"}]})
                if route == "checkinRecord": return httpx.Response(200, json={"checkinCourse": {"id": "event-1"}, "stuList": [{"no": "000001", "name": "合成1", "status": "CHECKED"}]})
                if route == "exportPdf": return httpx.Response(200, content=binary, headers={"Content-Type": "application/pdf"})
                raise AssertionError(route)
            @asynccontextmanager
            async def authenticated(access):
                async with httpx.AsyncClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)) as client:
                    yield client, None, None
            with patch.object(source, "DATA_DIR", folder), patch.object(source, "load_source_access", return_value={"username": "synthetic"}), patch.object(source, "open_authenticated_smart_classroom_client", authenticated):
                with self.assertRaises(source.AttendanceSourceError):
                    await source.fetch_attendance_source_snapshot(1, "synthetic-key", "2025-2026", 2, "opaque-synthetic")


if __name__ == "__main__":
    unittest.main()
