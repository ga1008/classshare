"""好友推荐 + AI 找人 + 隐藏数据守卫 (docs/social-friends-groups-2026-10-27.md §12.2, §12.3, §12.6)."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from unittest.mock import patch

from classroom_app import database
from classroom_app.services import social_ai_search_service as ai_search
from classroom_app.services import social_ai_service
from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_recommendation_service as recs
from classroom_app.services.social_common import SocialError
from tests.social_test_support import SocialDbTestCase, grant_tier

TRAIT_WORDS = ("openness", "conscientiousness", "extraversion", "agreeableness", "stability", "traits_json",
               "learning_style", "source_hash", "profile_summary")
HIDDEN_MARKER = "侧写"
EXTRA_PEOPLE = ("己同学", "庚同学", "辛同学", "壬同学")


class StubAI:
    """Deterministic fake model: records every call by task label; never reaches a network."""

    def __init__(self, *, fail_labels: tuple[str, ...] = ()):
        self.calls: list[tuple[str, dict]] = []
        self.fail_labels = fail_labels

    def labels(self, prefix: str) -> list[str]:
        return [label for label, _ in self.calls if label.startswith(prefix)]

    async def __call__(self, endpoint, payload, **kwargs):
        label = payload["task_label"]
        message = payload["new_message"]
        self.calls.append((label, payload))
        if any(label.startswith(item) for item in self.fail_labels):
            raise RuntimeError("stub failure")
        if label == "social:traits":
            items = json.loads(message)["items"]
            return {"status": "success", "response_json": {"items": [
                {"id": item["id"], "traits": {"openness": (item["id"] % 5) / 5, "extraversion": (item["id"] % 3) / 3,
                                              "conscientiousness": 0.4, "agreeableness": 0.6, "stability": 0.5},
                 "learning_style": "稳扎稳打", "interests": ["编程", f"兴趣{item['id'] % 2}"]} for item in items]}}
        if label.startswith("social:recs:"):
            ids = [member["id"] for member in json.loads(message)["members"]]
            return {"status": "success", "response_json": {"items": [
                {"id": me, "complement": [other for other in reversed(ids) if other != me],
                 "similar": [other for other in ids if other != me]} for me in ids]}}
        if label == "social:ai_search:parse":
            return {"status": "success", "response_json": {"role": "student", "interests": ["英语"], "evil": "x"}}
        if label == "social:ai_search:rerank":
            ids = [item["id"] for item in json.loads(message)["candidates"]]
            return {"status": "success", "response_json": {"items": [
                {"id": number, "reason": f"{HIDDEN_MARKER}显示 TA 很外向，QQ：123456789 欢迎联系 abc@example.test"}
                for number in ids]}}
        return {"status": "success", "response_json": {}}


class RecommendationWorld(SocialDbTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.connect() as conn:
            extra = []
            for index, name in enumerate(EXTRA_PEOPLE):
                conn.execute("INSERT INTO students (student_id_number, name, class_id, enrollment_status) "
                             "VALUES (?, ?, ?, 'active')", (f"S-REC-{index}", name, cls.class_id))
                pk = conn.execute("SELECT id FROM students WHERE student_id_number = ?",
                                  (f"S-REC-{index}",)).fetchone()[0]
                grant_tier(conn, cls.offering_id, pk, 1)
                extra.append(f"student:{pk}")
            conn.commit()
        cls.extra = extra
        cls.everyone = [cls.a, cls.b, cls.c, cls.d, cls.e, *extra]

    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute("DELETE FROM ai_psychology_profiles")
            conn.execute("DELETE FROM ai_chat_sessions")
            conn.commit()
        for identity in self.everyone:
            self.add_profile(identity, f"{HIDDEN_MARKER}：该生喜欢编程，节奏稳定 {identity}")

    def add_profile(self, identity: str, summary: str) -> None:
        pk = int(identity.split(":")[1])
        with self.connect() as conn:
            conn.execute("INSERT INTO ai_chat_sessions (session_uuid, class_offering_id, user_pk, user_role) "
                         "VALUES (?, ?, ?, 'student')", (uuid.uuid4().hex, self.offering_id, pk))
            session_id = conn.execute("SELECT MAX(id) FROM ai_chat_sessions").fetchone()[0]
            conn.execute("INSERT INTO ai_psychology_profiles (class_offering_id, session_id, user_pk, user_role, "
                         "profile_summary, mental_state_summary) VALUES (?, ?, ?, 'student', ?, ?)",
                         (self.offering_id, session_id, pk, summary, "隐藏的心理状态"))
            conn.commit()

    def run_pipeline(self, stub: StubAI) -> dict:
        return asyncio.run(recs.run_recommendations_now(database.get_db_connection, ai_caller=stub))


class TraitExtractionTest(RecommendationWorld):
    def test_only_changed_sources_are_re_extracted(self):
        stub = StubAI()
        first = self.run_pipeline(stub)
        self.assertEqual(first["traits"]["extracted"], len(self.everyone))
        self.assertEqual(len(stub.labels("social:traits")), 1)  # one batch of 9
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_trait_cards"), len(self.everyone))

        stub_again = StubAI()
        second = self.run_pipeline(stub_again)
        self.assertEqual(stub_again.labels("social:traits"), [])
        self.assertEqual(stub_again.labels("social:recs"), [])  # unchanged buckets are skipped
        self.assertEqual(second["skipped"], second["buckets"])

        self.add_profile(self.b, "新的资料：喜欢辩论")
        stub_third = StubAI()
        self.run_pipeline(stub_third)
        traits_calls = [payload for label, payload in stub_third.calls if label == "social:traits"]
        self.assertEqual(len(traits_calls), 1)
        self.assertEqual(len(json.loads(traits_calls[0]["new_message"])["items"]), 1)

    def test_ai_payloads_are_anonymous(self):
        stub = StubAI()
        self.run_pipeline(stub)
        ranking = [payload for label, payload in stub.calls if label.startswith("social:recs:")]
        self.assertTrue(ranking)
        for payload in ranking:
            self.assertNotIn("student:", payload["new_message"])
            self.assertNotIn("同学", payload["new_message"])
            self.assertNotIn(HIDDEN_MARKER, payload["new_message"])


class BucketRankingTest(RecommendationWorld):
    def methods(self) -> dict[str, str]:
        with self.connect() as conn:
            return {row[0]: row[1] for row in conn.execute("SELECT scope_key, method FROM social_recommendation_buckets")}

    def test_small_bucket_uses_one_ai_call_and_large_bucket_backend(self):
        self.run_pipeline(StubAI())
        self.assertEqual(self.methods()[f"class:{self.class_id}"], recs.METHOD_AI)
        with self.connect() as conn:
            conn.execute("DELETE FROM social_recommendation_buckets")
            conn.execute("DELETE FROM social_recommendation_pairs")
            conn.commit()
        with patch.object(recs, "AI_BUCKET_MAX", 3):
            backend_stub = StubAI()
            self.run_pipeline(backend_stub)
        self.assertEqual(backend_stub.labels("social:recs"), [])
        self.assertEqual(self.methods()[f"class:{self.class_id}"], recs.METHOD_BACKEND)
        self.assertGreater(self.count("SELECT COUNT(*) FROM social_recommendation_pairs"), 0)

    def test_daily_cap_falls_back_to_backend_scoring(self):
        with patch.dict(os.environ, {"SOCIAL_REC_DAILY_AI_CAP": "1"}):
            stub = StubAI()
            summary = self.run_pipeline(stub)
        self.assertEqual(len(stub.calls), 1)  # the single unit went to trait extraction
        self.assertEqual(summary["ai"], 0)
        self.assertIn(recs.METHOD_FALLBACK, set(self.methods().values()))

    def test_failed_ai_keeps_yesterdays_rows(self):
        self.run_pipeline(StubAI())
        before = self.count("SELECT COUNT(*) FROM social_recommendation_pairs")
        self.add_profile(self.c, "资料变化")
        summary = self.run_pipeline(StubAI(fail_labels=("social:recs",)))
        self.assertGreater(summary["kept"], 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_recommendation_pairs"), before)


class ServingTest(RecommendationWorld):
    def test_top_five_each_with_fixed_hints_and_exclusions(self):
        self.run_pipeline(StubAI())
        self.befriend(self.a, self.b)
        self.run_service(friends.send_request, self.user(self.a), self.c)
        self.run_service(friends.block_user, self.user(self.d), self.a)
        payload = self.client_as(self.a).get("/api/social/recommendations").json()
        self.assertFalse(payload["locked"])
        self.assertTrue(payload["generated_on"])
        self.assertEqual(len(payload["complement"]), 5)
        self.assertLessEqual(len(payload["similar"]), 5)
        shown = [item["identity"] for item in payload["complement"] + payload["similar"]]
        self.assertEqual(len(shown), len(set(shown)))
        for excluded in (self.a, self.b, self.c, self.d):
            self.assertNotIn(excluded, shown)
        for item in payload["complement"]:
            self.assertEqual((item["match"], item["hint"]), ("complement", recs.HINTS["complement"]))
        for item in payload["similar"]:
            self.assertEqual((item["match"], item["hint"]), ("similar", recs.HINTS["similar"]))
        legacy = self.client_as(self.a).get("/api/social/users/suggestions").json()["items"]
        self.assertEqual({item["identity"] for item in legacy}, set(shown))
        self.assertNotIn("hint", legacy[0])

    def test_tier_zero_is_locked_and_teacher_gets_plain_suggestions(self):
        self.run_pipeline(StubAI())
        with self.connect() as conn:
            conn.execute("DELETE FROM learning_certificates WHERE student_id = ?", (int(self.e.split(":")[1]),))
            conn.commit()
        locked = self.client_as(self.e).get("/api/social/recommendations").json()
        self.assertEqual((locked["locked"], locked["complement"], locked["similar"]), (True, [], []))
        teacher = self.client_as(self.teacher).get("/api/social/recommendations").json()
        self.assertEqual(teacher["complement"], [])
        self.assertTrue(teacher["similar"])
        self.assertTrue(all(item["hint"] == recs.HINTS["similar"] for item in teacher["similar"]))


class AiSearchTest(RecommendationWorld):
    def search(self, identity: str, stub: StubAI, prompt: str = "找喜欢英语的同学"):
        with patch.object(social_ai_service, "default_caller", stub):
            return self.client_as(identity).post("/api/social/users/ai-search", json={"prompt": prompt})

    def test_teacher_search_sanitizes_reasons_and_counts_quota(self):
        response = self.search(self.teacher, StubAI())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["items"])
        self.assertLessEqual(len(body["items"]), 10)
        self.assertEqual(body["remaining_today"], ai_search.DAILY_LIMIT - 1)
        self.assertNotIn("evil", body["conditions"])
        self.assertEqual(body["conditions"]["role"], "student")
        self.assertTrue(body["conditions"]["summary"])
        for item in body["items"]:
            self.assertLessEqual(len(item["reason"]), 40)
            for banned in (HIDDEN_MARKER, "123456789", "@", "QQ"):
                self.assertNotIn(banned, item["reason"])

    def test_quota_exhausted_is_429_and_low_tier_is_403(self):
        with patch.object(ai_search, "DAILY_LIMIT", 1):
            self.assertEqual(self.search(self.teacher, StubAI()).status_code, 200)
            self.assertEqual(self.search(self.teacher, StubAI()).status_code, 429)
        self.assertEqual(self.search(self.a, StubAI()).status_code, 403)
        with self.connect() as conn:
            grant_tier(conn, self.offering_id, int(self.a.split(":")[1]), 5)
            conn.commit()
        self.assertEqual(self.search(self.a, StubAI()).status_code, 200)

    def test_hidden_cultivation_never_matches_tier_conditions(self):
        from classroom_app.services.cultivation_display_service import set_cultivation_hidden

        with self.connect() as conn:
            set_cultivation_hidden(conn, int(self.b.split(":")[1]), True)
            conn.commit()
            found = ai_search.find_candidates(conn, self.teacher, {"tier_min": 1})
            set_cultivation_hidden(conn, int(self.b.split(":")[1]), False)
            conn.commit()
        self.assertNotIn(self.b, {item["identity"] for item in found})
        self.assertIn(self.c, {item["identity"] for item in found})


class HiddenDataGuardTest(RecommendationWorld):
    """Every /api/social/* JSON a user can reach in this scenario is free of trait data and 侧写."""

    def test_no_api_response_leaks_traits_or_hidden_markers(self):
        self.run_pipeline(StubAI())
        self.befriend(self.a, self.e)
        with self.connect() as conn:
            grant_tier(conn, self.offering_id, int(self.a.split(":")[1]), 5)
            conn.commit()
        bodies = []
        stub = StubAI()
        with patch.object(social_ai_service, "default_caller", stub):
            for identity in (self.a, self.teacher):
                client = self.client_as(identity)
                for path in ("/api/social/scope", "/api/social/recommendations", "/api/social/users/suggestions",
                             "/api/social/users/search?q=同学", f"/api/social/users/{self.b}/card",
                             f"/api/social/users/{self.e}/card", f"/api/social/users/{self.a}/card",
                             f"/api/social/users/{self.b}/profile", "/api/social/friends",
                             "/api/social/friends/requests", "/api/social/conversations", "/api/social/groups",
                             "/api/social/blocks", f"/api/social/presence?identities={self.b},{self.e}"):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, f"{path}: {response.text}")
                    bodies.append((path, response.json()))
                response = client.post("/api/social/users/ai-search", json={"prompt": "找同学"})
                self.assertEqual(response.status_code, 200, response.text)
                bodies.append(("ai-search", response.json()))
        self.assertTrue(stub.calls)
        for path, payload in bodies:
            decoded = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(HIDDEN_MARKER, decoded, path)
            self.assertNotIn("隐藏的心理状态", decoded, path)
            for word in TRAIT_WORDS:
                self.assertNotIn(word, decoded, f"{path} leaks {word}")
            for kind in ("complement", "similar"):
                for item in payload.get(kind, []) if isinstance(payload, dict) else []:
                    self.assertIn(item["hint"], set(recs.HINTS.values()), path)
        with self.connect() as conn:
            notices = " ".join(f"{row[0]} {row[1]}" for row in conn.execute(
                "SELECT title, COALESCE(body_preview, '') FROM message_center_notifications"))
        self.assertNotIn(HIDDEN_MARKER, notices)

    def test_super_admin_runner_returns_counts_only(self):
        with patch.object(social_ai_service, "default_caller", StubAI()):
            response = self.client_as(self.teacher).post("/api/social/admin/recommendations/run")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(isinstance(value, int) for value in response.json().values()))
        denied = self.client_as(self.a).post("/api/social/admin/recommendations/run")
        self.assertEqual(denied.status_code, 403)

    def test_blank_prompt_is_refused(self):
        with self.assertRaises(SocialError):
            asyncio.run(ai_search.ai_search(self.user(self.teacher), "   "))
