import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import APIRouter, FastAPI
from classroom_app.services import agent_gateway_budget as budgets


class ToolHttpBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_timeout_keeps_actual_thread_capacity_until_business_finishes(self):
        release = threading.Event()
        completed = threading.Event()
        router = APIRouter(route_class=budgets.AgentToolBudgetRoute)

        @router.get("/work")
        def work():
            release.wait(3)
            completed.set()
            return {"ok": True}

        app = FastAPI()
        app.include_router(router)
        with patch.object(budgets, "TOOL_HTTP_TIMEOUT_SECONDS", 0.02), \
             patch.object(budgets, "reserve_tool_http_budget", return_value=SimpleNamespace(id="fixture")), \
             patch.object(budgets, "finish_tool_http_budget") as finish:
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                    result = await client.get("/work", headers={"Authorization": "Bearer lsagt_fixture"})
                self.assertEqual(504, result.status_code)
                self.assertFalse(completed.is_set())
                finish.assert_not_called()
                release.set()
                await asyncio.wait_for(asyncio.gather(*list(budgets._INFLIGHT_TOOL_WORK)), 3)
                self.assertTrue(completed.is_set())
                finish.assert_called_once()
            finally:
                release.set()
