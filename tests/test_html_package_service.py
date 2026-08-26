import sqlite3
import unittest

from fastapi import HTTPException

from classroom_app.services.html_package_service import (
    apply_package_session_bindings,
    extract_html_text,
    find_html_package_root,
    lesson_number_from_dir_name,
    lesson_number_from_entry_name,
    parse_html_package,
)
from classroom_app.services.material_render_service import resolve_render_target
from classroom_app.services.session_material_generation_service import (
    _normalize_generated_html_package_nodes,
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
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            home_learning_material_id INTEGER
        );
        CREATE TABLE class_offering_sessions (
            id INTEGER PRIMARY KEY,
            class_offering_id INTEGER NOT NULL,
            order_index INTEGER NOT NULL,
            title TEXT DEFAULT '',
            learning_material_id INTEGER,
            updated_at TEXT
        );
        CREATE TABLE course_material_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            class_offering_id INTEGER NOT NULL,
            assigned_by_teacher_id INTEGER,
            created_at TEXT,
            UNIQUE(material_id, class_offering_id)
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


def _seed_package(conn, *, root_id=10, prefix="pkg"):
    """pkg/main.html + lesson_1/lesson_1.html + lesson_02/lesson_02.html + common/style.css"""
    _insert(conn, id=root_id, teacher_id=1, parent_id=None, root_id=root_id, name=prefix,
            material_path=prefix, node_type="folder", preview_type="folder")
    _insert(conn, id=root_id + 1, teacher_id=1, parent_id=root_id, root_id=root_id, name="main.html",
            material_path=f"{prefix}/main.html", node_type="file", preview_type="text",
            file_ext="html", mime_type="text/html", file_hash="h-main")
    _insert(conn, id=root_id + 2, teacher_id=1, parent_id=root_id, root_id=root_id, name="lesson_1",
            material_path=f"{prefix}/lesson_1", node_type="folder", preview_type="folder")
    _insert(conn, id=root_id + 3, teacher_id=1, parent_id=root_id + 2, root_id=root_id, name="lesson_1.html",
            material_path=f"{prefix}/lesson_1/lesson_1.html", node_type="file", preview_type="text",
            file_ext="html", mime_type="text/html", file_hash="h-l1")
    _insert(conn, id=root_id + 4, teacher_id=1, parent_id=root_id, root_id=root_id, name="lesson_02",
            material_path=f"{prefix}/lesson_02", node_type="folder", preview_type="folder")
    _insert(conn, id=root_id + 5, teacher_id=1, parent_id=root_id + 4, root_id=root_id, name="lesson_02.html",
            material_path=f"{prefix}/lesson_02/lesson_02.html", node_type="file", preview_type="text",
            file_ext="html", mime_type="text/html", file_hash="h-l2")
    _insert(conn, id=root_id + 6, teacher_id=1, parent_id=root_id, root_id=root_id, name="common",
            material_path=f"{prefix}/common", node_type="folder", preview_type="folder")
    _insert(conn, id=root_id + 7, teacher_id=1, parent_id=root_id + 6, root_id=root_id, name="style.css",
            material_path=f"{prefix}/common/style.css", node_type="file", preview_type="text",
            file_ext="css", mime_type="text/css", file_hash="h-css")
    return root_id


class HtmlPackageParseTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.root_id = _seed_package(self.conn)

    def _row(self, material_id):
        return dict(self.conn.execute("SELECT * FROM course_materials WHERE id=?", (material_id,)).fetchone())

    def test_lesson_name_parsers(self):
        self.assertEqual(lesson_number_from_dir_name("lesson_11"), 11)
        self.assertEqual(lesson_number_from_dir_name("Lesson-03"), 3)
        self.assertEqual(lesson_number_from_dir_name("common"), 0)
        self.assertEqual(lesson_number_from_entry_name("lesson_12.html"), 12)
        self.assertEqual(lesson_number_from_entry_name("main.html"), 0)

    def test_parse_valid_package(self):
        package = parse_html_package(self.conn, self._row(self.root_id))
        self.assertIsNotNone(package)
        self.assertEqual(package["root_node_id"], self.root_id)
        self.assertEqual(package["main_relpath"], "main.html")
        self.assertEqual(sorted(package["lesson_by_number"]), [1, 2])
        self.assertEqual(package["lesson_by_number"][2]["entry_relpath"], "lesson_02/lesson_02.html")
        self.assertEqual(package["shared_dirs"], ["common"])

    def test_folder_without_main_is_not_package(self):
        self.conn.execute("DELETE FROM course_materials WHERE id=?", (self.root_id + 1,))
        self.assertIsNone(parse_html_package(self.conn, self._row(self.root_id)))

    def test_folder_without_lessons_is_not_package(self):
        conn = _make_conn()
        _insert(conn, id=1, teacher_id=1, parent_id=None, root_id=1, name="site",
                material_path="site", node_type="folder", preview_type="folder")
        _insert(conn, id=2, teacher_id=1, parent_id=1, root_id=1, name="main.html",
                material_path="site/main.html", node_type="file", preview_type="text",
                file_ext="html", file_hash="h")
        row = dict(conn.execute("SELECT * FROM course_materials WHERE id=1").fetchone())
        self.assertIsNone(parse_html_package(conn, row))

    def test_find_root_from_lesson_entry(self):
        entry = self._row(self.root_id + 5)
        package = find_html_package_root(self.conn, entry)
        self.assertIsNotNone(package)
        self.assertEqual(package["root_node_id"], self.root_id)

    def test_render_target_anchors_to_package_root(self):
        entry = self._row(self.root_id + 5)
        target = resolve_render_target(self.conn, entry)
        self.assertIsNotNone(target)
        self.assertEqual(target["node_id"], self.root_id)
        self.assertEqual(target["render_url"], f"/materials/render/{self.root_id}/lesson_02/lesson_02.html")
        self.assertIn(f"/materials/render-view/{self.root_id}?path=", target["shell_url"])

    def test_package_root_prefers_main_html(self):
        # 加一个 index.html，main.html 仍应是入口。
        _insert(self.conn, id=99, teacher_id=1, parent_id=self.root_id, root_id=self.root_id, name="index.html",
                material_path="pkg/index.html", node_type="file", preview_type="text",
                file_ext="html", file_hash="h-idx")
        target = resolve_render_target(self.conn, self._row(self.root_id))
        self.assertEqual(target["entry_name"], "main.html")


class HtmlPackageBindingTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.root_id = _seed_package(self.conn)
        self.conn.execute("INSERT INTO class_offerings (id, teacher_id) VALUES (5, 1)")
        for session_id, order_index in ((51, 1), (52, 2), (53, 3)):
            self.conn.execute(
                "INSERT INTO class_offering_sessions (id, class_offering_id, order_index, title) VALUES (?, 5, ?, ?)",
                (session_id, order_index, f"第{order_index}次课"),
            )

    def test_apply_bindings(self):
        package = parse_html_package(
            self.conn,
            dict(self.conn.execute("SELECT * FROM course_materials WHERE id=?", (self.root_id,)).fetchone()),
        )
        result = apply_package_session_bindings(
            self.conn, package=package, offering_ids=[5], teacher_id=1
        )
        self.assertEqual(result["total_home_assignments"], 1)
        self.assertEqual(result["total_assignments"], 2)
        home_id = self.conn.execute("SELECT home_learning_material_id FROM class_offerings WHERE id=5").fetchone()[0]
        self.assertEqual(home_id, self.root_id + 1)
        lesson1 = self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=51").fetchone()[0]
        lesson2 = self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=52").fetchone()[0]
        lesson3 = self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=53").fetchone()[0]
        self.assertEqual(lesson1, self.root_id + 3)
        self.assertEqual(lesson2, self.root_id + 5)
        self.assertIsNone(lesson3)
        # 课堂材料分配锚定到包根（学生可访问整包含共享资源）。
        assigned = {
            int(row["material_id"])
            for row in self.conn.execute(
                "SELECT material_id FROM course_material_assignments WHERE class_offering_id = 5"
            ).fetchall()
        }
        self.assertEqual(assigned, {self.root_id})

    def test_unauthorized_offering_skipped(self):
        package = parse_html_package(
            self.conn,
            dict(self.conn.execute("SELECT * FROM course_materials WHERE id=?", (self.root_id,)).fetchone()),
        )
        result = apply_package_session_bindings(
            self.conn, package=package, offering_ids=[999], teacher_id=1
        )
        self.assertEqual(result["total_assignments"], 0)
        self.assertEqual(len(result["skipped_assignments"]), 1)


