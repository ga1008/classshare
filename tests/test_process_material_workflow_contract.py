import re
import unittest
from pathlib import Path

from classroom_app.routers.materials_parts.final_material_helpers import (
    _build_manage_final_material_context,
)
from classroom_app.routers.materials_parts.library import (
    GRADE_RECORD_GENERATE_BLOCKERS,
    GRADE_RECORD_IMPORT_PRESETS,
)
from classroom_app.services.material_ai_import_service import get_material_ai_import_registry


class ProcessMaterialWorkflowContractTests(unittest.TestCase):
    def _final_import_types(self):
        registry = get_material_ai_import_registry()
        final = next(group for group in registry if group["key"] == "final_material")
        return {item["key"] for item in final["types"]}

    def test_grade_record_import_types_are_available_in_registry(self):
        self.assertTrue(
            {
                "assessment_plan",
                "grading_rubric",
                "ordinary_grade_record",
                "exam_grade_record",
            }.issubset(self._final_import_types())
        )

    def test_manage_generic_generation_does_not_expose_grade_records(self):
        html = Path("templates/manage/materials.html").read_text(encoding="utf-8")
        match = re.search(r'<select id="materials-ai-generate-type".*?</select>', html, re.S)
        self.assertIsNotNone(match)
        select_html = match.group(0)

        self.assertIn('value="assessment_plan"', select_html)
        self.assertIn('value="exam_paper"', select_html)
        self.assertIn('value="grading_rubric"', select_html)
        self.assertNotIn('value="ordinary_grade_record"', select_html)
        self.assertNotIn('value="exam_grade_record"', select_html)

    def test_grade_record_pages_preselect_import_and_block_generic_generation(self):
        for key in ("ordinary_grade_record", "exam_grade_record"):
            self.assertEqual(GRADE_RECORD_IMPORT_PRESETS[key]["document_group"], "final_material")
            self.assertEqual(GRADE_RECORD_IMPORT_PRESETS[key]["document_type"], key)
            self.assertTrue(GRADE_RECORD_GENERATE_BLOCKERS[key]["blocked"])
            self.assertEqual(GRADE_RECORD_GENERATE_BLOCKERS[key]["document_type"], key)
            self.assertIn("Excel", GRADE_RECORD_GENERATE_BLOCKERS[key]["status"])

    def test_manage_ai_generate_button_reuses_process_material_preset(self):
        script = Path("static/js/materials_manage.js").read_text(encoding="utf-8")
        self.assertIn("const initialAiGeneratePreset = getInitialAiGeneratePreset();", script)
        self.assertIn("openAiGenerateModal(initialAiGeneratePreset);", script)

    def test_grading_rubric_menu_does_not_auto_open_generate_modal(self):
        source = Path("classroom_app/routers/materials_parts/library.py").read_text(encoding="utf-8")
        match = re.search(
            r"manage_grading_rubrics_page[\s\S]+?initial_ai_generate=\{(?P<preset>[\s\S]+?)\}\s*,\s*\)",
            source,
        )
        self.assertIsNotNone(match)
        preset = match.group("preset")
        self.assertIn('"document_type": "grading_rubric"', preset)
        self.assertNotIn('"open": True', preset)

    def test_grading_rubric_manage_context_requires_exam_questions(self):
        weak_context = _build_manage_final_material_context(
            document_type="grading_rubric",
            prompt="",
            parent_context=None,
            attachments=[
                {
                    "title": "课程说明",
                    "content": "本课程主要讲授 Web 后端开发基础、数据库访问与项目部署。",
                }
            ],
        )
        self.assertNotIn("source_exam_paper", weak_context)

        concrete_context = _build_manage_final_material_context(
            document_type="grading_rubric",
            prompt="",
            parent_context=None,
            attachments=[
                {
                    "title": "课程考核试卷",
                    "content": "课程考核试卷\n第一题、基础环境配置（共40分）：完成账号创建并提交截图10.png。",
                }
            ],
        )
        self.assertIn("source_exam_paper", concrete_context)
        self.assertIn("截图10.png", concrete_context["source_exam_paper"]["content_markdown"])

    def test_exam_paper_manage_context_detects_assessment_plan_attachment(self):
        context = _build_manage_final_material_context(
            document_type="exam_paper",
            prompt="",
            parent_context=None,
            attachments=[
                {
                    "title": "课程考核计划表",
                    "metadata": {"document_type": "assessment_plan"},
                    "content": "课程考核计划表\n考核形式：机试\n考核技能/内容：环境部署，分值60；综合应用，分值40。",
                }
            ],
        )
        self.assertIn("source_assessment_plan", context)
        self.assertFalse(context.get("requires_assessment_plan_confirmation"))

    def test_process_material_workflow_doc_covers_all_menu_items(self):
        doc = Path("docs/process-material-workflow-coverage.md").read_text(encoding="utf-8")
        for label in ("考核计划表", "评分细则表", "平时成绩表", "考核登分表", "教师评学表"):
            self.assertIn(label, doc)


if __name__ == "__main__":
    unittest.main()
