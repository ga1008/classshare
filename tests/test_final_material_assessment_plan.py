import io
import json
import unittest
import zipfile
import xml.etree.ElementTree as ET

from classroom_app.services.material_ai_import_service import (
    MaterialExtraction,
    normalize_ai_parse_result,
    resolve_material_ai_import_type,
)
from classroom_app.services.material_export_template_service import build_material_export_artifact
from classroom_app.services.material_final_document_service import (
    ASSESSMENT_PLAN_NOTES,
    ASSESSMENT_PLAN_SCHEMA_VERSION,
    FINAL_MATERIAL_LAYOUTS,
    build_final_material_generation_seed,
    normalize_final_material_payload,
)


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{%s}" % NS["w"]

COURSE_NAME = "\u52a8\u6001web\u7a0b\u5e8f\u8bbe\u8ba1"
CLASS_NAME = "\u8f6f\u5de52401\u73ed"
TEACHER_NAME = "\u5f20\u6d77\u6797"
COLLEGE = "\u6570\u5b57\u79d1\u6280\u5b66\u9662"
DEPARTMENT = "\u8f6f\u4ef6\u5de5\u7a0b\u7cfb"
EXAM_TYPE = "\u8003\u8bd5"
CHECK_TYPE = "\u8003\u67e5"
NON_WRITTEN = "\u975e\u7b14\u8bd5\u8003\u6838"
PRACTICAL_METHOD = "\u9879\u76ee\u5b9e\u64cd"
MACHINE_TEST = "\u673a\u8bd5"
DATE_TEXT = "2026\u5e746\u670826\u65e5"
SERVER_COURSE_NAME = "\u670d\u52a1\u5668\u914d\u7f6e\u4e0e\u7ba1\u7406"
REVIEWER_NAME = "\u962e\u5c0f\u7434"

ASSESSMENT_ROWS = [
    ["\u8003\u6838\u5f62\u5f0f", "\u8003\u6838\u6280\u80fd/\u5185\u5bb9", "\u5206\u503c"],
    [MACHINE_TEST, "Spring MVC controller and layered development", "40"],
    [MACHINE_TEST, "MyBatis data access and transaction processing", "35"],
    [MACHINE_TEST, "Spring Boot integrated project deployment", "25"],
]

ASSESSMENT_MARKDOWN = "\n".join(
    [
        "## assessment items",
        "| form | content | score |",
        "| --- | --- | --- |",
        "| machine test | Spring MVC controller and layered development | 40 |",
        "| machine test | MyBatis data access and transaction processing | 35 |",
        "| machine test | Spring Boot integrated project deployment | 25 |",
    ]
)


def _attr(element, name):
    return None if element is None else element.get(W + name)


def _text(element):
    return "".join(node.text or "" for node in element.findall(".//w:t", NS))


