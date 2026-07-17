import unittest
from unittest.mock import AsyncMock, patch

from classroom_app.services import blog_news_crawler_service as crawler
from classroom_app.services import blog_service
from classroom_app.services.blog_image_policy import is_suitable_news_cover_dimensions


class BlogNewsImageSelectionTests(unittest.TestCase):
    def test_page_parser_ignores_navigation_branding_and_prefers_article_srcset(self):
        parser = crawler._NewsPageParser("https://news.example.com/posts/42")
        parser.feed(
            """
            <header><img src="/assets/site-logo.png" width="640" height="320"></header>
            <main><article>
                <img src="/assets/loading.png"
                     srcset="/media/story-small.jpg 480w, /media/story-large.jpg 1280w"
                     width="1280" height="720" alt="robot demonstration">
            </article></main>
            <footer><img src="/assets/copyright.jpg" width="1200" height="600"></footer>
            """
        )

        media = crawler._normalize_media(parser.media)

        self.assertEqual(1, len(media))
        self.assertEqual("https://news.example.com/media/story-large.jpg", media[0]["url"])
        self.assertEqual("page-img-content", media[0]["source"])

    def test_media_normalization_rejects_logo_and_prioritizes_content_image(self):
        media = crawler._normalize_media(
            [
                {"type": "image", "url": "https://example.com/logo.png", "source": "page-meta"},
                {"type": "image", "url": "https://example.com/social.jpg", "source": "page-meta"},
                {
                    "type": "image",
                    "url": "https://cdn.example.com/story.jpg",
                    "source": "page-img-content",
                    "width": 1280,
                    "height": 720,
                },
            ]
        )

        self.assertEqual(2, len(media))
        self.assertEqual("https://cdn.example.com/story.jpg", media[0]["url"])
        self.assertNotIn("logo.png", " ".join(item["url"] for item in media))

    def test_news_cover_dimensions_reject_pixels_avatars_and_square_logos(self):
        self.assertFalse(is_suitable_news_cover_dimensions(1, 1))
        self.assertFalse(is_suitable_news_cover_dimensions(100, 100))
        self.assertFalse(is_suitable_news_cover_dimensions(800, 800))
        self.assertTrue(is_suitable_news_cover_dimensions(1280, 720))


class BlogNewsImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_slot_builder_keeps_trying_after_a_bad_first_candidate(self):
        stored = {
            "file_hash": "a" * 64,
            "filename": "story.jpg",
            "mime_type": "image/jpeg",
            "file_size": 1234,
            "image_width": 1280,
            "image_height": 720,
        }
        download = AsyncMock(side_effect=[ValueError("too small"), stored])
        candidate = {
            "title": "A useful story",
            "media": [
                {"type": "image", "url": "https://cdn.example.com/first.jpg"},
                {"type": "image", "url": "https://cdn.example.com/second.jpg"},
            ],
        }

        with patch.object(crawler, "_download_and_store_image", download):
            slots = await crawler._build_local_image_slots(
                candidate,
                {"max_images_per_post": 1, "max_image_bytes": 6 * 1024 * 1024},
                client=object(),
            )

        self.assertEqual(2, download.await_count)
        self.assertEqual(1, len(slots))
        self.assertEqual("{{image_1}}", slots[0]["token"])
        self.assertEqual("https://cdn.example.com/second.jpg", slots[0]["source_url"])


class BlogNewsCoverPresentationTests(unittest.TestCase):
    def test_invalid_assistant_cover_becomes_explicit_editorial_fallback(self):
        row = {
            "id": 22,
            "author_role": "assistant",
            "cover_image_hash": "b" * 64,
            "cover_image_width": 1,
            "cover_image_height": 1,
        }

        cover = blog_service._presentation_cover(row)

        self.assertEqual("", cover["hash"])
        self.assertEqual("editorial", cover["kind"])

    def test_rejected_assistant_cover_is_removed_from_detail_markdown(self):
        file_hash = "c" * 64
        row = {
            "author_role": "assistant",
            "cover_image_hash": file_hash,
            "cover_image_width": 100,
            "cover_image_height": 100,
        }
        content = (
            "Opening paragraph.\n\n"
            f"![platform logo](/api/blog/image/{file_hash})\n\n"
            "> 配图来源：[example.com](https://example.com/logo.png)\n\n"
            "Main analysis."
        )

        cleaned = blog_service._strip_rejected_assistant_cover_markdown(content, row)

        self.assertNotIn(file_hash, cleaned)
        self.assertNotIn("配图来源", cleaned)
        self.assertIn("Main analysis.", cleaned)


if __name__ == "__main__":
    unittest.main()
