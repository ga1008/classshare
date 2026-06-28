import io
import re
import unittest
import zipfile

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
        self.assertEqual(fields["reviewer_name"], "【系主任未填写】")
        self.assertIn("签名库", fields["reviewer_missing_notice"])
        self.assertEqual(fields["leader_name"], "【主管教学领导未填写】")
        self.assertIn("签名库", fields["leader_missing_notice"])
        self.assertFalse(structured["requires_assessment_plan_confirmation"])
        self.assertEqual(structured["total_score"], 100.0)
        self.assertEqual(len(structured["paper_sections"]), 2)
        self.assertTrue(any("10.png" in item for item in structured["screenshot_requirements"]))
        self.assertTrue(any("zip" in item.lower() for item in structured["submission_requirements"]))
        self.assertTrue(any("SELECT" in item for item in structured["command_blocks"]))

    def test_seed_inherits_exam_metadata_from_source_plan_fields(self):
        seed = build_final_material_generation_seed(
            document_type="exam_paper",
            classroom_context={
                "course_name": "服务器配置与管理",
                "class_name": "网工2401班",
                "teacher_name": "张海林",
                "source_assessment_plan": {
                    "record_id": 18,
                    "title": "服务器配置与管理课程考核计划表",
                    "fields": {
                        "reviewer_name": "阮小琴",
                        "leader_name": "黄老师",
                        "paper_volume": "B卷",
                        "paper_type": "闭卷",
                        "exam_duration": "100",
                        "education_level": "专科",
                    },
                    "structured": {
                        "assessment_items": [
                            {"assessment_form": "笔试", "content": "Linux 基础命令", "score": "40"},
                            {"assessment_form": "笔试", "content": "服务部署与排障", "score": "60"},
                        ]
                    },
                },
            },
            prompt="",
        )

        fields = seed["export_payload"]["fields"]
        self.assertEqual(fields["reviewer_name"], "阮小琴")
        self.assertEqual(fields["leader_name"], "黄老师")
        self.assertEqual(fields["paper_volume"], "B卷")
        self.assertEqual(fields["paper_type"], "闭卷")
        self.assertEqual(fields["exam_duration"], "100")
        self.assertEqual(fields["education_level"], "专科")
        self.assertEqual(fields["source_assessment_plan_record_id"], 18)

    def test_imported_exam_text_extracts_names_without_signature_ocr_duplicates(self):
        payload = normalize_final_material_payload(
            document_type="exam_paper",
            metadata={},
            content_markdown=(
                "广西外国语学院课程考核试卷 "
                "命题教师 张海林 张海林 "
                "系（教研室）主任审核签字 阮小琴 阮小琴 "
                "二级学院（部）主管教学领导 黄老师 黄老师 "
                "A卷（ ）/B卷（√） 开卷（ ）/闭卷（√） 题号 满分 总分100\n\n"
                "一、Linux 基础命令（共100分）\n完成用户、目录、权限配置任务。"
            ),
            tables=[],
            export_payload={},
        )

        fields = payload["fields"]
        self.assertEqual(fields["examiner_name"], "张海林")
        self.assertEqual(fields["teacher_name"], "张海林")
        self.assertEqual(fields["reviewer_name"], "阮小琴")
        self.assertEqual(fields["leader_name"], "黄老师")
        self.assertEqual(fields["paper_volume"], "B卷")
        self.assertEqual(fields["paper_type"], "闭卷")

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

    def test_exam_paper_docx_contains_seal_footer_and_hides_signature_paths(self):
        payload = normalize_final_material_payload(
            document_type="exam_paper",
            metadata={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406班（专升本）",
                "teacher_name": "张海林",
                "reviewer_name": "阮小琴",
                "leader_name": "黄老师",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "paper_volume": "A卷",
                "examiner_signature_image_path": "/app/data/media/signatures/private-examiner.png",
            },
            content_markdown=(
                "一、Linux 基础命令（共30分）\n完成用户、目录、权限配置任务。\n\n"
                "二、Web 服务部署（共70分）\n完成 httpd 服务安装、启动和访问测试。"
            ),
            tables=[],
            export_payload={},
        )

        artifact = build_material_export_artifact(
            payload,
            fallback_filename="exam-paper",
            requested_format="docx",
        )

        with zipfile.ZipFile(io.BytesIO(artifact.content)) as package:
            xml = "\n".join(
                package.read(name).decode("utf-8", errors="ignore")
                for name in package.namelist()
                if name.startswith("word/") and name.endswith(".xml")
            )

        self.assertIn("ExamSealLineShape", xml)
        self.assertRegex(xml, re.compile(r"密.*封.*线.*内.*不.*要.*答.*题", re.S))
        self.assertIn("广西外国语学院课程考核试卷", xml)
        self.assertIn("考试过程中不得将试卷拆开", xml)
        self.assertIn("A 卷", xml)
        self.assertIn("开卷", xml)
        self.assertIn("张海林", xml)
        self.assertNotIn("/app/data/media/signatures/private-examiner.png", xml)


if __name__ == "__main__":
    unittest.main()
