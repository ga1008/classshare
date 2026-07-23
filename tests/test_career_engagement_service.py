"""Privacy and idempotency checks for the career/resume product funnel."""

from __future__ import annotations

import json
import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

import classroom_app.db.schema_career_engagement as schema_mod
from classroom_app.services.career_engagement_service import (
    record_student_career_event,
    record_student_career_event_safely,
    sanitize_event_context,
)


class CareerEngagementTests(unittest.TestCase):
    def setUp(self):
        schema_mod._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_context_keeps_only_non_sensitive_funnel_fields(self):
        cleaned = sanitize_event_context({
            "resume_id": 7,
            "career_tag": "A1",
            "target_position": "后端开发工程师",
            "email": "student@example.com",
            "id_card": "secret",
            "resume_text": "private body",
        })
        self.assertEqual(cleaned["resume_id"], 7)
        self.assertEqual(cleaned["career_tag"], "A1")
        self.assertNotIn("email", cleaned)
        self.assertNotIn("id_card", cleaned)
        self.assertNotIn("resume_text", cleaned)

    def test_client_retry_is_deduplicated(self):
        first = record_student_career_event(
            self.conn, 5, surface="resume", event_name="resume_home_viewed",
            context={"mode": "result_first"}, client_event_id="evt-client-0001",
        )
        second = record_student_career_event(
            self.conn, 5, surface="resume", event_name="resume_home_viewed",
            context={"mode": "result_first"}, client_event_id="evt-client-0001",
        )
        self.assertTrue(first)
        self.assertFalse(second)
        row = self.conn.execute("SELECT context_json FROM student_career_events").fetchone()
        self.assertEqual(json.loads(row["context_json"]), {"mode": "result_first"})

    def test_unknown_event_is_rejected(self):
        with self.assertRaises(ValueError):
            record_student_career_event(
                self.conn, 1, surface="resume", event_name="resume_private_text_captured",
            )

    def test_safe_tracking_failure_preserves_business_transaction(self):
        record_student_career_event(
            self.conn, 1, surface="resume", event_name="resume_home_viewed",
        )
        self.conn.execute("DELETE FROM student_career_events")
        self.conn.execute("CREATE TABLE business_records (value TEXT NOT NULL)")
        self.conn.execute(
            """
            CREATE TRIGGER fail_career_event BEFORE INSERT ON student_career_events
            BEGIN
                SELECT RAISE(ABORT, 'simulated analytics outage');
            END
            """
        )

        statements = []

        class RecordingConnection:
            def execute(inner_self, sql, params=()):
                statements.append(sql.strip().upper())
                return self.conn.execute(sql, params)

        recorded = record_student_career_event_safely(
            RecordingConnection(), 1, surface="resume", event_name="resume_created",
        )
        self.assertFalse(recorded)
        self.assertTrue(any(sql.startswith("ROLLBACK TO SAVEPOINT") for sql in statements))
        self.assertTrue(any(sql.startswith("RELEASE SAVEPOINT") for sql in statements))

        self.conn.execute("INSERT INTO business_records (value) VALUES (?)", ("saved",))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT value FROM business_records").fetchone()["value"],
            "saved",
        )


if __name__ == "__main__":
    unittest.main()
