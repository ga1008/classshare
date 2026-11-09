"""Ordinary group attachment policy survives the text/reference adapter."""
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

from classroom_app.services import social_media_store as store
from fastapi import HTTPException
from classroom_app.services.social_common import now_iso
from classroom_app.services.social_conversation_service import insert_message
from classroom_app.services.agent_actor_service import resolve_agent_actor
from classroom_app.services.wanderer_identity_service import load_wanderer
from classroom_app.services.wanderer_platform_attachments import read_attachment
from tests.wanderer_test_support import WandererDbTestCase


class WandererAttachmentReadTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="lanshare-synthetic-media-"))
        self.enterContext(patch.object(store, "SOCIAL_STORE_ROOT", Path(directory)))
        with self.connect() as conn:
            wid = conn.execute("SELECT id FROM wanderer_agents ORDER BY id LIMIT 1").fetchone()[0]
            wanderer = load_wanderer(conn, wid)
            self.actor = resolve_agent_actor(conn, "student", wanderer["student_id"], service_wanderer=wanderer)
            stamp = now_iso()
            self.group = conn.execute("INSERT INTO social_groups(name,owner_identity,created_at,updated_at) VALUES('Synthetic',?,?,?) RETURNING id",
                                      (self.actor.key, stamp, stamp)).fetchone()[0]
            self.key = f"group:{self.group}"
            conn.execute("INSERT INTO social_group_members(group_id,member_identity,joined_at) VALUES(?,?,?)", (self.group, self.actor.key, stamp))
            self.message = insert_message(conn, key=self.key, sender=self.actor.key, body="Synthetic attachment", client_nonce=uuid.uuid4().hex)
            conn.commit()

    def attachment(self, conn, content, name, mime, kind):
        digest, _ = store.publish_bytes(content)
        store.upsert_blob_ready(conn, digest, len(content), mime)
        return conn.execute("INSERT INTO social_message_media(message_id,blob_hash,store,original_filename,mime_type,file_size,media_kind,created_at) "
                            "VALUES(?,?,'social',?,?,?,?,?) RETURNING id", (self.message, digest, name, mime, len(content), kind, now_iso())).fetchone()[0]

    def test_text_extract_keeps_scope_and_never_exposes_server_path(self):
        with self.connect() as conn:
            identifier = self.attachment(conn, b"Authorized synthetic document", "note.txt", "text/plain", "file")
            result = read_attachment(conn, self.actor, {"social_media_id": identifier})
            self.assertEqual(result["content"], "Authorized synthetic document")
            self.assertEqual(result["scope"], {"kind": "conversation", "key": self.key})
            self.assertTrue(result["content_observed"])
            self.assertNotIn("path", result)
            conn.commit()

    def test_recalled_or_revoked_group_attachment_cannot_be_recovered(self):
        with self.connect() as conn:
            identifier = self.attachment(conn, b"Synthetic", "note.txt", "text/plain", "file")
            conn.execute("UPDATE social_messages SET recalled_at=? WHERE id=?", (now_iso(), self.message))
            with self.assertRaises(HTTPException):
                read_attachment(conn, self.actor, {"social_media_id": identifier})
            conn.execute("UPDATE social_messages SET recalled_at=NULL WHERE id=?", (self.message,))
            conn.execute("UPDATE social_group_members SET left_at=? WHERE group_id=? AND member_identity=?", (now_iso(), self.group, self.actor.key))
            with self.assertRaises(HTTPException):
                read_attachment(conn, self.actor, {"social_media_id": identifier})

    def test_image_reference_never_claims_visual_content_was_observed(self):
        with self.connect() as conn:
            identifier = self.attachment(conn, b"Synthetic image bytes", "image.png", "image/png", "image")
            result = read_attachment(conn, self.actor, {"social_media_id": identifier})
            self.assertFalse(result["content_observed"])
            self.assertEqual(result["perception"], "reference_only")
            self.assertNotIn("content", result)
            self.assertEqual(result["url"], f"/api/social/media/{identifier}")

    def test_legacy_attachment_reuses_sender_recipient_access_and_private_scope(self):
        from classroom_app.services import message_center_service as messages
        from classroom_app.services.agent_bridge_service import allowed_file_roots
        key = messages.build_conversation_key(self.actor.key, "teacher:1", None)
        data = b"Legacy synthetic attachment"
        digest, path = store.publish_bytes(data)
        with self.connect() as conn:
            message = conn.execute("INSERT INTO private_messages(conversation_key,sender_identity,sender_role,sender_user_pk,"
                "sender_display_name,recipient_identity,recipient_role,recipient_user_pk,recipient_display_name,content) "
                "VALUES(?,?,'student',?,'Synthetic','teacher:1','teacher',1,'Teacher','Attachment') RETURNING id",
                (key,self.actor.key,self.actor.id)).fetchone()[0]
            identifier = conn.execute("INSERT INTO private_message_attachments(message_id,conversation_key,uploaded_by_identity,"
                "uploaded_by_role,file_hash,original_filename,mime_type,file_size) VALUES(?,?,?,'student',?,'note.txt','text/plain',?) RETURNING id",
                (message,key,self.actor.key,digest,len(data))).fetchone()[0]
            # Only the physical content store is synthetic; the ordinary
            # sender/recipient authorizer and descriptor parser remain real.
            with patch.object(messages, "resolve_global_file_path", return_value=path), patch(
                    "classroom_app.services.agent_bridge_service.allowed_file_roots", return_value=[*allowed_file_roots(), Path(store.SOCIAL_STORE_ROOT)]):
                result = read_attachment(conn,self.actor,{"private_attachment_id":identifier})
                self.assertEqual(result["content"], data.decode())
                self.assertEqual(result["scope"], {"kind":"conversation","key":key})
                conn.execute("UPDATE private_messages SET sender_identity='student:999999' WHERE id=?", (message,))
                with self.assertRaises(PermissionError):
                    read_attachment(conn,self.actor,{"private_attachment_id":identifier})
