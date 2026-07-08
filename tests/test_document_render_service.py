import tempfile
import unittest
from pathlib import Path

from classroom_app.services.document_render_service import (
    DocumentRenderService,
    sign_render_key,
    verify_render_token,
)


def _build_pdf_bytes(page_count: int = 2) -> bytes:
    import fitz

    doc = fitz.open()
    try:
        for index in range(page_count):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 96), f"LanShare preview page {index + 1}", fontsize=18)
        return doc.tobytes()
    finally:
        doc.close()


class DocumentRenderServiceTests(unittest.TestCase):
    def test_pdf_render_cache_download_and_large_page(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            pdf_bytes = _build_pdf_bytes(page_count=2)

            first = service.render_artifact(
                pdf_bytes,
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )
            second = service.render_artifact(
                pdf_bytes,
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            self.assertEqual(first.key, second.key)
            self.assertEqual(2, first.page_count)
            medium_page = service.get_page_image_path(first.key, 1, size="medium")
            large_page = service.get_page_image_path(first.key, 1, size="large")
            document_path, filename, media_type = service.get_download_path(first.key)

            self.assertTrue(medium_page.exists())
            self.assertTrue(large_page.exists())
            self.assertGreater(large_page.stat().st_size, medium_page.stat().st_size)
            self.assertEqual("sample.pdf", filename)
            self.assertEqual("application/pdf", media_type)
            self.assertEqual(pdf_bytes, document_path.read_bytes())

    def test_render_key_token_is_required(self):
        key = "a" * 64
        token = sign_render_key(key)

        self.assertTrue(verify_render_token(key, token))
        self.assertFalse(verify_render_token(key, "wrong-token"))
        self.assertFalse(verify_render_token("b" * 64, token))


if __name__ == "__main__":
    unittest.main()

