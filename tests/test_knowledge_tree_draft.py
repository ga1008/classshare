"""Explicit draft saves use synthetic in-memory rows only."""
import copy
import json
import unittest
from contextlib import contextmanager
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from test_session_knowledge_tree import _conn, _ready_row
from classroom_app.services import session_knowledge_tree_edit_service as kte
from classroom_app.services import session_knowledge_tree_service as kt


class KnowledgeTreeDraftTests(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()
        self.addCleanup(self.conn.close)
        self.row, self.tree = _ready_row(self.conn)
        self.version = int(self.row['version'])

    def save(self, tree, done=None, version=None, conn=None):
        return kte.save_draft(conn or self.conn, 7, expected_version=self.version if version is None else version,
                              tree=tree, completed_ids=done or [])

    def test_add_edit_delete_undo_and_reload_preserve_ids_and_completion(self):
        leaf = next(kt.iter_leaves(self.tree['nodes']))
        done = [leaf['id']]
        added, done, extra = kte.add_node(self.tree, done, parent_id=self.tree['nodes'][1]['id'])
        edited, done, _ = kte.update_node(added, done, node_id=extra['created_id'], changes={'label': '新增单元'})
        saved = self.save(edited, done)
        self.assertEqual(json.loads(saved['completed_json']), done)
        self.assertEqual(int(saved['version']), self.version + 1)
        removed, reduced, _ = kte.delete_node(edited, done, node_id=leaf['id'])
        deleted = self.save(removed, reduced, version=saved['version'])
        restored = self.save(edited, done, version=deleted['version'])
        self.assertEqual(json.loads(restored['completed_json']), done)
        self.assertEqual(json.loads(restored['tree_json'])['nodes'], edited['nodes'])
        self.assertEqual(restored['source_fingerprint'], self.row['source_fingerprint'])

    def test_response_loss_retry_is_noop(self):
        self.tree['title'] = '保存测试'
        saved = self.save(self.tree)
        retry = self.save(self.tree)
        self.assertEqual(retry['version'], saved['version'])
        self.assertEqual(retry['updated_at'], saved['updated_at'])

    def test_other_window_progress_conflicts_without_overwrite(self):
        leaf = next(kt.iter_leaves(self.tree['nodes']))
        remote = kt.set_leaf_done(self.conn, 7, leaf['id'], True)
        self.tree['title'] = '本地草稿'
        with self.assertRaises(HTTPException) as error:
            self.save(self.tree)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(kt.load_row(self.conn, 7), remote)

    def test_generation_lock_including_race_after_read(self):
        self.tree['title'] = '本地草稿'
        original_execute = self.conn.execute
        def execute(sql, args=()):
            if sql.lstrip().startswith('UPDATE session_knowledge_trees') and 'tree_json = ?' in sql:
                original_execute("UPDATE session_knowledge_trees SET status='generating' WHERE session_id=7")
            return original_execute(sql, args)
        proxy = mock.Mock(wraps=self.conn)
        proxy.execute.side_effect = execute
        with self.assertRaises(HTTPException) as error:
            self.save(self.tree, conn=proxy)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(json.loads(kt.load_row(self.conn, 7)['tree_json'])['title'], 'TCP 可靠传输')
        with self.assertRaises(HTTPException) as error:
            self.save(self.tree)
        self.assertEqual(error.exception.status_code, 409)

    def test_invalid_snapshots_are_atomic(self):
        bad = []
        duplicate = copy.deepcopy(self.tree)
        duplicate['nodes'].append(duplicate['nodes'][0])
        bad.append((duplicate, []))
        empty = {**self.tree, 'nodes': []}
        bad.append((empty, []))
        deep = copy.deepcopy(self.tree)
        next(kt.iter_leaves(deep['nodes']))['children'] = [{'id': 'too-deep', 'label': '第四层', 'weight': 1}]
        bad.append((deep, []))
        long_text = copy.deepcopy(self.tree)
        long_text['nodes'][0]['label'] = 'a' * 41
        bad.append((long_text, []))
        bad.append((self.tree, ['missing']))
        bad.append((self.tree, [self.tree['nodes'][0]['id']]))
        for tree, done in bad:
            with self.subTest(tree=tree, done=done), self.assertRaises(HTTPException):
                self.save(tree, done)
            self.assertEqual(kt.load_row(self.conn, 7), self.row)

    def test_metadata_is_server_owned_and_same_state_does_not_bump(self):
        self.tree['source_fingerprint'] = 'injected'
        self.tree['edited_at'] = 'fake'
        same = self.save(self.tree)
        self.assertEqual(same, self.row)
        self.tree['title'] = '新标题'
        saved = self.save(self.tree)
        self.assertNotIn('source_fingerprint', json.loads(saved['tree_json']))
        self.assertNotEqual(json.loads(saved['tree_json'])['edited_at'], 'fake')


class KnowledgeTreeDraftApiTests(unittest.TestCase):
    def test_permission_validation_and_persisted_response(self):
        from classroom_app.routers import session_knowledge_trees as router
        from classroom_app.dependencies import get_current_user
        conn = _conn()
        self.addCleanup(conn.close)
        row, tree = _ready_row(conn)
        conn.executescript('''
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, teacher_id INTEGER, course_id INTEGER, class_id INTEGER);
            CREATE TABLE class_offering_sessions (id INTEGER PRIMARY KEY, class_offering_id INTEGER);
            INSERT INTO courses VALUES (1,'synthetic'); INSERT INTO classes VALUES (1,'synthetic');
            INSERT INTO class_offerings VALUES (3,1,1,1);
            INSERT INTO class_offering_sessions VALUES (7,3);
        ''')
        @contextmanager
        def connection():
            yield conn
        user = {'id': 1, 'role': 'teacher'}
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[get_current_user] = lambda: user
        tree['title'] = '保存后的标题'
        payload = {'expected_version': row['version'], 'tree': tree, 'completed_ids': []}
        path = '/api/classrooms/3/sessions/7/knowledge-tree/draft'
        # TestClient runs the route on its own thread; use a separately-created
        # shared-memory connection with SQLite's explicit thread opt-in.
        import sqlite3
        shared = sqlite3.connect(':memory:', check_same_thread=False)
        shared.row_factory = sqlite3.Row
        conn.commit()
        conn.backup(shared)
        conn = shared
        self.addCleanup(shared.close)
        with mock.patch.object(router, 'get_db_connection', connection), \
             mock.patch.object(kt, 'session_material_ids', return_value=[]), \
             mock.patch('classroom_app.services.resource_access_service.is_super_admin_teacher', return_value=False), \
             TestClient(app) as client:
            response = client.put(path, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['tree']['title'], '保存后的标题')
            user['role'] = 'student'
            self.assertEqual(client.put(path, json=payload).status_code, 403)
            user.update(role='teacher', id=2)
            self.assertEqual(client.put(path, json=payload).status_code, 403)
            user['id'] = 1
            self.assertEqual(client.put(path.replace('/7/', '/99/'), json=payload).status_code, 404)
            self.assertEqual(client.put(path, json={**payload, 'expected_version': 0}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
