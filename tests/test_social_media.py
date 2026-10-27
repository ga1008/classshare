"""聊天媒体：预检去重、分块上传与校验、配额、令牌、下载鉴权、过期、局域网补传 (social doc §6)."""
from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from PIL import Image

from classroom_app.services import social_media_service as media
from classroom_app.services import social_message_service as messages
from classroom_app.services.social_common import SocialError, dm_key
from tests.social_test_support import SocialDbTestCase, sha


def png_bytes(size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class SocialMediaTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.key = dm_key(self.a, self.b)

    @staticmethod
    def spec(data: bytes, mime="application/octet-stream", filename="资料.bin") -> dict:
        return {"sha256": sha(data), "size": len(data), "mime": mime, "filename": filename}

    def upload(self, owner: str, data: bytes, **spec) -> str:
        session, _ = self.run_service(media.upload_init, self.user(owner), **{**self.spec(data), **spec})
        chunk = session["chunk_size"]
        for index in range(0, max(1, (len(data) + chunk - 1) // chunk)):
            self.run_service(media.write_chunk, self.user(owner), session["upload_id"], index,
                             data[index * chunk:(index + 1) * chunk])
        done, _ = self.run_service(media.upload_complete, self.user(owner), session["upload_id"])
        return done["media_token"]

    def send_with(self, token: str, sender=None) -> dict:
        message, _ = self.run_service(messages.send_message, self.user(sender or self.a), self.key,
                                      {"body": "", "media_tokens": [token]})
        return message

    def test_chunked_upload_publishes_and_precheck_dedupes(self):
        data = os.urandom(media.CHUNK_SIZE + 1234)
        token = self.upload(self.a, data)
        self.assertTrue((self.social_root / sha(data)[:2] / sha(data)[2:4] / sha(data)).is_file())
        quota, _ = self.run_service(media.quota, self.user(self.a))
        self.assertEqual(quota["used_today"], len(data))
        # Knowing the hash is not access: b has never seen these bytes.
        stranger, _ = self.run_service(media.precheck, self.user(self.b), **self.spec(data))
        self.assertEqual(stranger, {"exists": False})
        own, _ = self.run_service(media.precheck, self.user(self.a), **self.spec(data))  # own re-send dedupes
        self.assertTrue(own["exists"] and own["media_token"])
        message = self.send_with(token)
        forwarded, _ = self.run_service(media.precheck, self.user(self.b), **self.spec(data))
        self.assertTrue(forwarded["exists"])  # b received it in a conversation b belongs to
        quota_b, _ = self.run_service(media.quota, self.user(self.b))
        self.assertEqual(quota_b["used_today"], 0)  # dedupe hits are free
        outsider, _ = self.run_service(media.precheck, self.user(self.c), **self.spec(data))
        self.assertFalse(outsider["exists"])
        self.assertEqual(message["message_type"], "media")
        self.assertEqual(message["media"][0]["size"], len(data))
        self.assertEqual(self.count("SELECT ref_count FROM social_media_blobs WHERE blob_hash = ?", (sha(data),)), 1)

    def test_global_store_only_reached_through_a_verified_upload(self):
        data = b"global bytes " * 100
        self.put_global_file(data)
        result, _ = self.run_service(media.precheck, self.user(self.a), **self.spec(data))
        self.assertEqual(result, {"exists": False})  # precheck never hands out global-store tokens
        token = self.upload(self.a, data)
        quota, _ = self.run_service(media.quota, self.user(self.a))
        self.assertEqual(quota["used_today"], 0)  # bytes reused, nothing newly written
        self.assertFalse((self.social_root / sha(data)[:2] / sha(data)[2:4] / sha(data)).exists())  # no 2nd copy
        message = self.send_with(token)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_message_media WHERE store = 'global'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_blobs"), 0)
        client = self.client_as(self.b)
        response = client.get(message["media"][0]["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, data)

    def test_sha_mismatch_is_rejected(self):
        data = os.urandom(5000)
        declared = self.spec(data)
        declared["sha256"] = sha(b"something else")
        session, _ = self.run_service(media.upload_init, self.user(self.a), **declared)
        self.run_service(media.write_chunk, self.user(self.a), session["upload_id"], 0, data)
        with self.assertRaises(SocialError) as ctx:
            self.run_service(media.upload_complete, self.user(self.a), session["upload_id"])
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_blobs"), 0)
        self.assertEqual(list((self.tmp / "uploads").glob("*.part")), [])

    def test_size_quota_and_single_session(self):
        with patch.dict(os.environ, {"SOCIAL_MEDIA_FILE_LIMIT_MB": "1", "SOCIAL_MEDIA_DAILY_LIMIT_MB": "1"}):
            with self.assertRaises(SocialError) as ctx:
                self.run_service(media.upload_init, self.user(self.a), **self.spec(b"x" * (2 * 1024 * 1024)))
            self.assertEqual(ctx.exception.status_code, 413)
            self.upload(self.a, os.urandom(700 * 1024))
            with self.assertRaises(SocialError) as ctx:
                self.run_service(media.upload_init, self.user(self.a), **self.spec(os.urandom(400 * 1024)))
            self.assertEqual(ctx.exception.status_code, 429)
        first = os.urandom(100)
        self.run_service(media.upload_init, self.user(self.b), **self.spec(first))
        resumed, _ = self.run_service(media.upload_init, self.user(self.b), **self.spec(first))
        self.assertEqual(resumed["received_bytes"], 0)
        with self.assertRaises(SocialError) as ctx:
            self.run_service(media.upload_init, self.user(self.b), **self.spec(os.urandom(100)))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_token_single_use_and_owner_bound(self):
        token = self.upload(self.a, os.urandom(3000))
        with self.assertRaises(SocialError):
            self.send_with(token, sender=self.b)
        self.send_with(token)
        with self.assertRaises(SocialError) as ctx:
            self.send_with(token)
        self.assertEqual(ctx.exception.status_code, 410)

    def test_download_auth_range_thumbnail_and_expiry(self):
        image = png_bytes()
        token = self.upload(self.a, image, mime="image/png", filename="图.png")
        item = self.send_with(token)["media"][0]
        self.assertEqual(item["kind"], "image")
        outsider = self.client_as(self.c)
        self.assertEqual(outsider.get(item["url"]).status_code, 403)
        client = self.client_as(self.b)
        full = client.get(item["url"])
        self.assertEqual((full.status_code, full.content), (200, image))
        partial = client.get(item["url"], headers={"Range": "bytes=0-9"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, image[:10])
        self.assertEqual(partial.headers["Content-Range"], f"bytes 0-9/{len(image)}")
        thumb = client.get(item["thumb_url"])
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.headers["content-type"], "image/jpeg")
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_message_media WHERE thumbnail_hash <> ''"), 1)
        with self.connect() as conn:
            conn.execute("UPDATE social_message_media SET expired_at = '2026-01-01T00:00:00+00:00'")
            conn.commit()
        self.assertEqual(client.get(item["url"]).status_code, 410)

    def test_http_upload_flow(self):
        data = os.urandom(media.CHUNK_SIZE + 10)
        client = self.client_as(self.a)
        self.assertFalse(client.post("/api/social/media/precheck", json=self.spec(data)).json()["exists"])
        session = client.post("/api/social/media/upload/init", json=self.spec(data)).json()
        self.assertEqual(set(session), {"upload_id", "chunk_size", "received_bytes"})
        base = f"/api/social/media/upload/{session['upload_id']}"
        tail, head = data[media.CHUNK_SIZE:], data[:media.CHUNK_SIZE]
        self.assertEqual(client.put(f"{base}/chunk", params={"index": 1}, content=tail).status_code, 409)
        self.assertEqual(client.put(f"{base}/chunk", params={"index": 0}, content=head).status_code, 200)
        self.assertEqual(client.put(f"{base}/chunk", params={"index": 1}, content=tail).status_code, 200)
        token = client.post(f"{base}/complete").json()["media_token"]
        self.assertTrue(token)
        quota = client.get("/api/social/quota").json()
        self.assertEqual(set(quota), {"daily_limit", "used_today", "file_limit"})

    def test_lan_media_backfill(self):
        data = os.urandom(4096)
        message, _ = self.run_service(messages.send_message, self.user(self.a), self.key,
                                      {"body": "直传", "lan_media": [self.spec(data)]})
        item = message["media"][0]
        self.assertEqual((item["delivered_via"], item["server_ready"]), ("lan", False))
        self.assertTrue(item["transfer_id"])
        client = self.client_as(self.b)
        self.assertEqual(client.get(item["url"]).status_code, 409)
        status = client.post(f"/api/social/lan/{item['transfer_id']}/status", json={"status": "p2p_done"})
        self.assertEqual(status.json()["status"], "p2p_done")
        token = self.upload(self.a, data)
        result, events = self.run_service(media.backfill, self.user(self.a), item["id"], token)
        self.assertTrue(result["server_ready"])
        self.assertEqual(events[0][1]["type"], "message")
        self.assertEqual(client.get(item["url"]).content, data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_lan_transfers WHERE status = 'server_backfilled'"), 1)


if __name__ == "__main__":
    unittest.main()
