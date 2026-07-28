import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from classroom_app.app import app
from classroom_app.services.deployment_cache_service import (
    DEPLOYMENT_CACHE_CLEAR_HEADER,
    DEPLOYMENT_RELEASE_COOKIE,
    DEPLOYMENT_RELEASE_HEADER,
    apply_deployment_cache_headers,
    normalize_deployment_release_id,
    static_asset_cache_control,
)


def _request(*, cookie: str = "", method: str = "GET", scheme: str = "https") -> Request:
    headers = [(b"host", b"guardianangel.net.cn")]
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if scheme == "https":
        headers.append((b"x-forwarded-proto", b"https"))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": scheme,
            "path": "/classroom/10",
            "raw_path": b"/classroom/10",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("guardianangel.net.cn", 443 if scheme == "https" else 80),
        }
    )


class DeploymentBrowserCacheTests(unittest.TestCase):
    def test_first_html_request_clears_only_http_cache_and_marks_release(self):
        request = _request()
        response = HTMLResponse("<html></html>")

        changed = apply_deployment_cache_headers(
            request,
            response,
            release_id="20260728-abcd1234",
        )

        self.assertTrue(changed)
        self.assertEqual("20260728-abcd1234", response.headers[DEPLOYMENT_RELEASE_HEADER])
        self.assertEqual(DEPLOYMENT_CACHE_CLEAR_HEADER, response.headers["Clear-Site-Data"])
        self.assertEqual('"cache"', response.headers["Clear-Site-Data"])
        self.assertNotIn("storage", response.headers["Clear-Site-Data"])
        self.assertNotIn("cookies", response.headers["Clear-Site-Data"])
        self.assertEqual(
            "private, no-store, max-age=0, must-revalidate",
            response.headers["Cache-Control"],
        )
        set_cookie = response.headers["set-cookie"]
        self.assertIn(f"{DEPLOYMENT_RELEASE_COOKIE}=20260728-abcd1234", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)

    def test_acknowledged_release_does_not_clear_cache_again(self):
        release_id = "20260728-abcd1234"
        request = _request(cookie=f"{DEPLOYMENT_RELEASE_COOKIE}={release_id}")
        response = HTMLResponse("<html></html>")

        changed = apply_deployment_cache_headers(request, response, release_id=release_id)

        self.assertFalse(changed)
        self.assertNotIn("Clear-Site-Data", response.headers)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(
            "private, no-store, max-age=0, must-revalidate",
            response.headers["Cache-Control"],
        )

    def test_api_response_exposes_release_without_clearing_browser_state(self):
        request = _request()
        response = JSONResponse({"status": "ok"})

        changed = apply_deployment_cache_headers(request, response, release_id="release-2")

        self.assertFalse(changed)
        self.assertEqual("release-2", response.headers[DEPLOYMENT_RELEASE_HEADER])
        self.assertNotIn("Clear-Site-Data", response.headers)
        self.assertNotIn("set-cookie", response.headers)

    def test_legacy_static_urls_revalidate_even_when_their_manual_version_is_stale(self):
        self.assertEqual(
            "public, no-cache, max-age=0, must-revalidate",
            static_asset_cache_control("js/classroom_materials.js"),
        )
        self.assertEqual(
            "public, max-age=31536000, immutable",
            static_asset_cache_control("dist/assets/app-shell-AbCdEf123.js"),
        )

    def test_release_id_is_safe_for_cookie_and_header_use(self):
        self.assertEqual("release-with-spaces", normalize_deployment_release_id(" release with spaces "))
        self.assertEqual("dev", normalize_deployment_release_id(""))
        self.assertLessEqual(len(normalize_deployment_release_id("x" * 200)), 96)

    def test_real_app_acknowledges_release_once_and_revalidates_legacy_static_asset(self):
        with patch.dict("os.environ", {"LANSHARE_RELEASE_ID": "integration-release-1"}):
            client = TestClient(app, base_url="https://guardianangel.net.cn")
            try:
                first_page = client.get("/student/login")
                second_page = client.get("/student/login")
                legacy_asset = client.get("/static/js/auth.js?v=old-manual-label")
                health = client.get("/api/internal/health")
            finally:
                client.close()

        self.assertEqual(200, first_page.status_code)
        self.assertEqual('"cache"', first_page.headers["Clear-Site-Data"])
        self.assertNotIn("Clear-Site-Data", second_page.headers)
        self.assertEqual(
            "public, no-cache, max-age=0, must-revalidate",
            legacy_asset.headers["Cache-Control"],
        )
        self.assertEqual("integration-release-1", health.headers[DEPLOYMENT_RELEASE_HEADER])
        self.assertEqual("integration-release-1", health.json()["release_id"])

    def test_deploy_contract_rotates_release_without_touching_answer_storage(self):
        exam_page = Path("templates/exam_take.html").read_text(encoding="utf-8")
        deploy_script_path = Path("deployment/deploy_remote.ps1")

        if deploy_script_path.is_file():
            deploy_script = deploy_script_path.read_text(encoding="utf-8")
            self.assertIn("Get-FileHash -LiteralPath $archivePath -Algorithm SHA256", deploy_script)
            self.assertIn("upsert_env_value LANSHARE_RELEASE_ID", deploy_script)
            self.assertIn("CACHE_RELEASE_VERIFIED", deploy_script)
            self.assertIn("PUBLIC_CACHE_RELEASE_VERIFIED", deploy_script)
            self.assertIn('$cacheReleaseId', deploy_script)
        self.assertIn("localStorage.setItem(LOCAL_DRAFT_KEY", exam_page)
        self.assertIn("/draft`, { method: 'GET'", exam_page)
        self.assertIn("/draft`, {", exam_page)
        self.assertIn("method: 'POST'", exam_page)


if __name__ == "__main__":
    unittest.main()
