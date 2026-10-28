"""个人信息面板 InfoCard (docs/social-friends-groups-2026-10-27.md §12.4, §12.6)."""
from __future__ import annotations

import json

from classroom_app.services import social_card_service
from classroom_app.services import social_friend_service as friends
from classroom_app.services.cultivation_display_service import invalidate_emblem_cache, set_cultivation_hidden
from tests.social_test_support import SocialDbTestCase

CARD_KEYS = {"identity", "name", "role", "role_label", "avatar_url", "is_wanderer", "tier_badge", "archetype",
             "homepage_url", "interests", "org", "student_no_tail", "mood", "courses", "radar", "cultivation_hidden",
             "relation", "can_add", "add_blocked_reason", "online", "friend_extras"}
SECRETS = ("13800000000", "qq-secret-778899", "wx-secret-id", "card-b@example.test", "2026001234")


class SocialCardTest(SocialDbTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pk = int(cls.b.split(":")[1])
        with cls.connect() as conn:
            conn.execute("UPDATE students SET student_id_number = ?, phone = ?, qq = ?, wechat = ?, email = ?, "
                         "today_mood = ?, college = ? WHERE id = ?",
                         ("2026001234", "13800000000", "qq-secret-778899", "wx-secret-id", "card-b@example.test",
                          "今天学得很开心", "外语学院", pk))
            conn.execute("UPDATE teachers SET college = ?, department = ? WHERE id = ?",
                         ("信息学院", "计算机系", cls.teacher_id))
            for title, mode in (("实名帖子", "real_name"), ("笔名帖子", "nickname"), ("匿名帖子", "anonymous")):
                conn.execute(
                    "INSERT INTO blog_posts (author_identity, author_role, author_user_pk, author_display_name, "
                    "author_display_mode, title, status, visibility, like_count, comment_count) "
                    "VALUES (?, 'student', ?, ?, ?, ?, 'published', 'public', 3, 2)",
                    (cls.b, pk, "乙同学", mode, title))
            conn.commit()

    def setUp(self):
        super().setUp()
        social_card_service.clear_radar_cache()
        invalidate_emblem_cache()

    def card(self, viewer: str, target: str) -> dict:
        response = self.client_as(viewer).get(f"/api/social/users/{target}/card")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_public_fields_only_and_contract_shape(self):
        card = self.card(self.a, self.b)
        self.assertEqual(set(card), CARD_KEYS)
        self.assertEqual(set(card["org"]), {"school", "college", "class_name", "major", "grade"})
        self.assertEqual(card["student_no_tail"], "1234")
        self.assertEqual(card["org"]["college"], "外语学院")
        self.assertEqual(card["relation"], "none")
        self.assertTrue(card["can_add"])
        text = json.dumps(card, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertIsNone(card["mood"])  # strangers see neither mood nor online (§10.2)
        self.assertIsNone(card["online"])
        self.assertIsNone(card["friend_extras"])
        self.assertEqual(card["courses"][0]["course_name"], "社交测试课")
        self.assertEqual(card["courses"][0]["tier_name"], "启蒙入门")
        self.assertTrue(card["courses"][0]["emblem"])
        self.assertIsNotNone(card["tier_badge"])
        self.assertFalse(card["cultivation_hidden"])
        self.assertEqual(card["interests"], [])

    def test_internal_interest_inferences_are_never_public(self):
        private_interest = "内部推断兴趣不得公开"
        with self.connect() as conn:
            for identity in (self.b, self.teacher):
                conn.execute(
                    "INSERT INTO social_trait_cards (identity, source_hash, traits_json, updated_at) "
                    "VALUES (?, 'private-source', ?, '2026-10-28T08:00:00')",
                    (identity, json.dumps({"interests": [private_interest], "learning_style": "私有学习画像"})))
            conn.commit()
        # Even friendship does not make inferred interests or learning profiles public.
        self.befriend(self.a, self.b)
        for target in (self.b, self.teacher):
            card = self.card(self.a, target)
            self.assertEqual(card["interests"], [])
            self.assertNotIn(private_interest, json.dumps(card, ensure_ascii=False))
            self.assertNotIn("私有学习画像", json.dumps(card, ensure_ascii=False))

    def test_hidden_cultivation_hides_tiers_and_radar(self):
        pk = int(self.b.split(":")[1])
        with self.connect() as conn:
            set_cultivation_hidden(conn, pk, True)
            conn.commit()
        try:
            card = self.card(self.a, self.b)
        finally:
            with self.connect() as conn:
                set_cultivation_hidden(conn, pk, False)
                conn.commit()
        self.assertTrue(card["cultivation_hidden"])
        self.assertIsNone(card["tier_badge"])
        self.assertIsNone(card["radar"])
        self.assertTrue(all(item["tier_name"] is None and item["emblem"] is None for item in card["courses"]))

    def test_friend_extras_only_for_friends_and_only_real_name_posts(self):
        self.assertIsNone(self.card(self.a, self.b)["friend_extras"])
        self.befriend(self.a, self.b)
        card = self.card(self.a, self.b)
        extras = card["friend_extras"]
        self.assertEqual([item["title"] for item in extras["creations"]], ["实名帖子"])
        self.assertEqual(extras["creations"][0]["likes"], 3)
        self.assertEqual(extras["creations"][0]["comments"], 2)
        titles = [item["title"] for item in extras["recent"]]
        self.assertIn("实名帖子", titles)
        self.assertNotIn("笔名帖子", titles)
        self.assertNotIn("匿名帖子", titles)
        self.assertTrue(any(item["kind"] == "breakthrough" for item in extras["recent"]))
        self.assertEqual(card["mood"], "今天学得很开心")
        self.assertIsNotNone(card["online"])
        self.assertFalse(card["can_add"])
        self.assertEqual(card["interests"], [])

    def test_blocked_relation_gets_minimal_card(self):
        self.run_service(friends.block_user, self.user(self.a), self.b)
        card = self.card(self.a, self.b)
        self.assertEqual(card["relation"], "blocked")
        self.assertEqual(card["name"], "乙同学")
        self.assertTrue(card["avatar_url"])
        self.assertEqual((card["courses"], card["radar"], card["student_no_tail"], card["tier_badge"]),
                         ([], None, None, None))
        self.assertEqual(card["org"], {"school": "", "college": "", "class_name": "", "major": "", "grade": ""})
        self.assertFalse(card["can_add"])

    def test_teacher_and_self_variants(self):
        teacher = self.card(self.a, self.teacher)
        self.assertEqual(teacher["role"], "teacher")
        self.assertEqual(teacher["org"]["department"], "计算机系")
        self.assertEqual([item["course_name"] for item in teacher["courses"]], ["社交测试课"])
        self.assertIsNone(teacher["student_no_tail"])
        self.assertIsNone(teacher["radar"])
        me = self.card(self.a, self.a)
        self.assertEqual(me["relation"], "self")
        self.assertFalse(me["can_add"])

    def test_unknown_identity_is_404_and_ai_identity_400(self):
        self.assertEqual(self.client_as(self.a).get("/api/social/users/student:999999/card").status_code, 404)
        self.assertEqual(self.client_as(self.a).get("/api/social/users/assistant:1/card").status_code, 400)

    def test_wanderer_variant_shows_archetype_and_homepage(self):
        from classroom_app.services.wanderer_identity_service import ensure_wanderer_students
        from classroom_app.services.wanderer_public_service import build_wanderer_home

        interests = ["天文", "星图", "摄影", "夜读", "科普", "观星", "写作"]
        seed = {"slug": "card-test-wanderer", "display_name": "卡片散修", "archetype": "爱看星星的观察者",
                "persona_prompt": "合成人格", "interests": interests, "initial_tier": 2}
        with self.connect() as conn:
            student_id = ensure_wanderer_students(conn, [seed])["card-test-wanderer"]
            conn.commit()
            home = build_wanderer_home(conn, self.user(self.teacher), "card-test-wanderer")
        card = self.card(self.teacher, f"student:{student_id}")
        self.assertTrue(card["is_wanderer"])
        self.assertEqual(card["archetype"], "爱看星星的观察者")
        self.assertEqual(card["homepage_url"], "/wanderers/card-test-wanderer")
        self.assertIsNone(card["student_no_tail"])
        self.assertIsNone(card["radar"])
        self.assertTrue(card["can_add"])
        self.assertEqual(card["interests"], interests[:6])
        self.assertEqual(card["interests"], home["interests"])
        self.assertNotIn("合成人格", json.dumps(card, ensure_ascii=False))
        self.run_service(friends.block_user, self.user(self.teacher), f"student:{student_id}")
        self.assertEqual(self.card(self.teacher, f"student:{student_id}")["interests"], [])
