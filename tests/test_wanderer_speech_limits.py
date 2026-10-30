"""Active vs passive speech length (2026-10-31).

Self-initiated turns are clipped to 100 × (tier + 1) characters; replies to a
direct question or @-mention keep their full length but still lose AI formatting.
"""
import unittest

from classroom_app.services.wanderer_actions_blog import post_draft
from classroom_app.services.wanderer_actions_common import ActionContext, clip_part, turn_parts
from classroom_app.services.wanderer_prompt_service import _output_contract, _tier_block

LONG = "好" * 150


class SpeechLimitTest(unittest.TestCase):
    @staticmethod
    def ctx(tier=0):
        return ActionContext(conn_factory=None, wanderer={"id": 1, "student_id": 2, "tier": tier,
                                                          "display_name": "甲"})

    def test_active_turn_is_capped_by_tier(self):
        self.assertEqual(len(clip_part(self.ctx(0), LONG)), 100)
        self.assertEqual(len(clip_part(self.ctx(1), LONG)), 150)  # cap 200, body already shorter
        self.assertEqual(clip_part(self.ctx(0), "**" + LONG), LONG[:100])

    def test_passive_turn_keeps_full_length_but_strips_ai_formatting(self):
        self.assertEqual(clip_part(self.ctx(0), "**" + LONG, limit=False), LONG)

    def test_turn_parts_applies_limit_only_when_asked(self):
        turn = {"parts": [LONG, ""]}
        self.assertEqual(turn_parts(self.ctx(0), turn), [LONG[:100]])
        self.assertEqual(turn_parts(self.ctx(0), turn, limit=False), [LONG])

    def test_prompt_contract_matches_the_lane(self):
        self.assertIn("不超过 100 字", _output_contract({"tier": 0}))
        self.assertIn("不设字数上限", _output_contract({"tier": 0}, passive=True))
        self.assertIn("每条不超过 100 字", _tier_block({"tier": 0}))
        self.assertIn("不受主动发言的字数上限", _tier_block({"tier": 0}, passive=True))

    def test_blog_draft_body_uses_the_active_limit(self):
        raw = '{"action":"reply","title":"标题","parts":["' + LONG + '"]}'
        _, _, active, _ = post_draft(raw, max_body=100)
        _, _, default, _ = post_draft(raw)
        self.assertEqual(len(active), 100)
        self.assertEqual(len(default), 150)


if __name__ == "__main__":
    unittest.main()
