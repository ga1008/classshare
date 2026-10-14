import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

from classroom_app.routers import files as files_router
from classroom_app.services.submission_assets import parse_submission_manifest
from classroom_app.services.submission_file_safety import (
    submission_inline_response_params,
    trusted_submission_mime_type,
)


class _NullConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _upload(name: str, content_type: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(b"<svg onload=alert(1)></svg>"), filename=name, headers={"content-type": content_type})


class TrustedMimeTypeTests(unittest.TestCase):
    def test_mime_type_comes_from_extension_not_client(self):
        self.assertEqual(trusted_submission_mime_type("photo.png"), "image/png")
        self.assertEqual(trusted_submission_mime_type("PHOTO.JPG"), "image/jpeg")
        self.assertEqual(trusted_submission_mime_type("noext"), "application/octet-stream")

    def test_manifest_content_type_is_ignored(self):
        manifest = json.dumps([{"relative_path": "a.png", "content_type": "text/html"}])
        entries = parse_submission_manifest([_upload("a.png", "text/html")], manifest)
        self.assertEqual(entries[0].content_type, "image/png")

    def test_upload_header_content_type_is_ignored(self):
        entries = parse_submission_manifest([_upload("evil.html", "image/png")])
        self.assertNotEqual(entries[0].content_type, "image/png")


class InlineResponseParamsTests(unittest.TestCase):
    def test_raster_image_is_inline_with_sandbox_headers(self):
        media_type, headers = submission_inline_response_params(
            {"relative_path": "a.png", "original_filename": "a.png", "mime_type": "text/html"}
        )
        self.assertEqual(media_type, "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertNotIn("attachment", headers.get("Content-Disposition", ""))

    def test_svg_is_forced_to_attachment_and_sandboxed(self):
        media_type, headers = submission_inline_response_params(
            {"relative_path": "x.svg", "original_filename": "x.svg", "mime_type": "text/html"}
        )
        self.assertEqual(media_type, "image/svg+xml")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
        self.assertIn("sandbox", headers["Content-Security-Policy"])


class RawRouteTests(unittest.TestCase):
    def _call(self, route, file_info, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / file_info["original_filename"]
            path.write_bytes(b"<svg onload=alert(1)></svg>")

            async def _missing_variant(*_args, **_kwargs):
                return None

            with patch.object(files_router, "get_db_connection", return_value=_NullConnection()), \
                 patch.object(files_router, "ensure_submission_file_access", return_value=file_info), \
                 patch.object(files_router, "_resolve_file_path", return_value=str(path)), \
                 patch.object(files_router, "resolve_submission_image_variant", _missing_variant):
                return asyncio.run(route(1, user={"role": "teacher", "id": 1}, **kwargs))

    def test_raw_route_ignores_stored_mime_type(self):
        info = {"id": 1, "stored_path": "x", "relative_path": "x.svg", "original_filename": "x.svg",
                "mime_type": "text/html", "file_hash": ""}
        response = self._call(files_router.get_submission_file_raw, info)
        self.assertEqual(response.media_type, "image/svg+xml")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", response.headers["content-security-policy"])
        self.assertTrue(response.headers["content-disposition"].startswith("attachment"))

    def test_image_route_original_fallback_is_sandboxed(self):
        info = {"id": 1, "stored_path": "x", "relative_path": "x.png", "original_filename": "x.png",
                "mime_type": "text/html", "file_hash": ""}
        response = self._call(files_router.get_submission_file_image, info, variant="thumb")
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_download_route_sets_nosniff(self):
        info = {"id": 1, "stored_path": "x", "relative_path": "a.html", "original_filename": "a.html",
                "mime_type": "text/html", "file_hash": ""}
        response = self._call(files_router.download_submission_file, info)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertTrue(response.headers["content-disposition"].startswith("attachment"))


if __name__ == "__main__":
    unittest.main()
