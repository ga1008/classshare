from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta

from classroom_app.services.ai_usage_budget_service import (
    AIUsageBudgetError,
    build_ai_usage_dashboard,
    count_stage_exam_generations_last_24h,
    ensure_stage_exam_generation_quota,
    mark_ai_usage_budget_overage_if_needed,
    save_offering_ai_budget_config,
    should_defer_low_priority_ai_task,
)


class AIUsageBudgetServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT,
                email TEXT,
                is_super_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                ai_weekly_budget_json TEXT NOT NULL DEFAULT '',
                ai_weekly_budget_updated_at TEXT
            );
            CREATE TABLE ai_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'P1',
                endpoint TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'unknown',
                status_code INTEGER,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                prompt_tokens_estimate INTEGER NOT NULL DEFAULT 0,
                completion_tokens_estimate INTEGER NOT NULL DEFAULT 0,
                class_offering_id INTEGER,
                student_id INTEGER,
                teacher_id INTEGER,
                source_ref TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                student_id INTEGER,
                stage_key TEXT,
                status TEXT,
                generated_at TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE message_center_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_identity TEXT,
                recipient_role TEXT,
                recipient_user_pk INTEGER,
                category TEXT,
                severity TEXT,
                actor_identity TEXT,
                actor_role TEXT,
                actor_user_pk INTEGER,
                actor_display_name TEXT,
                title TEXT,
                body_preview TEXT,
                link_url TEXT,
                class_offering_id INTEGER,
                ref_type TEXT,
                ref_id TEXT,
                metadata_json TEXT,
                created_at TEXT,
                read_at TEXT
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO teachers (id, name, email, is_super_admin, is_active) VALUES (?, ?, ?, ?, 1)",
            [
                (1, "授课教师", "teacher@example.test", 0),
                (2, "超管", "admin@example.test", 1),
            ],
        )
        self.conn.execute("INSERT INTO classes (id, name) VALUES (1, 'P03')")
        self.conn.execute("INSERT INTO courses (id, name) VALUES (1, '综合英语')")
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (1, 1, 1, 1)"
        )

    def tearDown(self):
        self.conn.close()

    def _insert_usage(
        self,
        *,
        task_type: str,
        status: str = "success",
        created_at: datetime | None = None,
        class_offering_id: int = 1,
        student_id: int | None = None,
        source_ref: str = "",
        metadata: dict | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO ai_usage_log (
                task_type, priority, endpoint, status, duration_ms,
                prompt_tokens_estimate, completion_tokens_estimate,
                class_offering_id, student_id, teacher_id, source_ref, metadata_json, created_at
            )
            VALUES (?, 'P0', '/api/ai/test', ?, 1200, 100, 40, ?, ?, 1, ?, ?, ?)
            """,
            (
                task_type,
                status,
                class_offering_id,
                student_id,
                source_ref,
                json.dumps(metadata or {}, ensure_ascii=False),
                (created_at or datetime.now()).replace(microsecond=0).isoformat(),
            ),
        )
        return int(cursor.lastrowid)

    def test_dashboard_aggregates_tasks_weeks_and_offering_budget(self):
        save_offering_ai_budget_config(self.conn, 1, {"stage_exam_generation": 2})
        self._insert_usage(task_type="stage_exam_generation", student_id=11)
        self._insert_usage(task_type="stage_exam_generation", student_id=12)
        self._insert_usage(task_type="stage_exam_generation", student_id=13)
        self._insert_usage(task_type="behavior_profile", created_at=datetime.now() - timedelta(days=7))

        dashboard = build_ai_usage_dashboard(self.conn)

        self.assertEqual(4, dashboard["summary"]["count"])
        task_counts = {item["key"]: item["count"] for item in dashboard["task_items"]}
        self.assertEqual(3, task_counts["stage_exam_generation"])
        offering = dashboard["offering_items"][0]
        self.assertEqual("综合英语", offering["course_name"])
        self.assertEqual(3, offering["stage_exam_generation_this_week"])
        self.assertEqual(2, offering["stage_exam_generation_budget"])
        self.assertTrue(offering["over_stage_exam_budget"])

    def test_stage_exam_generation_quota_counts_deleted_attempt_logs(self):
        now = datetime.now().replace(microsecond=0)
        self.conn.executemany(
            """
            INSERT INTO learning_stage_exam_attempts (
                id, class_offering_id, student_id, stage_key, status, generated_at
            )
            VALUES (?, 1, 42, 'foundation', 'generated', ?)
            """,
            [(1, now.isoformat()), (2, now.isoformat())],
        )
        self._insert_usage(
            task_type="stage_exam_generation",
            student_id=42,
            source_ref="stage-exam:1",
            metadata={"stage_key": "foundation"},
        )
        self._insert_usage(
            task_type="stage_exam_generation",
            student_id=42,
            source_ref="stage-exam:999",
            metadata={"stage_key": "foundation"},
        )

        count = count_stage_exam_generations_last_24h(
            self.conn,
            class_offering_id=1,
            student_id=42,
            stage_key="foundation",
        )

        self.assertEqual(3, count)
        with self.assertRaises(AIUsageBudgetError):
            ensure_stage_exam_generation_quota(
                self.conn,
                class_offering_id=1,
                student_id=42,
                stage_key="foundation",
            )

    def test_budget_overage_marks_usage_and_notifies_once(self):
        save_offering_ai_budget_config(self.conn, 1, {"stage_exam_generation": 1, "total": 100})
        self._insert_usage(task_type="stage_exam_generation")
        usage_id = self._insert_usage(task_type="stage_exam_generation")

        result = mark_ai_usage_budget_overage_if_needed(
            self.conn,
            usage_log_id=usage_id,
            class_offering_id=1,
            task_type="stage_exam_generation",
            priority="P0",
        )
        duplicate = mark_ai_usage_budget_overage_if_needed(
            self.conn,
            usage_log_id=usage_id,
            class_offering_id=1,
            task_type="stage_exam_generation",
            priority="P0",
        )

        self.assertTrue(result["over_budget"])
        self.assertGreaterEqual(result["notification_count"], 1)
        self.assertEqual(0, duplicate["notification_count"])
        row = self.conn.execute("SELECT metadata_json FROM ai_usage_log WHERE id = ?", (usage_id,)).fetchone()
        metadata = json.loads(row["metadata_json"])
        self.assertTrue(metadata["budget_overage"])
        notification_count = self.conn.execute("SELECT COUNT(*) AS count FROM message_center_notifications").fetchone()
        self.assertGreaterEqual(int(notification_count["count"]), 1)

    def test_low_priority_task_defers_when_task_budget_is_used_up(self):
        save_offering_ai_budget_config(self.conn, 1, {"behavior_profile": 1})
        self._insert_usage(task_type="behavior_profile")

        self.assertTrue(
            should_defer_low_priority_ai_task(
                self.conn,
                class_offering_id=1,
                task_type="behavior_profile",
            )
        )


if __name__ == "__main__":
    unittest.main()
