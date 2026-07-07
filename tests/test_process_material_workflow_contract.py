import re
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
