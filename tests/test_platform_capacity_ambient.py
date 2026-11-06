"""Ambient load cadence, identity and shutdown using clocks/clients only."""
from __future__ import annotations

import asyncio
import inspect
import json
import unittest

from tools.platform_capacity_ambient import AmbientLoad, DEFAULT_INTERVALS


class Clock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value


class Response:
    def __init__(self, status=200, body=None, error=None):
        self.status_code, self.body, self.error = status, body, error
    def json(self):
        if self.error: raise self.error
        return self.body


class Client:
    base_url = "http://127.0.0.1:18767/"
    def __init__(self, handler):
        self.handler, self.records, self.cancelled = handler, [], 0
        self.active, self.max_active = {}, {}
    async def send(self, method, path, **options):
        self.records.append((method, path, options))
        key = (path, options["headers"]["Cookie"])
        self.active[key] = self.active.get(key, 0) + 1
        self.max_active[key] = max(self.active[key], self.max_active.get(key, 0))
        try:
            value = self.handler(method, path, options)
            return await value if inspect.isawaitable(value) else value
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active[key] -= 1
    async def get(self, path, **options): return await self.send("GET", path, **options)
    async def post(self, path, **options): return await self.send("POST", path, **options)


def receipt(options, **changes):
    return {"status": "success", "batch_id": options["json"]["batch_id"],
            "write_mode": "durable_queued", **changes}


class AmbientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.loads = []
    async def asyncTearDown(self):
        for load in self.loads:
            await load.stop()
    def make(self, handler, *, tokens=None, **options):
        client = Client(handler)
        load = AmbientLoad(client, tokens or ["synthetic-a"], 42, synthetic=True, **options)
        self.loads.append(load)
        return load, client
    async def ticker(self, load, index, channel, count):
        waits = []
        async def wait(seconds):
            waits.append((load.clock(), seconds))
            if len(waits) > count:
                return True
            if len(waits) > 1:
                load.clock.value += seconds
            return False
        load._wait = wait
        if load.started_at is None:
            load.started_at = load.clock()
        await load._ticker(index, channel, 0)
        return waits

    async def test_cadence_is_single_flight_completion_plus_interval(self):
        clock, starts = Clock(), []
        def respond(_method, _path, _options):
            starts.append(clock())
            clock.value += 3
            return Response(200, {})
        load, client = self.make(respond, clock=clock)
        waits = await self.ticker(load, 0, "bell", 2)
        self.assertEqual(starts, [100, 118])  # 3s response + 15s interval
        self.assertEqual(waits[1], (103, 15))
        self.assertEqual(list(client.max_active.values()), [1])
        snapshot = load.snapshot()
        self.assertEqual((snapshot["started_requests"], snapshot["completed_requests"]), (2, 2))
        self.assertEqual(snapshot["latencies"]["ambient-bell"]["p50_ms"], 3000)

    async def test_initial_phases_stagger_identities_and_each_flow(self):
        load, client = self.make(lambda *_args: Response(), tokens=["a", "b", "c"], clock=Clock())
        phases = []
        async def stop_in_initial_wait(seconds):
            phases.append(seconds)
            return True
        load._wait = stop_in_initial_wait
        load.start()
        await asyncio.gather(*load.tasks)
        expected = [DEFAULT_INTERVALS[channel] * (index + channel_index / 3) / 3
                    for index in range(3) for channel_index, channel in enumerate(DEFAULT_INTERVALS)]
        self.assertEqual(len(phases), len(expected))
        for actual, target in zip(phases, expected):
            self.assertAlmostEqual(actual, target)
        self.assertEqual(len(load.tasks), 9)
        self.assertEqual(client.records, [])

    async def test_heartbeat_binds_cookie_account_class_and_uses_unique_immutable_ids(self):
        load, client = self.make(lambda _m, _p, options: Response(202, receipt(options)),
            tokens=["a", "b"], user_keys=["student:10", "student:11"], clock=Clock(), wall_time=lambda: 1_800_000_000.0)
        await self.ticker(load, 0, "heartbeat", 2)
        await self.ticker(load, 1, "heartbeat", 1)
        bodies = [options["json"] for _method, _path, options in client.records]
        self.assertEqual(len({body["batch_id"] for body in bodies}), 3)
        for index, (method, path, options) in enumerate(client.records):
            actor = 0 if index < 2 else 1
            self.assertEqual((method, path), ("POST", "/api/classrooms/42/behavior/batch"))
            self.assertEqual(options["headers"], {"Cookie": "access_token=" + ("a" if actor == 0 else "b")})
            self.assertEqual(options["json"]["user_key"], f"student:{10 + actor}")
            self.assertRegex(options["json"]["batch_id"], r"^b1_1800000000000_ambient[A-Za-z0-9_]+$")
            self.assertEqual(options["json"]["events"][0]["payload"], {
                "visibility_state": "visible", "focused": True, "idle_seconds": 0, "ai_panel_open": False})
        self.assertEqual(load.snapshot()["error_count"], 0)

    async def test_stop_wakes_sleepers_and_waits_for_inflight_post_without_cancelling(self):
        entered, release, clock = asyncio.Event(), asyncio.Event(), Clock()
        async def respond(method, _path, options):
            self.assertEqual(method, "POST")
            entered.set()
            await release.wait()
            return Response(202, receipt(options))
        load, client = self.make(respond, clock=clock)
        load.started_at = clock()
        heartbeat = asyncio.create_task(load._ticker(0, "heartbeat", 0))
        sleeper = asyncio.create_task(load._ticker(0, "blog", 999))
        load.tasks = [heartbeat, sleeper]
        await entered.wait()
        clock.value = 120
        stopping = asyncio.create_task(load.stop())
        await asyncio.sleep(0)
        self.assertTrue(load.stop_event.is_set())
        self.assertFalse(stopping.done())
        self.assertEqual(load.snapshot()["completed_requests"], 0)
        await asyncio.wait_for(sleeper, 1)
        clock.value = 125
        release.set()
        await asyncio.wait_for(stopping, 1)
        result = load.snapshot()
        self.assertEqual((result["scheduling_seconds"], result["drain_seconds"], result["elapsed_seconds"]), (20, 5, 25))
        self.assertEqual((result["started_requests"], result["completed_requests"], result["error_count"]), (1, 1, 0))
        self.assertEqual(client.cancelled, 0)
        self.assertEqual(len(client.records), 1)
        clock.value = 140
        await load.stop()
        self.assertEqual(load.snapshot()["elapsed_seconds"], 25)

    async def test_http_transport_parse_and_receipt_errors_count_once_and_cannot_certify_success(self):
        clock, sequence = Clock(), 0
        def respond(_method, _path, options):
            nonlocal sequence
            sequence += 1
            clock.value += .25
            if sequence == 1: return Response(503)
            if sequence == 2: return Response(200, receipt(options, batch_id="wrong-batch"))
            if sequence == 3: return Response(202, receipt(options, degraded=True))
            if sequence == 4: return Response(200, error=ValueError("sensitive-token-not-for-report"))
            if sequence == 5: raise ConnectionError("sensitive-token-not-for-report")
            return Response(200, receipt(options))
        load, _client = self.make(respond, clock=clock)
        await self.ticker(load, 0, "heartbeat", 6)
        result = load.snapshot()
        self.assertEqual((result["started_requests"], result["completed_requests"], result["error_count"]), (6, 6, 5))
        self.assertEqual(sum(item["count"] for item in result["statuses"]), 6)
        self.assertEqual(sum(item["error"] == "invalid_durable_receipt" for item in result["faults"]), 2)
        self.assertNotIn("sensitive-token", json.dumps(result))

    async def test_stop_before_start_never_schedules_and_defaults_model_twenty_rps_for_200(self):
        load, client = self.make(lambda *_args: Response(), tokens=[f"synthetic-{index}" for index in range(200)])
        load.request_stop()
        with self.assertRaises(RuntimeError): load.start()
        await load.stop()
        report = load.snapshot()
        self.assertEqual(report["nominal_rps_before_response_delay"], 20)
        self.assertEqual(report["started_requests"], 0)
        self.assertEqual(client.records, [])


class AmbientBoundaryTests(unittest.TestCase):
    def test_only_explicit_synthetic_loopback_origins_and_distinct_identity_tokens_are_allowed(self):
        client = Client(lambda *_args: Response())
        for origin in ("http://example.com:18767", "http://127.0.0.1", "http://u:p@127.0.0.1:18767",
                       "http://127.0.0.1:18767/prod", "http://localhost:18767", "http://127.0.0.1:18767/?x=1"):
            client.base_url = origin
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                AmbientLoad(client, ["a"], 42, synthetic=True)
        client.base_url = "http://127.0.0.1:18767/"
        for tokens in (["a", "a"], ["bad\r\nCookie: x"], [""], []):
            with self.subTest(tokens=tokens), self.assertRaises(ValueError):
                AmbientLoad(client, tokens, 42, synthetic=True)
        with self.assertRaises(ValueError): AmbientLoad(client, ["a"], 42)
        with self.assertRaises(ValueError): AmbientLoad(client, ["a"], 42, synthetic=True, user_keys=["teacher:10"])
        with self.assertRaises(ValueError): AmbientLoad(client, ["a"], 42, synthetic=True, intervals={"bell": 0})
        with self.assertRaises(ValueError): AmbientLoad(client, ["a"], True, synthetic=True)
