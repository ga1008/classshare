"""Student review transitions, super-admin package explorer, optional AI risk check, and private mail queues."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import httpx

from classroom_app import dependencies
from classroom_app.services import minigame_content_assessment as assessment
from classroom_app.services import minigame_launch as launch_service
from classroom_app.services import minigame_moderation as moderation
from classroom_app.services import minigame_packages as packages
from classroom_app.services import minigame_platform as platform
from tests.test_minigame_platform import API, ROOT, GamePlatformFixture


def clear_verdict(refs):
    return {
        "covered": list(refs),
        "categories": {key: "clear" for key in assessment.CATEGORIES},
        "reason": "已逐项核对代码、配置和图片，无重大内容风险。",
        "report": {
            "summary": "全部脚本与美术素材均正常，包含少量常规游戏元素，符合校园小游戏规范。",
            "adultElementsNote": "未发现露骨或违规内容。",
            "reviewAdvice": "",
        },
        "evidence": [],
    }


def gateway_response(*args, **kwargs):
    payload = kwargs["json_payload"]
    refs = [f["name"].split(" ", 1)[0] for f in payload["file_texts"]]
    refs.extend(img["label"].split(" ", 1)[0] for img in payload["image_inputs"])
    return httpx.Response(
        200,
        request=httpx.Request("POST", "http://synthetic.invalid"),
        json={
            "status": "success",
            "response_json": clear_verdict(refs),
            "execution_metadata": {
                "task_type": payload["task_type"],
                "capability": payload["model_capability"],
                "thinking_type": "enabled",
                "operation": payload["business_context"]["operation"],
                "reasoning_effort": "high",
                "finish_reason": "stop",
            },
        },
    )


def major_risk_gateway_response(*args, **kwargs):
    payload = kwargs["json_payload"]
    refs = [f["name"].split(" ", 1)[0] for f in payload["file_texts"]]
    refs.extend(img["label"].split(" ", 1)[0] for img in payload["image_inputs"])
    code_ref = next((f["name"].split(" ", 1)[0] for f in payload["file_texts"] if "js/game.js" in f["name"]), refs[0])
    verdict = {
        "covered": refs,
        "categories": {
            **{key: "clear" for key in assessment.CATEGORIES},
            "drugs": "major_risk",
        },
        "reason": "检测到涉毒引导文本，建议超管人工复核后处理。",
        "report": {
            "summary": "在脚本中发现疑似违禁品推广片段，已标记具体文件。",
            "adultElementsNote": "未发现",
            "reviewAdvice": "请打开 js/game.js 核实台词上下文。",
        },
        "evidence": [{"id": code_ref, "reason": "存在疑似违禁品引导语句"}],
    }
    return httpx.Response(
        200,
        request=httpx.Request("POST", "http://synthetic.invalid"),
        json={
            "status": "success",
            "response_json": verdict,
            "execution_metadata": {
                "task_type": payload["task_type"],
                "capability": payload["model_capability"],
                "thinking_type": "enabled",
                "operation": payload["business_context"]["operation"],
                "reasoning_effort": "high",
                "finish_reason": "stop",
            },
        },
    )


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

    def submit(self, name="star-map", expected_status="manual"):
        package = ROOT / "docs/minigames/downloads" / (name + ".zip")
        with package.open("rb") as stream:
            receipt = packages.import_package(
                self.actor(931), uuid.uuid4().hex, stream, {"tags": ["像素风"], "allowRemix": False}
            )
        self.assertTrue(packages.worker_once())
        with platform.game_db() as conn:
            row = moderation.review_row(conn, receipt["versionId"])
            self.assertEqual((row["status"], row["version_status"]), (expected_status, "reviewing"))
            self.assertNotEqual(row["game_status"], "listed")
        return receipt

    def grant_super(self):
        with platform.game_db() as conn:
            conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=901")
            conn.commit()

    def test_manual_queue_package_explorer_and_advisory_ai(self):
        receipt = self.submit("star-map", expected_status="manual")
        vid = receipt["versionId"]
        url = self.base + "/" + vid

        # Non-super-admin teacher and student cannot access review workbench APIs.
        self.switch(931)
        self.assertEqual(self.client.get(self.base).status_code, 403)
        self.assertEqual(self.client.get(self.base + "/settings").status_code, 403)
        self.assertEqual(self.client.get(url + "/tree").status_code, 403)
        self.switch(901)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.grant_super()
        headers = self.switch(901)

        # Super admins manage the whole catalog, including student submissions.
        own_list = self.client.get(API + "/admin/games").json()["items"]
        self.assertEqual([g["id"] for g in own_list], [receipt["gameId"]])

        # Round-1 passed student game immediately appears in "小游戏审核" pending manual review queue.
        queue = self.client.get(self.base).json()
        self.assertEqual([item["versionId"] for item in queue["items"]], [vid])
        self.assertEqual(queue["counts"]["manual"], 1)
        self.assertFalse(queue["settings"]["autoAiReview"])

        # Super admin can inspect extracted folder/file tree, preview code/image safely, and download full ZIP.
        tree_resp = self.client.get(url + "/tree")
        self.assertEqual(tree_resp.status_code, 200, tree_resp.text)
        tree_data = tree_resp.json()
        self.assertEqual(tree_data["tree"]["node_type"], "folder")
        self.assertGreater(tree_data["stats"]["file_count"], 3)

        code_resp = self.client.get(url + "/file-content", params={"path": "js/game.js"})
        self.assertEqual(code_resp.status_code, 200, code_resp.text)
        self.assertTrue(len(code_resp.json()["content"]) > 20)

        # Raw HTML must NEVER serve inline on the platform main origin (forced attachment + sandbox CSP).
        html_file = self.client.get(url + "/file", params={"path": "main.html"})
        self.assertEqual(html_file.status_code, 200)
        self.assertIn("attachment", html_file.headers.get("Content-Disposition", ""))
        self.assertIn("sandbox", html_file.headers.get("Content-Security-Policy", ""))

        # Image preview serves inline safely with nosniff + sandbox CSP.
        img_file = self.client.get(url + "/file", params={"path": "static/icon.png"})
        self.assertEqual(img_file.status_code, 200)
        self.assertEqual(img_file.headers["Content-Type"], "image/png")
        self.assertEqual(img_file.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", img_file.headers["Content-Security-Policy"])

        icon_resp = self.client.get(url + "/icon")
        self.assertEqual(icon_resp.status_code, 200)
        self.assertEqual(icon_resp.headers["Content-Type"], "image/png")

        # Super admin can launch an isolated preview of a reviewing version on the content host.
        preview_resp = self.client.post(url + "/preview", headers=headers)
        self.assertEqual(preview_resp.status_code, 201, preview_resp.text)
        self.assertIn("previewVersion=" + vid, preview_resp.json()["url"])
        launched = launch_service.launch(
            self.actor(901),
            receipt["gameId"],
            {"hostInstance": "review-host-01", "purpose": "preview", "versionId": vid},
            uuid.uuid4().hex,
        )
        self.assertEqual(self.content.get(launched["contentEntryUrl"]).status_code, 200)

        # A favorable AI report still requires an explicit super-admin decision.
        review_detail = self.client.get(url).json()
        ai_trigger = self.client.post(
            url + "/ai-review", json={"expectedDigest": review_detail["digest"]}, headers=headers
        )
        self.assertEqual(ai_trigger.status_code, 200, ai_trigger.text)
        self.assertEqual(ai_trigger.json()["status"], "queued")

        gateway = AsyncMock(side_effect=gateway_response)
        with patch("classroom_app.services.ai_gateway_service.ai_gateway_post", gateway):
            self.assertTrue(asyncio.run(moderation.process_one()))
        call = gateway.await_args.kwargs
        self.assertEqual((call["student_id"], call["priority"]), (931, "P2"))
        payload = call["json_payload"]
        self.assertEqual(
            (payload["task_type"], payload["model_capability"]), (assessment.AI_TASK_DEEP_MULTIMODAL, "vision")
        )
        self.assertEqual(
            payload["business_context"], {"operation": "adjudication", "source_feature": "minigame_content_review"}
        )
        self.assertIn("允许包含少量的成人", payload["system_prompt"])
        self.assertTrue(any(f["name"].endswith("js/game.js") and f["content"] for f in payload["file_texts"]))
        self.assertTrue(any("像素风" in f["content"] for f in payload["file_texts"]))
        self.assertTrue(
            any(
                img["label"].find("static/icon.png") >= 0 and img["url"].startswith("data:image/png;base64,")
                for img in payload["image_inputs"]
            )
        )
        self.assertEqual(payload["tools"], [])
        with platform.game_db() as conn:
            row = moderation.review_row(conn, vid)
            self.assertEqual((row["status"], row["version_status"], row["game_status"]), ("manual", "reviewing", "draft"))
        decision = self.client.post(url + "/decision", headers=headers, json={
            "expectedDigest": review_detail["digest"], "decision": "approve", "reason": "已核对原包",
        })
        self.assertEqual(decision.status_code, 200, decision.text)
        with platform.game_db() as conn:
            row = moderation.review_row(conn, vid)
            self.assertEqual((row["status"], row["version_status"], row["game_status"]), ("approved", "ready", "listed"))
            self.assertTrue(platform.require_play(conn, self.actor(932), receipt["gameId"]))
        done_detail = self.client.get(url).json()
        self.assertEqual(done_detail["aiReport"]["verdict"], "approved")
        self.assertIsNone(moderation.claim())

    def test_auto_ai_setting_major_risk_and_unread_audio_escalate_to_manual_with_report(self):
        self.grant_super()
        headers = self.switch(901)

        # Enable automatic AI review setting.
        toggle = self.client.patch(self.base + "/settings", json={"autoAiReview": True}, headers=headers)
        self.assertEqual(toggle.status_code, 200, toggle.text)
        self.assertTrue(toggle.json()["autoAiReview"])

        # 1. Major risk from AI MUST escalate to manual (not auto-reject) with full report and super-admin notice.
        risky = self.submit("star-map", expected_status="queued")
        risky_vid = risky["versionId"]
        with patch(
            "classroom_app.services.ai_gateway_service.ai_gateway_post",
            AsyncMock(side_effect=major_risk_gateway_response),
        ):
            self.assertTrue(asyncio.run(moderation.process_one()))
        risky_detail = self.client.get(self.base + "/" + risky_vid).json()
        self.assertEqual(risky_detail["status"], "manual")
        self.assertEqual(risky_detail["aiReport"]["verdict"], "major_risk")
        self.assertIn("drugs", risky_detail["categories"])
        self.assertTrue(any(e["file"] == "js/game.js" for e in risky_detail["evidence"]))
        self.assertIn("js/game.js", risky_detail["aiReport"]["reviewAdvice"])

        # 2. Unreadable audio (.wav) escalates to manual, notifies durably, and requires bound super-admin decision.
        receipt = self.submit("beacon-relay", expected_status="queued")
        vid = receipt["versionId"]
        url = self.base + "/" + vid
        with patch("classroom_app.services.ai_gateway_service.ai_gateway_post", AsyncMock(side_effect=gateway_response)):
            self.assertTrue(asyncio.run(moderation.process_one()))
        while moderation.dispatch_notice():
            pass
        with platform.game_db() as conn:
            notice = conn.execute(
                "SELECT * FROM mg_review_notices WHERE version_id=? AND teacher_id=901", (vid,)
            ).fetchone()
            self.assertGreater(notice["inbox_id"], 0)
            self.assertEqual(notice["email_queued"], 0)
            conn.execute(
                "INSERT INTO teacher_email_configs(teacher_id,smtp_host,from_email) VALUES(901,'smtp.invalid','test@example.invalid')"
            )
            conn.execute("UPDATE mg_review_notices SET due=0")
            conn.commit()
        while moderation.dispatch_notice():
            pass
        with platform.game_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM message_center_notifications WHERE ref_id=?", (vid,)).fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM email_outbox WHERE notification_id=?", (notice["inbox_id"],)).fetchone()[0],
                1,
            )
        review = self.client.get(url).json()
        self.assertEqual(review["status"], "manual")
        self.assertEqual(review["aiReport"]["verdict"], "uncertain")
        self.assertTrue(any(item["file"].endswith(".wav") for item in review["evidence"]))
        self.assertTrue(any(item["path"] == "js/game.js" for item in review["files"]))

        package = self.client.get(review["packageUrl"])
        self.assertEqual(package.content, (ROOT / "docs/minigames/downloads/beacon-relay.zip").read_bytes())
        self.assertIn("attachment", package.headers["Content-Disposition"])

        decision = {"decision": "approve", "reason": "已完整试听并核查。", "expectedDigest": review["digest"]}
        self.assertEqual(self.client.post(url + "/decision", json=decision).status_code, 403)
        self.assertEqual(
            self.client.post(url + "/decision", json={**decision, "expectedDigest": "stale"}, headers=headers).status_code,
            409,
        )
        for _ in range(2):
            result = self.client.post(url + "/decision", json=decision, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual((result.json()["status"], result.json()["decider"]), ("approved", "teacher:901"))
            self.assertEqual(result.json()["aiReport"]["verdict"], "uncertain")
        self.assertEqual(
            self.client.post(url + "/decision", json={**decision, "decision": "reject"}, headers=headers).status_code,
            409,
        )

    def test_outdated_claim_and_cancelled_release_cannot_publish(self):
        self.grant_super()
        headers = self.switch(901)
        self.client.patch(self.base + "/settings", json={"autoAiReview": True}, headers=headers)
        self.submit("star-map", expected_status="queued")
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

    def test_missing_coverage_malformed_and_major_risk_all_escalate_to_manual(self):
        self.grant_super()
        headers = self.switch(901)
        self.client.patch(self.base + "/settings", json={"autoAiReview": True}, headers=headers)
        receipt = self.submit("star-map", expected_status="queued")
        with patch(
            "classroom_app.services.ai_gateway_service.ai_gateway_post",
            AsyncMock(
                return_value=httpx.Response(
                    200,
                    request=httpx.Request("POST", "http://synthetic.invalid"),
                    json={"status": "success", "response_json": {}},
                )
            ),
        ):
            self.assertTrue(asyncio.run(moderation.process_one()))
        with platform.game_db() as conn:
            self.assertEqual(moderation.get_summary(conn, receipt["versionId"])["status"], "manual")

        packet = {"index": {"F1": "main.html", "F2": "js/game.js"}, "unread": []}
        verdict = clear_verdict(["F1"])
        incomplete = assessment.conclusion(verdict, packet)
        self.assertEqual((incomplete["status"], incomplete["report"]["verdict"]), ("manual", "uncertain"))

        verdict["covered"].append("F2")
        approved = assessment.conclusion(verdict, packet)
        self.assertEqual((approved["status"], approved["report"]["verdict"]), ("approved", "approved"))

        verdict["categories"]["drugs"] = "major_risk"
        verdict["evidence"] = [{"id": "F2", "reason": "合成风险证据"}]
        flagged = assessment.conclusion(verdict, packet)
        self.assertEqual((flagged["status"], flagged["report"]["verdict"]), ("manual", "major_risk"))

        # Legacy "violation" label still normalizes cleanly to major_risk escalation.
        verdict["categories"]["drugs"] = "violation"
        flagged_legacy = assessment.conclusion(verdict, packet)
        self.assertEqual((flagged_legacy["status"], flagged_legacy["report"]["verdict"]), ("manual", "major_risk"))

        del verdict["categories"]["sexual"]
        with self.assertRaises(ValueError):
            assessment.conclusion(verdict, packet)
