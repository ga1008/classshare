"""No provider calls: material imports must not present partial vision as complete."""
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import fitz
from fastapi import HTTPException
from PIL import Image

from classroom_app.services import material_ai_import_service as service


def pdf_bytes(pages, *, text=False):
    picture = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(picture, "PNG")
    with fitz.open() as document:
        for index in range(pages):
            page = document.new_page(width=200, height=240)
            if text:
                page.insert_text((15, 20), f"Chapter {index + 1}: course goals and teaching methods.")
            else:
                page.insert_image(fitz.Rect(10, 10, 100, 100), stream=picture.getvalue())
        return document.tobytes()


async def parsed_ai_result(*args, **kwargs):
    return {"metadata": {}, "content_markdown": "# Teaching material\n\nCourse goals and teaching methods have been organized from the supplied source."}


class MaterialImportVisionCompletenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-material-vision-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "material.pdf"

    async def parse(self, ai, *, original_name=None):
        return await service.parse_material_document(
            file_path=self.path, original_name=original_name or self.path.name,
            document_group="teaching_material", document_type="teaching_document", ai_chat=ai,
        )

    async def test_nine_scan_pages_are_rejected_before_ai(self):
        self.path.write_bytes(pdf_bytes(9))
        ai = mock.AsyncMock(side_effect=parsed_ai_result)
        with self.assertRaises(HTTPException) as raised:
            await self.parse(ai)
        self.assertEqual(422, raised.exception.status_code)
        self.assertIn("9 页", str(raised.exception.detail))
        self.assertIn("8 页", str(raised.exception.detail))
        ai.assert_not_awaited()

    async def test_eight_scan_pages_reach_one_complete_vision_call(self):
        self.path.write_bytes(pdf_bytes(8))
        ai = mock.AsyncMock(side_effect=parsed_ai_result)
        result = await self.parse(ai)
        self.assertTrue(result.ai_used)
        ai.assert_awaited_once()
        self.assertEqual("vision", ai.call_args.kwargs["capability"])
        self.assertEqual(8, len(ai.call_args.kwargs["base64_urls"]))

    async def test_nine_page_text_pdf_preserves_text_first_without_rendering(self):
        self.path.write_bytes(pdf_bytes(9, text=True))
        ai = mock.AsyncMock(side_effect=parsed_ai_result)
        with mock.patch.object(service, "_render_complete_pdf_pages", side_effect=AssertionError("must not render")):
            result = await self.parse(ai)
        self.assertTrue(result.ai_used)
        ai.assert_awaited_once()
        self.assertEqual("thinking", ai.call_args.kwargs["capability"])
        self.assertNotIn("base64_urls", ai.call_args.kwargs)

    async def test_failed_page_render_cannot_call_vision_with_partial_pages(self):
        self.path.write_bytes(pdf_bytes(2))
        ai = mock.AsyncMock(side_effect=parsed_ai_result)
        with mock.patch.object(service, "render_pdf_pages_to_data_urls", return_value=[{"data_url": "data:image/png;base64,AA=="}]), self.assertRaises(HTTPException) as raised:
            await self.parse(ai)
        self.assertIn("未能完整渲染", str(raised.exception.detail))
        ai.assert_not_awaited()

    async def test_extraction_issues_truncation_and_missing_image_do_not_reach_ai(self):
        for extraction in (
            service.MaterialExtraction(source_kind="docx", visual_issues=["图像缺失"]),
            service.MaterialExtraction(source_kind="docx", images=[{"data_url": "placeholder"}], truncated=True),
            service.MaterialExtraction(source_kind="docx", images=[{"filename": "missing.png"}]),
            service.MaterialExtraction(source_kind="docx", images=[{"data_url": "placeholder"}] * 9),
        ):
            with self.subTest(extraction=extraction):
                ai = mock.AsyncMock(side_effect=parsed_ai_result)
                with mock.patch.object(service, "extract_material_content", return_value=extraction), self.assertRaises(HTTPException):
                    await self.parse(ai, original_name="material.docx")
                ai.assert_not_awaited()

    async def test_usable_text_succeeds_even_with_unneeded_visual_issues(self):
        extraction = service.MaterialExtraction(
            source_kind="docx", method="test_text", text="Course metadata and teaching objectives are fully readable. " * 30,
            visual_issues=["Decorative SVG is not part of the metadata extraction"],
        )
        ai = mock.AsyncMock(side_effect=parsed_ai_result)
        with mock.patch.object(service, "extract_material_content", return_value=extraction):
            result = await self.parse(ai, original_name="metadata.docx")
        ai.assert_awaited_once()
        self.assertEqual("thinking", ai.call_args.kwargs["capability"])
        self.assertTrue(result.ai_used)
        self.assertEqual(extraction.visual_issues, result.parsed_payload["extraction"]["visual_issues"])

    def test_office_render_keeps_full_page_limit_failure_visible(self):
        converted = types.SimpleNamespace(output_bytes=pdf_bytes(9))
        warnings, issues = [], []
        with mock.patch.object(service, "convert_office_file", return_value=converted):
            images = service._render_office_pages_to_images(Path("synthetic.xlsx"), ".xlsx", warnings, issues)
        self.assertEqual([], images)
        self.assertTrue(any("9 页" in issue for issue in issues))

    def test_office_render_accepts_complete_eight_pages(self):
        converted = types.SimpleNamespace(output_bytes=pdf_bytes(8))
        warnings, issues = [], []
        with mock.patch.object(service, "convert_office_file", return_value=converted):
            images = service._render_office_pages_to_images(Path("synthetic.xlsx"), ".xlsx", warnings, issues)
        self.assertEqual(8, len(images))
        self.assertFalse(issues)


if __name__ == "__main__":
    unittest.main()
