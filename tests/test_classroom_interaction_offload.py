"""Exercise real ASGI handlers while a synthetic DB/behavior unit is blocked."""
import asyncio
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
import httpx

from classroom_app.dependencies import get_current_user
from classroom_app.routers import classroom_interactions as router
from classroom_app.db.errors import DatabaseBusyError


class InteractionOffloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.loop_thread = threading.get_ident()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.order = []
        self.behavior_threads = []
        self.block_at = "database"
        self.committed = False
        self.closed = False
        self.commit_error = None
        owner = self

        class SyntheticConnection:
            def __enter__(self):
                owner.assertNotEqual(owner.loop_thread, threading.get_ident())
                if owner.block_at == "database":
                    owner.entered.set()
                    if not owner.release.wait(2):
                        raise AssertionError("The event loop did not release the isolated DB unit")
                owner.order.append("opened")
                return self

            def execute(self, sql, params):
                owner.assertNotEqual(owner.loop_thread, threading.get_ident())
                return self

            def fetchone(self):
                return {"class_offering_id": 20}

            def commit(self):
                owner.assertNotEqual(owner.loop_thread, threading.get_ident())
                if owner.commit_error:
                    raise owner.commit_error
                owner.committed = True
                owner.order.append("committed")

            def __exit__(self, exc_type, exc, tb):
                owner.closed = True
                owner.order.append("rolled_back" if exc_type else "closed")

        self.stack.enter_context(patch.object(router, "get_db_connection", SyntheticConnection))
        activity = {"id": 10, "kind": "quiz", "title": "Synthetic", "class_offering_id": 20}
        question = {"id": 41, "activity_id": 10, "is_anonymous": 1, "status": "addressed"}
        signal = {"id": 51, "class_offering_id": 20, "signal_label": "求助", "signal_type": "help"}
        for name, value in (("create_activity", activity), ("respond_to_activity", activity),
                            ("close_activity", activity), ("submit_question", question),
                            ("resolve_question", question), ("set_help_signal", signal),
                            ("resolve_help_signal", signal), ("clear_my_help_signal", None),
                            ("mark_chat_message_useful", {"counted": True})):
            self.stack.enter_context(patch.object(router, name, return_value=value))

        def snapshot(conn, offering_id, user):
            self.assertNotEqual(self.loop_thread, threading.get_ident())
            self.assertEqual({"id": 7, "role": "student", "name": "Synthetic"}, user)
            return {"private_to": user["id"], "class_offering_id": offering_id}

        def behavior(**kwargs):
            self.behavior_threads.append(threading.get_ident())
            self.order.append("behavior")
            if self.block_at == "behavior":
                self.entered.set()
                if not self.release.wait(2):
                    raise AssertionError("Behavior intake blocked the event loop")

        async def broadcast(*args, **kwargs):
            self.assertEqual(self.loop_thread, threading.get_ident())
            self.assertTrue(self.committed)
            self.assertTrue(self.closed)
            self.order.append("broadcast")

        self.stack.enter_context(patch.object(router, "load_interaction_snapshot", snapshot))
        self.stack.enter_context(patch.object(router, "record_behavior_batch", behavior))
        self.broadcast = self.stack.enter_context(patch.object(router, "_broadcast_changed", side_effect=broadcast))
        self.app = FastAPI()
        self.app.include_router(router.router)
        self.app.dependency_overrides[get_current_user] = lambda: {"id": 7, "role": "student", "name": "Synthetic"}

        @self.app.get("/live")
        async def live():
            return {"status": "ok"}

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://synthetic.test")
        self.addAsyncCleanup(self.client.aclose)

    async def assert_responsive(self, method, path, payload=None):
        pending = asyncio.create_task(self.client.request(method, "/api/classroom-interactions" + path, json=payload))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 1))
            self.assertFalse(pending.done(), "The handler blocked its ASGI event loop until the slow unit finished")
            response = await asyncio.wait_for(self.client.get("/live"), timeout=0.5)
            self.assertEqual(200, response.status_code)
            self.assertFalse(pending.done())
        finally:
            self.release.set()
        response = await pending
        self.assertEqual(200, response.status_code)
        if "snapshot" in response.json():
            self.assertEqual({"private_to": 7, "class_offering_id": 20}, response.json()["snapshot"])
        return response

    async def test_snapshot_waits_off_event_loop(self):
        await self.assert_responsive("GET", "/classrooms/20/snapshot")
        self.assertFalse(self.committed)
        self.broadcast.assert_not_called()

    async def test_every_mutation_commits_before_behavior_and_broadcast(self):
        endpoints = [
            ("/classrooms/20/messages/3/useful", None),
            ("/classrooms/20/activities", {"kind": "quiz"}),
            ("/activities/10/respond", {"option_ids": [1]}),
            ("/activities/10/questions", {"text": "Synthetic", "is_anonymous": True}),
            ("/activities/10/close", None),
            ("/questions/41/resolve", {"status": "addressed"}),
            ("/classrooms/20/signals", {"signal_type": "help"}),
            ("/classrooms/20/signals/clear", None),
            ("/signals/51/resolve", None),
        ]
        for path, payload in endpoints:
            with self.subTest(path=path):
                self.entered.clear(); self.release.clear()
                self.order.clear(); self.behavior_threads.clear()
                self.committed = self.closed = False
                self.broadcast.reset_mock()
                await self.assert_responsive("POST", path, payload)
                self.assertEqual(1, self.broadcast.call_count)
                self.assertLess(self.order.index("committed"), self.order.index("closed"))
                self.assertLess(self.order.index("closed"), self.order.index("broadcast"))
                if self.behavior_threads:
                    self.assertTrue(all(value != self.loop_thread for value in self.behavior_threads))
                    self.assertLess(self.order.index("closed"), self.order.index("behavior"))

    async def test_behavior_intake_also_waits_off_event_loop(self):
        self.block_at = "behavior"
        await self.assert_responsive("POST", "/activities/10/respond", {"option_ids": [1]})
        self.assertTrue(self.behavior_threads)
        self.assertTrue(all(value != self.loop_thread for value in self.behavior_threads))

    async def test_commit_failure_never_broadcasts_success(self):
        self.block_at = None
        self.commit_error = DatabaseBusyError("Synthetic commit failure")
        with self.assertRaises(DatabaseBusyError):
            await self.client.post("/api/classroom-interactions/activities/10/respond", json={})
        self.broadcast.assert_not_called()
        self.assertEqual([], self.behavior_threads)
        self.assertIn("rolled_back", self.order)

    async def test_permission_failure_and_invalid_json_keep_existing_errors(self):
        self.block_at = None
        with patch.object(router, "respond_to_activity", side_effect=HTTPException(403, "Synthetic ownership refusal")):
            denied = await self.client.post("/api/classroom-interactions/activities/10/respond", json={})
        self.assertEqual(403, denied.status_code)
        self.assertEqual("Synthetic ownership refusal", denied.json()["detail"])
        self.broadcast.assert_not_called()
        self.assertFalse(self.committed)
        self.order.clear()
        invalid = await self.client.post("/api/classroom-interactions/activities/10/respond", json=[])
        self.assertEqual(400, invalid.status_code)
        self.assertEqual([], self.order)
