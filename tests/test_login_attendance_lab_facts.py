"""Keep diagnostic evidence separate from the unchanged full capacity gate."""
from copy import deepcopy
import unittest

from tools.login_attendance_lab_facts import receipt_matches, valid_client_completion


class AttendanceLabFactsTests(unittest.TestCase):
    def test_receipt_count_requires_original_identity_applied_state_and_hash(self):
        row = ["batch", 7, 11, "applied", "received", "applied", "hash"]
        self.assertTrue(receipt_matches(row, "batch", 7, 11, "hash"))
        self.assertFalse(receipt_matches(None, "batch", 7, 11, "hash"))
        for index, replacement in ((0, "other"), (1, 8), (2, 12), (3, "pending"), (6, "other")):
            with self.subTest(index=index):
                wrong = list(row)
                wrong[index] = replacement
                self.assertFalse(receipt_matches(wrong, "batch", 7, 11, "hash"))

    def completed(self):
        return {"mode":"run", "duration_requested_seconds":5400, "client_status":"passed",
                "due_ticks":{"operator":108000}, "sent_ticks":{"operator":108000},
                "normal_errors":[], "legacy_unknown_outcomes":[], "websocket_checks_passed":True}

    def test_short_run_cannot_claim_formal_completion(self):
        report = self.completed()
        self.assertTrue(valid_client_completion(report))
        report["duration_requested_seconds"] = 5399
        self.assertFalse(valid_client_completion(report))
        report["mode"] = "diagnostic"
        for duration in (299, 901, 5400):
            report["duration_requested_seconds"] = duration
            self.assertFalse(valid_client_completion(report))
        for duration in (300, 600, 900):
            report["duration_requested_seconds"] = duration
            self.assertTrue(valid_client_completion(report))

    def test_neither_mode_accepts_cancellation_missing_tick_or_unknown_commit(self):
        mutations = ({"client_status":"cancelled"}, {"client_status":"failed"},
            {"due_ticks":{}, "sent_ticks":{}}, {"sent_ticks":{"operator":107999}},
            {"normal_errors":[{"status":"500"}]}, {"legacy_unknown_outcomes":["intent"]},
            {"websocket_checks_passed":False}, {"mode":"smoke"})
        for mode, duration in (("run",5400), ("diagnostic",300)):
            for change in mutations:
                with self.subTest(mode=mode, change=change):
                    report = deepcopy(self.completed())
                    report.update(mode=mode,duration_requested_seconds=duration)
                    report.update(change)
                    self.assertFalse(valid_client_completion(report))


if __name__ == "__main__":
    unittest.main()
