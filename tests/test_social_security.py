"""Security review fixes for the social backend (H1–H4, M5–M14) and the live-QA follow-ups.

Isolated SQLite + temp media roots (``SocialDbTestCase``); no network, no real data.
"""
from __future__ import annotations

import asyncio
import io
import os
import threading
import time
from unittest.mock import patch

from PIL import Image
from starlette.websockets import WebSocketDisconnect

from classroom_app.db import schema_social
from classroom_app.services import presence_service, social_push_service
from classroom_app.services import social_conversation_service as conversations
from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_group_service as groups
from classroom_app.services import social_identity_service as identities
from classroom_app.services import social_media_retention_service as retention
from classroom_app.services import social_media_service as media
from classroom_app.services import social_media_store as store
from classroom_app.services import social_message_service as messages
from classroom_app.services.social_common import SocialError, blocked_either, dm_key, run_db
from classroom_app.services.social_relations import presence_audience
from tests.social_test_support import SocialDbTestCase, sha
from tests.test_social_ws import fake_verify_token, receive_until


class _Base(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.key = dm_key(self.a, self.b)

    @staticmethod
    def spec(data: bytes, mime="application/octet-stream") -> dict:
        return {"sha256": sha(data), "size": len(data), "mime": mime, "filename": "f.bin"}

    def upload(self, owner: str, data: bytes, **extra) -> str:
        session, _ = self.run_service(media.upload_init, self.user(owner), **{**self.spec(data), **extra})
        chunk = session["chunk_size"]
        for index in range(0, max(1, (len(data) + chunk - 1) // chunk)):
            self.run_service(media.write_chunk, self.user(owner), session["upload_id"], index,
                             data[index * chunk:(index + 1) * chunk])
        return self.run_service(media.upload_complete, self.user(owner), session["upload_id"])[0]["media_token"]

    def send_media(self, token: str, sender=None, key=None) -> dict:
        message, _ = self.run_service(messages.send_message, self.user(sender or self.a), key or self.key,
                                      {"body": "", "media_tokens": [token]})
        return message


class PrecheckAccessTest(_Base):  # H1
    def test_hash_alone_grants_nothing(self):
        data = os.urandom(3000)
        self.upload(self.a, data)
        self.assertEqual(self.run_service(media.precheck, self.user(self.c), **self.spec(data))[0], {"exists": False})
        self.assertTrue(self.run_service(media.precheck, self.user(self.a), **self.spec(data))[0]["exists"])

    def test_global_store_never_via_precheck(self):
        data = b"course handout " * 40
        self.put_global_file(data)
        self.assertEqual(self.run_service(media.precheck, self.user(self.a), **self.spec(data))[0], {"exists": False})
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_tokens"), 0)

    def test_group_member_access_ends_when_leaving(self):
        self.befriend(self.a, self.c)
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.c])
        data = os.urandom(2048)
        self.send_media(self.upload(self.a, data), key=card["conversation_key"])
        self.assertTrue(self.run_service(media.precheck, self.user(self.c), **self.spec(data))[0]["exists"])
        self.run_service(groups.leave_group, self.user(self.c), card["id"])
        self.assertFalse(self.run_service(media.precheck, self.user(self.c), **self.spec(data))[0]["exists"])


class UploadAbuseTest(_Base):  # H2, M14
    def _put(self, client, upload_id: str, data: bytes):
        return client.put(f"/api/social/media/upload/{upload_id}/chunk", params={"index": 0}, content=data)

    def test_foreign_session_rejected_before_any_limiter(self):
        data = os.urandom(1000)
        session, _ = self.run_service(media.upload_init, self.user(self.a), **self.spec(data))
        response = self._put(self.client_as(self.b), session["upload_id"], data)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(media.upload_limiter._buckets, {})
        self.assertEqual(media.upload_limiter._in_flight, set())

    def test_one_chunk_in_flight_per_person(self):
        data = os.urandom(1000)
        session, _ = self.run_service(media.upload_init, self.user(self.a), **self.spec(data))
        media.upload_limiter.begin(self.a)  # another chunk of a is still being written
        try:
            response = self._put(self.client_as(self.a), session["upload_id"], data)
        finally:
            media.upload_limiter.end(self.a)
        self.assertEqual(response.status_code, 429)

    def test_token_bucket_rejects_with_retry_after(self):
        limiter = media.ByteRateLimiter()
        rate = 2 * 1024 * 1024
        limiter.take("x", media.CHUNK_SIZE, rate, now=0.0)
        limiter.take("x", media.CHUNK_SIZE, rate, now=0.0)  # burst of two chunks
        with self.assertRaises(SocialError) as ctx:
            limiter.take("x", media.CHUNK_SIZE, rate, now=0.0)
        self.assertEqual((ctx.exception.status_code, ctx.exception.retry_after), (429, 1))
        limiter.take("x", media.CHUNK_SIZE, rate, now=0.5)  # refilled at ~2 MB/s
        data = os.urandom(1000)
        session, _ = self.run_service(media.upload_init, self.user(self.a), **self.spec(data))
        media.upload_limiter._buckets[self.a] = (-10.0 * rate, time.monotonic())  # far over the rate
        response = self._put(self.client_as(self.a), session["upload_id"], data)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_global_upload_slot_has_a_short_timeout(self):
        data = os.urandom(1000)
        session, _ = self.run_service(media.upload_init, self.user(self.a), **self.spec(data))
        media._slot_registry["upload"] = asyncio.Semaphore(0)  # every slot busy
        with patch.object(media, "UPLOAD_SLOT_WAIT_SECONDS", 0.05):
            response = self._put(self.client_as(self.a), session["upload_id"], data)
        self.assertEqual(response.status_code, 503)

    def test_mime_is_validated(self):  # M14
        cases = (("image/PNG", "image/png"), ("text/html; charset=utf-8", "application/octet-stream"),
                 ("../../etc", "application/octet-stream"), ("video/x-m4v", "video/x-m4v"),
                 ("application/vnd.ms-excel", "application/vnd.ms-excel"), ("a/b c", "application/octet-stream"))
        for raw, expected in cases:
            self.assertEqual(media._clean_mime(raw), expected, raw)


class WebSocketAbuseTest(_Base):  # H3, M9
    def setUp(self):
        super().setUp()
        verify = patch("classroom_app.routers.social_ws.verify_token", side_effect=fake_verify_token)
        verify.start()
        self.addCleanup(verify.stop)

    def connect_ws(self, client, identity: str):
        return client.websocket_connect("/ws/social", headers={"cookie": f"access_token={identity}"})

    def test_frame_limiter_burst_drop_and_abuse(self):
        from classroom_app.routers.social_ws import FrameLimiter

        limiter = FrameLimiter()
        self.assertEqual(sum(limiter.allow(now=0.0) for _ in range(60)), 40)
        self.assertFalse(limiter.abusive)
        for _ in range(100):
            limiter.allow(now=0.0)
        self.assertTrue(limiter.abusive)

    def test_typing_throttle(self):
        from classroom_app.routers.social_ws import typing_allowed

        self.assertTrue(typing_allowed("t:1", "k", now=100.0))
        self.assertFalse(typing_allowed("t:1", "k", now=101.0))
        self.assertTrue(typing_allowed("t:1", "other", now=101.0))
        self.assertTrue(typing_allowed("t:1", "k", now=103.5))

    def test_at_most_five_sockets_oldest_evicted(self):
        for index in range(5):
            self.assertEqual(social_push_service.register("student:99", f"c{index}", object()), [])
        evicted = social_push_service.register("student:99", "c5", object())
        self.assertEqual([conn_id for conn_id, _ in evicted], ["c0"])
        self.assertEqual(social_push_service.connection_count("student:99"), 5)

    def test_lan_payload_cap_and_offline_peer(self):
        client = self.client_as(self.a)
        with self.connect_ws(client, self.a) as ws_a:
            receive_until(ws_a, "hello")
            ws_a.send_json({"type": "lan_offer", "to": self.b, "transfer_id": 1, "payload": {}})
            self.assertEqual(receive_until(ws_a, "error")["detail"], "对方不在线，无法直传。")
            with self.connect_ws(client, self.b) as ws_b:
                receive_until(ws_b, "hello")
                ws_a.send_json({"type": "lan_offer", "to": self.b, "transfer_id": 1,
                                "payload": {"sdp": "x" * 17000}})
                self.assertEqual(receive_until(ws_a, "error")["detail"], "直传信令过大。")

    def test_flooding_socket_is_closed(self):
        from classroom_app.routers import social_ws

        client = self.client_as(self.a)
        limits = (patch.object(social_ws, "ABUSE_DROPS", 3), patch.object(social_ws, "FRAME_BURST", 2.0),
                  patch.object(social_ws, "FRAME_RATE", 0.001))
        for item in limits:
            item.start()
            self.addCleanup(item.stop)
        with self.connect_ws(client, self.a) as ws:
            receive_until(ws, "hello")
            with self.assertRaises(WebSocketDisconnect) as ctx:
                for _ in range(10):
                    ws.send_json({"type": "focus", "conversation_key": None})
                for _ in range(10):
                    ws.receive_json()
        self.assertEqual(ctx.exception.code, 1008)

    def test_failed_push_closes_the_socket(self):  # M9
        class Broken:
            closed_with = None

            async def send_text(self, text):
                raise RuntimeError("gone")

            async def close(self, code=1000):
                Broken.closed_with = code

        social_push_service.register("student:77", "dead", Broken())
        delivered = asyncio.run(social_push_service.deliver_local(["student:77"], {"type": "pong"}))
        self.assertEqual(delivered, 0)
        self.assertEqual(Broken.closed_with, 1011)
        self.assertEqual(social_push_service.connection_count("student:77"), 0)

    def test_social_db_work_uses_its_own_pool(self):
        name = asyncio.run(run_db(lambda: threading.current_thread().name))
        self.assertTrue(name.startswith("social-db"), name)


class FriendRequestAbuseTest(_Base):  # H4
    def test_withdraw_never_refunds_the_daily_cap(self):
        with patch.object(friends, "DAILY_REQUEST_LIMIT", 2):
            first, _ = self.run_service(friends.send_request, self.user(self.c), self.d)
            self.run_service(friends.withdraw_request, self.user(self.c), first["request_id"])
            self.run_service(friends.send_request, self.user(self.c), self.e)
            with self.assertRaises(SocialError) as ctx:
                self.run_service(friends.send_request, self.user(self.c), self.a)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_withdraw_starts_a_24h_pair_cooldown(self):
        first, _ = self.run_service(friends.send_request, self.user(self.c), self.d)
        self.run_service(friends.withdraw_request, self.user(self.c), first["request_id"])
        with self.assertRaises(SocialError) as ctx:
            self.run_service(friends.send_request, self.user(self.c), self.d)
        self.assertIn("24 小时", ctx.exception.detail)
        self.run_service(friends.send_request, self.user(self.c), self.e)  # other people are fine

    def test_repeat_requests_aggregate_into_one_notification(self):
        self.run_service(friends.send_request, self.user(self.c), self.d, "hi")
        self.run_service(friends.send_request, self.user(self.d), self.c)  # mutual: now friends
        self.run_service(friends.delete_friend, self.user(self.c), self.d)
        self.run_service(friends.send_request, self.user(self.c), self.d, "again")
        self.assertEqual(self.count("SELECT COUNT(*) FROM message_center_notifications WHERE recipient_identity = ? "
                                    "AND ref_id LIKE 'friend_request:%'", (self.d,)), 1)


class RetentionRaceTest(_Base):  # M5, M6, M13
    def test_attach_after_purge_is_refused(self):
        data = os.urandom(2048)
        token = self.upload(self.a, data)
        with self.connect() as conn:
            conn.execute("UPDATE social_media_blobs SET storage_state = 'deleted'")
            conn.commit()
        store.blob_path(sha(data)).unlink()
        with self.assertRaises(SocialError) as ctx:
            self.send_media(token)
        self.assertEqual(ctx.exception.status_code, 410)

    def test_purge_never_forces_refs_and_respects_live_tokens(self):
        data = os.urandom(2048)
        self.upload(self.a, data)  # live token, ref_count 0
        with self.connect() as conn:
            self.assertEqual(retention._delete_blob_locked(conn, sha(data), dry_run=False, grace_cutoff=None), 0)
            conn.execute("UPDATE social_media_tokens SET used_at = '2026-01-01T00:00:00+00:00'")
            conn.execute("UPDATE social_media_blobs SET ref_count = 1")
            self.assertEqual(retention._delete_blob_locked(conn, sha(data), dry_run=False, grace_cutoff=None), 0)
            conn.commit()
        self.assertEqual(self.count("SELECT ref_count FROM social_media_blobs"), 1)
        self.assertTrue(store.blob_path(sha(data)).is_file())

    def test_units_are_committed_as_they_go(self):
        item = self.send_media(self.upload(self.a, os.urandom(1024)))["media"][0]
        with self.connect() as conn:
            conn.execute("UPDATE social_message_media SET expires_at = '2025-01-01T00:00:00+00:00' WHERE id = ?",
                         (item["id"],))
            conn.commit()
        with patch.object(retention, "disk_usage", return_value=(1000, 100)), self.connect() as conn:
            retention.run_retention(conn)
            self.assertFalse(conn.in_transaction)  # nothing left pending for the caller
            # Visible from another connection without any further commit by the caller.
            self.assertEqual(self.count("SELECT COUNT(*) FROM social_message_media WHERE expired_at IS NOT NULL"), 1)

    def test_compression_publishes_a_new_blob_and_repoints_media(self):
        buffer = io.BytesIO()
        Image.frombytes("RGB", (256, 256), os.urandom(256 * 256 * 3)).save(buffer, format="PNG")
        image = buffer.getvalue()
        item = self.send_media(self.upload(self.a, image, mime="image/png"))["media"][0]
        with patch.object(retention, "COMPRESS_MIN_BYTES", 1024), \
                patch.object(retention, "disk_usage", return_value=(10_000_000, 9_500_000)):
            self.run_service(retention.run_retention)
        with self.connect() as conn:
            media_row = dict(conn.execute("SELECT * FROM social_message_media WHERE id = ?", (item["id"],)).fetchone())
            old = dict(conn.execute("SELECT * FROM social_media_blobs WHERE blob_hash = ?", (sha(image),)).fetchone())
            new = dict(conn.execute("SELECT * FROM social_media_blobs WHERE blob_hash = ?",
                                    (media_row["blob_hash"],)).fetchone())
        self.assertNotEqual(media_row["blob_hash"], sha(image))
        self.assertEqual((media_row["mime_type"], old["storage_state"]), ("image/webp", "deleted"))
        self.assertEqual((new["storage_state"], new["ref_count"]), ("compressed", 1))
        self.assertFalse(store.blob_path(sha(image)).exists())
        webp = store.blob_path(media_row["blob_hash"]).read_bytes()
        self.assertEqual(sha(webp), media_row["blob_hash"])  # content addressing stays true
        response = self.client_as(self.b).get(item["url"])
        self.assertEqual((response.status_code, response.content), (200, webp))


class PrivacyAndBlockTest(_Base):  # M7, M8, live-QA items 1 and 4
    def test_profile_and_presence_hidden_from_strangers_and_across_blocks(self):
        presence_service.connection_opened(self.b, "s", "social_ws")
        friend_view, _ = self.run_service(identities.profile_sidebar, self.user(self.a), self.b)
        self.assertTrue(friend_view["online"])
        stranger_view, _ = self.run_service(identities.profile_sidebar, self.user(self.c), self.b)
        self.assertEqual((stranger_view["online"], stranger_view["last_seen_at"], stranger_view["mood"]),
                         (None, None, ""))
        self.befriend(self.a, self.c)
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b, self.c])
        self.run_service(friends.block_user, self.user(self.c), self.b)
        with self.connect() as conn:
            self.assertNotIn(self.c, presence_audience(conn, self.b))
        detail, _ = self.run_service(groups.group_detail, self.user(self.c), card["id"])
        member = next(item for item in detail["members"] if item["identity"] == self.b)
        self.assertIsNone(member["online"])

    def test_group_nudge_refused_across_a_block(self):
        self.befriend(self.a, self.c)
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b, self.c])
        self.run_service(friends.block_user, self.user(self.c), self.b)
        with self.assertRaises(SocialError) as ctx:
            self.run_service(messages.nudge, self.user(self.b), card["conversation_key"], self.c)
        self.assertEqual((ctx.exception.status_code, ctx.exception.detail), (403, "暂时无法给对方发消息。"))

    def test_teacher_can_be_blocked_in_the_social_scope_only(self):
        client = self.client_as(self.a)
        response = client.post("/api/social/blocks", json={"identity": self.teacher})
        self.assertEqual(response.status_code, 200, response.text)
        blocks = client.get("/api/social/blocks").json()["items"]
        self.assertEqual([(item["identity"], item["scope"]) for item in blocks], [(self.teacher, "social")])
        self.assertTrue(blocks[0]["created_at"].endswith("+00:00"))
        self.assertEqual(self.count("SELECT COUNT(*) FROM private_message_blocks"), 0)  # classroom unaffected
        with self.connect() as conn:
            self.assertTrue(blocked_either(conn, self.a, self.teacher))
        with self.assertRaises(SocialError):
            self.run_service(friends.send_request, self.user(self.teacher), self.a)
        self.assertEqual(client.delete(f"/api/social/blocks/{self.teacher}").status_code, 200)
        self.assertEqual(client.get("/api/social/blocks").json()["items"], [])

    def test_block_created_at_is_utc(self):
        self.run_service(friends.block_user, self.user(self.a), self.c)
        with self.connect() as conn:
            conn.execute("UPDATE private_message_blocks SET created_at = '2026-10-27 08:00:00'")  # legacy UTC stamp
            conn.commit()
        blocks, _ = self.run_service(friends.list_blocks, self.user(self.a))
        self.assertEqual((blocks[0]["scope"], blocks[0]["created_at"]), ("all", "2026-10-27T08:00:00.000000+00:00"))


