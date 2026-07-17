from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services.blog_editorial_memory_service import (
    append_internal_reading_links,
    find_related_posts,
    format_memory_for_ai,
    normalize_editorial_profile,
    upsert_editorial_metadata,
)


class BlogEditorialMemoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE blog_posts (
                id INTEGER PRIMARY KEY, section_key TEXT, title TEXT, content_md TEXT,
                status TEXT, author_role TEXT, view_count INTEGER, like_count INTEGER,
                comment_count INTEGER, bookmark_count INTEGER, created_at TEXT
            );
            CREATE TABLE blog_comments (
                id INTEGER PRIMARY KEY, post_id INTEGER, author_display_name TEXT,
                content_md TEXT, status TEXT, like_count INTEGER, created_at TEXT
            );
            CREATE TABLE blog_post_editorial_metadata (
                post_id INTEGER PRIMARY KEY, topic TEXT NOT NULL DEFAULT '',
                keywords_json TEXT NOT NULL DEFAULT '[]', source_title TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                source_published_at TEXT NOT NULL DEFAULT '',
                classification_confidence REAL NOT NULL DEFAULT 0,
                classification_reason TEXT NOT NULL DEFAULT '',
                memory_post_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def _insert_post(self, post_id: int, title: str, content: str, section: str = "ai") -> None:
        self.conn.execute(
            """
            INSERT INTO blog_posts VALUES (?, ?, ?, ?, 'published', 'assistant', 20, 3, 1, 2, ?)
            """,
            (post_id, section, title, content, f"2026-07-{post_id:02d}"),
        )

    def test_related_memory_contains_full_article_source_interactions_comments_and_link(self):
        self._insert_post(1, "Gemini 走进安卓系统", "这是需要完整保留的历史正文，讨论 AI 助手的能力与边界。")
        self.conn.execute(
            "INSERT INTO blog_comments VALUES (1, 1, '小林', '隐私权限也值得注意', 'active', 4, '2026-07-10')"
        )
        with patch(
            "classroom_app.services.blog_editorial_memory_service.get_configured_db_engine",
            return_value="sqlite",
        ):
            upsert_editorial_metadata(
                self.conn,
                1,
                {"topic": "手机 AI 助手", "keywords": ["Gemini", "安卓", "隐私"], "confidence": 0.9, "reason": "AI能力"},
                source_name="极客公园",
                source_url="https://example.com/gemini",
                source_published_at="2026-07-09",
            )
        related = find_related_posts(
            self.conn,
            {"topic": "安卓 AI 助手", "keywords": ["Gemini", "手机", "隐私"], "section_key": "ai"},
        )
        self.assertEqual([post["id"] for post in related], [1])
        memory_text = format_memory_for_ai(related)
        self.assertIn("这是需要完整保留的历史正文", memory_text)
        self.assertIn("极客公园", memory_text)
        self.assertIn("隐私权限也值得注意", memory_text)
        self.assertIn("20 阅读 / 3 赞 / 1 评论 / 2 收藏", memory_text)
        self.assertIn("/blog?section=ai&post=1", memory_text)

    def test_internal_links_only_accept_provided_memory_ids(self):
        related = [
            {"id": 2, "title": "前情一篇", "internal_url": "/blog?section=ai&post=2"},
            {"id": 3, "title": "前情二篇", "internal_url": "/blog?section=computer&post=3"},
        ]
        content, used = append_internal_reading_links("正文", related, [999, 3, 3, 2])
        self.assertEqual(used, [3, 2])
        self.assertNotIn("999", content)
        self.assertIn("/blog?section=computer&post=3", content)
        self.assertIn("/blog?section=ai&post=2", content)

    def test_profile_rejects_unknown_section_and_limits_keywords(self):
        profile = normalize_editorial_profile(
            {
                "topic": " 大模型进入课堂 ",
                "section_key": "made-up",
                "keywords": ["大模型", "AI", "AI", "课堂", "学生", "治理", "隐私", "边界", "证据", "额外"],
                "confidence": 3,
            },
            allowed_sections={"general", "ai"},
            fallback_section="ai",
        )
        self.assertEqual(profile["section_key"], "ai")
        self.assertEqual(profile["confidence"], 1.0)
        self.assertEqual(len(profile["keywords"]), 8)
        self.assertEqual(profile["keywords"].count("AI"), 1)


if __name__ == "__main__":
    unittest.main()
