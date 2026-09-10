"""Single-file normal uploads through reviewed multipart, actual DB and blobs."""
import asyncio
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from classroom_app.routers import files
from tests.test_agent_platform_request_collaboration import CollaborationRequestFixture


class AgentFileUploadRequestsTests(CollaborationRequestFixture):
    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patched('classroom_app.services.file_service.GLOBAL_FILES_DIR', self.root / 'blobs')
        self.patched('classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS', ())
        self.patched('classroom_app.services.agent_platform_multipart_service.AGENT_TASK_WORKSPACE_ROOT', self.root)
        for task in (10,11,12,13):
            directory = self.root / 'tasks' / str(task)
            directory.mkdir(parents=True)
            (directory / 'source.txt').write_bytes(b'Synthetic uploaded source')
        self.patched('classroom_app.routers.files.get_db_connection', self.connection)
        self.patched('classroom_app.db.connection.get_configured_db_engine', return_value='sqlite')
        with self.connection() as conn:
            conn.executescript("""
              ALTER TABLE courses ADD COLUMN created_by_teacher_id INTEGER;
              ALTER TABLE courses ADD COLUMN school_code TEXT DEFAULT 'A';
              ALTER TABLE courses ADD COLUMN school_name TEXT DEFAULT 'School A';
              ALTER TABLE courses ADD COLUMN college TEXT DEFAULT 'C';
              ALTER TABLE courses ADD COLUMN department TEXT DEFAULT 'D';
              UPDATE courses SET created_by_teacher_id=CASE id WHEN 1 THEN 7 ELSE 8 END;
              CREATE TABLE course_files(id INTEGER PRIMARY KEY AUTOINCREMENT,course_id INTEGER,file_name TEXT,file_hash TEXT,file_size INTEGER,
                is_public INTEGER,is_teacher_resource INTEGER,uploaded_by_teacher_id INTEGER,owner_role TEXT,owner_user_pk INTEGER,
                scope_level TEXT,class_offering_id INTEGER,class_id INTEGER,school_code TEXT,school_name TEXT,college TEXT,department TEXT,
                published_at TEXT,updated_at TEXT);
            """)
            conn.commit()
        self.app.include_router(files.router)

    def upload(self, role, key, params, body, *, operation_id=None, refs=None):
        return self.dispatch(role,key,operation_id,path_params=params,body=body,files=refs or [{'path':'source.txt'}])

    def test_course_upload_returns_exact_id_for_normal_download_and_scope_is_explicit(self):
        params = {'course_id':1}
        body = {'is_public':False,'is_teacher_resource':True}
        operation_id = str(uuid.uuid4())
        result = self.upload('teacher','http.course.file.upload',params,body,operation_id=operation_id)
        self.assertEqual(200,result['result']['http_status'],result)
        data = result['result']['data']['file']
        self.assertEqual('private',data['scope_level'])
        downloaded = self.web('teacher','GET',data['download_url'])
        self.assertEqual(200,downloaded.status_code)
        self.assertEqual(b'Synthetic uploaded source',downloaded.content)
        self.assertEqual(403,self.web('student','GET',data['download_url']).status_code)
        self.assertEqual(result,self.upload('teacher','http.course.file.upload',params,body,operation_id=operation_id))
        self.assertEqual(1,len(self.sql('SELECT id FROM course_files')))
        for role in ('student','other'):
            normal = self.web(role,'POST','/api/courses/1/files/upload',data={'is_public':'false','is_teacher_resource':'true'},
                files={'file':('source.txt',b'Synthetic uploaded source','text/plain')})
            denied = self.upload(role,'http.course.file.upload',params,body)
            self.assertEqual(normal.status_code,denied['result']['http_status'])
            self.assertIn(normal.status_code,(403,404))

    def test_group_upload_actual_private_snapshot_receipt_and_role_parity(self):
        group = self.create()
        result = self.upload('student','http.collaboration.file.upload',{'group_id':group},{'description':'Shared project source'})
        self.assertEqual(200,result['result']['http_status'],result)
        data = result['result']['data']['file']
        self.assertEqual(1,len(self.sql('SELECT id FROM study_group_files')))
        downloaded = self.web('student','GET',data['download_url'])
        self.assertEqual(b'Synthetic uploaded source',downloaded.content)
        self.assertEqual(200,self.web('teacher','GET',data['download_url']).status_code)
        self.assertEqual(403,self.web('peer','GET',data['download_url']).status_code)
        for role in ('peer','other'):
            normal = self.web(role,'POST',f'/api/collaboration/groups/{group}/files',data={'description':'Denied'},
                files={'file':('source.txt',b'Synthetic uploaded source','text/plain')})
            denied = self.upload(role,'http.collaboration.file.upload',{'group_id':group},{'description':'Denied'})
            self.assertEqual(403,normal.status_code)
            self.assertEqual(normal.status_code,denied['result']['http_status'])

    def test_field_name_and_single_file_count_are_server_owned_and_digest_checked(self):
        params, body = {'course_id':1}, {'is_public':False,'is_teacher_resource':False}
        for refs in ([{'path':'source.txt'},{'path':'source.txt','filename':'another.txt'}],
                     [{'path':'source.txt','field_name':'files'}],[{'path':'source.txt','sha256':'0'*64}]):
            with self.assertRaises(HTTPException): self.upload('teacher','http.course.file.upload',params,body,refs=refs)
        self.assertEqual(0,len(self.sql('SELECT id FROM course_files')))
        self.assertEqual(0,len(self.sql('SELECT id FROM agent_platform_requests')))

    def test_upload_failure_does_not_bind_a_db_row_and_same_operation_is_not_reexecuted(self):
        with patch.object(files,'build_course_file_scope',side_effect=RuntimeError('Synthetic DB preparation failure')):
            operation_id = str(uuid.uuid4())
            result = self.upload('teacher','http.course.file.upload',{'course_id':1},{'is_public':False,'is_teacher_resource':False},operation_id=operation_id)
            self.assertEqual(500,result['result']['http_status'])
            self.assertEqual('uncertain',result['status'])
            replay = self.upload('teacher','http.course.file.upload',{'course_id':1},{'is_public':False,'is_teacher_resource':False},operation_id=operation_id)
            self.assertEqual(result,replay)
        self.assertEqual([],self.sql('SELECT id FROM course_files'))
        # Immutable file publication and DB binding are separate; retain any
        # orphan bytes, because another completed upload may reference them.
        self.assertTrue(any((self.root/'blobs').rglob('*')))

    def test_course_broadcast_has_one_deadline_and_preserves_external_cancel(self):
        calls = []
        real_timeout = asyncio.timeout
        async def stalled(room, payload):
            calls.append(room)
            await asyncio.sleep(60)
        async def run():
            with patch.object(files.manager,'broadcast',side_effect=stalled), patch.object(files.asyncio,'timeout',side_effect=lambda seconds: real_timeout(.02)):
                await files.broadcast_file_update(1,'Synthetic message')
            self.assertEqual([1],calls)
            with patch.object(files.manager,'broadcast',side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError): await files.broadcast_file_update(1,'Synthetic message')
        asyncio.run(run())
