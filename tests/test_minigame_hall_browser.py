"""Real three-package hall. Observe layout from navigation and operate each layout using a keyboard."""

import json
import os
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

import test_minigame_multiplayer_browser as multiplayer
import test_minigame_platform as fixture


@skipUnless(
    os.environ.get("MINIGAMES_BROWSER_INTEGRATION") == "1",
    "opt-in real hall browser test",
)
class HallBrowserTests(TestCase):
    def test_layout_stability_keyboard_and_reduced_motion(self):
        from playwright.sync_api import expect, sync_playwright

        publisher = multiplayer.MultiplayerBrowserTests()
        self.addCleanup(publisher.doCleanups)
        publisher.setUp()
        f = publisher.fixture
        with (
            fixture.server(fixture.app, "127.0.0.1") as main,
            fixture.server(fixture.content_app, "localhost") as content,
            patch.dict(
                os.environ,
                {"MINIGAMES_APP_ORIGIN": main, "MINIGAMES_CONTENT_ORIGIN": content},
            ),
        ):
            recent = f.publish(True)
            publisher.publish_sample("star-map")
            publisher.publish_sample("three-in-line")
            f.api(
                931,
                "POST",
                "/games/" + recent["id"] + "/runs",
                json={"mode": "solo", "hostInstance": "hall-fixture"},
            )
            evidence = {}
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    executable_path=f.browser_bin(), chromium_sandbox=os.name != "nt"
                )
                try:
                    for width in (1440, 390):
                        mobile = width == 390
                        context = browser.new_context(
                            viewport={"width": width, "height": 844 if mobile else 900},
                            is_mobile=mobile,
                            has_touch=mobile,
                            reduced_motion="no-preference",
                        )
                        context.add_cookies(
                            [
                                {
                                    "name": "access_token",
                                    "value": f.tokens[931],
                                    "url": main,
                                }
                            ]
                        )
                        context.add_init_script("""
                            window.gameLayoutShifts = [];
                            if (PerformanceObserver.supportedEntryTypes.includes('layout-shift')) {
                                window.gameLayoutObserver = new PerformanceObserver(list => {
                                    for (const e of list.getEntries()) if (!e.hadRecentInput)
                                        window.gameLayoutShifts.push({time:e.startTime,value:e.value});
                                });
                                window.gameLayoutObserver.observe({type:'layout-shift', buffered:true});
                            }
                        """)
                        page = context.new_page()
                        page.goto(main + "/games", wait_until="networkidle")
                        tiles = page.locator("[data-games-grid] .game-tile")
                        expect(tiles).to_have_count(3)
                        page.wait_for_function("""() => [...document.querySelectorAll('[data-games-grid] img')]
                            .every(image=>image.complete && image.naturalWidth>0)""")
                        measure = page.evaluate("""async () => {
                            await document.fonts.ready;
                            await Promise.all([...document.querySelectorAll('[data-games-grid] img')].map(i=>i.decode()));
                            await new Promise(resolve=>setTimeout(resolve,1200));
                            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                            if(!window.gameLayoutObserver) throw Error('Layout Shift API is unavailable');
                            let total=0, peak=0, score=0, start=0, previous=-Infinity;
                            for(const e of window.gameLayoutShifts){
                                if(e.time-previous>=1000 || e.time-start>=5000){start=e.time; score=0;}
                                total+=e.value; score+=e.value; peak=Math.max(peak,score); previous=e.time;
                            }
                            return {cls:peak,totalShiftScore:total,shiftEntries:window.gameLayoutShifts.length,
                                decodedGameCovers:document.querySelectorAll('[data-games-grid] img').length,
                                fonts:document.fonts.status, viewport:innerWidth};
                        }""")
                        self.assertEqual(measure["decodedGameCovers"], 3)
                        self.assertEqual(measure["viewport"], width)
                        self.assertLessEqual(measure["cls"], 0.1)
                        first = tiles.first
                        if not mobile:
                            first.hover(position={"x": 20, "y": 20})
                            expect(first).to_have_attribute("data-halo-active", "")
                        page.emulate_media(reduced_motion="reduce")
                        first.hover(position={"x": 30, "y": 35})
                        expect(first.locator(".game-halo")).to_have_css("opacity", "0")
                        expect(page.locator("[data-halo-active]")).to_have_count(0)
                        self.assertTrue(
                            page.evaluate(
                                "matchMedia('(prefers-reduced-motion: reduce)').matches"
                            )
                        )
                        for n in range(1, 30):
                            page.keyboard.press("Tab")
                            if page.evaluate(
                                "document.activeElement.matches('.game-tile:focus-visible')"
                            ):
                                break
                        else:
                            self.fail(
                                "A game tile was not reached by tabbing from the start of the page"
                            )
                        href = page.evaluate("document.activeElement.href")
                        with context.expect_page() as created:
                            page.keyboard.press("Enter")
                        host = created.value
                        host.wait_for_url(href)
                        host.wait_for_load_state("networkidle")
                        expect(host.locator("[data-stage-panel]")).to_be_attached()
                        self.assertTrue(host.evaluate("window.opener === null"))
                        evidence[str(width)] = {
                            **measure,
                            "hardwareTabSteps": n,
                            "enterOpenedGameHost": True,
                            "openerIsolated": True,
                            "reducedMotionHaloOpacity": 0,
                            "touchEmulation": mobile,
                        }
                        context.close()
                finally:
                    browser.close()
            target = os.environ.get("MINIGAMES_HALL_METRICS")
            if target:
                path = Path(target).resolve()
                self.assertTrue(
                    path.is_relative_to(
                        (fixture.ROOT / ".codex-temp/evidence").resolve()
                    )
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
