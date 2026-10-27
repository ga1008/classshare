"""聊天媒体留存与空间守护 (social doc §6): expiry, ref-count deletion, global store untouched, disk guard."""
from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from PIL import Image

from classroom_app.services import social_media_retention_service as retention
from classroom_app.services import social_media_service as media
from classroom_app.services import social_message_service as messages
from classroom_app.services.social_common import dm_key
from tests.social_test_support import SocialDbTestCase, sha

PAST = "2025-01-01T00:00:00+00:00"


def noisy_png(side=256) -> bytes:
    buffer = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buffer, format="PNG")
    return buffer.getvalue()


class SocialRetentionTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.key = dm_key(self.a, self.b)

    def blob_file(self, digest: str):
        return self.social_root / digest[:2] / digest[2:4] / digest

    def upload(self, data: bytes, mime="application/octet-stream") -> str:
        spec = {"sha256": sha(data), "size": len(data), "mime": mime, "filename": "f"}
        result, _ = self.run_service(media.precheck, self.user(self.a), **spec)
        if result["exists"]:
            return result["media_token"]
        session, _ = self.run_service(media.upload_init, self.user(self.a), **spec)
        chunk = session["chunk_size"]
        for index in range(0, (len(data) + chunk - 1) // chunk):
            self.run_service(media.write_chunk, self.user(self.a), session["upload_id"], index,
                             data[index * chunk:(index + 1) * chunk])
        return self.run_service(media.upload_complete, self.user(self.a), session["upload_id"])[0]["media_token"]

    def send(self, token: str) -> dict:
        with patch.object(messages, "RATE_WINDOWS", ((10, 1000), (60, 1000))):
            message, _ = self.run_service(messages.send_message, self.user(self.a), self.key,
                                          {"body": "", "media_tokens": [token]})
        return message["media"][0]

    def expire(self, media_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE social_message_media SET expires_at = ? WHERE id = ?", (PAST, media_id))
            conn.commit()

    def run_retention(self, **kwargs):
        with patch.object(retention, "disk_usage", return_value=(1000, 100)):
            return self.run_service(retention.run_retention, **kwargs)[0]

    def test_expiry_marks_media_and_deletes_unreferenced_blob(self):
        data = os.urandom(2048)
        item = self.send(self.upload(data))
        self.assertGreater(self.count("SELECT COUNT(*) FROM social_message_media WHERE expires_at > '2027'"), 0)
        self.expire(item["id"])
        summary = self.run_retention()
        self.assertEqual((summary["expired_media"], summary["deleted_blobs"]), (1, 1))
        self.assertFalse(self.blob_file(sha(data)).exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_blobs WHERE storage_state = 'deleted'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_message_media WHERE expired_at IS NOT NULL"), 1)
        self.assertEqual(retention.last_summary()["expired_media"], 1)

    def test_shared_blob_survives_until_last_reference(self):
        data = os.urandom(2048)
        first = self.send(self.upload(data))
        self.send(self.upload(data))
        self.assertEqual(self.count("SELECT ref_count FROM social_media_blobs"), 2)
        self.expire(first["id"])
        summary = self.run_retention()
        self.assertEqual(summary["deleted_blobs"], 0)
        self.assertTrue(self.blob_file(sha(data)).exists())
        self.assertEqual(self.count("SELECT ref_count FROM social_media_blobs"), 1)

    def test_fresh_unredeemed_upload_is_kept(self):
        data = os.urandom(1024)
        self.upload(data)
        self.assertEqual(self.run_retention()["deleted_blobs"], 0)
        self.assertTrue(self.blob_file(sha(data)).exists())

    def test_global_store_is_never_touched(self):
        data = b"shared course file" * 50
        digest = self.put_global_file(data)
        item = self.send(self.upload(data))
        self.expire(item["id"])
        with patch.object(retention, "disk_usage", return_value=(1000, 990)):
            summary = self.run_service(retention.run_retention)[0]
        self.assertEqual(summary["expired_media"], 1)
        self.assertTrue((self.global_root / digest[:2] / digest[2:4] / digest).exists())

    def test_watermark_guard_dry_run_then_real(self):
        image = noisy_png()
        video = os.urandom(4096)
        image_item = self.send(self.upload(image, mime="image/png"))
        video_item = self.send(self.upload(video, mime="video/mp4"))
        with patch.object(retention, "COMPRESS_MIN_BYTES", 1024), \
                patch.object(retention, "disk_usage", return_value=(10_000_000, 9_500_000)):
            dry = self.run_service(retention.run_retention, dry_run=True)[0]
            self.assertTrue(dry["dry_run"])
            self.assertEqual((dry["compressed_images"], dry["early_expired"]), (1, 1))
            self.assertGreater(dry["disk_before"], retention.high_watermark())
            self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_blobs WHERE storage_state <> 'ready'"), 0)
            self.assertEqual(self.count("SELECT COUNT(*) FROM social_message_media WHERE expired_at IS NOT NULL"), 0)
            self.assertEqual(self.blob_file(sha(image)).read_bytes(), image)
            real = self.run_service(retention.run_retention)[0]
        self.assertEqual((real["compressed_images"], real["early_expired"]), (1, 1))
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_blobs WHERE storage_state = 'compressed' "
                                    "AND mime_type = 'image/webp'"), 1)
        self.assertFalse(self.blob_file(sha(video)).exists())
        client = self.client_as(self.b)
        self.assertEqual(client.get(image_item["url"]).headers["content-type"], "image/webp")
        self.assertEqual(client.get(video_item["url"]).status_code, 410)
        # A compressed blob is never offered for dedupe; re-uploading restores the original bytes.
        spec = {"sha256": sha(image), "size": len(image), "mime": "image/png", "filename": "f"}
        self.assertFalse(self.run_service(media.precheck, self.user(self.a), **spec)[0]["exists"])
        self.upload(image, mime="image/png")
        self.assertEqual(self.blob_file(sha(image)).read_bytes(), image)

    def test_admin_storage_endpoint(self):
        self.run_retention()
        client = self.client_as(self.teacher)
        payload = client.get("/api/social/admin/storage").json()
        for key in ("blobs", "media", "active_uploads", "disk", "watermarks", "last_run", "presence"):
            self.assertIn(key, payload)
        self.assertEqual(self.client_as(self.a).get("/api/social/admin/storage").status_code, 403)

    def test_scheduler_tasks_registered_and_armed(self):
        from classroom_app.services import scheduled_task_handlers  # noqa: F401 - registers handlers
        from classroom_app.services.scheduled_task_service import get_registered_task_kinds

        kinds = get_registered_task_kinds()
        self.assertIn(retention.TASK_KIND_RETENTION, kinds)
        self.assertIn(retention.TASK_KIND_UPLOAD_CLEANUP, kinds)
        for _ in range(2):
            self.run_service(retention.ensure_social_media_tasks)
        self.assertEqual(self.count("SELECT COUNT(*) FROM scheduled_tasks WHERE dedupe_key LIKE 'social:%'"), 2)

    def test_stale_upload_sessions_cleaned(self):
        spec = {"sha256": sha(b"x" * 10), "size": 10, "mime": "text/plain", "filename": "x.txt"}
        session, _ = self.run_service(media.upload_init, self.user(self.a), **spec)
        with self.connect() as conn:
            conn.execute("UPDATE social_upload_sessions SET updated_at = ?", (PAST,))
            conn.commit()
        self.assertEqual(self.run_service(media.cleanup_stale_sessions)[0], 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_upload_sessions WHERE status = 'expired'"), 1)
        self.assertFalse((self.tmp / "uploads" / f"{session['upload_id']}.part").exists())


if __name__ == "__main__":
    unittest.main()
