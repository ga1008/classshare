"""Forward history recovery uses indexed bounded pages and current access."""
from datetime import timedelta

from classroom_app.services import social_conversation_service as conversations
from classroom_app.services import social_group_service as groups
from classroom_app.services import social_message_service as messages
from classroom_app.services.social_common import SocialError, dm_key, utc_now
from tests.social_test_support import SocialDbTestCase


class SocialHistoryCursorTests(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.befriend(self.a, self.c)
        self.key = dm_key(self.a, self.b)

    def seed_messages(self, key, count):
        with self.connect() as conn:
            ids = [conversations.insert_message(conn, key=key, sender=self.a, body=f"Synthetic {index}")
                   for index in range(count)]
            conn.commit()
        return ids

    def page(self, identity, key=None, **options):
        return self.run_service(conversations.list_messages, self.user(identity), key or self.key, **options)[0]

    def test_forward_pages_fill_more_than_fifty_messages_to_a_fixed_watermark(self):
        ids = self.seed_messages(self.key, 235)
        first = self.page(self.b, after_id=ids[3], limit=100)
        self.assertEqual(ids[4:104], [row["id"] for row in first["items"]])
        self.assertTrue(first["has_more"])
        self.assertEqual(ids[-1], first["through_id"])
        later = self.seed_messages(self.key, 1)[0]
        collected = [row["id"] for row in first["items"]]
        cursor = first["next_after_id"]
        while first["has_more"]:
            first = self.page(self.b, after_id=cursor, through_id=ids[-1], limit=100)
            next_ids = [row["id"] for row in first["items"]]
            self.assertTrue(all(value > cursor for value in next_ids))
            collected.extend(next_ids)
            cursor = first["next_after_id"]
        self.assertEqual(ids[4:], collected)
        self.assertNotIn(later, collected)
        self.assertTrue(all(not row["mine"] for row in first["items"]))

    def test_cursor_cannot_read_another_direct_conversation_and_old_paging_stays_descending(self):
        ids = self.seed_messages(self.key, 7)
        foreign_key = dm_key(self.a, self.c)
        foreign = self.seed_messages(foreign_key, 1)[0]
        page = self.page(self.b, after_id=ids[0], through_id=foreign, limit=100)
        self.assertEqual(ids[1:], [row["id"] for row in page["items"]])
        with self.assertRaises(SocialError):
            self.page(self.c, after_id=0)
        newest = self.page(self.b, limit=2)
        self.assertEqual(ids[-2:], [row["id"] for row in newest["items"]])
        older = self.page(self.b, before_id=ids[-2], limit=2)
        self.assertEqual(ids[-4:-2], [row["id"] for row in older["items"]])

    def test_group_watermark_and_rows_respect_join_time_and_recheck_each_page(self):
        group, _ = self.run_service(groups.create_group, self.user(self.a), "Synthetic group", [self.b])
        key = group["conversation_key"]
        self.seed_messages(key, 2)
        since = (utc_now() - timedelta(hours=1)).isoformat()
        before_join = (utc_now() - timedelta(days=1)).isoformat()
        with self.connect() as conn:
            conn.execute("UPDATE social_messages SET created_at = ? WHERE conversation_key = ?", (before_join, key))
            conn.execute("UPDATE social_group_members SET joined_at = ? WHERE group_id = ? AND member_identity = ?", (since, group["id"], self.b))
            conn.commit()
        empty = self.page(self.b, key, after_id=0)
        self.assertEqual([], empty["items"])
        self.assertEqual(0, empty["through_id"])
        new = self.seed_messages(key, 103)
        page = self.page(self.b, key, after_id=0, limit=100)
        self.assertEqual(new[:100], [row["id"] for row in page["items"]])
        self.assertEqual(since, page["history_since"])
        self.run_service(groups.leave_group, self.user(self.b), group["id"])
        with self.assertRaises(SocialError) as refusal:
            self.page(self.b, key, after_id=page["next_after_id"], through_id=page["through_id"])
        self.assertEqual(403, refusal.exception.status_code)

    def test_recalled_body_stays_redacted_during_forward_recovery(self):
        ids = self.seed_messages(self.key, 2)
        self.run_service(messages.recall_message, self.user(self.a), ids[-1])
        page = self.page(self.b, after_id=ids[0])
        self.assertEqual(1, len(page["items"]))
        self.assertTrue(page["items"][0]["recalled"])
        self.assertEqual("", page["items"][0]["body"])
        self.assertEqual([], page["items"][0]["media"])

    def test_invalid_cursor_combinations_are_local_400_and_http_bounds_are_enforced(self):
        for options in ({"before_id": 2, "after_id": 0}, {"through_id": 2},
                        {"after_id": -1}, {"after_id": 3, "through_id": 2}):
            with self.subTest(options=options), self.assertRaises(SocialError) as refusal:
                self.page(self.b, **options)
            self.assertEqual(400, refusal.exception.status_code)
        client = self.client_as(self.b)
        url = f"/api/social/conversations/{self.key}/messages"
        for query in ("after_id=-1", "after_id=9223372036854775808", "after_id=0&through_id=0"):
            self.assertEqual(422, client.get(url + "?" + query).status_code)
