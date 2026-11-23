import asyncio
import os
import secrets
import uuid
from http.client import HTTPConnection
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from jose import jwt

from classroom_app import dependencies
from classroom_app.minigame_api import app as api
from classroom_app.minigame_content import app as content
from classroom_app.services import minigame_platform as platform
from deployment.docker import minigame_health
from tests.test_minigame_platform import API, GamePlatformFixture, server


class DedicatedGameApiTests(GamePlatformFixture):
    def setUp(self):
        self.enterContext(patch.object(dependencies, "SECRET_KEY", secrets.token_urlsafe(48)))
        super().setUp()
        self.enterContext(patch.dict(os.environ, {"MINIGAMES_EXTERNAL_API": "1"}))

    def test_container_probes_require_a_ready_api_and_a_working_content_database(self):
        for app, kind in ((api, "api"), (content, "content")):
            with self.subTest(kind=kind), server(app, "127.0.0.1", lifespan="on") as url:
                port = urlsplit(url).port
                with patch.object(minigame_health.http.client, "HTTPConnection",
                                  side_effect=lambda host, _port, timeout: HTTPConnection(host, port, timeout=timeout)):
                    minigame_health.check(kind, False)
                    if kind == "api":
                        with patch.object(api.state, "minigames_ready", False), self.assertRaises(RuntimeError):
                            minigame_health.check(kind, False)
                    else:
                        from fastapi import HTTPException
                        with patch("classroom_app.minigame_content.resolve_asset", side_effect=HTTPException(503)):
                            with self.assertRaises(RuntimeError):
                                minigame_health.check(kind, False)

    def test_dedicated_api_over_http_uses_real_classroom_sessions_and_only_game_routes(self):
        main_api = {
            (r.path, method)
            for r in self.client.app.routes
            if r.path.startswith(API + "/")
            for method in r.methods
        }
        self.assertSetEqual({(r.path, method) for r in api.routes for method in r.methods}, main_api)
        self.client.cookies.set("access_token", self.tokens[931])
        self.assertEqual(self.client.get("/games").status_code, 200)
        self.client.cookies.set("access_token", self.tokens[901])
        self.assertEqual(self.client.get("/manage/library/games").status_code, 200)
        with (
            server(api, "127.0.0.1", lifespan="on") as url,
            httpx.Client(base_url=url, trust_env=False) as browser,
        ):
            self.assertTrue(api.state.minigames_owns_workers)
            for path in (
                "/games",
                "/games/any",
                "/manage/library/games",
                "/login",
                "/api/profile/ui-preferences",
                "/openapi.json",
                "/docs",
                "/l/anything/main.html",
            ):
                with self.subTest(path=path):
                    response = browser.get(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                    self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
            denied = browser.get(API + "/catalog", headers={"Authorization": "Bearer " + self.tokens[931]})
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(denied.headers["Cache-Control"], "private, no-store")
            mp_claims = {**dependencies.decode_token_payload(self.tokens[931]), "auth_channel": "mp"}
            mp_cookie = jwt.encode(mp_claims, dependencies.SECRET_KEY, algorithm=dependencies.ALGORITHM)
            browser.cookies.set("access_token", mp_cookie)
            self.assertEqual(browser.get(API + "/catalog").status_code, 401)
            browser.cookies.set("access_token", self.tokens[931])
            self.assertEqual(browser.get(API + "/catalog").json()["items"], [])
            self.assertEqual(browser.get(API + "/me/activity").status_code, 200)
            self.assertEqual(browser.get(API + "/admin/games").status_code, 403)
            self.assertEqual(browser.post(API + "/me/heartbeat", json={}).status_code, 403)
            browser.cookies.set("access_token", self.tokens[901])
            claims = dependencies.decode_token_payload(self.tokens[901])
            csrf = platform.csrf_token({"actor": "teacher:901", "session_id": claims["session_id"]})
            headers = {
                "Origin": "http://127.0.0.1",
                "X-LanGame-CSRF": csrf,
                "Idempotency-Key": uuid.uuid4().hex,
            }
            endpoint = API + "/admin/games"
            self.assertEqual(browser.get(endpoint).status_code, 200)
            self.assertEqual(browser.post(endpoint, json={"title": "教师小游戏"}).status_code, 403)
            cross_origin = {**headers, "Origin": "http://localhost"}
            self.assertEqual(browser.post(endpoint, headers=cross_origin, json={}).status_code, 403)
            created = browser.post(endpoint, headers=headers, json={"title": "教师小游戏"})
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["game"]["title"], "教师小游戏")
            with platform.game_db() as conn:
                conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:901'")
            self.assertEqual(browser.get(endpoint).status_code, 401)
        self.assertFalse(api.state.minigames_ready)
        self.assertFalse(api.state.minigames_owns_workers)

    def test_external_main_reads_schema_without_starting_or_stopping_workers(self):
        from classroom_app.services import minigame_packages, minigame_runtime

        host = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.object(minigame_runtime, "start_runtime") as start_runtime,
            patch.object(minigame_packages, "start_package_worker") as start_packages,
            patch.object(minigame_runtime, "stop_runtime") as stop_runtime,
            patch.object(minigame_packages, "stop_package_worker") as stop_packages,
        ):
            asyncio.run(platform.start_minigames(host))
            self.assertTrue(host.state.minigames_ready)
            self.assertFalse(host.state.minigames_owns_workers)
            asyncio.run(platform.stop_minigames(host))
            for operation in (start_runtime, start_packages, stop_runtime, stop_packages):
                operation.assert_not_called()
            with patch.dict(os.environ, {"MINIGAMES_EXTERNAL_API": "0"}):
                asyncio.run(platform.start_minigames(host))
                self.assertTrue(host.state.minigames_ready)
                self.assertTrue(host.state.minigames_owns_workers)
                asyncio.run(platform.stop_minigames(host))
            for operation in (start_runtime, start_packages, stop_runtime, stop_packages):
                operation.assert_called_once()

    def test_dedicated_startup_rejects_weak_key_invalid_origins_and_incomplete_schema(self):
        for key in (
            "",
            "short",
            "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME",
            " DEFAULT_WEAK_SECRET_KEY_REPLACE_ME ",
            " " * 40,
        ):
            with self.subTest(key_length=len(key)), patch.object(dependencies, "SECRET_KEY", key):
                with self.assertRaisesRegex(RuntimeError, "签名密钥"), TestClient(api):
                    pass
        with patch.dict(os.environ, {"MINIGAMES_CONTENT_ORIGIN": "http://127.0.0.1"}):
            with self.assertRaisesRegex(RuntimeError, "独立内容站"), TestClient(api):
                pass
        with platform.game_db() as conn:
            conn.execute("DROP TABLE mg_admin_plans")
        with self.assertRaisesRegex(RuntimeError, "数据库迁移"), TestClient(api):
            pass
        self.assertFalse(api.state.minigames_ready)
