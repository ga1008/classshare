import tempfile
import unittest
from pathlib import Path

from classroom_app.services.document_render_service import (
    DocumentRenderService,
    issue_render_token,
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
        owner = {"id": 7, "role": "teacher"}
        other_user = {"id": 8, "role": "teacher"}
        token = issue_render_token(key, user=owner)

        self.assertTrue(verify_render_token(key, token, user=owner))
        self.assertFalse(verify_render_token(key, token, user=other_user))
        self.assertFalse(verify_render_token(key, token, user=None))
        self.assertFalse(verify_render_token(key, "wrong-token"))
        self.assertFalse(verify_render_token("b" * 64, token, user=owner))

    def test_preview_html_uses_3d_deck_and_wheel_navigation(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = service.render_artifact(
                _build_pdf_bytes(page_count=3),
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            preview_html = service.render_preview_html(
                job,
                title="Sample Preview",
                user={"id": 7, "role": "teacher"},
            )

            self.assertIn('class="doc-preview-deck-shell"', preview_html)
            self.assertIn("data-page-deck", preview_html)
            self.assertIn("data-deck-count", preview_html)
            self.assertIn("data-deck-prev", preview_html)
            self.assertIn("data-deck-next", preview_html)
            self.assertIn("data-zoom-out", preview_html)
            self.assertIn("data-zoom-reset", preview_html)
            self.assertIn("data-zoom-in", preview_html)
            self.assertIn("draggable=\"false\"", preview_html)
            self.assertIn("stage.addEventListener('wheel'", preview_html)
            self.assertIn("image.addEventListener('wheel'", preview_html)
            self.assertIn("image.addEventListener('pointerdown'", preview_html)
            self.assertIn("image.addEventListener('pointermove'", preview_html)
            self.assertIn("stepDeck(wheelAccumulator > 0 ? 1 : -1)", preview_html)
            self.assertIn("setZoom(zoomScale * factor", preview_html)
            self.assertIn("doc-preview-card.is-active", preview_html)
            self.assertNotIn("repeat(auto-fit", preview_html)

    def test_cache_stats_reports_jobs_and_render_profile_separates_keys(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            root = Path(temp_dir)
            pdf_bytes = _build_pdf_bytes(page_count=1)
            first_service = DocumentRenderService(root=root)
            first = first_service.render_artifact(
                pdf_bytes,
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            second_service = DocumentRenderService(root=root)
            second_service.medium_zoom = first_service.medium_zoom + 0.1
            second = second_service.render_artifact(
                pdf_bytes,
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            stats = second_service.cache_stats()
            self.assertNotEqual(first.key, second.key)
            self.assertEqual(2, stats["job_count"])
            self.assertGreater(stats["total_bytes"], len(pdf_bytes))
            self.assertEqual(2, stats["medium_pages"])
            self.assertEqual(0, stats["large_pages"])


if __name__ == "__main__":
    unittest.main()
