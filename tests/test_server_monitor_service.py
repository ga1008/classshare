import os
import unittest

from classroom_app.services import server_monitor_service as monitor
from classroom_app.services.manage_nav_service import MANAGE_NAV_ITEMS


class ServerMonitorServiceTests(unittest.TestCase):
    def test_resource_snapshot_has_core_sections(self):
        snapshot = monitor.build_resource_snapshot()
        self.assertTrue(snapshot["resource_ok"])
        self.assertGreaterEqual(snapshot["cpu"]["core_count"], 1)
        self.assertGreater(snapshot["memory"]["total_mb"], 0)
        self.assertGreaterEqual(snapshot["memory"]["percent"], 0)
        self.assertIn("percent", snapshot["disk"])
        self.assertGreater(snapshot["process_count"], 0)

    def test_monitor_snapshot_combines_resources_traffic_and_connections(self):
        snapshot = monitor.build_monitor_snapshot()
        self.assertIn("resources", snapshot)
        self.assertIn("history", snapshot)
        self.assertIsInstance(snapshot["history"], list)
        traffic = snapshot["traffic"]
        for key in ("uptime_seconds", "active_requests", "total_requests", "status_counts", "top_routes"):
            self.assertIn(key, traffic)
        connections = snapshot["connections"]
        for key in ("ws_active", "ws_total", "ws_disconnects", "ws_loss_rate"):
            self.assertIn(key, connections)

    def test_history_sample_tracks_request_deltas(self):
        sample = monitor._collect_history_sample()
        self.assertIsNotNone(sample)
        self.assertIn("cpu_percent", sample)
        self.assertIn("requests_delta", sample)
        self.assertGreaterEqual(sample["requests_delta"], 0)

    def test_process_tree_contains_current_process(self):
        tree = monitor.build_process_tree()
        self.assertTrue(tree["resource_ok"])
        self.assertGreater(tree["total_count"], 0)
        self.assertEqual(tree["self_pid"], os.getpid())
        pids = {proc["pid"] for proc in tree["processes"]}
        self.assertIn(os.getpid(), pids)
        self_entry = next(proc for proc in tree["processes"] if proc["pid"] == os.getpid())
        self.assertTrue(self_entry["is_self"])

    def test_terminate_refuses_init_and_self(self):
        with self.assertRaises(monitor.ProcessActionError):
            monitor.terminate_process(0)
        with self.assertRaises(monitor.ProcessActionError):
            monitor.terminate_process(1)
        with self.assertRaises(monitor.ProcessActionError):
            monitor.terminate_process(os.getpid())

    def test_terminate_refuses_missing_process(self):
        with self.assertRaises(monitor.ProcessActionError):
            monitor.terminate_process(2 ** 22 + 12345)

    def test_optimize_memory_reports_metrics(self):
        result = monitor.optimize_memory()
        self.assertGreaterEqual(result["collected_objects"], 0)
        self.assertGreaterEqual(result["rss_before_mb"], 0)
        self.assertGreaterEqual(result["rss_after_mb"], 0)
        self.assertIn("malloc_trimmed", result)

    def test_ai_insight_payload_is_compact_digest(self):
        snapshot = monitor.build_monitor_snapshot()
        digest = monitor.build_ai_insight_payload(snapshot)
        self.assertIn("cpu_percent", digest)
        self.assertIn("memory_percent", digest)
        self.assertIn("total_requests", digest)
        self.assertIn("ws_loss_rate_percent", digest)
        self.assertLessEqual(len(digest["top_routes"]), 6)
        self.assertLessEqual(len(digest["recent_cpu_percent_series"]), 24)

    def test_monitor_nav_item_is_super_admin_only(self):
        item = next(entry for entry in MANAGE_NAV_ITEMS if entry.key == "system_monitor")
        self.assertEqual("admin", item.domain)
        self.assertEqual("平台管理", item.group)
        self.assertEqual("super_admin", item.required_flag)
        self.assertEqual("/manage/system/monitor", item.href)


if __name__ == "__main__":
    unittest.main()
