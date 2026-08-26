import sqlite3
import unittest

from fastapi import HTTPException

from classroom_app.db import schema_session_learning_materials as _schema
from classroom_app.services import session_learning_materials_service as svc


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teachers (id INTEGER PRIMARY KEY, is_active INTEGER DEFAULT 1, is_super_admin INTEGER DEFAULT 0,
            school_code TEXT, school_name TEXT, college TEXT, department TEXT);
        CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER, teacher_id INTEGER,
            home_learning_material_id INTEGER);
        CREATE TABLE class_offering_sessions (id INTEGER PRIMARY KEY, class_offering_id INTEGER, order_index INTEGER,
            title TEXT, content TEXT, learning_material_id INTEGER, updated_at TEXT);
        CREATE TABLE course_materials (id INTEGER PRIMARY KEY, teacher_id INTEGER, parent_id INTEGER, root_id INTEGER,
            name TEXT, material_path TEXT, node_type TEXT, preview_type TEXT DEFAULT '', file_ext TEXT DEFAULT '',
            mime_type TEXT DEFAULT '', scope_level TEXT DEFAULT 'private', owner_role TEXT DEFAULT 'teacher',
            owner_user_pk INTEGER, school_code TEXT, school_name TEXT, college TEXT, department TEXT);
        CREATE TABLE course_material_assignments (material_id INTEGER, class_offering_id INTEGER,
            assigned_by_teacher_id INTEGER, created_at TEXT, UNIQUE(material_id, class_offering_id));
        """
    )
    conn.execute("INSERT INTO teachers (id) VALUES (1)")
    conn.execute("INSERT INTO class_offerings (id,class_id,course_id,teacher_id) VALUES (5,1,1,1)")
    conn.execute("INSERT INTO class_offering_sessions (id,class_offering_id,order_index,title) VALUES (10,5,1,'L1')")
    conn.execute(
        "INSERT INTO course_materials (id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext)"
        " VALUES (100,1,NULL,100,'guide.md','guide.md','file','markdown','md')"
    )
    conn.execute(
        "INSERT INTO course_materials (id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type)"
        " VALUES (200,1,NULL,200,'site','site','folder','folder')"
    )
    conn.execute(
        "INSERT INTO course_materials (id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext,mime_type)"
        " VALUES (201,1,200,200,'index.html','site/index.html','file','text','html','text/html')"
    )
    conn.commit()
    return conn


class SessionLearningMaterialsServiceTests(unittest.TestCase):
    def setUp(self):
        # 每个用例用独立的内存库；重置模块级 schema 缓存，确保新连接上重新建表。
        _schema._SCHEMA_READY = False
        self.conn = _make_conn()

    def _primary(self, session_id=10):
        return self.conn.execute(
            "SELECT learning_material_id FROM class_offering_sessions WHERE id=?", (session_id,)
        ).fetchone()[0]

    def test_add_markdown_and_html_folder(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        svc.add_material(self.conn, 5, 10, 200, 1)
        entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1)
        self.assertEqual([e["material_id"] for e in entries], [100, 200])
        # md -> markdown viewer, html folder -> 全屏渲染壳页
        self.assertEqual(entries[0]["open_url"], "/materials/view/100")
        self.assertEqual(entries[1]["open_url"], "/materials/render-view/200")
        self.assertTrue(entries[1]["is_renderable"])

    def test_first_add_mirrors_primary(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        self.assertEqual(self._primary(), 100)

    def test_duplicate_add_rejected(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        with self.assertRaises(HTTPException) as ctx:
            svc.add_material(self.conn, 5, 10, 100, 1)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_remove_primary_promotes_next(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        svc.add_material(self.conn, 5, 10, 200, 1)
        svc.remove_material(self.conn, 5, 10, 100, 1)
        self.assertEqual(self._primary(), 200)
        entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1)
        self.assertEqual([e["material_id"] for e in entries], [200])

    def test_external_primary_change_appears_in_list(self):
        svc.add_material(self.conn, 5, 10, 200, 1)
        # Simulate AI/Git auto-bind overwriting the legacy single column.
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id = 100 WHERE id=10")
        entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1)
        self.assertIn(100, [e["material_id"] for e in entries])

    def test_home_binding_uses_session_zero(self):
        svc.add_material(self.conn, 5, 0, 100, 1)
        self.assertEqual(
            self.conn.execute("SELECT home_learning_material_id FROM class_offerings WHERE id=5").fetchone()[0],
            100,
        )

    def test_non_bindable_material_rejected(self):
        self.conn.execute(
            "INSERT INTO course_materials (id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext)"
            " VALUES (300,1,NULL,300,'data.bin','data.bin','file','binary','bin')"
        )
        with self.assertRaises(HTTPException):
            svc.add_material(self.conn, 5, 10, 300, 1)

    def test_attach_counts(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        svc.add_material(self.conn, 5, 10, 200, 1)
        sessions = [{"id": 10, "learning_material_id": self._primary()}]
        offering = {"home_learning_material_id": 0}
        svc.attach_learning_material_counts(self.conn, 5, sessions, offering)
        self.assertEqual(sessions[0]["learning_material_count"], 2)
        self.assertEqual(offering["home_learning_material_count"], 0)


if __name__ == "__main__":
    unittest.main()
