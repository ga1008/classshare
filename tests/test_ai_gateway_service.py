from __future__ import annotations

import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
from classroom_app.services import ai_gateway_service as gateway


class _FakeResponse:
    status_code = 200
    text = '{"status":"success"}'

    def json(self):
        return {"status": "success"}


class _FakeAIClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def post(self, endpoint: str, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(str(kwargs.get("json", {}).get("name") or endpoint))
        await asyncio.sleep(0.01)
        self.active -= 1
        return _FakeResponse()


class AIGatewayServiceTests(unittest.TestCase):
    def test_schema_creates_ai_usage_log(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            ensure_cultivation_progress_schema(conn, engine="sqlite")
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(ai_usage_log)").fetchall()
            }
            self.assertIn("task_type", columns)
            self.assertIn("priority", columns)
            self.assertIn("duration_ms", columns)
            self.assertIn("prompt_tokens_estimate", columns)
        finally:
            conn.close()

    def test_gateway_prioritizes_waiting_jobs_and_records_usage(self):
        records: list[dict] = []

        async def scenario():
            gateway._GATEWAYS_BY_LOOP.clear()
            client = _FakeAIClient()
            with (
                patch.object(gateway, "AI_GATEWAY_MAX_CONCURRENT", 1),
                patch.object(gateway, "record_ai_usage", side_effect=lambda **payload: records.append(payload) or 1),
            ):
                tasks = [
                    asyncio.create_task(
                        gateway.ai_gateway_post(
                            client,
                            "/api/ai/chat",
                            json_payload={"name": "p2"},
                            task_type="background",
                            priority="P2",
                        )
                    ),
                    asyncio.create_task(
                        gateway.ai_gateway_post(
                            client,
                            "/api/ai/chat",
                            json_payload={"name": "p0"},
                            task_type="interactive",
                            priority="P0",
                        )
                    ),
                    asyncio.create_task(
                        gateway.ai_gateway_post(
                            client,
                            "/api/ai/chat",
                            json_payload={"name": "p1"},
                            task_type="daily",
                            priority="P1",
                        )
                    ),
                ]
                await asyncio.gather(*tasks)
                self.assertEqual(["p0", "p1", "p2"], client.calls)
                self.assertEqual(1, client.max_active)

            for active_gateway in list(gateway._GATEWAYS_BY_LOOP.values()):
                for worker in active_gateway.workers:
                    worker.cancel()
                await asyncio.gather(*active_gateway.workers, return_exceptions=True)
            gateway._GATEWAYS_BY_LOOP.clear()

        asyncio.run(scenario())
        self.assertEqual(["P0", "P1", "P2"], [item["priority"] for item in records])
        self.assertTrue(all(item["status"] == "success" for item in records))


if __name__ == "__main__":
    unittest.main()
