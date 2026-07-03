import json
import sqlite3
import unittest
from unittest.mock import patch

import classroom_app.db.schema_assessment_plans as assessment_schema
from classroom_app.services import exam_material_reverse_service as svc


def _make_conn() -> sqlite3.Connection:
    assessment_schema._SCHEMA_READY = False
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
            college TEXT DEFAULT '广西外国语学院',
            department TEXT DEFAULT '网络工程系'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE exam_papers (
            id TEXT PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            questions_json TEXT NOT NULL,
            exam_config_json TEXT,
            status TEXT DEFAULT 'draft',
            ai_gen_task_id TEXT,
            ai_gen_status TEXT,
            ai_gen_error TEXT,
            owner_role TEXT DEFAULT 'teacher',
            owner_user_pk INTEGER,
            scope_level TEXT DEFAULT 'private',
            school_code TEXT DEFAULT 'gxufl',
            school_name TEXT DEFAULT '广西外国语学院',
            college TEXT DEFAULT '广西外国语学院',
            department TEXT DEFAULT '网络工程系',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO teachers (id, name, username, email) VALUES (1, '张海林', 'zhang', 'zhang@example.com')"
    )
    return conn


def _exam_questions(*, complete: bool = True) -> dict:
    pages = []
    ordinal = 1
    for page_index, page_name in enumerate(("基础网络配置", "路由交换配置", "综合验证提交"), start=1):
        questions = []
        for _ in range(4):
            question = {
                "id": f"q{ordinal}",
                "type": "textarea",
                "text": f"完成第{ordinal}项网络实验任务，包含 VLAN、OSPF、NAT 或 DHCP 的配置与验证截图。",
                "points": 10,
                "answer": f"第{ordinal}项配置正确，验证命令输出符合题目要求。",
                "grading_guidance": f"配置步骤完整、关键命令正确、截图能证明第{ordinal}项结果。",
                "deduction_points": "命令错误、缺少验证截图、结果与题目不符时按比例扣分。",
                "attachment_requirements": {
                    "enabled": True,
                    "required": True,
                    "min_count": 1,
                    "max_count": 3,
                    "allowed_file_types": [".png", ".jpg", ".pdf"],
                    "description": "提交配置截图和实验报告。",
                },
            }
            if not complete:
                question.pop("answer")
                question.pop("grading_guidance")
                question.pop("deduction_points")
            questions.append(question)
            ordinal += 1
        pages.append({"name": page_name, "questions": questions})
    return {
        "grading": {"total_score": 120, "description": "按题目标准答案、关键步骤和截图证据评分。", "style": "medium"},
        "pages": pages,
    }


def _insert_paper(conn: sqlite3.Connection, *, paper_id: str = "paper-1", complete: bool = True) -> None:
    conn.execute(
        """
        INSERT INTO exam_papers (
            id, teacher_id, title, description, questions_json, status,
            owner_role, owner_user_pk, scope_level, school_code, school_name, college, department
        ) VALUES (?, 1, '计算机网络实验-实验报告8-综合考核', '网络实验综合考核', ?, 'ready',
                  'teacher', 1, 'private', 'gxufl', '广西外国语学院', '广西外国语学院', '网络工程系')
        """,
        (paper_id, json.dumps(_exam_questions(complete=complete), ensure_ascii=False)),
    )


