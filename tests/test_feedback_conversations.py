import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db.schema_feedback_conversations import ensure_feedback_conversation_schema
from classroom_app.services import feedback_conversation_service as service
from classroom_app.services import message_center_service as notifications


class FeedbackConversationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT,email TEXT,is_active INTEGER,is_super_admin INTEGER);
            INSERT INTO teachers VALUES(1,'ordinary','',1,0),(2,'admin','',1,1),(3,'admin2','',1,1),(4,'disabled','',0,1);
            CREATE TABLE app_feedback(id INTEGER PRIMARY KEY,user_role TEXT,user_id TEXT,user_name TEXT,
                title TEXT,description TEXT,feedback_type TEXT,section TEXT,page_url TEXT,status TEXT,created_at TEXT,updated_at TEXT);
            INSERT INTO app_feedback VALUES(1,'student','1','student','Original','Original body','bug','','','pending','old','old');
            CREATE TABLE app_feedback_attachments(id INTEGER PRIMARY KEY,feedback_id INTEGER REFERENCES app_feedback(id) ON DELETE CASCADE,
                file_hash TEXT,original_filename TEXT,file_size INTEGER,mime_type TEXT,created_at TEXT);
            INSERT INTO app_feedback_attachments VALUES(1,1,'hash','Original.png',10,'image/png','old');
            CREATE TABLE message_center_notifications(id INTEGER PRIMARY KEY,recipient_role TEXT,recipient_user_pk INTEGER,
                category TEXT,ref_type TEXT,ref_id TEXT,read_at TEXT);
        """)
        ensure_feedback_conversation_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.owner = {"role": "student", "id": 1, "name": "Student"}
        self.admin = {"role": "teacher", "id": 2, "name": "Admin"}
        self.other = {"role": "teacher", "id": 1, "name": "Same numeric id"}
        self.engine = patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite")
        self.engine.start()
        self.notice_patch = patch.object(service, "create_feedback_conversation_notifications", return_value=1)
        self.notice = self.notice_patch.start()

    def tearDown(self):
        self.notice_patch.stop()
        self.engine.stop()
        self.conn.close()

    def reply(self, user=None, token="message_0001", content="Reply"):
        result = service.reply_to_feedback(self.conn, 1, user or self.owner,
                                           {"content": content, "client_message_id": token})
        self.conn.commit()
        return result

    def status(self, status, last_id, token="status_00001", user=None):
        result = service.change_feedback_status(self.conn, 1, user or self.admin,
            {"status": status, "expected_last_message_id": last_id, "client_message_id": token})
        self.conn.commit()
        return result

    def assert_http(self, code, callback):
        with self.assertRaises(HTTPException) as ctx:
            callback()
        self.assertEqual(code, ctx.exception.status_code)
        self.conn.rollback()

    def test_role_id_collision_blocks_detail_reply_withdraw_and_owner_upload_check(self):
        for action in (lambda: service.get_feedback_detail(self.conn, 1, self.other),
                       lambda: self.reply(self.other),
                       lambda: service.withdraw_feedback(self.conn, 1, self.other),
                       lambda: service.load_feedback(self.conn, 1, self.other, lock=True, owner_only=True)):
            self.assert_http(403, action)
        self.assertEqual([], service.list_feedback(self.conn, self.other)["items"])
        self.assert_http(403, lambda: service.list_feedback(self.conn, self.other, admin=True))

    def test_additive_migration_keeps_original_body_attachments_and_is_repeatable(self):
        ensure_feedback_conversation_schema(self.conn, engine="sqlite")
        detail = service.get_feedback_detail(self.conn, 1, self.owner)
        self.assertEqual("Original body", detail["feedback"]["description"])
        self.assertEqual("Original.png", detail["attachments"][0]["original_filename"])
        self.assertTrue(detail["can_withdraw"])

    def test_reply_deduplicates_and_token_cannot_change_content(self):
        first = self.reply()
        second = self.reply()
        self.assertEqual(first["message"]["id"], second["message"]["id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(1, self.notice.call_count)
        self.assert_http(409, lambda: self.reply(content="Changed"))
        detail = service.get_feedback_detail(self.conn, 1, self.admin)
        self.assertEqual(1, detail["feedback"]["unread_count"])
        self.assertFalse(detail["can_withdraw"])

    def test_only_admin_controls_status_and_stale_close_does_not_hide_new_reply(self):
        mid = self.reply()["message"]["id"]
        self.assert_http(403, lambda: self.status("closed", mid, user=self.owner))
        self.assert_http(409, lambda: self.status("closed", 0))
        closed = self.status("closed", mid)
        self.assertFalse(closed["feedback"]["can_reply"])
        self.assertTrue(self.status("closed", mid)["deduplicated"])
        self.assertTrue(self.reply()["deduplicated"])
        self.assert_http(409, lambda: self.reply(token="after_closed"))
        self.assert_http(409, lambda: service.withdraw_feedback(self.conn, 1, self.owner))
        reopened = self.status("open", closed["message"]["id"], token="reopen_0001")
        self.assertTrue(reopened["feedback"]["can_reply"])
        self.assertEqual("reopened", reopened["message"]["event_type"])
        self.reply(token="after_reopen")

    def test_read_cursor_is_owned_monotonic_and_notifications_stop_at_observed_message(self):
        first = self.reply(token="first_0001")["message"]["id"]
        second = self.reply(token="second_0001")["message"]["id"]
        for message_id in (first, second):
            self.conn.execute("INSERT INTO message_center_notifications VALUES(?, 'teacher',2,'app_feedback','app_feedback_message',?,NULL)",
                              (message_id, str(message_id)))
        self.conn.commit()
        self.assertEqual(1, service.mark_feedback_read(self.conn, 1, self.admin, first)["unread_count"])
        self.conn.commit()
        self.assertIsNone(self.conn.execute("SELECT read_at FROM message_center_notifications WHERE id=?", (second,)).fetchone()[0])
        self.assertEqual(0, service.mark_feedback_read(self.conn, 1, self.admin, second)["unread_count"])
        self.conn.commit()
        service.mark_feedback_read(self.conn, 1, self.admin, first)
        self.conn.commit()
        self.assertEqual(second, self.conn.execute("SELECT last_read_message_id FROM app_feedback_reads").fetchone()[0])
        self.assert_http(400, lambda: service.mark_feedback_read(self.conn, 1, self.admin, second + 100))
        self.assert_http(403, lambda: service.mark_feedback_read(self.conn, 1, self.other, first))

    def test_pagination_has_no_duplicate_or_skipped_messages(self):
        expected = [self.reply(token=f"page_{i:08d}")["message"]["id"] for i in range(7)]
        page = service.list_messages(self.conn, 1, limit=3)
        self.assertEqual(expected[-3:], [m["id"] for m in page["items"]])
        older = service.list_messages(self.conn, 1, before_id=page["next_before_id"], limit=4)
        self.assertEqual(expected[:-3], [m["id"] for m in older["items"]])
        self.assertFalse(older["has_more"])

    def test_notifications_reach_owner_and_other_admins_with_correct_destinations(self):
        feedback = dict(self.conn.execute("SELECT * FROM app_feedback WHERE id=1").fetchone())
        event = {"id": 20, "sender_role": "teacher", "sender_id": "2", "sender_name": "Admin",
                 "event_type": "reply", "content": "Response", "created_at": "now"}
        with patch.object(notifications, "_insert_notification_if_allowed", return_value=1) as insert:
            self.assertEqual(2, notifications.create_feedback_conversation_notifications(self.conn, feedback, event))
        payloads = [call.args[1] for call in insert.call_args_list]
        by_identity = {(p["recipient_role"], p["recipient_user_pk"]): p for p in payloads}
        self.assertEqual({("student", 1), ("teacher", 3)}, set(by_identity))
        self.assertEqual("/dashboard?feedback_id=1", by_identity[("student", 1)]["link_url"])
        self.assertEqual("/manage/system/feedback?feedback_id=1", by_identity[("teacher", 3)]["link_url"])
        self.assertIn("app_feedback", notifications.VISIBLE_NOTIFICATION_CATEGORIES["student"])

    def test_untouched_feedback_can_be_withdrawn_with_attachments_cascading(self):
        service.withdraw_feedback(self.conn, 1, self.owner)
        self.conn.commit()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM app_feedback_attachments").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
