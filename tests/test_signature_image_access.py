"""Viewing signatures never implicitly grants raw download permission."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from classroom_app.routers import signatures


class SignatureImageAccessTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.row = {"id": 1, "name": "测试签名", "file_hash": "f" * 64,
                    "file_ext": ".png", "mime_type": "image/png"}
        self.actor = {"role": "teacher", "id": 9, "is_super_admin": True}
        self.request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
        self.patches = [
            patch.object(signatures, "get_db_connection", return_value=nullcontext(self.conn)),
            patch.object(signatures.signature_service, "get_signature_row_for_actor", return_value=(self.row, self.actor)),
            patch.object(signatures.signature_service, "resolve_signature_file_path", return_value=Path("synthetic.png")),
            patch.object(signatures.signature_service, "can_use_signature", return_value=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def image(self, *, download=0):
        return asyncio.run(signatures.api_signature_image(1, self.request, download, self.actor))

    def test_admin_raw_management_preview_remains_available_without_cache(self):
        response = self.image()
        self.assertEqual(Path("synthetic.png"), response.path)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])

    def test_admin_download_requires_direct_use_permission(self):
        with self.assertRaises(HTTPException) as raised:
            self.image(download=1)
        self.assertEqual(403, raised.exception.status_code)
        self.conn.commit.assert_not_called()

    def test_visible_non_owner_only_receives_watermarked_preview(self):
        self.actor["is_super_admin"] = False
        with patch.object(signatures.signature_image_service, "ensure_preview", return_value=Path("preview.png")) as preview:
            response = self.image()
        preview.assert_called_once()
        self.assertEqual(Path("preview.png"), response.path)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])

    def test_owner_download_is_audited_and_not_cached(self):
        with patch.object(signatures.signature_service, "can_use_signature", return_value=True), \
             patch.object(signatures.signature_service, "record_signature_usage") as record:
            response = self.image(download=1)
        self.assertEqual("download", record.call_args.kwargs["action"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.conn.commit.assert_called_once()

    def test_upload_passes_explicit_organization_with_visibility(self):
        uploaded = MagicMock()
        with patch.object(signatures.signature_service, "create_signature_from_upload", new_callable=AsyncMock) as create:
            create.return_value = {"id": 1}
            asyncio.run(signatures.api_upload_signature(
                file=uploaded, name="测试", subject_role="teacher", subject_name="测试",
                subject_id=None, scope_level="college", school_code="school-a", college="college-a", department="department-a",
                identity_category="teacher", signature_kind="personal", description="", user=self.actor,
            ))
        self.assertEqual("college", create.call_args.kwargs["scope_level"])
        self.assertEqual("school-a", create.call_args.kwargs["school_code"])
        self.assertEqual("college-a", create.call_args.kwargs["college"])
        self.assertEqual("department-a", create.call_args.kwargs["department"])


if __name__ == "__main__":
    unittest.main()
