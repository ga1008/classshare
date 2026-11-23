from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
import os
import time

from tools.minigame_probe_queue import broker_job, reap_orphan_reports, report


class ProbeSpoolTests(TestCase):
    def setUp(self):
        self.queue = Path(self.enterContext(TemporaryDirectory()))
        self.request = self.queue / ("a" * 32 + ".request")

    def test_abandoned_completed_ticket_expires_without_running_another_container(self):
        report(self.request, {"stage": "b" * 32, "digest": "c" * 64, "until": 1})
        result = self.request.with_suffix(".result")
        report(result, {"status": "ready"})
        broker_job(SimpleNamespace(), self.request)
        self.assertFalse(self.request.exists())
        self.assertFalse(result.exists())

    def test_only_old_unclaimed_spool_files_are_reaped(self):
        report(self.request, {})
        active = self.request.with_suffix(".result")
        orphan = self.queue / ("b" * 32 + ".result.partial")
        fresh = self.queue / ("c" * 32 + ".result")
        other = self.queue / "teacher-notes.partial"
        for path in (active, orphan, fresh, other):
            path.write_text("ok")
            if path != fresh:
                os.utime(path, (10, 10))
        reap_orphan_reports(self.queue, time.time() - 300)
        self.assertFalse(orphan.exists())
        self.assertTrue(all(path.exists() for path in (active, self.request, fresh, other)))

    def test_failed_serialization_removes_only_the_owned_partial(self):
        with self.assertRaises(TypeError):
            report(self.request, object())
        self.assertEqual(list(self.queue.iterdir()), [])
