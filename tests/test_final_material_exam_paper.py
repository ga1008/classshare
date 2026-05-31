import unittest

from classroom_app.services.material_export_template_service import build_material_export_artifact
from classroom_app.services.material_final_document_service import (
    build_final_material_generation_seed,
    normalize_final_material_payload,
)


class FinalMaterialExamPaperTests(unittest.TestCase):
    def test_seed_builds_exam_paper_from_assessment_plan(self):
        seed = build_final_material_generation_seed(
            document_type="exam_paper",
            classroom_context={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406-2408班（专升本）",
                "teacher_name": "张海林",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "source_assessment_plan": {
                    "record_id": 12,
                    "title": "服务器配置与管理课程考核计划表",
                    "updated_at": "2026-05-31T10:00:00",
                    "structured": {
                        "assessment_items": [
                            {
                                "assessment_form": "机试",
                                "content": "Linux 用户与目录管理",
                                "score": "24",
                            },
                            {
                                "assessment_form": "机试",
                                "content": "Web 服务部署与配置",
                                "score": "76",
                            },
                        ]
                    },
                },
            },
            prompt="要求截图编号从10.png开始，提交zip压缩包。",
        )

        payload = seed["export_payload"]
        fields = payload["fields"]
        structured = payload["structured"]

        self.assertEqual(payload["template_key"], "exam_paper")
        self.assertEqual(fields["source_assessment_plan_record_id"], 12)
        self.assertEqual(fields["source_assessment_plan_title"], "服务器配置与管理课程考核计划表")
        self.assertEqual(fields["paper_type"], "开卷")
        self.assertEqual(structured["total_score"], 100.0)
        self.assertFalse(structured["requires_assessment_plan_confirmation"])
        self.assertEqual([item["score"] for item in structured["paper_sections"]], ["24", "76"])
        self.assertEqual(structured["score_table"]["scores"], ["24", "76"])
        self.assertIn("Linux 用户与目录管理", structured["paper_sections"][0]["content"])

    def test_normalize_imported_exam_paper_preserves_dynamic_fields_and_tasks(self):
        payload = normalize_final_material_payload(
            document_type="exam_paper",
            metadata={
                "course_name": "动态Web程序设计",
                "class_name": "网工2403班（专升本）",
                "teacher_name": "李老师",
                "academic_year": "2024-2025",
                "semester": "第一学期",
                "exam_duration": "120",
                "paper_type": "开卷",
                "source_assessment_plan": {
                    "record_id": 5,
                    "title": "动态Web程序设计考核计划表",
                    "updated_at": "2026-05-30T08:00:00",
                },
            },
            content_markdown=(
                "一、基础功能实现（共30分）\n"
                "1. 创建数据库表并完成用户登录页面。\n"
                "2. 截图保存为 10.png。\n"
                "3. 提交 班级-学号-姓名.zip。\n\n"
                "二、综合项目部署（共70分）\n"
                "1. 完成项目路由、模板和数据库连接。\n"
                "SELECT * FROM users;\n"
                "2. 截图保存为 20.png。"
            ),
            tables=[],
            export_payload={},
        )

        structured = payload["structured"]
        fields = payload["fields"]

        self.assertEqual(fields["course_name"], "动态Web程序设计")
        self.assertEqual(fields["source_assessment_plan_record_id"], 5)
        self.assertFalse(structured["requires_assessment_plan_confirmation"])
        self.assertEqual(structured["total_score"], 100.0)
        self.assertEqual(len(structured["paper_sections"]), 2)
        self.assertTrue(any("10.png" in item for item in structured["screenshot_requirements"]))
        self.assertTrue(any("zip" in item.lower() for item in structured["submission_requirements"]))
        self.assertTrue(any("SELECT" in item for item in structured["command_blocks"]))

    def test_exam_paper_export_builds_docx(self):
        seed = build_final_material_generation_seed(
            document_type="exam_paper",
            classroom_context={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406班（专升本）",
                "teacher_name": "张海林",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "source_assessment_plan": {
                    "record_id": 1,
                    "title": "课程考核计划表",
                    "structured": {
                        "assessment_items": [
                            {"assessment_form": "机试", "content": "Linux 用户管理", "score": "30"},
                            {"assessment_form": "机试", "content": "Staging Web 服务部署", "score": "70"},
                        ]
                    },
                },
            },
            prompt="",
        )

        artifact = build_material_export_artifact(
            seed["export_payload"],
            fallback_filename="exam-paper",
            requested_format="docx",
        )

        self.assertEqual(
            artifact.media_type,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertTrue(artifact.filename.endswith(".docx"))
        self.assertGreater(len(artifact.content), 25000)


if __name__ == "__main__":
    unittest.main()
