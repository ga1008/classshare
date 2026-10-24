"""Growth curve, signal breakdown and stage-trial difficulty guidance (pure, no DB)."""
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import learning_progress_service as service
from classroom_app.services.cultivation_growth_service import (
    SIGNAL_DEFINITIONS,
    apply_growth_curve,
    build_component_breakdown,
    build_growth_summary,
    build_stage_effort_table,
    growth_curve_exponent,
    longest_streak,
    raw_score_for,
)
from classroom_app.services.learning_progress_service import LEARNING_LEVELS, _build_stage_exam_prompt
from classroom_app.services.stage_exam_guidance_service import (
    COURSE_FAMILIES,
    build_stage_exam_guidance_block,
    build_tier_profile,
    detect_course_family,
)


class GrowthCurveTests(unittest.TestCase):
    def test_curve_is_monotonic_and_pins_both_ends(self):
        previous = -1.0
        for raw in range(0, 101):
            score = apply_growth_curve(raw, exponent=1.45)
            self.assertGreaterEqual(score, previous)
            previous = score
        self.assertEqual(0.0, apply_growth_curve(0, exponent=1.45))
        self.assertEqual(100.0, apply_growth_curve(100, exponent=1.45))

    def test_early_points_are_worth_more_than_late_points(self):
        early = apply_growth_curve(10, exponent=1.45) - apply_growth_curve(0, exponent=1.45)
        late = apply_growth_curve(100, exponent=1.45) - apply_growth_curve(90, exponent=1.45)
        self.assertGreater(early, 10)
        self.assertLess(late, 10)
        self.assertGreater(early, late)

    def test_inverse_round_trips(self):
        for score in (8, 18, 32, 46, 58, 70, 80, 88, 95, 100):
            raw = raw_score_for(score, exponent=1.45)
            self.assertAlmostEqual(score, apply_growth_curve(raw, exponent=1.45), delta=0.15)

    def test_exponent_env_override_is_clamped(self):
        with patch.dict("os.environ", {"CULTIVATION_GROWTH_EXPONENT": "9"}):
            self.assertEqual(2.5, growth_curve_exponent())
            self.assertEqual(100.0, apply_growth_curve(100))
        with patch.dict("os.environ", {"CULTIVATION_GROWTH_EXPONENT": "garbage"}):
            self.assertEqual(1.45, growth_curve_exponent())

    def test_later_stages_need_more_raw_effort(self):
        table = build_stage_effort_table(LEARNING_LEVELS, exponent=1.45)
        self.assertEqual(len(LEARNING_LEVELS), len(table))
        gaps = [row["raw_gap"] for row in table]
        self.assertLess(gaps[0], gaps[-1])
        self.assertLess(gaps[-3], gaps[-2])
        self.assertLess(gaps[-2], gaps[-1])
        self.assertEqual(100.0, table[-1]["raw_required"])

    def test_longest_streak_counts_consecutive_days_only(self):
        self.assertEqual(0, longest_streak([]))
        self.assertEqual(3, longest_streak(["2026-10-01 09:00", "2026-10-02", "2026-10-03", "2026-10-07", "bad"]))
        self.assertEqual(1, longest_streak(["2026-10-01", "2026-10-03"]))


class BreakdownTests(unittest.TestCase):
    def test_signal_shares_sum_to_one_per_component(self):
        for component, rows in SIGNAL_DEFINITIONS.items():
            self.assertAlmostEqual(1.0, sum(row["share"] for row in rows), places=6, msg=component)

    def test_breakdown_points_sum_to_component_points_and_tolerate_legacy_metrics(self):
        metrics = {
            "weights": {"material": 45, "task": 35, "interaction": 15, "consistency": 5},
            "components": {"material": 22.5, "task": 17.5, "interaction": 6.0, "consistency": 2.0},
            "material": {"required_count": 4, "completed_count": 2, "mastered_count": 1, "ratio": 0.5},
            "assignments": {"assignment_count": 2, "submitted_count": 1, "graded_count": 1, "completion_ratio": 0.5, "score_ratio": 0.9},
            "interactions": {"ai_question_count": 4, "chat_message_count": 3, "active_days": 2, "focus_seconds": 600},
        }
        rows = build_component_breakdown(metrics)
        self.assertEqual(["material", "task", "interaction", "consistency"], [row["key"] for row in rows])
        for row in rows:
            self.assertAlmostEqual(row["points"], sum(signal["points"] for signal in row["signals"]), delta=0.2)
            self.assertAlmostEqual(row["weight"] - row["points"], row["remaining"], places=1)
        material = rows[0]
        self.assertEqual("研读覆盖", material["signals"][0]["label"])
        self.assertEqual("2 / 4 份读完", material["signals"][0]["value_label"])
        self.assertIn("连续", rows[3]["signals"][1]["value_label"])

    def test_growth_summary_reports_gap_to_next_stage(self):
        summary = build_growth_summary(
            30, apply_growth_curve(30, exponent=1.45),
            next_stage={"key": "foundation", "unlock_score": 32}, exponent=1.45,
        )
        self.assertEqual("middle", summary["phase"])
        self.assertGreater(summary["bonus"], 0)
        self.assertEqual("foundation", summary["next_stage"]["key"])
        self.assertGreaterEqual(summary["next_stage"]["raw_gap"], 0)


