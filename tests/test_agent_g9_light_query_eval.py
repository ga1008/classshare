import json
import unittest

from tools.agent_g9_light_query_eval import (
    TARGET_ACCURACY,
    TARGET_P95_MS,
    light_query_eval_cases,
    run_light_query_eval,
)


class AgentG9LightQueryEvalTests(unittest.TestCase):
    def test_eval_cases_cover_twenty_light_queries(self):
        cases = light_query_eval_cases()

        self.assertEqual(20, len(cases))
        self.assertEqual(20, len({case.id for case in cases}))
        self.assertTrue(all(case.message for case in cases))
        self.assertTrue(all(case.expected_views for case in cases))
        self.assertTrue(any(case.expect_needs_agent for case in cases))

    def test_local_planner_passes_g9_eval_gate(self):
        report = run_light_query_eval(planner="local")

        self.assertEqual(20, report["case_count"])
        self.assertGreaterEqual(report["accuracy"], TARGET_ACCURACY)
        self.assertLessEqual(report["p95_ms"], TARGET_P95_MS)
        self.assertTrue(report["passed"])
        self.assertFalse([item for item in report["results"] if not item["success"]])
        self.assertEqual({"local_fallback"}, {item["planner_source"] for item in report["results"]})
        json.dumps(report, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
