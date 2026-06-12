from __future__ import annotations

import sqlite3
import unittest

from classroom_app.services.learning_progress_service import _load_interaction_metrics
from classroom_app.services.psych_profile_service import normalize_psych_profile_payload


def _build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE chat_logs (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            user_role TEXT,
            user_id TEXT,
            message TEXT,
            created_at TEXT
        );
        CREATE TABLE classroom_behavior_events (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            user_pk INTEGER,
            user_role TEXT,
            action_type TEXT,
            payload_json TEXT,
            created_at TEXT
        );
        CREATE TABLE classroom_behavior_states (
            class_offering_id INTEGER,
            user_pk INTEGER,
            user_role TEXT,
            total_activity_count INTEGER DEFAULT 0,
            online_accumulated_seconds INTEGER DEFAULT 0,
            focus_total_seconds INTEGER DEFAULT 0,
            visible_total_seconds INTEGER DEFAULT 0,
            discussion_lurk_total_seconds INTEGER DEFAULT 0,
            ai_panel_open_total_seconds INTEGER DEFAULT 0
        );
        CREATE TABLE private_messages (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            sender_identity TEXT,
            recipient_role TEXT,
            created_at TEXT
        );
        CREATE TABLE classroom_behavior_profiles (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            user_pk INTEGER,
            user_role TEXT,
            interaction_quality REAL,
            interaction_quality_label TEXT,
            interaction_quality_reason TEXT,
            created_at TEXT
        );
        CREATE TABLE learning_certificates (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER,
            student_id INTEGER,
            tier INTEGER
        );
        """
    )
    return conn


def _seed_full_interaction_counts(conn: sqlite3.Connection) -> None:
    for idx in range(12):
        message = "@AI how should I connect this to the course?" if idx < 4 else "course discussion note"
        conn.execute(
            """
            INSERT INTO chat_logs (class_offering_id, user_role, user_id, message, created_at)
            VALUES (1, 'student', '7', ?, ?)
            """,
            (message, f"2026-06-{idx % 5 + 1:02d}T09:00:00"),
        )
    for idx in range(8):
        conn.execute(
            """
            INSERT INTO classroom_behavior_events (class_offering_id, user_pk, user_role, action_type, created_at)
            VALUES (1, 7, 'student', 'ai_question', ?)
            """,
            (f"2026-06-{idx % 5 + 1:02d}T10:00:00",),
        )
    for idx in range(3):
        conn.execute(
            """
            INSERT INTO private_messages (class_offering_id, sender_identity, recipient_role, created_at)
            VALUES (1, 'student:7', 'teacher', ?)
            """,
            (f"2026-06-0{idx + 1}T11:00:00",),
        )
    conn.execute(
        """
        INSERT INTO classroom_behavior_states (
            class_offering_id, user_pk, user_role, total_activity_count,
            online_accumulated_seconds, focus_total_seconds
        )
        VALUES (1, 7, 'student', 80, 7200, 5400)
        """
    )
    conn.commit()


class InteractionQualityScoringTests(unittest.TestCase):
    def test_default_profile_uses_085_factor(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        _seed_full_interaction_counts(conn)

        metrics = _load_interaction_metrics(conn, 1, 7)

        self.assertAlmostEqual(metrics["base_interaction_ratio"], 1.0)
        self.assertEqual(metrics["interaction_quality_source"], "default")
        self.assertAlmostEqual(metrics["interaction_quality"], 0.7)
        self.assertAlmostEqual(metrics["interaction_quality_factor"], 0.85)
        self.assertAlmostEqual(metrics["interaction_ratio"], 0.85)

    def test_profile_quality_floats_interaction_score_inside_same_weight_bucket(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        _seed_full_interaction_counts(conn)
        conn.execute(
            """
            INSERT INTO classroom_behavior_profiles (
                class_offering_id, user_pk, user_role, interaction_quality,
                interaction_quality_label, interaction_quality_reason, created_at
            )
            VALUES (1, 7, 'student', 1.0, 'high', 'specific course questions', '2026-06-08T09:00:00')
            """
        )
        high_quality = _load_interaction_metrics(conn, 1, 7)
        self.assertAlmostEqual(high_quality["interaction_ratio"], 1.0)
        self.assertAlmostEqual(high_quality["interaction_ratio"] * 15, 15.0)

        conn.execute(
            "UPDATE classroom_behavior_profiles SET interaction_quality = 0.2, interaction_quality_label = 'low'",
        )
        low_quality = _load_interaction_metrics(conn, 1, 7)
        self.assertAlmostEqual(low_quality["interaction_quality_factor"], 0.6)
        self.assertAlmostEqual(low_quality["interaction_ratio"], 0.6)
        self.assertAlmostEqual(low_quality["interaction_ratio"] * 15, 9.0)

    def test_peer_help_counts_as_question_units_and_high_tier_doubles_units(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        conn.execute(
            """
            INSERT INTO classroom_behavior_states (class_offering_id, user_pk, user_role)
            VALUES (1, 7, 'student')
            """
        )
        conn.execute(
            """
            INSERT INTO classroom_behavior_events (class_offering_id, user_pk, user_role, action_type, created_at)
            VALUES (1, 7, 'student', 'peer_help', '2026-06-09T09:00:00')
            """
        )
        low_tier = _load_interaction_metrics(conn, 1, 7)
        self.assertEqual(low_tier["peer_help_count"], 1)
        self.assertEqual(low_tier["peer_help_units"], 1)
        self.assertAlmostEqual(low_tier["base_interaction_ratio"], 0.34 / 8)

        conn.execute(
            "INSERT INTO learning_certificates (class_offering_id, student_id, tier) VALUES (1, 7, 6)"
        )
        high_tier = _load_interaction_metrics(conn, 1, 7)
        self.assertEqual(high_tier["peer_help_multiplier"], 2)
        self.assertEqual(high_tier["peer_help_units"], 2)
        self.assertAlmostEqual(high_tier["base_interaction_ratio"], low_tier["base_interaction_ratio"] * 2)

    def test_profile_payload_normalizes_interaction_quality(self) -> None:
        normalized = normalize_psych_profile_payload(
            {
                "user_profile_summary": "steady",
                "interaction_quality_score": 85,
                "interaction_quality_reason": "asks concrete questions",
            }
        )
        self.assertAlmostEqual(normalized["interaction_quality"], 0.85)
        self.assertEqual(normalized["interaction_quality_label"], "high")
        self.assertEqual(normalized["interaction_quality_reason"], "asks concrete questions")

        invalid = normalize_psych_profile_payload({"interaction_quality": "not-a-number"})
        self.assertIsNone(invalid["interaction_quality"])
        self.assertEqual(invalid["interaction_quality_label"], "")


if __name__ == "__main__":
    unittest.main()
