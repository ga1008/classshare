import asyncio
import hashlib
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_user
from classroom_app.routers.materials_parts import common, exports
from classroom_app.services.deployment_cache_service import apply_deployment_cache_headers


class MaterialRenderCacheTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.user = {"id": 17, "role": "student"}
        self.root = {"id": 1, "node_type": "folder", "name": "course"}
        self.target = {"id": 2, "node_type": "file", "name": "course.css", "mime_type": "text/css"}
        self.access_calls = []
        self.revoked_id = None
        self.publish(b"body { color: red; }")

        def check_access(conn, material_id, user):
            self.access_calls.append(material_id)
            if material_id == self.revoked_id:
                raise HTTPException(403, "Material access revoked")
            return self.root if material_id == 1 else self.target

        def storage_path(file_hash):
            path = self.directory / file_hash
            return path if path.is_file() else None

        self.stack.enter_context(patch.object(exports, "get_db_connection", side_effect=lambda: nullcontext(object())))
        self.stack.enter_context(patch.object(exports, "ensure_user_material_access", side_effect=check_access))
        self.stack.enter_context(patch.object(exports, "resolve_render_target", return_value={"entry_id": 2}))
        self.stack.enter_context(patch.object(exports, "resolve_render_file", side_effect=lambda *args: self.target))
        self.storage_lookup = self.stack.enter_context(patch.object(common, "resolve_global_file_path", side_effect=storage_path))

        app = FastAPI()
        app.include_router(exports.router)
        app.dependency_overrides[get_current_user] = lambda: self.user

        @app.middleware("http")
        async def deployment_cache(request, call_next):
            response = await call_next(request)
            apply_deployment_cache_headers(request, response, release_id="material-cache-test")
            return response

        self.client = TestClient(app)
        self.stack.callback(self.client.close)

    def publish(self, content):
        self.target["file_hash"] = hashlib.sha256(content).hexdigest()
        path = self.directory / self.target["file_hash"]
        path.write_bytes(content)
        # Equal mtime and size must not hide a different stored content hash.
        os.utime(path, (1700000000, 1700000000))
        return path

    def test_asset_revalidates_without_retransmitting_unchanged_bytes(self):
        for mime_type, filename in (("text/css", "course.css"), ("application/javascript", "course.js")):
            with self.subTest(mime_type=mime_type):
                self.target.update(name=filename, mime_type=mime_type)
                first = self.client.get(f"/materials/render/1/{filename}")
                self.assertEqual(200, first.status_code)
                self.assertEqual(b"body { color: red; }", first.content)
                self.assertEqual(f'"{self.target["file_hash"]}"', first.headers["etag"])
                self.assertEqual("private, no-cache, max-age=0, must-revalidate", first.headers["cache-control"])
                self.assertEqual("nosniff", first.headers["x-content-type-options"])

                self.access_calls.clear()
                second = self.client.get(f"/materials/render/1/{filename}", headers={"If-None-Match": first.headers["etag"]})
                self.assertEqual(304, second.status_code)
                self.assertEqual(b"", second.content)
                self.assertEqual(first.headers["etag"], second.headers["etag"])
                self.assertEqual(first.headers["cache-control"], second.headers["cache-control"])
                self.assertEqual([1, 2], self.access_calls)

    def test_same_url_returns_updated_asset_despite_equal_size_and_mtime(self):
        url = "/materials/render/1/course.css"
        old = self.client.get(url)
        self.publish(b"body { color: tan; }")

        updated = self.client.get(url, headers={"If-None-Match": old.headers["etag"]})

        self.assertEqual(200, updated.status_code)
        self.assertEqual(b"body { color: tan; }", updated.content)
        self.assertNotEqual(old.headers["etag"], updated.headers["etag"])
        self.assertEqual(old.headers["content-length"], updated.headers["content-length"])
        self.assertEqual(old.headers["last-modified"], updated.headers["last-modified"])
        unchanged = self.client.get(url, headers={"If-None-Match": updated.headers["etag"]})
        self.assertEqual(304, unchanged.status_code)

    def test_if_none_match_supports_weak_list_and_wildcard_validators(self):
        etag = f'"{self.target["file_hash"]}"'
        for validator in (f"W/{etag}", f'"stale", W/{etag}', "*"):
            with self.subTest(validator=validator):
                response = self.client.get("/materials/render/1/course.css", headers={"If-None-Match": validator})
                self.assertEqual(304, response.status_code)

    def test_revoked_package_or_asset_cannot_bypass_authorization_with_etag(self):
        for material_id, calls in ((1, [1]), (2, [1, 2])):
            with self.subTest(material_id=material_id):
                self.revoked_id = material_id
                self.access_calls.clear()
                self.storage_lookup.reset_mock()

                response = self.client.get("/materials/render/1/course.css", headers={"If-None-Match": "*"})

                self.assertEqual(403, response.status_code)
                self.assertNotIn("etag", response.headers)
                self.assertEqual(calls, self.access_calls)
                self.storage_lookup.assert_not_called()

    def test_missing_storage_file_is_not_masked_by_matching_etag(self):
        (self.directory / self.target["file_hash"]).unlink()

        response = self.client.get("/materials/render/1/course.css", headers={"If-None-Match": "*"})

        self.assertEqual(404, response.status_code)
        self.assertNotIn("etag", response.headers)

    def test_html_entry_keeps_no_store_and_deployment_cache_invalidation(self):
        self.target.update(name="main.html", mime_type="text/html")
        self.publish(b"<html>updated course</html>")
        for url in ("/materials/render/1", "/materials/render/1/main.html"):
            with self.subTest(url=url):
                self.client.cookies.clear()
                response = self.client.get(url, headers={"If-None-Match": f'"{self.target["file_hash"]}"'})
                self.assertEqual(200, response.status_code)
                self.assertEqual(b"<html>updated course</html>", response.content)
                self.assertEqual("private, no-store, max-age=0, must-revalidate", response.headers["cache-control"])
                self.assertEqual('"cache"', response.headers["clear-site-data"])
                self.assertEqual("no-cache", response.headers["pragma"])

    def test_direct_helper_call_without_request_remains_compatible(self):
        response = asyncio.run(exports._serve_rendered_material(1, "course.css", self.user))
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-cache, max-age=0, must-revalidate", response.headers["cache-control"])
        self.assertEqual(f'"{self.target["file_hash"]}"', response.headers["etag"])


if __name__ == "__main__":
    unittest.main()
