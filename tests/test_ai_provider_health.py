"""Provider circuit breaker: trip timing, local-network suppression, probe/recovery."""
from __future__ import annotations

import unittest

from classroom_app.services.ai_provider_health import (
    FAILURE_KIND_CONNECTION, FAILURE_KIND_OTHER, FAILURE_KIND_REJECTED, FAILURE_KIND_SERVER, FAILURE_KIND_TIMEOUT,
    STATE_CLOSED, STATE_HALF_OPEN, STATE_OPEN, ProviderHealthRegistry,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_registry(clock, **kwargs):
    events = []
    registry = ProviderHealthRegistry(
        ("deepseek", "volcengine"), failure_threshold=2, window_seconds=300, cooldown_seconds=60,
        max_cooldown_seconds=600, clock=clock, wall=lambda: "T",
        on_change=lambda provider, old, new, reason: events.append((provider, old, new)), **kwargs,
    )
    return registry, events


class BreakerTripTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry, self.events = make_registry(self.clock)

    def test_two_outage_failures_in_window_open_the_breaker(self):
        self.assertFalse(self.registry.record_failure("deepseek", FAILURE_KIND_SERVER, "HTTP 503"))
        self.assertEqual(self.registry.state_of("deepseek"), STATE_CLOSED)
        self.assertTrue(self.registry.record_failure("deepseek", FAILURE_KIND_TIMEOUT, "read timeout"))
        self.assertEqual(self.registry.state_of("deepseek"), STATE_OPEN)
        self.assertEqual(self.registry.order(["deepseek", "volcengine"]), ["volcengine", "deepseek"])
        self.assertEqual(self.events, [("deepseek", STATE_CLOSED, STATE_OPEN)])

    def test_failures_outside_window_do_not_accumulate(self):
        self.registry.record_failure("deepseek", FAILURE_KIND_SERVER)
        self.clock.advance(301)
        self.assertFalse(self.registry.record_failure("deepseek", FAILURE_KIND_SERVER))
        self.assertEqual(self.registry.state_of("deepseek"), STATE_CLOSED)

    def test_rejections_and_format_errors_never_trip(self):
        for _ in range(5):
            self.registry.record_failure("deepseek", FAILURE_KIND_REJECTED, "HTTP 400")
            self.registry.record_failure("deepseek", FAILURE_KIND_OTHER, "bad json")
        self.assertEqual(self.registry.state_of("deepseek"), STATE_CLOSED)
        self.assertEqual(self.registry.snapshot()["providers"]["deepseek"]["last_error_kind"], FAILURE_KIND_OTHER)

    def test_connection_failures_on_both_vendors_mean_local_network(self):
        self.registry.record_failure("volcengine", FAILURE_KIND_CONNECTION, "connect error")
        self.registry.record_failure("deepseek", FAILURE_KIND_CONNECTION, "connect error")
        self.assertFalse(self.registry.record_failure("deepseek", FAILURE_KIND_CONNECTION, "connect error"))
        snapshot = self.registry.snapshot()["providers"]["deepseek"]
        self.assertEqual(snapshot["state"], STATE_CLOSED)
        self.assertTrue(snapshot["network_suspected"])

    def test_server_errors_trip_even_when_peer_had_connection_trouble(self):
        self.registry.record_failure("volcengine", FAILURE_KIND_CONNECTION, "connect error")
        self.registry.record_failure("deepseek", FAILURE_KIND_SERVER, "HTTP 500")
        self.assertTrue(self.registry.record_failure("deepseek", FAILURE_KIND_SERVER, "HTTP 500"))

    def test_success_resets_counters(self):
        self.registry.record_failure("deepseek", FAILURE_KIND_SERVER)
        self.registry.record_success("deepseek")
        self.assertFalse(self.registry.record_failure("deepseek", FAILURE_KIND_SERVER))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry, self.events = make_registry(self.clock)
        self.registry.record_failure("deepseek", FAILURE_KIND_SERVER)
        self.registry.record_failure("deepseek", FAILURE_KIND_SERVER)

    def test_open_breaker_is_not_available_until_cooldown(self):
        self.assertFalse(self.registry.is_available("deepseek"))
        self.assertEqual(self.registry.probe_due(), [])
        self.clock.advance(59)
        self.assertFalse(self.registry.is_available("deepseek"))
        self.clock.advance(1)
        self.assertEqual(self.registry.state_of("deepseek"), STATE_HALF_OPEN)
        self.assertTrue(self.registry.is_available("deepseek"))
        self.assertEqual(self.registry.probe_due(), ["deepseek"])

    def test_single_probe_slot_then_recovery(self):
        self.clock.advance(60)
        self.assertTrue(self.registry.acquire_probe("deepseek"))
        self.assertFalse(self.registry.acquire_probe("deepseek"))
        self.assertEqual(self.registry.order(["deepseek", "volcengine"]), ["volcengine", "deepseek"])
        self.registry.record_success("deepseek")
        self.assertEqual(self.registry.state_of("deepseek"), STATE_CLOSED)
        self.assertEqual(self.registry.order(["deepseek", "volcengine"]), ["deepseek", "volcengine"])
        self.assertEqual(self.events[-1], ("deepseek", STATE_HALF_OPEN, STATE_CLOSED))

    def test_failed_probe_reopens_with_doubled_cooldown(self):
        self.clock.advance(60)
        self.registry.acquire_probe("deepseek")
        self.assertTrue(self.registry.record_failure("deepseek", FAILURE_KIND_TIMEOUT))
        snapshot = self.registry.snapshot()["providers"]["deepseek"]
        self.assertEqual(snapshot["state"], STATE_OPEN)
        self.assertEqual(snapshot["cooldown_seconds"], 120.0)
        self.assertEqual(snapshot["trips"], 2)
        for _ in range(6):
            self.clock.advance(snapshot["cooldown_seconds"] + 1)
            self.registry.acquire_probe("deepseek")
            self.registry.record_failure("deepseek", FAILURE_KIND_TIMEOUT)
            snapshot = self.registry.snapshot()["providers"]["deepseek"]
        self.assertEqual(snapshot["cooldown_seconds"], 600.0)

    def test_late_success_does_not_close_an_open_breaker(self):
        self.registry.record_success("deepseek")  # an in-flight call finishing after the trip
        self.assertEqual(self.registry.state_of("deepseek"), STATE_OPEN)
        self.clock.advance(60)
        self.assertTrue(self.registry.acquire_probe("deepseek"))
        self.registry.record_success("deepseek")
        self.assertEqual(self.registry.state_of("deepseek"), STATE_CLOSED)

    def test_manual_trip_and_reset(self):
        self.registry.reset("deepseek")
        self.assertEqual(self.registry.state_of("deepseek"), STATE_CLOSED)
        self.registry.trip("volcengine", "maintenance")
        snapshot = self.registry.snapshot()
        self.assertEqual(snapshot["providers"]["volcengine"]["state"], STATE_OPEN)
        self.assertTrue(snapshot["providers"]["volcengine"]["manual"])
        self.assertEqual(snapshot["providers"]["volcengine"]["retry_in_seconds"], 600.0)
        self.assertTrue(snapshot["degraded"])

    def test_note_failover_counts(self):
        self.registry.note_failover("deepseek", "volcengine")
        snapshot = self.registry.snapshot()["providers"]
        self.assertEqual(snapshot["deepseek"]["failovers_out"], 1)
        self.assertEqual(snapshot["volcengine"]["served_as_failover"], 1)


if __name__ == "__main__":
    unittest.main()
