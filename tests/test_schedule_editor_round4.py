"""课表编辑器第四轮：学期结束日期禁放、证明材料、AI 调课原因兜底。"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from classroom_app import config
from classroom_app.db import schema_schedule_availability, schema_schedule_editor
from classroom_app.services import schedule_editor_service as editor
from classroom_app.services import schedule_reason_service as reason
from tests.test_schedule_editor import lesson, overview


class TermEndAndProofTests(unittest.TestCase):
    def setUp(self):
        schema_schedule_editor.reset_schema_ready_for_tests()
        schema_schedule_availability.reset_schema_ready_for_tests()
        clock = patch.object(editor, "china_today", return_value=date(2026, 3, 1))
        clock.start(); self.addCleanup(clock.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        schema_schedule_editor.ensure_schedule_editor_schema(self.conn)
        self.conn.execute("CREATE TABLE academic_semesters (id INTEGER PRIMARY KEY, start_date TEXT, end_date TEXT)")
        self.conn.execute("INSERT INTO academic_semesters VALUES (1, '2026-03-02', '2026-06-14')")
        self.overview = overview({5: [lesson("ev-a", week=5, weekday=4, sections=(4, 5))]})
        self.overview["selected_term"]["semester_id"] = 1
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        data_dir = patch.object(config, "DATA_DIR", Path(self.tmp.name)); data_dir.start(); self.addCleanup(data_dir.stop)

    def test_calendar_carries_term_end_and_dates_after_it_are_rejected(self):
        calendar = editor.build_term_calendar(self.conn, editor._term_context(self.overview))
        self.assertEqual((calendar["term_start"], calendar["term_end"]), ("2026-03-02", "2026-06-14"))
        self.assertEqual(editor.build_editor_payload(self.conn, 1, self.overview)["calendar"]["term_end"], "2026-06-14")
        # 第 16 周周一 = 2026-06-15，学期已于 06-14 结束
        with self.assertRaisesRegex(editor.ScheduleEditError, "学期结束日期"):
            editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", "week": 16, "weekday": 1, "start_section": 4})
        draft = editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", "week": 15, "weekday": 1, "start_section": 4})
        self.assertEqual(draft["proposed"]["date"], "2026-06-08")

    def test_proofs_are_stored_listed_downloadable_and_removable(self):
        draft = editor.save_draft(self.conn, 1, self.overview, {"event_key": "ev-a", "week": 7, "weekday": 1, "start_section": 4})
        self.assertEqual(draft["proofs"], [])
        with self.assertRaisesRegex(editor.ScheduleEditError, "仅支持"):
            editor.add_draft_proof(self.conn, 1, draft["id"], filename="virus.exe", content=b"x")
        with self.assertRaisesRegex(editor.ScheduleEditError, "为空"):
            editor.add_draft_proof(self.conn, 1, draft["id"], filename="notice.pdf", content=b"")
        record = editor.add_draft_proof(self.conn, 1, draft["id"], filename="../放假通知.pdf", content=b"%PDF-1.4 synthetic")
        self.assertEqual((record["name"], record["size"], record["stored"].endswith(".pdf")), ("放假通知.pdf", 18, True))
        stored = editor.get_draft(self.conn, 1, draft["id"])["proofs"]
        self.assertEqual([p["id"] for p in stored], [record["id"]])
        found, path = editor.find_draft_proof(self.conn, 1, draft["id"], record["id"])
        self.assertTrue(path.is_file() and str(path).startswith(str(Path(self.tmp.name))))
        with self.assertRaises(editor.ScheduleEditError):
            editor.find_draft_proof(self.conn, 2, draft["id"], record["id"])  # another teacher: draft not visible
        with self.assertRaisesRegex(editor.ScheduleEditError, "无效"):
            editor.draft_proof_path(1, draft["id"], "../../etc/passwd")
        self.assertEqual(editor.remove_draft_proof(self.conn, 1, draft["id"], record["id"])["id"], record["id"])
        self.assertFalse(path.exists())
        self.assertIsNone(editor.remove_draft_proof(self.conn, 1, draft["id"], record["id"]))


class ReasonSuggestionTests(unittest.TestCase):
    def test_fallback_reason_reads_the_calendar_facts(self):
        base = {"original": {"date": "2026-10-01", "sections": [2, 3]}, "proposed": {"date": "2026-10-10", "sections": [2, 3]}}
        self.assertIn("国庆节", reason.fallback_reason({**base, "original_day": {"kind": "holiday", "label": "国庆节"}}))
        self.assertIn("调休", reason.fallback_reason({**base, "proposed_day": {"kind": "workday", "label": "国庆节调休上课"}}))
        self.assertEqual(reason.fallback_reason({"original": {"date": "2026-10-01", "sections": [2, 3]}, "proposed": {"date": "2026-10-01", "sections": [2, 3]}}), "因教学需要更换上课教室")
        self.assertEqual(reason.fallback_reason(base), "因教学安排调整，课程调换上课时间")

    def test_suggest_uses_ai_text_when_available_and_falls_back_otherwise(self):
        context = {"course_name": "Python程序设计", "original": {"date": "2026-10-01", "sections": [2, 3], "week": 4}, "proposed": {"date": "2026-10-10", "sections": [2, 3], "week": 5},
                   "original_day": {"kind": "holiday", "label": "国庆节"}}

        class FakeResponse:
            def __init__(self, payload): self.payload = payload
            def raise_for_status(self): return None
            def json(self): return self.payload

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def post(self, path, json):
                assert json["task_type"] == "fast_text_response"
                return FakeResponse({"status": "success", "response_text": "“国庆节放假，课程顺延至调休日补上。”\n"})

        with patch.object(reason.httpx, "AsyncClient", FakeClient):
            result = asyncio.run(reason.suggest_reason(context))
        self.assertEqual(result, {"reason": "国庆节放假，课程顺延至调休日补上", "source": "ai"})

        class BrokenClient(FakeClient):
            async def post(self, path, json): raise RuntimeError("gateway down")

        with patch.object(reason.httpx, "AsyncClient", BrokenClient):
            result = asyncio.run(reason.suggest_reason(context))
        self.assertEqual(result["source"], "fallback")
        self.assertIn("国庆节", result["reason"])


if __name__ == "__main__":
    unittest.main()
