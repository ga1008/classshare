"""Real Web/Agent material lifecycle, permission and task-file multipart tests."""
import asyncio
from contextlib import contextmanager
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
import uuid

from fastapi import HTTPException
from classroom_app.routers.materials_parts import node_ops, library
from classroom_app.services import agent_platform_request_registry as registry
from classroom_app.services import material_delete_service as deletion
from classroom_app.services import materials_service
from tests.test_agent_material_content_requests import MaterialContentRequestFixture, FixedClock


class MaterialManagementFixture(MaterialContentRequestFixture):
    @contextmanager
    def connection(self):
        with super().connection() as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            yield conn

    def setUp(self):
        super().setUp()
        self.patched('classroom_app.routers.materials_parts.node_ops.get_db_connection', self.connection)
        self.patched('classroom_app.routers.materials_parts.node_ops.datetime', FixedClock)
        self.patched('classroom_app.services.materials_git_service._now_iso', return_value='2026-09-10T12:00:00')
        with self.connection() as conn:
            original = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='course_materials'").fetchone()[0]
            conn.execute('DROP TABLE course_materials')
            conn.execute(original.replace('parent_id INTEGER,', 'parent_id INTEGER REFERENCES course_materials(id) ON DELETE CASCADE,'))
            for name, definition in {'owner_role':"TEXT DEFAULT 'teacher'",'owner_user_pk':'INTEGER',
                    'school_code':"TEXT DEFAULT ''",'school_name':"TEXT DEFAULT ''",'college':"TEXT DEFAULT ''",
                    'department':"TEXT DEFAULT ''",'published_at':'TEXT',
                    **{key:"TEXT DEFAULT ''" for key in ('git_repo_status','git_provider','git_remote_name','git_remote_url',
                        'git_remote_host','git_remote_protocol','git_default_branch','git_head_branch','git_detect_error','git_detected_at')}}.items():
                conn.execute(f'ALTER TABLE course_materials ADD COLUMN {name} {definition}')
            conn.executescript("""
                CREATE TABLE course_material_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT,material_id INTEGER,
                    class_offering_id INTEGER,assigned_by_teacher_id INTEGER,created_at TEXT,UNIQUE(material_id,class_offering_id));
                CREATE TABLE learning_material_progress(id INTEGER PRIMARY KEY,material_id INTEGER,class_offering_id INTEGER,
                    student_id INTEGER,is_completed INTEGER DEFAULT 0,is_mastered INTEGER DEFAULT 0,updated_at TEXT);
                ALTER TABLE class_offerings ADD COLUMN semester TEXT DEFAULT '';
                ALTER TABLE class_offering_sessions ADD COLUMN title TEXT DEFAULT '';
                ALTER TABLE class_offering_sessions ADD COLUMN updated_at TEXT;
                ALTER TABLE courses ADD COLUMN created_by_teacher_id INTEGER;
                UPDATE courses SET created_by_teacher_id=CASE id WHEN 1 THEN 7 ELSE 8 END;
            """)
            conn.commit()
        self.app.include_router(node_ops.router)
        self.workspace_root = self.blobs.parent / ('task-files-' + uuid.uuid4().hex)
        self.addCleanup(lambda: __import__('shutil').rmtree(self.workspace_root))
        for task_id in (10,11,12):
            (self.workspace_root / 'tasks' / str(task_id)).mkdir(parents=True)
        self.patched('classroom_app.services.agent_platform_multipart_service.AGENT_TASK_WORKSPACE_ROOT', self.workspace_root)

    def row(self, mid):
        return dict(self.sql('SELECT * FROM course_materials WHERE id=?', (mid,))[0])

    def create(self, name, *, parent=None, kind='folder', actor='teacher', content='# Synthetic document'):
        body = {'name':name, 'parent_id':parent}
        if kind == 'file': body['content'] = content
        result = self.dispatch(actor, 'http.materials.' + kind + '.create', body=body)
        self.assertEqual(200, result['result']['http_status'], result)
        return result['result']['data']['material']['id']

    def move_body(self, mid, target):
        return {'target_parent_id':target, 'expected_updated_at':self.row(mid)['updated_at'] or 'legacy',
                'expected_target_updated_at':(self.row(target)['updated_at'] or 'legacy') if target else 'root'}

    def impact(self, mid):
        response = self.dispatch('teacher', 'http.materials.delete.impact', path_params={'material_id':mid})
        self.assertEqual(200, response['result']['http_status'], response)
        return response['result']['data']['impact']