class HtmlPackageGenerationValidationTests(unittest.TestCase):
    VALID_HTML = "<!DOCTYPE html><html><head><title>t</title></head><body>ok</body></html>"

    def test_valid_result_normalized(self):
        bind_path, nodes = _normalize_generated_html_package_nodes(
            {
                "files": [
                    {"path": "lesson_03/lesson_03.html", "content": self.VALID_HTML},
                    {"path": "lesson_3/assets/extra.css", "content": "body{}"},
                ]
            },
            lesson_number=3,
        )
        self.assertEqual(bind_path, "lesson_3/lesson_3.html")
        paths = [node["path"] for node in nodes]
        self.assertIn("lesson_3", paths)  # 文件夹节点
        self.assertIn("lesson_3/lesson_3.html", paths)
        self.assertIn("lesson_3/assets/extra.css", paths)
        entry_node = next(node for node in nodes if node["path"] == "lesson_3/lesson_3.html")
        self.assertTrue(entry_node["bind"])

    def test_missing_entry_rejected(self):
        with self.assertRaises(HTTPException):
            _normalize_generated_html_package_nodes(
                {"files": [{"path": "lesson_3/notes.md", "content": "# x"}]},
                lesson_number=3,
            )

    def test_wrong_lesson_dir_rejected(self):
        with self.assertRaises(HTTPException):
            _normalize_generated_html_package_nodes(
                {"files": [{"path": "lesson_4/lesson_4.html", "content": self.VALID_HTML}]},
                lesson_number=3,
            )

    def test_outside_lesson_dir_rejected(self):
        with self.assertRaises(HTTPException):
            _normalize_generated_html_package_nodes(
                {"files": [
                    {"path": "lesson_3/lesson_3.html", "content": self.VALID_HTML},
                    {"path": "common/style.css", "content": "body{}"},
                ]},
                lesson_number=3,
            )

    def test_incomplete_html_rejected(self):
        with self.assertRaises(HTTPException):
            _normalize_generated_html_package_nodes(
                {"files": [{"path": "lesson_3/lesson_3.html", "content": "<div>只有片段</div>"}]},
                lesson_number=3,
            )

    def test_binary_extension_rejected(self):
        with self.assertRaises(HTTPException):
            _normalize_generated_html_package_nodes(
                {"files": [
                    {"path": "lesson_3/lesson_3.html", "content": self.VALID_HTML},
                    {"path": "lesson_3/pic.png", "content": "xxxx"},
                ]},
                lesson_number=3,
            )


class ExtractHtmlTextTests(unittest.TestCase):
    def test_strips_tags_scripts_and_entities(self):
        text = extract_html_text(
            "<html><head><style>body{}</style><script>var a=1;</script></head>"
            "<body><h1>标题&amp;示例</h1><p>第一段</p><p>第二段</p></body></html>"
        )
        self.assertIn("标题&示例", text)
        self.assertIn("第一段", text)
        self.assertNotIn("var a=1", text)
        self.assertNotIn("<p>", text)


if __name__ == "__main__":
    unittest.main()
