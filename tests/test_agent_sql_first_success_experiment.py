import json
import unittest

from tools.agent_sql_first_success_experiment import (
    TARGET_SUCCESS_RATE,
    experiment_cases,
    run_sql_first_success_experiment,
)


class AgentSqlFirstSuccessExperimentTests(unittest.TestCase):
    def test_experiment_covers_ten_teacher_queries(self):
        cases = experiment_cases()

        self.assertEqual(10, len(cases))
        self.assertEqual(10, len({case.id for case in cases}))
        self.assertTrue(all(case.expected_columns for case in cases))

    def test_experiment_passes_target_success_rate(self):
        report = run_sql_first_success_experiment()

        self.assertEqual(10, report["case_count"])
        self.assertGreaterEqual(report["success_rate"], TARGET_SUCCESS_RATE)
        self.assertEqual(report["case_count"], report["success_count"])
        self.assertTrue(report["passed"])
        self.assertFalse([result for result in report["results"] if not result["success"]])
        json.dumps(report, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