def _create_assignment_context_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT,
            school_name TEXT,
            college TEXT,
            department TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE classes (
            id INTEGER PRIMARY KEY,
            name TEXT,
            academic_class_name TEXT,
            academic_major TEXT,
            major TEXT,
            department TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            course_id INTEGER,
            class_id INTEGER,
            teacher_id INTEGER,
            semester TEXT,
            academic_teaching_class_name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE assignments (
            id INTEGER PRIMARY KEY,
            course_id INTEGER,
            title TEXT,
            status TEXT,
            created_at TEXT,
            exam_paper_id TEXT,
            class_offering_id INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO courses (id, name, school_name, college, department)
        VALUES (10, 'Dynamic Web Program Design', 'GXUFL', 'Software College', 'Software Engineering')
        """
    )
    conn.execute(
        """
        INSERT INTO classes (id, name, academic_class_name, academic_major, major, department)
        VALUES (20, 'Software 2401', 'Soft2401', 'Software Engineering', 'Software Engineering', 'Software Engineering')
        """
    )
    conn.execute(
        """
        INSERT INTO class_offerings (id, course_id, class_id, teacher_id, semester, academic_teaching_class_name)
        VALUES (30, 10, 20, 1, '2025-2026-2', 'Soft2401')
        """
    )
    conn.execute(
        """
        INSERT INTO assignments (id, course_id, title, status, created_at, exam_paper_id, class_offering_id)
        VALUES (40, 10, 'Final exam', 'published', '2026-07-03T13:05:32', 'paper-1', 30)
        """
    )


def _create_material_ai_import_records_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE material_ai_import_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            package_material_id INTEGER,
            source_material_id INTEGER,
            parsed_material_id INTEGER,
            parent_material_id INTEGER,
            document_group TEXT,
            document_type TEXT,
            document_type_label TEXT,
            parse_status TEXT,
            parse_mode TEXT,
            extraction_method TEXT,
            source_file_name TEXT,
            source_file_hash TEXT,
            source_file_size INTEGER,
            source_mime_type TEXT,
            metadata_json TEXT,
            content_markdown TEXT,
            parsed_payload_json TEXT,
            export_payload_json TEXT,
            warnings_json TEXT,
            content_quality_status TEXT,
            content_quality_json TEXT,
            error_message TEXT,
            created_at TEXT,
            started_at TEXT,
            updated_at TEXT,
            completed_at TEXT,
            failed_at TEXT
        )
        """
    )


class ExamMaterialReverseServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.teacher = {"id": 1, "name": "张海林", "username": "zhang"}

    def tearDown(self):
        self.conn.close()

    def test_context_scales_scores_and_builds_source_bound_items(self):
        _insert_paper(self.conn)
        context = svc.build_exam_reverse_context(
            self.conn,
            paper_id="paper-1",
            teacher=self.teacher,
            require_complete=True,
        )

        self.assertEqual(context["fields"]["course_name"], "计算机网络实验")
        self.assertEqual(sum(int(item["score"]) for item in context["assessment_items"]), 100)
        self.assertEqual(len(context["assessment_items"]), 3)
        self.assertEqual(len(context["rubric_items"]), 12)
        rubric_text = json.dumps(context["rubric_items"], ensure_ascii=False)
        self.assertIn("标准答案", rubric_text)
        self.assertIn("得分点", rubric_text)
        self.assertIn("扣分点", rubric_text)
        self.assertIn("附件/提交要求", rubric_text)
        plan_text = json.dumps(context["assessment_items"], ensure_ascii=False)
        for forbidden in ("平时", "考勤", "课堂表现", "作业", "过程性"):
            self.assertNotIn(forbidden, plan_text)

    def test_assessment_plan_placeholder_is_generating_exam_reverse_card(self):
        _insert_paper(self.conn)
        result = svc.create_assessment_plan_reverse_placeholder(
            self.conn,
            teacher=self.teacher,
            paper_id="paper-1",
            prompt="突出网络拓扑验证",
        )

        row = self.conn.execute("SELECT * FROM assessment_plans WHERE id = ?", (result["plan_id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_type"], "exam_reverse")
        self.assertEqual(row["status"], "generating")
        self.assertEqual(row["ai_gen_status"], "pending")
        self.assertEqual(result["redirect_url"], "/manage/teaching/assessment-plans")

    def test_assignment_context_tolerates_assignments_without_updated_at(self):
        _insert_paper(self.conn)
        _create_assignment_context_tables(self.conn)

        context = svc.build_exam_reverse_context(
            self.conn,
            paper_id="paper-1",
            teacher=self.teacher,
            require_complete=True,
        )

        self.assertEqual(context["course_id"], 10)
        self.assertEqual(context["class_offering_id"], 30)
        self.assertEqual(context["fields"]["course_name"], "Dynamic Web Program Design")
        self.assertEqual(context["fields"]["class_name"], "Soft2401")
        self.assertEqual(sum(int(item["score"]) for item in context["assessment_items"]), 100)
        self.assertEqual(len(context["rubric_items"]), 12)

    def test_grading_rubric_placeholder_creates_running_import_record(self):
        _insert_paper(self.conn)
        _create_assignment_context_tables(self.conn)
        _create_material_ai_import_records_table(self.conn)

        with (
            patch.object(svc, "ensure_materials_integrations_schema", lambda conn: None),
            patch.object(svc, "get_configured_db_engine", return_value="sqlite"),
        ):
            result = svc.create_grading_rubric_reverse_placeholder(
                self.conn,
                teacher=self.teacher,
                paper_id="paper-1",
                prompt="Focus on Spring Boot project delivery.",
            )

        row = self.conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (result["record_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["document_group"], "final_material")
        self.assertEqual(row["document_type"], "grading_rubric")
        self.assertEqual(row["parse_status"], "running")
        self.assertEqual(row["parse_mode"], "ai_generated")
        self.assertEqual(row["extraction_method"], "exam_reverse")
        self.assertEqual(result["redirect_url"], "/manage/teaching/grading-rubrics")
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(metadata["source_exam_paper_id"], "paper-1")
        self.assertEqual(metadata["generation_mode"], "exam_reverse")
        self.assertEqual(metadata["teacher_prompt"], "Focus on Spring Boot project delivery.")

    def test_optional_fetchone_rolls_back_failed_optional_query(self):
        log: list[str] = []

        class FakeCursor:
            def fetchone(self):
                return {"ok": 1}

        class FakeConn:
            def execute(self, sql, params=()):
                log.append(str(sql))
                if "bad_table" in str(sql):
                    raise RuntimeError("missing relation")
                return FakeCursor()

        result = svc._optional_fetchone(FakeConn(), "SELECT * FROM bad_table WHERE id = ?", (1,))

        self.assertIsNone(result)
        self.assertTrue(any(entry.startswith("SAVEPOINT exam_material_reverse_optional") for entry in log))
        self.assertTrue(any(entry.startswith("ROLLBACK TO SAVEPOINT exam_material_reverse_optional") for entry in log))
        self.assertTrue(any(entry.startswith("RELEASE SAVEPOINT exam_material_reverse_optional") for entry in log))

    def test_grading_rubric_requires_complete_scoring_source(self):
        _insert_paper(self.conn, paper_id="paper-incomplete", complete=False)

        with self.assertRaises(Exception) as caught:
            svc.build_exam_reverse_context(
                self.conn,
                paper_id="paper-incomplete",
                teacher=self.teacher,
                require_complete=True,
            )
        self.assertIn("评分细则表要求试卷已补齐", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
