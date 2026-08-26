import os
import sqlite3
import unittest
from unittest import mock

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.services import material_hub_service as hub


def _fake_scope(conn, teacher_id):
    return {
        "school_code": "gxufl",
        "school_name": "GXUFL",
        "college": "信息工程学院",
        "department": "网络工程系",
    }


class MaterialHubServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER,
                teacher_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                material_path TEXT NOT NULL,
                node_type TEXT NOT NULL,
                preview_type TEXT DEFAULT 'binary',
                scope_level TEXT DEFAULT 'private',
                school_code TEXT DEFAULT 'gxufl',
                college TEXT DEFAULT '',
                department TEXT DEFAULT '',
                updated_at TEXT
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                document_type TEXT,
                document_type_label TEXT DEFAULT '',
                source_file_name TEXT DEFAULT '',
                content_markdown TEXT DEFAULT '',
                parse_status TEXT,
                package_material_id INTEGER,
                source_material_id INTEGER,
                parsed_material_id INTEGER,
                updated_at TEXT
            );
            CREATE TABLE session_material_generation_tasks (
                id INTEGER PRIMARY KEY,
                generated_material_id INTEGER
            );
            CREATE TABLE lesson_plans (
                id TEXT PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                tags_json TEXT DEFAULT '[]',
                cover_json TEXT DEFAULT '{}',
                scope_level TEXT DEFAULT 'private',
                school_code TEXT DEFAULT 'gxufl',
                college TEXT DEFAULT '',
                department TEXT DEFAULT '',
                status TEXT DEFAULT 'draft',
                updated_at TEXT
            );
            CREATE TABLE exam_papers (
                id TEXT PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                tags_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'draft',
                updated_at TEXT
            );
            CREATE TABLE textbooks (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                publisher TEXT DEFAULT '',
                updated_at TEXT
            );
            """
        )
        self.conn.execute("INSERT INTO teachers (id, name) VALUES (1, '张老师')")
        self.conn.executemany(
            """
            INSERT INTO course_materials
                (id, parent_id, teacher_id, name, material_path, node_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (21, None, 1, "Python课程学习文档", "Python课程学习文档", "folder", "2026-08-02T10:00:00"),
                (11, None, 1, "AI解析-评学表", "AI解析-评学表", "folder", "2026-08-01T10:00:00"),
                (12, 11, 1, "readme.md", "AI解析-评学表/readme.md", "file", "2026-08-01T10:00:00"),
                (31, None, 1, "课堂总结-第3课.md", "课堂总结-第3课.md", "file", "2026-08-03T10:00:00"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO material_ai_import_records
                (id, document_type, document_type_label, source_file_name, content_markdown,
                 parse_status, package_material_id, source_material_id, parsed_material_id, updated_at)
            VALUES (1, 'grading_rubric', '评分细则', '细则.docx', 'Python 期末评分细则内容',
                    'completed', 11, 12, 12, '2026-08-01T10:00:00')
            """
        )
        self.conn.execute(
            "INSERT INTO session_material_generation_tasks (id, generated_material_id) VALUES (1, 31)"
        )
        self.conn.execute(
            """
            INSERT INTO lesson_plans (id, teacher_id, title, updated_at)
            VALUES ('lp1', 1, 'Python 程序设计教案', '2026-08-04T10:00:00')
            """
        )
        self.conn.execute(
            "INSERT INTO exam_papers (id, teacher_id, title, updated_at) VALUES ('ep1', 1, 'Python 期末试卷', '2026-08-05T10:00:00')"
        )
        self.conn.execute(
            "INSERT INTO textbooks (id, teacher_id, title, publisher, updated_at) VALUES (1, 1, 'Python 教程', '人民邮电', '2026-08-06T10:00:00')"
        )

        self.patches = [
            mock.patch.object(hub, "load_teacher_org_scope", _fake_scope),
            mock.patch.object(hub, "is_super_admin_teacher", lambda conn, teacher_id: False),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        self.conn.close()

    def _user(self):
        return {"id": 1, "name": "张老师", "role": "teacher"}

    def test_learning_docs_exclude_postclass_and_postclass_only_has_them(self) -> None:
        result = hub.search_material_hub(
            self.conn, self._user(), categories=["learning_docs", "postclass"]
        )
        by_key = {group["key"]: group for group in result["groups"]}
        learning_titles = [item["title"] for item in by_key["learning_docs"]["items"]]
        postclass_titles = [item["title"] for item in by_key["postclass"]["items"]]
        self.assertIn("Python课程学习文档", learning_titles)
        self.assertNotIn("AI解析-评学表", learning_titles)
        self.assertNotIn("课堂总结-第3课.md", learning_titles)
        self.assertIn("AI解析-评学表", postclass_titles)
        self.assertIn("课堂总结-第3课.md", postclass_titles)

    def test_keyword_search_spans_categories(self) -> None:
        result = hub.search_material_hub(
            self.conn,
            self._user(),
            query="Python",
            categories=["learning_docs", "lesson_plans", "exam_papers", "textbooks", "grading_rubrics"],
        )
        matched = {group["key"] for group in result["groups"]}
        self.assertIn("learning_docs", matched)
        self.assertIn("lesson_plans", matched)
        self.assertIn("exam_papers", matched)
        self.assertIn("textbooks", matched)
        self.assertIn("grading_rubrics", matched)
        self.assertGreaterEqual(result["total"], 5)

    def test_scope_filter_and_bad_category_resilience(self) -> None:
        # 全部材料都是 private → 按「本校」过滤应为空；
        # gongwen 检索器会自建 schema 返回空结果，无论如何整次搜索不允许抛异常。
        result = hub.search_material_hub(
            self.conn,
            self._user(),
            categories=["learning_docs", "gongwen"],
            scope_filter="school",
        )
        self.assertEqual(0, result["total"])
        self.assertEqual(0, result["counts"].get("gongwen", 0))

    def test_normalize_categories_defaults_to_all(self) -> None:
        self.assertEqual(list(hub.CATEGORY_KEYS), hub.normalize_hub_categories(""))
        self.assertEqual(["gongwen"], hub.normalize_hub_categories("gongwen,unknown"))


if __name__ == "__main__":
    unittest.main()
