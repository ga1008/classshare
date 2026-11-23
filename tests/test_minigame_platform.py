"""Integration checks using real platform sessions and isolated application/content databases and files."""

import json
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from classroom_app import config, database, dependencies
from classroom_app.db import schema_social
from classroom_app.app import app
from classroom_app.db.schema import init_database
from classroom_app.minigame_content import app as content_app
from classroom_app.services import minigame_packages as pk
from classroom_app.services import minigame_platform as p
from classroom_app.services.minigame_package_validation import validate_package
from tools.minigame_pack import build as build_game

ROOT = Path(__file__).resolve().parents[1]
API = "/api/games/v1"


@contextmanager
def server(application, hostname, *, lifespan="off"):
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        service = uvicorn.Server(
            uvicorn.Config(
                application, log_level="error", access_log=False, lifespan=lifespan
            )
        )
        thread = threading.Thread(
            target=service.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            until = time.monotonic() + 10
            while not service.started:
                if not thread.is_alive() or time.monotonic() > until:
                    raise RuntimeError("local game server failed")
                time.sleep(0.025)
            yield f"http://{hostname}:{port}"
        finally:
            service.should_exit = True
            thread.join(12)
            if thread.is_alive():
                raise RuntimeError("local game server failed to stop")


class GamePlatformFixture(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(config, "DB_ENGINE", "sqlite"))
        self.enterContext(patch.object(config, "DB_PATH", self.root / "classroom.db"))
        self.enterContext(patch.object(database, "DB_PATH", self.root / "classroom.db"))
        self.enterContext(patch.object(schema_social, "_SCHEMA_READY", False))
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "LANSHARE_DATA_ROOT": str(self.root),
                    "MINIGAMES_APP_ORIGIN": "http://127.0.0.1",
                    "MINIGAMES_CONTENT_ORIGIN": "http://localhost",
                },
            )
        )
        init_database()
        self.tokens = {}
        with p.game_db() as conn:
            conn.execute(
                "INSERT INTO teachers(id,name,email,hashed_password) VALUES(901,'教师甲','901@example.invalid','fixture')"
            )
            conn.execute(
                "INSERT INTO classes(id,name,created_by_teacher_id) VALUES(921,'合成班',901)"
            )
            for uid in (931, 932):
                conn.execute(
                    "INSERT INTO students(id,name,student_id_number,class_id) VALUES(?,?,?,921)",
                    (uid, "玩家" + str(uid), str(uid)),
                )
            for role, uid in (("teacher", 901), ("student", 931), ("student", 932)):
                token = dependencies.create_access_token(
                    {"id": str(uid), "role": role, "name": "合成用户"},
                    "127.0.0.1",
                    conn=conn,
                )
                self.tokens[uid] = token
            conn.commit()
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)
        self.content = TestClient(content_app, base_url="http://localhost")
        self.addCleanup(self.content.close)


