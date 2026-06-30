import asyncio
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from openpyxl import load_workbook

from classroom_app.services.material_ai_import_service import parse_material_document
from classroom_app.services.material_export_template_service import (
    XLSX_MEDIA_TYPE,
    build_material_export_artifact,
)
from classroom_app.services.ordinary_grade_record_service import (
    ORDINARY_GRADE_RECORD_TYPE,
    build_ordinary_grade_record_payload,
    build_ordinary_grade_record_xlsx,
    list_ordinary_grade_assignment_candidates,
    normalize_ordinary_grade_record_payload,
    parse_ordinary_grade_record_file,
    validate_ordinary_grade_sources,
)


class OrdinaryGradeRecordServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._create_schema()
        self._seed_data()

    def tearDown(self) -> None:
        self.conn.close()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                total_hours INTEGER,
                credits REAL,
                college TEXT,
                department TEXT
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                semester TEXT
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                student_id_number TEXT,
                name TEXT,
                enrollment_status TEXT
            );
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY,
                title TEXT,
                status TEXT,
                exam_paper_id TEXT,
                created_at TEXT,
                due_at TEXT,
                grading_mode TEXT,
                class_offering_id INTEGER
            );
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT,
                student_pk_id INTEGER,
                score REAL
            );
            CREATE TABLE smart_classroom_checkin_sessions (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                class_offering_id INTEGER,
                session_id INTEGER,
                checkin_time TEXT,
                synced_at TEXT
            );
            CREATE TABLE smart_classroom_checkin_students (
                id INTEGER PRIMARY KEY,
                checkin_session_id INTEGER,
                student_id INTEGER,
                status TEXT
            );
            """
        )

    def _seed_data(self) -> None:
        self.conn.execute("INSERT INTO teachers VALUES (1, '张海林', '数字科技学院', '软件工程系')")
        self.conn.execute("INSERT INTO courses VALUES (10, '服务器配置与管理', 48, 3.0, '数字科技学院', '软件工程系')")
        self.conn.execute("INSERT INTO classes VALUES (20, '软工2406班（专升本）', '数字科技学院', '软件工程系')")
        self.conn.execute("INSERT INTO class_offerings VALUES (30, 20, 10, 1, '2025-2026-1')")
        students = [
            (101, 20, "20240101", "学生一", "active"),
            (102, 20, "20240102", "学生二", "active"),
            (103, 20, "20240103", "学生三", "active"),
        ]
        self.conn.executemany("INSERT INTO students VALUES (?, ?, ?, ?, ?)", students)
        assignments = [
            (201, "第一次作业", "published", "", "2025-09-01", "2025-09-10", "manual", 30),
            (202, "第二次作业", "published", "", "2025-09-11", "2025-09-20", "manual", 30),
            (203, "第三次作业", "published", "", "2025-09-21", "2025-09-30", "manual", 30),
            (204, "阶段测评", "published", "9", "2025-10-01", "2025-10-10", "manual", 30),
        ]
        self.conn.executemany("INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", assignments)
        submissions = [
            (1, "201", 101, 91),
            (2, "202", 101, 92),
            (3, "203", 101, 93),
            (4, "204", 101, 88),
            (5, "201", 102, 81),
            (6, "202", 102, 82),
            (7, "203", 102, 83),
            (8, "204", 102, 78),
            (9, "201", 103, 71),
        ]
        self.conn.executemany("INSERT INTO submissions VALUES (?, ?, ?, ?)", submissions)
        self.conn.executemany(
            "INSERT INTO smart_classroom_checkin_sessions VALUES (?, ?, ?, ?, ?, ?)",
            [
                (301, 1, 30, 1, "2025-09-01 08:00:00", "2025-09-01 09:00:00"),
                (302, 1, 30, 2, "2025-09-08 08:00:00", "2025-09-08 09:00:00"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO smart_classroom_checkin_students VALUES (?, ?, ?, ?)",
            [
                (401, 301, 101, "CHECKED"),
                (402, 301, 102, "CHECKED"),
                (403, 302, 101, "CHECKED"),
                (404, 302, 102, "UNCHECKED"),
            ],
        )
        self.conn.commit()

    def test_source_validation_rejects_wrong_count_and_overlap(self):
        with self.assertRaises(HTTPException):
            validate_ordinary_grade_sources(homework_assignment_ids=[201, 202], assessment_assignment_id=204)
        with self.assertRaises(HTTPException):
            validate_ordinary_grade_sources(homework_assignment_ids=[201, 202, 203], assessment_assignment_id=202)

    def test_candidates_and_payload_use_classroom_scores(self):
        candidates = list_ordinary_grade_assignment_candidates(self.conn, class_offering_id=30, teacher_id=1)
        self.assertEqual([item["id"] for item in candidates], [201, 202, 203, 204])
        self.assertEqual(candidates[0]["graded_count"], 3)
        self.assertEqual(candidates[3]["kind"], "exam")

        payload = build_ordinary_grade_record_payload(
            self.conn,
            class_offering_id=30,
            teacher_id=1,
            homework_assignment_ids=[201, 202, 203],
            assessment_assignment_id=204,
        )

        self.assertEqual(payload["document_type"], ORDINARY_GRADE_RECORD_TYPE)
        self.assertEqual(payload["fields"]["course_name"], "服务器配置与管理")
        self.assertEqual(payload["fields"]["class_size"], 3)
        students = payload["structured"]["students"]
        self.assertEqual(students[0]["attendance_raw_score"], 100.0)
        self.assertEqual(students[1]["attendance_raw_score"], 50.0)
        self.assertEqual(students[0]["homework_scores"], [91.0, 92.0, 93.0])
        self.assertEqual(students[0]["assessment_score"], 88.0)
        self.assertTrue(any("学生三" in warning for warning in payload["structured"]["warnings"]))

    def test_xlsx_export_preserves_pages_headers_notes_and_formulas(self):
        students = []
        for index in range(1, 46):
            students.append(
                {
                    "index": index,
                    "student_number": f"2024{index:04d}",
                    "student_name": f"学生{index}",
                    "attendance_raw_score": 100,
                    "homework_scores": [80 + index % 5, 81 + index % 5, 82 + index % 5],
                    "assessment_score": 90,
                }
            )
        payload = normalize_ordinary_grade_record_payload(
            metadata={
                "college": "数字科技学院",
                "course_name": "服务器配置与管理",
                "course_hours": 48,
                "credits": 3.0,
                "teacher_name": "张海林",
                "class_name": "软工2406班（专升本）",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "class_size": 45,
            },
            content_markdown="",
            tables=[],
            export_payload={"structured": {"students": students}},
        )
        content = build_ordinary_grade_record_xlsx(payload)
        wb = load_workbook(io.BytesIO(content), data_only=False)
        ws = wb.active

        self.assertEqual(ws["A1"].value, "广西外国语学院学生平时成绩记录表")
        self.assertEqual(ws["A38"].value, "广西外国语学院学生平时成绩记录表")
        self.assertIn("A1:L1", [str(item) for item in ws.merged_cells.ranges])
        self.assertEqual(ws["I7"].value, "=D7")
        self.assertEqual(ws["J7"].value, "=AVERAGE(E7:G7)")
        self.assertEqual(ws["K7"].value, "=H7")
        self.assertEqual(ws["L7"].value, "=I7*0.4+J7*0.3+K7*0.3")
        self.assertEqual(ws["I44"].value, "=D44")
        self.assertIn("该表可为电子表格", str(ws["A69"].value))
        self.assertEqual(str(ws.page_setup.paperSize), "9")
        self.assertEqual(ws.page_setup.fitToWidth, 1)
        self.assertEqual(len(ws.row_breaks.brk), 1)

    def test_parser_and_ai_import_path_recognize_excel_formulas_without_ai(self):
        payload = build_ordinary_grade_record_payload(
            self.conn,
            class_offering_id=30,
            teacher_id=1,
            homework_assignment_ids=[201, 202, 203],
            assessment_assignment_id=204,
        )
        content = build_ordinary_grade_record_xlsx(payload)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as temp:
            temp.write(content)
            temp_path = Path(temp.name)
        try:
            parsed = parse_ordinary_grade_record_file(temp_path, "平时成绩记录表.xlsx")
            self.assertEqual(parsed.formula_count, 12)
            self.assertEqual(len(parsed.export_payload["structured"]["students"]), 3)

            async def fail_ai_chat(*args, **kwargs):  # pragma: no cover - must not be called
                raise AssertionError("ordinary grade import must use the local Excel parser")

            result = asyncio.run(
                parse_material_document(
                    file_path=temp_path,
                    original_name="平时成绩记录表.xlsx",
                    document_group="final_material",
                    document_type=ORDINARY_GRADE_RECORD_TYPE,
                    ai_chat=fail_ai_chat,
                )
            )
            self.assertFalse(result.ai_used)
            self.assertEqual(result.document_type, ORDINARY_GRADE_RECORD_TYPE)
            self.assertEqual(result.extraction_method, "ordinary_grade_excel_formula_parser")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_export_artifact_forces_xlsx_even_if_docx_requested(self):
        payload = build_ordinary_grade_record_payload(
            self.conn,
            class_offering_id=30,
            teacher_id=1,
            homework_assignment_ids=[201, 202, 203],
            assessment_assignment_id=204,
        )
        artifact = build_material_export_artifact(payload, fallback_filename="ordinary", requested_format="docx")
        self.assertEqual(artifact.media_type, XLSX_MEDIA_TYPE)
        self.assertTrue(artifact.filename.endswith(".xlsx"))
        self.assertGreater(len(artifact.content), 6000)


if __name__ == "__main__":
    unittest.main()
