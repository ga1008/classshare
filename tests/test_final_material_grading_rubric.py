import unittest

from classroom_app.services.material_export_template_service import build_material_export_artifact
from classroom_app.services.material_final_document_service import (
    SCORING_RUBRIC_NOTES,
    build_final_material_generation_seed,
    normalize_final_material_payload,
)


class FinalMaterialGradingRubricTests(unittest.TestCase):
    def test_seed_builds_rubric_from_source_exam_paper(self):
        seed = build_final_material_generation_seed(
            document_type="grading_rubric",
            classroom_context={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406-2408班（专升本）",
                "teacher_name": "张海林",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "source_exam_paper": {
                    "record_id": 7,
                    "title": "服务器配置与管理课程考核试卷",
                    "updated_at": "2026-05-31T09:00:00",
                    "structured": {
                        "paper_sections": [
                            {
                                "title": "一、第一大题：基础环境配置（共30分）",
                                "score": "30",
                                "content": "运维账户创建，对应截图 10.png；日志备份目录配置，对应截图 11.png。",
                            },
                            {
                                "title": "二、第二大题：Staging 环境部署（共70分）",
                                "score": "70",
                                "content": "部署 Web 服务，对应截图 20.png；数据库授权，对应截图 22.png。",
                            },
                        ]
                    },
                },
            },
            prompt="突出截图编号一致性",
        )

        structured = seed["export_payload"]["structured"]
        fields = seed["export_payload"]["fields"]
        self.assertEqual(fields["source_exam_paper_record_id"], 7)
        self.assertEqual(fields["source_exam_paper_title"], "服务器配置与管理课程考核试卷")
        self.assertEqual(structured["total_score"], 100.0)
        self.assertFalse(structured["requires_exam_paper_confirmation"])
        self.assertEqual(len(structured["rubric_items"]), 2)
        self.assertIn("截图10.png", structured["rubric_body_markdown"])
        self.assertEqual(structured["notes"], SCORING_RUBRIC_NOTES)

    def test_normalize_imported_rubric_preserves_template_fields(self):
        payload = normalize_final_material_payload(
            document_type="grading_rubric",
            metadata={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406-2408班（专升本）",
                "examiner_name": "张海林",
                "assessment_type": "考试",
                "assessment_mode": "non_written",
                "date": "2025.10.13",
            },
            content_markdown=(
                "## 评分细则\n"
                "机试扣分项与给分原则\n"
                "【5分】脚本第一行包含学生姓名。\n"
                "【5分】截图显示 20.png 且 Web 服务状态正确。\n"
                "若权限设置错误，扣 3 分。"
            ),
            tables=[],
            export_payload={},
        )

        structured = payload["structured"]
        self.assertEqual(payload["template_key"], "grading_rubric")
        self.assertEqual(payload["fields"]["date"], "2025.10.13")
        self.assertEqual(structured["total_score"], 10.0)
        self.assertTrue(structured["requires_exam_paper_confirmation"])
        self.assertIn("若权限设置错误，扣 3 分。", structured["deduction_points"])
        self.assertTrue(any("20.png" in item for item in structured["screenshot_requirements"]))

    def test_grading_rubric_export_builds_docx(self):
        seed = build_final_material_generation_seed(
            document_type="grading_rubric",
            classroom_context={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406班",
                "teacher_name": "张海林",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "source_exam_paper": {
                    "record_id": 1,
                    "title": "课程考核试卷",
                    "structured": {
                        "paper_sections": [
                            {"title": "一、基础任务（共40分）", "score": "40", "content": "对应截图 10.png。"},
                            {"title": "二、综合任务（共60分）", "score": "60", "content": "对应截图 20.png。"},
                        ]
                    },
                },
            },
            prompt="",
        )

        artifact = build_material_export_artifact(
            seed["export_payload"],
            fallback_filename="grading-rubric",
            requested_format="docx",
        )
        self.assertEqual(artifact.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertTrue(artifact.filename.endswith(".docx"))
        self.assertGreater(len(artifact.content), 25000)


if __name__ == "__main__":
    unittest.main()