class GamePlatformTests(GamePlatformFixture):
    def test_teacher_can_read_and_download_the_same_author_spec(self):
        from io import BytesIO
        from zipfile import ZipFile

        base = "/manage/library/games/author-guide"
        for uid in (None, 931):
            self.client.cookies.clear()
            if uid:
                self.client.cookies.set("access_token", self.tokens[uid])
            for suffix in ("", "/markdown", "/download"):
                denied = self.client.get(base + suffix, follow_redirects=False)
                self.assertIn(denied.status_code, (303, 401, 403))
                self.assertNotIn("attachment", denied.headers.get("Content-Disposition", ""))

        self.client.cookies.clear()
        self.client.cookies.set("access_token", self.tokens[901])
        page = self.client.get(base, follow_redirects=False)
        markdown = self.client.get(base + "/markdown", follow_redirects=False)
        archive = self.client.get(base + "/download", follow_redirects=False)
        for res in (page, markdown, archive):
            self.assertEqual(res.status_code, 200)
            self.assertIn("private", res.headers["Cache-Control"])
        self.assertIn('href="#section-1"', page.text)
        self.assertIn('id="section-1"', page.text)
        self.assertIn(base + "/download", page.text)
        self.assertIn(base + "/markdown", page.text)
        self.assertIn("attachment", markdown.headers["Content-Disposition"])
        self.assertIn("attachment", archive.headers["Content-Disposition"])
        self.assertTrue(markdown.headers["Content-Type"].startswith("text/markdown"))
        self.assertEqual(archive.headers["Content-Type"], "application/zip")
        with ZipFile(BytesIO(archive.content)) as package:
            self.assertEqual(package.read("AI-GAME-SPEC.md"), markdown.content)
        for line in markdown.text.splitlines():
            if line.startswith("# "):
                self.assertIn(line[2:], page.text)
                break

    def test_interrupted_preview_does_not_authorize_playtest(self):
        import asyncio
        from classroom_app import minigame_content

        source = self.root / "main.html"
        source.write_bytes(b"synthetic preview")
        response = minigame_content.GameFileResponse(
            uuid.uuid4().hex, source, preview=("fixture",)
        )

        async def disconnected(message):
            if message["type"] == "http.response.body":
                raise OSError("synthetic client disconnected")

        with (
            patch.object(minigame_content, "mark_preview_loaded") as receipt,
            self.assertRaises(OSError),
        ):
            asyncio.run(
                response(
                    {"type": "http", "method": "GET", "headers": []}, None, disconnected
                )
            )
        receipt.assert_not_called()

    def test_author_can_regenerate_portable_package(self):
        import zipfile

        source = self.root / "source"
        with zipfile.ZipFile(
            ROOT / "docs/minigames/downloads/beacon-relay.zip"
        ) as sample:
            sample.extractall(source)
        (source / "js/lan-game-sdk.v1.js").unlink()
        (source / "js/offline-contracts.js").unlink()
        output = self.root / "author.zip"
        result = build_game(source, output)
        self.assertEqual(result["config"]["gameKey"], "beacon-relay")
        self.assertIn("static/返航 提示.wav", result["files"])
        self.assertFalse((source / "js/offline-contracts.js").exists())
        with self.assertRaises(FileExistsError):
            build_game(source, output)

    def test_nonlocal_origins_require_a_full_length_signing_secret(self):
        from fastapi import HTTPException

        with patch.dict(
            os.environ,
            {
                "MINIGAMES_APP_ORIGIN": "https://class.example.invalid",
                "MINIGAMES_CONTENT_ORIGIN": "https://games.example.invalid",
            },
        ):
            with (
                patch.object(dependencies, "SECRET_KEY", "short"),
                self.assertRaises(HTTPException) as denied,
            ):
                p.content_origins()
            self.assertEqual(denied.exception.status_code, 503)
            with patch.object(
                dependencies, "SECRET_KEY", "fixture-only".ljust(32, "a")
            ):
                self.assertEqual(
                    p.content_origins()[1], "https://games.example.invalid"
                )

    def test_existing_classroom_alias_can_manage_games_and_content_origin_is_never_a_parent(self):
        env = {
            "MINIGAMES_APP_ORIGIN": "https://class.example.invalid",
            "MINIGAMES_APP_ALIASES": "https://www.example.invalid",
            "MINIGAMES_CONTENT_ORIGIN": "https://games.example.invalid",
        }
        with patch.dict(os.environ, env), patch.object(dependencies, "SECRET_KEY", "fixture-signing-secret-for-https-alias"):
            with p.game_db() as conn:
                self.tokens[901] = dependencies.create_access_token(
                    {"id": "901", "role": "teacher", "name": "合成教师"}, "127.0.0.1", conn=conn
                )
                conn.commit()
            parents = p.platform_origins(env["MINIGAMES_APP_ORIGIN"])
            self.assertEqual(parents, (env["MINIGAMES_APP_ORIGIN"], env["MINIGAMES_APP_ALIASES"]))
            self.api(901, "POST", "/admin/games", json={"scope": "public"}, headers={"Origin": parents[1]})
            with patch.dict(os.environ, {"MINIGAMES_APP_ALIASES": env["MINIGAMES_CONTENT_ORIGIN"]}):
                with self.assertRaises(ValueError):
                    p.platform_origins(parents[0])

    def api(self, uid, method, path, **args):
        self.client.cookies.set("access_token", self.tokens[uid])
        payload = dependencies.decode_token_payload(self.tokens[uid])
        headers = {
            "Origin": os.environ["MINIGAMES_APP_ORIGIN"],
            "X-LanGame-CSRF": p.csrf_token(
                {
                    "actor": payload["role"] + ":" + str(uid),
                    "session_id": payload["session_id"],
                }
            ),
            "Idempotency-Key": uuid.uuid4().hex,
        }
        headers.update(args.pop("headers", {}))
        response = self.client.request(method, API + path, headers=headers, **args)
        self.assertLess(response.status_code, 300, (path, response.text[:600]))
        return response.json()

    def publish(self, check_browser=False):
        z = ROOT / "docs/minigames/downloads/beacon-relay.zip"
        g = self.api(901, "POST", "/admin/games", json={"scope": "public"})["game"]
        path = "/admin/games/" + g["id"]
        with z.open("rb") as f:
            self.api(
                901,
                "POST",
                path + "/versions",
                data={"expectedRevision": str(g["revision"])},
                files={"file": (z.name, f, "application/zip")},
            )

        def run_validation():
            pk.worker_once()
            job = self.api(901, "GET", path)["jobs"][0]
            if job["status"] == "retry" and job["error"].get("code") == "ASSET_BUSY":
                # Advance this fixture past the persisted retry deadline after a Windows sharing conflict.
                with patch.object(p, "now_ms", return_value=p.now_ms() + 31_000):
                    pk.worker_once()

        if check_browser:
            safe = self.root / "checked"
            safe.mkdir()
            meta = validate_package(z, safe)
            argv = [
                sys.executable,
                str(ROOT / "tools/minigame_browser_probe.py"),
                "--browser",
                self.browser_bin(),
                "--synthetic-digest",
                meta["digest"],
            ]
            if os.name == "nt":
                argv.append("--trusted-browser-harness")
            with patch.dict(os.environ, {"MINIGAMES_PROBE_COMMAND": json.dumps(argv)}):
                run_validation()
        else:
            with patch.object(
                pk,
                "probe_package",
                side_effect=lambda _, d: {
                    "digest": d,
                    "offline": True,
                    "platform": True,
                },
            ):
                run_validation()
        d = self.api(901, "GET", path)
        v = d["versions"][0]
        self.assertEqual(v["status"], "ready", d["jobs"][0])
        preview = self.api(
            901,
            "POST",
            "/games/" + g["id"] + "/launches",
            json={
                "purpose": "preview",
                "versionId": v["id"],
                "hostInstance": "teacher-preview",
            },
        )
        self.assertEqual(self.content.get(preview["contentEntryUrl"]).status_code, 200)
        self.api(
            901,
            "POST",
            path + "/versions/" + v["id"] + "/playtests",
            json={"expectedRevision": d["game"]["revision"], "passed": True},
        )
        return self.api(
            901,
            "POST",
            path + "/publish",
            json={"expectedRevision": d["game"]["revision"], "versionId": v["id"]},
        )["game"]

    def browser_bin(self):
        bins = [
            os.environ.get("MINIGAMES_TEST_BROWSER"),
            *sorted(
                (Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright").glob(
                    "chromium-*/chrome-win64/chrome.exe"
                ),
                reverse=True,
            ),
            shutil.which("chromium"),
            shutil.which("google-chrome"),
        ]
        return str(next(b for b in bins if b and Path(b).is_file()))

    def test_catalog_leases_and_revocation(self):
        self.assertTrue(p.schema_installed())
        g = self.publish()
        gid = g["id"]
        directory = self.api(931, "GET", "/catalog?q=航标&mode=coop")
        self.assertEqual([x["id"] for x in directory["items"]], [gid])
        self.assertEqual(self.api(931, "GET", "/sidebar")["recommended"][0]["id"], gid)
        self.assertEqual(self.client.get("/games").status_code, 200)
        self.assertIn("航标接力", self.client.get("/games/beacon-relay").text)
        self.assertEqual(
            self.client.post(API + "/games/" + gid + "/launches", json={}).status_code,
            403,
        )
        grant = self.api(
            931,
            "POST",
            "/games/" + gid + "/launches",
            json={"purpose": "play", "hostInstance": "test-main"},
        )
        url = urlsplit(grant["contentEntryUrl"]).path
        original = self.content.get(url)
        self.assertEqual(original.status_code, 200)
        self.assertIn(
            "sandbox allow-scripts", original.headers["Content-Security-Policy"]
        )
        self.assertEqual(self.content.head(url).status_code, 200)
        self.assertEqual(
            self.content.get(url, headers={"range": "bytes=0-9"}).status_code, 206
        )
        audio_url = url.removesuffix("main.html") + "static/返航 提示.wav"
        audio = self.content.get(audio_url, headers={"range": "bytes=0-3"})
        self.assertEqual((audio.status_code, audio.content), (206, b"RIFF"))
        self.assertEqual(
            self.content.get(
                url.removesuffix("main.html") + "static%2F返航%20提示.wav"
            ).status_code,
            404,
        )
        self.assertEqual(self.content.get(url + "?x=1").status_code, 404)
        self.assertEqual(
            self.content.get(url, headers={"host": "127.0.0.1"}).status_code, 404
        )
        g = self.api(
            901,
            "PATCH",
            "/admin/games/" + gid,
            json={
                "expectedRevision": g["revision"],
                "scope": "class",
                "targets": [921],
            },
        )["game"]
        self.assertEqual(self.content.get(url).status_code, 200)
        self.api(
            931,
            "POST",
            "/launches/" + grant["leaseId"] + "/renew",
            json={"hostInstance": "test-main"},
        )
        self.api(
            901,
            "PATCH",
            "/admin/games/" + gid,
            json={"expectedRevision": g["revision"], "minTier": 1},
        )
        self.assertEqual(self.api(931, "GET", "/catalog")["items"], [])
        self.assertEqual(self.content.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(API + "/games/" + gid + "/icon").status_code, 404
        )

    def test_sessions_control_idempotency_and_file_drain(self):
        g = self.publish()
        endpoint = "/games/" + g["id"] + "/launches"
        params = {"purpose": "play", "hostInstance": "student-window"}
        headers = {"Idempotency-Key": uuid.uuid4().hex}
        grant = self.api(931, "POST", endpoint, json=params, headers=headers)
        repeat = self.api(931, "POST", endpoint, json=params, headers=headers)
        self.assertEqual(repeat, grant)
        path = urlsplit(grant["contentEntryUrl"]).path
        with p.asset_usage(g["id"], exclusive=True):
            self.assertEqual(self.content.get(path).status_code, 503)
        with p.asset_usage(g["id"]):
            self.assertEqual(self.content.get(path).status_code, 200)
        with p.game_db() as conn:
            dependencies.create_access_token(
                {"id": "931", "role": "student", "name": "再次登录"},
                "127.0.0.1",
                conn=conn,
            )
        self.assertEqual(self.content.get(path).status_code, 404)
        self.assertEqual(
            self.client.get(API + "/catalog", follow_redirects=False).status_code, 401
        )

    def test_replaced_or_inactive_identities_cannot_enter(self):
        g = self.publish()
        self.api(931, "GET", "/catalog")
        with p.game_db() as conn:
            conn.execute(
                "UPDATE students SET enrollment_status='inactive' WHERE id=931"
            )
        self.assertEqual(self.client.get(API + "/games/" + g["id"]).status_code, 403)
        self.client.cookies.set("access_token", self.tokens[932])
        with p.game_db() as conn:
            conn.execute("UPDATE students SET control_mode='ai' WHERE id=932")
        self.assertEqual(self.client.get(API + "/catalog").status_code, 403)
        self.client.cookies.set("access_token", self.tokens[901])
        with p.game_db() as conn:
            conn.execute("UPDATE teachers SET is_active=0 WHERE id=901")
        self.assertEqual(self.client.get(API + "/admin/games").status_code, 403)

    def test_background_game_traffic_and_classroom_access(self):
        from classroom_app.services import remember_login_service as remember

        g = self.publish()
        self.client.cookies.set(remember.REMEMBER_COOKIE, "synthetic-device")
        with (
            patch.object(remember, "remember_enabled", return_value=True),
            patch.object(dependencies, "_observe_active_student") as observer,
        ):
            run = self.api(
                931,
                "POST",
                "/games/" + g["id"] + "/runs",
                json={"hostInstance": "foreground-game", "mode": "solo"},
            )
            self.api(
                931,
                "POST",
                "/sync",
                json={
                    "runId": run["runId"],
                    "hostInstance": "foreground-game",
                    "controlEpoch": run["controlEpoch"],
                    "after": 0,
                    "waitMs": 0,
                },
            )
            self.api(931, "GET", "/sidebar")
            self.assertEqual(
                self.client.get(API + "/catalog").headers["Cache-Control"],
                "private, no-store",
            )
            observer.assert_not_called()
            self.assertEqual(
                self.client.get("/api/profile/ui-preferences").status_code, 200
            )
            self.assertTrue(observer.called)

    @skipUnless(
        os.environ.get("MINIGAMES_BROWSER_INTEGRATION") == "1",
        "opt-in real browser and network test",
    )
    def test_chromium_separate_content_host(self):
        from playwright.sync_api import sync_playwright

        with (
            server(app, "127.0.0.1") as main,
            server(content_app, "localhost") as asset,
            patch.dict(
                os.environ,
                {"MINIGAMES_APP_ORIGIN": main, "MINIGAMES_CONTENT_ORIGIN": asset},
            ),
        ):
            game = self.publish(True)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    executable_path=self.browser_bin(), chromium_sandbox=os.name != "nt"
                )
                try:
                    ctx = browser.new_context()
                    ctx.add_cookies(
                        [
                            {
                                "name": "access_token",
                                "value": self.tokens[931],
                                "url": main,
                            }
                        ]
                    )
                    page = ctx.new_page()
                    page.set_default_timeout(12000)
                    page.goto(
                        main + "/games/" + game["slug"], wait_until="domcontentloaded"
                    )
                    frame = page.frame_locator("iframe[sandbox]")
                    frame.get_by_role("button", name="东", exact=True).click()
                    frame.get_by_text("沿地图向下航行", exact=False).wait_for()
                    frame.get_by_role("button", name="切换海图配色").click()
                    self.assertEqual(
                        self.api(931, "GET", "/me/progress/" + game["id"])["rank"], 1
                    )
                    page.reload(wait_until="domcontentloaded")
                    frame.get_by_text("沿地图向下航行", exact=False).wait_for()
                    frame.get_by_role("button", name="南", exact=True).click()
                    frame.get_by_text("夕阳正在海平面落下", exact=False).wait_for()
                    frame.get_by_role("button", name="西", exact=True).click()
                    frame.get_by_text("船队已抵达航道", exact=False).wait_for()
                    frame.get_by_role("button", name="引导船队返航").click()
                    page.get_by_text("本局结束", exact=True).first.wait_for()
                    self.assertTrue(
                        self.api(931, "GET", "/me/history?gameId=" + game["id"])[
                            "items"
                        ]
                    )
                    for name in ("beacon-relay", "star-map", "three-in-line"):
                        import zipfile

                        dst = self.root / ("local-" + name)
                        with zipfile.ZipFile(
                            ROOT / "docs/minigames/downloads" / (name + ".zip")
                        ) as bundle:
                            bundle.extractall(dst)
                        page.goto((dst / "main.html").as_uri())
                        page.locator("#board button").first.wait_for()
                        self.assertEqual(
                            page.evaluate(
                                "async()=> (await LanGame.ready()).environment"
                            ),
                            "local",
                        )
                finally:
                    browser.close()
