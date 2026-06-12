from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
from classroom_app.services import learning_progress_service as service
from classroom_app.services.material_mastery_check_service import build_material_mastery_check_payload


def _ready_check_payload() -> str:
    return json.dumps(
        {
            "version": "material_mastery_check_v1",
            "status": "ready",
            "pass_count": 2,
            "questions": [
                {
                    "id": "q1",
                    "type": "single_choice",
                    "prompt": "核心概念是什么？",
                    "options": [{"id": "A", "text": "数组边界"}, {"id": "B", "text": "只看页码"}],
                    "answer": "A",
                    "explanation": "材料围绕数组边界展开。",
                },
                {
                    "id": "q2",
                    "type": "single_choice",
                    "prompt": "应该如何应用？",
                    "options": [{"id": "A", "text": "等待答案"}, {"id": "B", "text": "结合代码证据"}],
                    "answer": "B",
                    "explanation": "需要结合代码证据说明。",
                },
                {
                    "id": "q3",
                    "type": "single_choice",
                    "prompt": "读完后怎样复核？",
                    "options": [{"id": "A", "text": "跳过例子"}, {"id": "C", "text": "复述关键风险"}],
                    "answer": "C",
                    "explanation": "复述关键风险才算掌握。",
                },
            ],
        },
        ensure_ascii=False,
    )


class MaterialMasteryCheckTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                home_learning_material_id INTEGER
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT,
                school_code TEXT DEFAULT '',
                school_name TEXT DEFAULT '',
                college TEXT DEFAULT '',
                department TEXT DEFAULT ''
            );
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT,
                school_code TEXT DEFAULT '',
                school_name TEXT DEFAULT '',
                college TEXT DEFAULT '',
                department TEXT DEFAULT ''
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                name TEXT,
                school_code TEXT DEFAULT '',
                school_name TEXT DEFAULT '',
                college TEXT DEFAULT '',
                department TEXT DEFAULT '',
                enrollment_status TEXT DEFAULT 'active'
            );
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                material_path TEXT,
                name TEXT,
                node_type TEXT DEFAULT 'file',
                preview_type TEXT DEFAULT 'markdown',
                check_questions_json TEXT DEFAULT '',
                check_questions_status TEXT DEFAULT 'idle',
                ai_parse_result_json TEXT,
                ai_optimized_markdown TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                order_index INTEGER,
                learning_material_id INTEGER
            );
            CREATE TABLE course_material_assignments (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                material_id INTEGER
            );
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY,
                title TEXT,
                class_offering_id INTEGER,
                exam_paper_id TEXT,
                status TEXT DEFAULT 'published',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id INTEGER,
                student_pk_id INTEGER,
                score REAL,
                status TEXT,
                is_absence_score INTEGER DEFAULT 0
            );
            CREATE TABLE chat_logs (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                user_role TEXT,
                user_id TEXT,
                message TEXT
            );
            CREATE TABLE classroom_behavior_events (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                user_pk INTEGER,
                user_role TEXT,
                action_type TEXT,
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
                recipient_role TEXT
            );
            CREATE TABLE message_center_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_identity TEXT NOT NULL,
                recipient_role TEXT NOT NULL,
                recipient_user_pk INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'normal',
                title TEXT NOT NULL,
                body_preview TEXT DEFAULT '',
                link_url TEXT DEFAULT '',
                class_offering_id INTEGER,
                ref_type TEXT DEFAULT '',
                ref_id TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                email_status TEXT NOT NULL DEFAULT 'not_required',
                read_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        ensure_cultivation_progress_schema(self.conn, engine="sqlite")
        self.conn.executescript(
            """
            CREATE TABLE learning_material_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                session_id INTEGER,
                view_count INTEGER NOT NULL DEFAULT 0,
                accumulated_seconds INTEGER NOT NULL DEFAULT 0,
                active_seconds INTEGER NOT NULL DEFAULT 0,
                max_scroll_ratio REAL NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                mastered INTEGER NOT NULL DEFAULT 0,
                mastered_at TEXT,
                mastery_source TEXT NOT NULL DEFAULT '',
                mastery_attempts INTEGER NOT NULL DEFAULT 0,
                mastery_last_attempt_json TEXT DEFAULT '{}',
                progress_rule_version TEXT NOT NULL DEFAULT 'material_mastery_v2',
                first_viewed_at TEXT,
                last_viewed_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                metadata_json TEXT DEFAULT '{}',
                UNIQUE (class_offering_id, student_id, material_id)
            );
            CREATE TABLE learning_stage_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                stage_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'locked',
                progress_score REAL NOT NULL DEFAULT 0,
                readiness_score REAL NOT NULL DEFAULT 0,
                unlocked_at TEXT,
                passed_at TEXT,
                last_exam_assignment_id INTEGER,
                certificate_id INTEGER,
                last_calculated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                metadata_json TEXT DEFAULT '{}',
                UNIQUE (class_offering_id, student_id, stage_key)
            );
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                stage_key TEXT NOT NULL,
                assignment_id INTEGER,
                exam_paper_id TEXT,
                status TEXT NOT NULL DEFAULT 'generated',
                score REAL,
                generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                submitted_at TEXT,
                graded_at TEXT,
                passed_at TEXT,
                ai_error TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE learning_certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                stage_key TEXT NOT NULL,
                level_key TEXT NOT NULL,
                level_name TEXT NOT NULL,
                tier INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                certificate_code TEXT NOT NULL UNIQUE,
                issued_at TEXT DEFAULT CURRENT_TIMESTAMP,
                revealed_at TEXT,
                metadata_json TEXT DEFAULT '{}',
                UNIQUE (class_offering_id, student_id, stage_key)
            );
            """
        )
        self.conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (1, 10, 20, 30)")
        self.conn.execute("INSERT INTO students (id, class_id, name) VALUES (2, 10, '学生甲')")
        self.conn.execute(
            """
            INSERT INTO course_materials (
                id, teacher_id, material_path, name, node_type, preview_type,
                check_questions_json, check_questions_status
            )
            VALUES (3, 30, 'array.md', '数组边界', 'file', 'markdown', ?, 'ready')
            """,
            (_ready_check_payload(),),
        )
        self.conn.execute(
            "INSERT INTO class_offering_sessions (id, class_offering_id, order_index, learning_material_id) VALUES (7, 1, 1, 3)"
        )

    def tearDown(self):
        self.conn.close()

    def test_reading_gets_seventy_percent_until_mastery_check_passes(self):
        with patch.object(service, "get_configured_db_engine", return_value="sqlite"):
            read_result = service.record_material_learning_progress(
                self.conn,
                class_offering_id=1,
                student_id=2,
                material_id=3,
                session_id=7,
                duration_seconds=310,
                active_seconds=200,
                scroll_ratio=0.9,
                completed=True,
            )

        self.assertTrue(read_result["completed"])
        self.assertFalse(read_result["mastered"])
        self.assertTrue(read_result["mastery_check"]["available"])
        self.assertEqual(31.5, read_result["progress"]["score"])

        with patch.object(service, "get_configured_db_engine", return_value="sqlite"):
            retry_result = service.submit_material_mastery_check(
                self.conn,
                class_offering_id=1,
                student_id=2,
                material_id=3,
                answers={"q1": "B", "q2": "A", "q3": "A"},
            )
        self.assertFalse(retry_result["passed"])
        self.assertFalse(retry_result["mastered"])
        self.assertEqual(31.5, retry_result["progress"]["score"])

        with patch.object(service, "get_configured_db_engine", return_value="sqlite"):
            pass_result = service.submit_material_mastery_check(
                self.conn,
                class_offering_id=1,
                student_id=2,
                material_id=3,
                answers={"q1": "A", "q2": "B", "q3": "C"},
            )

        self.assertTrue(pass_result["passed"])
        self.assertTrue(pass_result["mastered"])
        self.assertEqual(2, pass_result["attempts"])
        self.assertEqual(45.0, pass_result["progress"]["score"])
        event_refs = [
            row["source_ref"]
            for row in self.conn.execute("SELECT source_ref FROM cultivation_score_events ORDER BY id").fetchall()
        ]
        self.assertIn("material:3:read-v2", event_refs)
        self.assertIn("material:3:mastery-check-v2", event_refs)

    def test_legacy_completed_progress_without_mastery_column_keeps_full_credit(self):
        self.assertEqual(1.0, service._material_unit_ratio({"completed": 1}))

    def test_check_generation_failure_payload_keeps_single_tier_fallback(self):
        payload = build_material_mastery_check_payload({}, material_name="空材料")

        self.assertEqual("fallback", payload["status"])
        self.assertEqual([], payload["questions"])
        self.assertTrue(payload["reason"])


if __name__ == "__main__":
    unittest.main()
