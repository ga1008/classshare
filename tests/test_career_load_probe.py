import asyncio
import unittest

from tools.career_mixed_load_probe import Measurements, completed_arrival
from tools.career_teaching_reminder_probe import ReminderMeasurements


class LoadProbeMeasurementTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_arrival_exception_is_not_silently_discarded(self):
        stats = Measurements()
        stats.phase = "mixed"
        async def broken_payload():
            raise KeyError("result_version")
        task = asyncio.create_task(broken_payload())
        active = {task}
        await asyncio.sleep(0)
        completed_arrival(task,active,stats)
        self.assertFalse(active)
        self.assertEqual(stats.unexpected[0]["error"],"KeyError")
        self.assertEqual(stats.status["mixed/arrival_task"]["task_error"],1)

    async def test_truncated_metric_samples_keep_true_total_and_explicit_scope(self):
        stats = Measurements()
        for _ in range(250005):
            stats.timing(stats.sql_time,"mixed/career",1.5)
        summary = stats.summarize(stats.sql_time,"mixed/career")
        self.assertEqual(summary["total_count"],250005)
        self.assertEqual(summary["truncated_count"],5)
        self.assertEqual(summary["sample_scope"],"first_250000")

    async def test_reminder_same_recipient_count_does_not_hide_wrong_student(self):
        stats = ReminderMeasurements(None,None)
        stats.records[1] = {"task_id":1,"due_epoch":10,"done_epoch":11,"expected_recipients":[7],"actual_recipients":[8]}
        self.assertFalse(stats.report()["ok"])
        self.assertEqual(stats.report()["recipient_mismatch"],[1])

    async def test_reminder_missing_completion_is_failure_and_zero_samples_are_explicit(self):
        stats = ReminderMeasurements(None,None)
        self.assertFalse(stats.report()["exercised"])
        stats.records[1] = {"task_id":1,"due_epoch":10,"expected_recipients":[7]}
        self.assertFalse(stats.report()["ok"])
        self.assertEqual(stats.report()["incomplete"],[1])


if __name__ == "__main__":
    unittest.main()
