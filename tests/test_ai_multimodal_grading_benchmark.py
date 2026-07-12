import json
import tempfile
import unittest
from pathlib import Path

from tools.ai_multimodal_grading_benchmark import (
    anonymized_answers_text,
    estimated_cost,
    model_specs,
    normalized_score,
    parse_json_object,
    pearson,
    score_recognition,
    spearman,
    summarize_results,
)


class AIMultimodalGradingBenchmarkTests(unittest.TestCase):
    def test_anonymized_answers_excludes_student_identity(self):
        raw = json.dumps(
            {
                "student_id": "secret-id",
                "student_name": "Secret Name",
                "answers": [
                    {
                        "question_id": "q1",
                        "question": "2+2?",
                        "answer": "4",
                    }
                ],
            },
            ensure_ascii=False,
        )
        text = anonymized_answers_text(raw)
        self.assertIn("q1", text)
        self.assertIn("4", text)
        self.assertNotIn("secret-id", text)
        self.assertNotIn("Secret Name", text)

    def test_parse_json_object_accepts_fenced_response(self):
        parsed = parse_json_object('说明\n```json\n{"score": 88, "questions": []}\n```')
        self.assertEqual(parsed["score"], 88)
        self.assertEqual(normalized_score(parsed), 88.0)

    def test_normalized_score_rejects_invalid_and_clamps_range(self):
        self.assertIsNone(normalized_score(None))
        self.assertIsNone(normalized_score({"score": "bad"}))
        self.assertEqual(normalized_score({"score": 120}), 100.0)
        self.assertEqual(normalized_score({"score": -3}), 0.0)

    def test_recognition_scoring_is_field_level(self):
        parsed = {
            "identity_visible": False,
            "search_controls_visible": True,
            "product_card_count": 0,
            "seller_null_visible": True,
            "screenshot_usable": True,
        }
        correct, total = score_recognition(parsed, "S001")
        self.assertEqual(total, 5)
        self.assertEqual(correct, 4)

    def test_estimated_cost_uses_provider_usage(self):
        spec = model_specs()["doubao_pro"]
        cost = estimated_cost(spec, {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
        self.assertEqual(cost, 36.0)

    def test_correlations_handle_ties(self):
        self.assertAlmostEqual(pearson([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 2, 4], [10, 20, 20, 40]), 1.0)

    def test_summary_distinguishes_human_and_historical_references(self):
        rows = [
            {
                "model_key": "qwen36",
                "provider": "qwen",
                "model": "qwen3.6-flash",
                "sample_id": "S001",
                "reference_source": "human_teacher",
                "reference_score": 20,
                "task": "grading",
                "repeat": 0,
                "status": "success",
                "score": 25,
                "json_valid": True,
                "latency_ms": 1000,
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "estimated_cost_cny": 0.001,
            },
            {
                "model_key": "qwen36",
                "provider": "qwen",
                "model": "qwen3.6-flash",
                "sample_id": "S009",
                "reference_source": "historical_ai_or_auto",
                "reference_score": 25,
                "task": "grading",
                "repeat": 0,
                "status": "success",
                "score": 30,
                "json_valid": True,
                "latency_ms": 1200,
                "usage": {"prompt_tokens": 120, "completion_tokens": 60},
                "estimated_cost_cny": 0.002,
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = root / "results.jsonl"
            results.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            summary = summarize_results(results, root / "summary")
            model = summary["models"][0]
            self.assertEqual(model["human_samples"], 1)
            self.assertEqual(model["historical_samples"], 1)
            self.assertEqual(model["human_mae"], 5.0)
            self.assertTrue((root / "summary" / "summary.md").exists())

    def test_summary_reports_independent_adjudication_separately(self):
        rows = []
        for sample_id, score in (("S013", 60), ("S017", 95)):
            rows.append(
                {
                    "model_key": "qwen36",
                    "provider": "qwen",
                    "model": "qwen3.6-flash",
                    "sample_id": sample_id,
                    "reference_source": "historical_ai_or_auto",
                    "reference_score": 90,
                    "task": "grading",
                    "repeat": 0,
                    "status": "success",
                    "score": score,
                    "json_valid": True,
                    "latency_ms": 1000,
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    "estimated_cost_cny": 0.001,
                }
            )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results = root / "results.jsonl"
            results.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            summary = summarize_results(results, root / "summary")
            model = summary["models"][0]
            self.assertEqual(model["adjudicated_samples"], 2)
            self.assertEqual(model["adjudicated_hit_rate"], 0.5)
            self.assertEqual(model["adjudicated_mean_distance"], 3.5)


if __name__ == "__main__":
    unittest.main()