class FinalMaterialAssessmentPlanTests(unittest.TestCase):
    def _build_doc_root(self):
        seed = build_final_material_generation_seed(
            document_type="assessment_plan",
            classroom_context={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406、2407、2408班（专升本）",
                "teacher_name": "张海林",
                "academic_year": "2025-2026",
                "semester": "第一学期",
            },
            prompt="",
        )
        seed["export_payload"]["fields"]["reviewer_name"] = "阮小琴"
        seed["export_payload"]["fields"]["date"] = "2025年10月13日"
        artifact = build_material_export_artifact(
            seed["export_payload"],
            fallback_filename="assessment-plan",
            requested_format="docx",
        )
        self.assertTrue(artifact.filename.endswith(".docx"))
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as docx:
            return ET.fromstring(docx.read("word/document.xml"))

    def test_import_normalization_extracts_items_notes_and_queryable_fields(self):
        payload = normalize_final_material_payload(
            document_type="assessment_plan",
            metadata={
                "course_name": COURSE_NAME,
                "class_name": CLASS_NAME,
                "teacher_name": TEACHER_NAME,
                "academic_year": "2025-2026",
                "semester": "\u7b2c\u4e8c\u5b66\u671f",
                "assessment_type": EXAM_TYPE,
                "assessment_method": MACHINE_TEST,
                "date": DATE_TEXT,
            },
            content_markdown=ASSESSMENT_MARKDOWN,
            tables=[{"title": "assessment table", "rows": ASSESSMENT_ROWS}],
            classroom_context={"college": COLLEGE, "department": DEPARTMENT},
        )

        self.assertEqual(payload["document_group"], "final_material")
        self.assertEqual(payload["document_type"], "assessment_plan")
        self.assertEqual(payload["template_key"], "assessment_plan")
        fields = payload["fields"]
        self.assertEqual(fields["course_name"], COURSE_NAME)
        self.assertEqual(fields["class_name"], CLASS_NAME)
        self.assertEqual(fields["teacher_name"], TEACHER_NAME)
        self.assertEqual(fields["college"], COLLEGE)
        self.assertEqual(fields["department"], DEPARTMENT)
        self.assertEqual(fields["assessment_type"], EXAM_TYPE)
        self.assertEqual(fields["assessment_method"], MACHINE_TEST)
        self.assertEqual(fields["date"], DATE_TEXT)

        structured = payload["structured"]
        self.assertEqual(structured["template_schema_version"], ASSESSMENT_PLAN_SCHEMA_VERSION)
        self.assertEqual(structured["notes"], ASSESSMENT_PLAN_NOTES)
        self.assertEqual(structured["total_score"], 100.0)
        self.assertEqual([item["score"] for item in structured["assessment_items"]], ["40", "35", "25"])
        self.assertEqual(structured["assessment_items"][0]["content"], "Spring MVC controller and layered development")
        self.assertEqual(payload["queryable_fields"]["assessment_items"], structured["assessment_items"])
        self.assertEqual(payload["queryable_fields"]["total_score"], 100.0)

    def test_docx_fallback_prefers_template_tables_over_note_text(self):
        wrong_note_course_name = "\u5fc5\u987b\u4e0e\u6559\u5b66\u8ba1\u5212\u4e0a\u7684\u540d\u79f0\u4e00\u81f4\u3002 2"
        checked_exam = "\u8003\u67e5( ) / \u8003\u8bd5( \u221a )"
        payload = normalize_final_material_payload(
            document_type="assessment_plan",
            metadata={},
            content_markdown=(
                "\u6ce8\uff1a\n"
                "1\uff0e\u8bfe\u7a0b\u540d\u79f0\u5fc5\u987b\u4e0e\u6559\u5b66\u8ba1\u5212\u4e0a\u7684\u540d\u79f0\u4e00\u81f4\u3002\n"
                "2\uff0e\u8003\u6838\u7c7b\u578b\uff1a\u8003\u67e5\u3001\u8003\u8bd5\uff08\u6309\u6559\u5b66\u8ba1\u5212\u586b\u5199\uff09\u3002"
            ),
            tables=[
                {
                    "title": "metadata",
                    "rows": [
                        ["\u8bfe\u7a0b\u540d\u79f0", SERVER_COURSE_NAME, "", ""],
                        ["\u4e13\u4e1a \u5e74\u7ea7\u73ed\u7ea7", CLASS_NAME, "\u8003\u6838\u7c7b\u578b", checked_exam],
                        [
                            "\u547d\u9898\u6559\u5e08",
                            TEACHER_NAME,
                            "\u7cfb\uff08\u6559\u7814\u5ba4\uff09 \u4e3b\u4efb\u5ba1\u6838\u7b7e\u5b57",
                            REVIEWER_NAME,
                        ],
                        ["\u547d\u9898\u65e5\u671f", DATE_TEXT, "", ""],
                    ],
                },
                {"title": "assessment table", "rows": ASSESSMENT_ROWS},
            ],
            export_payload={
                "fields": {
                    "course_name": wrong_note_course_name,
                    "assessment_type": checked_exam,
                },
                "structured": {
                    "assessment_items": [
                        {"assessment_form": MACHINE_TEST, "content": "stale fallback item", "score": "100"}
                    ]
                },
            },
        )

        fields = payload["fields"]
        self.assertEqual(fields["course_name"], SERVER_COURSE_NAME)
        self.assertEqual(fields["assessment_type"], EXAM_TYPE)
        self.assertEqual(fields["class_name"], CLASS_NAME)
        self.assertEqual(fields["teacher_name"], TEACHER_NAME)
        self.assertEqual(fields["reviewer_name"], REVIEWER_NAME)
        self.assertEqual(payload["structured"]["total_score"], 100.0)
        self.assertEqual([item["score"] for item in payload["structured"]["assessment_items"]], ["40", "35", "25"])

    def test_ai_parse_result_accepts_fenced_json_and_builds_export_payload(self):
        raw_payload = {
            "metadata": {
                "course_name": COURSE_NAME,
                "class_name": CLASS_NAME,
                "teacher_name": TEACHER_NAME,
                "academic_year": "2025-2026",
                "semester": "\u7b2c\u4e8c\u5b66\u671f",
                "assessment_type": EXAM_TYPE,
                "assessment_method": MACHINE_TEST,
                "date": DATE_TEXT,
            },
            "content_markdown": ASSESSMENT_MARKDOWN,
            "tables": [{"title": "assessment table", "rows": ASSESSMENT_ROWS}],
            "warnings": ["teacher should review scores"],
            "export_payload": {
                "template_key": "assessment_plan",
                "fields": {"reviewer_name": "\u5f85\u586b\u5199"},
            },
        }
        fenced_json = "```json\n" + json.dumps(raw_payload, ensure_ascii=False) + "\n```"

        result = normalize_ai_parse_result(
            fenced_json,
            original_name="\u8bfe\u7a0b\u8003\u6838\u8ba1\u5212\u8868.docx",
            type_meta=resolve_material_ai_import_type("final_material", "assessment_plan"),
            extraction=MaterialExtraction(
                text="local extracted assessment plan text",
                method="python_docx_tables",
                source_kind="docx",
                quality={"usable": True},
            ),
            extra_warnings=["local warning"],
            ai_used=True,
        )

        self.assertTrue(result.ai_used)
        self.assertEqual(result.document_group, "final_material")
        self.assertEqual(result.document_type, "assessment_plan")
        self.assertIn("local warning", result.warnings)
        self.assertIn("teacher should review scores", result.warnings)
        self.assertEqual(result.metadata["source_filename"], "\u8bfe\u7a0b\u8003\u6838\u8ba1\u5212\u8868.docx")
        self.assertEqual(result.metadata["course_name"], COURSE_NAME)
        export_payload = result.export_payload
        self.assertEqual(export_payload["template_key"], "assessment_plan")
        self.assertEqual(export_payload["document_group"], "final_material")
        self.assertEqual(export_payload["document_type"], "assessment_plan")
        self.assertEqual(export_payload["fields"]["reviewer_name"], "\u5f85\u586b\u5199")
        self.assertEqual(len(export_payload["structured"]["assessment_items"]), 3)
        self.assertEqual(export_payload["structured"]["total_score"], 100.0)
        self.assertEqual(export_payload["structured"]["template_schema_version"], ASSESSMENT_PLAN_SCHEMA_VERSION)

    def test_generation_seed_carries_classroom_context_and_assessment_options(self):
        seed = build_final_material_generation_seed(
            document_type="assessment_plan",
            classroom_context={
                "course_name": COURSE_NAME,
                "class_name": CLASS_NAME,
                "teacher_name": TEACHER_NAME,
                "academic_year": "2025-2026",
                "semester": "\u7b2c\u4e8c\u5b66\u671f",
                "college": COLLEGE,
                "department": DEPARTMENT,
                "academic_exam_method": CHECK_TYPE,
                "academic_exam_mode": NON_WRITTEN,
                "assessment_method": PRACTICAL_METHOD,
            },
            prompt="focus on Spring Boot project delivery",
        )

        fields = seed["export_payload"]["fields"]
        structured = seed["export_payload"]["structured"]
        self.assertEqual(fields["course_name"], COURSE_NAME)
        self.assertEqual(fields["class_name"], CLASS_NAME)
        self.assertEqual(fields["teacher_name"], TEACHER_NAME)
        self.assertEqual(fields["college"], COLLEGE)
        self.assertEqual(fields["department"], DEPARTMENT)
        self.assertEqual(fields["assessment_type"], CHECK_TYPE)
        self.assertEqual(fields["assessment_mode"], "non_written")
        self.assertEqual(fields["assessment_mode_label"], NON_WRITTEN)
        self.assertEqual(fields["assessment_method"], PRACTICAL_METHOD)
        self.assertEqual(structured["notes"], ASSESSMENT_PLAN_NOTES)
        self.assertEqual(structured["template_schema_version"], ASSESSMENT_PLAN_SCHEMA_VERSION)
        self.assertTrue(structured["assessment_items"])

    def test_layout_profile_uses_reference_template_metrics(self):
        layout = FINAL_MATERIAL_LAYOUTS["assessment_plan"]
        self.assertEqual(layout["margins_twips"], {"top": 851, "bottom": 851, "left": 851, "right": 708, "footer": 992})
        self.assertEqual(layout["metadata_table_grid_twips"], [2628, 2442, 2409, 2864])
        self.assertEqual(layout["assessment_table_grid_twips"], [2628, 5731, 1984])
        self.assertEqual(layout["metadata_row_heights_twips"], [626, 629, 629, 624])
        self.assertEqual(layout["assessment_header_height_twips"], 652)
        self.assertEqual(layout["assessment_body_height_twips"], 1134)

    def test_export_docx_matches_reference_template_geometry(self):
        root = self._build_doc_root()
        sect = root.find("w:body/w:sectPr", NS)
        pg_sz = sect.find("w:pgSz", NS)
        pg_mar = sect.find("w:pgMar", NS)
        doc_grid = sect.find("w:docGrid", NS)
        self.assertEqual((_attr(pg_sz, "w"), _attr(pg_sz, "h")), ("11907", "16839"))
        self.assertEqual(
            {key: _attr(pg_mar, key) for key in ("top", "right", "bottom", "left", "header", "footer", "gutter")},
            {"top": "851", "right": "708", "bottom": "851", "left": "851", "header": "851", "footer": "992", "gutter": "0"},
        )
        self.assertEqual((_attr(doc_grid, "type"), _attr(doc_grid, "linePitch")), ("lines", "312"))

        tables = root.findall(".//w:tbl", NS)
        self.assertEqual(len(tables), 2)
        self._assert_table(tables[0], [2628, 2442, 2409, 2864], [626, 629, 629, 624])
        self._assert_table(tables[1], [2628, 5731, 1984], [652, 1134, 1134, 1134, 1134, 1134])

        self.assertIn("（20 25  — 20 26  学年度第 一 学期）", _text(root))
        self.assertIn("考查(  ) / 考试( √ )", _text(tables[0]))
        self.assertIn("6. 命题完成后将该表与评分细则", _text(root))

    def _assert_table(self, table, expected_grid, expected_heights):
        tbl_w = table.find("w:tblPr/w:tblW", NS)
        self.assertEqual((_attr(tbl_w, "w"), _attr(tbl_w, "type")), ("10343", "dxa"))
        self.assertEqual([int(_attr(col, "w")) for col in table.findall("w:tblGrid/w:gridCol", NS)], expected_grid)
        for border_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = table.find(f"w:tblPr/w:tblBorders/w:{border_name}", NS)
            self.assertEqual((_attr(border, "val"), _attr(border, "sz"), _attr(border, "color")), ("single", "4", "auto"))
        self.assertEqual(
            [int(_attr(row.find("w:trPr/w:trHeight", NS), "val")) for row in table.findall("w:tr", NS)],
            expected_heights,
        )


if __name__ == "__main__":
    unittest.main()
