"""Student review transitions using isolated browser sessions, real game ZIPs and private mail queues."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import httpx

from classroom_app import dependencies
from classroom_app.services import minigame_content_assessment as assessment
from classroom_app.services import minigame_moderation as moderation
from classroom_app.services import minigame_packages as packages
from classroom_app.services import minigame_platform as platform
from tests.test_minigame_platform import API, ROOT, GamePlatformFixture


def clear_verdict(refs):
    return {"covered": list(refs), "categories": {key: "clear" for key in assessment.CATEGORIES},
            "reason": "已逐项核对代码、配置和图片。", "evidence": []}


def gateway_response(*args, **kwargs):
    payload = kwargs["json_payload"]
    refs = [f["name"].split(" ", 1)[0] for f in payload["file_texts"]]
    refs.extend(img["label"].split(" ", 1)[0] for img in payload["image_inputs"])
    return httpx.Response(200, request=httpx.Request("POST", "http://synthetic.invalid"),
        json={"status": "success", "response_json": clear_verdict(refs), "execution_metadata": {
            "task_type": payload["task_type"], "capability": payload["model_capability"], "thinking_type": "enabled",
            "operation": payload["business_context"]["operation"], "reasoning_effort": "high", "finish_reason": "stop"}})


class GameModerationTests(GamePlatformFixture):
    base = API + "/moderation/reviews"

    def actor(self, uid):
        role = "teacher" if uid == 901 else "student"
        with platform.game_db() as conn:
            user = platform.load_actor(conn, f"{role}:{uid}")
        user["session_id"] = dependencies.decode_token_payload(self.tokens[uid])["session_id"]
        return user

    def switch(self, uid):
        self.client.cookies.set("access_token", self.tokens[uid])
        return {"Origin": "http://127.0.0.1", "X-LanGame-CSRF": platform.csrf_token(self.actor(uid))}

    def submit(self, name="star-map"):
        package = ROOT / "docs/minigames/downloads" / (name + ".zip")
        with package.open("rb") as stream:
            receipt = packages.import_package(self.actor(931), uuid.uuid4().hex, stream,
                                              {"tags": ["像素风"], "allowRemix": False})
        self.assertTrue(packages.worker_once())
        with platform.game_db() as conn:
            row = moderation.review_row(conn, receipt["versionId"])
            self.assertEqual((row["status"], row["version_status"]), ("queued", "reviewing"))
            self.assertNotEqual(row["game_status"], "listed")
        return receipt

    def grant_super(self):
        with platform.game_db() as conn:
            conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=901")
            conn.commit()

    def test_complete_visual_and_code_review_publishes_only_the_validated_version(self):
        receipt = self.submit()
        gateway = AsyncMock(side_effect=gateway_response)
        with patch("classroom_app.services.ai_gateway_service.ai_gateway_post", gateway):
            self.assertTrue(asyncio.run(moderation.process_one()))
        call = gateway.await_args.kwargs
        self.assertEqual((call["student_id"], call["priority"]), (931, "P2"))
        payload = call["json_payload"]
        self.assertEqual((payload["task_type"], payload["model_capability"]), (assessment.AI_TASK_DEEP_MULTIMODAL, "vision"))
        self.assertEqual(payload["business_context"], {"operation": "adjudication", "source_feature": "minigame_content_review"})
        self.assertTrue(any(f["name"].endswith("js/game.js") and f["content"] for f in payload["file_texts"]))
        self.assertTrue(any("像素风" in f["content"] for f in payload["file_texts"]))
        self.assertTrue(any(img["label"].find("static/icon.png") >= 0 and img["url"].startswith("data:image/png;base64,")
                            for img in payload["image_inputs"]))
        self.assertEqual(payload["tools"], [])
        with platform.game_db() as conn:
            row = moderation.review_row(conn, receipt["versionId"])
            self.assertEqual((row["status"], row["version_status"], row["game_status"]), ("approved", "ready", "listed"))
            self.assertTrue(platform.require_play(conn, self.actor(932), receipt["gameId"]))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mg_review_notices").fetchone()[0], 0)
        self.assertIsNone(moderation.claim())

    def test_unknown_audio_notifies_super_admin_durably_and_requires_bound_decision(self):
        receipt = self.submit("beacon-relay")
        vid = receipt["versionId"]
        url = self.base + "/" + vid
        self.switch(931)
        self.assertEqual(self.client.get(self.base).status_code, 403)
        self.assertEqual(self.client.get(url + "/package").status_code, 403)
        self.switch(901)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.grant_super()
        with patch("classroom_app.services.ai_gateway_service.ai_gateway_post", AsyncMock(side_effect=gateway_response)):
            self.assertTrue(asyncio.run(moderation.process_one()))
        self.assertTrue(moderation.dispatch_notice())  # Sender configuration is not available yet.
        with platform.game_db() as conn:
            notice = conn.execute("SELECT * FROM mg_review_notices WHERE version_id=? AND teacher_id=901", (vid,)).fetchone()
            self.assertGreater(notice["inbox_id"], 0)
            self.assertEqual(notice["email_queued"], 0)
            conn.execute("INSERT INTO teacher_email_configs(teacher_id,smtp_host,from_email) VALUES(901,'smtp.invalid','test@example.invalid')")
            conn.execute("UPDATE mg_review_notices SET due=0")
            conn.commit()
        self.assertTrue(moderation.dispatch_notice())
        self.assertFalse(moderation.dispatch_notice())
        with platform.game_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM message_center_notifications WHERE ref_id=?", (vid,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM email_outbox WHERE notification_id=?", (notice["inbox_id"],)).fetchone()[0], 1)
        self.assertEqual([item["versionId"] for item in self.client.get(self.base).json()["items"]], [vid])
        review = self.client.get(url).json()
        self.assertEqual(review["status"], "manual")
        self.assertTrue(any(item["file"].endswith(".wav") for item in review["evidence"]))
        self.assertTrue(any(item["path"] == "js/game.js" for item in review["files"]))
        package = self.client.get(review["packageUrl"])
        self.assertEqual(package.content, (ROOT / "docs/minigames/downloads/beacon-relay.zip").read_bytes())
        self.assertIn("attachment", package.headers["Content-Disposition"])
        decision = {"decision": "approve", "reason": "已完整试听并核查。", "expectedDigest": review["digest"]}
        headers = self.switch(901)
        self.assertEqual(self.client.post(url + "/decision", json=decision).status_code, 403)
        self.assertEqual(self.client.post(url + "/decision", json={**decision, "expectedDigest": "stale"}, headers=headers).status_code, 409)
        for _ in range(2):
            result = self.client.post(url + "/decision", json=decision, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual((result.json()["status"], result.json()["decider"]), ("approved", "teacher:901"))
        self.assertEqual(self.client.post(url + "/decision", json={**decision, "decision": "reject"}, headers=headers).status_code, 409)

    def test_outdated_claim_and_cancelled_release_cannot_publish(self):
        self.submit()
        first = moderation.claim()
        self.assertIsNone(moderation.claim())
        with platform.game_db() as conn:
            conn.execute("UPDATE mg_content_reviews SET lease_until=0")
            conn.commit()
        second = moderation.claim()
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        approve = {"status": "approved", "reason": "测试", "categories": [], "evidence": []}
        moderation.finish(first, approve)
        with platform.game_db() as conn:
            row = moderation.review_row(conn, first["version_id"])
            self.assertEqual(row["status"], "running")
            moderation.cancel(conn, first["game_id"])
            conn.commit()
        moderation.finish(second, approve)
        with platform.game_db() as conn:
            row = moderation.review_row(conn, first["version_id"])
            self.assertEqual(row["status"], "cancelled")
            self.assertNotEqual(row["game_status"], "listed")

    def test_missing_coverage_malformed_and_unconfirmed_route_remain_private(self):
        receipt = self.submit()
        with patch("classroom_app.services.ai_gateway_service.ai_gateway_post", AsyncMock(return_value=httpx.Response(
                200, request=httpx.Request("POST", "http://synthetic.invalid"), json={"status": "success", "response_json": {}}))):
            self.assertTrue(asyncio.run(moderation.process_one()))
        with platform.game_db() as conn:
            self.assertEqual(moderation.get_summary(conn, receipt["versionId"])["status"], "manual")
        packet = {"index": {"F1": "main.html", "F2": "js/game.js"}, "unread": []}
        verdict = clear_verdict(["F1"])
        self.assertEqual(assessment.conclusion(verdict, packet)["status"], "manual")
        verdict["covered"].append("F2")
        verdict["categories"]["drugs"] = "violation"
        self.assertEqual(assessment.conclusion(verdict, packet)["status"], "manual")
        verdict["evidence"] = [{"id": "F2", "reason": "合成测试证据"}]
        self.assertEqual(assessment.conclusion(verdict, packet)["status"], "rejected")
        del verdict["categories"]["sexual"]
        with self.assertRaises(ValueError):
            assessment.conclusion(verdict, packet)
