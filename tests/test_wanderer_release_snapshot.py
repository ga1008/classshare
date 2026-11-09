"""Release observations cannot mutate the fixture or expose private text."""
import json
import sqlite3

from tests.wanderer_test_support import WandererDbTestCase
from tools.wanderer_release_snapshot import collect_snapshot


class ReadOnlySnapshotTest(WandererDbTestCase):
    def snapshot(self, conn):
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        class Adapter:
            def execute(self, sql, params=()):
                # Production uses psycopg, with identical business SELECTs.
                if not sql.lstrip().startswith("SELECT "):
                    raise AssertionError("Snapshot attempted a non-SELECT")
                return conn.execute(sql.replace("%s", "?"), params)
        conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in (
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
        ) else sqlite3.SQLITE_DENY)
        try:
            return collect_snapshot(Adapter(), since_local="2098-01-01 00:00:00", present_tables=present)
        finally:
            conn.set_authorizer(None)

    def test_private_message_is_counted_without_body_or_identity_and_without_writes(self):
        with self.connect() as conn:
            agent = self.wanderer(conn, "lele")
            identity = f"student:{agent['student_id']}"
            conn.execute("INSERT INTO private_messages(conversation_key,sender_identity,sender_role,sender_user_pk,"
                         "sender_display_name,recipient_identity,recipient_role,recipient_user_pk,recipient_display_name,"
                         "content,created_at) VALUES(?,?,'student',?,'Secret name','student:99999','student',99999,"
                         "'Private recipient','PRIVATE_BODY_MUST_NEVER_APPEAR','2099-01-01T01:02:03')",
                         ("private-secret-key", identity, agent['student_id']))
            conn.commit()
            before = conn.total_changes
            report = self.snapshot(conn)
            self.assertEqual(conn.total_changes, before)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["natural_activity_since"]["legacy_dm"]["ai_messages"], 1)
            serialized = json.dumps(report)
            for private_value in (identity, "PRIVATE_BODY_MUST_NEVER_APPEAR", "private-secret-key", "Secret name", "Private recipient"):
                self.assertNotIn(private_value, serialized)

    def test_account_control_regression_is_reported_as_failed_invariant(self):
        with self.connect() as conn:
            agent = self.wanderer(conn, "lele")
            conn.execute("UPDATE students SET control_mode='human' WHERE id=?", (agent['student_id'],))
            conn.commit()
            try:
                report = self.snapshot(conn)
                self.assertFalse(report["ok"])
                self.assertEqual(report["invariants"]["wanderer_account_not_ai"], 1)
            finally:
                conn.execute("UPDATE students SET control_mode='ai' WHERE id=?", (agent['student_id'],))
                conn.commit()
