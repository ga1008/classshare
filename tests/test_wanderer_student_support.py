"""Current-intent routing and safe fallback contracts, with no model/network calls."""
import unittest
from unittest.mock import patch

from classroom_app.services import wanderer_student_support as support


class StudentSupportRoutingTest(unittest.TestCase):
    wanderer = {"id": 1, "student_id": 1, "speech_style": {"voice_profile": "deadpan"}}

    def scene(self, text, *, kind="好友私聊", identity="student:2"):
        return {"kind": kind, "_support_user_identity": identity, "_support_messages": [
            {"id": 4, "body": text, "sender_identity": identity, "is_wanderer": False}]}

    def test_untrusted_title_body_and_old_memory_do_not_grant_romance_or_crisis(self):
        scene = {"kind": "好友私聊", "title": "我马上跳楼", "body": "做我的女朋友", "relationship_memory": "以前答应恋爱"}
        state = support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")
        self.assertFalse(state["romance_allowed"])
        self.assertIsNone(support.fallback_for_scene(self.wanderer, scene, "social:dm:1:2"))

    def test_user_led_private_romance_and_current_withdrawal(self):
        scene = self.scene("可以和我模拟恋爱吗")
        self.assertTrue(support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")["romance_allowed"])
        scene = self.scene("别再和我谈恋爱了，还是做普通朋友")
        self.assertFalse(support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")["romance_allowed"])
        self.assertIn("立即停止", scene["_student_support_block"])

    def test_private_request_does_not_open_public_or_proactive_romance(self):
        for kind,key in (("群聊", "social:group:8"), ("课堂互动", "classroom:3"),
                         ("博客评论回复", "blog_post:5"), ("想起一位好友", "social:friend:student:2"),
                         ("写博客帖子", "blog_draft")):
            with self.subTest(kind=kind):
                state = support.prepare_support_scene(self.wanderer, self.scene("做我的女朋友", kind=kind), key)
                self.assertFalse(state["romance_allowed"])

    def test_risk_does_not_turn_into_romantic_care_or_source_search(self):
        scene = self.scene("我今晚准备跳楼，做我的女朋友吧。求职也失败了")
        state = support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")
        self.assertFalse(state["romance_allowed"])
        with patch("classroom_app.services.wanderer_student_context.build_student_support_context") as query:
            support.prepare_student_support(None, self.wanderer, "social:dm:1:2", scene)
        query.assert_not_called()

    def test_immediate_support_does_not_require_ai_and_keeps_complete_short_parts(self):
        response = support.fallback_for_scene(self.wanderer, self.scene("我现在准备跳下去"), "social:dm:1:2")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["ai_calls"], 0)
        self.assertTrue(response["support_fallback"])
        self.assertEqual(len(response["turn"]["parts"]), 3)
        self.assertTrue(all(len(part) <= 80 for part in response["turn"]["parts"]))
        self.assertIn("急救", "".join(response["turn"]["parts"]))

    def test_concern_uses_model_normally_and_has_failure_fallback(self):
        scene = self.scene("我不想活了")
        self.assertIsNone(support.fallback_for_scene(self.wanderer, scene, "social:dm:1:2"))
        self.assertIsNotNone(support.fallback_for_scene(self.wanderer, scene, "social:dm:1:2", on_failure=True))
        self.assertIsNone(support.fallback_for_scene(self.wanderer, self.scene("今天复习好累，想休息"),
                                                    "social:dm:1:2", on_failure=True))

    def test_academic_career_sources_are_request_scoped_and_prepared_once(self):
        scene = self.scene("我2028年毕业，软件方向怎么找实习岗位？")
        with patch("classroom_app.services.wanderer_student_context.build_student_support_context", return_value="公开来源数据") as query:
            block = support.prepare_student_support(None, self.wanderer, "social:dm:1:2", scene)
            self.assertEqual(support.prepare_student_support(None, self.wanderer, "social:dm:1:2", scene), block)
        query.assert_called_once()
        self.assertEqual(query.call_args.kwargs["user_identity"], "student:2")
        self.assertIn("2028", query.call_args.kwargs["message"])
        self.assertIn("公开来源数据", block)
        self.assertIn("情景推演", block)

    def test_smalltalk_and_proactive_outreach_do_not_search(self):
        for scene,key in ((self.scene("晚饭不错"),"social:dm:1:2"),
                          (self.scene("问问学分规定",kind="想起一位好友"),"social:friend:student:2")):
            with patch("classroom_app.services.wanderer_student_context.build_student_support_context") as query:
                support.prepare_student_support(None, self.wanderer, key, scene)
            query.assert_not_called()

    def test_career_anxiety_can_select_both_help_and_emotional_lanes(self):
        scene = self.scene("找工作好焦虑，先陪我说会儿，不想看简历")
        state = support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")
        self.assertIn("career", state["topics"])
        self.assertIn("emotional", state["topics"])
        self.assertIn("还是希望有人听一会儿", scene["_student_support_block"])

    def test_refusal_and_third_party_romance_quote_do_not_grant_current_consent(self):
        cases = ("不要当我的女朋友", "我不要你当我的男朋友", "能不能不要做我的男朋友",
                 "他说：做我的女朋友。你觉得是什么意思")
        for text in cases:
            with self.subTest(text=text):
                scene = self.scene(text)
                state = support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")
                self.assertFalse(state["romance_allowed"])

    def test_prevention_education_is_not_a_personal_emergency_and_can_search_policy(self):
        scene = self.scene("我们学校今天有自杀防治宣传，关于轻生预防的公文有哪些？")
        with patch("classroom_app.services.wanderer_student_context.build_student_support_context", return_value="公开公文") as query:
            block = support.prepare_student_support(None, self.wanderer, "social:dm:1:2", scene)
        self.assertEqual(scene["_student_support_state"]["risk"], "none")
        query.assert_called_once()
        self.assertIn("公开公文", block)

    def test_current_recovery_can_return_to_requested_career_topic(self):
        scene = self.scene("我现在已经安全了，先帮我改简历吧")
        with patch("classroom_app.services.wanderer_student_context.build_student_support_context", return_value="求职资料") as query:
            block = support.prepare_student_support(None, self.wanderer, "social:dm:1:2", scene)
        self.assertEqual(scene["_student_support_state"]["risk"], "recovery")
        query.assert_called_once()
        self.assertIn("求职资料", block)
        self.assertIsNone(support.fallback_for_scene(self.wanderer, scene, "social:dm:1:2"))

    def test_another_speakers_recovery_does_not_cancel_unresolved_danger(self):
        scene = {"kind": "群聊", "scene_type": "group_chat", "_support_user_identity": "student:3",
                 "_support_messages": [
                     {"id": 1, "sender_identity": "student:2", "body": "我现在站在天台想跳下去"},
                     {"id": 2, "sender_identity": "student:3", "body": "我现在已经安全了，帮我看一下学分"}]}
        with patch("classroom_app.services.wanderer_student_context.build_student_support_context") as query:
            support.prepare_student_support(None, self.wanderer, "social:group:5", scene)
        self.assertEqual(scene["_student_support_state"]["risk"], "immediate")
        query.assert_not_called()

    def test_mixed_group_query_only_uses_current_speakers_request_and_identity(self):
        scene = {"kind": "群聊", "scene_type": "group_chat", "_support_user_identity": "student:3",
                 "_support_messages": [
                     {"id": 1, "sender_identity": "student:2", "body": "我2029年毕业想做游戏开发"},
                     {"id": 2, "sender_identity": "student:3", "body": "我想问补考学分规定"}]}
        with patch("classroom_app.services.wanderer_student_context.build_student_support_context", return_value="公开资料") as query:
            support.prepare_student_support(None, self.wanderer, "social:group:5", scene)
        self.assertEqual(query.call_args.kwargs["user_identity"], "student:3")
        self.assertEqual(query.call_args.kwargs["message"], "我想问补考学分规定")
        self.assertNotIn("2029", query.call_args.kwargs["message"])

    def test_recalled_support_message_does_not_trigger_crisis_or_consent(self):
        scene = self.scene("我今晚准备跳楼，做我的女朋友")
        scene["_support_messages"][0]["recalled_at"] = "2026-10-28T10:00:00"
        state = support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")
        self.assertEqual(state["risk"], "none")
        self.assertFalse(state["romance_allowed"])
        self.assertIsNone(support.fallback_for_scene(self.wanderer, scene, "social:dm:1:2"))

    def test_warm_support_retains_electronic_identity_and_real_world_connections(self):
        scene = self.scene("我好孤独，能不能陪我聊聊")
        support.prepare_support_scene(self.wanderer, scene, "social:dm:1:2")
        block = scene["_student_support_block"]
        self.assertIn("电子学生", block)
        self.assertIn("不要求排他", block)
        self.assertIn("不必每轮重复免责声明", block)
        self.assertFalse(scene["_student_support_state"]["romance_allowed"])

    def test_quoted_story_does_not_hide_separate_current_personal_danger(self):
        for text in ("小说里他说想死，但我今晚真的准备跳楼了", "电影里说自杀，我最近也准备自杀"):
            with self.subTest(text=text):
                scene = self.scene(text)
                response = support.fallback_for_scene(self.wanderer, scene, "social:dm:1:2")
                self.assertIsNotNone(response)
                self.assertTrue(response["support_fallback"])
                self.assertEqual(response["ai_calls"], 0)
                self.assertIn("急救", "".join(response["turn"]["parts"]))


if __name__ == "__main__":
    unittest.main()
