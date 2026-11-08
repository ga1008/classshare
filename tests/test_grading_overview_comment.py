"""Personalized grading overview: filled from the hidden support reference, never leaked."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import ai_assistant
from classroom_app.services.grading_feedback_service import (
    build_structured_feedback_markdown,
    _default_overview,
)


def _job(**overrides):
    return ai_assistant.GradingJob(
        submission_id=1,
        rubric_md="评分标准",
        requirements_md="完成共享以太网与交换机的实验并提交截图",
        **overrides,
    )


def _result(summary="", questions=None):
    return {
        "score": 96,
        "summary": summary,
        "questions": questions or [
            {"question_no": 8, "deduction_points": "没有说明生成树端口状态", "evaluation": "状态判断偏快"},
            {"question_no": 12, "deduction_points": "无", "evaluation": "准确"},
        ],
    }


class OverviewSanitizerTest(unittest.TestCase):
    def test_prefix_is_stripped_and_length_capped(self):
        text = ai_assistant._sanitize_overview_comment("总评：这次第 8 题的端口状态判断基本对，下次把生成树收敛过程写完整。")
        self.assertTrue(text.startswith("这次第 8 题"))
        self.assertLessEqual(len(text), 120)

    def test_any_profiler_marker_discards_the_comment(self):
        for text in ("根据你的侧写，你偏好分步提示。", "后台画像显示你出勤稳定。",
                     "内部支持参考说先肯定全勤。", "测评结果显示你节奏偏快。",
                     "profile_summary 显示投入稳定。"):
            self.assertEqual(ai_assistant._sanitize_overview_comment(text), "", text)
        self.assertEqual(ai_assistant._sanitize_overview_comment("这次不错，接下来补第 8 题。"),
                         "这次不错，接下来补第 8 题。")

    def test_legacy_profile_is_not_sent_to_the_feedback_model(self):
        profile = ai_assistant._build_hidden_grading_profile_prompt("学习支持摘要：全勤，偏好分步引导。")
        messages = ai_assistant._build_overview_comment_messages(_job(), _result(), profile)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("严禁出现", messages[0]["content"])
        self.assertNotIn("内部个性化支持参考", messages[1]["content"])
        self.assertIn("第8题", messages[1]["content"])
        self.assertNotIn("全勤", messages[1]["content"])


class EnsureOverviewCommentTest(unittest.TestCase):
    def _run(self, result, reply, profile="学习支持摘要：全勤，偏好分步引导。"):
        caller = AsyncMock(return_value=reply)
        with patch.object(ai_assistant, "_call_ai_platform", caller):
            output = asyncio.run(ai_assistant._ensure_grading_overview_comment(
                _job(student_profile_context=profile),
                result,
                business_context={"operation": "grading", "class_offering_id": 13},
            ))
        return output, caller

    def test_missing_summary_gets_one_personalized_call(self):
        output, caller = self._run(
            _result(),
            "第 8 题的生成树端口状态判断基本对，下一步把收敛过程按“阻塞—监听—学习—转发”逐段写一遍。",
        )
        self.assertEqual(caller.await_count, 1)
        self.assertIn("第 8 题", output["summary"])
        payload = caller.await_args.args[0]
        self.assertNotIn("全勤", payload[1]["content"])

    def test_slogan_only_summary_is_rewritten(self):
        output, caller = self._run(
            _result(summary="这次作业整体完成得不错，希望你继续保持，继续努力，再接再厉。"),
            "第 8 题判断基本对，下一步把生成树收敛过程写完整。",
        )
        self.assertEqual(caller.await_count, 1)
        self.assertIn("第 8 题", output["summary"])

    def test_existing_summary_skips_the_extra_call(self):
        output, caller = self._run(_result(summary="这次整体完成度高，下一步补第 8 题的过程说明。"), "")
        self.assertEqual(caller.await_count, 0)
        self.assertEqual(output["summary"], "这次整体完成度高，下一步补第 8 题的过程说明。")

    def test_leaky_reply_is_discarded(self):
        output, caller = self._run(_result(), "根据你的侧写，你偏好分步提示。")
        self.assertEqual(caller.await_count, 1)
        self.assertEqual(output["summary"], "")

    def test_verbatim_profile_copy_is_discarded(self):
        profile = "学习支持摘要：专升本学生，14次点名全部出勤，投入稳定。"
        output, caller = self._run(_result(), "你是专升本学生，14次点名全部出勤，投入稳定。", profile=profile)
        self.assertEqual(caller.await_count, 1)
        self.assertEqual(output["summary"], "")

    def test_existing_verbatim_copy_is_rewritten(self):
        profile = "学习支持摘要：专升本学生，14次点名全部出勤，投入稳定。"
        output, caller = self._run(
            _result(summary="你是专升本学生，14次点名全部出勤，投入稳定。"),
            "第 8 题判断基本对，下一步把收敛过程写完整。", profile=profile)
        self.assertEqual(caller.await_count, 1)
        self.assertIn("第 8 题", output["summary"])

    def test_existing_copy_with_failed_rewrite_is_blanked(self):
        profile = "学习支持摘要：专升本学生，14次点名全部出勤，投入稳定。"
        output, _caller = self._run(
            _result(summary="你是专升本学生，14次点名全部出勤，投入稳定。"),
            "根据你的侧写，你偏好分步提示。", profile=profile)
        self.assertEqual(output["summary"], "")

    def test_failed_call_keeps_the_grading_result(self):
        caller = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch.object(ai_assistant, "_call_ai_platform", caller):
            output = asyncio.run(ai_assistant._ensure_grading_overview_comment(
                _job(student_profile_context="学习支持摘要：全勤。"), _result(),
                business_context={"operation": "grading"}))
        self.assertEqual(output["score"], 96)
        self.assertEqual(output["summary"], "")

    def test_without_a_profile_no_extra_call_is_made(self):
        output, caller = self._run(_result(), "不会用到", profile="")
        self.assertEqual(caller.await_count, 0)
        self.assertEqual(output["summary"], "")


class DeterministicFallbackTest(unittest.TestCase):
    def test_fallback_names_the_deducted_questions(self):
        text = _default_overview(96, questions=_result()["questions"])
        self.assertIn("96", text)
        self.assertIn("第 8 题", text)
        self.assertNotIn("逐题反馈检查错误点", text)

    def test_fallback_without_deductions_is_specific_to_full_marks(self):
        text = _default_overview(100, questions=[{"question_no": 1, "deduction_points": "无"}])
        self.assertIn("没有扣分点", text)

    def test_markdown_overview_uses_the_specific_fallback(self):
        markdown = build_structured_feedback_markdown(_result())
        self.assertIn("第 8 题还有扣分点", markdown)


if __name__ == "__main__":
    unittest.main()
