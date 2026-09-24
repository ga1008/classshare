"""Legacy classroom history keeps both account and current class boundaries."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.routers import ai as routes
from classroom_app.services import ai_workspace_legacy_service as legacy
from classroom_app.services import ai_workspace_service as store
from tests import test_ai_workspace as fixture

TEACHER, STUDENT = fixture.TEACHER, fixture.STUDENT


class WorkspaceLegacyTests(unittest.TestCase):
    connect = fixture.WorkspaceTests.connect

    def setUp(self):
        fixture.WorkspaceTests.setUp(self)
        with self.connect() as conn:
            conn.executescript('''
                ALTER TABLE students ADD COLUMN enrollment_status TEXT DEFAULT 'active';
                ALTER TABLE class_offerings ADD COLUMN course_id INTEGER DEFAULT 1;
                ALTER TABLE class_offerings ADD COLUMN teacher_id INTEGER;
                UPDATE class_offerings SET teacher_id=CASE id WHEN 10 THEN 1 ELSE 2 END;
                CREATE TABLE teachers(id INTEGER PRIMARY KEY,is_super_admin INTEGER,is_active INTEGER);
                INSERT INTO teachers VALUES(1,0,1),(2,0,1),(3,1,1);
                CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO courses VALUES(1,'网络基础');
                CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO classes VALUES(11,'一班'),(22,'二班');
                CREATE TABLE ai_chat_sessions(id INTEGER PRIMARY KEY,session_uuid TEXT UNIQUE,class_offering_id INTEGER,
                    user_pk INTEGER,user_role TEXT,title TEXT,context_prompt TEXT,created_at TEXT);
                CREATE TABLE ai_chat_messages(id INTEGER PRIMARY KEY,session_id INTEGER,role TEXT,message TEXT,
                    thinking_content TEXT,final_answer TEXT,attachments_json TEXT,timestamp TEXT);
                INSERT INTO ai_chat_sessions VALUES
                    (1,'teacher-own',10,1,'teacher','旧课堂问题','不可复制的旧隐藏提示','2026-01-01'),
                    (2,'teacher-revoked',20,1,'teacher','原授课课堂','secret','2026-01-02'),
                    (3,'other-teacher',20,2,'teacher','他人问题','secret','2026-01-03'),
                    (4,'student-own',10,1,'student','学生问题','secret','2026-01-04'),
                    (5,'student-combined',20,1,'student','合班问题','secret','2026-01-05');
                INSERT INTO ai_chat_messages VALUES
                    (1,1,'user','解释子网',NULL,NULL,'[{"name":"笔记.txt","type":"text"}]','2026-01-01T10:00:00'),
                    (2,1,'assistant','{"answer":"旧回答"}','旧思考',NULL,NULL,'2026-01-01T10:01:00'),
                    (3,1,'system','不可导入的隐藏提示',NULL,NULL,NULL,'2026-01-01T10:00:00');
            ''')

    def import_session(self, conn, session_uuid='teacher-own', user=TEACHER):
        return legacy.import_legacy_session(conn, session_uuid, user, message_decoder=routes._extract_message_text)

    def test_list_is_owner_scoped_and_uses_current_teacher_and_combined_student_access(self):
        with self.connect() as conn:
            self.assertEqual([s['session_uuid'] for s in legacy.list_legacy_sessions(conn, TEACHER)], ['teacher-own'])
            self.assertEqual([s['session_uuid'] for s in legacy.list_legacy_sessions(conn, STUDENT)], ['student-own'])
            conn.execute('INSERT INTO class_offering_class_links VALUES(20,11)')
            self.assertEqual([s['session_uuid'] for s in legacy.list_legacy_sessions(conn, STUDENT)], ['student-combined', 'student-own'])
            conn.execute("UPDATE students SET enrollment_status='inactive' WHERE id=1")
            self.assertEqual(legacy.list_legacy_sessions(conn, STUDENT), [])
            self.assertEqual(legacy.list_legacy_sessions(conn, {**TEACHER, 'id': 3}), [])

    def test_import_is_complete_idempotent_and_does_not_mutate_legacy_or_count_rounds(self):
        with self.connect() as conn:
            before = [tuple(row) for row in conn.execute('SELECT * FROM ai_chat_messages ORDER BY id')]
            session = self.import_session(conn)
            self.assertEqual(session, self.import_session(conn))
            history = store.load_history(conn, session['session_uuid'], TEACHER)
            self.assertEqual([m['message'] for m in history['messages']], ['解释子网', '旧回答'])
            self.assertEqual(history['messages'][0]['attachments'][0]['name'], '笔记.txt')
            self.assertEqual(history['messages'][1]['thinking_content'], '旧思考')
            self.assertFalse(history['pending'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_sessions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_messages').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_profile_states').fetchone()[0], 0)
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM ai_chat_messages ORDER BY id')], before)
            self.assertNotIn('隐藏提示', json.dumps(history, ensure_ascii=False))
            conn.execute("INSERT INTO ai_chat_messages VALUES(4,1,'user','后来的消息',NULL,NULL,NULL,'2026-02-01')")
            conn.commit()
            self.import_session(conn)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_messages').fetchone()[0], 2)

    def test_import_checks_owner_role_and_current_class_even_after_prior_import(self):
        with self.connect() as conn:
            for session_uuid, user in (('teacher-own', STUDENT), ('teacher-own', {**TEACHER, 'id': 2}),
                                       ('teacher-revoked', TEACHER), ('missing', TEACHER)):
                with self.subTest(session=session_uuid, user=user), self.assertRaises(HTTPException) as error:
                    self.import_session(conn, session_uuid, user)
                self.assertEqual(error.exception.status_code, 403)
            self.import_session(conn)
            conn.execute('UPDATE class_offerings SET teacher_id=2 WHERE id=10')
            conn.commit()
            with self.assertRaises(HTTPException):
                self.import_session(conn)

    def test_concurrent_import_has_one_snapshot_and_applies_existing_output_filter(self):
        with self.connect() as conn:
            conn.execute("UPDATE ai_chat_messages SET thinking_content='侧写师的隐藏提示',final_answer='侧写师建议练习' WHERE id=2")
        barrier = Barrier(2)

        def import_once():
            with self.connect() as conn:
                barrier.wait(timeout=5)
                return self.import_session(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: import_once(), range(2)))
        self.assertEqual(results[0], results[1])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_sessions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_workspace_messages').fetchone()[0], 2)
            history = store.load_history(conn, results[0]['session_uuid'], TEACHER)
            self.assertNotIn('侧写师', json.dumps(history, ensure_ascii=False))
            self.assertNotIn('隐藏提示', json.dumps(history, ensure_ascii=False))

    def test_routes_expose_group_and_import_without_ai_and_assessment_stays_blocked(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: TEACHER
        with patch.object(routes, 'get_db_connection', self.connect), \
             patch.object(routes.ai_client, 'stream') as upstream, TestClient(app) as client:
            response = client.get('/api/ai/workspace/sessions')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['sessions'], [])
            self.assertEqual(response.json()['legacy_sessions'][0]['course_name'], '网络基础')
            imported = client.post('/api/ai/workspace/session/import/teacher-own')
            self.assertEqual(imported.status_code, 200, imported.text)
            again = client.post('/api/ai/workspace/session/import/teacher-own')
            self.assertEqual(imported.json(), again.json())
            session_uuid = imported.json()['session']['session_uuid']
            self.assertEqual(len(client.get('/api/ai/workspace/history/' + session_uuid).json()['messages']), 2)
            app.dependency_overrides[routes.get_current_user] = lambda: STUDENT
            response = client.post('/api/ai/workspace/session/import/student-own', headers={'referer': 'http://testserver/submission/1'})
            self.assertEqual(response.status_code, 403)
            upstream.assert_not_called()
