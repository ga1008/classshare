import sqlite3
import unittest
from datetime import datetime, timedelta

import classroom_app.db.schema_assessment_plans as assessment_schema
import classroom_app.db.schema_lesson_plans as lesson_schema
import classroom_app.db.schema_teacher_evaluations as evaluation_schema
from classroom_app.db.schema_assessment_plans import ensure_assessment_plan_schema
from classroom_app.db.schema_lesson_plans import ensure_lesson_plan_schema
from classroom_app.db.schema_teacher_evaluations import ensure_teacher_evaluation_schema
from classroom_app.services import assessment_plan_service as assessment_service
from classroom_app.services import lesson_plan_service as lesson_service
from classroom_app.services import teacher_evaluation_service as evaluation_service
from classroom_app.services.lesson_plan_recovery_service import expire_stale_lesson_plan_tasks
from classroom_app.services.process_material_recovery_service import (
    expire_stale_assessment_plan_tasks,
    expire_stale_teacher_evaluation_tasks,
)


def _make_conn() -> sqlite3.Connection:
    assessment_schema._SCHEMA_READY = False
    lesson_schema._SCHEMA_READY = False
    evaluation_schema._SCHEMA_READY = False
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            email TEXT,
            is_super_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            school_code TEXT DEFAULT 'gxufl',
            school_name TEXT DEFAULT '广西外国语学院',
            college TEXT DEFAULT '',
            department TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        "INSERT INTO teachers (id, name, username, email, college, department) "
        "VALUES (1, '张老师', 'zhang', 'zhang@example.edu', '信息工程学院', '软件工程系')"
    )
    conn.execute(
        "INSERT INTO teachers (id, name, username, email, college, department) "
        "VALUES (2, '李老师', 'li', 'li@example.edu', '信息工程学院', '软件工程系')"
    )
    ensure_assessment_plan_schema(conn)
    ensure_lesson_plan_schema(conn)
    ensure_teacher_evaluation_schema(conn)
    return conn


def _teacher() -> dict:
    return {"id": 1, "name": "张老师", "username": "zhang"}


def _other_teacher() -> dict:
    return {"id": 2, "name": "李老师", "username": "li"}


def _set_stale_timestamp(conn: sqlite3.Connection, table_name: str, item_id: str) -> None:
    stale_at = (datetime.now() - timedelta(minutes=45)).isoformat()
    conn.execute(f"UPDATE {table_name} SET updated_at = ? WHERE id = ?", (stale_at, item_id))


def _row(conn: sqlite3.Connection, table_name: str, item_id: str) -> dict:
    row = conn.execute(f"SELECT * FROM {table_name} WHERE id = ?", (item_id,)).fetchone()
    return dict(row)


