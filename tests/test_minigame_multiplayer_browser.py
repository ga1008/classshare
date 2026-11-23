"""Two independent authenticated browser contexts exercising the production game and room servers."""
import asyncio
import json
import os
import re
import sys
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

import test_minigame_platform as platform_fixture
from classroom_app.db import schema_social
from classroom_app.services import minigame_runtime


@contextmanager
def room_scheduler():
    # The fixture HTTP servers deliberately disable global lifespan. Start only the game scheduler.
    ready = threading.Event()
    ended = threading.Event()

    async def serve():
        minigame_runtime.start_runtime()
        ready.set()
        try:
            while not ended.is_set():
                await asyncio.sleep(.1)
        finally:
            await minigame_runtime.stop_runtime()

    worker = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    worker.start()
    try:
        if not ready.wait(5):
            raise RuntimeError("room scheduler failed to start")
        yield
    finally:
        ended.set()
        worker.join(10)
        if worker.is_alive():
            raise RuntimeError("room scheduler failed to stop")


@skipUnless(os.environ.get("MINIGAMES_BROWSER_INTEGRATION") == "1", "opt-in two-browser network test")
class MultiplayerBrowserTests(TestCase):
    def setUp(self):
        self.enterContext(patch.object(schema_social, "_SCHEMA_READY", False))
        self.fixture = platform_fixture.GamePlatformTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def capture(self, page, name):
        dest = os.environ.get("MINIGAMES_BROWSER_SCREENSHOTS")
        if not dest:
            return
        path = Path(dest).resolve()
        self.assertTrue(path.is_relative_to((platform_fixture.ROOT / ".codex-temp/evidence").resolve()))
        path.mkdir(exist_ok=True, parents=True)
        page.screenshot(path=str(path / (name + ".png")))

    def test_private_room_seats_chat_reconnect_and_shared_ending(self):
        from playwright.sync_api import expect, sync_playwright

        fixture = self.fixture
        with (platform_fixture.server(platform_fixture.app, "127.0.0.1") as main,
              platform_fixture.server(platform_fixture.content_app, "localhost") as content,
              patch.dict(os.environ, {"MINIGAMES_APP_ORIGIN": main, "MINIGAMES_CONTENT_ORIGIN": content})):
            game = fixture.publish(True)
            with room_scheduler(), sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=fixture.browser_bin(), chromium_sandbox=os.name != "nt")
                try:
                    def player(uid):
                        context = browser.new_context()
                        context.add_cookies([{"name": "access_token", "value": fixture.tokens[uid], "url": main}])
                        page = context.new_page()
                        page.set_default_timeout(15000)
                        page.goto(main + "/games/" + game["slug"], wait_until="domcontentloaded")
                        frame = page.frame_locator("iframe[sandbox]")
                        frame.get_by_role("button", name="东", exact=True).wait_for()
                        return context, page, frame

                    _, owner, a = player(931)
                    guest_context, guest, b = player(932)
                    owner.get_by_role("button", name="创建队伍").click()
                    owner.get_by_label("队伍人数").select_option("2")
                    owner.get_by_label("队伍名称（选填）").fill("联机验收")
                    owner.get_by_role("button", name="创建", exact=True).click()
                    code_view = owner.locator("[data-code-text]")
                    expect(code_view).to_have_text(re.compile(r"^[A-Z2-9]{4}$"))
                    code = code_view.inner_text()
                    self.assertRegex(code, "[A-Z]")
                    self.assertRegex(code, "[2-9]")
                    guest.get_by_role("button", name="加入队伍", exact=True).click()
                    guest.get_by_label("四位队伍码").fill(code.lower())
                    join = time.monotonic()
                    guest.get_by_role("button", name="加入", exact=True).click()
                    expect(guest.locator("[data-countdown]")).to_have_text("5")
                    expect(owner.locator("[data-countdown]")).to_be_visible()
                    self.capture(owner, "01-private-countdown")
                    a.get_by_role("button", name="东", exact=True).wait_for()
                    self.assertGreaterEqual(time.monotonic() - join, 4.8)
                    expect(b.get_by_role("button", name="东", exact=True)).to_be_disabled()
                    a.get_by_role("button", name="东", exact=True).click()
                    b.get_by_role("button", name="南", exact=True).click()
                    a.get_by_text("夕阳正在海平面落下", exact=False).wait_for()

                    owner.get_by_role("button", name="队伍与对话").click()
                    owner.get_by_role("textbox", name="本局消息").fill("下一步请向西")
                    owner.get_by_role("button", name="发送", exact=True).click()
                    guest.get_by_role("button", name=re.compile("队伍与对话")).click()
                    guest.get_by_text("下一步请向西", exact=True).wait_for()
                    self.capture(guest, "02-room-chat")
                    guest.keyboard.press("Escape")
                    owner.keyboard.press("Escape")
                    run_id = fixture.api(931, "GET", "/me/activity?gameId=" + game["id"])["multiplayer"]["runId"]

                    # The second browser actually goes offline. The production lease and recovery grace run in real time.
                    guest_context.set_offline(True)
                    owner.get_by_role("heading", name="等待队友重连").wait_for(timeout=75000)
                    self.capture(owner, "03-reconnecting")
                    guest_context.set_offline(False)
                    expect(a.get_by_role("button", name="西", exact=True)).to_be_enabled(timeout=20000)
                    a.get_by_role("button", name="西", exact=True).click()
                    b.get_by_role("button", name="引导船队返航").click()
                    for host in (owner, guest):
                        host.get_by_role("heading", name="本局结束").wait_for()
                    for uid in (931, 932):
                        items = fixture.api(uid, "GET", "/me/history?gameId=" + game["id"])["items"]
                        self.assertTrue(any(r["runId"] == run_id and r["outcome"] == "completed" for r in items))
                        self.assertEqual(fixture.api(uid, "GET", "/me/progress/" + game["id"])["rank"], 2)
                    owner.get_by_role("button", name="历史队伍").click()
                    owner.get_by_text(re.compile("联机验收 · 2 人")).wait_for()
                    owner.get_by_role("button", name="按此配置新建").click()
                    expect(owner.get_by_label("队伍人数")).to_have_value("2")
                    expect(owner.get_by_label("队伍名称（选填）")).to_have_value("联机验收")
                    self.capture(owner, "04-team-template")
                    owner.get_by_role("button", name="创建", exact=True).click()
                    expect(code_view).to_have_text(re.compile(r"^[A-Z2-9]{4}$"))
                    self.assertNotEqual(code_view.inner_text(), code)
                    self.assertIsNone(fixture.api(932, "GET", "/me/activity?gameId=" + game["id"])["multiplayer"])
                finally:
                    browser.close()

    def publish_sample(self, key="three-in-line"):
        f = self.fixture
        z = platform_fixture.ROOT / "docs/minigames/downloads" / (key + ".zip")
        checked = f.root / ("checked-" + key)
        checked.mkdir()
        result = platform_fixture.validate_package(z, checked)
        game = f.api(901, "POST", "/admin/games", json={"scope": "public"})["game"]
        path = "/admin/games/" + game["id"]
        with z.open("rb") as handle:
            f.api(901, "POST", path + "/versions", data={"expectedRevision": str(game["revision"])},
                  files={"file": (z.name, handle, "application/zip")})
        command = [sys.executable, str(platform_fixture.ROOT / "tools/minigame_browser_probe.py"), "--browser",
                   f.browser_bin(), "--synthetic-digest", result["digest"]]
        if os.name == "nt":
            command.append("--trusted-browser-harness")
        with patch.dict(os.environ, {"MINIGAMES_PROBE_COMMAND": json.dumps(command)}):
            platform_fixture.pk.worker_once()
        detail = f.api(901, "GET", path)
        version = detail["versions"][0]
        self.assertEqual(version["status"], "ready", detail["jobs"][0])
        preview = f.api(901, "POST", "/games/" + game["id"] + "/launches",
              json={"purpose": "preview", "versionId": version["id"], "hostInstance": "teacher-board"})
        self.assertEqual(f.content.get(preview["contentEntryUrl"]).status_code, 200)
        revision = detail["game"]["revision"]
        f.api(901, "POST", path + "/versions/" + version["id"] + "/playtests", json={"expectedRevision": revision, "passed": True})
        return f.api(901, "POST", path + "/publish", json={"expectedRevision": revision, "versionId": version["id"]})["game"]

    def test_random_board_cancel_rematch_winner_and_offline_games(self):
        from playwright.sync_api import expect, sync_playwright

        f = self.fixture
        with (platform_fixture.server(platform_fixture.app, "127.0.0.1") as main,
              platform_fixture.server(platform_fixture.content_app, "localhost") as content,
              patch.dict(os.environ, {"MINIGAMES_APP_ORIGIN": main, "MINIGAMES_CONTENT_ORIGIN": content})):
            game = self.publish_sample()
            with room_scheduler(), sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=f.browser_bin(), chromium_sandbox=os.name != "nt")
                try:
                    pages = []
                    for uid in (931, 932):
                        context = browser.new_context()
                        context.add_cookies([{"name": "access_token", "value": f.tokens[uid], "url": main}])
                        page = context.new_page()
                        page.set_default_timeout(15000)
                        page.goto(main + "/games/" + game["slug"])
                        pages.append(page)

                    def queue(p):
                        p.get_by_role("button", name="随机匹配").click()
                        p.get_by_role("button", name="开始匹配", exact=True).click()

                    queue(pages[0])
                    pages[0].get_by_role("heading", name="正在匹配队友").wait_for()
                    pages[0].get_by_role("button", name="退出等待").click()
                    pages[0].get_by_role("button", name="确认", exact=True).click()
                    expect(pages[0].get_by_role("button", name="随机匹配")).to_be_visible()
                    self.assertIsNone(f.api(931, "GET", "/me/activity?gameId=" + game["id"])["multiplayer"])
                    for page in pages:
                        queue(page)
                    frames = [p.frame_locator("iframe[sandbox]") for p in pages]
                    cells = ["第 1 行第 1 列", "第 2 行第 1 列", "第 1 行第 2 列", "第 2 行第 2 列", "第 1 行第 3 列"]
                    frames[0].get_by_role("button", name=cells[0], exact=True).wait_for()
                    frames[1].get_by_role("button", name=cells[0], exact=True).wait_for()
                    first = 0 if frames[0].get_by_role("button", name=cells[0], exact=True).is_enabled() else 1
                    for turn, name in enumerate(cells):
                        frames[(first + turn) % 2].get_by_role("button", name=name, exact=True).click()
                    for page in pages:
                        page.get_by_text("第 1 席获胜。", exact=True).wait_for()
                    self.capture(pages[0], "05-online-board-winner")
                    for uid in (931, 932):
                        result = f.api(uid, "GET", "/me/history?gameId=" + game["id"])["items"][0]
                        self.assertEqual((result["mode"], result["outcome"]), ("random", "actorWins"))

                    local = browser.new_context(offline=True).new_page()
                    for name in ("star-map", "three-in-line"):
                        target = f.root / ("local-" + name)
                        with zipfile.ZipFile(platform_fixture.ROOT / "docs/minigames/downloads" / (name + ".zip")) as bundle:
                            bundle.extractall(target)
                        local.goto((target / "main.html").as_uri())
                        if name == "star-map":
                            for answer in ("△", "七", "✦"):
                                local.get_by_role("button", name=answer, exact=True).click()
                            local.get_by_text("已完成本局。", exact=True).wait_for()
                        else:
                            for label in cells:
                                local.get_by_role("button", name=label, exact=True).click()
                            local.get_by_text("第 1 席连线获胜", exact=True).wait_for()
                        self.capture(local, "06-local-" + name)
                        self.assertEqual(local.evaluate("async()=> (await LanGame.ready()).environment"), "local")
                finally:
                    browser.close()
