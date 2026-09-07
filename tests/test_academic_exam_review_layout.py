from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from classroom_app.services.academic_final_material_document_service import build_exam_analysis_docx


class AcademicExamReviewLayoutTests(unittest.TestCase):
    def document(self, fields=None, text="一、成绩分析\n教学分析内容。"):
        return Document(BytesIO(build_exam_analysis_docx({
            "fields": fields or {}, "structured": {"analysis_text": text},
        })))

    def test_reviews_are_two_unbroken_cells_and_note_is_outside_table(self):
        document = self.document({"department_review_opinion": "已核", "dean_review_opinion": "同意"})
        row = document.tables[0].rows[-1]
        self.assertEqual(2, len(row._tr.tc_lst))
        self.assertEqual(["已核", "同意"], [shape._inline.docPr.get("descr") for shape in document.inline_shapes if shape._inline.docPr.get("descr")])
        for cell in [row.cells[0], row.cells[-1]]:
            self.assertIn("审核意见：", cell.paragraphs[0].text)
            self.assertIn("签字：", cell.paragraphs[-1].text)
        self.assertIn("本表一式两份", document.paragraphs[-1].text)
        self.assertFalse(document.paragraphs[-1]._p.xpath(".//w:pBdr"))

    def test_differently_shaped_signatures_fit_without_stretching_before_label(self):
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ["wide.png", "tall.png"]]
            sizes = [(1000, 40), (40, 1000)]
            for path, size in zip(paths, sizes):
                Image.new("RGBA", size, "black").save(path)
            document = self.document(dict(zip(["department_signature_image_path", "dean_signature_image_path"], map(str, paths))))
        for shape, size in zip(list(document.inline_shapes)[-2:], sizes):
            self.assertAlmostEqual(size[0] / size[1], shape.width / shape.height, delta=(size[0] / size[1]) * .0001)
            self.assertLessEqual(shape.width.pt, 88.01)
            self.assertLessEqual(shape.height.pt, 32.01)
        for cell in [document.tables[0].rows[-1].cells[0], document.tables[0].rows[-1].cells[-1]]:
            paragraph = cell.paragraphs[-1]
            self.assertTrue(paragraph.runs[0]._r.xpath(".//w:drawing"))
            self.assertEqual(" 签字：", paragraph.runs[-1].text)
            self.assertEqual(38, paragraph.paragraph_format.line_spacing.pt)

    def test_long_body_can_continue_and_short_headings_stay_with_body(self):
        text = "一、成绩分析\n" + "教学分析内容完整保留。" * 250 + "\n二、改进措施\n全文结束。"
        document = self.document(text=text)
        row = document.tables[0].rows[17]
        self.assertEqual("atLeast", row._tr.trPr.find(qn("w:trHeight")).get(qn("w:hRule")))
        self.assertFalse(row._tr.trPr.findall(qn("w:cantSplit")))
        self.assertFalse(row.cells[0]._tc.xpath(".//w:textDirection"))
        body = row.cells[-1]
        self.assertTrue(body.paragraphs[0].paragraph_format.keep_with_next)
        self.assertIn("全文结束。", body.text)

    def test_explicit_empty_opinion_suppresses_old_opinion_image(self):
        asset = Path(__file__).resolve().parents[1] / "classroom_app/services/assets/gxufl_exam_review_checked.png"
        document = self.document({"department_review_opinion": "", "department_review_opinion_image_path": str(asset)})
        self.assertEqual(1, len(document.inline_shapes))  # Only the score chart.


if __name__ == "__main__":
    unittest.main()
