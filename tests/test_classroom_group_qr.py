import asyncio
import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from classroom_app.db.postgres_schema import POSTGRES_RUNTIME_COLUMN_DEFINITIONS
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.routers import classroom_group_qr as routes
from classroom_app.services import classroom_group_qr_service as service, file_service

ROOT = Path(__file__).resolve().parents[1]


def seed_fixture(db_path):
    with connect_fixture(db_path) as conn:
        conn.executescript("""
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, teacher_id INTEGER, class_id INTEGER);
            CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER, enrollment_status TEXT);
            CREATE TABLE class_offering_class_links (offering_id INTEGER, class_id INTEGER);
            INSERT INTO class_offerings VALUES (11, 1, 1), (12, 2, 3);
            INSERT INTO students VALUES (1, 1, 'active'), (2, 2, 'active'), (3, 3, 'active'),
                (4, 1, 'inactive'), (5, 1, NULL);
            INSERT INTO class_offering_class_links VALUES (11, 1), (11, 2);
        """)
        for column, definition in POSTGRES_RUNTIME_COLUMN_DEFINITIONS['class_offerings'].items():
            if column.startswith('group_qr_'):
                conn.execute(f'ALTER TABLE class_offerings ADD COLUMN {column} {definition}')


@contextmanager
def connect_fixture(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def image_bytes(format='PNG', color='white'):
    output = io.BytesIO()
    Image.new('RGB', (240, 280), color).save(output, format=format)
    return output.getvalue()


class ClassroomGroupQRTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.codex-temp'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='group-qr-test-', dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / 'test.db'
        seed_fixture(self.db_path)
        for patcher in (
            patch.object(routes, 'get_db_connection', lambda: connect_fixture(self.db_path)),
            patch.object(file_service, 'GLOBAL_FILES_DIR', self.root / 'files'),
            patch.object(file_service, 'GLOBAL_FILES_LEGACY_DIRS', ()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = FastAPI()
        self.app.include_router(routes.router)
        self.as_user('teacher', 1)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def as_user(self, role, user_id):
        self.app.dependency_overrides[routes.get_current_user] = lambda: {'role': role, 'id': user_id}

    def save(self, description='入群后请备注姓名', revision='', content=None, name='qr.png',
             mime='image/png', remove_image=False):
        return self.client.post('/api/classrooms/11/group-qr',
            data={'description': description, 'revision': revision, 'remove_image': str(remove_image).lower()},
            files={'file': (name, content, mime)} if content is not None else None)

    def shared_resource_fixture(self):
        saved = self.save(content=image_bytes()).json()
        with connect_fixture(self.db_path) as conn:
            file_hash = conn.execute('SELECT group_qr_file_hash FROM class_offerings WHERE id=11').fetchone()[0]
            conn.executescript('''
                CREATE TABLE course_materials (
                    id INTEGER PRIMARY KEY, teacher_id INTEGER, parent_id INTEGER, root_id INTEGER,
                    material_path TEXT, name TEXT, node_type TEXT, file_hash TEXT, updated_at TEXT);
                CREATE TABLE course_files (
                    id INTEGER PRIMARY KEY, course_id INTEGER, file_name TEXT, file_hash TEXT);
            ''')
        return saved, file_hash

    def test_course_file_deletion_preserves_qr_image_until_last_reference_is_removed(self):
        from classroom_app.routers import files as file_routes
        saved, file_hash = self.shared_resource_fixture()
        with connect_fixture(self.db_path) as conn:
            conn.execute("INSERT INTO course_files VALUES (501,10,'qr.png',?)", (file_hash,))
        with patch.object(file_routes, 'get_db_connection', lambda: connect_fixture(self.db_path)), \
             patch.object(file_routes, 'resolve_teacher_course_id', return_value=10), \
             patch.object(file_routes, 'can_manage_scoped_resource', return_value=True), \
             patch.object(file_routes, 'broadcast_file_update', new_callable=AsyncMock):
            result = asyncio.run(file_routes.delete_course_file(10, 501, {'role': 'teacher', 'id': 1}))
            self.assertEqual(result['status'], 'success')
            self.assertEqual(self.client.get(saved['image_url']).status_code, 200)
            self.save(description=saved['description'], revision=saved['revision'], remove_image=True)
            with connect_fixture(self.db_path) as conn:
                conn.execute("INSERT INTO course_files VALUES (502,10,'qr.png',?)", (file_hash,))
            asyncio.run(file_routes.delete_course_file(10, 502, {'role': 'teacher', 'id': 1}))
        self.assertIsNone(file_service.resolve_global_file_path(file_hash))

    def test_material_deletion_preserves_shared_qr_image(self):
        from classroom_app.routers.materials_parts import library
        saved, file_hash = self.shared_resource_fixture()
        with connect_fixture(self.db_path) as conn:
            conn.execute("INSERT INTO course_materials VALUES (31,1,NULL,31,'qr.png','qr.png','file',?,NULL)",
                         (file_hash,))
        with patch.object(library, 'get_db_connection', lambda: connect_fixture(self.db_path)), \
             patch.object(library, 'get_configured_db_engine', return_value='sqlite'), \
             patch.object(library, 'build_material_delete_impact', return_value={'total_reference_count': 0}), \
             patch('classroom_app.services.lessondoc.pack_service.archive_pack_for_material'):
            result = asyncio.run(library.delete_material(31, unlink_references=False, impact_token='',
                                                        user={'role': 'teacher', 'id': 1}))
        self.assertEqual(result['removed_file_count'], 0)
        self.assertEqual(self.client.get(saved['image_url']).status_code, 200)
        with connect_fixture(self.db_path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM course_materials').fetchone()[0], 0)
            self.assertEqual(file_service.count_global_file_references(conn, file_hash), 1)

    def test_material_git_cleanup_preserves_shared_qr_image(self):
        from classroom_app.services import materials_git_service as git_service
        saved, file_hash = self.shared_resource_fixture()
        workspace = self.root / 'empty-repository'
        workspace.mkdir()
        with connect_fixture(self.db_path) as conn:
            conn.execute("INSERT INTO course_materials VALUES (30,1,NULL,30,'repo','repo','folder',NULL,NULL)")
            conn.execute("INSERT INTO course_materials VALUES (31,1,30,30,'repo/qr.png','qr.png','file',?,NULL)",
                         (file_hash,))
            root = dict(conn.execute('SELECT * FROM course_materials WHERE id=30').fetchone())
            summary, removable, _ = git_service._sync_workspace_to_repository(conn, root, workspace)
            self.assertEqual(summary['deleted'], 1)
            self.assertEqual(removable, [])
        self.assertEqual(self.client.get(saved['image_url']).status_code, 200)

    def test_upload_reload_replace_and_description_only_preserves_image(self):
        original = image_bytes()
        response = self.save(content=original)
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        self.assertEqual(self.client.get(first['image_url']).content, original)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), first)
        updated = self.save(description='修改简介\n保留二维码', revision=first['revision']).json()
        self.assertEqual(updated['image_url'], first['image_url'])
        self.assertEqual(updated['description'], '修改简介\n保留二维码')
        self.assertNotEqual(updated['revision'], first['revision'])
        replaced = self.save(content=image_bytes('JPEG', 'navy'), name='qr.jpg',
                             mime='image/jpeg', revision=updated['revision']).json()
        self.assertNotEqual(replaced['image_url'], first['image_url'])
        image = self.client.get(replaced['image_url'])
        self.assertEqual(image.headers['content-type'], 'image/jpeg')
        self.assertEqual(image.headers['cache-control'], 'private, no-store')
        self.assertEqual(image.headers['x-content-type-options'], 'nosniff')

    def test_primary_combined_and_legacy_active_students_read_but_cannot_write(self):
        saved = self.save(content=image_bytes()).json()
        for student_id in (1, 2, 5):
            with self.subTest(student=student_id):
                self.as_user('student', student_id)
                self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)
                self.assertEqual(self.client.get(saved['image_url']).status_code, 200)
                self.assertEqual(self.save(content=image_bytes()).status_code, 403)

    def test_multipart_description_line_endings_round_trip_as_lf(self):
        response = self.save(description='第一行\r\n第二行\r第三行\n第四行', content=image_bytes())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('multipart/form-data', response.request.headers['content-type'])
        saved = response.json()
        expected = '第一行\n第二行\n第三行\n第四行'
        self.assertEqual(saved['description'], expected)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)
        with connect_fixture(self.db_path) as conn:
            self.assertEqual(conn.execute('SELECT group_qr_description FROM class_offerings WHERE id=11')
                             .fetchone()[0], expected)
            conn.execute('UPDATE class_offerings SET group_qr_description=? WHERE id=11',
                         ('旧记录\r\n第二行\r第三行',))
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json()['description'],
                         '旧记录\n第二行\n第三行')

    def test_description_limit_counts_normalized_line_endings(self):
        description = '字' * 998 + '\r\n末'
        response = self.save(description=description, content=image_bytes())
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(len(saved['description']), 1000)
        self.assertEqual(saved['description'], description.replace('\r\n', '\n'))
        self.assertEqual(self.save(description=description + '字', revision=saved['revision'],
                                   content=image_bytes()).status_code, 400)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)

    def test_unrelated_inactive_and_unknown_users_cannot_read_image_or_metadata(self):
        saved = self.save(content=image_bytes()).json()
        for role, user_id in (('teacher', 2), ('student', 3), ('student', 4), ('student', 999)):
            with self.subTest(role=role, user=user_id):
                self.as_user(role, user_id)
                self.assertEqual(self.client.get('/api/classrooms/11/group-qr').status_code, 404)
                self.assertEqual(self.client.get(saved['image_url']).status_code, 404)
                self.assertIn(self.save(content=image_bytes()).status_code, (403, 404))

    def test_empty_state_description_clear_and_other_classroom_isolation(self):
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(),
                         {'image_url': '', 'description': '', 'revision': ''})
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr/image').status_code, 404)
        saved = self.save().json()
        cleared = self.save(description='', revision=saved['revision']).json()
        self.assertEqual(cleared['description'], '')
        self.as_user('teacher', 2)
        self.assertEqual(self.client.get('/api/classrooms/12/group-qr').json()['description'], '')

    def test_stale_save_cannot_overwrite_newer_description_or_store_upload(self):
        saved = self.save(content=image_bytes()).json()
        with patch('classroom_app.services.classroom_group_qr_service.store_file_object_globally') as store:
            response = self.save(description='stale', content=image_bytes())
            self.assertEqual(response.status_code, 409)
            store.assert_not_called()
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)

    def test_remove_image_preserves_description_and_shared_file_and_can_be_replaced(self):
        original = image_bytes()
        saved = self.save(content=original).json()
        self.as_user('teacher', 2)
        other = self.client.post('/api/classrooms/12/group-qr', files={
            'file': ('other.png', original, 'image/png'),
        }).json()
        self.as_user('teacher', 1)
        removed_response = self.save(description=saved['description'], revision=saved['revision'],
                                     remove_image=True)
        self.assertEqual(removed_response.status_code, 200, removed_response.text)
        removed = removed_response.json()
        self.assertEqual(removed['image_url'], '')
        self.assertEqual(removed['description'], saved['description'])
        self.assertNotEqual(removed['revision'], saved['revision'])
        self.assertEqual(self.client.get(saved['image_url']).status_code, 404)
        with connect_fixture(self.db_path) as conn:
            self.assertEqual(conn.execute('SELECT group_qr_mime_type FROM class_offerings WHERE id=11')
                             .fetchone()[0], '')
        self.as_user('teacher', 2)
        self.assertEqual(self.client.get(other['image_url']).content, original)
        self.as_user('teacher', 1)
        replaced = self.save(revision=removed['revision'], content=image_bytes('JPEG', 'navy'))
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(self.client.get(replaced.json()['image_url']).headers['content-type'], 'image/jpeg')

    def test_remove_image_is_authorized_versioned_and_mutually_exclusive_with_upload(self):
        saved = self.save(content=image_bytes()).json()
        with patch.object(service, 'store_file_object_globally') as store:
            response = self.save(revision=saved['revision'], content=image_bytes(), remove_image=True)
            self.assertEqual(response.status_code, 400)
            store.assert_not_called()
        self.assertEqual(self.save(remove_image=True).status_code, 409)
        self.as_user('student', 1)
        self.assertEqual(self.save(revision=saved['revision'], remove_image=True).status_code, 403)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)

    def test_download_preserves_original_and_uses_verified_extension_and_membership(self):
        revision = ''
        for format, mime, extension in (('PNG', 'image/png', 'png'), ('JPEG', 'image/jpeg', 'jpg'),
                                        ('WEBP', 'image/webp', 'webp')):
            with self.subTest(format=format):
                self.as_user('teacher', 1)
                original = image_bytes(format)
                saved = self.save(revision=revision, content=original, name='untrusted.html',
                                  mime='text/html').json()
                revision = saved['revision']
                self.as_user('student', 2)
                preview = self.client.get(saved['image_url'])
                self.assertNotIn('content-disposition', preview.headers)
                download = self.client.get(saved['image_url'] + '&download=true')
                self.assertEqual(download.content, original)
                self.assertEqual(download.headers['content-type'], mime)
                self.assertEqual(download.headers['content-disposition'],
                                 f'attachment; filename="classroom-11-group-qr.{extension}"')
                self.assertEqual(download.headers['cache-control'], 'private, no-store')
                self.as_user('student', 3)
                self.assertEqual(self.client.get(saved['image_url'] + '&download=true').status_code, 404)

    def test_revoked_membership_cannot_reuse_previous_preview_or_download_url(self):
        saved = self.save(content=image_bytes()).json()
        self.as_user('student', 2)
        self.assertEqual(self.client.get(saved['image_url']).status_code, 200)
        with connect_fixture(self.db_path) as conn:
            conn.execute('DELETE FROM class_offering_class_links WHERE offering_id=11 AND class_id=2')
        for url in ('/api/classrooms/11/group-qr', saved['image_url'], saved['image_url'] + '&download=true'):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_competing_saves_accept_only_one_revision(self):
        saved = self.save(content=image_bytes()).json()
        readers = Barrier(2, timeout=10)
        load_offering = service.load_group_qr_offering

        def load_together(*args, **kwargs):
            offering = load_offering(*args, **kwargs)
            readers.wait()
            return offering

        def save_in_connection(description):
            try:
                with connect_fixture(self.db_path) as conn:
                    return 200, service.update_group_qr(conn, 11, {'role': 'teacher', 'id': 1},
                        description=description, revision=saved['revision'])
            except HTTPException as exc:
                return exc.status_code, None

        with patch.object(service, 'load_group_qr_offering', side_effect=load_together):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(save_in_connection, ('first draft', 'second draft')))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        winner = next(payload for status, payload in results if status == 200)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), winner)

    def test_invalid_or_out_of_range_classroom_ids_do_not_raise_database_errors(self):
        for offering_id in ('0', '-1', str(2 ** 63), str(10 ** 100)):
            with self.subTest(offering_id=offering_id):
                endpoint = f'/api/classrooms/{offering_id}/group-qr'
                self.assertEqual(self.client.get(endpoint).status_code, 404)
                self.assertEqual(self.client.get(endpoint + '/image?download=true').status_code, 404)
                self.assertEqual(self.client.post(endpoint, data={'remove_image': 'true'}).status_code, 404)

    def test_invalid_uploads_and_long_description_preserve_previous_record(self):
        saved = self.save(content=image_bytes()).json()
        cases = [(b'<svg onload="alert(1)"/>', 'qr.png', 'image/png'),
                 (b'', 'empty.png', 'image/png'),
                 (b'x' * (5 * 1024 * 1024 + 1), 'large.png', 'image/png'),
                 (image_bytes('GIF'), 'qr.gif', 'image/gif'),
                 (image_bytes('JPEG')[:100], 'broken.jpg', 'image/jpeg')]
        for content, name, mime in cases:
            with self.subTest(name=name):
                response = self.save(content=content, name=name, mime=mime, revision=saved['revision'])
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)
        self.assertEqual(self.save(description='字' * 1001, revision=saved['revision']).status_code, 400)

    def test_verified_format_controls_response_mime_and_pixels_are_bounded(self):
        saved = self.save(content=image_bytes('WEBP'), name='bad.html', mime='text/html').json()
        self.assertEqual(self.client.get(saved['image_url']).headers['content-type'], 'image/webp')
        with patch('classroom_app.services.classroom_group_qr_service.MAX_IMAGE_PIXELS', 100):
            self.assertEqual(self.save(content=image_bytes(), revision=saved['revision']).status_code, 400)

    def test_corrupted_png_checksum_and_animated_images_are_rejected_without_changes(self):
        saved = self.save(content=image_bytes()).json()
        broken_png = bytearray(image_bytes())
        data_type_index = broken_png.index(b'IDAT')
        chunk_length = int.from_bytes(broken_png[data_type_index - 4:data_type_index], 'big')
        broken_png[data_type_index + 4 + chunk_length] ^= 0xFF
        animated = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(animated, format='PNG', save_all=True,
            append_images=[Image.new('RGB', (100, 100), 'black')], duration=100, loop=0)
        for content in (bytes(broken_png), animated.getvalue()):
            response = self.save(content=content, revision=saved['revision'])
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)

    def test_missing_file_and_malformed_stored_metadata_are_not_served(self):
        saved = self.save(content=image_bytes()).json()
        with connect_fixture(self.db_path) as conn:
            file_hash = conn.execute('SELECT group_qr_file_hash FROM class_offerings WHERE id=11').fetchone()[0]
        file_service.resolve_global_file_path(file_hash).unlink()
        self.assertEqual(self.client.get(saved['image_url']).status_code, 404)
        with connect_fixture(self.db_path) as conn:
            conn.execute("UPDATE class_offerings SET group_qr_file_hash='../test.db' WHERE id=11")
        with patch.object(routes, 'resolve_global_file_path') as resolve:
            self.assertEqual(self.client.get(saved['image_url']).status_code, 404)
            resolve.assert_not_called()

    def test_storage_failure_preserves_existing_record(self):
        saved = self.save(content=image_bytes()).json()
        with patch.object(service, 'store_file_object_globally', side_effect=OSError('disk full')):
            with TestClient(self.app, raise_server_exceptions=False) as client:
                response = client.post('/api/classrooms/11/group-qr',
                    data={'description': 'do not persist', 'revision': saved['revision']},
                    files={'file': ('replacement.png', image_bytes(color='black'), 'image/png')})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)

    def test_database_failure_does_not_replace_existing_record(self):
        saved = self.save(content=image_bytes()).json()
        with connect_fixture(self.db_path) as conn:
            conn.execute("""CREATE TRIGGER reject_qr_update BEFORE UPDATE ON class_offerings
                            BEGIN SELECT RAISE(ABORT, 'test write failure'); END""")
        with TestClient(self.app, raise_server_exceptions=False) as client:
            response = client.post('/api/classrooms/11/group-qr',
                                   data={'description': 'do not persist', 'revision': saved['revision'],
                                         'remove_image': 'true'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get('/api/classrooms/11/group-qr').json(), saved)

    def test_sqlite_schema_upgrade_is_idempotent_and_preserves_existing_settings(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        with patch('classroom_app.db.schema_foundation._seed_initial_super_admin'):
            ensure_foundation_schema(conn)
            conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (11, 1, 1, 1)")
            conn.execute("UPDATE class_offerings SET group_qr_description = '保留原有简介' WHERE id=11")
            conn.execute('ALTER TABLE class_offerings DROP COLUMN group_qr_revision')
            ensure_foundation_schema(conn)
            ensure_foundation_schema(conn)
        row = dict(conn.execute('SELECT * FROM class_offerings WHERE id=11').fetchone())
        self.assertEqual(row['group_qr_description'], '保留原有简介')
        self.assertEqual(row['group_qr_revision'], '')


if __name__ == '__main__':
    unittest.main()
