from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from classroom_app.services.ai_provider_usage_service import (
    _read_log_tail,
    build_provider_usage_snapshot,
)


class AIProviderUsageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "ai_usage.jsonl"
        self.now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _event(
        self,
        *,
        provider: str,
        model: str,
        status: str = "success",
        age_days: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost: float | None = None,
        duration_ms: float = 1000,
    ) -> dict:
        event = {
            "event": "ai_usage",
            "finished_at": (self.now - timedelta(days=age_days)).isoformat(),
            "status": status,
            "platform": provider,
            "model": model,
            "duration_ms": duration_ms,
            "extra": {"task_type": "multimodal_grading"},
        }
        if prompt_tokens is not None or completion_tokens is not None:
            event["provider_usage"] = {
                "prompt_tokens": prompt_tokens or 0,
                "completion_tokens": completion_tokens or 0,
            }
        if cost is not None:
            event["cost_estimate"] = {
                "currency": "CNY",
                "estimated_cost": cost,
                "prompt_tokens": prompt_tokens or 0,
                "completion_tokens": completion_tokens or 0,
            }
        return event

    def test_snapshot_aggregates_recent_provider_usage_and_skips_bad_data(self):
        rows = [
            json.dumps(
                self._event(
                    provider="qwen",
                    model="qwen3.7-plus",
                    prompt_tokens=100,
                    completion_tokens=20,
                    cost=0.1234567,
                    duration_ms=1000,
                )
            ),
            "{not-json",
            json.dumps(
                self._event(
                    provider="volcengine",
                    model="doubao-seed-2-1-pro-260628",
                    status="error",
                    duration_ms=2000,
                )
            ),
            json.dumps(
                self._event(
                    provider="qwen",
                    model="expired-model",
                    age_days=60,
                    prompt_tokens=999,
                    completion_tokens=999,
                    cost=9.0,
                )
            ),
            json.dumps({"event": "unrelated", "finished_at": self.now.isoformat()}),
        ]
        self.log_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        snapshot = build_provider_usage_snapshot(
            path=self.log_path,
            days=56,
            now=self.now,
        )

        self.assertTrue(snapshot["available"])
        self.assertEqual(2, snapshot["events_read"])
        self.assertEqual(1, snapshot["malformed_lines_skipped"])
        self.assertEqual(2, snapshot["summary"]["calls"])
        self.assertEqual(1, snapshot["summary"]["successful_calls"])
        self.assertEqual(50.0, snapshot["summary"]["success_rate"])
        self.assertEqual(120, snapshot["summary"]["total_tokens"])
        self.assertEqual(1500, snapshot["summary"]["avg_duration_ms"])
        self.assertEqual(0.123457, snapshot["summary"]["estimated_cost_cny"])
        self.assertEqual(1, snapshot["summary"]["cost_known_calls"])
        self.assertEqual(
            ["qwen", "volcengine"],
            [item["provider"] for item in snapshot["model_items"]],
        )
        self.assertEqual("multimodal_grading", snapshot["task_items"][0]["task_type"])

    def test_missing_log_returns_empty_safe_snapshot(self):
        snapshot = build_provider_usage_snapshot(
            path=self.log_path,
            days=7,
            now=self.now,
        )

        self.assertFalse(snapshot["available"])
        self.assertFalse(snapshot["path_exists"])
        self.assertEqual(0, snapshot["summary"]["calls"])
        self.assertEqual([], snapshot["model_items"])

    def test_tail_reader_discards_a_partial_first_line(self):
        complete_event = json.dumps(
            self._event(provider="qwen", model="qwen3.6-flash"),
            ensure_ascii=False,
        )
        self.log_path.write_text("x" * 400 + "\n" + complete_event + "\n", encoding="utf-8")

        lines = _read_log_tail(self.log_path, max_bytes=len(complete_event.encode("utf-8")) + 16)

        self.assertEqual([complete_event], lines)

    def test_management_template_exposes_provider_cost_panel(self):
        template = Path("templates/manage/system/ai_usage.html").read_text(encoding="utf-8")

        self.assertIn("供应商与模型实际用量", template)
        self.assertIn("供应商估算费用", template)
        self.assertIn("provider_model_items", template)
        self.assertIn("日志采用有界尾读", template)


if __name__ == "__main__":
    unittest.main()
