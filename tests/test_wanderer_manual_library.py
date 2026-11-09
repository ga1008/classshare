"""Built-in manuals in the learning library and stage-gated reading."""
import hashlib
import unittest
from unittest.mock import patch

from classroom_app.services.wanderer_actions_knowledge import readable_materials
from classroom_app.services.wanderer_life_core import cultivation_target
from classroom_app.services import wanderer_manual_library as manual_library
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
            self.assertEqual([row["name"] for row in rows], ["平台与天道手册.md", "散修进阶手册.md", "此方世界：散修世界手册.md"])
            for row in rows:
                self.assertEqual(row["preview_type"], "markdown")
                self.assertEqual(row["scope_level"], "public")
                self.assertEqual(int(row["wanderer_min_tier"]), 0)
            indexed = conn.execute(
                "SELECT COUNT(*) AS n FROM material_search_documents d JOIN course_materials m ON m.id = d.material_id "
                "WHERE d.status = 'ready' AND d.chunk_count > 0 AND m.name LIKE '%手册.md'"
            ).fetchone()
            self.assertEqual(int(indexed["n"]), 3)

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

    def test_reseeding_preserves_custom_content_permissions_and_index_without_render_or_store(self):
        with self.connect() as conn:
            folder_id = self._seed(conn)["folder_id"]
            try:
                rows = conn.execute("SELECT id, name FROM course_materials WHERE parent_id=? ORDER BY id", (folder_id,)).fetchall()
                for index, row in enumerate(rows):
                    custom = f"# 用户定制手册 {index}\n\n仅保留原有三条禁则。".encode("utf-8")
                    conn.execute("""UPDATE course_materials SET file_hash=?,file_size=?,updated_at=?,
                        material_path=?,scope_level='private',owner_role='teacher',owner_user_pk=999,
                        wanderer_min_tier=4,wanderer_max_tier=7,ai_parse_status='completed',
                        ai_parse_result_json=?,ai_optimize_status='completed',ai_optimized_markdown=? WHERE id=?""",
                        (hashlib.sha256(custom).hexdigest(), len(custom), "2026-10-01T09:15:00",
                         f"customized/manual-{index}.md", '{"custom":true}', custom.decode("utf-8"), row["id"]))
                before = [dict(row) for row in conn.execute("SELECT * FROM course_materials WHERE parent_id=? ORDER BY id", (folder_id,))]
                documents = [dict(row) for row in conn.execute("SELECT d.* FROM material_search_documents d JOIN course_materials m "
                    "ON m.id=d.material_id WHERE m.parent_id=? ORDER BY d.material_id", (folder_id,))]
                chunks = [dict(row) for row in conn.execute("SELECT c.* FROM material_search_chunks c JOIN course_materials m "
                    "ON m.id=c.material_id WHERE m.parent_id=? ORDER BY c.id", (folder_id,))]
                with patch.object(manual_library, "advancement_manual_markdown", side_effect=AssertionError("existing content must not render")) as advancement, \
                     patch.object(manual_library, "platform_manual_markdown", side_effect=AssertionError("existing content must not render")) as platform, \
                     patch.object(manual_library, "_store_markdown", side_effect=AssertionError("existing content must not store")) as store, \
                     patch("classroom_app.services.material_search_index_service.index_material_by_id", side_effect=AssertionError("existing content must not reindex")) as index_material:
                    for _ in range(2):
                        result = ensure_manual_materials(conn)
                        self.assertEqual((result["status"], result["created"], result["updated"]), ("ok", 0, 0))
                    for mocked in (advancement, platform, store, index_material):
                        mocked.assert_not_called()
                self.assertEqual(before, [dict(row) for row in conn.execute(
                    "SELECT * FROM course_materials WHERE parent_id=? ORDER BY id", (folder_id,))])
                self.assertEqual(documents, [dict(row) for row in conn.execute("SELECT d.* FROM material_search_documents d JOIN course_materials m "
                    "ON m.id=d.material_id WHERE m.parent_id=? ORDER BY d.material_id", (folder_id,))])
                self.assertEqual(chunks, [dict(row) for row in conn.execute("SELECT c.* FROM material_search_chunks c JOIN course_materials m "
                    "ON m.id=c.material_id WHERE m.parent_id=? ORDER BY c.id", (folder_id,))])
            finally:
                conn.rollback()

    def test_reseeding_repairs_legacy_null_root_without_replacing_manuals(self):
        with self.connect() as conn:
            seeded = self._seed(conn)
            folder_id = seeded["folder_id"]
            root_before = dict(conn.execute("SELECT * FROM course_materials WHERE id = ?", (folder_id,)).fetchone())
            files_before = [dict(row) for row in conn.execute(
                "SELECT id, file_hash, updated_at FROM course_materials WHERE parent_id = ? ORDER BY id",
                (folder_id,),
            ).fetchall()]
            documents_before = [dict(row) for row in conn.execute(
                "SELECT d.* FROM material_search_documents d JOIN course_materials m ON m.id = d.material_id "
                "WHERE m.parent_id = ? ORDER BY d.material_id", (folder_id,),
            ).fetchall()]
            chunks_before = [dict(row) for row in conn.execute(
                "SELECT c.* FROM material_search_chunks c JOIN course_materials m ON m.id = c.material_id "
                "WHERE m.parent_id = ? ORDER BY c.id", (folder_id,),
            ).fetchall()]
            self.assertEqual(len(files_before), 3)
            self.assertEqual(len(documents_before), 3)
            self.assertTrue(chunks_before)
            conn.execute("UPDATE course_materials SET root_id = NULL WHERE id = ?", (folder_id,))
            conn.commit()

            repaired = self._seed(conn)
            root = conn.execute("SELECT id, root_id FROM course_materials WHERE id = ?", (folder_id,)).fetchone()
            self.assertEqual(root["root_id"], folder_id)
            self.assertEqual(root_before, dict(conn.execute(
                "SELECT * FROM course_materials WHERE id = ?", (folder_id,),
            ).fetchone()))
            self.assertEqual(repaired["folder_id"], folder_id)
            self.assertEqual((repaired["created"], repaired["updated"]), (0, 0))
            self.assertEqual(files_before, [dict(row) for row in conn.execute(
                "SELECT id, file_hash, updated_at FROM course_materials WHERE parent_id = ? ORDER BY id",
                (folder_id,),
            ).fetchall()])
            self.assertEqual(documents_before, [dict(row) for row in conn.execute(
                "SELECT d.* FROM material_search_documents d JOIN course_materials m ON m.id = d.material_id "
                "WHERE m.parent_id = ? ORDER BY d.material_id", (folder_id,),
            ).fetchall()])
            self.assertEqual(chunks_before, [dict(row) for row in conn.execute(
                "SELECT c.* FROM material_search_chunks c JOIN course_materials m ON m.id = c.material_id "
                "WHERE m.parent_id = ? ORDER BY c.id", (folder_id,),
            ).fetchall()])
            again = self._seed(conn)
            self.assertEqual((again["created"], again["updated"]), (0, 0))
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM course_materials WHERE name = ? AND node_type = 'folder'", (MANUAL_FOLDER_NAME,),
            ).fetchone()[0], 1)

    def test_reseeding_completes_legacy_empty_null_root_folder(self):
        with self.connect() as conn:
            folder_id = self._seed(conn)["folder_id"]
            conn.execute("DELETE FROM material_search_chunks WHERE material_id IN "
                         "(SELECT id FROM course_materials WHERE parent_id = ?)", (folder_id,))
            conn.execute("DELETE FROM material_search_documents WHERE material_id IN "
                         "(SELECT id FROM course_materials WHERE parent_id = ?)", (folder_id,))
            conn.execute("DELETE FROM course_materials WHERE parent_id = ?", (folder_id,))
            conn.execute("UPDATE course_materials SET root_id = NULL WHERE id = ?", (folder_id,))
            conn.commit()

            repaired = self._seed(conn)
            self.assertEqual(repaired["folder_id"], folder_id)
            self.assertEqual((repaired["created"], repaired["updated"]), (3, 0))
            self.assertEqual(conn.execute(
                "SELECT root_id FROM course_materials WHERE id = ?", (folder_id,),
            ).fetchone()[0], folder_id)
            rows = conn.execute(
                "SELECT m.name, m.root_id, d.status, d.chunk_count FROM course_materials m "
                "JOIN material_search_documents d ON d.material_id = m.id WHERE m.parent_id = ? ORDER BY m.name",
                (folder_id,),
            ).fetchall()
            self.assertEqual([row["name"] for row in rows], ["平台与天道手册.md", "散修进阶手册.md", "此方世界：散修世界手册.md"])
            self.assertTrue(all(row["root_id"] == folder_id and row["status"] == "ready" and row["chunk_count"] > 0
                                for row in rows))
            again = self._seed(conn)
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