class AgentMaterialManagementTests(MaterialManagementFixture):
    def test_create_nested_folder_markdown_and_unique_names_match_normal_web(self):
        root = self.create('Root')
        self.sql("UPDATE course_materials SET scope_level='school' WHERE id=?", (root,))
        child = self.create('Nested', parent=root)
        mid = self.create('Lesson', parent=child, kind='file')
        row = self.row(mid)
        self.assertEqual('Root/Nested/Lesson.md', row['material_path'])
        self.assertEqual(('school',root,child), (row['scope_level'],row['root_id'],row['parent_id']))
        from classroom_app.services.file_service import resolve_global_file_path
        self.assertEqual(b'# Synthetic document', resolve_global_file_path(row['file_hash']).read_bytes())
        unique = self.create('Lesson', parent=child, kind='file')
        self.assertEqual('Lesson (2).md', self.row(unique)['name'])
        result = self.restored_parity('teacher', 'http.materials.folder.create', body={'name':'Parity','parent_id':child})
        self.assertEqual(200, result['result']['http_status'])
        for role, expected in (('student',403),('other',404)):
            normal = self.web(role,'POST','/api/materials/folders',json={'name':'Denied','parent_id':root})
            delegated = self.dispatch(role,'http.materials.folder.create',body={'name':'Denied','parent_id':root})
            self.assertEqual(expected,normal.status_code)
            self.assertEqual(expected,delegated['result']['http_status'])

    def test_move_cas_scope_cycle_and_duplicate_operation_preserve_tree(self):
        source = self.create('Source')
        child = self.create('Child',parent=source)
        leaf = self.create('Leaf',parent=child,kind='file')
        dest = self.create('Destination')
        self.sql("UPDATE course_materials SET scope_level='public' WHERE id=?", (dest,))
        body = self.move_body(child,dest)
        operation_id = str(uuid.uuid4())
        result = self.dispatch('teacher','http.materials.move',operation_id,path_params={'material_id':child},body=body)
        self.assertEqual(200,result['result']['http_status'],result)
        self.assertEqual('Destination/Child/Leaf.md',self.row(leaf)['material_path'])
        self.assertEqual('public',self.row(leaf)['scope_level'])
        self.assertEqual(result,self.dispatch('teacher','http.materials.move',operation_id,path_params={'material_id':child},body=body))
        stale = self.web('teacher','POST',f'/api/materials/{child}/move',json=body)
        self.assertEqual(409,stale.status_code)
        cycle = self.dispatch('teacher','http.materials.move',path_params={'material_id':dest},body=self.move_body(dest,child))
        self.assertEqual(400,cycle['result']['http_status'])
        root_move = self.restored_parity('teacher','http.materials.move',path_params={'material_id':child},body=self.move_body(child,None))
        self.assertEqual(200,root_move['result']['http_status'],root_move)
        self.assertEqual(child,self.row(leaf)['root_id'])

    def test_admin_creates_in_managed_foreign_tree_with_inherited_ownership(self):
        root = self.create('Other teacher root',actor='other')
        child = self.create('Admin addition',parent=root)
        leaf = self.create('Admin source',parent=child,kind='file')
        for mid in (root,child,leaf):
            self.assertEqual(8,self.row(mid)['teacher_id'])
        path = self.workspace_root/'tasks'/'10'/'source.txt'
        path.write_bytes(b'Admin upload for other teacher')
        result = self.dispatch('teacher','http.materials.upload',body={'parent_id':root},files=[{'path':'source.txt'}])
        self.assertEqual(200,result['result']['http_status'],result)
        uploaded = result['result']['data']['created_items'][0]['id']
        self.assertEqual(8,self.row(uploaded)['teacher_id'])
        ledger = self.sql('SELECT actor_role,actor_id FROM agent_platform_requests WHERE id=?',(result['request_id'],))[0]
        self.assertEqual(('teacher',7),tuple(ledger))

    def test_delete_complete_snapshot_detects_same_count_replacement_and_content_change(self):
        mid = self.create('Delete source',kind='file')
        self.sql("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id,created_at) VALUES(?,1,7,'now')",(mid,))
        first = self.impact(mid)
        self.sql('UPDATE course_material_assignments SET class_offering_id=2 WHERE material_id=?',(mid,))
        second = self.impact(mid)
        self.assertEqual(first['total_reference_count'],second['total_reference_count'])
        self.assertNotEqual(first['impact_token'],second['impact_token'])
        rejected = self.dispatch('teacher','http.materials.delete',path_params={'material_id':mid},query_params={
            'unlink_references':True,'impact_token':first['impact_token']})
        self.assertEqual(409,rejected['result']['http_status'])
        self.assertEqual(1,len(self.sql('SELECT * FROM course_material_assignments')))
        self.sql('UPDATE course_materials SET file_hash=? WHERE id=?',('a'*64,mid))
        self.assertNotEqual(second['impact_token'],self.impact(mid)['impact_token'])

    def test_wildcard_directory_never_authorizes_or_unlinks_private_sibling(self):
        root = self.create('Root')
        allowed = self.create('unit_%',parent=root)
        other = self.create('unit_X',parent=root)
        own = self.create('Public lesson',parent=allowed,kind='file')
        secret = self.create('Private secret name',parent=other,kind='file')
        self.sql("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id,created_at) VALUES(?,1,7,'now')",(allowed,))
        self.sql("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id,created_at) VALUES(?,2,7,'now')",(secret,))
        user = {'id':7,'role':'student'}
        with self.connection() as conn:
            self.assertEqual(own,materials_service.ensure_user_material_access(conn,own,user)['id'])
            with self.assertRaises(HTTPException): materials_service.ensure_user_material_access(conn,secret,user)
        tree = self.web('student','GET',f'/api/materials/{own}/tree')
        self.assertEqual(200,tree.status_code,tree.text)
        self.assertNotIn('Private secret name',tree.text)
        impact = self.impact(allowed)
        self.assertEqual(1,impact['total_reference_count'])
        self.assertEqual(2,impact['subtree']['node_count'])
        deleted = self.dispatch('teacher','http.materials.delete',path_params={'material_id':allowed},query_params={
            'unlink_references':True,'impact_token':impact['impact_token']})
        self.assertEqual(200,deleted['result']['http_status'],deleted)
        self.assertEqual(secret,self.sql('SELECT material_id FROM course_material_assignments')[0][0])
        self.assertEqual([],self.sql('SELECT id FROM course_materials WHERE id=?',(own,)))

    def test_task_upload_uses_correct_existing_base_without_duplicate_ancestor(self):
        root = self.create('Upload destination')
        task_file = self.workspace_root / 'tasks' / '10' / 'lesson.md'
        task_file.write_bytes(b'# Task source')
        operation_id = str(uuid.uuid4())
        args = {'body':{'parent_id':root},'files':[{'path':'lesson.md','filename':'subdir/lesson.md'}]}
        result = self.dispatch('teacher','http.materials.upload',operation_id,**args)
        self.assertEqual(200,result['result']['http_status'],result)
        self.assertEqual(1,result['result']['data']['uploaded_file_count'])
        rows = self.sql('SELECT material_path FROM course_materials ORDER BY id')
        self.assertEqual(['Upload destination','Upload destination/subdir','Upload destination/subdir/lesson.md'],[row[0] for row in rows])
        self.assertEqual(result,self.dispatch('teacher','http.materials.upload',operation_id,**args))
        self.assertEqual(3,len(self.sql('SELECT id FROM course_materials')))
        task_file.write_bytes(b'Changed task bytes')
        with self.assertRaises(HTTPException) as conflict: self.dispatch('teacher','http.materials.upload',operation_id,**args)
        self.assertEqual(409,conflict.exception.status_code)
        with self.assertRaises(HTTPException): self.dispatch('teacher','http.materials.upload',body={'parent_id':root},files=[{'path':'lesson.md','filename':'archive.zip'}])

    def test_root_material_upload_matches_actual_normal_multipart_response(self):
        path = self.workspace_root/'tasks'/'10'/'source.txt'
        path.write_bytes(b'Root upload')
        snapshot = sqlite3.connect(':memory:')
        with self.connection() as conn: conn.backup(snapshot)
        try:
            normal = self.web('teacher','POST','/api/materials/upload',files={'files':('source.txt',b'Root upload','text/plain')})
            with self.connection() as conn: snapshot.backup(conn)
        finally: snapshot.close()
        delegated = self.dispatch('teacher','http.materials.upload',body={'parent_id':None},files=[{'path':'source.txt'}])
        self.assertEqual(200,normal.status_code,normal.text)
        self.assertEqual(normal.status_code,delegated['result']['http_status'],delegated)
        self.assertEqual(normal.json(),delegated['result']['data'])

    def test_delete_snapshot_includes_references_beyond_truncated_ui_samples(self):
        mid = self.create('Many references',kind='file')
        for number in range(100,126):
            self.sql('INSERT INTO class_offerings(id,teacher_id,class_id,course_id) VALUES(?,7,30,1)',(number,))
            self.sql("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id,created_at) VALUES(?,?,7,'now')",(mid,number))
        first = self.impact(mid)
        self.assertTrue(first['groups'][0]['has_more'])
        self.assertEqual(26,first['total_reference_count'])
        self.sql('UPDATE course_material_assignments SET assigned_by_teacher_id=8 WHERE class_offering_id=125')
        self.assertNotEqual(first['impact_token'],self.impact(mid)['impact_token'])

    def test_plain_tree_guard_and_ancestor_delete_archives_registered_pack(self):
        ancestor = self.create('Container')
        pack_root = self.create('Registered package',parent=ancestor)
        child = self.create('Internal source',parent=pack_root,kind='file')
        self.sql("INSERT INTO course_doc_packs(root_material_id,course_id,teacher_id,created_at,updated_at) VALUES(?,1,7,'now','now')",(pack_root,))
        blocked = self.dispatch('teacher','http.materials.move',path_params={'material_id':pack_root},body=self.move_body(pack_root,None))
        self.assertEqual(409,blocked['result']['http_status'])
        create = self.dispatch('teacher','http.materials.folder.create',body={'parent_id':pack_root,'name':'Break layout'})
        self.assertEqual(409,create['result']['http_status'])
        inner = self.impact(child)
        self.assertFalse(inner['can_delete_directly'])
        self.assertIn('学习文档包内部', inner['structural_blocker'])
        refused = self.dispatch('teacher','http.materials.delete',path_params={'material_id':child},query_params={'unlink_references':False,'impact_token':inner['impact_token']})
        self.assertEqual(409,refused['result']['http_status'])
        impact = self.impact(ancestor)
        self.assertEqual(1, impact['subtree']['registered_pack_count'])
        self.assertIsNone(impact['structural_blocker'])
        deleted = self.dispatch('teacher','http.materials.delete',path_params={'material_id':ancestor},query_params={'unlink_references':False,'impact_token':impact['impact_token']})
        self.assertEqual(200,deleted['result']['http_status'],deleted)
        self.assertEqual(1,deleted['result']['data']['archived_pack_count'])
        self.assertEqual('archived',self.sql('SELECT status FROM course_doc_packs')[0][0])
        self.assertEqual([],self.sql('SELECT id FROM course_materials'))
