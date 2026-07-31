from __future__ import annotations

import sqlite3
import unittest
from io import BytesIO

from docx import Document

from classroom_app.db import schema_academic_final_materials
from classroom_app.services.academic_final_material_document_service import (
    build_exam_analysis_docx,
    build_grade_register_docx,
)
from classroom_app.services.academic_final_material_service import (
    ACADEMIC_EXAM_ANALYSIS_TYPE,
    ACADEMIC_GRADE_REGISTER_TYPE,
    build_exam_analysis_export_payload,
    build_grade_register_export_payload,
    is_grade_entry_submitted,
    parse_exam_analysis_rtf,
    parse_grade_register_rtf,
    upsert_batch_state,
    validate_paired_reports,
)
from classroom_app.services.manage_nav_service import build_manage_nav
from classroom_app.services.material_ai_import_service import resolve_material_ai_import_type
from classroom_app.services.material_export_template_service import build_material_export_artifact


def _rtf_bytes(text: str) -> bytes:
    parts = [r"{\rtf1\ansi\uc1 "]
    for char in text:
        if char == "\n":
            parts.append(r"\par ")
        elif char == "\t":
            parts.append(r"\tab ")
        elif char in r"\{}":
            parts.append("\\" + char)
        elif ord(char) < 128:
            parts.append(char)
        else:
            codepoint = ord(char)
            signed = codepoint if codepoint < 32768 else codepoint - 65536
            parts.append(rf"\u{signed}?")
    parts.append("}")
    return "".join(parts).encode("ascii")


GRADE_TEXT = """广西外国语学院期末成绩登记表
2025-2026学年第2学期
开课部门\t软件工程系\t班级\t软工2303班\t任课教师\t张老师\t学分\t2.0
课程名称\t计算机网络实验\t课程性质\t实践教学\t考核方式\t考查\t填表日期\t2026-07-31
学号\t姓名\t平时\t期中\t实验在线\t期末\t总评\t备注\t学号\t姓名\t平时\t期中\t实验在线\t期末\t总评\t备注
2300000001\t学生甲\t100\t\t\t80\t88\t\t2300000002\t学生乙\t100\t\t\t100\t100\t
教师：
总评成绩 = 平时*40% + 期末*60%"""


ANALYSIS_TEXT = """广西外国语学院课程试卷分析表
2025-2026学年第2学期
课程名称\t计算机网络实验\t学时数\t32\t开课单位\t软件工程系
教师姓名\t张老师
学生班级\t软工2303班
人数\t0\t0\t0\t1\t1
比例\t0.00\t0.00\t0.00\t50.00\t50.00
平均分\t90.00\t标准差\t14.14
最高分\t100\t最低分\t80\t及格率\t100.00"""


class AcademicFinalMaterialServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grade = parse_grade_register_rtf(_rtf_bytes(GRADE_TEXT))
        self.analysis = parse_exam_analysis_rtf(_rtf_bytes(ANALYSIS_TEXT))
        self.validation = validate_paired_reports(
            self.grade,
            self.analysis,
            remote_student_count=2,
        )

    def test_paired_parser_and_deterministic_validator_pass(self) -> None:
        self.assertEqual(2, len(self.grade["students"]))
        self.assertEqual("计算机网络实验", self.grade["fields"]["course_name"])
        self.assertTrue(self.validation["passed"], self.validation["errors"])
        self.assertEqual([0, 0, 0, 1, 1], self.validation["computed"]["distribution_counts"])
        self.assertEqual(14.14, self.validation["computed"]["standard_deviation"])

    def test_validator_blocks_modified_analysis_statistics(self) -> None:
        self.analysis["statistics"]["average"] = 99.99
        validation = validate_paired_reports(self.grade, self.analysis, remote_student_count=2)
        self.assertFalse(validation["passed"])
        check = next(item for item in validation["checks"] if item["key"] == "statistics_average")
        self.assertFalse(check["ok"])
        self.assertIn("平均分", validation["errors"][0])

    def test_grade_entry_status_does_not_treat_negative_labels_as_submitted(self) -> None:
        for status in ("未提交", "未录入", "待提交", "未完成"):
            self.assertFalse(is_grade_entry_submitted(status), status)
        for status in ("已提交", "已录入", "已完成"):
            self.assertTrue(is_grade_entry_submitted(status), status)
        self.assertTrue(is_grade_entry_submitted("", {"cjsftj": "1"}))

    def test_both_docx_renderers_produce_openable_documents(self) -> None:
        grade_payload = build_grade_register_export_payload(self.grade, self.validation)
        analysis_payload = build_exam_analysis_export_payload(
            self.analysis,
            self.validation,
            defaults={
                "proposition_form": "教师组题",
                "exam_form": "闭卷",
                "separate_teaching_exam": "否",
                "course_nature": "必修",
                "marking_form": "本人阅卷",
                "analysis_text": "试题结构合理，覆盖核心知识与实践能力。成绩分布集中且整体掌握良好，后续将通过分层案例、课堂复盘和专项训练强化综合分析与知识迁移。",
            },
        )
        for payload, builder, title in (
            (grade_payload, build_grade_register_docx, "广西外国语学院期末成绩登记表"),
            (analysis_payload, build_exam_analysis_docx, "广西外国语学院课程试卷分析表"),
        ):
            content = builder(payload)
            self.assertTrue(content.startswith(b"PK"))
            document = Document(BytesIO(content))
            all_text = "\n".join(
                [paragraph.text for paragraph in document.paragraphs]
                + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
            )
            self.assertIn(title, all_text)
            self.assertIn("计算机网络实验", all_text)

    def test_shared_export_contract_supports_both_types(self) -> None:
        grade_payload = build_grade_register_export_payload(self.grade, self.validation)
        analysis_payload = build_exam_analysis_export_payload(self.analysis, self.validation)
        for payload, expected_type in (
            (grade_payload, ACADEMIC_GRADE_REGISTER_TYPE),
            (analysis_payload, ACADEMIC_EXAM_ANALYSIS_TYPE),
        ):
            type_meta = resolve_material_ai_import_type("final_material", expected_type)
            self.assertEqual(expected_type, type_meta["key"])
            artifact = build_material_export_artifact(
                {"export_payload": payload},
                fallback_filename="academic-final",
                requested_format="docx",
            )
            self.assertTrue(artifact.content.startswith(b"PK"))
            self.assertTrue(artifact.filename.endswith(".docx"))

    def test_navigation_exposes_dedicated_final_material_group(self) -> None:
        nav = build_manage_nav(
            {"id": 1, "role": "teacher"},
            "academic_grade_registers",
            is_super_admin=False,
        )
        groups = {
            group["label"]: group
            for domain in nav["domains"]
            for group in domain["groups"]
        }
        self.assertIn("期末材料", groups)
        keys = {item["key"] for item in groups["期末材料"]["items"]}
        self.assertEqual({"academic_grade_registers", "academic_exam_analyses"}, keys)

    def test_schema_is_idempotent_and_enforces_one_batch_per_class(self) -> None:
        schema_academic_final_materials._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        try:
            schema_academic_final_materials.ensure_academic_final_material_schema(conn)
            schema_academic_final_materials.ensure_academic_final_material_schema(conn)
            conn.execute(
                """
                INSERT INTO academic_final_material_batches
                (id, teacher_id, class_offering_id)
                VALUES ('one', 1, 8)
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO academic_final_material_batches
                    (id, teacher_id, class_offering_id)
                    VALUES ('two', 1, 8)
                    """
                )
        finally:
            conn.close()
            schema_academic_final_materials._SCHEMA_READY = False

    def test_batch_upsert_is_idempotent_and_preserves_unspecified_state(self) -> None:
        schema_academic_final_materials._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            first = upsert_batch_state(
                conn,
                teacher_id=3,
                class_offering_id=9,
                values={"sync_status": "running", "course_name": "计算机网络"},
            )
            second = upsert_batch_state(
                conn,
                teacher_id=3,
                class_offering_id=9,
                values={"sync_status": "completed", "validation_status": "passed"},
            )
            self.assertEqual(first["id"], second["id"])
            self.assertEqual("计算机网络", second["course_name"])
            self.assertEqual("completed", second["sync_status"])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM academic_final_material_batches").fetchone()[0])
        finally:
            conn.close()
            schema_academic_final_materials._SCHEMA_READY = False


if __name__ == "__main__":
    unittest.main()
