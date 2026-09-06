"""Resource sampler unit checks: synthetic psutil, no PostgreSQL or load run."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from tools import career_mixed_load_probe as probe


class FakePsutil:
    Error = RuntimeError

    def __init__(self, *, slow=False, init_error=False):
        self.threads = set()
        self.main_thread = threading.get_ident()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.slow = slow
        self.init_error = init_error
        self.calls = []

    def record(self, call):
        ident = threading.get_ident()
        if ident == self.main_thread:
            raise AssertionError("psutil must never run on the request event-loop thread")
        self.threads.add(ident)
        self.calls.append(call)

    def Process(self, pid=None):
        self.record(("process", pid))
        if self.init_error:
            raise RuntimeError("synthetic initialization failure")
        source = self

        class Process:
            def cpu_percent(self):
                source.record(("process_cpu", pid))
                return 12 if pid is None else 2

            def cpu_times(self):
                source.record(("cpu_times", pid))
                return (2, 3, 0)

            def memory_info(self):
                source.record(("memory", pid))
                if source.slow and not source.entered.is_set():
                    source.entered.set()
                    if not source.release.wait(3):
                        raise AssertionError("Test did not release the deliberately slow sample")
                return SimpleNamespace(rss=(64 if pid is None else 16)*1048576)

        return Process()

    def cpu_percent(self):
        self.record(("host_cpu", None))
        return 42

    def cpu_count(self, logical=True):
        self.record(("host_count", logical))
        return 20 if logical else 10

    def virtual_memory(self):
        self.record(("host_ram", None))
        return SimpleNamespace(total=16*1073741824)


class ResourceSamplerTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_sampling(self, fake):
        async def entered():
            while not fake.entered.is_set():
                await asyncio.sleep(.001)
        await asyncio.wait_for(entered(), 1)

    def assert_stopped(self, sampler):
        self.assertTrue(sampler._shutdown_task.done())
        self.assertTrue(all(not thread.is_alive() for thread in sampler._executor._threads))

    async def test_slow_sampler_keeps_real_asgi_request_event_loop_operable_and_all_metrics(self):
        fake = FakePsutil(slow=True)
        app = FastAPI()

        @app.get("/synthetic-read")
        async def read():
            await asyncio.sleep(0)
            return {"ok": True}

        with patch.object(probe, "psutil", fake):
            async with probe.ResourceSampler() as sampler:
                pending = asyncio.create_task(sampler.sample((11, 12)))
                try:
                    await self.wait_for_sampling(fake)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
                        responses = await asyncio.wait_for(asyncio.gather(*(client.get("/synthetic-read") for _ in range(10))), .5)
                    self.assertTrue(all(response.status_code == 200 for response in responses))
                    self.assertFalse(pending.done(), "Requests must finish while the fake sampler is still blocked")
                    self.assertFalse(fake.release.is_set())
                finally:
                    fake.release.set()
                sample = await pending
                self.assertEqual(sample["app_and_generator_rss_mb"], 64)
                self.assertEqual(sample["app_and_generator_cpu_percent_one_core_100"], 12)
                self.assertEqual(sample["host_cpu_percent"], 42)
                self.assertEqual(sample["pool_backend_rss_mb"], 32)
                self.assertEqual(sample["pool_backend_cpu_percent_one_core_100"], 4)
                self.assertGreater(sample["resource_sampling_duration_ms"], 0)
                self.assertGreaterEqual(sample["resource_sampling_await_ms"], sample["resource_sampling_duration_ms"])
                self.assertEqual(await sampler.cpu_seconds(), 5)
                self.assertEqual(sampler.machine, {"logical_cpus":20,"physical_cpus":10,"ram_gb":16})
                await asyncio.gather(*(sampler.sample((11, 12)) for _ in range(4)))
                self.assertEqual(len(fake.threads), 1, "Initial/baseline/final/process/host measurements must share one thread")
                self.assertEqual(fake.calls.count(("process", 11)), 1, "Keep process CPU baselines across samples")
            self.assert_stopped(sampler)

    async def test_cancellation_waits_for_owned_sampler_then_releases_thread(self):
        fake = FakePsutil(slow=True)
        sampler = probe.ResourceSampler()

        async def run():
            async with sampler:
                await sampler.sample((11,))

        with patch.object(probe, "psutil", fake):
            task = asyncio.create_task(run())
            try:
                await self.wait_for_sampling(fake)
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done(), "Cancellation must not detach an in-flight sampler thread")
            finally:
                fake.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_stopped(sampler)

    async def test_initialization_failure_still_closes_owned_executor(self):
        sampler = probe.ResourceSampler()
        with patch.object(probe, "psutil", FakePsutil(init_error=True)):
            with self.assertRaisesRegex(RuntimeError, "synthetic initialization"):
                async with sampler:
                    self.fail("Initialization should have failed")
        self.assert_stopped(sampler)


if __name__ == "__main__":
    unittest.main()
