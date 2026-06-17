import sqlite3
import unittest

from classroom_app.services.material_render_service import (
    attach_render_metadata,
    resolve_render_file,
    resolve_render_target,
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

    def test_markdown_is_not_renderable(self):
        row = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=5").fetchone())
        self.assertIsNone(resolve_render_target(self.conn, row))

    def test_resolve_entry_file_for_folder(self):
        folder = self.conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone()
        served = resolve_render_file(self.conn, folder, "")
        self.assertEqual(int(served["id"]), 2)  # index.html

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


if __name__ == "__main__":
    unittest.main()
