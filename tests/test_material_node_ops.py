import asyncio
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers.materials_parts import node_ops
from classroom_app.routers.materials_parts import learning as learning_router
from classroom_app.routers.materials_parts.generation_helpers import (
    _build_ai_material_rewrite_system_prompt,
    _normalize_material_scope_level,
    _normalize_rewrite_strictness,
)


TEACHER = {"id": 1, "role": "teacher", "name": "T1"}


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teachers (id INTEGER PRIMARY KEY, is_active INTEGER DEFAULT 1, is_super_admin INTEGER DEFAULT 0,
            school_code TEXT DEFAULT '', school_name TEXT DEFAULT '', college TEXT DEFAULT '', department TEXT DEFAULT '');
        CREATE TABLE course_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER, parent_id INTEGER, root_id INTEGER,
            material_path TEXT, name TEXT, node_type TEXT,
            mime_type TEXT DEFAULT '', preview_type TEXT DEFAULT '', ai_capability TEXT DEFAULT '',
            file_ext TEXT DEFAULT '', file_hash TEXT, file_size INTEGER DEFAULT 0,
            ai_parse_status TEXT DEFAULT 'idle', ai_parse_result_json TEXT,
            check_questions_json TEXT DEFAULT '', check_questions_status TEXT DEFAULT 'idle',
            check_questions_error TEXT DEFAULT '', check_questions_generated_at TEXT,
            ai_optimize_status TEXT DEFAULT 'idle', ai_optimized_markdown TEXT,
            owner_role TEXT DEFAULT 'teacher', owner_user_pk INTEGER,
            scope_level TEXT DEFAULT 'private',
            school_code TEXT DEFAULT '', school_name TEXT DEFAULT '', college TEXT DEFAULT '', department TEXT DEFAULT '',
            published_at TEXT, created_at TEXT, updated_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO teachers (id) VALUES (1)")
    conn.commit()
    return conn


class _NodeOpsPatches:
    """Patch node_ops externals so endpoints run against a bare sqlite conn."""

    def __init__(self, conn):
        self.conn = conn
        self.written_files = {}

        @contextmanager
        def fake_get_db_connection():
            yield conn

        async def fake_write_material_file(file_hash, payload_bytes):
            self.written_files[file_hash] = payload_bytes

        self._patches = [
            patch.object(node_ops, "get_db_connection", fake_get_db_connection),
            patch.object(node_ops, "refresh_root_git_metadata", lambda *a, **k: {}),
            patch.object(node_ops, "_fetch_material_response_item", lambda conn_, mid, user: {"id": int(mid)}),
            patch.object(node_ops, "load_teacher_org_scope", lambda conn_, tid: {
                "school_code": "gx", "school_name": "GX", "college": "IT学院", "department": "软件工程系",
            }),
            patch.object(node_ops, "_write_material_file", fake_write_material_file),
        ]

    def __enter__(self):
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self._patches:
            item.stop()
        return False


def _row(conn, material_id):
    return conn.execute("SELECT * FROM course_materials WHERE id = ?", (material_id,)).fetchone()


class MaterialNodeOpsTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.patcher = _NodeOpsPatches(self.conn)
        self.patcher.__enter__()

    def tearDown(self):
        self.patcher.__exit__()
        self.conn.close()

    # -------- helpers --------
    def _create_folder(self, name, parent_id=None):
        result = asyncio.run(
            node_ops.create_material_folder(node_ops.MaterialFolderCreateRequest(name=name, parent_id=parent_id), TEACHER)
        )
        return int(result["material"]["id"])

    def _create_file(self, name, parent_id=None, content=""):
        result = asyncio.run(
            node_ops.create_material_markdown_file(
                node_ops.MaterialFileCreateRequest(name=name, parent_id=parent_id, content=content), TEACHER
            )
        )
        return int(result["material"]["id"])

    def _move(self, material_id, target_parent_id):
        return asyncio.run(
            node_ops.move_material_node(material_id, node_ops.MaterialMoveRequest(target_parent_id=target_parent_id), TEACHER)
        )

    # -------- name / scope normalization --------
    def test_normalize_node_name_rejects_path_chars(self):
        with self.assertRaises(HTTPException):
            node_ops._normalize_node_name("a/b")
        with self.assertRaises(HTTPException):
            node_ops._normalize_node_name(".git")
        with self.assertRaises(HTTPException):
            node_ops._normalize_node_name("   ")
        self.assertEqual(node_ops._normalize_node_name(" 第4次课 "), "第4次课")

    def test_scope_level_normalization(self):
        self.assertEqual(_normalize_material_scope_level("public"), "public")
        self.assertEqual(_normalize_material_scope_level("SCHOOL"), "school")
        self.assertEqual(_normalize_material_scope_level("bogus"), "private")

    def test_rewrite_strictness(self):
        self.assertEqual(_normalize_rewrite_strictness("strict"), "strict")
        self.assertEqual(_normalize_rewrite_strictness("nope"), "balanced")
        strict_prompt = _build_ai_material_rewrite_system_prompt("optimize", "strict")
        self.assertIn("一字不改", strict_prompt)
        polish_prompt = _build_ai_material_rewrite_system_prompt("polish")
        self.assertIn("润色", polish_prompt)

    # -------- create --------
    def test_create_root_folder_is_own_root_and_private(self):
        folder_id = self._create_folder("课程A")
        row = _row(self.conn, folder_id)
        self.assertEqual(row["node_type"], "folder")
        self.assertIsNone(row["parent_id"])
        self.assertEqual(int(row["root_id"]), folder_id)
        self.assertEqual(row["scope_level"], "private")
        self.assertEqual(row["material_path"], "课程A")

    def test_nested_folder_inherits_root_scope(self):
        root_id = self._create_folder("课程B")
        self.conn.execute("UPDATE course_materials SET scope_level='public' WHERE id=?", (root_id,))
        self.conn.commit()
        child_id = self._create_folder("第1章", parent_id=root_id)
        row = _row(self.conn, child_id)
        self.assertEqual(int(row["parent_id"]), root_id)
        self.assertEqual(int(row["root_id"]), root_id)
        self.assertEqual(row["scope_level"], "public")
        self.assertEqual(row["material_path"], "课程B/第1章")

    def test_create_markdown_file_appends_extension(self):
        root_id = self._create_folder("课程C")
        file_id = self._create_file("讲义", parent_id=root_id)
        row = _row(self.conn, file_id)
        self.assertEqual(row["name"], "讲义.md")
        self.assertEqual(row["preview_type"], "markdown")
        self.assertEqual(row["material_path"], "课程C/讲义.md")
        self.assertTrue(self.patcher.written_files)

    def test_duplicate_name_gets_unique_suffix(self):
        self._create_folder("重复")
        second_id = self._create_folder("重复")
        row = _row(self.conn, second_id)
        self.assertEqual(row["name"], "重复 (2)")

    # -------- move --------
    def test_move_file_updates_path_root_and_scope(self):
        src_root = self._create_folder("源目录")
        dst_root = self._create_folder("目标目录")
        self.conn.execute("UPDATE course_materials SET scope_level='school' WHERE id=?", (dst_root,))
        self.conn.commit()
        file_id = self._create_file("讲义", parent_id=src_root)

        result = self._move(file_id, dst_root)
        self.assertEqual(result["status"], "success")
        row = _row(self.conn, file_id)
        self.assertEqual(int(row["parent_id"]), dst_root)
        self.assertEqual(int(row["root_id"]), dst_root)
        self.assertEqual(row["material_path"], "目标目录/讲义.md")
        self.assertEqual(row["scope_level"], "school")

    def test_move_folder_moves_subtree(self):
        src_root = self._create_folder("A")
        child = self._create_folder("子层", parent_id=src_root)
        file_id = self._create_file("笔记", parent_id=child)
        dst_root = self._create_folder("B")

        self._move(child, dst_root)
        child_row = _row(self.conn, child)
        file_row = _row(self.conn, file_id)
        self.assertEqual(child_row["material_path"], "B/子层")
        self.assertEqual(file_row["material_path"], "B/子层/笔记.md")
        self.assertEqual(int(file_row["root_id"]), dst_root)

    def test_move_into_own_descendant_rejected(self):
        root = self._create_folder("外层")
        inner = self._create_folder("内层", parent_id=root)
        with self.assertRaises(HTTPException) as ctx:
            self._move(root, inner)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_move_to_root_becomes_own_root(self):
        root = self._create_folder("外层2")
        inner = self._create_folder("内层2", parent_id=root)
        self._move(inner, None)
        row = _row(self.conn, inner)
        self.assertIsNone(row["parent_id"])
        self.assertEqual(int(row["root_id"]), inner)
        self.assertEqual(row["material_path"], "内层2")

    def test_move_conflict_auto_renames(self):
        root_a = self._create_folder("甲")
        root_b = self._create_folder("乙")
        self._create_file("讲义", parent_id=root_b)
        file_id = self._create_file("讲义", parent_id=root_a)
        result = self._move(file_id, root_b)
        self.assertTrue(result["renamed"])
        row = _row(self.conn, file_id)
        self.assertEqual(row["name"], "讲义 (2).md")
        self.assertEqual(row["material_path"], "乙/讲义 (2).md")


class MaterialLearningBindingContextTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER,
                teacher_id INTEGER, semester TEXT, home_learning_material_id INTEGER);
            CREATE TABLE class_offering_sessions (id INTEGER PRIMARY KEY, class_offering_id INTEGER,
                order_index INTEGER, title TEXT, learning_material_id INTEGER);
            CREATE TABLE class_offering_learning_materials (id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER, session_id INTEGER, material_id INTEGER, sort_order INTEGER,
                created_by_teacher_id INTEGER, created_at TEXT, updated_at TEXT);
            """
        )
        self.conn.execute("INSERT INTO classes (id,name) VALUES (1,'软工2406')")
        self.conn.execute("INSERT INTO courses (id,name) VALUES (1,'服务器配置')")
        self.conn.execute(
            "INSERT INTO class_offerings (id,class_id,course_id,teacher_id,semester,home_learning_material_id)"
            " VALUES (5,1,1,1,'2025-2026-1',NULL)"
        )
        self.conn.execute(
            "INSERT INTO class_offering_sessions (id,class_offering_id,order_index,title,learning_material_id)"
            " VALUES (10,5,1,'第一课',NULL)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_bindings_merge_list_table_and_legacy_columns(self):
        # 列表表绑定到课次 10；旧单列绑定首页
        self.conn.execute(
            "INSERT INTO class_offering_learning_materials (class_offering_id,session_id,material_id,sort_order,created_by_teacher_id)"
            " VALUES (5,10,100,0,1)"
        )
        self.conn.execute("UPDATE class_offerings SET home_learning_material_id=100 WHERE id=5")
        self.conn.commit()
        context = learning_router._load_material_learning_binding_context(self.conn, 100, 1)
        offering = context["offerings"][0]
        self.assertTrue(offering["home_bound"])
        self.assertEqual(offering["bound_session_ids"], [10])
        self.assertIn((5, 0), context["bound_targets"])
        self.assertIn((5, 10), context["bound_targets"])

    def test_bindings_empty_material(self):
        context = learning_router._load_material_learning_binding_context(self.conn, 999, 1)
        offering = context["offerings"][0]
        self.assertFalse(offering["home_bound"])
        self.assertEqual(offering["bound_session_ids"], [])
        self.assertEqual(context["bound_targets"], [])


if __name__ == "__main__":
    unittest.main()
