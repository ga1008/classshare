import asyncio
import json
import tempfile
import unittest
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from classroom_app.frontend_assets import asset_url
from classroom_app.routers import document_renderer as document_renderer_router
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
            self.assertEqual(0, service.cache_stats()["medium_pages"])
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
            self.assertIn("doc-preview-card__placeholder", preview_html)
            self.assertIn("data-page-image", preview_html)
            self.assertIn("loadMediumPage", preview_html)
            self.assertIn("requestVisiblePages", preview_html)
            self.assertIn("stage.addEventListener('wheel'", preview_html)
            self.assertIn("image.addEventListener('wheel'", preview_html)
            self.assertIn("image.addEventListener('pointerdown'", preview_html)
            self.assertIn("image.addEventListener('pointermove'", preview_html)
            self.assertIn("stepDeck(wheelAccumulator > 0 ? 1 : -1)", preview_html)
            self.assertIn("setZoom(zoomScale * factor", preview_html)
            self.assertIn("doc-preview-card.is-active", preview_html)
            self.assertNotIn("<img src=\"/api/document-renderer", preview_html)
            self.assertNotIn("repeat(auto-fit", preview_html)

    def test_preview_html_can_disable_cached_download(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = service.render_artifact(
                _build_pdf_bytes(page_count=1),
                filename="draft.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            preview_html = service.render_preview_html(
                job,
                title="Draft Preview",
                user={"id": 7, "role": "teacher"},
                download_disabled_reason="请先补齐业务字段后再导出。",
            )

            self.assertRegex(preview_html, r'<button class="doc-preview-download is-disabled [^"]*lq-btn[^>]+ disabled aria-disabled="true"')
            self.assertIn('aria-disabled="true"', preview_html)
            self.assertIn("doc-preview-download-note", preview_html)
            self.assertIn("请先补齐业务字段后再导出。", preview_html)
            self.assertNotIn("/api/document-renderer/jobs/" + job.key + "/download", preview_html)

    def test_preview_shared_assets_and_content_slots_preserve_signed_urls(self):
        from classroom_app.services.document_render_service import RenderedDocumentJob
        with tempfile.TemporaryDirectory(prefix="lanshare-preview-contract-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = RenderedDocumentJob("a" * 64, {"page_count": 3}, Path(temp_dir))
            owner = {"id": 7, "role": "teacher"}
            source = service.render_preview_html(job, title='<script>alert("title")</script>', user=owner,
                                                 signature_request_id=42)
            self.assertIn(asset_url("tailwind_app"), source)
            self.assertIn(asset_url("js/lq/theme.js"), source)
            self.assertIn("window, document", source)  # Shared synchronous resolver.
            self.assertIn('data-theme="lanshare"', source)
            self.assertNotIn('<script>alert("title")</script>', source)
            self.assertEqual(4, source.count('data-lq-content="document-page"'))
            cards = re.findall(r'<button[^>]+data-page-index="[^"]+"[^>]*>', source)
            self.assertEqual(3, len(cards))
            self.assertTrue(all('data-lq-component="content-slot"' in card and 'lq-btn' not in card for card in cards))
            buttons = re.findall(r'<button\b[^>]*>', source)
            self.assertTrue(all('data-lq-component="button"' in button or 'data-lq-component="content-slot"' in button for button in buttons))
            payload = json.loads(re.search(r"const pages = (\[.*?\]);", source).group(1))
            for page in payload:
                for size in ("medium", "large"):
                    query = parse_qs(urlsplit(page[size + "Url"]).query)
                    self.assertEqual([size], query["size"])
                    token = query["token"][0]
                    self.assertTrue(verify_render_token(job.key, token, user=owner))
                    self.assertFalse(verify_render_token(job.key, token, user={"id": 8, "role": "teacher"}))
            self.assertNotIn("backdrop-filter: blur", source)
            self.assertFalse(list(Path(temp_dir).iterdir()))

    def test_preview_error_uses_shared_material_without_trusting_error_html(self):
        source = DocumentRenderService().render_error_html(title="error", message='<img src=x onerror="alert(1)">')
        self.assertIn(asset_url("tailwind_app"), source)
        self.assertIn('data-lq-material="raised"', source)
        self.assertIn('&lt;img', source)
        self.assertNotIn('<img src=x', source)

    def test_medium_pages_are_lazy_and_rendered_per_page(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = service.render_artifact(
                _build_pdf_bytes(page_count=3),
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            self.assertFalse((job.root / "page-001.medium.png").exists())
            self.assertFalse((job.root / "page-002.medium.png").exists())

            page_2 = service.get_page_image_path(job.key, 2, size="medium")

            self.assertTrue(page_2.exists())
            self.assertFalse((job.root / "page-001.medium.png").exists())
            self.assertFalse((job.root / "page-003.medium.png").exists())
            self.assertEqual(1, service.cache_stats()["medium_pages"])

    def test_large_documents_use_lazy_page_rendering_under_default_cap(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = service.render_artifact(
                _build_pdf_bytes(page_count=93),
                filename="long.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )

            self.assertEqual(93, job.page_count)
            self.assertEqual(0, service.cache_stats()["medium_pages"])
            page_93 = service.get_page_image_path(job.key, 93, size="medium")

            self.assertTrue(page_93.exists())
            self.assertEqual(1, service.cache_stats()["medium_pages"])

    def test_metadata_route_reports_per_page_cache_state(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = service.render_artifact(
                _build_pdf_bytes(page_count=3),
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )
            service.get_page_image_path(job.key, 2, size="medium")
            token = issue_render_token(job.key, user={"id": 7, "role": "teacher"})
            original_service = document_renderer_router.document_render_service
            document_renderer_router.document_render_service = service
            try:
                response = asyncio.run(
                    document_renderer_router.get_rendered_document_metadata(
                        job.key,
                        token=token,
                        user={"id": 7, "role": "teacher"},
                    )
                )
            finally:
                document_renderer_router.document_render_service = original_service

            payload = json.loads(response.body.decode("utf-8"))
            self.assertEqual(3, payload["page_count"])
            self.assertEqual(1, payload["medium_pages_cached"])
            self.assertEqual(0, payload["large_pages_cached"])
            self.assertEqual(
                [
                    {"number": 1, "medium_cached": False, "large_cached": False},
                    {"number": 2, "medium_cached": True, "large_cached": False},
                    {"number": 3, "medium_cached": False, "large_cached": False},
                ],
                payload["pages"],
            )

    def test_download_route_never_caches_dynamic_document(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-render-test-") as temp_dir:
            service = DocumentRenderService(root=Path(temp_dir))
            job = service.render_artifact(
                _build_pdf_bytes(page_count=1),
                filename="sample.pdf",
                media_type="application/pdf",
                source_format="pdf",
            )
            token = issue_render_token(job.key, user={"id": 7, "role": "teacher"})
            original_service = document_renderer_router.document_render_service
            document_renderer_router.document_render_service = service
            try:
                response = asyncio.run(
                    document_renderer_router.download_rendered_document(
                        job.key,
                        token=token,
                        user={"id": 7, "role": "teacher"},
                    )
                )
            finally:
                document_renderer_router.document_render_service = original_service

            self.assertEqual("private, no-store, max-age=0, must-revalidate", response.headers["cache-control"])
            self.assertEqual("no-cache", response.headers["pragma"])
            self.assertEqual("0", response.headers["expires"])

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
            self.assertEqual(0, stats["medium_pages"])
            self.assertEqual(0, stats["large_pages"])


if __name__ == "__main__":
    unittest.main()
