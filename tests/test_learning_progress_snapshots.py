from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
from classroom_app.services import learning_progress_service as service


def _metrics(score: float, *, material: float = 0, task: float = 0, interaction: float = 0, consistency: float = 0) -> dict:
    return {
        "score": score,
        "components": {
            "material": material,
            "task": task,
            "interaction": interaction,
            "consistency": consistency,
        },
        "material": {
            "required_count": 2,
            "completed_count": 1,
            "ratio": 0.5,
            "items": [
                {"id": 10, "name": "第一章", "unit_ratio": 1, "percent": 100},
                {"id": 11, "name": "第二章", "unit_ratio": 0.25, "percent": 25},
            ],
        },
        "assignments": {
            "assignment_count": 1,
            "submitted_count": 0,
            "graded_count": 0,
            "completion_ratio": 0,
            "score_ratio": 0,
            "task_ratio": 0,
            "items": [{"id": 20, "title": "课后练习", "submitted": False}],
        },
        "interactions": {
            "interaction_ratio": 0.2,
            "consistency_ratio": 0.1,
        },
    }


class LearningProgressSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_cultivation_progress_schema(self.conn, engine="sqlite")
        self.conn.execute(
            """
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                course_name TEXT,
                course_sect_name TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                name TEXT,
                student_id_number TEXT,
                enrollment_status TEXT DEFAULT 'active'
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE learning_certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                level_key TEXT,
                level_name TEXT,
                tier INTEGER,
                title TEXT,
                certificate_code TEXT,
                issued_at TEXT,
                revealed_at TEXT,
                metadata_json TEXT DEFAULT '{}'
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                assignment_id INTEGER,
                exam_paper_id TEXT,
                status TEXT,
                score REAL,
                generated_at TEXT,
                submitted_at TEXT,
                graded_at TEXT,
                passed_at TEXT,
                ai_error TEXT,
                metadata_json TEXT DEFAULT '{}'
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE learning_stage_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                status TEXT,
                progress_score REAL,
                readiness_score REAL,
                unlocked_at TEXT,
                passed_at TEXT,
                last_exam_assignment_id INTEGER,
                certificate_id INTEGER,
                last_calculated_at TEXT,
                metadata_json TEXT DEFAULT '{}',
                UNIQUE (class_offering_id, student_id, stage_key)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE message_center_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_identity TEXT NOT NULL,
                recipient_role TEXT NOT NULL,
                recipient_user_pk INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'normal',
                actor_identity TEXT DEFAULT '',
                actor_role TEXT DEFAULT '',
                actor_user_pk INTEGER,
                actor_display_name TEXT DEFAULT '',
                title TEXT NOT NULL,
                body_preview TEXT DEFAULT '',
                link_url TEXT DEFAULT '',
                class_offering_id INTEGER,
                ref_type TEXT DEFAULT '',
                ref_id TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                email_status TEXT NOT NULL DEFAULT 'not_required',
                email_job_id INTEGER,
                email_queued_at TEXT,
                email_sent_at TEXT,
                read_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_get_student_learning_state_reads_clean_snapshot_without_recalculation(self):
        metrics = _metrics(12.3, material=8, task=2, interaction=1, consistency=1.3)
        self.conn.execute(
            """
            INSERT INTO learning_progress_snapshots (
                class_offering_id, student_id, score, progress_percent,
                components_json, metrics_json, level_key, calculated_at, dirty
            )
            VALUES (1, 2, 12.3, 68, ?, ?, 'mortal', '2026-06-12T08:00:00', 0)
            """,
            (json.dumps(metrics["components"]), json.dumps(metrics)),
        )

        with patch.object(service, "_build_learning_metrics", side_effect=AssertionError("should not recalculate")):
            state = service.get_student_learning_state(self.conn, 1, 2)

        self.assertEqual(12.3, state["score"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM learning_stage_status").fetchone()[0])

    def test_refresh_student_learning_state_writes_snapshot_and_score_events(self):
        with patch.object(service, "_build_learning_metrics", return_value=_metrics(10, material=6, task=4)):
            state = service.refresh_student_learning_state(self.conn, 1, 2, event_source_ref="material:11")

        self.assertEqual(10, state["score"])
        snapshot = self.conn.execute(
            "SELECT score, dirty, components_json FROM learning_progress_snapshots WHERE class_offering_id = 1 AND student_id = 2"
        ).fetchone()
        self.assertEqual(10, snapshot["score"])
        self.assertEqual(0, snapshot["dirty"])
        events = [
            dict(row)
            for row in self.conn.execute(
                "SELECT event_type, component, delta FROM cultivation_score_events ORDER BY id"
            ).fetchall()
        ]
        self.assertEqual(
            [
                {"event_type": "material_progress", "component": "material", "delta": 6.0},
                {"event_type": "task_progress", "component": "task", "delta": 4.0},
            ],
            events,
        )

    def test_build_score_opportunities_prioritizes_actionable_gains(self):
        opportunities = service.build_score_opportunities(
            _metrics(20, material=11.2, task=0, interaction=3, consistency=0.5),
            {"key": "foundation", "short_name": "筑基", "unlock_score": 32, "status": "available"},
            class_offering_id=9,
        )

        self.assertGreaterEqual(len(opportunities), 3)
        self.assertEqual("assignment", opportunities[0]["type"])
        self.assertTrue(opportunities[0]["action_url"].endswith("/20"))
        self.assertTrue(all("estimated_delta" in item for item in opportunities))

    def test_certificate_reveal_state_is_server_tracked_and_idempotent(self):
        metrics = _metrics(24, material=18, task=4, interaction=1, consistency=1)
        self.conn.execute(
            """
            INSERT INTO learning_progress_snapshots (
                class_offering_id, student_id, score, progress_percent,
                components_json, metrics_json, level_key, calculated_at, dirty
            )
            VALUES (1, 2, 24, 80, ?, ?, 'qi_awakening', '2026-06-12T08:00:00', 0)
            """,
            (json.dumps(metrics["components"]), json.dumps(metrics)),
        )
        self.conn.execute(
            """
            INSERT INTO learning_certificates (
                id, class_offering_id, student_id, stage_key, level_key, level_name,
                tier, title, certificate_code, issued_at, revealed_at, metadata_json
            )
            VALUES (7, 1, 2, 'qi_awakening', 'qi_awakening', '启蒙入门',
                    1, '启蒙道印', 'CERT-7', '2026-06-12T09:00:00', NULL, '{}')
            """
        )
        self.conn.execute(
            """
            INSERT INTO learning_certificates (
                id, class_offering_id, student_id, stage_key, level_key, level_name,
                tier, title, certificate_code, issued_at, revealed_at, metadata_json
            )
            VALUES (8, 1, 3, 'qi_awakening', 'qi_awakening', '启蒙入门',
                    1, '启蒙道印', 'CERT-8', '2026-06-12T09:00:00', NULL, '{}')
            """
        )

        before = service.get_student_learning_state(self.conn, 1, 2)
        self.assertEqual(7, before["latest_unrevealed_certificate"]["id"])
        self.assertTrue(before["latest_unrevealed_certificate"]["needs_reveal"])

        first_mark = service.mark_learning_certificate_revealed(self.conn, 7, 2)
        second_mark = service.mark_learning_certificate_revealed(self.conn, 7, 2)
        denied_mark = service.mark_learning_certificate_revealed(self.conn, 7, 3)
        after = service.get_student_learning_state(self.conn, 1, 2)

        self.assertIsNotNone(first_mark)
        self.assertEqual(first_mark["revealed_at"], second_mark["revealed_at"])
        self.assertIsNone(denied_mark)
        self.assertIsNone(after["latest_unrevealed_certificate"])
        stored = self.conn.execute(
            "SELECT revealed_at FROM learning_certificates WHERE id = 7"
        ).fetchone()
        self.assertEqual(first_mark["revealed_at"], stored["revealed_at"])

    def test_capture_weekly_snapshots_is_idempotent_and_updates_same_week(self):
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, course_name) VALUES (1, 10, 20, 30, 'Course')"
        )
        self.conn.executemany(
            "INSERT INTO students (id, class_id, name, student_id_number, enrollment_status) VALUES (?, 10, ?, ?, 'active')",
            [(2, "Alice", "S002"), (3, "Bob", "S003")],
        )
        for student_id, score in ((2, 12.0), (3, 15.0)):
            metrics = _metrics(score, material=score, task=0, interaction=0, consistency=0)
            self.conn.execute(
                """
                INSERT INTO learning_progress_snapshots (
                    class_offering_id, student_id, score, progress_percent,
                    components_json, metrics_json, level_key, calculated_at, dirty
                )
                VALUES (1, ?, ?, 40, ?, ?, 'mortal', '2026-06-10T08:00:00', 0)
                """,
                (student_id, score, json.dumps(metrics["components"]), json.dumps(metrics)),
            )

        first = service.capture_cultivation_weekly_snapshots(
            self.conn,
            class_offering_id=1,
            week_start="2026-06-10",
            refresh_current=False,
        )
        self.conn.execute(
            """
            UPDATE learning_progress_snapshots
            SET score = 16.5, components_json = ?
            WHERE class_offering_id = 1 AND student_id = 3
            """,
            (json.dumps({"material": 16.5, "task": 0, "interaction": 0, "consistency": 0}),),
        )
        second = service.capture_cultivation_weekly_snapshots(
            self.conn,
            class_offering_id=1,
            week_start="2026-06-10",
            refresh_current=False,
        )

        self.assertEqual("2026-06-08", first["week_start"])
        self.assertEqual(2, first["captured"])
        self.assertEqual(2, second["captured"])
        rows = self.conn.execute(
            """
            SELECT student_id, score, week_start
            FROM cultivation_weekly_snapshots
            ORDER BY student_id
            """
        ).fetchall()
        self.assertEqual(2, len(rows))
        self.assertEqual(16.5, rows[1]["score"])
        self.assertEqual("2026-06-08", rows[0]["week_start"])

    def test_weekly_trends_cover_student_and_class_aggregates(self):
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, course_name) VALUES (1, 10, 20, 30, 'Course')"
        )
        self.conn.executemany(
            "INSERT INTO students (id, class_id, name, student_id_number, enrollment_status) VALUES (?, 10, ?, ?, 'active')",
            [(2, "Alice", "S002"), (3, "Bob", "S003")],
        )
        rows = [
            (2, "2026-05-25", 10.0, {"material": 6, "task": 2, "interaction": 1, "consistency": 1}),
            (2, "2026-06-01", 12.0, {"material": 7, "task": 3, "interaction": 1, "consistency": 1}),
            (2, "2026-06-08", 12.0, {"material": 7, "task": 3, "interaction": 1, "consistency": 1}),
            (3, "2026-05-25", 20.0, {"material": 12, "task": 5, "interaction": 2, "consistency": 1}),
            (3, "2026-06-01", 20.0, {"material": 12, "task": 5, "interaction": 2, "consistency": 1}),
            (3, "2026-06-08", 21.0, {"material": 12, "task": 6, "interaction": 2, "consistency": 1}),
        ]
        self.conn.executemany(
            """
            INSERT INTO cultivation_weekly_snapshots (
                class_offering_id, student_id, week_start, week_end, score,
                progress_percent, components_json, level_key, snapshot_source, created_at
            )
            VALUES (1, ?, ?, date(?, '+6 days'), ?, 50, ?, 'mortal', 'test', '2026-06-12T08:00:00')
            """,
            [(student_id, week, week, score, json.dumps(components)) for student_id, week, score, components in rows],
        )

        student_trend = service.build_student_cultivation_growth_trend(self.conn, 1, 2, weeks=8)
        class_trend = service.build_class_cultivation_trend_summary(self.conn, 1, weeks=8)

        self.assertEqual(3, student_trend["weeks"])
        self.assertTrue(student_trend["has_enough_data"])
        self.assertEqual(2.0, student_trend["total_delta"])
        self.assertEqual(3, class_trend["weeks"])
        self.assertEqual(0.5, class_trend["average_delta"])
        self.assertEqual("Alice", class_trend["stalled_students"][0]["name"])
        self.assertTrue(class_trend["sparkline"]["points"])

    def test_create_weekly_reports_is_idempotent_and_uses_snapshot_delta(self):
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, course_name) VALUES (1, 10, 20, 30, 'Course')"
        )
        self.conn.executemany(
            "INSERT INTO students (id, class_id, name, student_id_number, enrollment_status) VALUES (?, 10, ?, ?, 'active')",
            [(2, "Alice", "S002"), (3, "Bob", "S003")],
        )
        rows = [
            (2, "2026-06-01", 10.0, {"material": 6.0, "task": 2.0, "interaction": 1.0, "consistency": 1.0}),
            (2, "2026-06-08", 13.6, {"material": 8.0, "task": 3.0, "interaction": 1.5, "consistency": 1.1}),
            (3, "2026-06-01", 20.0, {"material": 12.0, "task": 5.0, "interaction": 2.0, "consistency": 1.0}),
            (3, "2026-06-08", 20.0, {"material": 12.0, "task": 5.0, "interaction": 2.0, "consistency": 1.0}),
        ]
        self.conn.executemany(
            """
            INSERT INTO cultivation_weekly_snapshots (
                class_offering_id, student_id, week_start, week_end, score,
                progress_percent, components_json, level_key, snapshot_source, created_at
            )
            VALUES (1, ?, ?, date(?, '+6 days'), ?, 50, ?, 'mortal', 'test', '2026-06-12T08:00:00')
            """,
            [(student_id, week, week, score, json.dumps(components)) for student_id, week, score, components in rows],
        )
        self.conn.execute(
            """
            INSERT INTO cultivation_score_events (
                class_offering_id, student_id, event_type, delta, component, source_ref, metadata_json, created_at
            )
            VALUES (1, 2, 'task_progress', 1.2, 'task', 'assignment:44', '{}', '2026-06-10T12:00:00')
            """
        )

        with patch(
            "classroom_app.services.message_center_service.queue_notification_email_if_applicable",
            return_value=False,
        ), patch(
            "classroom_app.services.message_center_service._load_student_support_profile",
            return_value="",
        ):
            first = service.create_cultivation_weekly_reports(
                self.conn,
                class_offering_id=1,
                week_start="2026-06-08",
            )
            second = service.create_cultivation_weekly_reports(
                self.conn,
                class_offering_id=1,
                week_start="2026-06-08",
            )

        self.assertEqual(2, first["checked"])
        self.assertEqual(1, first["created"])
        self.assertEqual(1, first["skipped"])
        self.assertEqual(0, first["duplicates"])
        self.assertEqual(0, second["created"])
        self.assertEqual(1, second["duplicates"])
        notifications = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT recipient_user_pk, ref_id, metadata_json, body_preview
                FROM message_center_notifications
                ORDER BY id
                """
            ).fetchall()
        ]
        self.assertEqual(1, len(notifications))
        self.assertEqual(2, notifications[0]["recipient_user_pk"])
        self.assertEqual("cultivation-weekly-report:1:2:2026-06-08", notifications[0]["ref_id"])
        metadata = json.loads(notifications[0]["metadata_json"])
        self.assertEqual("2026-06-08", metadata["week_start"])
        self.assertEqual("2026-06-14", metadata["week_end"])
        self.assertEqual(3.6, metadata["score_delta"])
        self.assertEqual(13.6, metadata["current_score"])
        self.assertEqual(1, metadata["event_count"])
        self.assertIn("+3.6", notifications[0]["body_preview"])

    def test_archive_cultivation_score_events_rolls_up_old_rows_only(self):
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, course_name) VALUES (1, 10, 20, 30, 'Course')"
        )
        self.conn.execute(
            "INSERT INTO students (id, class_id, name, student_id_number, enrollment_status) VALUES (2, 10, 'Alice', 'S002', 'active')"
        )
        self.conn.executemany(
            """
            INSERT INTO cultivation_score_events (
                class_offering_id, student_id, event_type, delta, component, source_ref, metadata_json, created_at
            )
            VALUES (1, 2, ?, ?, ?, ?, '{}', ?)
            """,
            [
                ("material_progress", 1.2, "material", "material:1", "2026-02-01T08:00:00"),
                ("material_progress", 0.5, "material", "material:2", "2026-02-17T09:00:00"),
                ("task_progress", 2.0, "task", "assignment:3", "2026-05-01T08:00:00"),
            ],
        )

        first = service.archive_cultivation_score_events(
            self.conn,
            retention_days=90,
            as_of="2026-06-12T00:00:00",
        )
        second = service.archive_cultivation_score_events(
            self.conn,
            retention_days=90,
            as_of="2026-06-12T00:00:00",
        )

        self.assertEqual(1, first["archive_rows"])
        self.assertEqual(2, first["archived_events"])
        self.assertEqual(2, first["deleted_events"])
        self.assertEqual(0, second["archived_events"])
        archive = self.conn.execute(
            """
            SELECT archive_month, event_type, component, event_count, total_delta, first_event_at, last_event_at
            FROM cultivation_score_event_archives
            """
        ).fetchone()
        self.assertEqual("2026-02", archive["archive_month"])
        self.assertEqual("material_progress", archive["event_type"])
        self.assertEqual("material", archive["component"])
        self.assertEqual(2, archive["event_count"])
        self.assertAlmostEqual(1.7, archive["total_delta"])
        self.assertEqual("2026-02-01T08:00:00", archive["first_event_at"])
        self.assertEqual("2026-02-17T09:00:00", archive["last_event_at"])
        remaining = self.conn.execute("SELECT event_type, created_at FROM cultivation_score_events").fetchall()
        self.assertEqual(1, len(remaining))
        self.assertEqual("task_progress", remaining[0]["event_type"])


if __name__ == "__main__":
    unittest.main()
