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
        self.assertEqual(body["knowledge"]["active_count"], 3)
        self.assertTrue(body["profile"]["persona_prompt"])
        self.assertIn("behaviors", body["profile"])

        for kind in ("actions", "reviews", "knowledge"):
            response = client.get(f"{API}/{card['id']}/{kind}?page=1")
            self.assertEqual(response.status_code, 200, kind)
            self.assertEqual({"items", "page", "limit", "total", "pages"} - set(response.json()), set())
        self.assertEqual(len(client.get(f"{API}/{card['id']}/knowledge").json()["items"]), 3)
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
        self.assertEqual(client.post(f"{API}/{wid}/wipe", json={}).status_code, 400)
        wiped = client.post(f"{API}/{wid}/wipe", json={"confirm": True})
        self.assertEqual(wiped.status_code, 200, wiped.text)
        self.assertEqual(wiped.json()["card"]["status"], "dead")
        self.assertEqual(wiped.json()["forgotten"], 3)
        knowledge = client.get(f"{API}/{wid}/knowledge").json()
        self.assertEqual(knowledge["summary"]["active_count"], 0)
        self.assertTrue(all(item["status"] == "forgotten" for item in knowledge["items"]))
        self.assertEqual(client.post(f"{API}/{wid}/status", json={"status": "active"}).status_code, 400)

        revived = client.post(f"{API}/{wid}/revive")
        self.assertEqual(revived.status_code, 200, revived.text)
        self.assertEqual((revived.json()["card"]["status"], revived.json()["card"]["tier"]), ("active", 0))
        self.assertEqual(client.get(f"{API}/{wid}/knowledge").json()["summary"]["active_count"], 3)
        kinds = [item["review_kind"] for item in client.get(f"{API}/{wid}/reviews").json()["items"]]
        self.assertEqual(kinds[:2], ["revive", "death"])
        self.assertEqual(client.post(f"{API}/{wid}/revive").status_code, 400)

    def test_settings_get_and_update(self):
        client = self.super_client()
        current = client.get(f"{API}/settings")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["settings"]["dao_review_hour"], "03:30")
        for bad in ({"daily_ai_call_cap": 9000}, {"tick_batch_size": 0}, {"dao_review_hour": "25:00"},
                    {"enabled": "maybe"}, {"unknown": 1}, {}):
            self.assertEqual(client.post(f"{API}/settings", json=bad).status_code, 400, bad)
        saved = client.post(f"{API}/settings", json={
            "enabled": 0, "daily_ai_call_cap": 120, "tick_batch_size": 3,
            "classroom_visits_enabled": 0, "dao_review_hour": "04:15",
        })
        self.addCleanup(lambda: self.super_client().post(f"{API}/settings", json={"enabled": 1, "daily_ai_call_cap": 400}))
        self.assertEqual(saved.status_code, 200, saved.text)
        settings = saved.json()["settings"]
        self.assertEqual((settings["enabled"], settings["daily_ai_call_cap"], settings["tick_batch_size"]), (False, 120, 3))
        self.assertEqual((settings["classroom_visits_enabled"], settings["dao_review_hour"]), (False, "04:15"))
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
                conn, wid, "skip", target_type="tick", outcome="skipped", detail={"reason": "capped"})
            conn.commit()
        items = self.super_client().get(f"{API}/{wid}/actions").json()["items"]
        self.assertEqual(items[0]["detail"], {"reason": "capped"})

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
        # HTML 403 is turned into a redirect to the login page by app.py.
        denied = self.plain_client().get("/manage/system/wanderers", follow_redirects=False)
        self.assertIn(denied.status_code, {303, 401, 403})
        self.assertIn("wd-genesis-btn", response.text)
        self.assertIn('name="genesis_max_total"', response.text)

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
