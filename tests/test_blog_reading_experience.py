import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import blog_service


class BlogReadingExperienceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE blog_posts (
                id INTEGER PRIMARY KEY,
                author_identity TEXT NOT NULL DEFAULT 'teacher:1',
                author_role TEXT NOT NULL DEFAULT 'teacher',
                author_user_pk INTEGER NOT NULL DEFAULT 1,
                author_display_name TEXT NOT NULL DEFAULT 'Teacher',
                author_display_mode TEXT NOT NULL DEFAULT 'real_name',
                status TEXT NOT NULL DEFAULT 'published',
                visibility TEXT NOT NULL DEFAULT 'public',
                visible_class_id INTEGER,
                visible_user_identities_json TEXT DEFAULT '[]',
                view_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE blog_post_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                viewer_identity TEXT NOT NULL,
                view_bucket TEXT NOT NULL,
                first_viewed_at TEXT,
                last_viewed_at TEXT,
                view_events INTEGER NOT NULL DEFAULT 1,
                dwell_seconds INTEGER NOT NULL DEFAULT 0,
                max_scroll_ratio REAL NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                UNIQUE (post_id, viewer_identity, view_bucket)
            );
            INSERT INTO blog_posts (id, view_count) VALUES (7, 9);
            """
        )
        self.engine_patch = patch.object(blog_service, "get_configured_db_engine", return_value="sqlite")
        self.engine_patch.start()

    def tearDown(self):
        self.engine_patch.stop()
        self.conn.close()

    def test_view_count_is_unique_per_identity_and_hour(self):
        first = blog_service._record_post_view(
            self.conn,
            7,
            "student:3",
            viewed_at="2026-07-15T10:02:00",
        )
        repeated = blog_service._record_post_view(
            self.conn,
            7,
            "student:3",
            viewed_at="2026-07-15T10:58:00",
        )
        next_hour = blog_service._record_post_view(
            self.conn,
            7,
            "student:3",
            viewed_at="2026-07-15T11:01:00",
        )

        self.assertTrue(first)
        self.assertFalse(repeated)
        self.assertTrue(next_hour)
        self.assertEqual(11, self.conn.execute("SELECT view_count FROM blog_posts WHERE id = 7").fetchone()[0])
        rows = self.conn.execute(
            "SELECT view_bucket, view_events FROM blog_post_views ORDER BY view_bucket"
        ).fetchall()
        self.assertEqual([("2026-07-15T10", 2), ("2026-07-15T11", 1)], [tuple(row) for row in rows])

    def test_trending_score_caps_views_and_decays_old_content(self):
        recent = {
            "like_count": 2,
            "comment_count": 2,
            "bookmark_count": 1,
            "view_count": 500,
            "created_at": "2999-01-01T00:00:00",
        }
        old = {**recent, "created_at": "2020-01-01T00:00:00"}

        self.assertEqual(62, blog_service._calculate_trending_score(recent))
        self.assertLess(blog_service._calculate_trending_score(old), 5)

    def test_reading_progress_keeps_maximum_and_marks_completion(self):
        first = blog_service.update_post_reading_progress(
            self.conn,
            {"id": 3, "role": "student"},
            7,
            dwell_seconds=18,
            max_scroll_ratio=0.45,
        )
        completed = blog_service.update_post_reading_progress(
            self.conn,
            {"id": 3, "role": "student"},
            7,
            dwell_seconds=12,
            max_scroll_ratio=0.95,
        )
        row = self.conn.execute(
            "SELECT dwell_seconds, max_scroll_ratio, completed FROM blog_post_views WHERE post_id = 7"
        ).fetchone()
        self.assertFalse(first["completed"])
        self.assertTrue(completed["completed"])
        self.assertEqual(18, row["dwell_seconds"])
        self.assertAlmostEqual(0.95, row["max_scroll_ratio"])
        self.assertEqual(1, row["completed"])

    def test_anonymous_summary_never_exposes_identity(self):
        payload = blog_service._serialize_post_summary(
            {
                "id": 22,
                "author_identity": "student:99",
                "author_role": "student",
                "author_user_pk": 99,
                "author_display_name": "匿名同学",
                "author_display_mode": "anonymous",
                "title": "匿名分享",
                "status": "published",
                "visibility": "public",
            },
            viewer_identity="student:99",
        )
        self.assertEqual("", payload["author"]["identity"])
        self.assertIsNone(payload["author"]["user_pk"])
        self.assertTrue(payload["is_author"])


if __name__ == "__main__":
    unittest.main()
