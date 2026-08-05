import json
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from classroom_app.services.academic_calendar_sync_service import (
    _fetch_academic_alignment_candidates,
    _parse_academic_calendar_alignment,
    _resolve_calendar_sync_status,
    is_semester_calendar_sync_active,
)
from classroom_app.services.academic_service import (
    _serialize_calendar_day_row,
    build_holiday_lookup,
    serialize_semester_row,
)
from classroom_app.services.semester_identity_service import SemesterIdentity


class AcademicCalendarEndpointTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_real_zfsoft_calendar_header(self):
        candidates = _parse_academic_calendar_alignment(
            "<table><caption>2025-2026学年2学期(2026-03-09至2026-07-12)</caption></table>",
            source_url="https://jwxt.gxufl.com/pkgl/xlgl_cxXlIndex.html",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "2025-2026第二学期")
        self.assertEqual(candidates[0].start_date, "2026-03-09")
        self.assertEqual(candidates[0].end_date, "2026-07-12")
        self.assertEqual(candidates[0].week_count, 18)

    async def test_queries_target_term_with_verified_xnm_xqm_and_gnmkdm(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                text="<div>2026-2027学年2学期(2027-03-01至2027-07-11)</div>",
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://jwxt.gxufl.com",
            transport=httpx.MockTransport(handler),
        ) as client:

            @asynccontextmanager
            async def fake_authenticated_client(_access_payload):
                yield client, SimpleNamespace(base_url="https://jwxt.gxufl.com"), {}

            with patch(
                "classroom_app.services.academic_calendar_sync_service.open_authenticated_academic_client",
                fake_authenticated_client,
            ):
                candidates, sources = await _fetch_academic_alignment_candidates(
                    {"credential": "configured"},
                    expected_identity=SemesterIdentity(2026, 2),
                )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/pkgl/xlgl_cxXlIndex.html")
        self.assertEqual(calls[0].url.params["xnm"], "2026")
        self.assertEqual(calls[0].url.params["xqm"], "12")
        self.assertEqual(calls[0].url.params["gnmkdm"], "N210505")
        self.assertEqual(candidates[0].start_date, "2027-03-01")
        self.assertEqual(candidates[0].confidence, 0.99)
        self.assertEqual(sources[0]["status"], "success")


class SemesterCalendarStateTests(unittest.TestCase):
    def test_official_alignment_is_complete_without_ai_dependency(self):
        self.assertEqual(_resolve_calendar_sync_status(has_academic_alignment=True), "synced")
        self.assertEqual(_resolve_calendar_sync_status(has_academic_alignment=False), "generated")

    def test_stale_running_state_does_not_lock_semester(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(
            is_semester_calendar_sync_active(
                {"calendar_sync_status": "running", "updated_at": now - timedelta(minutes=2)},
                reference_time=now,
            )
        )
        self.assertFalse(
            is_semester_calendar_sync_active(
                {"calendar_sync_status": "running", "updated_at": now - timedelta(minutes=30)},
                reference_time=now,
            )
        )

    def test_semester_temporal_status_is_precise(self):
        common = {
            "id": 1,
            "name": "2025-2026第二学期",
            "start_date": "2026-03-09",
            "end_date": "2026-07-12",
            "week_count": 18,
            "calendar_sync_status": "synced",
        }
        self.assertEqual(
            serialize_semester_row(common, reference_date=date(2026, 8, 5))["temporal_status_label"],
            "已结束",
        )
        self.assertEqual(
            serialize_semester_row(common, reference_date=date(2026, 3, 20))["temporal_status_label"],
            "进行中",
        )
        self.assertEqual(
            serialize_semester_row(common, reference_date=date(2026, 2, 1))["temporal_status_label"],
            "未开始",
        )

    def test_makeup_mapping_survives_calendar_day_serialization(self):
        info = build_holiday_lookup([2026])["2026-05-09"]
        self.assertEqual(info["makeup_for_date"], "2026-05-05")
        self.assertEqual(info["makeup_for_weekday"], "周二")
        self.assertIn("补 5 月 5 日", info["label"])
        serialized = _serialize_calendar_day_row(
            {
                "date": "2026-05-09",
                "day_type": "workday",
                "label": info["label"],
                "source": "built_in",
                "source_url": info["source_url"],
                "confidence": 0.96,
                "week_index": 9,
                "weekday": 5,
                "metadata_json": json.dumps(info, ensure_ascii=False),
            }
        )
        self.assertEqual(serialized["makeup_for_date"], "2026-05-05")
        self.assertEqual(serialized["makeup_for_weekday"], "周二")


if __name__ == "__main__":
    unittest.main()