class StageTrialGuidanceTests(unittest.TestCase):
    def test_question_count_and_difficulty_rise_with_tier(self):
        previous_count, previous_create, previous_review = 0, -1, -1
        for tier in range(1, 11):
            profile = build_tier_profile({"tier": tier})
            self.assertGreaterEqual(profile["question_count"], previous_count)
            self.assertGreaterEqual(profile["bloom_mix"]["create"], previous_create)
            self.assertGreaterEqual(profile["review_share_percent"], previous_review)
            self.assertEqual(profile["question_count"], sum(profile["type_mix"].values()))
            self.assertTrue(all(value >= 1 for value in profile["type_mix"].values()))
            self.assertEqual(100, sum(profile["bloom_mix"].values()))
            previous_count = profile["question_count"]
            previous_create = profile["bloom_mix"]["create"]
            previous_review = profile["review_share_percent"]
        self.assertEqual(6, build_tier_profile({"tier": 1})["question_count"])
        self.assertEqual(10, build_tier_profile({"tier": 10})["question_count"])
        self.assertEqual(10, build_tier_profile({"key": "stage-a"})["question_count"])

    def test_course_family_detection_covers_common_courses(self):
        self.assertEqual("programming", detect_course_family("Python程序设计"))
        self.assertEqual("language", detect_course_family("商务英语口语"))
        self.assertEqual("math_stats", detect_course_family("概率论与数理统计"))
        self.assertEqual("business_management", detect_course_family("市场营销学"))
        self.assertEqual("humanities_social", detect_course_family("中国近现代史纲要"))
        self.assertEqual("general", detect_course_family("通识研讨"))
        self.assertTrue(all(family["guidance"] for family in COURSE_FAMILIES.values()))

    def test_guidance_block_mentions_count_types_and_family(self):
        block = build_stage_exam_guidance_block({"tier": 2, "name": "基础成形"}, course_name="Java程序设计")
        self.assertEqual("programming", block["course_family"])
        self.assertIn("恰好 6 题", block["text"])
        self.assertIn("入门档", block["text"])
        self.assertIn("无需回顾更早境界", block["text"])
        self.assertIn("代码片段", block["text"])
        later = build_stage_exam_guidance_block({"tier": 9, "name": "自主精进"}, course_name="Java程序设计")
        self.assertIn("精通档", later["text"])
        self.assertIn("约 30% 的题目回顾", later["text"])

    def test_stage_exam_prompt_embeds_guidance_and_real_weights(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        progress = {
            "score": 40.0,
            "metrics": {
                "raw_score": 30.0,
                "weights": {"material": 30, "task": 55, "interaction": 10, "consistency": 5},
                "components": {"material": 12.0, "task": 15.0, "interaction": 2.0, "consistency": 1.0},
                "material": {"required_count": 3, "completed_count": 2, "mastered_count": 1},
                "assignments": {"assignment_count": 4, "submitted_count": 3, "on_time_count": 2},
                "interactions": {
                    "ai_question_count": 1, "chat_message_count": 2, "assistant_mention_count": 0,
                    "private_teacher_count": 0, "live_participation_count": 3,
                },
            },
        }
        patches = {
            "_load_offering": lambda conn, cid: {"course_name": "数据库原理", "course_description": "SQL 与范式", "class_name": "A班", "teacher_name": "T"},
            "_load_student": lambda conn, sid: {"name": "学生甲", "class_name": "A班"},
            "load_explicit_user_profile": lambda *a, **k: {},
            "build_explicit_user_profile_prompt": lambda *a, **k: "",
            "load_latest_hidden_profile": lambda *a, **k: None,
            "_build_course_knowledge_snapshot": lambda *a, **k: "【当前破境范围】第一章",
            "_load_stage_exam_retreat_prompt_block": lambda *a, **k: "",
            "_load_historical_stage_exam_question_texts": lambda *a, **k: [],
            "_load_stage_exam_template_text": lambda: "{}",
        }
        with patch.multiple(service, **patches):
            prompt = _build_stage_exam_prompt(
                conn, class_offering_id=1, student_id=2,
                level={"key": "qi_awakening", "name": "启蒙入门", "tier": 1}, progress=progress,
            )
        self.assertIn("【试炼难度指引】", prompt)
        self.assertIn("本境界 6 题", prompt)
        self.assertIn("课程类型：编程", prompt)
        self.assertIn("材料得分 12.0 / 30", prompt)
        self.assertIn("任务得分 15.0 / 55", prompt)
        self.assertIn("课堂活动 3 次", prompt)
        self.assertNotIn("/ 45", prompt)


if __name__ == "__main__":
    unittest.main()
