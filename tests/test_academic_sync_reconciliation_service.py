import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, patch

from classroom_app import config, database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.db.schema import init_database
from classroom_app.services import academic_course_sync_service as course_sync
from classroom_app.services import academic_sync_reconciliation_service as reconciliation
from classroom_app.services.academic_roster_sync_service import AcademicRosterStudent, AcademicTeachingClassRoster
from classroom_app.services.course_planning_service import replace_offering_sessions


class AcademicSyncReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_engine = config.DB_ENGINE
        self.original_path = config.DB_PATH
        self.original_database_path = database.DB_PATH
        config.DB_ENGINE = "sqlite"
        config.DB_PATH = Path(self.temp_dir.name) / "classroom.db"
        database.DB_PATH = config.DB_PATH
        init_database()
        with database.get_db_connection() as conn:
            self.teacher_id = execute_insert_returning_id(
                conn,
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                ("测试教师", "sync@example.com", "x"),
            )
            self.semester_id = execute_insert_returning_id(
                conn,
                "INSERT INTO academic_semesters (teacher_id, name, start_date, end_date) VALUES (?, ?, ?, ?)",
                (self.teacher_id, "2025-2026学年第1学期", "2025-09-01", "2026-01-20"),
            )
            self.course_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO courses (
                    name, created_by_teacher_id, academic_source,
                    academic_course_code, total_hours
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("服务器配置与管理", self.teacher_id, "gxufl_jwxt", "WRONG-ID", 32),
            )
            self.class_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO classes (
                    name, created_by_teacher_id, academic_source, academic_class_code
                ) VALUES (?, ?, ?, ?)
                """,
                ("软工2406班", self.teacher_id, "gxufl_jwxt", "OLD-CLASS"),
            )
            self.textbook_id = execute_insert_returning_id(
                conn,
                "INSERT INTO textbooks (title, teacher_id) VALUES (?, ?)",
                ("必须保留的教材", self.teacher_id),
            )
            self.offering_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO class_offerings (
                    class_id, course_id, teacher_id, semester, semester_id,
                    textbook_id, academic_teaching_class_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.class_id,
                    self.course_id,
                    self.teacher_id,
                    "2025-2026学年第1学期",
                    self.semester_id,
                    self.textbook_id,
                    "服务器配置与管理-0001",
                ),
            )
            conn.commit()

    def tearDown(self):
        config.DB_ENGINE = self.original_engine
        config.DB_PATH = self.original_path
        database.DB_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def roster(self, *, course_name="服务器配置与管理"):
        return AcademicTeachingClassRoster(
            teaching_class_id="JXB-STABLE-1",
            teaching_class_name="服务器配置与管理-0002",
            academic_year="2025",
            academic_term="3",
            course_code="E020185B3",
            course_internal_id="INTERNAL-KC-1",
            course_code_source="teacher_timetable",
            course_name=course_name,
            class_composition="软工2406班",
            college="数字科技学院",
            schedule_text="星期一第4-5节{1-3周}",
            location_text="知新楼B410",
            declared_student_count=1,
            students=[
                AcademicRosterStudent(
                    student_number="2400000001",
                    name="学生甲",
                    class_name="软工2406班",
                    class_code="NEW-CLASS",
                    college="数字科技学院",
                    grade="2024",
                    major="软件工程",
                )
            ],
        )

    def semester(self):
        return {
            "id": self.semester_id,
            "teacher_id": self.teacher_id,
            "name": "2025-2026学年第1学期",
            "start_date": "2025-09-01",
            "end_date": "2026-01-20",
        }

    def _store_plan(self, preview, roster):
        snapshot = {
            "semester": self.semester(),
            "rosters": [asdict(roster)],
            "source_summary": [],
            "identity_warnings": [],
        }
        with database.get_db_connection() as conn:
            plan_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO teacher_academic_sync_plans (
                    teacher_id, semester_id, status, source_fingerprint,
                    snapshot_json, preview_json, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.teacher_id,
                    self.semester_id,
                    "pending",
                    hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(preview, ensure_ascii=False),
                    "2099-01-01T00:00:00",
                ),
            )
            conn.commit()
        return plan_id

    def test_preview_marks_identity_changes_with_existing_classroom_as_conflicts(self):
        roster = self.roster()
        with database.get_db_connection() as conn:
            preview = reconciliation.build_academic_sync_preview(
                conn,
                teacher_id=self.teacher_id,
                semester=self.semester(),
                rosters=[roster],
            )
        course_item = next(item for item in preview["items"] if item["entity_type"] == "course")
        class_item = next(item for item in preview["items"] if item["entity_type"] == "class")
        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(course_item["local_id"], self.course_id)
        self.assertEqual(course_item["status"], "conflict")
        self.assertEqual(class_item["local_id"], self.class_id)
        self.assertEqual(class_item["status"], "conflict")
        self.assertEqual(course_item["impacts"][0]["textbook_id"], self.textbook_id)

    def test_confirmed_merge_keeps_course_class_offering_and_textbook_ids(self):
        roster = self.roster()
        with database.get_db_connection() as conn:
            preview = reconciliation.build_academic_sync_preview(
                conn,
                teacher_id=self.teacher_id,
                semester=self.semester(),
                rosters=[roster],
            )
        plan_id = self._store_plan(preview, roster)
        resolutions = []
        for item in preview["items"]:
            fields = [field["name"] for field in item.get("fields") or []]
            resolutions.append(
                {
                    "key": item["key"],
                    "action": item["recommended_action"],
                    "remote_fields": fields,
                }
            )
        with patch.object(
            reconciliation,
            "infer_missing_course_metadata_with_ai",
            new=AsyncMock(return_value=({}, {"status": "disabled", "accepted_count": 0})),
        ):
            result = asyncio.run(
                reconciliation.apply_teacher_academic_sync_plan(
                    self.teacher_id,
                    plan_id,
                    {"items": resolutions},
                )
            )
        self.assertEqual(result["status"], "success")
        with database.get_db_connection() as conn:
            course = dict(conn.execute("SELECT * FROM courses WHERE id = ?", (self.course_id,)).fetchone())
            class_row = dict(conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone())
            offering = dict(conn.execute("SELECT * FROM class_offerings WHERE id = ?", (self.offering_id,)).fetchone())
            plan = dict(conn.execute("SELECT * FROM teacher_academic_sync_plans WHERE id = ?", (plan_id,)).fetchone())
        self.assertEqual(course["academic_course_code"], "E020185B3")
        self.assertEqual(class_row["academic_class_code"], "NEW-CLASS")
        self.assertEqual(offering["course_id"], self.course_id)
        self.assertEqual(offering["class_id"], self.class_id)
        self.assertEqual(offering["textbook_id"], self.textbook_id)
        self.assertEqual(offering["academic_teaching_class_id"], "JXB-STABLE-1")
        self.assertEqual(plan["status"], "applied")
        self.assertEqual(plan["snapshot_json"], "{}")
        second_apply = asyncio.run(
            reconciliation.apply_teacher_academic_sync_plan(
                self.teacher_id,
                plan_id,
                {"items": resolutions},
            )
        )
        self.assertEqual(second_apply["status"], "invalid_plan")

    def test_stable_binding_matches_renamed_course_without_creating_parallel_identity(self):
        with database.get_db_connection() as conn:
            reconciliation._upsert_binding(
                conn,
                teacher_id=self.teacher_id,
                semester_scope=0,
                entity_type="course",
                source_key="internal:internal-kc-1",
                local_entity_id=self.course_id,
                source_label="服务器配置与管理",
                confirmed=True,
            )
            conn.commit()
            preview = reconciliation.build_academic_sync_preview(
                conn,
                teacher_id=self.teacher_id,
                semester=self.semester(),
                rosters=[self.roster(course_name="服务器部署与管理")],
            )
        course_item = next(item for item in preview["items"] if item["entity_type"] == "course")
        self.assertEqual(course_item["local_id"], self.course_id)
        self.assertEqual(course_item["match_reason"], "stable_binding")
        self.assertTrue(any(field["name"] == "name" for field in course_item["fields"]))

    def test_existing_human_description_is_not_selected_for_overwrite_by_default(self):
        with database.get_db_connection() as conn:
            conn.execute(
                "UPDATE courses SET description = ? WHERE id = ?",
                ("教师人工维护的课程简介", self.course_id),
            )
            conn.commit()
            preview = reconciliation.build_academic_sync_preview(
                conn,
                teacher_id=self.teacher_id,
                semester=self.semester(),
                rosters=[self.roster()],
            )

        course_item = next(item for item in preview["items"] if item["entity_type"] == "course")
        description = next(field for field in course_item["fields"] if field["name"] == "description")
        self.assertFalse(description["default_remote"])

    def test_apply_rejects_preview_after_local_identity_field_changes(self):
        roster = self.roster()
        with database.get_db_connection() as conn:
            preview = reconciliation.build_academic_sync_preview(
                conn,
                teacher_id=self.teacher_id,
                semester=self.semester(),
                rosters=[roster],
            )
        plan_id = self._store_plan(preview, roster)
        with database.get_db_connection() as conn:
            conn.execute(
                "UPDATE courses SET academic_course_code = ? WHERE id = ?",
                ("MANUAL-NEW-CODE", self.course_id),
            )
            conn.commit()

        result = asyncio.run(
            reconciliation.apply_teacher_academic_sync_plan(self.teacher_id, plan_id, {})
        )

        self.assertEqual(result["status"], "stale_plan")
        self.assertTrue(result["changes"])

    def test_removed_academic_sessions_are_cancelled_instead_of_deleted(self):
        sessions = [
            {
                "order_index": index,
                "title": f"第 {index} 次课",
                "content": "",
                "section_count": 2,
                "slot_section_count": 2,
                "session_date": f"2025-09-0{index}",
                "weekday": index - 1,
                "week_index": 1,
                "schedule_source": "academic_sync",
            }
            for index in (1, 2)
        ]
        with database.get_db_connection() as conn:
            replace_offering_sessions(conn, offering_id=self.offering_id, sessions=sessions)
            session_two_id = int(
                conn.execute(
                    "SELECT id FROM class_offering_sessions WHERE class_offering_id = ? AND order_index = 2",
                    (self.offering_id,),
                ).fetchone()["id"]
            )
            result = replace_offering_sessions(
                conn,
                offering_id=self.offering_id,
                sessions=sessions[:1],
                preserve_removed=True,
            )
            kept = dict(conn.execute("SELECT * FROM class_offering_sessions WHERE id = ?", (session_two_id,)).fetchone())
            conn.commit()
        self.assertEqual(result["preserved_count"], 1)
        self.assertEqual(kept["schedule_status"], "cancelled")
        self.assertIn("保留课次", kept["schedule_note"])

    def test_conflict_free_course_upsert_keeps_legacy_auto_sync_path_working(self):
        roster = self.roster()
        items = course_sync.build_schedule_items_from_teaching_class_rosters(
            [roster],
            source_url="/academic-test",
        )
        with database.get_db_connection() as conn:
            conn.execute(
                "UPDATE courses SET academic_course_code = ? WHERE id = ?",
                ("E020185B3", self.course_id),
            )
            conn.commit()
            result = course_sync._upsert_courses_and_schedule_items(
                conn,
                teacher_id=self.teacher_id,
                semester=self.semester(),
                items=items,
                source_summary=[],
            )
            conn.commit()

        self.assertEqual(result["course_count"], 1)
        self.assertEqual(result["courses"][0]["course_id"], self.course_id)


if __name__ == "__main__":
    unittest.main()
