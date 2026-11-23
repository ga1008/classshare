"""Real-browser recovery of an interrupted random-match countdown, including a read-only tab."""

import os
from unittest import TestCase, skipUnless
from unittest.mock import patch

import test_minigame_multiplayer_browser as multiplayer
import test_minigame_platform as platform_fixture


@skipUnless(
    os.environ.get("MINIGAMES_BROWSER_INTEGRATION") == "1",
    "opt-in real game browser test",
)
class RandomCountdownRecoveryTests(TestCase):
    def test_random_countdown_departure_restores_owned_ticket_without_requeue(self):
        from playwright.sync_api import expect, sync_playwright

        publisher = multiplayer.MultiplayerBrowserTests()
        self.addCleanup(publisher.doCleanups)
        publisher.setUp()
        f = publisher.fixture
        with (
            platform_fixture.server(platform_fixture.app, "127.0.0.1") as main,
            platform_fixture.server(
                platform_fixture.content_app, "localhost"
            ) as content,
            patch.dict(
                os.environ,
                {"MINIGAMES_APP_ORIGIN": main, "MINIGAMES_CONTENT_ORIGIN": content},
            ),
        ):
            game = publisher.publish_sample()
            activity_path = "/me/activity?gameId=" + game["id"]
            with multiplayer.room_scheduler(), sync_playwright() as pw:
                browser = pw.chromium.launch(
                    executable_path=f.browser_bin(), chromium_sandbox=os.name != "nt"
                )
                try:

                    def open_game(uid, context=None):
                        ctx = context or browser.new_context()
                        ctx.add_cookies(
                            [
                                {
                                    "name": "access_token",
                                    "value": f.tokens[uid],
                                    "url": main,
                                }
                            ]
                        )
                        page = ctx.new_page()
                        page.set_default_timeout(10000)
                        page.goto(
                            main + "/games/" + game["slug"],
                            wait_until="domcontentloaded",
                        )
                        return page

                    def queue(page):
                        page.get_by_role("button", name="随机匹配", exact=True).click()
                        page.get_by_role("button", name="开始匹配", exact=True).click()

                    def leave(page):
                        page.get_by_role("button", name="退出等待", exact=True).click()
                        page.get_by_role("button", name="确认", exact=True).click()

                    a, b, c = open_game(931), open_game(932), open_game(901)
                    queue_posts = []
                    a.on(
                        "request",
                        lambda r: (
                            queue_posts.append(r.method)
                            if r.url.endswith("/queue")
                            else None
                        ),
                    )
                    duplicate_replies = []

                    def duplicate_leave(route):
                        first = route.fetch()
                        second = route.fetch()
                        duplicate_replies.append(
                            (first.status, second.status, first.json() == second.json())
                        )
                        route.fulfill(response=first)

                    b.route("**/api/games/v1/rooms/*/leave", duplicate_leave)

                    queue(a)
                    expect(
                        a.get_by_role("heading", name="正在匹配队友")
                    ).to_be_visible()
                    before = f.api(931, "GET", activity_path)["multiplayer"]
                    queue(b)
                    expect(a.locator("[data-countdown]")).to_be_visible()
                    mirror = open_game(931, a.context)
                    expect(
                        mirror.get_by_role("heading", name="游戏已在另一页面打开")
                    ).to_be_visible()
                    expect(
                        mirror.get_by_role("button", name="队伍与对话")
                    ).to_be_visible()
                    leave(b)
                    expect(
                        a.get_by_role("heading", name="正在匹配队友")
                    ).to_be_visible()
                    after = f.api(931, "GET", activity_path)["multiplayer"]
                    self.assertEqual(
                        (after["phase"], after["ticketId"], after["queuedAt"]),
                        ("waiting", before["ticketId"], before["queuedAt"]),
                    )
                    expect(
                        mirror.get_by_role("button", name="队伍与对话")
                    ).to_be_hidden()
                    expect(
                        mirror.get_by_role("button", name="接管控制")
                    ).to_be_visible()
                    expect(mirror.get_by_role("button", name="退出等待")).to_be_hidden()
                    expect(
                        b.get_by_role("button", name="随机匹配", exact=True)
                    ).to_be_visible()
                    self.assertIsNone(f.api(932, "GET", activity_path)["multiplayer"])
                    leave(a)
                    expect(
                        a.get_by_role("button", name="随机匹配", exact=True)
                    ).to_be_visible()
                    self.assertIsNone(f.api(931, "GET", activity_path)["multiplayer"])
                    mirror.close()

                    queue(a)
                    queue(b)
                    expect(a.locator("[data-countdown]")).to_be_visible()
                    old_room = f.api(931, "GET", activity_path)["multiplayer"]["roomId"]
                    leave(b)
                    queue(c)
                    a.frame_locator("iframe[sandbox]").get_by_role(
                        "button", name="第 1 行第 1 列"
                    ).wait_for(timeout=16000)
                    room = f.api(931, "GET", activity_path)["multiplayer"]
                    self.assertEqual(
                        room["roomId"],
                        f.api(901, "GET", activity_path)["multiplayer"]["roomId"],
                    )
                    self.assertNotEqual(room["roomId"], old_room)
                    self.assertIsNone(f.api(932, "GET", activity_path)["multiplayer"])
                    self.assertEqual(queue_posts, ["POST", "POST"])
                    self.assertEqual(
                        duplicate_replies, [(200, 200, True), (200, 200, True)]
                    )
                finally:
                    browser.close()
