"""ASGI regression checks for a classroom burst while the DB is waiting on a lock."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from classroom_app.routers import collaboration as routes


class CollaborationRequestIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.release = threading.Event()
        self.entered = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.thread = threading.get_ident()
        self.checkouts = []
        self.authentications = []
        self.receipt = {"id": 17, "class_offering_id": 9}

        @contextmanager
        def checkout():
            class Connection:
                committed = False

                def commit(self):
                    self.committed = True

            self.assertNotEqual(self.thread, threading.get_ident())
            connection = Connection()
            self.checkouts.append(connection)
            yield connection

        self.enterContext(patch.object(routes, "get_db_connection", checkout))
        self.enterContext(patch.object(routes, "POSTGRES_POOL_MAX", 4))
        app = FastAPI()
        app.include_router(routes.router)
        self.app = app
        def authenticate():
            self.authentications.append(threading.get_ident())
            return {"id": 7, "role": "student"}

        app.dependency_overrides[routes.get_current_user] = authenticate

        @app.get("/other-classroom")
        def unrelated_sync_request():
            return {"alive": True}

        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://isolated.test",
        ))

    def hold_writer(self, *args):
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(4):
            raise TimeoutError("the worker blocked unrelated requests")
        return {"group_id": 3, "group_name": "一组", "scheme": self.receipt}

    def snapshot_after_commit(self, connection, offering, user):
        self.assertTrue(connection.committed)
        return {"schemes": [dict(self.receipt, groups=[{"id": 3}])]}

    async def test_join_burst_queues_without_shared_threads_or_connections(self):
        self.enterContext(patch.object(routes, "join_scheme_member", self.hold_writer))
        self.enterContext(patch.object(routes, "load_collaboration_snapshot", self.snapshot_after_commit))
        jobs = [asyncio.create_task(self.client.post("/api/collaboration/schemes/17/random-join")) for _ in range(48)]
        try:
            await asyncio.wait_for(self.entered.wait(), 2)
            response = await asyncio.wait_for(self.client.get("/other-classroom"), 1)
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, len(self.checkouts))
            self.assertEqual(1, len(self.authentications))
        finally:
            self.release.set()
            results = await asyncio.gather(*jobs)
        for response in results:
            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual(data["snapshot"]["schemes"][0], data["result"]["scheme"])

    async def test_free_group_and_separate_schemes_share_bounded_capacity(self):
        self.enterContext(patch.object(routes, "create_student_group", self.hold_writer))
        self.enterContext(patch.object(routes, "join_scheme_member", self.hold_writer))
        self.enterContext(patch.object(routes, "load_collaboration_snapshot", self.snapshot_after_commit))
        tasks = [asyncio.create_task(self.client.post(
            "/api/collaboration/classrooms/9/student-groups", json={"name": "隔离测试"},
        ))]
        try:
            await asyncio.wait_for(self.entered.wait(), 2)
            tasks.extend(asyncio.create_task(self.client.post(f"/api/collaboration/schemes/{i}/random-join"))
                         for i in range(25))
            for _ in range(100):
                if len(self.checkouts) >= 2:
                    break
                await asyncio.sleep(0.01)
            response = await asyncio.wait_for(self.client.get("/other-classroom"), 1)
            self.assertEqual(200, response.status_code)
            self.assertEqual(2, len(self.checkouts))
            self.assertEqual(2, len(self.authentications))
        finally:
            self.release.set()
            results = await asyncio.gather(*tasks)
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertIn("group", results[0].json())

    async def test_slow_group_downloads_release_capacity_before_transmission(self):
        attachment = Path(self.enterContext(TemporaryDirectory())) / "synthetic.txt"
        attachment.write_text("isolated file", encoding="utf-8")
        self.enterContext(patch.object(routes, "resolve_group_file_download", return_value={
            "path": attachment, "mime_type": "text/plain", "filename": attachment.name,
        }))
        self.enterContext(patch.object(routes, "load_collaboration_snapshot", return_value={"schemes": []}))
        transmitting = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        async def slow_download(index):
            async def slow_app(scope, receive, send):
                async def slow_send(message):
                    if message["type"] == "http.response.body":
                        transmitting[index].set()
                        await release.wait()
                    await send(message)
                await self.app(scope, receive, slow_send)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=slow_app), base_url="http://isolated.test") as client:
                return await client.get("/api/collaboration/files/1/download")

        downloads = [asyncio.create_task(slow_download(i)) for i in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in transmitting)), 3)
            snapshot = await asyncio.wait_for(self.client.get("/api/collaboration/classrooms/9/snapshot"), 1)
            self.assertEqual(200, snapshot.status_code)
        finally:
            release.set()
            responses = await asyncio.gather(*downloads)
        self.assertTrue(all(r.text == "isolated file" for r in responses))
