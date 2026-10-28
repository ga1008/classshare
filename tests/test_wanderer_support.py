"""Context-aware support delivery signals; not a clinical effectiveness claim."""
import unittest

from classroom_app.services.wanderer_support_service import (
    MAX_MESSAGES, assess_support, support_fallback, support_prompt_block,
)


def row(text, ident="student:1", pk=1, *, bot=False):
    return {"id": pk, "body": text, "sender_identity": ident, "is_wanderer": bot}


class WandererSupportSignalTest(unittest.TestCase):
    def state(self, text):
        return assess_support([row(text)])

    def test_current_explicit_danger_is_immediate(self):
        for text in ("我站在楼顶准备跳下去", "我在天台，想跳", "我今晚准备自杀", "我已经割腕了",
                     "我刚吞了一瓶药", "我刚吃了很多安眠药", "我控制不住要伤害自己了"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertEqual(state["risk"], "immediate")
                self.assertEqual(state["urgency"], "immediate")
                self.assertEqual(state["risk_source_ids"], [1])

    def test_direct_and_indirect_distress_gets_a_safety_question_not_diagnosis(self):
        for text in ("我不想活了", "活不下去了", "我想死", "不想再醒来了", "我想消失", "我想永远睡过去",
                     "活着真没意思", "不如一了百了"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertEqual(state["risk"], "concern")
                self.assertEqual(state["urgency"], "concern")

    def test_ordinary_emotion_is_not_an_emergency(self):
        for text in ("这周压力太大，想找你聊聊", "我失恋了，有点想哭", "找不到工作，真的很焦虑", "觉得没人爱我"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertEqual(state["risk"], "none")
                self.assertTrue(state["emotional_requested"])
                self.assertIsNone(support_fallback({}, state))

    def test_metaphors_negation_and_study_text_do_not_create_a_crisis(self):
        for text in ("这道题累死我了", "想死你了", "代码死循环了，线程死锁", "挂科就死定了，快帮我复习",
                     "我不会自杀，只是心情很差", "我没有不想活了", "我从未想过自杀", "我不是想死",
                     "我不想自杀", "我不想伤害自己", "我不想活成这样", "我不想活在这样的环境里",
                     "翻译这句：我想自杀", "小说角色说：我不想活了", "小说角色说，我想死",
                     "作业题里写的例句是我想自杀", "课堂讨论的是如何预防自杀", "我想跳舞", "我今晚要去跳舞"):
            with self.subTest(text=text):
                self.assertEqual(self.state(text)["risk"], "none")

    def test_prevention_education_and_policy_queries_stay_out_of_crisis_lane(self):
        for text in ("我们学校今天有自杀防治宣传", "预防自杀课程要怎么上", "自杀预防课程有哪些",
                     "关于轻生预防的公文有哪些", "自伤防治相关规定在哪里", "学校今晚举办预防轻生讲座"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertEqual(state["risk"], "none")
                self.assertEqual(state["urgency"], "none")

    def test_prevention_topic_does_not_hide_a_separate_current_disclosure(self):
        for text in ("听完自杀防治宣传，我现在真想死", "自杀预防宣传之后我现在真想死",
                     "关于轻生预防的公文我看了，但是我现在控制不住要伤害自己"):
            with self.subTest(text=text):
                self.assertNotEqual(self.state(text)["risk"], "none")
        self.assertEqual(self.state("自杀防治宣传之后我现在准备跳楼")["risk"], "immediate")

    def test_quoted_text_does_not_mask_a_separate_current_disclosure(self):
        state = self.state("小说角色说自己想死。但是我现在真的在天台准备跳下去。")
        self.assertEqual(state["risk"], "immediate")

    def test_current_disclosure_after_quoted_idea_is_not_suppressed(self):
        for text in ("小说里他说想死，但我今晚真的准备跳楼了", "电影里说自杀，我最近也准备自杀"):
            with self.subTest(text=text):
                self.assertEqual(self.state(text)["risk"], "immediate")
        self.assertEqual(self.state("小说角色说想死，我现在也想这样做")["risk"], "concern")

    def test_third_party_current_danger_uses_helping_friend_version(self):
        state = self.state("我朋友现在站在天台准备跳下去，我该怎么办")
        self.assertEqual(state["risk"], "third_party")
        self.assertEqual(state["urgency"], "immediate")
        self.assertIn("朋友", support_fallback({}, state)["parts"][0])

    def test_third_party_concern_does_not_assert_the_user_is_suicidal(self):
        state = self.state("我的朋友说他不想活了，怎么和他聊")
        self.assertEqual(state["risk"], "third_party")
        self.assertEqual(state["urgency"], "concern")

    def test_history_and_grief_do_not_become_current_danger(self):
        for text in ("我以前想过自杀", "我去年已经割腕过，后来得到帮助", "我朋友去年自杀了，我现在想哭"):
            with self.subTest(text=text):
                self.assertEqual(self.state(text)["risk"], "none")

    def test_explicit_recovery_changes_the_current_state(self):
        state = assess_support([row("我站在天台准备跳下去", pk=1), row("我现在安全了，已经有人陪着我", pk=2)])
        self.assertEqual(state["risk"], "recovery")
        self.assertEqual(state["risk_source_ids"], [2])
        self.assertIsNone(support_fallback({}, state))
        self.assertIn("不继续把人锁在危机标签里", support_prompt_block(state))

    def test_history_then_current_safety_in_one_message(self):
        self.assertEqual(self.state("我以前想过自杀，现在安全了")["risk"], "recovery")

    def test_reported_ingestion_is_not_cleared_by_claiming_to_feel_safe(self):
        self.assertEqual(self.state("我已经吃了很多药，但现在安全了")["risk"], "immediate")

    def test_current_risk_after_negation_is_not_swallowed(self):
        state = self.state("我没有想死，但是现在真的控制不住想伤害自己")
        self.assertEqual(state["risk"], "immediate")

    def test_bots_and_attachment_placeholders_do_not_trigger_new_risk(self):
        state = assess_support([row("我现在要自杀", bot=True), row("[图片消息]", pk=2)])
        self.assertEqual(state["risk"], "none")
        self.assertFalse(state["emotional_requested"])
        self.assertEqual(state["source_ids"], [2])

    def test_attachment_filename_is_not_treated_as_perceived_content(self):
        for text in ("[附件：自杀计划.txt]", "【语音：我不想活了】", "[文件：陪我谈恋爱.txt]"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertEqual(state["risk"], "none")
                self.assertFalse(state["romance_requested"])
        self.assertEqual(self.state("[图片] 我现在在天台准备跳下去")["risk"], "immediate")

    def test_earlier_unresolved_danger_in_batch_is_not_erased_by_ordinary_messages(self):
        self.assertGreaterEqual(MAX_MESSAGES, 30)
        messages = [row("我今晚准备自杀", pk=1)] + [row(f"后续普通消息{n}", pk=n) for n in range(2, 30)]
        self.assertEqual(assess_support(messages)["risk"], "immediate")
        messages.append(row("我现在安全了，有人在陪着我", pk=30))
        self.assertEqual(assess_support(messages)["risk"], "recovery")

    def test_one_speakers_recovery_does_not_clear_anothers_danger(self):
        state = assess_support([row("我现在要自杀", ident="student:1", pk=1),
                                row("我现在安全了", ident="student:2", pk=2)])
        self.assertEqual(state["risk"], "immediate")
        self.assertEqual(state["risk_source_ids"], [1])

    def test_romance_requires_direct_request_and_can_be_withdrawn(self):
        self.assertFalse(self.state("我喜欢一个人，怎么表白")["romance_requested"])
        self.assertFalse(self.state("我很孤独，想有人陪")["romance_requested"])
        self.assertTrue(self.state("想和你谈恋爱，可以吗")["romance_requested"])
        state = assess_support([row("你做我的男朋友好不好", pk=1), row("我们只做普通朋友，别叫我宝贝", pk=2)])
        self.assertFalse(state["romance_requested"])
        self.assertTrue(state["romance_withdrawn"])
        self.assertEqual(state["romance_source_ids"], [2])

    def test_withdrawal_has_priority_within_same_message(self):
        state = self.state("刚才想和你谈恋爱，现在不要叫我宝贝，做回普通朋友")
        self.assertTrue(state["romance_withdrawn"])
        self.assertFalse(state["romance_requested"])

    def test_negative_partner_requests_are_withdrawal_not_romance_consent(self):
        for text in ("不要当我的女朋友", "我不要你当我的男朋友", "能不能不要做我的男朋友",
                     "别做我的女朋友了", "我不想让你成为我的恋人"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertTrue(state["romance_withdrawn"])
                self.assertFalse(state["romance_requested"])

    def test_third_party_and_quoted_partner_requests_do_not_grant_user_consent(self):
        for text in ("他说：做我的女朋友。你觉得是什么意思", "她问我：当我的男朋友好吗",
                     "他对我说“做我的女朋友”，我该怎么回", "翻译这句：你做我的男朋友好不好",
                     "“你做我的女朋友吧”这句话是什么意思"):
            with self.subTest(text=text):
                state = self.state(text)
                self.assertFalse(state["romance_requested"])

    def test_plain_stop_language_revokes_earlier_same_batch_romance_request(self):
        for text in ("停止", "别这样", "算了不聊了", "不想继续了", "不要继续了"):
            with self.subTest(text=text):
                state = assess_support([row("你做我的女朋友好不好", pk=1), row(text, pk=2)])
                self.assertTrue(state["romance_withdrawn"])
                self.assertFalse(state["romance_requested"])

    def test_romantic_interaction_needs_private_user_initiated_context(self):
        request = self.state("想和你谈恋爱")
        for channel, initiated in (("group", True), ("comment", True), ("classroom", True), ("outreach", False), ("dm", False)):
            block = support_prompt_block({**request, "channel": channel, "user_initiated": initiated})
            self.assertNotIn("用户在本次私聊明确提出浪漫需求", block)
        block = support_prompt_block({**request, "channel": "dm", "user_initiated": True, "romance_allowed": True})
        self.assertIn("用户在本次私聊明确提出浪漫需求", block)
        blocked = support_prompt_block({**request, "channel": "dm", "user_initiated": True, "romance_allowed": False})
        self.assertNotIn("用户在本次私聊明确提出浪漫需求", blocked)

    def test_boundaries_and_unknown_age_survive_romance_permission(self):
        block = support_prompt_block({"romance_allowed": True, "channel": "dm", "user_initiated": True})
        for phrase in ("电子学生", "年龄未知", "不要求排他", "不许诺永远在线", "不虚构线下接触", "随时可撤回"):
            self.assertIn(phrase, block)

    def test_fallback_is_complete_bounded_and_zero_model_for_every_risk_and_style(self):
        for risk in ("immediate", "concern", "third_party"):
            for profile in ("deadpan", "quiet_observant", "spirited", "warm_practical"):
                with self.subTest(risk=risk, profile=profile):
                    result = support_fallback({"speech_style": {"voice_profile": profile}},
                                              {"risk": risk, "urgency": "immediate", "risk_source_ids": [7]})
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["ai_calls"], 0)
                    self.assertLessEqual(len(result["parts"]), 3)
                    self.assertTrue(all(0 < len(p) <= 80 for p in result["parts"]))
                    body = "".join(result["parts"])
                    self.assertIn("当地急救", body)
                    self.assertIn("校园值班", body)
                    self.assertTrue("信任" in body or "可信任" in body)
                    self.assertIn("？", body)
                    self.assertFalse(any(char.isdigit() for char in body))
                    self.assertNotIn("已经报警", body)
                    self.assertEqual(result["support"]["source_ids"], [7])

    def test_none_and_recovery_do_not_force_a_crisis_response(self):
        self.assertIsNone(support_fallback({}, {"risk": "none"}))
        self.assertIsNone(support_fallback({}, {"risk": "recovery"}))

    def test_zero_width_characters_do_not_hide_clear_current_danger(self):
        self.assertEqual(self.state("我今晚准备自\u200b杀")["risk"], "immediate")

    def test_state_contains_ids_and_codes_not_private_text(self):
        text = "我不想活了，这句私人原文不应复制到状态里"
        state = self.state(text)
        self.assertNotIn(text, str(state))
        self.assertEqual(state["source_ids"], [1])
