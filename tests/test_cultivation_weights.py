from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
from classroom_app.services import learning_progress_service as service
from classroom_app.services.cultivation_weight_service import (
    CULTIVATION_WEIGHT_VERSION_DEFAULT,
    CultivationWeightValidationError,
    normalize_cultivation_weights,
)


def _build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_cultivation_progress_schema(conn, engine="sqlite")
    conn.executescript(
        """
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE classes (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT,
            sect_name TEXT,
            description TEXT,
            credits REAL
        );
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            class_id INTEGER,
            course_id INTEGER,
            teacher_id INTEGER,
            cultivation_weights_json TEXT NOT NULL DEFAULT '',
            cultivation_weights_version TEXT NOT NULL DEFAULT 'default-v1',
            cultivation_weights_updated_at TEXT,
            cultivation_weights_updated_by_teacher_id INTEGER
        );
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            class_id INTEGER,
            name TEXT,
            student_id_number TEXT,
            enrollment_status TEXT DEFAULT 'active'
        );
        """
    )
    conn.execute("INSERT INTO teachers (id, name) VALUES (30, 'Teacher')")
    conn.execute("INSERT INTO classes (id, name) VALUES (10, 'Class')")
    conn.execute("INSERT INTO courses (id, name, sect_name) VALUES (20, 'Course', 'Course Sect')")
    conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (1, 10, 20, 30)")
    conn.execute("INSERT INTO students (id, class_id, name, student_id_number) VALUES (7, 10, 'Alpha', '001')")
    conn.execute("INSERT INTO students (id, class_id, name, student_id_number) VALUES (8, 10, 'Beta', '002')")
    conn.execute("INSERT INTO students (id, class_id, name, student_id_number, enrollment_status) VALUES (9, 10, 'Gone', '003', 'inactive')")
    conn.commit()
    return conn


def _patch_score_sources():
    return (
        patch.object(service, "_load_required_materials", return_value=[]),
        patch.object(service, "_load_progress_rows", return_value={}),
        patch.object(
            service,
            "_load_assignment_metrics",
            return_value={
                "task_ratio": 0.5,
                "assignment_count": 2,
                "submitted_count": 1,
                "completion_ratio": 0.5,
                "items": [],
            },
        ),
        patch.object(
            service,
            "_load_interaction_metrics",
            return_value={
                "interaction_ratio": 1.0,
                "consistency_ratio": 1.0,
            },
        ),
    )


class CultivationWeightTests(unittest.TestCase):
    def test_default_weights_keep_legacy_score_shape(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        patches = _patch_score_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            metrics = service._build_learning_metrics(conn, 1, 7)

        self.assertEqual(metrics["weights"], {"material": 45, "task": 35, "interaction": 15, "consistency": 5})
        self.assertEqual(metrics["weight_version"], CULTIVATION_WEIGHT_VERSION_DEFAULT)
        self.assertEqual(metrics["components"]["task"], 17.5)
        self.assertEqual(metrics["components"]["interaction"], 15.0)
        self.assertEqual(metrics["components"]["consistency"], 5.0)
        self.assertEqual(metrics["score"], 37.5)

    def test_invalid_weight_sum_is_rejected(self) -> None:
        with self.assertRaises(CultivationWeightValidationError):
            normalize_cultivation_weights({"material": 45, "task": 35, "interaction": 15, "consistency": 4})

    def test_preview_returns_delta_without_dirtying_snapshots(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        patches = _patch_score_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            preview = service.preview_class_cultivation_weights(
                conn,
                1,
                {"material": 30, "task": 55, "interaction": 10, "consistency": 5},
            )

        self.assertEqual(preview["student_count"], 2)
        self.assertEqual(preview["old_average"], 37.5)
        self.assertEqual(preview["new_average"], 42.5)
        self.assertEqual(preview["affected_count"], 2)
        self.assertEqual(
            0,
            conn.execute("SELECT COUNT(*) FROM learning_progress_snapshots").fetchone()[0],
        )
        self.assertEqual(
            0,
            conn.execute("SELECT COUNT(*) FROM cultivation_score_events").fetchone()[0],
        )

    def test_update_weights_marks_active_students_dirty_and_logs_recalibration(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)

        result = service.update_class_cultivation_weights(
            conn,
            1,
            teacher_id=30,
            weights_payload={"material": 30, "task": 55, "interaction": 10, "consistency": 5},
        )

        self.assertTrue(result["updated"])
        self.assertEqual(result["dirty_count"], 2)
        self.assertFalse(result["weight_settings"]["can_update"])
        dirty_rows = conn.execute(
            """
            SELECT student_id, dirty, metadata_json
            FROM learning_progress_snapshots
            ORDER BY student_id
            """
        ).fetchall()
        self.assertEqual([7, 8], [int(row["student_id"]) for row in dirty_rows])
        self.assertTrue(all(int(row["dirty"]) == 1 for row in dirty_rows))
        self.assertTrue(all(json.loads(row["metadata_json"])["dirty_source_ref"].startswith("weights:") for row in dirty_rows))

        events = conn.execute(
            """
            SELECT event_type, component, delta, metadata_json
            FROM cultivation_score_events
            ORDER BY student_id
            """
        ).fetchall()
        self.assertEqual(2, len(events))
        for row in events:
            metadata = json.loads(row["metadata_json"])
            self.assertEqual(row["event_type"], "recalibration")
            self.assertEqual(row["component"], "total")
            self.assertEqual(float(row["delta"]), 0.0)
            self.assertEqual(metadata["reason"], "cultivation_weight_update")
            self.assertEqual(metadata["previous_weight_version"], "default-v1")
            self.assertEqual(metadata["weights"]["task"], 55)

    def test_weight_update_cooldown_rejects_second_change(self) -> None:
        conn = _build_conn()
        self.addCleanup(conn.close)
        service.update_class_cultivation_weights(
            conn,
            1,
            teacher_id=30,
            weights_payload={"material": 30, "task": 55, "interaction": 10, "consistency": 5},
        )

        with self.assertRaises(CultivationWeightValidationError):
            service.update_class_cultivation_weights(
                conn,
                1,
                teacher_id=30,
                weights_payload={"material": 30, "task": 25, "interaction": 40, "consistency": 5},
            )


if __name__ == "__main__":
    unittest.main()