class ProcessMaterialRecoveryTests(unittest.TestCase):
    def test_stale_assessment_import_becomes_failed_upload_action(self):
        conn = _make_conn()
        try:
            stale_id = assessment_service.create_assessment_plan(
                conn,
                teacher=_teacher(),
                title="导入卡住的考核计划表",
                source_type="import",
                status="parsing",
                ai_gen_status="running",
            )
            recent_id = assessment_service.create_assessment_plan(
                conn,
                teacher=_teacher(),
                title="刚开始生成的考核计划表",
                source_type="classroom",
                status="generating",
                ai_gen_status="running",
                class_offering_id=12,
            )
            _set_stale_timestamp(conn, "assessment_plans", stale_id)

            self.assertEqual(1, expire_stale_assessment_plan_tasks(conn, stale_minutes=30, teacher_id=1))

            stale = _row(conn, "assessment_plans", stale_id)
            recent = _row(conn, "assessment_plans", recent_id)
            self.assertEqual("failed", stale["status"])
            self.assertEqual("failed", stale["ai_gen_status"])
            self.assertIn("重新上传文件再解析", stale["ai_gen_error"])
            self.assertEqual("generating", recent["status"])
            self.assertEqual("running", recent["ai_gen_status"])
        finally:
            conn.close()

    def test_stale_assessment_recovery_can_be_scoped_to_current_teacher(self):
        conn = _make_conn()
        try:
            mine_id = assessment_service.create_assessment_plan(
                conn,
                teacher=_teacher(),
                title="我的卡住任务",
                source_type="classroom",
                status="generating",
                ai_gen_status="running",
                class_offering_id=12,
            )
            other_id = assessment_service.create_assessment_plan(
                conn,
                teacher=_other_teacher(),
                title="他人的卡住任务",
                source_type="classroom",
                status="generating",
                ai_gen_status="running",
                class_offering_id=21,
            )
            _set_stale_timestamp(conn, "assessment_plans", mine_id)
            _set_stale_timestamp(conn, "assessment_plans", other_id)

            self.assertEqual(1, expire_stale_assessment_plan_tasks(conn, stale_minutes=30, teacher_id=1))

            mine = _row(conn, "assessment_plans", mine_id)
            other = _row(conn, "assessment_plans", other_id)
            self.assertEqual("failed", mine["status"])
            self.assertEqual("generating", other["status"])
        finally:
            conn.close()

    def test_stale_teacher_evaluation_generation_becomes_failed_retry_action(self):
        conn = _make_conn()
        try:
            stale_id = evaluation_service.create_evaluation(
                conn,
                teacher=_teacher(),
                title="生成卡住的评学表",
                source_type="classroom",
                status="generating",
                ai_gen_status="pending",
                class_offering_id=18,
            )
            _set_stale_timestamp(conn, "teacher_evaluations", stale_id)

            self.assertEqual(1, expire_stale_teacher_evaluation_tasks(conn, stale_minutes=30, teacher_id=1))

            stale = _row(conn, "teacher_evaluations", stale_id)
            self.assertEqual("failed", stale["status"])
            self.assertEqual("failed", stale["ai_gen_status"])
            self.assertIn("重试生成", stale["ai_gen_error"])
        finally:
            conn.close()

    def test_stale_lesson_plan_import_and_generation_get_specific_recovery_actions(self):
        conn = _make_conn()
        try:
            import_id = lesson_service.create_lesson_plan(
                conn,
                teacher=_teacher(),
                title="导入卡住的教案",
                source_type="import",
                status="parsing",
                ai_gen_status="running",
            )
            generate_id = lesson_service.create_lesson_plan(
                conn,
                teacher=_teacher(),
                title="生成卡住的教案",
                source_type="classroom",
                status="generating",
                ai_gen_status="pending",
                class_offering_id=18,
            )
            other_id = lesson_service.create_lesson_plan(
                conn,
                teacher=_other_teacher(),
                title="其他教师的卡住教案",
                source_type="classroom",
                status="generating",
                ai_gen_status="running",
                class_offering_id=21,
            )
            _set_stale_timestamp(conn, "lesson_plans", import_id)
            _set_stale_timestamp(conn, "lesson_plans", generate_id)
            _set_stale_timestamp(conn, "lesson_plans", other_id)

            self.assertEqual(2, expire_stale_lesson_plan_tasks(conn, stale_minutes=30, teacher_id=1))

            import_row = _row(conn, "lesson_plans", import_id)
            generate_row = _row(conn, "lesson_plans", generate_id)
            other_row = _row(conn, "lesson_plans", other_id)
            self.assertEqual("failed", import_row["status"])
            self.assertEqual("failed", import_row["ai_gen_status"])
            self.assertIn("重新上传文件再解析", import_row["ai_gen_error"])
            self.assertEqual("failed", generate_row["status"])
            self.assertEqual("failed", generate_row["ai_gen_status"])
            self.assertIn("重试生成", generate_row["ai_gen_error"])
            self.assertEqual("generating", other_row["status"])
            self.assertEqual("running", other_row["ai_gen_status"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
