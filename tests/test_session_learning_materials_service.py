import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.db import schema_session_learning_materials as _schema
from classroom_app.services import session_learning_materials_service as svc


class SessionMaterialPreviewRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_session_rejected_before_collecting(self):
        from classroom_app.routers.materials_parts import learning as route
        conn = _make_conn()
        conn.execute("INSERT INTO class_offering_sessions (id,class_offering_id) VALUES (99,6)")

        @contextmanager
        def isolated_connection():
            yield conn

        with (patch.object(route, 'get_db_connection', isolated_connection),
              patch.object(route, 'ensure_classroom_access'),
              patch.object(route, 'build_material_entries') as read):
            for session_id in (99, -1):
                with self.assertRaises(HTTPException) as caught:
                    await route.list_classroom_learning_materials(5, session_id, False, {'role': 'student', 'id': 9})
                self.assertEqual(caught.exception.status_code, 404)
            read.assert_not_called()
        conn.close()

    async def test_explicit_preview_never_generates_blurbs_or_persists_legacy(self):
        from classroom_app.routers.materials_parts import learning as route
        conn = _make_conn()
        conn.execute("UPDATE class_offering_sessions SET learning_material_id=100 WHERE id=10")
        before = conn.total_changes

        @contextmanager
        def isolated_connection():
            yield conn

        with (patch.object(route, 'get_db_connection', isolated_connection),
              patch.object(route, 'ensure_classroom_access') as access,
              patch.object(route, 'generate_material_blurb', new_callable=AsyncMock) as ai):
            user = {'role': 'teacher', 'id': 1}
            result = await route.list_classroom_learning_materials(5, 10, False, user)
            access.assert_called_once_with(conn, 5, user)
            ai.assert_not_awaited()
            self.assertEqual(result['materials'][0]['material_id'], 100)
            self.assertTrue(result['can_manage'])
        self.assertEqual(conn.total_changes, before)
        self.assertFalse(svc.has_material_bindings_table(conn))
        conn.close()

    async def test_denied_access_stops_before_reading_materials_or_generating(self):
        from classroom_app.routers.materials_parts import learning as route
        conn = _make_conn()

        @contextmanager
        def isolated_connection():
            yield conn

        with (patch.object(route, 'get_db_connection', isolated_connection),
              patch.object(route, 'ensure_classroom_access', side_effect=HTTPException(403, 'denied')),
              patch.object(route, 'build_material_entries') as read,
              patch.object(route, 'generate_material_blurb', new_callable=AsyncMock) as ai):
            with self.assertRaises(HTTPException) as caught:
                await route.list_classroom_learning_materials(5, 10, False, {'role': 'student', 'id': 9})
            self.assertEqual(caught.exception.status_code, 403)
            read.assert_not_called()
            ai.assert_not_awaited()
        conn.close()


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
        CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER, enrollment_status TEXT DEFAULT 'active');
        CREATE TABLE class_offering_class_links (offering_id INTEGER, class_id INTEGER);
        CREATE TABLE course_materials (id INTEGER PRIMARY KEY, teacher_id INTEGER, parent_id INTEGER, root_id INTEGER,
            name TEXT, material_path TEXT, node_type TEXT, preview_type TEXT DEFAULT '', file_ext TEXT DEFAULT '',
            mime_type TEXT DEFAULT '', file_hash TEXT DEFAULT '', scope_level TEXT DEFAULT 'private', owner_role TEXT DEFAULT 'teacher',
            owner_user_pk INTEGER, school_code TEXT, school_name TEXT, college TEXT, department TEXT);
        CREATE TABLE course_material_assignments (material_id INTEGER, class_offering_id INTEGER,
            assigned_by_teacher_id INTEGER, created_at TEXT, UNIQUE(material_id, class_offering_id));
        """
    )
    conn.execute("INSERT INTO teachers (id) VALUES (1)")
    conn.execute("INSERT INTO students (id,class_id) VALUES (9,1)")
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

    def tearDown(self):
        self.conn.close()

    def test_add_markdown_and_html_folder(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        svc.add_material(self.conn, 5, 10, 200, 1)
        entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1)
        self.assertEqual([e["material_id"] for e in entries], [100, 200])
        # md -> markdown viewer, html folder -> 全屏渲染壳页
        self.assertEqual(entries[0]["open_url"], "/materials/view/100")
        self.assertEqual(entries[1]["open_url"], "/materials/render-view/200")
        self.assertTrue(entries[1]["is_renderable"])

    def test_student_complete_collection_shrinks_with_reader_permissions_without_writing(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        svc.add_material(self.conn, 5, 10, 200, 1)
        def read():
            before = self.conn.total_changes
            entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1, persist_legacy=False, user={'role': 'student', 'id': 9})
            self.assertEqual(self.conn.total_changes, before)
            return [entry['material_id'] for entry in entries]
        self.assertEqual(read(), [100, 200])
        self.conn.execute('DELETE FROM course_material_assignments WHERE material_id=200')
        self.assertEqual(read(), [100])
        self.conn.execute('DELETE FROM course_material_assignments WHERE material_id=100')
        self.assertEqual(read(), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM class_offering_learning_materials').fetchone()[0], 2)

    def test_child_html_entry_requires_reader_package_root_access(self):
        svc.add_material(self.conn, 5, 10, 201, 1)
        self.conn.execute('DELETE FROM course_material_assignments')
        self.conn.execute('INSERT INTO course_material_assignments (material_id,class_offering_id) VALUES (201,5)')
        target = {'kind': 'html', 'node_id': 200, 'source_node_id': 201, 'entry_id': 201,
                  'render_url': '/materials/render/200/index.html', 'shell_url': '/materials/render-view/200?path=index.html'}
        with patch.object(svc, 'resolve_render_target', return_value=target):
            entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1, persist_legacy=False, user={'role': 'student', 'id': 9})
            self.assertEqual(entries, [])
            self.conn.execute('INSERT INTO course_material_assignments (material_id,class_offering_id) VALUES (200,5)')
            entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1, persist_legacy=False, user={'role': 'student', 'id': 9})
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['open_url'], target['shell_url'])

    def test_reader_permission_unexpected_errors_are_not_reported_as_empty(self):
        svc.add_material(self.conn, 5, 10, 100, 1)
        with patch.object(svc, 'ensure_user_material_access', side_effect=HTTPException(503, 'unavailable')):
            with self.assertRaises(HTTPException) as caught:
                svc.build_material_entries(self.conn, 5, 10, teacher_id=1, persist_legacy=False, user={'role': 'student', 'id': 9})
            self.assertEqual(caught.exception.status_code, 503)

    def test_reader_access_cache_is_shared_per_collection_and_rechecks_next_request(self):
        svc.add_material(self.conn, 5, 10, 200, 1)
        svc.add_material(self.conn, 5, 10, 201, 1)
        target = {'kind': 'html', 'node_id': 200, 'entry_id': 201,
                  'render_url': '/materials/render/200/index.html', 'shell_url': '/materials/render-view/200?path=index.html'}
        with (patch.object(svc, 'resolve_render_target', return_value=target),
              patch.object(svc, 'ensure_user_material_access', wraps=svc.ensure_user_material_access) as access):
            args = dict(teacher_id=1, persist_legacy=False, user={'role': 'student', 'id': 9})
            self.assertEqual(len(svc.build_material_entries(self.conn, 5, 10, **args)), 2)
            self.assertEqual([call.args[1] for call in access.call_args_list], [200, 201])
            access.reset_mock()
            self.conn.execute('DELETE FROM course_material_assignments')
            self.conn.execute('INSERT INTO course_material_assignments (material_id,class_offering_id) VALUES (201,5)')
            self.assertEqual(svc.build_material_entries(self.conn, 5, 10, **args), [])
            self.assertEqual([call.args[1] for call in access.call_args_list], [200, 201])

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

    def test_preview_legacy_installation_does_not_create_binding_table(self):
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=100 WHERE id=10")
        before = self.conn.total_changes
        entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1, persist_legacy=False)
        self.assertEqual([entry['material_id'] for entry in entries], [100])
        self.assertFalse(svc.has_material_bindings_table(self.conn))
        self.assertEqual(self.conn.total_changes, before)
        sessions = [{'id': 10, 'learning_material_id': 100}]
        offering = {'home_learning_material_id': 200}
        svc.attach_learning_material_counts(self.conn, 5, sessions, offering)
        self.assertEqual(sessions[0]['learning_material_count'], 1)
        self.assertEqual(offering['home_learning_material_count'], 1)
        self.assertFalse(svc.has_material_bindings_table(self.conn))

    def test_preview_merges_legacy_primary_without_writing(self):
        svc.add_material(self.conn, 5, 10, 200, 1)
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=100 WHERE id=10")
        before = self.conn.total_changes
        entries = svc.build_material_entries(self.conn, 5, 10, teacher_id=1, persist_legacy=False)
        self.assertEqual([entry['material_id'] for entry in entries], [100, 200])
        self.assertEqual(entries[0]['row_id'], 0)
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM class_offering_learning_materials WHERE class_offering_id=5 AND session_id=10"
        ).fetchone()[0], 1)

    def test_preview_home_primary_is_deduplicated_and_stays_read_only(self):
        svc.add_material(self.conn, 5, 0, 100, 1)
        before = self.conn.total_changes
        entries = svc.build_material_entries(self.conn, 5, 0, teacher_id=1, persist_legacy=False)
        self.assertEqual([entry['material_id'] for entry in entries], [100])
        self.assertEqual(self.conn.total_changes, before)

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
