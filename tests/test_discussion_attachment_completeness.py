"""Validate the requested set before saving a discussion message or calling AI."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException
from PIL import Image

from classroom_app.services import discussion_attachment_service as service


class DiscussionAttachmentCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE discussion_attachments (
            id INTEGER PRIMARY KEY, class_offering_id INTEGER, uploaded_by_user_id TEXT,
            uploaded_by_role TEXT, original_filename TEXT DEFAULT 'picture.png',
            mime_type TEXT DEFAULT 'image/png', file_size INTEGER DEFAULT 100,
            image_width INTEGER DEFAULT 10, image_height INTEGER DEFAULT 10,
            file_hash TEXT DEFAULT 'fixture', created_at TEXT DEFAULT '2026-09-07')""")
        self.conn.executemany("INSERT INTO discussion_attachments (id,class_offering_id,uploaded_by_user_id,uploaded_by_role) VALUES (?,?,?,?)", [
            (1, 7, "9", "student"), (2, 7, "10", "student"),
            (3, 8, "9", "student"), (4, 7, "9", "teacher"),
        ])
        self.user = {"id": 9, "role": "student"}
        self.schema_patch = mock.patch.object(service, "ensure_discussion_attachment_schema")
        self.schema_patch.start()
        self.addCleanup(self.schema_patch.stop)
        self.addCleanup(self.conn.close)

    def test_missing_other_owner_class_or_role_rejects_entire_requested_set(self):
        for invalid in (99, 2, 3, 4):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException) as caught:
                service.resolve_discussion_attachment_payloads(self.conn, 7, [1, invalid], self.user)
            self.assertEqual(400, caught.exception.status_code)

    def test_invalid_identifiers_do_not_disappear_before_validation(self):
        for invalid in (None, True, 0, -1, "bad", "", {}, 1.5, "1_0", 2**100):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException):
                service.resolve_discussion_attachment_payloads(self.conn, 7, [1, invalid], self.user)

    def test_own_image_string_ids_dedup_and_empty_requests_still_work(self):
        payloads = service.resolve_discussion_attachment_payloads(self.conn, 7, ["1", 1], self.user)
        self.assertEqual([1], [item["attachment_id"] for item in payloads])
        self.assertIn("/7/discussion-attachments/1/preview", payloads[0]["preview_url"])
        self.assertEqual([], service.resolve_discussion_attachment_payloads(self.conn, 7, [], self.user))

    def test_strict_payload_identifiers_reject_missing_and_malformed_metadata(self):
        for payloads in ([{}], [{"attachment_id": None}], ["image"], [{"attachment_id": 99}]):
            with self.subTest(payloads=payloads), self.assertRaises(HTTPException):
                service.build_attachment_image_inputs_from_payloads(self.conn, 7, payloads, strict=True)

    def test_strict_stats_oversized_preview_before_any_read(self):
        path = mock.Mock(spec=Path)
        path.stat.return_value.st_size = service.DISCUSSION_ATTACHMENT_MAX_BYTES + 1
        preview = {"path": path, "mime_type": "image/png"}
        with mock.patch.object(service, "resolve_discussion_attachment_file_payload", return_value=preview), self.assertRaises(HTTPException):
            service.build_attachment_image_inputs_from_payloads(self.conn, 7, [{"attachment_id": 1}], strict=True)
        path.open.assert_not_called()
        path.read_bytes.assert_not_called()

    def test_strict_stats_original_before_generating_missing_preview(self):
        path = mock.Mock(spec=Path)
        path.stat.return_value.st_size = service.DISCUSSION_ATTACHMENT_MAX_BYTES + 1
        with mock.patch.object(service, "resolve_discussion_attachment_file_payload", return_value=None), \
             mock.patch.object(service, "_resolve_original_file_payload", return_value={"path": path}), \
             mock.patch.object(service, "_ensure_discussion_attachment_derivative_sync") as derive, \
             self.assertRaises(HTTPException):
            service.build_attachment_image_inputs_from_payloads(self.conn, 7, [{"attachment_id": 1}], strict=True)
        derive.assert_not_called()
        path.open.assert_not_called()

    def test_strict_good_image_and_legacy_id_payload_reach_input(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-discussion-image-") as directory:
            path = Path(directory) / "image.png"
            Image.new("RGB", (10, 10), "white").save(path, "PNG")
            with mock.patch.object(service, "resolve_discussion_attachment_file_payload", return_value={"path": path, "mime_type": "image/png"}):
                inputs = service.build_attachment_image_inputs_from_payloads(self.conn, 7, [{"id": 1}], strict=True)
            self.assertEqual(1, len(inputs))
            self.assertTrue(inputs[0]["url"].startswith("data:image/png;base64,"))

    def test_strict_limits_read_if_file_grows_after_stat(self):
        path = mock.MagicMock(spec=Path)
        path.stat.return_value.st_size = 100
        source = path.open.return_value.__enter__.return_value
        source.read.return_value = b"x" * 101
        with mock.patch.object(service, "DISCUSSION_ATTACHMENT_MAX_BYTES", 100), \
             mock.patch.object(service, "resolve_discussion_attachment_file_payload", return_value={"path": path, "mime_type": "image/png"}), \
             self.assertRaises(HTTPException):
            service.build_attachment_image_inputs_from_payloads(self.conn, 7, [{"attachment_id": 1}], strict=True)
        source.read.assert_called_once_with(101)


if __name__ == "__main__":
    unittest.main()