class ConversationIndexTest(_Base):  # M10, M11, M12, live-QA items 2 and 3
    def test_dm_listing_uses_the_participants_table(self):
        self.run_service(messages.send_message, self.user(self.a), self.key, {"body": "hi"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_dm_participants WHERE conversation_key = ?",
                                    (self.key,)), 2)
        with self.connect() as conn:
            self.assertEqual(set(conversations.dm_keys(conn, self.b)), {self.key})
            conn.execute("DELETE FROM social_dm_participants")
            schema_social._backfill_dm_participants(conn)  # one-time fill of an empty table
            conn.commit()
            self.assertEqual(set(conversations.dm_keys(conn, self.a)), {self.key})

    def test_sender_is_serialised_before_counting(self):
        with patch.object(messages, "serialize_on", wraps=messages.serialize_on) as spy:
            self.run_service(messages.send_message, self.user(self.a), self.key, {"body": "hi"})
        self.assertEqual(spy.call_args_list[0].args[1:], ("social-send", [self.a]))

    def test_schema_flag_only_after_a_committed_ensure(self):
        with patch.object(schema_social, "_SCHEMA_READY", False), self.connect() as conn:
            conn.execute("INSERT INTO user_presence (identity, last_seen_at) VALUES ('student:999', 'x')")
            schema_social.ensure_social_schema(conn)  # caller transaction is open: may still roll back
            self.assertFalse(schema_social._SCHEMA_READY)
            conn.rollback()
            schema_social.ensure_social_schema(conn)  # idle connection: committed on its own
            self.assertTrue(schema_social._SCHEMA_READY)

    def test_profile_survives_a_broken_blog_query(self):
        with patch("classroom_app.services.blog_moderation_states.public_state_sql", return_value="no_such_column"):
            profile, _ = self.run_service(identities.profile_sidebar, self.user(self.a), self.b)
        self.assertEqual(profile["recent"], [])

    def test_system_and_own_messages_are_never_unread(self):
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b])
        self.run_service(messages.send_message, self.user(self.a), card["conversation_key"], {"body": "我的消息"})
        mine = {item["conversation_key"]: item["unread"] for item in
                self.run_service(conversations.list_conversations, self.user(self.a))[0]}
        theirs = {item["conversation_key"]: item["unread"] for item in
                  self.run_service(conversations.list_conversations, self.user(self.b))[0]}
        self.assertEqual((mine[card["conversation_key"]], mine[self.key]), (0, 0))
        self.assertEqual((theirs[card["conversation_key"]], theirs[self.key]), (1, 0))

    def test_lan_backfill_repush_is_per_recipient(self):
        data = os.urandom(4096)
        message, _ = self.run_service(messages.send_message, self.user(self.a), self.key,
                                      {"body": "直传", "lan_media": [self.spec(data)]})
        token = self.upload(self.a, data)
        _, events = self.run_service(media.backfill, self.user(self.a), message["media"][0]["id"], token)
        mine = {targets[0]: event["message"]["mine"] for targets, event in events if event["type"] == "message"}
        self.assertEqual(mine, {self.a: True, self.b: False})
