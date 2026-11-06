"""Real ASGI poll routes keep a blocked DB unit off the event loop."""
import asyncio
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from classroom_app.dependencies import get_current_user
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.routers import polls


class PollOffloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.loop_thread = threading.get_ident()
        self.entered, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.committed = self.closed = False
        self.commit_error = None
        owner = self
        class Connection:
            def __enter__(self):
                owner.assertNotEqual(owner.loop_thread, threading.get_ident())
                owner.entered.set()
                if not owner.release.wait(2):
                    raise AssertionError("The event loop never released the blocked poll DB unit")
                return self
            def commit(self):
                owner.assertNotEqual(owner.loop_thread, threading.get_ident())
                if owner.commit_error:
                    raise owner.commit_error
                owner.committed = True
            def __exit__(self, *args):
                owner.closed = True
        self.stack.enter_context(patch.object(polls, "get_db_connection", Connection))
        poll = {"id": 10, "status": "active", "title": "Synthetic"}
        values = {"load_classroom_snapshot": {"polls": []}, "list_class_candidates": [],
                  "load_management_list": {"polls": []}, "list_teacher_offerings": [],
                  "load_poll_detail": poll, "create_poll": poll, "vote": poll,
                  "update_poll": poll, "set_poll_status": poll, "set_poll_assignments": poll, "delete_poll": 10}
        def in_worker(value):
            def operation(*args, **kwargs):
                self.assertNotEqual(self.loop_thread, threading.get_ident())
                return value
            return operation
        for name, value in values.items():
            self.stack.enter_context(patch.object(polls.poll_service, name, side_effect=in_worker(value)))
        self.stack.enter_context(patch.object(polls.poll_service, "_load_poll", side_effect=in_worker(poll)))
        self.stack.enter_context(patch.object(polls, "_poll_class_ids", side_effect=in_worker([20])))
        self.stack.enter_context(patch.object(polls, "_notify_if_active", side_effect=in_worker(None)))
        async def broadcast(*args, **kwargs):
            self.assertEqual(self.loop_thread, threading.get_ident())
            self.assertTrue(self.committed and self.closed, "Only committed and closed DB units may broadcast")
        self.broadcast = self.stack.enter_context(patch.object(polls, "_broadcast_poll_changed", side_effect=broadcast))
        app = FastAPI()
        app.include_router(polls.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 7, "role": "teacher"}
        @app.get("/live")
        async def live():
            return {"ok": True}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://poll.test")
        self.addAsyncCleanup(self.client.aclose)

    async def responsive(self, method, path, payload=None, expected=200):
        operation = asyncio.create_task(self.client.request(method, path, json=payload))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 1))
            self.assertFalse(operation.done())
            live = await asyncio.wait_for(self.client.get("/live"), timeout=0.5)
            self.assertEqual(200, live.status_code)
            self.assertFalse(operation.done())
        finally:
            self.release.set()
        response = await operation
        self.assertEqual(expected, response.status_code, response.text)

    async def test_all_poll_reads_use_the_sync_worker_lane(self):
        for path in ("/api/polls/classrooms/20/snapshot", "/api/polls/classrooms/20/candidates",
                     "/api/polls/manage/list", "/api/polls/manage/offerings", "/api/polls/10"):
            with self.subTest(path=path):
                self.entered.clear(); self.release.clear()
                await self.responsive("GET", path)
                self.assertFalse(self.committed)
                self.broadcast.assert_not_called()

    async def test_all_mutations_commit_and_close_in_worker_before_broadcast(self):
        for method, path, payload in (("POST", "/api/polls/classrooms/20/polls", {"title": "Synthetic"}),
            ("POST", "/api/polls/manage/polls", {"title": "Synthetic"}),
            ("POST", "/api/polls/10/vote", {"option_id": 1}), ("PUT", "/api/polls/10", {"title": "Synthetic"}),
            ("POST", "/api/polls/10/status", {"status": "closed"}),
            ("POST", "/api/polls/10/assignments", {"class_offering_ids": [20]}), ("DELETE", "/api/polls/10", None)):
            with self.subTest(path=path, method=method):
                self.entered.clear(); self.release.clear()
                self.committed = self.closed = False
                self.broadcast.reset_mock()
                await self.responsive(method, path, payload)
                self.broadcast.assert_awaited_once()

    async def test_failed_commit_rolls_back_without_success_broadcast(self):
        self.commit_error = DatabaseBusyError("synthetic commit unavailable")
        await self.responsive("POST", "/api/polls/10/vote", {"option_ids": [1]}, expected=500)
        self.assertFalse(self.committed)
        self.assertTrue(self.closed)
        self.broadcast.assert_not_called()

    async def test_invalid_json_body_is_rejected_before_opening_db(self):
        response = await self.client.post("/api/polls/10/vote", json=[1])
        self.assertEqual(400, response.status_code)
        self.assertFalse(self.entered.is_set())
        self.broadcast.assert_not_called()
