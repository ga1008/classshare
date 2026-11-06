"""Real asyncio drain/single-flight checks with a network-free capacity client."""
import asyncio
import time
import unittest

from tools.platform_capacity_ambient import AmbientLoad


class Response:
    status_code = 202
    def __init__(self, body):
        self.body = body
    def json(self):
        return {"status": "success", "batch_id": self.body["batch_id"], "write_mode": "durable_queued", "degraded": False}


class Client:
    base_url = "http://127.0.0.1:18767"
    def __init__(self):
        self.calls = []
    async def get(self, path, **options):
        self.calls.append((path, options))
        return type("Summary", (), {"status_code": 200})()
    async def post(self, path, **options):
        self.calls.append((path, options))
        return Response(options["json"])


class AmbientDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_wakes_all_600_tickers_without_cancelling_them(self):
        client = Client()
        load = AmbientLoad(client, [f"synthetic-{i}" for i in range(200)], 7, synthetic=True)
        load.start()
        await asyncio.sleep(.02)
        await asyncio.wait_for(load.stop(), 1)
        report = load.snapshot()
        self.assertEqual(report["ticker_count"], 600)
        self.assertTrue(all(task.done() and not task.cancelled() for task in load.tasks))
        self.assertEqual(report["started_requests"], report["completed_requests"])
        self.assertEqual(report["error_count"], 0)

    async def test_stop_drains_inflight_post_once_and_preserves_immutable_identity_body(self):
        started, release = asyncio.Event(), asyncio.Event()
        client = Client()
        post = client.post
        cancelled = []
        async def gated_post(path, **options):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return await post(path, **options)
        client.post = gated_post
        load = AmbientLoad(client, ["synthetic-a"], 7, synthetic=True, user_keys=["student:73"],
                           intervals={"bell": .03, "blog": .03, "heartbeat": .03})
        load.start()
        await asyncio.wait_for(started.wait(), 1)
        stopping = asyncio.create_task(load.stop())
        try:
            await asyncio.sleep(.02)
            self.assertFalse(stopping.done())
            self.assertFalse(cancelled)
        finally:
            release.set()
            await asyncio.wait_for(stopping, 1)
        posts = [(path, options) for path, options in client.calls if path.endswith("behavior/batch")]
        self.assertEqual(len(posts), 1)
        path, options = posts[0]
        self.assertEqual(path, "/api/classrooms/7/behavior/batch")
        self.assertEqual(options["headers"]["Cookie"], "access_token=synthetic-a")
        self.assertEqual(options["json"]["user_key"], "student:73")
        self.assertRegex(options["json"]["batch_id"], r"^b1_\d{13}_ambient[a-f0-9]{16}_0_1$")
        self.assertEqual(options["json"]["events"][0]["payload"],
                         {"visibility_state": "visible", "focused": True, "idle_seconds": 0, "ai_panel_open": False})
        self.assertEqual(load.snapshot()["error_count"], 0)

    async def test_interval_begins_after_response_completion_and_never_overlaps(self):
        client = Client()
        starts, completed = [], []
        active, peak = 0, 0
        enough = asyncio.Event()
        get = client.get
        async def slow_get(path, **options):
            nonlocal active, peak
            if path == "/api/message-center/summary":
                starts.append(time.perf_counter())
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(.03)
                completed.append(time.perf_counter())
                active -= 1
                if len(completed) == 3:
                    enough.set()
            return await get(path, **options)
        client.get = slow_get
        load = AmbientLoad(client, ["synthetic-a"], 7, synthetic=True,
                           intervals={"bell": .03, "blog": 60, "heartbeat": 60})
        load.start()
        try:
            await asyncio.wait_for(enough.wait(), 1)
        finally:
            await load.stop()
        self.assertEqual(peak, 1)
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(next_start - end >= .025 for next_start, end in zip(starts[1:], completed)))
