"""散修管理 API / 页面（docs/wanderer-ai-free-agents-2026-10-25.md §6）。

隔离 SQLite + FastAPI TestClient（依赖覆盖登录身份）；行为引擎与天道用 stub，
不调用任何模型。
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.dependencies import get_current_teacher, get_current_user
from classroom_app.services import wanderer_identity_service
from tests.wanderer_test_support import WandererDbTestCase

API = "/api/manage/system/wanderers"


def _restore_module(key: str, previous) -> None:
    if previous is None:
        sys.modules.pop(key, None)
    else:
        sys.modules[key] = previous


class WandererAdminApiTest(WandererDbTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.connect() as conn:
            for name, email, flag in (("超管教师", "super@example.test", 1), ("普通教师", "plain@example.test", 0)):
                conn.execute(
                    "INSERT INTO teachers (name, email, hashed_password, is_super_admin) VALUES (?, ?, ?, ?)",
                    (name, email, "synthetic-hash", flag),
                )
            cls.super_id = conn.execute("SELECT id FROM teachers WHERE email = ?", ("super@example.test",)).fetchone()[0]
            cls.plain_id = conn.execute("SELECT id FROM teachers WHERE email = ?", ("plain@example.test",)).fetchone()[0]
            class_id = conn.execute("SELECT id FROM classes LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO students (student_id_number, name, class_id, enrollment_status, created_at) "
                "VALUES (?, ?, ?, 'active', '2026-10-25T08:00:00')",
                ("S-REAL-1", "真实学生", class_id),
            )
            cls.real_student_id = conn.execute(
                "SELECT id FROM students WHERE student_id_number = ?", ("S-REAL-1",)
            ).fetchone()[0]
            cls.wanderer_ids = [int(item["id"]) for item in wanderer_identity_service.list_wanderers(conn)]
            conn.commit()

    def setUp(self):
        super().setUp()
        self.addCleanup(app.dependency_overrides.pop, get_current_user, None)
        self.addCleanup(app.dependency_overrides.pop, get_current_teacher, None)

    # -- helpers ---------------------------------------------------------

    def client_as(self, user: dict) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: dict(user)
        if user["role"] == "teacher":
            app.dependency_overrides[get_current_teacher] = lambda: dict(user)
        else:
            app.dependency_overrides.pop(get_current_teacher, None)
        return TestClient(app)

    def super_client(self) -> TestClient:
        return self.client_as({"id": self.super_id, "role": "teacher", "name": "超管教师"})

    def plain_client(self) -> TestClient:
        return self.client_as({"id": self.plain_id, "role": "teacher", "name": "普通教师"})

    def student_client(self) -> TestClient:
        return self.client_as({"id": self.real_student_id, "role": "student", "name": "真实学生"})

    def install_engine(self, name: str, attribute: str | None, calls: list) -> None:
        key = f"classroom_app.services.{name}"
        module = types.ModuleType(key)
        if attribute:
            async def stub(conn_factory=None, **kwargs):
                calls.append(kwargs)
                return {"processed": 1, "stub": True}

            setattr(module, attribute, stub)
        self.addCleanup(_restore_module, key, sys.modules.get(key))
        sys.modules[key] = module

    # -- reads -----------------------------------------------------------

    def test_social_view_and_transcript_are_super_admin_only(self):
        wid = self.wanderer_ids[0]
        with self.connect() as conn:
            student_id = int(conn.execute("SELECT student_id FROM wanderer_agents WHERE id = ?", (wid,)).fetchone()[0])
            me = f"student:{student_id}"
            peer = f"student:{self.real_student_id}"
            key = f"dm:{me}|{peer}"
            conn.execute("INSERT INTO social_friendships (identity_a, identity_b, status, requester_identity, created_at) "
                         "VALUES (?, ?, 'accepted', ?, '2026-10-30T10:00:00')", (me, peer, me))
            conn.execute("INSERT INTO social_dm_participants (identity, conversation_key, last_message_id) VALUES (?, ?, 1)",
                         (me, key))
            conn.execute("INSERT INTO social_messages (conversation_type, conversation_key, sender_identity, body, "
                         "body_format, message_type, mentions_json, client_nonce, created_at) "
                         "VALUES ('dm', ?, ?, ?, 'text', 'text', '[]', 'admin-test-1', '2026-10-30T10:01:00')",
                         (key, peer, "管理端应能看到这条交流内容"))
            conn.commit()
        client = self.super_client()
        social = client.get(f"{API}/{wid}/social")
        self.assertEqual(social.status_code, 200, social.text)
        self.assertEqual(social.json()["counts"]["friends"], 1)
        chats = client.get(f"{API}/{wid}/conversations")
        self.assertEqual(chats.status_code, 200, chats.text)
        self.assertEqual(chats.json()["items"][0]["key"], key)
        messages = client.get(f"{API}/{wid}/conversations/{key}/messages")
        self.assertEqual(messages.status_code, 200, messages.text)
        self.assertIn("管理端应能看到这条交流内容", messages.json()["items"][0]["body"])
        denied = self.plain_client().get(f"{API}/{wid}/social")
        self.assertIn(denied.status_code, {401, 403})

    def test_list_detail_and_paged_lists(self):
        client = self.super_client()
        listing = client.get(API)
        self.assertEqual(listing.status_code, 200, listing.text)
        payload = listing.json()
        self.assertEqual(len(payload["wanderers"]), 20)
        self.assertIn("天道规则", payload["rules"])
        self.assertEqual(payload["overview"]["counts"]["total"], 20)
        card = payload["wanderers"][0]
        for key in ("id", "student_id", "slug", "tier_name", "emblem", "status", "today_actions",
                    "warnings_count", "promotion_requested", "avatar_url", "last_action_preview"):
            self.assertIn(key, card)
        self.assertIn(f"user_id={card['student_id']}", card["avatar_url"])

        detail = client.get(f"{API}/{card['id']}")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()["detail"]
        self.assertEqual(len(body["stats"]["daily"]), 7)
        self.assertEqual(body["knowledge"]["active_count"], 0)
        self.assertTrue(body["profile"]["persona_prompt"])
        self.assertIn("behaviors", body["profile"])
        self.assertIn("active", body["memory"])
        self.assertIsInstance(body["reflections"], list)
        self.assertIn("valence", body["mood"])
        self.assertIn("proactive_multiplier", body["self_tuning"])

        for kind in ("actions", "reviews", "knowledge", "memory", "reflections"):
            response = client.get(f"{API}/{card['id']}/{kind}?page=1")
            self.assertEqual(response.status_code, 200, f"{kind}: {response.text}")
        social = client.get(f"{API}/{card['id']}/social")
        self.assertEqual(social.status_code, 200, social.text)
        self.assertIn("friends", social.json())
        self.assertIn("groups", social.json())
        conversations = client.get(f"{API}/{card['id']}/conversations?page=1")
        self.assertEqual(conversations.status_code, 200, conversations.text)
        for kind in ("actions", "reviews", "knowledge"):
            response = client.get(f"{API}/{card['id']}/{kind}?page=1")
            self.assertEqual(response.status_code, 200, kind)
            self.assertEqual({"items", "page", "limit", "total", "pages"} - set(response.json()), set())
        self.assertEqual(len(client.get(f"{API}/{card['id']}/knowledge").json()["items"]), 0)
        self.assertEqual(client.get(f"{API}/999999").status_code, 404)

    def test_permissions(self):
        # app.py rewrites API 403 to 401 when no session cookie is present (TestClient has none).
        plain = self.plain_client()
        self.assertIn(plain.get(API).status_code, {401, 403})
        self.assertIn(plain.post(f"{API}/settings", json={"enabled": 0}).status_code, {401, 403})
        self.assertIn(plain.post(f"{API}/{self.wanderer_ids[0]}/wipe", json={"confirm": True}).status_code, {401, 403})
        self.assertIn(self.student_client().get(API).status_code, {401, 403})

    # -- writes ----------------------------------------------------------

    def test_patch_validation_and_cache_invalidation(self):
        client = self.super_client()
        wid = self.wanderer_ids[0]
        for bad in ({"activity_level": 9}, {"tier": 5}, {"schedule_json": "[1"}, {"interests_json": {}},
                    {"daily_action_budget": 101}, {"persona_prompt": "  "}, {}):
            self.assertEqual(client.patch(f"{API}/{wid}", json=bad).status_code, 400, bad)

        with self.connect() as conn:  # warm the 60s identity cache before the write
            student_id = wanderer_identity_service.load_wanderer(conn, wid)["student_id"]
            wanderer_identity_service.load_wanderers_by_student_ids(conn, [student_id])
        ok = client.patch(f"{API}/{wid}", json={
            "persona_prompt": "你是测试散修，说话简短。", "activity_level": 2,
            "schedule_json": {"active_hours": [[8, 10]]}, "behaviors_json": {"blog_like": 0},
        })
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["detail"]["profile"]["persona_prompt"], "你是测试散修，说话简短。")
        with self.connect() as conn:
            cached = wanderer_identity_service.load_wanderers_by_student_ids(conn, [student_id])[student_id]
        self.assertEqual(cached["persona_prompt"], "你是测试散修，说话简短。")
        self.assertEqual(cached["behaviors"]["blog_like"], 0)
        detail = client.get(f"{API}/{wid}").json()["detail"]
        self.assertEqual(detail["profile"]["activity_level"], 2)
        self.assertEqual(detail["profile"]["schedule"]["active_hours"], [[8, 10]])

    def test_status_pause_resume(self):
        client = self.super_client()
        wid = self.wanderer_ids[1]
        paused = client.post(f"{API}/{wid}/status", json={"status": "paused"})
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["card"]["status"], "paused")
        self.assertEqual(client.post(f"{API}/{wid}/status", json={"status": "dead"}).status_code, 400)
        resumed = client.post(f"{API}/{wid}/status", json={"status": "active"})
        self.assertEqual(resumed.json()["card"]["status"], "active")

    def test_set_tier_writes_admin_override_and_reset_warnings(self):
        client = self.super_client()
        wid = self.wanderer_ids[2]
        self.assertEqual(client.post(f"{API}/{wid}/tier", json={"tier": 4, "reason": ""}).status_code, 400)
        self.assertEqual(client.post(f"{API}/{wid}/tier", json={"tier": 11, "reason": "x"}).status_code, 400)
        response = client.post(f"{API}/{wid}/tier", json={"tier": 4, "reason": "测试调整"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["card"]["tier"], 4)
        override = client.get(f"{API}/{wid}/reviews").json()["items"][0]
        self.assertEqual((override["review_kind"], override["verdict"], override["to_tier"]), ("admin_override", "noted", 4))
        self.assertEqual(override["created_by"], f"teacher:{self.super_id}")

        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET warnings_count = 2 WHERE id = ?", (wid,))
            conn.commit()
        cleared = client.post(f"{API}/{wid}/reset-warnings")
        self.assertEqual(cleared.json()["card"]["warnings_count"], 0)

    def test_wipe_then_revive(self):
        client = self.super_client()
        wid = self.wanderer_ids[3]
        before_count=client.get(f"{API}/{wid}/knowledge").json()['summary']['active_count']
        self.assertEqual(client.post(f"{API}/{wid}/wipe", json={}).status_code, 400)
        wiped = client.post(f"{API}/{wid}/wipe", json={"confirm": True})
        self.assertEqual(wiped.status_code, 200, wiped.text)
        self.assertEqual(wiped.json()["card"]["status"], "dead")
        self.assertEqual(wiped.json()["forgotten"], 0)
        knowledge = client.get(f"{API}/{wid}/knowledge").json()
        self.assertEqual(knowledge["summary"]["active_count"], before_count)
        self.assertTrue(all(item["status"] == "active" for item in knowledge["items"]))
        self.assertEqual(client.post(f"{API}/{wid}/status", json={"status": "active"}).status_code, 400)

        revived = client.post(f"{API}/{wid}/revive")
        self.assertEqual(revived.status_code, 200, revived.text)
        self.assertEqual((revived.json()["card"]["status"], revived.json()["card"]["tier"]), ("active", 0))
        self.assertEqual(client.get(f"{API}/{wid}/knowledge").json()["summary"]["active_count"], 0)
        kinds = [item["review_kind"] for item in client.get(f"{API}/{wid}/reviews").json()["items"]]
        self.assertEqual(kinds[:2], ["revive", "death"])
        self.assertEqual(client.post(f"{API}/{wid}/revive").status_code, 400)

    def test_life_and_talent_detail_and_updates(self):
        client = self.super_client()
        wid = self.wanderer_ids[0]
        detail = client.get(f"{API}/{wid}").json()["detail"]
        self.assertIn("life", detail)
        self.assertIn("requirements", detail)
        self.assertIn("relationships", detail)
        self.assertIn("legacies", detail)
        self.assertEqual(set(detail["profile"]["talents"]), {"insight", "memory", "vitality", "eloquence", "charm", "luck"})
        self.assertIn("life_weeks", client.get(API).json()["wanderers"][0])

        bad = client.patch(f"{API}/{wid}", json={"talents_json": {"insight": 1.2}})
        self.assertEqual(bad.status_code, 400)
        talents = {**detail["profile"]["talents"], "insight": 1.3}
        saved = client.patch(f"{API}/{wid}", json={"talents_json": talents, "talent_reason": "合成测试"})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["detail"]["profile"]["talents"]["insight"], 1.3)
        kinds = [item["review_kind"] for item in client.get(f"{API}/{wid}/reviews").json()["items"]]
        self.assertIn("admin_override", kinds)

        before = saved.json()["detail"]["life"]["remaining_days"]
        extended = client.post(f"{API}/{wid}/life", json={"weeks": 1, "reason": "合成延寿"})
        self.assertEqual(extended.status_code, 200, extended.text)
        self.assertGreater(extended.json()["detail"]["life"]["remaining_days"], before)
        self.assertEqual(client.post(f"{API}/{wid}/life", json={"weeks": 1}).status_code, 400)
        self.assertEqual(client.post(f"{API}/{wid}/life", json={"weeks": 99, "reason": "太大"}).status_code, 400)

    def test_lifecycle_settings_switch(self):
        client = self.super_client()
        settings = client.get(f"{API}/settings").json()["settings"]
        self.assertTrue(settings["lifecycle_enabled"])
        off = client.post(f"{API}/settings", json={"lifecycle_enabled": 0})
        self.assertEqual(off.status_code, 200, off.text)
        self.assertFalse(off.json()["settings"]["lifecycle_enabled"])
        client.post(f"{API}/settings", json={"lifecycle_enabled": 1})

    def test_settings_get_and_update(self):
        client = self.super_client()
        current = client.get(f"{API}/settings")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["settings"]["dao_review_hour"], "03:30")
        for bad in ({"daily_ai_call_cap": 9000}, {"tick_batch_size": 0}, {"dao_review_hour": "25:00"},
                    {"reflection_hour": "25:00"}, {"prompt_char_budget": 10},
                    {"enabled": "maybe"}, {"unknown": 1}, {}):
            self.assertEqual(client.post(f"{API}/settings", json=bad).status_code, 400, bad)
        saved = client.post(f"{API}/settings", json={
            "enabled": 0, "daily_ai_call_cap": 120, "tick_batch_size": 3,
            "classroom_visits_enabled": 0, "dao_review_hour": "04:15",
            "reflection_enabled": 1, "reflection_hour": "22:45", "reflection_batch_size": 4,
            "prompt_char_budget": 90_000, "memory_retention_days": 200,
        })
        self.addCleanup(lambda: self.super_client().post(f"{API}/settings", json={
            "enabled": 1, "daily_ai_call_cap": 4096, "reflection_enabled": 1,
            "reflection_hour": "23:30", "reflection_batch_size": 10,
            "prompt_char_budget": 120_000, "memory_retention_days": 180,
        }))
        self.assertEqual(saved.status_code, 200, saved.text)
        settings = saved.json()["settings"]
        self.assertEqual((settings["enabled"], settings["daily_ai_call_cap"], settings["tick_batch_size"]), (False, 120, 3))
        self.assertEqual((settings["classroom_visits_enabled"], settings["dao_review_hour"]), (False, "04:15"))
        self.assertEqual((settings["reflection_enabled"], settings["reflection_hour"],
                          settings["reflection_batch_size"]), (True, "22:45", 4))
        self.assertEqual((settings["prompt_char_budget"], settings["memory_retention_days"]), (90_000, 200))
        self.assertEqual(client.get(API).json()["overview"]["daily_ai_call_cap"], 120)
        self.addCleanup(lambda: self.super_client().post(f"{API}/settings", json={"dao_review_hour": "03:30"}))
        with self.connect() as conn:
            dao_task = conn.execute("SELECT run_at FROM scheduled_tasks WHERE dedupe_key = 'wanderer:dao-daily'").fetchone()
        self.assertIsNotNone(dao_task)  # the hour change re-armed the daily review
        self.assertIn("T04:15:00", dao_task["run_at"])

    def test_reseed_is_idempotent(self):
        response = self.super_client().post(f"{API}/reseed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"], {"created": [], "knowledge_added": 0, "total": 20})

    # -- Phase B bridge --------------------------------------------------

    def capture_runs(self) -> list:
        """Replace the background runner: collect coroutines, drain them explicitly."""
        from classroom_app.routers import wanderers as wanderers_router

        pending: list = []
        original = wanderers_router.TASK_RUNNER
        wanderers_router.TASK_RUNNER = pending.append

        def restore():
            wanderers_router.TASK_RUNNER = original
            for coroutine in pending:
                coroutine.close()
            for kind in wanderers_router._RUNNING:
                wanderers_router._RUNNING[kind] = False

        self.addCleanup(restore)
        return pending

    @staticmethod
    def drain(pending: list) -> None:
        while pending:
            asyncio.run(pending.pop(0))

    def test_tick_and_review_now_run_in_background(self):
        tick_calls, review_calls = [], []
        self.install_engine("wanderer_behavior_service", "run_wanderer_tick_once", tick_calls)
        self.install_engine("heavenly_dao_service", "run_dao_review_once", review_calls)
        pending = self.capture_runs()
        client = self.super_client()
        wid = self.wanderer_ids[0]
        tick = client.post(f"{API}/tick-now", json={"wanderer_id": wid})
        self.assertEqual(tick.status_code, 202, tick.text)
        self.assertEqual(tick.json(), {"status": "accepted", "message": "已开始，稍后刷新查看结果"})
        self.assertEqual(tick_calls, [])  # not run inside the request
        busy = client.post(f"{API}/tick-now", json={})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json(), {"status": "error", "message": "已有一轮正在运行"})
        self.drain(pending)
        self.assertEqual(tick_calls[-1], {"wanderer_ids": [wid], "force": True})
        overview = client.get(API).json()["overview"]
        self.assertEqual(overview["last_manual_tick"]["status"], "ok")
        self.assertEqual(overview["last_manual_tick"]["summary"], {"processed": 1, "stub": True})

        self.assertEqual(client.post(f"{API}/tick-now", json={}).status_code, 202)
        self.drain(pending)
        self.assertIsNone(tick_calls[-1]["wanderer_ids"])
        self.assertEqual(client.post(f"{API}/tick-now", json={"wanderer_id": 999999}).status_code, 404)
        self.assertEqual(client.post(f"{API}/tick-now", json={"wanderer_id": "x"}).status_code, 400)

        review = client.post(f"{API}/{wid}/review-now")
        self.assertEqual(review.status_code, 202, review.text)
        self.assertEqual(client.post(f"{API}/review-now").status_code, 409)  # same kind in flight
        self.drain(pending)
        self.assertEqual(review_calls[-1], {"wanderer_ids": [wid], "force": True})
        self.assertEqual(client.post(f"{API}/review-now").status_code, 202)
        self.drain(pending)
        self.assertEqual(review_calls[-1], {"force": True})
        self.assertEqual(client.get(API).json()["overview"]["last_manual_review"]["status"], "ok")

    def test_background_failure_is_recorded_and_releases_the_lock(self):
        key = "classroom_app.services.heavenly_dao_service"
        module = types.ModuleType(key)

        async def broken(**_kwargs):
            raise RuntimeError("synthetic outage")

        module.run_dao_review_once = broken
        self.addCleanup(_restore_module, key, sys.modules.get(key))
        sys.modules[key] = module
        pending = self.capture_runs()
        client = self.super_client()
        self.assertEqual(client.post(f"{API}/review-now").status_code, 202)
        self.drain(pending)
        last = client.get(API).json()["overview"]["last_manual_review"]
        self.assertEqual(last["status"], "error")
        self.assertIn("synthetic outage", last["error"])
        self.assertEqual(client.post(f"{API}/review-now").status_code, 202)

    def test_action_items_expose_parsed_detail(self):
        wid = self.wanderer_ids[4]
        with self.connect() as conn:
            wanderer_identity_service.log_wanderer_action(
                conn, wid, "skip", target_type="tick", outcome="skipped", detail={"reason": "capped"},
                location="平台 · 自主活动")
            conn.commit()
        items = self.super_client().get(f"{API}/{wid}/actions").json()["items"]
        self.assertEqual(items[0]["detail"], {"reason": "capped"})
        self.assertEqual(items[0]["location"], "平台 · 自主活动")

    def test_engine_not_ready_returns_503(self):
        self.install_engine("heavenly_dao_service", None, [])
        response = self.super_client().post(f"{API}/review-now")
        self.assertEqual(response.status_code, 503)
        self.assertIn("行为引擎尚未就绪", response.text)

    # -- public card + page ------------------------------------------------

    def test_public_card(self):
        client = self.student_client()
        with self.connect() as conn:
            student_id = wanderer_identity_service.load_wanderer(conn, self.wanderer_ids[5])["student_id"]
        response = client.get(f"/api/wanderers/{student_id}/card")
        self.assertEqual(response.status_code, 200)
        card = response.json()["card"]
        self.assertTrue(card["is_wanderer"])
        for key in ("display_name", "archetype", "tier", "tier_name", "status", "description"):
            self.assertIn(key, card)
        self.assertEqual(client.get(f"/api/wanderers/{self.real_student_id}/card").status_code, 404)

    def test_manage_page_renders_for_super_admin(self):
        response = self.super_client().get("/manage/system/wanderers")
        self.assertEqual(response.status_code, 200, response.text[:500])
        self.assertIn("散修管理", response.text)
        self.assertIn("wd-card-grid", response.text)
        self.assertIn("manage_wanderers", response.text)
        self.assertIn("data-wd-avatar-form", response.text)  # design 11: silhouette select
        self.assertIn('<option value="sword">', response.text)
        # HTML 403 is turned into a redirect to the login page by app.py.
        denied = self.plain_client().get("/manage/system/wanderers", follow_redirects=False)
        self.assertIn(denied.status_code, {303, 401, 403})
        self.assertIn("wd-genesis-btn", response.text)
        self.assertIn('name="genesis_max_total"', response.text)
        self.assertIn('name="reflection_hour"', response.text)
        self.assertIn('name="prompt_char_budget"', response.text)
        self.assertIn('data-lq-tab="memory"', response.text)
        for tab in ('overview','memory','conversations','relations','activity','resources','identity'):
            self.assertIn(f'data-lq-tab="{tab}"',response.text)
        for control in ('daily_wake_limit','autonomous_v2_enabled'):
            self.assertIn(f'name="{control}"',response.text)
        for action in ('data-wd-persona-form','data-wd-life-form','data-wd-tier-form','data-wd-talent-form'):
            self.assertIn(action,response.text)

    # -- round 2: genesis (design §10.2 / §10.6) ------------------------------

    def test_genesis_settings_validation(self):
        client = self.super_client()
        settings = client.get(f"{API}/settings").json()["settings"]
        self.assertEqual(
            (settings["genesis_enabled"], settings["genesis_min_real_actions_24h"], settings["genesis_max_total"],
             settings["genesis_max_per_day"], settings["genesis_max_per_week"]),
            (True, 20, 50, 2, 5),
        )
        for bad in ({"genesis_enabled": 2}, {"genesis_max_total": -1}, {"genesis_max_total": 501},
                    {"genesis_max_per_day": 1.5}, {"genesis_min_real_actions_24h": "many"}):
            self.assertEqual(client.post(f"{API}/settings", json=bad).status_code, 400, bad)
        saved = client.post(f"{API}/settings", json={
            "genesis_enabled": 0, "genesis_min_real_actions_24h": 5, "genesis_max_total": 30,
            "genesis_max_per_day": 1, "genesis_max_per_week": 3,
        })
        self.addCleanup(lambda: self.super_client().post(f"{API}/settings", json={
            "genesis_enabled": 1, "genesis_min_real_actions_24h": 20, "genesis_max_total": 50,
            "genesis_max_per_day": 2, "genesis_max_per_week": 5}))
        self.assertEqual(saved.status_code, 200, saved.text)
        settings = saved.json()["settings"]
        self.assertEqual(
            (settings["genesis_enabled"], settings["genesis_min_real_actions_24h"], settings["genesis_max_total"],
             settings["genesis_max_per_day"], settings["genesis_max_per_week"]),
            (False, 5, 30, 1, 3),
        )
        self.assertIn(self.plain_client().post(f"{API}/settings", json={"genesis_enabled": 1}).status_code, {401, 403})

    def test_genesis_now_runs_in_background_for_super_admin(self):
        calls: list = []
        self.install_engine("heavenly_dao_genesis", "run_genesis", calls)
        pending = self.capture_runs()
        self.assertIn(self.plain_client().post(f"{API}/genesis-now").status_code, {401, 403})
        client = self.super_client()
        started = client.post(f"{API}/genesis-now")
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(calls, [])
        self.assertEqual(client.post(f"{API}/genesis-now").status_code, 409)
        self.drain(pending)
        self.assertEqual(calls, [{"manual": True}])
        overview = client.get(API).json()["overview"]
        self.assertEqual(overview["last_manual_genesis"]["status"], "ok")
        self.assertIn("dao_created", overview["counts"])
        card = client.get(API).json()["wanderers"][0]
        self.assertEqual((card["origin"], card["origin_label"]), ("seed", "种子"))
        self.assertTrue(card["homepage_url"].startswith("/wanderers/"))

    def test_workspace_sections_and_history_are_admin_only(self):
        wid=self.wanderer_ids[0]
        client=self.super_client()
        for section in ('overview','memory','conversations','relations','activity','resources','identity'):
            response=client.get(f'{API}/{wid}/workspace',params={'section':section})
            self.assertEqual(response.status_code,200,(section,response.text[:500]))
        for kind in ('friends','requests','groups','blocked','bond_requests','relationships','relationship_changes'):
            response=client.get(f'{API}/{wid}/workspace',params={'section':'relations','kind':kind,'q':'合成'})
            self.assertEqual(response.status_code,200,(kind,response.text[:500]))
        self.assertEqual(self.plain_client().get(f'{API}/{wid}/workspace?section=memory').status_code,403)
        self.assertEqual(self.student_client().get(f'{API}/{wid}/workspace?section=memory').status_code,403)
        with self.connect() as conn:
            self.assertGreaterEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_admin_access WHERE wanderer_id=?',(wid,)).fetchone()['n'],14)

    def test_admin_memory_pagination_retains_private_full_text(self):
        from classroom_app.services.wanderer_autobiography_service import write_memory
        from classroom_app.time_utils import local_iso
        wid=self.wanderer_ids[0]
        with self.connect() as conn:
            w=wanderer_identity_service.load_wanderer(conn,wid)
            write_memory(conn,w,{'content':'管理员可审阅的私密全文_验收','location':'私室'},turn_id='admin-test',
                         operation_id='admin-private-fixture',scope={'kind':'private'},available_evidence=[])
            conn.commit()
        response=self.super_client().get(f'{API}/{wid}/workspace?section=memory&q=私密全文_验收')
        self.assertEqual(response.status_code,200,response.text[:300])
        self.assertEqual(response.json()['items'][0]['content'],'管理员可审阅的私密全文_验收')
        public=self.student_client().get(f"/api/wanderers/{w['student_id']}/memory?q=私密全文_验收")
        self.assertEqual(public.status_code,200)
        self.assertEqual(public.json()['items'],[])

    def test_all_retained_ledger_kinds_are_scoped_paged_and_admin_only(self):
        import json
        from unittest.mock import patch
        from classroom_app.time_utils import local_iso
        from classroom_app.services import wanderer_bond_service as bonds, wanderer_resource_service as resources
        from classroom_app.services.wanderer_penalty_service import apply_penalty,reverse_penalty
        from classroom_app.services.wanderer_cultivation_service import award_cultivation
        from classroom_app.services.wanderer_life_service import apply_talent_change
        from classroom_app.services.wanderer_workspace_service import RECORDS
        a,b,c=self.wanderer_ids[-3:]
        with patch('classroom_app.services.heavenly_dao_service.notify_wanderer'):
            with self.connect() as conn:
                conn.execute("UPDATE wanderer_agents SET status='active',born_at=?,lifespan_weeks=52,life_penalty_weeks=0,cultivation_score=0 WHERE id IN (?,?,?)",(local_iso(),a,b,c))
                first,second=sorted((a,b))
                conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) VALUES('admin-ledger-bond',?,?,?,'active','fixture','fixture',?,?) ON CONFLICT(pair_key) DO UPDATE SET state='active'",
                             (f'{first}:{second}',first,second,local_iso(),local_iso()))
                self.assertTrue(resources.consume(conn,b,'ai_wake',1,'admin-ledger-loan',base_limits={a:1000,b:0})['ok'])
                self.assertTrue(resources.resize_capacity(conn,b,'knowledge_chars','admin-ledger-capacity',10,base_limits={a:1000,b:0})['ok'])
                apply_penalty(conn,a,{'cultivation':20},reason='完整原始裁决',operation_id='admin-ledger-penalty')
                award_cultivation(conn,a,5,'knowledge','admin-ledger-gain')
                apply_talent_change(conn,wanderer_identity_service.load_wanderer(conn,a),'luck',0.1,reason='合成增益',event_id='admin-ledger-talent')
                reverse_penalty(conn,'admin-ledger-penalty',reason='证据纠正',operation_id='admin-ledger-reverse')
                request=bonds.propose(conn,a,c,operation_id='admin-ledger-review',reason='申请审阅')
                conn.execute("UPDATE wanderer_bond_requests SET review_token='private-review-token' WHERE id=?",(request['id'],))
                for index in range(3):
                    conn.execute('INSERT INTO wanderer_dao_history_blocks(request_id,block_id,content_hash,content_json,created_at) VALUES(?,?,?,?,?)',
                                 (request['id'],f'block-{index}','hash',json.dumps({'record':f'完整历史{index}','api_key':'do-not-return','reasoning_content':'hidden-chain'}),local_iso()))
                conn.execute("INSERT INTO wanderer_dao_history_blocks(request_id,block_id,content_hash,content_json,created_at) VALUES('unrelated-request','unrelated-block','hash','{}',?)",(local_iso(),))
                conn.commit()
        client=self.super_client()
        def page(owner,kind,**params):
            response=client.get(f'{API}/{owner}/workspace',params={'section':'identity','kind':kind,**params})
            self.assertEqual(response.status_code,200,(kind,response.text[:500]))
            return response.json()
        listing=page(a,'')
        self.assertEqual(set(listing['summary']['record_kinds']),set(RECORDS))
        for kind in RECORDS:
            page(a,kind,limit=1)
        loans=page(a,'resource_reservations',q='admin-ledger-loan')['items']
        self.assertEqual([(r['actor_id'],r['operation_id']) for r in loans],[(b,'admin-ledger-loan')])
        self.assertIn('admin-ledger-capacity',{r['object_key'] for r in page(a,'capacity')['items']})
        self.assertEqual(page(c,'resource_reservations',q='admin-ledger-loan')['items'],[])
        self.assertEqual(page(b,'penalty_roots',q='完整原始裁决')['items'][0]['id'],'admin-ledger-penalty')
        self.assertEqual(page(b,'penalty_reversals',q='证据纠正')['items'][0]['root_id'],'admin-ledger-penalty')
        self.assertTrue(page(a,'gain_debt_payments',q='admin-ledger-gain')['items'])
        self.assertTrue(page(a,'talent_awards',q='合成增益')['items'])
        first_page=page(a,'dao_history_blocks',limit=2)
        second_page=page(a,'dao_history_blocks',limit=2,cursor=first_page['next_cursor'])
        self.assertEqual((first_page['total'],len(first_page['items']),len(second_page['items'])),(3,2,1))
        self.assertEqual(page(b,'dao_history_blocks')['items'],[])
        serialized=json.dumps(first_page)
        self.assertNotIn('do-not-return',serialized)
        self.assertNotIn('hidden-chain',serialized)
        self.assertNotIn('private-review-token',json.dumps(page(a,'bond_requests')))
        self.assertEqual(self.plain_client().get(f'{API}/{a}/workspace?section=identity&kind=dao_history_blocks').status_code,403)
        self.assertEqual(self.student_client().get(f'{API}/{a}/workspace?section=identity&kind=penalty_roots').status_code,403)

    def test_unrelated_attachment_cannot_be_read_through_admin_ai_scope(self):
        response=self.super_client().get(f'{API}/{self.wanderer_ids[0]}/attachments/legacy/999999')
        self.assertEqual(response.status_code,403,response.text)

    def test_genesis_reviews_expose_activity_snapshot(self):
        wid = self.wanderer_ids[6]
        with self.connect() as conn:
            wanderer_identity_service.insert_dao_review(
                conn, wanderer_id=wid, review_kind="genesis", verdict="created", from_tier=0, to_tier=0,
                reason="平台冷清", evidence={"activity": {"real_actions_24h": 3}, "seed_summary": {"slug": "x"}})
            conn.commit()
        items = self.super_client().get(f"{API}/{wid}/reviews").json()["items"]
        genesis = next(item for item in items if item["review_kind"] == "genesis")
        self.assertEqual((genesis["review_kind_label"], genesis["verdict_label"]), ("天道造人", "造人"))
        self.assertEqual(genesis["activity"], {"real_actions_24h": 3})


if __name__ == "__main__":
    unittest.main()
