import sqlite3
import unittest

from classroom_app.services.material_render_service import (
    attach_render_metadata,
    resolve_render_file,
    resolve_render_target,
    resolve_render_targets,
)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE course_materials (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            parent_id INTEGER,
            root_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            material_path TEXT NOT NULL,
            node_type TEXT NOT NULL,
            preview_type TEXT DEFAULT '',
            file_ext TEXT DEFAULT '',
            mime_type TEXT DEFAULT '',
            file_hash TEXT
        );
        """
    )
    return conn


def _insert(conn, **kwargs):
    cols = ", ".join(kwargs)
    placeholders = ", ".join("?" for _ in kwargs)
    conn.execute(
        f"INSERT INTO course_materials ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )


class MaterialRenderServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        # Front-end project folder: project/ with index.html + assets/style.css
        _insert(self.conn, id=1, teacher_id=1, parent_id=None, root_id=1, name="project",
                material_path="project", node_type="folder", preview_type="folder")
        _insert(self.conn, id=2, teacher_id=1, parent_id=1, root_id=1, name="index.html",
                material_path="project/index.html", node_type="file", preview_type="text",
                file_ext="html", mime_type="text/html", file_hash="h-index")
        _insert(self.conn, id=3, teacher_id=1, parent_id=1, root_id=1, name="style.css",
                material_path="project/style.css", node_type="file", preview_type="text",
                file_ext="css", mime_type="text/css", file_hash="h-css")
        # Standalone single html file at root
        _insert(self.conn, id=4, teacher_id=1, parent_id=None, root_id=4, name="demo.html",
                material_path="demo.html", node_type="file", preview_type="text",
                file_ext="html", mime_type="text/html", file_hash="h-demo")
        # A plain markdown doc — must NOT be renderable
        _insert(self.conn, id=5, teacher_id=1, parent_id=None, root_id=5, name="readme.md",
                material_path="readme.md", node_type="file", preview_type="markdown",
                file_ext="md", mime_type="text/markdown", file_hash="h-md")

    def test_folder_with_index_is_renderable(self):
        folder = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone())
        target = resolve_render_target(self.conn, folder)
        self.assertIsNotNone(target)
        self.assertEqual(target["kind"], "html")
        self.assertEqual(target["entry_id"], 2)
        self.assertEqual(target["render_url"], "/materials/render/1/")

    def test_single_html_file_is_renderable(self):
        row = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=4").fetchone())
        target = resolve_render_target(self.conn, row)
        self.assertIsNotNone(target)
        self.assertEqual(target["entry_id"], 4)

    def test_nested_folder_requires_exact_path_and_accepts_legacy_html_extension(self):
        for node_id, name in enumerate(("unitA1", "unit_1", "unit%1", "UnitA1"), start=6):
            _insert(self.conn, id=node_id, teacher_id=1, parent_id=1, root_id=1, name=name,
                    material_path=f"project/{name}", node_type="folder", preview_type="folder")
        _insert(self.conn, id=10, teacher_id=1, parent_id=6, root_id=1, name="Index.HTM",
                material_path="project/unitA1/Index.HTM", node_type="file", preview_type="text")

        folders = self.conn.execute("SELECT * FROM course_materials WHERE id BETWEEN 6 AND 9 ORDER BY id").fetchall()
        self.assertEqual(10, resolve_render_target(self.conn, folders[0])["entry_id"])
        for folder in folders[1:]:
            with self.subTest(name=folder["name"]):
                self.assertIsNone(resolve_render_target(self.conn, folder))
        self.assertEqual(
            {int(row["id"]): resolve_render_target(self.conn, row) for row in folders},
            resolve_render_targets(self.conn, folders),
        )

    def test_markdown_is_not_renderable(self):
        row = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=5").fetchone())
        self.assertIsNone(resolve_render_target(self.conn, row))

    def test_resolve_entry_file_for_folder(self):
        folder = self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone()
        served = resolve_render_file(self.conn, folder, "")
        self.assertEqual(int(served["id"]), 2)  # index.html

    def test_resolve_entry_reuses_the_current_request_target(self):
        folder = self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone()
        target = resolve_render_target(self.conn, folder)
        queries = []
        self.conn.set_trace_callback(queries.append)
        self.assertEqual(2, resolve_render_file(self.conn, folder, render_target=target)["id"])
        self.conn.set_trace_callback(None)
        self.assertEqual(1, len(queries))

    def test_resolve_subpath_asset(self):
        folder = self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone()
        served = resolve_render_file(self.conn, folder, "style.css")
        self.assertEqual(int(served["id"]), 3)

    def test_resolve_single_file_entry(self):
        row = self.conn.execute("SELECT * FROM course_materials WHERE id=4").fetchone()
        served = resolve_render_file(self.conn, row, "")
        self.assertEqual(int(served["id"]), 4)

    def test_path_traversal_is_blocked(self):
        folder = self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone()
        with self.assertRaises(Exception):
            # normalize_material_path rejects parent jumps
            resolve_render_file(self.conn, folder, "../demo.html")

    def test_attach_render_metadata(self):
        items = [dict(self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone())]
        attach_render_metadata(self.conn, items)
        self.assertTrue(items[0]["is_renderable"])
        self.assertEqual(items[0]["render_url"], "/materials/render/1/")

    def test_bulk_folder_render_metadata_has_bounded_queries(self):
        for index in range(200):
            folder_id = 1000 + index * 2
            path = f"project/unit_{index}"
            _insert(self.conn, id=folder_id, teacher_id=1, parent_id=1, root_id=1,
                    name=f"unit_{index}", material_path=path, node_type="folder")
            _insert(self.conn, id=folder_id+1, teacher_id=1, parent_id=folder_id, root_id=1,
                    name="index.html", material_path=f"{path}/index.html", node_type="file", file_ext="html")
        items = [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE id>=1000 AND node_type='folder'")]
        queries = []
        self.conn.set_trace_callback(queries.append)
        attach_render_metadata(self.conn, items)
        self.conn.set_trace_callback(None)
        self.assertTrue(all(item["is_renderable"] for item in items))
        self.assertLessEqual(len(queries), 4, f"200 folders issued {len(queries)} SQL statements")

    def test_explicit_assets_do_not_search_for_default_html_entry(self):
        folder = self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone()
        queries = []
        self.conn.set_trace_callback(queries.append)
        for _ in range(20):
            self.assertEqual(3, resolve_render_file(self.conn, folder, "style.css")["id"])
        self.conn.set_trace_callback(None)
        self.assertEqual(20, len(queries), "Every explicit asset needs only its exact-path lookup")

    def test_package_batch_matches_single_resolution_and_reads_current_versions(self):
        for index in range(1, 201):
            folder_id = 1000 + index * 2
            path = f"project/lesson_{index}"
            _insert(self.conn, id=folder_id, teacher_id=1, parent_id=1, root_id=1,
                    name=f"lesson_{index}", material_path=path, node_type="folder")
            _insert(self.conn, id=folder_id+1, teacher_id=1, parent_id=folder_id, root_id=1,
                    name=f"lesson_{index}.html", material_path=f"{path}/lesson_{index}.html",
                    node_type="file", file_ext="html", file_hash=f"h-lesson-{index}")
        folders = self.conn.execute("SELECT * FROM course_materials WHERE id>=1000 AND node_type='folder' ORDER BY id").fetchall()
        queries = []
        self.conn.set_trace_callback(queries.append)
        expected = {int(row["id"]): resolve_render_target(self.conn, row) for row in folders}
        single_queries = len(queries)
        queries.clear()
        actual = resolve_render_targets(self.conn, folders)
        batch_queries = len(queries)
        self.conn.set_trace_callback(None)
        self.assertEqual(expected, actual)
        self.assertEqual(800, single_queries)
        self.assertLessEqual(batch_queries, 3)
        self.assertTrue(all(target["node_id"] == 1 for target in actual.values()))
        self.conn.execute("DELETE FROM course_materials WHERE id=1003")
        current = resolve_render_targets(self.conn, folders[:1])
        self.assertIsNone(current[1002], "A new request cannot reuse a deleted entry from an older structural index")

    def test_batch_preserves_package_name_fallback_for_legacy_extension_metadata(self):
        self.conn.execute("UPDATE course_materials SET file_ext='txt' WHERE id=2")
        _insert(self.conn, id=20, teacher_id=1, parent_id=1, root_id=1, name="lesson_1",
                material_path="project/lesson_1", node_type="folder")
        _insert(self.conn, id=21, teacher_id=1, parent_id=20, root_id=1, name="lesson_1.html",
                material_path="project/lesson_1/lesson_1.html", node_type="file", file_ext="html")
        lesson = self.conn.execute("SELECT * FROM course_materials WHERE id=20").fetchone()
        expected = resolve_render_target(self.conn, lesson)
        self.assertEqual(1, expected["node_id"])
        self.assertEqual(expected, resolve_render_targets(self.conn, [lesson])[20])


if __name__ == "__main__":
    unittest.main()
