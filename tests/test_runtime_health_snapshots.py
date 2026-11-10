import asyncio
import threading
import unittest
from unittest.mock import patch

import httpx
from classroom_app import app as application
from classroom_app.services.runtime_health_service import RuntimeHealthSnapshots
from classroom_app.services import runtime_metrics_service as metrics


class RuntimeHealthSnapshotTests(unittest.TestCase):
    def test_stuck_diagnostic_does_not_block_readers_or_spawn_duplicate_collectors(self):
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            release.wait(5)
            return {"count": 1}

        health = RuntimeHealthSnapshots({"database_probe": lambda: {"ok": True}, "slow": slow})
        health.start()
        try:
            self.assertTrue(entered.wait(2))
            thread = health._thread
            for _ in range(30):
                health.start()
                snapshot = health.snapshot()
                self.assertIs(health._thread, thread)
                self.assertEqual({"ok": True}, snapshot["database_probe"])
                self.assertIn("slow", snapshot["diagnostics"]["pending"])
        finally:
            release.set()
            health.stop()

    def test_failure_preserves_last_success_and_reports_error_without_secrets(self):
        provider = lambda: {"ok": True}
        health = RuntimeHealthSnapshots({"database_probe": provider})
        health.refresh()
        snapshot = health.snapshot()
        snapshot["database_probe"]["ok"] = False
        self.assertTrue(health.snapshot()["database_probe"]["ok"])
        health.providers["database_probe"] = lambda: (_ for _ in ()).throw(RuntimeError("secret DSN"))
        health.refresh()
        snapshot = health.snapshot()
        self.assertEqual({"database_probe": "RuntimeError"}, snapshot["diagnostics"]["errors"])
        self.assertTrue(snapshot["database_probe"]["ok"])
        self.assertNotIn("secret DSN", str(snapshot))

    def test_unknown_raw_paths_never_enter_route_cardinality(self):
        for path in ("/materials/render-content/secret-grant/a.js", "/scanner/1234"):
            self.assertEqual("<unmatched>", metrics._normalize_route(None, path))
        self.assertEqual("/classroom/{id}", metrics._normalize_route("/classroom/{id}", "/classroom/1"))


class RuntimeProbeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovered_worker_keeps_failure_history_without_reporting_current_failure(self):
        import importlib
        module = importlib.import_module("classroom_app.app")
        health = RuntimeHealthSnapshots({"database_probe": lambda: {"ok": True}})
        health.refresh()
        stats = {"alive": True, "last_error": "", "quarantined_count": 0,
                 "queue_depth": 0, "queue_capacity": 20, "retry_count": 3,
                 "last_failure_code": "LockNotAvailable:55P03", "last_failure_phase": "cleanup",
                 "last_failure_at": "2026-11-10T12:06:00", "last_recovered_at": "2026-11-10T12:06:02"}
        with patch.object(module, "_health_snapshots", health), patch.object(
                module, "get_behavior_write_pipeline_stats", return_value=stats), patch.object(
                module.presence_service, "runtime_snapshot", return_value={"degraded": False}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://testserver") as client:
                payload = (await client.get("/api/internal/health")).json()
                self.assertEqual("ok", payload["status"])
                self.assertEqual(stats, payload["behavior_write_pipeline"])
                stats["last_error"] = "LockNotAvailable:55P03"
                self.assertEqual("degraded", (await client.get("/api/internal/health")).json()["status"])

    async def test_quarantine_stays_visible_after_success_clears_the_last_worker_error(self):
        import importlib
        module = importlib.import_module("classroom_app.app")
        health = RuntimeHealthSnapshots({"database_probe": lambda: {"ok": True}})
        health.refresh()
        stats = {"alive": True, "last_error": "", "quarantined_count": 1, "queue_depth": 0, "queue_capacity": 20}
        with patch.object(module, "_health_snapshots", health), patch.object(
                module, "get_behavior_write_pipeline_stats", return_value=stats):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://testserver") as client:
                response = await client.get("/api/internal/health")
                self.assertEqual(response.json()["status"], "degraded")
                self.assertEqual(response.json()["behavior_write_pipeline"]["quarantined_count"], 1)
                self.assertEqual((await client.get("/api/internal/live")).json()["status"], "ok")

    async def test_probes_remain_responsive_while_database_and_disk_are_unavailable(self):
        import importlib
        module = importlib.import_module("classroom_app.app")
        health = RuntimeHealthSnapshots({"database_probe": lambda: {"ok": True}})
        with patch.object(module, "_health_snapshots", health), \
             patch.object(module, "get_db_connection", side_effect=AssertionError("probe SQL")), \
             patch.object(module.document_render_service, "cache_stats", side_effect=AssertionError("probe disk")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://testserver") as client:
                live, diagnostic, ready = await asyncio.gather(*(
                    client.get(path) for path in ("/api/internal/live", "/api/internal/health", "/api/internal/ready")))
                self.assertEqual(200, live.status_code)
                self.assertEqual("degraded", diagnostic.json()["status"])
                self.assertIn("database_backend", diagnostic.json())
                self.assertEqual(503, ready.status_code)
                health.refresh()
                self.assertEqual(200, (await client.get("/api/internal/ready")).status_code)
                self.assertIn("x-request-id", live.headers)


if __name__ == "__main__":
    unittest.main()
