"""Normal material source HTTP routes, real scoped SQL and temporary blob IO."""
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile

from fastapi import HTTPException

from classroom_app.db import schema_course_doc_packs, schema_lessondoc_editor
from classroom_app.routers.materials_parts import common, library
from classroom_app.services import file_service, session_material_generation_service as generation
from classroom_app.services.lessondoc import editor_service, pack_service, render
from tests.test_agent_platform_requests import PlatformRequestFixture
from tests.test_lessondoc_service import _deck, _manifest


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 10, 12, 0, 0, tzinfo=tz)


class AgentMaterialContentRequestsTests(PlatformRequestFixture):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.blobs = Path(directory.name)
        self.patched('classroom_app.services.file_service.GLOBAL_FILES_DIR', self.blobs)
        self.patched('classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS', ())
        self.patched('classroom_app.routers.materials_parts.library.get_db_connection', self.connection)
        self.patched('classroom_app.routers.materials_parts.library.datetime', FixedClock)
        self.patched('classroom_app.services.lessondoc.pack_service._now_iso', return_value='2026-09-10T12:00:00')
        for module in (schema_course_doc_packs, schema_lessondoc_editor):
            module.reset_schema_ready_for_tests()
            self.addCleanup(module.reset_schema_ready_for_tests)
            self.patched(module.__name__ + '.get_configured_db_engine', return_value='sqlite')
        for target in ('classroom_app.db.connection.get_configured_db_engine',
                       'classroom_app.services.lessondoc.pack_service.get_configured_db_engine',
                       'classroom_app.services.session_material_generation_service.get_configured_db_engine'):
            self.patched(target, return_value='sqlite')
        with self.connection() as conn:
            conn.executescript("""
              CREATE TABLE course_materials(id INTEGER PRIMARY KEY AUTOINCREMENT,teacher_id INTEGER NOT NULL,parent_id INTEGER,root_id INTEGER,
                material_path TEXT NOT NULL,name TEXT NOT NULL,node_type TEXT NOT NULL,mime_type TEXT DEFAULT '',preview_type TEXT DEFAULT '',
                ai_capability TEXT DEFAULT 'none',file_ext TEXT DEFAULT '',file_hash TEXT,file_size INTEGER DEFAULT 0,
                ai_parse_status TEXT DEFAULT 'idle',ai_parse_result_json TEXT,ai_optimize_status TEXT DEFAULT 'idle',ai_optimized_markdown TEXT,
                check_questions_json TEXT DEFAULT '',check_questions_status TEXT DEFAULT 'idle',check_questions_error TEXT DEFAULT '',
                check_questions_generated_at TEXT,created_at TEXT,updated_at TEXT,scope_level TEXT DEFAULT 'private');
              CREATE TABLE session_material_generation_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,class_offering_id INTEGER,session_id INTEGER,
                teacher_id INTEGER,trigger_mode TEXT,status TEXT,document_type TEXT,requirement_text TEXT,request_payload_json TEXT,
                result_payload_json TEXT,generated_material_id INTEGER,generated_material_path TEXT,error_message TEXT,created_at TEXT,
                started_at TEXT,completed_at TEXT,updated_at TEXT);
              CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,order_index INTEGER,learning_material_id INTEGER);
              ALTER TABLE class_offerings ADD COLUMN home_learning_material_id INTEGER;
            """)
            schema_course_doc_packs.ensure_course_doc_pack_schema(conn)
            schema_lessondoc_editor.ensure_lessondoc_editor_schema(conn)
            conn.commit()
        common._load_cached_text_content.cache_clear()
        self.addCleanup(common._load_cached_text_content.cache_clear)
        self.app.include_router(library.router)

    def material(self, content='Original text', *, name='fixture.md', owner=7):
        with self.connection() as conn:
            row = generation._create_file_row(conn, teacher_id=owner, parent_id=None, root_id=None,
                material_path=name, name=name, content=content, now='2026-09-10T12:00:00')
            conn.commit()
        return row['id']

    def read(self, material_id, actor='teacher'):
        return self.restored_parity(actor, 'http.materials.content.read', path_params={'material_id': material_id},
            query_params={'max_response_bytes': 131072})

    def payload(self, source, content, encoding='utf-8'):
        return {'content': content, 'encoding': encoding, 'revision': source['material']['revision'],
            'source_revision': source['material']['source_revision']}

    def save(self, material_id, body, actor='teacher', operation_id=None):
        return self.dispatch(actor, 'http.materials.content.save', operation_id=operation_id,
            path_params={'material_id': material_id}, query_params={'max_response_bytes': 131072}, body=body)

    def parity_save(self, material_id, body, actor='teacher'):
        # The normal save also gets a stable id; the B route derives its own
        # from the outer UUID. The public response deliberately excludes it.
        return self.restored_parity(actor, 'http.materials.content.save', path_params={'material_id': material_id},
            query_params={'max_response_bytes': 131072}, body=body)

    def test_plain_read_edit_noop_and_stale_revision_preserve_current_file(self):
        material_id = self.material()
        source = self.read(material_id)['result']['data']
        changed = self.parity_save(material_id, self.payload(source, 'Changed text\nSecond line'))
        self.assertEqual(200, changed['result']['http_status'])
        self.assertFalse(changed['verified_business'])
        current = self.read(material_id)['result']['data']
        self.assertEqual('Changed text\nSecond line', current['content'])
        self.assertNotEqual(source['material']['revision'], current['material']['revision'])
        unchanged = self.parity_save(material_id, self.payload(current, current['content']))
        self.assertTrue(unchanged['result']['data']['unchanged'])
        denied = self.parity_save(material_id, self.payload(source, 'Stale write'))
        self.assertEqual(409, denied['result']['http_status'])
        self.assertEqual(current['content'], self.read(material_id)['result']['data']['content'])
        with self.assertRaises(HTTPException) as caught:
            self.save(material_id, {'content': 'No revision', 'encoding': 'utf-8'})
        self.assertEqual(400, caught.exception.status_code)

    def test_normal_teacher_owner_student_and_superadmin_rules_are_preserved(self):
        material_id = self.material()
        self.assertEqual(403, self.read(material_id, 'student')['result']['http_status'])
        self.assertEqual(403, self.read(material_id, 'other')['result']['http_status'])
        source = self.read(material_id)['result']['data']
        self.assertEqual(404, self.parity_save(material_id, self.payload(source, 'Forbidden edit'), 'other')['result']['http_status'])
        foreign = self.material('Other owner text', name='other.md', owner=8)
        visible = self.read(foreign)['result']['data']
        self.assertEqual(200, self.parity_save(foreign, self.payload(visible, 'Admin edit'))['result']['http_status'])

    def test_declared_encodings_round_trip_and_unrepresentable_input_is_400(self):
        for encoding in ('utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'utf-16', 'utf-16-le', 'utf-16-be'):
            with self.subTest(encoding=encoding):
                material_id = self.material(name=encoding + '.txt')
                source = self.read(material_id)['result']['data']
                saved = self.parity_save(material_id, self.payload(source, '中文材料\nText 123', encoding))
                self.assertEqual(200, saved['result']['http_status'])
                self.assertEqual('中文材料\nText 123', self.read(material_id)['result']['data']['content'])
        material_id = self.material(name='unrepresentable.txt')
        source = self.read(material_id)['result']['data']
        failed = self.parity_save(material_id, self.payload(source, 'Emoji 😀', 'gbk'))
        self.assertEqual(400, failed['result']['http_status'])
        self.assertEqual(source['material']['revision'], self.read(material_id)['result']['data']['material']['revision'])
        for raw in (b'\x00\x01\x02\x03', b'\xff\xfea', b'\xff\xfe\x00\x00'):
            with self.assertRaises(HTTPException): common._decode_text_bytes(raw)
        self.assertEqual('ASCII text', common._decode_text_bytes('ASCII text'.encode('utf-16-le'))[0])

    def create_lesson(self):
        with self.connection() as conn:
            pack = pack_service.create_pack_skeleton(conn, teacher_id=7, course_id=1, manifest=_manifest())['pack']
            saved = editor_service.save_document(conn, pack_id=pack['id'], teacher_id=7, lesson_no=1,
                document=_deck(), expected_revision='absent', operation_id='synthetic_initial_lesson')
            conn.commit()
        return pack, saved['material_id']

    def test_registered_source_save_uses_native_receipt_projection_and_cas(self):
        pack, material_id = self.create_lesson()
        source = self.read(material_id)['result']['data']
        doc = render.extract_embedded_json(source['content'])
        doc['title'] = 'Updated lesson source'
        body = self.payload(source, render.render_lesson_html(doc))
        saved = self.parity_save(material_id, body)
        operation_id = saved['operation_id']
        self.assertEqual(200, saved['result']['http_status'])
        repeat = self.save(material_id, body, operation_id=operation_id)
        self.assertEqual(saved, repeat)
        operations = self.sql('SELECT operation_id FROM lessondoc_save_operations WHERE pack_id=? AND lesson_no=1 ORDER BY id', (pack['id'],))
        self.assertEqual(operation_id, operations[-1][0])
        with self.connection() as conn:
            state = editor_service.load_document(conn, pack_id=pack['id'], teacher_id=7, lesson_no=1)
            manifest = pack_service.read_manifest(conn, pack)
            self.assertEqual('Updated lesson source', state['document']['title'])
            self.assertEqual('Updated lesson source', manifest['lessons'][0]['title'])
        stale = self.parity_save(material_id, {**body, 'content': body['content'].replace('Updated lesson source', 'Stale edit')})
        self.assertEqual(409, stale['result']['http_status'])
        changed_id = {**body, 'operation_id': 'model_supplied_identity'}
        with self.assertRaises(HTTPException) as caught: self.save(material_id, changed_id)
        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual(2, len(operations))

    def test_unknown_save_cannot_be_retried_by_changing_outer_and_inner_ids(self):
        material_id = self.material()
        source = self.read(material_id)['result']['data']
        body = self.payload(source, 'Same intent')
        original = library._write_material_file
        async def stop_after_publication(*args):
            await original(*args)
            raise RuntimeError('synthetic interruption before DB commit')
        guard = self.patched('classroom_app.routers.materials_parts.library._write_material_file', side_effect=stop_after_publication)
        with self.assertRaises(RuntimeError): self.save(material_id, body)
        with self.assertRaises(HTTPException) as caught: self.save(material_id, body)
        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual(1, guard.call_count)
        rows = self.sql("SELECT request_json FROM agent_platform_requests WHERE capability_key='http.materials.content.save'")
        self.assertEqual(1, len(rows))
        self.assertIn('operation_id', json.loads(rows[0][0])['parameters']['body'])

    def test_response_budget_returns_413_and_does_not_commit_a_large_registered_save(self):
        material_id = self.material('x' * 132000)
        oversized = self.read(material_id)
        self.assertEqual(413, oversized['result']['http_status'])
        self.assertEqual('use_file_workflow_or_smaller_material_source_no_automatic_retry', oversized['result']['follow_up'])
        # Normal clients may keep the existing unbounded response contract.
        self.assertEqual(200, self.web('teacher', 'GET', f'/api/materials/{material_id}/content').status_code)
        pack, material_id = self.create_lesson()
        source = self.read(material_id)['result']['data']
        doc = render.extract_embedded_json(source['content'])
        doc['title'] = 'Bounded attempted update'
        body = {**self.payload(source, render.render_lesson_html(doc)), 'operation_id': 'normal_budgeted_edit'}
        response = self.web('teacher', 'PUT', f'/api/materials/{material_id}/content?max_response_bytes=1024', json=body)
        self.assertEqual(413, response.status_code)
        current = self.read(material_id)['result']['data']
        self.assertEqual(source['material'], current['material'])
        self.assertEqual(0, len(self.sql("SELECT id FROM lessondoc_save_operations WHERE operation_id='normal_budgeted_edit'")))

    def test_atomic_writer_rejects_an_existing_corrupt_blob_before_material_binding(self):
        payload = b'complete expected bytes'
        digest = hashlib.sha256(payload).hexdigest()
        target = file_service.global_file_write_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'partial')
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(common._write_material_file(digest, payload))
        self.assertEqual(409, caught.exception.status_code)
        self.assertFalse(list(self.blobs.rglob('.upload-*')))
