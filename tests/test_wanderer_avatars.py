"""散修 avatar chain (docs/wanderer-ai-free-agents-2026-10-25.md §11).

Static artwork gate + pick_silhouette + the endpoint's resolution order
(illustration → silhouette → geometric) on the isolated SQLite fixture.
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.dependencies import get_current_user
from classroom_app.services import wanderer_admin_service as admin
from classroom_app.services.wanderer_avatar_service import (
    AVATAR_DIR,
    MANIFEST_PATH,
    SILHOUETTE_DIR,
    SILHOUETTE_KEYS,
    avatar_source,
    pick_silhouette,
    render_wanderer_avatar_svg,
    validate_avatar_svg,
)
from classroom_app.services.wanderer_identity_service import invalidate_wanderer_cache
from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS, assign_seed_avatar_files
from tests.wanderer_test_support import WandererDbTestCase

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SILHOUETTES = {"student_m", "student_f", "scholar", "athlete", "musician", "foodie",
                        "gamer", "artist", "traveler", "dog", "cat", "sword"}


def _load_build_tool():
    spec = importlib.util.spec_from_file_location("build_wanderer_avatars", ROOT / "tools/ui/build_wanderer_avatars.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AvatarArtworkTest(unittest.TestCase):
    def test_every_seed_has_a_safe_illustration(self):
        for seed in WANDERER_SEEDS:
            path = AVATAR_DIR / f"{seed['slug']}.svg"
            with self.subTest(slug=seed["slug"]):
                self.assertTrue(path.is_file(), path)
                self.assertEqual(validate_avatar_svg(path.read_bytes()), [])
                self.assertNotIn(b"<text", path.read_bytes())

    def test_manifest_describes_every_illustration(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {seed["slug"] for seed in WANDERER_SEEDS})
        for slug, entry in manifest.items():
            with self.subTest(slug=slug):
                self.assertIn(entry["kind"], {"portrait", "object", "animal"})
                self.assertIn(entry["source"], {"codex", "opus"})
                self.assertTrue(entry["subject"])
                self.assertTrue(entry["palette"])

    def test_twelve_silhouettes_exist_validate_and_match_the_build_tool(self):
        self.assertEqual(set(SILHOUETTE_KEYS), EXPECTED_SILHOUETTES)
        tool = _load_build_tool()
        for key in SILHOUETTE_KEYS:
            path = SILHOUETTE_DIR / f"{key}.svg"
            with self.subTest(key=key):
                self.assertTrue(path.is_file(), path)
                self.assertEqual(validate_avatar_svg(path.read_bytes()), [])
                self.assertEqual(path.read_text(encoding="utf-8"), tool.render_silhouette(key))

    def test_validator_rejects_unsafe_svg(self):
        ok = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle r="1"/></svg>'
        self.assertEqual(validate_avatar_svg(ok), [])
        bad = {
            "script": ok.replace("<circle", "<script>alert(1)</script><circle"),
            "image": ok.replace("<circle", '<image href="https://x.test/a.png"/><circle'),
            "foreign": ok.replace("<circle", "<foreignObject/><circle"),
            "href": ok.replace("<circle", '<a href="https://x.test"/><circle'),
            "onload": ok.replace("<svg ", '<svg onload="x()" '),
            "url": ok.replace('r="1"', 'r="1" fill="url(https://x.test/#g)"'),
            "viewbox": ok.replace(' viewBox="0 0 10 10"', ""),
            "size": ok.replace("<circle", "<!--" + "x" * 13000 + "--><circle"),
            "xml": ok[:-6],
        }
        for name, svg in bad.items():
            with self.subTest(name=name):
                self.assertTrue(validate_avatar_svg(svg))


class PickSilhouetteTest(unittest.TestCase):
    def test_keyword_cases(self):
        cases = {
            "dog": {"interests": ["遛狗", "柯基"], "gender": "女"},
            "cat": {"interests": ["猫", "甜品"], "gender": "女"},  # cat outranks foodie
            "sword": {"interests": ["武侠小说"], "archetype": "仗剑少年", "gender": "男"},
            "musician": {"interests": ["吉他", "旅行"], "gender": "男"},
            "foodie": {"interests": ["美食", "段子"], "gender": "男"},
            "athlete": {"interests": ["篮球"], "gender": "男"},
            "gamer": {"interests": ["游戏"], "gender": "男"},
            "artist": {"interests": ["摄影"], "gender": "女"},
            "traveler": {"interests": ["城市漫步"], "gender": "女"},
            "scholar": {"interests": ["考研"], "gender": "女"},
        }
        for expected, source in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(pick_silhouette(source), expected)

    def test_temperament_counts_and_gender_is_the_fallback(self):
        self.assertEqual(pick_silhouette({"interests": ["发呆"], "temperament": ["爱读书"], "gender": "男"}), "scholar")
        self.assertEqual(pick_silhouette({"interests": ["诗歌", "雨天"], "gender": "女"}), "student_f")  # 诗歌 is poetry, not music
        self.assertEqual(pick_silhouette({"interests": ["雨天"], "gender": "男"}), "student_m")
        self.assertEqual(pick_silhouette(None), "student_m")

    def test_every_seed_maps_to_a_known_key(self):
        for seed in WANDERER_SEEDS:
            self.assertIn(pick_silhouette(seed), EXPECTED_SILHOUETTES)
        by_slug = {seed["slug"]: pick_silhouette(seed) for seed in WANDERER_SEEDS}
        self.assertEqual(by_slug["jiangye"], "musician")
        self.assertEqual(by_slug["dazhuang"], "foodie")
        self.assertEqual(by_slug["xiaoman"], "cat")
        self.assertEqual(by_slug["linwanzhou"], "scholar")  # 夜猫子 is a night owl, not a cat


class AvatarChainTest(WandererDbTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.connect() as conn:
            class_id = conn.execute("SELECT id FROM classes LIMIT 1").fetchone()[0]
            conn.execute("INSERT INTO students (student_id_number, name, class_id, enrollment_status) "
                         "VALUES ('S-AV-1', '看头像的同学', ?, 'active')", (class_id,))
            cls.viewer = {"id": conn.execute("SELECT id FROM students WHERE student_id_number = 'S-AV-1'")
                          .fetchone()[0], "role": "student", "name": "看头像的同学", "class_id": class_id}
            cls.admin_user = {"id": conn.execute("SELECT id FROM teachers LIMIT 1").fetchone()[0], "role": "teacher"}
            conn.commit()

    def setUp(self):
        super().setUp()
        app.dependency_overrides[get_current_user] = lambda: dict(self.viewer)
        self.addCleanup(app.dependency_overrides.pop, get_current_user, None)
        self.client = TestClient(app)

    def set_avatar(self, slug, avatar_file, silhouette):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET avatar_file = ?, avatar_silhouette = ? WHERE slug = ?",
                         (avatar_file, silhouette, slug))
            conn.commit()
        invalidate_wanderer_cache()

    def fetch(self, slug):
        with self.connect() as conn:
            student_id = self.wanderer(conn, slug)["student_id"]
        response = self.client.get(f"/api/profile/avatar?role=student&user_id={student_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers["content-type"])
        self.assertIn("max-age=3600", response.headers["cache-control"])
        return response.content

    def test_seeding_points_every_seed_at_its_illustration(self):
        with self.connect() as conn:
            for seed in WANDERER_SEEDS:
                self.assertEqual(self.wanderer(conn, seed["slug"])["avatar_file"], f"{seed['slug']}.svg")

    def test_chain_order_illustration_then_silhouette_then_geometric(self):
        self.addCleanup(self.set_avatar, "suqinghe", "suqinghe.svg", "")
        self.set_avatar("suqinghe", "suqinghe.svg", "scholar")
        self.assertEqual(self.fetch("suqinghe"), (AVATAR_DIR / "suqinghe.svg").read_bytes())
        self.set_avatar("suqinghe", "-", "scholar")
        self.assertEqual(self.fetch("suqinghe"), (SILHOUETTE_DIR / "scholar.svg").read_bytes())
        self.set_avatar("suqinghe", "", "")
        self.assertEqual(self.fetch("suqinghe").decode("utf-8"), render_wanderer_avatar_svg("suqinghe"))

    def test_unsafe_or_missing_values_fall_through(self):
        self.addCleanup(self.set_avatar, "momo", "momo.svg", "")
        self.set_avatar("momo", "../../app.py", "../../x")
        self.assertEqual(self.fetch("momo").decode("utf-8"), render_wanderer_avatar_svg("momo"))
        self.set_avatar("momo", "nosuchfile.svg", "cat")
        self.assertEqual(self.fetch("momo"), (SILHOUETTE_DIR / "cat.svg").read_bytes())

    def test_admin_silhouette_override_survives_reseed_and_restores(self):
        self.addCleanup(self.set_avatar, "atai", "atai.svg", "")
        with self.connect() as conn:
            wanderer_id = self.wanderer(conn, "atai")["id"]
            detail = admin.update_wanderer(conn, wanderer_id, {"avatar_silhouette": "athlete"}, actor=self.admin_user)
            conn.commit()
            self.assertEqual((detail["avatar"]["source"], detail["avatar"]["silhouette"]), ("silhouette", "athlete"))
            self.assertIn("&v=", detail["card"]["avatar_url"])
            assign_seed_avatar_files(conn)  # a restart must not undo the admin's choice
            conn.commit()
            self.assertEqual(avatar_source(self.wanderer(conn, "atai")), "silhouette")
            with self.assertRaises(ValueError):
                admin.update_wanderer(conn, wanderer_id, {"avatar_silhouette": "dragon"}, actor=self.admin_user)
            detail = admin.update_wanderer(conn, wanderer_id, {"avatar_silhouette": ""}, actor=self.admin_user)
            conn.commit()
        self.assertEqual((detail["avatar"]["source"], detail["avatar"]["silhouette"]), ("illustrated", ""))
        self.assertEqual(detail["avatar"]["suggested_silhouette"], "athlete")
        self.assertEqual(len(detail["avatar"]["silhouettes"]), 12)
        self.assertEqual(self.fetch("atai"), (AVATAR_DIR / "atai.svg").read_bytes())


if __name__ == "__main__":
    unittest.main()
