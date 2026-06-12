import json
import sqlite3
import unittest

from classroom_app.services import student_insight_service as service


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            class_id INTEGER NOT NULL,
            name TEXT DEFAULT '',
            enrollment_status TEXT DEFAULT 'active'
        );
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            sect_name TEXT
        );
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            class_id INTEGER,
            course_id INTEGER
        );
        CREATE TABLE learning_progress_snapshots (
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            components_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE cultivation_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            rule_key TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'L1',
            status TEXT NOT NULL DEFAULT 'active',
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            snoozed_until TEXT,
            handled_at TEXT,
            handled_by_teacher_id INTEGER,
            action_note TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE cultivation_score_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            delta REAL NOT NULL DEFAULT 0,
            component TEXT NOT NULL DEFAULT 'total',
            source_ref TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE learning_stage_exam_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'generated',
            score REAL,
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            submitted_at TEXT,
            graded_at TEXT,
            passed_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO courses (id, name, sect_name) VALUES (1, '综合英语', '剑修')")
    conn.execute("INSERT INTO class_offerings (id, class_id, course_id) VALUES (1, 10, 1)")
    return conn


class StudentInsightCultivationTests(unittest.TestCase):
    def test_merge_timeline_items_sorts_and_truncates(self):
        items = service._merge_timeline_items(
            [{"type": "old", "occurred_at": "2026-06-10T08:00:00", "sort_id": 9}],
            [{"type": "new", "occurred_at": "2026-06-12T08:00:00", "sort_id": 1}],
            [{"type": "tie_high", "occurred_at": "2026-06-11T08:00:00", "sort_id": 3}],
            [{"type": "tie_low", "occurred_at": "2026-06-11T08:00:00", "sort_id": 2}],
            limit=3,
        )

        self.assertEqual(["new", "tie_high", "tie_low"], [item["type"] for item in items])

    def test_load_active_alerts_is_scoped_and_serialized(self):
        conn = _conn()
        try:
            conn.execute("INSERT INTO students (id, class_id, name) VALUES (2, 10, '学生甲')")
            conn.execute(
                """
                INSERT INTO cultivation_alerts (
                    class_offering_id, student_id, rule_key, severity, status,
                    title, body, evidence_json, first_seen_at, last_seen_at
                )
                VALUES (1, 2, 'low_task_completion', 'L2', 'active', '任务完成率偏低', '已提交 1/4。', ?, '2026-06-12T08:00:00', '2026-06-12T09:00:00')
                """,
                (json.dumps({"assignment_count": 4, "submitted_count": 1, "completion_ratio": 0.25}, ensure_ascii=False),),
            )
            conn.execute(
                """
                INSERT INTO cultivation_alerts (
                    class_offering_id, student_id, rule_key, severity, status,
                    title, body, evidence_json, first_seen_at, last_seen_at
                )
                VALUES (1, 2, 'no_activity_7d', 'L3', 'handled', '7 天无活动', '已处理。', '{}', '2026-06-12T07:00:00', '2026-06-12T07:00:00')
                """
            )

            alerts = service._load_student_active_alerts(conn, offering_ids=[1], student_id=2)

            self.assertEqual(1, len(alerts))
            self.assertEqual("L2", alerts[0]["severity"])
            self.assertEqual("关注", alerts[0]["severity_label"])
            self.assertEqual("剑修", alerts[0]["sect_name"])
            self.assertIn({"label": "完成率", "value": "25%"}, alerts[0]["evidence_items"])
        finally:
            conn.close()

    def test_cultivation_timeline_merges_alerts_score_events_exams_and_teacher_note(self):
        conn = _conn()
        try:
            conn.execute("INSERT INTO students (id, class_id, name) VALUES (2, 10, '学生甲')")
            conn.execute(
                """
                INSERT INTO cultivation_alerts (
                    class_offering_id, student_id, rule_key, severity, status,
                    title, body, evidence_json, first_seen_at, last_seen_at
                )
                VALUES (1, 2, 'no_activity_7d', 'L3', 'active', '7 天无活动', '最近没有活动。', '{}', '2026-06-12T08:00:00', '2026-06-12T08:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO cultivation_score_events (
                    class_offering_id, student_id, event_type, delta, component, source_ref, created_at
                )
                VALUES (1, 2, 'material_progress', 3.5, 'material', 'material:8', '2026-06-11T08:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO learning_stage_exam_attempts (
                    class_offering_id, student_id, stage_key, status, score, generated_at, graded_at
                )
                VALUES (1, 2, 'foundation', 'failed', 54, '2026-06-10T07:00:00', '2026-06-10T08:00:00')
                """
            )
            teacher_note = {
                "has_note": True,
                "note_text": "需要先用短任务恢复节奏。",
                "updated_at": "2026-06-13T08:00:00",
                "updated_by_name": "王老师",
            }

            timeline = service._build_student_cultivation_timeline(
                conn,
                offering_ids=[1],
                student_id=2,
                teacher_note=teacher_note,
            )

            self.assertEqual(["teacher_note", "alert", "score_event", "stage_exam"], [item["type"] for item in timeline])
            self.assertEqual("danger", timeline[1]["tone"])
            self.assertIn("材料研读增长", timeline[2]["title"])
            self.assertEqual("破境试炼受阻", timeline[3]["title"])
        finally:
            conn.close()

    def test_class_radar_average_hides_single_student_and_uses_cached_snapshots_for_peer_group(self):
        conn = _conn()
        try:
            conn.execute("INSERT INTO students (id, class_id, name) VALUES (2, 10, '学生甲')")
            offerings = [{"class_offering_id": 1}]

            single = service._load_class_radar_average(conn, offerings=offerings, class_id=10)

            self.assertFalse(single["available"])
            self.assertEqual(1, single["student_count"])

            conn.execute("INSERT INTO students (id, class_id, name) VALUES (3, 10, '学生乙')")
            conn.execute(
                """
                INSERT INTO learning_progress_snapshots (class_offering_id, student_id, score, components_json)
                VALUES (1, 2, 50, ?), (1, 3, 100, ?)
                """,
                (
                    json.dumps({"material": 22.5, "task": 17.5, "interaction": 7.5, "consistency": 2.5}),
                    json.dumps({"material": 45, "task": 35, "interaction": 15, "consistency": 5}),
                ),
            )

            average = service._load_class_radar_average(conn, offerings=offerings, class_id=10)

            self.assertTrue(average["available"])
            self.assertEqual(2, average["student_count"])
            self.assertEqual(75, average["axes"]["material"])
            self.assertEqual(75, average["axes"]["task"])
            self.assertEqual(75, average["axes"]["quality"])
            self.assertEqual(75, average["axes"]["cultivation"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
