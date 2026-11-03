"""Built-in manuals in the learning library and stage-gated reading."""
import unittest

from classroom_app.services.wanderer_actions_knowledge import readable_materials
from classroom_app.services.wanderer_life_core import cultivation_target
from classroom_app.services.wanderer_manual_library import (
    MANUAL_FOLDER_NAME,
    advancement_manual_markdown,
    ensure_manual_materials,
    platform_manual_markdown,
)
from tests.wanderer_test_support import WandererDbTestCase


class ManualLibraryTest(WandererDbTestCase):
    def _seed(self, conn):
        result = ensure_manual_materials(conn)
        conn.commit()
        return result

    def test_manuals_are_seeded_indexed_and_readable_at_tier_zero(self):
        with self.connect() as conn:
            result = self._seed(conn)
            self.assertEqual(result["status"], "ok", result)
            folder = conn.execute(
                "SELECT id FROM course_materials WHERE name = ? AND node_type = 'folder'", (MANUAL_FOLDER_NAME,)
            ).fetchone()
            self.assertIsNotNone(folder)
            rows = conn.execute(
                "SELECT id, name, preview_type, scope_level, wanderer_min_tier FROM course_materials "
                "WHERE parent_id = ? AND node_type = 'file' ORDER BY name", (int(folder["id"]),)
            ).fetchall()
            self.assertEqual([row["name"] for row in rows], ["平台与天道手册.md", "散修进阶手册.md"])
            for row in rows:
                self.assertEqual(row["preview_type"], "markdown")
                self.assertEqual(row["scope_level"], "public")
                self.assertEqual(int(row["wanderer_min_tier"]), 0)
            indexed = conn.execute(
                "SELECT COUNT(*) AS n FROM material_search_documents d JOIN course_materials m ON m.id = d.material_id "
                "WHERE d.status = 'ready' AND d.chunk_count > 0 AND m.name LIKE '%手册.md'"
            ).fetchone()
            self.assertEqual(int(indexed["n"]), 2)

            wanderer = self.wanderer(conn, "chenmo")
            names = {item["material_name"] for item in readable_materials(conn, wanderer)}
            self.assertIn("散修进阶手册.md", names)
            self.assertIn("平台与天道手册.md", names)

    def test_seeding_is_idempotent(self):
        with self.connect() as conn:
            self._seed(conn)
            again = self._seed(conn)
            self.assertEqual(again["status"], "ok")
            self.assertEqual((again["created"], again["updated"]), (0, 0))

    def test_stage_band_controls_visibility(self):
        with self.connect() as conn:
            self._seed(conn)
            conn.execute(
                "UPDATE course_materials SET wanderer_min_tier = 5, wanderer_max_tier = 6 "
                "WHERE name = '散修进阶手册.md'"
            )
            conn.commit()
            wanderer = self.wanderer(conn, "chenmo")
            low = {**wanderer, "tier": 0}
            high = {**wanderer, "tier": 5}
            out_of_band = {**wanderer, "tier": 7}
            low_names = {item["material_name"] for item in readable_materials(conn, low)}
            high_names = {item["material_name"] for item in readable_materials(conn, high)}
            out_names = {item["material_name"] for item in readable_materials(conn, out_of_band)}
            self.assertNotIn("散修进阶手册.md", low_names)
            self.assertIn("散修进阶手册.md", high_names)
            self.assertNotIn("散修进阶手册.md", out_names)
            self.assertIn("平台与天道手册.md", low_names)

    def test_manuals_document_the_real_ladder(self):
        advancement = advancement_manual_markdown()
        self.assertIn("升阶条件", advancement)
        self.assertIn("| 第 9 境 | 第 10 境 |", advancement)
        self.assertIn(str(cultivation_target(9)), advancement)
        self.assertIn("近七日有效互动", advancement)
        self.assertIn("不会重置已经度过的时间", advancement)
        platform = platform_manual_markdown()
        self.assertIn("不能做什么", platform)
        self.assertIn("天道", platform)
        self.assertIn("冒充", platform)


if __name__ == "__main__":
    unittest.main()
