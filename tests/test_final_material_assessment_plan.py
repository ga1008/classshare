import io
import unittest
import zipfile
import xml.etree.ElementTree as ET

from classroom_app.services.material_export_template_service import build_material_export_artifact
from classroom_app.services.material_final_document_service import (
    FINAL_MATERIAL_LAYOUTS,
    build_final_material_generation_seed,
)


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{%s}" % NS["w"]


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
