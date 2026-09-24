"""Private screenshot recovery, quota rollback and idempotent admission."""
import asyncio
from contextlib import asynccontextmanager
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from starlette.datastructures import Headers
from starlette.requests import Request

from classroom_app.db.schema_ai_workspace import ensure_ai_workspace_schema
from classroom_app.routers import ai as routes
from classroom_app.services import ai_workspace_attachment_service as images
from classroom_app.services import ai_workspace_service as store


OWNER = {'id': 1, 'role': 'teacher', 'name': '教师'}


def png(color='pink'):
    output = io.BytesIO()
    Image.new('RGB', (24, 18), color).save(output, 'PNG')
    return output.getvalue()


class WorkspaceAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ai-private-image-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'test.db'
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE classroom_behavior_profiles(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT,created_at TEXT);
                CREATE TABLE ai_psychology_profiles(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT,created_at TEXT);
                CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER);
                CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER);
                CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
                CREATE TABLE assignments(id TEXT,class_offering_id INTEGER,exam_paper_id TEXT,status TEXT,availability_mode TEXT,assessment_kind TEXT);
                CREATE TABLE submissions(assignment_id TEXT,student_pk_id INTEGER,is_absence_score INTEGER);
                CREATE TABLE learning_stage_exam_attempts(assignment_id TEXT,student_id INTEGER);
            ''')
            ensure_ai_workspace_schema(conn, engine='sqlite')
            self.session = store.create_session(conn, OWNER)
        for target, name, value in ((images, 'DATA_DIR', self.root), (store, 'get_db_connection', self.connect), (routes, 'get_db_connection', self.connect)):
            mocked = patch.object(target, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def connect(self):
        conn = sqlite3.connect(self.db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def begin(self, request_id='image-1', data=None, user=OWNER):
        data = png() if data is None else data
        with self.connect() as conn:
            return store.begin_request(conn, self.session['session_uuid'], user, request_id, '请看截图',
                [{'type': 'image', 'name': '页面截图.png', 'image_count': 1}, {'type': 'text', 'name': 'notes.txt'}],
                {'message': '请看截图', 'image': data.hex()}, image_uploads=[{'attachment_index': 0, 'contents': data}])

    def finish(self, state, success=True):
        with self.connect() as conn:
            store.finish_request(conn, state['session_id'], state['request_id'], OWNER, '回答', success=success)

    def history(self):
        with self.connect() as conn:
            return store.load_history(conn, self.session['session_uuid'], OWNER)

    def files(self):
        return [p for p in (self.root / 'private').rglob('*') if p.is_file()] if (self.root / 'private').exists() else []

    def client(self, user=OWNER):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: user
        return TestClient(app)

    def test_history_keeps_original_image_and_endpoint_is_private_and_owner_scoped(self):
        original = png()
        self.finish(self.begin(data=original))
        attachments = self.history()['messages'][0]['attachments']
        url = attachments[0]['previewUrl']
        self.assertNotIn('previewUrl', attachments[1])
        self.assertEqual(self.files()[0].read_bytes(), original)
        with self.client() as client:
            result = client.get(url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, original)
        self.assertEqual(result.headers['content-type'], 'image/png')
        self.assertEqual(result.headers['cache-control'], 'private, no-store')
        self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        for user in ({**OWNER, 'id': 2}, {**OWNER, 'role': 'student'}):
            with self.client(user) as client:
                self.assertEqual(client.get(url).status_code, 403)
        with self.connect() as conn:
            other = store.create_session(conn, OWNER)
        with self.client() as client:
            self.assertEqual(client.get(url.replace(self.session['session_uuid'], other['session_uuid'])).status_code, 404)

    def test_foreign_owner_invalid_path_and_unreadable_images_never_create_files(self):
        with self.assertRaises(HTTPException):
            self.begin(user={**OWNER, 'id': 2})
        for name in ('../outside.png', 'a.png', 'a' * 64 + '.svg', 'a' * 64 + '.png/../outside'):
            with self.assertRaises(HTTPException) as error:
                images.resolve_image(OWNER, self.session['session_uuid'], name)
            self.assertEqual(error.exception.status_code, 404)
        for data in (b'<svg><script>alert(1)</script></svg>', b'broken image'):
            with self.assertRaises(HTTPException) as error:
                self.begin(data=data)
            self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.files(), [])
        self.assertFalse(self.history()['pending'])
        self.assertEqual(self.history()['messages'], [])

    def test_quota_and_single_image_limit_reject_before_message_or_lease_commit(self):
        first = self.begin()
        self.finish(first)
        used = sum(path.stat().st_size for path in self.files())
        with patch.object(images, 'MAX_USER_BYTES', used):
            with self.assertRaises(HTTPException) as error:
                self.begin('over-quota', png('blue'))
            self.assertEqual(error.exception.status_code, 413)
        with patch.object(images, 'MAX_IMAGE_BYTES', 1):
            with self.assertRaises(HTTPException) as error:
                self.begin('too-large')
            self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(len(self.files()), 1)
        self.assertFalse(self.history()['pending'])
        self.assertEqual(len(self.history()['messages']), 2)
        with self.connect() as conn:
            text = store.begin_request(conn, self.session['session_uuid'], OWNER, 'text-ok', '改用文字', [], {})
        self.finish(text)

    def test_pending_failure_retry_and_completed_replay_reuse_exact_image_file(self):
        first = self.begin()
        self.assertTrue(self.begin()['pending'])
        url = self.history()['messages'][0]['attachments'][0]['previewUrl']
        self.finish(first, success=False)
        retried = self.begin()
        self.finish(retried)
        self.assertTrue(self.begin()['replay'])
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.history()['messages'][0]['attachments'][0]['previewUrl'], url)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT completed_rounds FROM ai_workspace_profile_states').fetchone()[0], 1)

    def test_delete_requires_owner_and_finished_reply_then_releases_image_quota(self):
        first = self.begin()
        url = self.history()['messages'][0]['attachments'][0]['previewUrl']
        endpoint = '/api/ai/workspace/session/' + self.session['session_uuid']
        for user in ({**OWNER, 'id': 2}, {**OWNER, 'role': 'student'}):
            with self.client(user) as client:
                self.assertEqual(client.delete(endpoint).status_code, 403)
        with self.client() as client:
            self.assertEqual(client.delete(endpoint).status_code, 409)
        self.assertEqual(len(self.files()), 1)
        self.finish(first)
        original_session = self.session
        with self.connect() as conn:
            self.session = store.create_session(conn, OWNER)
        with patch.object(images, 'MAX_USER_BYTES', len(png())):
            with self.assertRaises(HTTPException) as error:
                self.begin('full')
            self.assertEqual(error.exception.status_code, 413)
            with self.client() as client:
                result = client.delete(endpoint)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json(), {'status': 'success'})
                self.assertEqual(client.get(url).status_code, 403)
            self.assertEqual(self.files(), [])
            self.finish(self.begin('after-delete'))
        with self.connect() as conn:
            self.assertFalse(conn.execute('SELECT 1 FROM ai_workspace_messages WHERE session_id=?', (original_session['id'],)).fetchone())
            self.assertEqual(len(store.list_sessions(conn, OWNER)), 1)
        self.assertEqual(len(self.files()), 1)

    def test_delete_only_exact_session_references_and_preserves_unknown_files(self):
        self.finish(self.begin())
        first_file = self.files()[0]
        unknown = first_file.parent / 'unreferenced.txt'
        unknown.write_text('Do not sweep', encoding='utf-8')
        outside = self.root / 'outside.png'
        outside.write_bytes(png('green'))
        with self.connect() as conn:
            other = store.create_session(conn, OWNER)
            state = store.begin_request(conn, other['session_uuid'], OWNER, 'other', '其他截图',
                [{'type': 'image', 'name': 'other.png'}], {}, image_uploads=[{'attachment_index': 0, 'contents': png('blue')}])
            store.finish_request(conn, state['session_id'], state['request_id'], OWNER, 'ok', success=True)
            other_url = store.load_history(conn, other['session_uuid'], OWNER)['messages'][0]['attachments'][0]['previewUrl']
            attachments = self.history()['messages'][0]['attachments'] + [
                {'type': 'image', 'previewUrl': other_url},
                {'type': 'image', 'previewUrl': f'/api/ai/workspace/attachments/{self.session["session_uuid"]}/../../outside.png'},
            ]
            conn.execute("UPDATE ai_workspace_messages SET attachments_json=? WHERE session_id=? AND role='user'",
                         (json.dumps(attachments), self.session['id']))
            conn.commit()
        with self.client() as client:
            self.assertEqual(client.delete('/api/ai/workspace/session/' + self.session['session_uuid']).status_code, 200)
            self.assertEqual(client.get(other_url).content, png('blue'))
        self.assertFalse(first_file.exists())
        self.assertEqual(unknown.read_text(encoding='utf-8'), 'Do not sweep')
        self.assertEqual(outside.read_bytes(), png('green'))

    def test_failed_file_cleanup_keeps_history_for_retry_and_stale_lease_is_releasable(self):
        self.finish(self.begin())
        original = self.files()[0]
        endpoint = '/api/ai/workspace/session/' + self.session['session_uuid']
        with patch.object(Path, 'unlink', side_effect=PermissionError('fixture lock')), self.client() as client:
            result = client.delete(endpoint)
            self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(len(self.history()['messages']), 2)
        self.assertTrue(original.exists())
        with self.connect() as conn:
            conn.execute("UPDATE ai_workspace_sessions SET active_request_id='crashed',request_started_at='2000-01-01' WHERE id=?", (self.session['id'],))
            conn.commit()
        with self.client() as client:
            self.assertEqual(client.delete(endpoint).status_code, 200)
        self.assertEqual(self.files(), [])

    def test_disk_and_database_failures_roll_back_admission_and_only_new_files(self):
        with patch.object(images.os, 'replace', side_effect=OSError('fixture disk error')):
            with self.assertRaises(OSError):
                self.begin()
        self.assertEqual(self.files(), [])
        self.assertFalse(self.history()['pending'])
        with self.connect() as conn:
            conn.execute("CREATE TRIGGER reject_message BEFORE INSERT ON ai_workspace_messages BEGIN SELECT RAISE(FAIL, 'fixture database error'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.begin()
        self.assertEqual(self.files(), [])
        self.assertEqual(self.history()['messages'], [])
        self.assertFalse(self.history()['pending'])

    def test_real_multipart_route_recovers_original_and_quota_never_calls_model(self):
        class Upstream:
            is_success = True

            async def aiter_lines(self):
                yield json.dumps({'event': 'answer_delta', 'delta': '看到了截图'})
                yield json.dumps({'event': 'done'})

        calls = []

        @asynccontextmanager
        async def stream(*args, **kwargs):
            calls.append(kwargs['json'])
            yield Upstream()

        async def no_retrieval(*args):
            if False:
                yield ''

        original = png()
        data = {'session_uuid': self.session['session_uuid'], 'request_id': 'multipart', 'message': '请看截图'}
        with patch.object(routes, 'build_user_knowledge_block', return_value=''), \
             patch.object(routes, 'load_explicit_user_profile', return_value={}), \
             patch.object(routes, '_gongwen_retrieval_events', no_retrieval), \
             patch.object(routes, '_platform_data_retrieval_events', no_retrieval), \
             patch.object(routes.ai_client, 'stream', stream), self.client() as client:
            response = client.post('/api/ai/workspace-chat', data=data, files=[('files', ('截图.png', original, 'image/png'))])
            self.assertEqual(response.status_code, 200, response.text)
            history = client.get('/api/ai/workspace/history/' + self.session['session_uuid']).json()
            self.assertEqual(client.get(history['messages'][0]['attachments'][0]['previewUrl']).content, original)
            self.assertEqual(client.post('/api/ai/workspace-chat', data=data, files=[('files', ('截图.png', original, 'image/png'))]).status_code, 200)
            self.assertEqual(len(calls), 1)
            # Same pixels yield the same model JPEG, but changing the original
            # must still conflict with this request ID instead of leaking a file.
            modified = io.BytesIO()
            info = PngInfo()
            info.add_text('fixture', 'changed original metadata')
            Image.open(io.BytesIO(original)).save(modified, 'PNG', pnginfo=info)
            conflict = client.post('/api/ai/workspace-chat', data=data, files=[('files', ('截图.png', modified.getvalue(), 'image/png'))])
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(len(self.files()), 1)
            with patch.object(images, 'MAX_USER_BYTES', len(original)):
                data['request_id'] = 'too-full'
                refused = client.post('/api/ai/workspace-chat', data=data, files=[('files', ('新截图.png', png('blue'), 'image/png'))])
            self.assertEqual(refused.status_code, 413, refused.text)
            self.assertEqual(len(calls), 1)
            self.assertFalse(self.history()['pending'])

    def test_cancelled_image_admission_reconciles_worker_without_orphaned_lease(self):
        entered, release = threading.Event(), threading.Event()
        real_begin = store.begin_request
        admission = {}

        def delayed_begin(*args, **kwargs):
            admission.update(args=args[1:], kwargs=kwargs)
            entered.set()
            if not release.wait(3):
                raise RuntimeError('fixture admission was not released')
            return real_begin(*args, **kwargs)

        async def scenario():
            request = Request({'type': 'http', 'method': 'POST', 'path': '/api/ai/workspace-chat', 'headers': []})
            upload = UploadFile(io.BytesIO(png()), filename='截图.png', headers=Headers({'content-type': 'image/png'}))
            task = asyncio.create_task(routes.handle_ai_workspace_chat(request, files=[upload], message='请看截图',
                user=OWNER, deep_thinking=False, context_prompt_extra='', session_uuid=self.session['session_uuid'],
                request_id='cancelled', page_path='/dashboard'))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with patch.object(store, 'begin_request', delayed_begin):
            asyncio.run(scenario())
        history = self.history()
        self.assertFalse(history['pending'])
        self.assertEqual([message['status'] for message in history['messages']], ['failed', 'failed'])
        self.assertEqual(len(self.files()), 1)
        with self.connect() as conn:
            retried = real_begin(conn, *admission['args'], **admission['kwargs'])
        self.finish(retried)
        self.assertEqual(len(self.files()), 1)


if __name__ == '__main__':
    unittest.main()
