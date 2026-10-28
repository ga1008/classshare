"""散修 public homepage + runtime avatars (docs/wanderer-ai-free-agents-2026-10-25.md §10.3).

Isolated SQLite + TestClient with the login dependency overridden; no model calls.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.dependencies import get_current_user
from classroom_app.services import blog_service
from classroom_app.services.heavenly_dao_service import demote
from classroom_app.services.wanderer_avatar_service import AVATAR_DIR, render_wanderer_avatar_svg
from classroom_app.services.wanderer_identity_service import log_wanderer_action
from classroom_app.services.wanderer_public_service import build_wanderer_home
from tests.wanderer_test_support import WandererDbTestCase


class WandererPublicHomeTest(WandererDbTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.connect() as conn:
            class_id = conn.execute("SELECT id FROM classes LIMIT 1").fetchone()[0]
            conn.execute("INSERT INTO students (student_id_number, name, class_id, enrollment_status) "
                         "VALUES ('S-HOME-1', '看主页的同学', ?, 'active')", (class_id,))
            cls.viewer = {"id": conn.execute("SELECT id FROM students WHERE student_id_number = 'S-HOME-1'")
                          .fetchone()[0], "role": "student", "name": "看主页的同学", "class_id": class_id}
            conn.commit()

    def setUp(self):
        super().setUp()
        app.dependency_overrides[get_current_user] = lambda: dict(self.viewer)
        self.addCleanup(app.dependency_overrides.pop, get_current_user, None)
        self.client = TestClient(app)

    def test_homepage_renders_for_a_wanderer_slug(self):
        response = self.client.get("/wanderers/linwanzhou")
        self.assertEqual(response.status_code, 200, response.text[:800])
        self.assertIn("林晚舟", response.text)
        self.assertIn("散修", response.text)
        self.assertIn("夜猫子哲学家", response.text)
        self.assertIn("种子散修", response.text)
        self.assertIn("wanderer-home", response.text)

    def test_homepage_shows_daily_search_reservations_and_uncertain_requests(self):
        usage = {"limit": 10, "used": 2, "reserved": 1, "unknown": 1, "remaining": 6,
                 "date": "2026-10-28", "reset_at": "2026-10-29T00:00:00+08:00", "available": True}
        with patch("classroom_app.services.wanderer_public_service.web_search_usage", return_value=usage):
            response = self.client.get("/wanderers/linwanzhou")
        self.assertEqual(response.status_code, 200)
        for text in ("data-wanderer-search-usage", "今日搜索", "2 / 10", "剩余 6 次", "处理中 1 次", "待确认 1 次", "北京时间零点重置"):
            self.assertIn(text, response.text)

    def test_unknown_slugs_and_real_students_are_404(self):
        for path in ("/wanderers/nobody", "/wanderers/S-HOME-1", "/wanderers/" + "x" * 60):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_payload_lists_public_actions_posts_and_verdicts_only(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            user = {"id": wanderer["student_id"], "role": "student", "name": "阿泰", "class_id": wanderer["class_id"]}
            post = blog_service.create_post(conn, user, title="晨跑五公里", content_md="今天跑了五公里，腿有点酸。")
            conn.execute("UPDATE blog_posts SET moderation_state = 'passed' WHERE id = ?", (post["id"],))
            log_wanderer_action(conn, wanderer["id"], "write_post", target_type="blog_post", target_id=post["id"],
                                content_preview="晨跑五公里", detail={"post_id": post["id"]})
            log_wanderer_action(conn, wanderer["id"], "comment", target_type="blog_comment", target_id=1,
                                content_preview="评论一个已删除的帖子", detail={"post_id": 999999})
            log_wanderer_action(conn, wanderer["id"], "browse_post", target_type="blog_post", target_id=post["id"])
            demote(conn, wanderer, "辱骂同学")
            conn.commit()
            home = build_wanderer_home(conn, self.viewer, "atai")
            self.assertIsNone(build_wanderer_home(conn, self.viewer, ""))
        self.assertEqual([item["kind"] for item in home["actions"]], ["write_post"])
        self.assertEqual(home["actions"][0]["href"], f"/blog?post={post['id']}")
        self.assertEqual(home["stats"]["posts"], 1)
        self.assertEqual([review["label"] for review in home["reviews"]], ["天道降阶"])
        self.assertTrue(home["avatar_url"].startswith("/api/profile/avatar?role=student&user_id="))

    def test_avatar_endpoint_serves_the_persona_illustration(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "suqinghe")
        response = self.client.get(f"/api/profile/avatar?role=student&user_id={wanderer['student_id']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers["content-type"])
        # design 11: seeded wanderers serve their persona illustration; geometric stays the last resort.
        self.assertEqual(response.content, (AVATAR_DIR / "suqinghe.svg").read_bytes())
        self.assertEqual(render_wanderer_avatar_svg("anyslug"), render_wanderer_avatar_svg("anyslug"))
        self.assertNotEqual(render_wanderer_avatar_svg("anyslug"), render_wanderer_avatar_svg("other"))

    def test_public_card_and_persona_carry_homepage_url(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "momo")
        card = self.client.get(f"/api/wanderers/{wanderer['student_id']}/card").json()["card"]
        self.assertEqual(card["homepage_url"], "/wanderers/momo")


if __name__ == "__main__":
    unittest.main()
