"""Real scoped file HTTP/SQL authorization against normal Web downloads.

Only synthetic auth decoding, DB/storage locations and generous fixture rate
configuration are substituted. Permission, revocation, budget SQL, snapshot
reader and Word text extraction remain real. No production data or model calls.
"""
from dataclasses import replace
import hashlib
from io import BytesIO
from pathlib import Path

from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.routers import agent_bridge, files
from classroom_app.services import agent_bridge_service as reader
from classroom_app.services import agent_scoped_read_service as scoped
from classroom_app.services import agent_request_budget_service as budget
from tests.test_agent_platform_request_learning import LearningRequestFixture


class AgentScopedFileTypesTests(LearningRequestFixture):
    def setUp(self):
        super().setUp()
        self.blobs = Path(self.path).parent / 'synthetic-blobs'
        self.blobs.mkdir()
        self.patched('classroom_app.services.file_service.GLOBAL_FILES_DIR', self.blobs)
        self.patched('classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS', ())
        self.patched('classroom_app.services.agent_bridge_service.allowed_file_roots', return_value=[self.blobs])
        for target in ('classroom_app.routers.agent_bridge.get_db_connection',
                       'classroom_app.routers.files.get_db_connection',
                       'classroom_app.services.agent_gateway_budget.get_db_connection'):
            self.patched(target, self.connection)
        configured = budget.CHANNEL_BUDGETS['tools']
        generous = budget.ScopeBudget(100, 100, 8)
        self.patched('classroom_app.services.agent_request_budget_service.CHANNEL_BUDGETS',
            {**budget.CHANNEL_BUDGETS, 'tools': replace(configured, platform=generous, actor=generous, task=generous)})
        with self.connection() as conn:
            conn.executescript("""
              ALTER TABLE courses ADD COLUMN created_by_teacher_id INTEGER;
              UPDATE courses SET created_by_teacher_id=CASE id WHEN 1 THEN 7 ELSE 8 END;
              ALTER TABLE assignments ADD COLUMN course_id INTEGER;
              CREATE TABLE submission_files(id INTEGER PRIMARY KEY,submission_id INTEGER,stored_path TEXT,
                original_filename TEXT,mime_type TEXT,file_size INTEGER,file_hash TEXT);
              CREATE TABLE study_group_files(id INTEGER PRIMARY KEY,group_id INTEGER,file_hash TEXT,original_filename TEXT,
                mime_type TEXT,file_size INTEGER,description TEXT,uploaded_by_name TEXT,uploaded_by_role TEXT,
                uploaded_by_user_pk INTEGER,created_at TEXT);
              CREATE TABLE course_files(id INTEGER PRIMARY KEY,course_id INTEGER,file_hash TEXT,file_name TEXT,file_size INTEGER,
                scope_level TEXT,owner_role TEXT,owner_user_pk INTEGER,class_offering_id INTEGER,description TEXT);
              INSERT INTO assignments(id,class_offering_id,course_id,title,status) VALUES(1,1,1,'Class task','published'),(2,1,1,'Personal task','published');
              INSERT INTO submissions(id,assignment_id,student_pk_id,status) VALUES(1,1,7,'submitted'),(2,2,7,'submitted');
              INSERT INTO learning_stage_exam_attempts(id,assignment_id,student_id) VALUES(1,2,7);
              INSERT INTO study_groups(id,class_offering_id,name,status,max_members,leader_student_id)
                VALUES(1,1,'Private group','active',3,7),(2,2,'Other classroom group','active',3,8);
              INSERT INTO study_group_members(group_id,student_id,status) VALUES(1,7,'active'),(2,8,'active');
            """)
            ensure_agent_request_budget_schema(conn)
            conn.commit()
        self.payload = 'Synthetic authorized text 文件'.encode()
        self.sha, self.source = self.blob(self.payload)
        self.sql('INSERT INTO submission_files VALUES(1,1,?,?,?,?,?)',
            (str(self.source), 'assignment.txt', 'text/plain', len(self.payload), self.sha))
        self.sql('INSERT INTO submission_files VALUES(2,2,?,?,?,?,?)',
            (str(self.source), 'personal.txt', 'text/plain', len(self.payload), self.sha))
        for file_id, group_id in ((1,1),(2,2)):
            self.sql('INSERT INTO study_group_files VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (file_id,group_id,self.sha,'group.txt','text/plain',len(self.payload),'','Student','student',7,'now'))
        for file_id, scope, owner_role, owner_id in ((1,'classroom','teacher',7),(2,'private','student',7),(3,'private','teacher',8)):
            self.sql('INSERT INTO course_files VALUES(?,?,?,?,?,?,?,?,?,?)',
                (file_id,1,self.sha,'course.txt',len(self.payload),scope,owner_role,owner_id,1,''))
        self.app.include_router(files.router)
        self.app.include_router(agent_bridge.router)

    def blob(self, content):
        digest = hashlib.sha256(content).hexdigest()
        source = self.blobs / digest
        source.write_bytes(content)
        return digest, source

    def read(self, role='student', **selectors):
        # Cookies deliberately cannot select the runtime's identity.
        self.client.cookies.set('access_token', 'teacher')
        return self.client.post('/api/agent-bridge/file', json=selectors,
            headers={'Authorization': 'Bearer ' + self.tokens[role]})

    def parity(self, role, selector, file_id, expected):
        normal_path = {'submission_file_id':'/submissions/download/',
            'collaboration_file_id':'/api/collaboration/files/', 'course_file_id':'/download/course_file/'}[selector]
        normal_path += str(file_id) + ('/download' if selector == 'collaboration_file_id' else '')
        normal = self.web(role, 'GET', normal_path)
        delegated = self.read(role, **{selector:file_id})
        self.assertEqual(expected, normal.status_code, normal.text)
        self.assertEqual(normal.status_code, delegated.status_code, delegated.text)
        if expected == 200:
            data = delegated.json()
            self.assertEqual(self.payload, normal.content)
            self.assertEqual(self.payload.decode(), data['content'])
            self.assertEqual(self.sha, data['sha256'])
            self.assertEqual(self.sha, data['revision'])
            self.assertEqual(normal_path, data['url'])
            for private in ('path','stored_path','_source_binding','file_hash','owner_user_pk'):
                self.assertNotIn(private, data)
            self.assertNotIn(str(self.blobs), delegated.text)
        return delegated

    def test_submission_owner_teacher_foreign_and_personal_stage_match_normal_download(self):
        for role, expected in (('student',200),('teacher',200),('peer',403),('other',403)):
            self.parity(role, 'submission_file_id', 1, expected)
        self.parity('student', 'submission_file_id', 2, 200)
        self.parity('teacher', 'submission_file_id', 2, 404)
        self.parity('peer', 'submission_file_id', 2, 403)
        self.parity('student', 'submission_file_id', 999, 404)

    def test_collaboration_private_membership_and_teacher_classroom_match_normal_download(self):
        for role, expected in (('student',200),('teacher',200),('peer',403),('other',403)):
            self.parity(role, 'collaboration_file_id', 1, expected)
        # Platform admin does not bypass a policy that the normal group route
        # deliberately restricts to the assigned classroom teacher.
        self.parity('teacher', 'collaboration_file_id', 2, 403)
        self.parity('other', 'collaboration_file_id', 2, 200)
        self.sql("UPDATE study_group_members SET status='left' WHERE group_id=1 AND student_id=7")
        self.parity('student', 'collaboration_file_id', 1, 403)

    def test_course_scope_role_collision_admin_and_download_size_policy_match_web(self):
        for role, file_id, expected in (('student',1,200),('peer',1,200),('other',1,403),
                ('student',2,200),('peer',2,403),('teacher',2,200),('student',3,403),('other',3,200)):
            self.parity(role, 'course_file_id', file_id, expected)
        self.patched('classroom_app.services.download_policy.CLASSROOM_DOWNLOAD_LIMIT_ACTIVE', True)
        self.patched('classroom_app.services.download_policy.CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES', 1)
        self.parity('teacher', 'course_file_id', 1, 403)
        # The normal submission/group routes impose no shared-course quota.
        self.parity('student', 'submission_file_id', 1, 200)
        self.parity('student', 'collaboration_file_id', 1, 200)

    def test_exactly_one_selector_strict_ids_and_no_arbitrary_platform_paths(self):
        for payload in ({}, {'path':' '}, {'submission_file_id':True}, {'submission_file_id':'1'},
                {'course_file_id':0}, {'collaboration_file_id':2**63}, {'stored_path':str(self.source)}):
            with self.subTest(payload=payload):
                self.assertEqual(422, self.read(**payload).status_code)
        selectors = {'material_id':1,'submission_file_id':1,'collaboration_file_id':1,'course_file_id':1,'path':'report.txt'}
        import itertools
        for left, right in itertools.combinations(selectors, 2):
            self.assertEqual(422, self.read(**{left:selectors[left],right:selectors[right]}).status_code)
        self.assertEqual(400, self.read(submission_file_id=1,parent_task_id=11).status_code)
        self.assertEqual(403, self.read(path=str(self.source)).status_code)
        self.assertEqual(401, self.client.post('/api/agent-bridge/file', json={'course_file_id':1}).status_code)

    def test_real_document_extraction_uses_authorized_filename_and_actual_byte_revision(self):
        from docx import Document
        document = Document()
        document.add_paragraph('Authorized submitted Word evidence')
        output = BytesIO()
        document.save(output)
        digest, source = self.blob(output.getvalue())
        self.sql("UPDATE submission_files SET stored_path=?,original_filename='evidence.docx',file_size=?,file_hash=NULL WHERE id=1",
            (str(source),len(output.getvalue())))
        response = self.read(submission_file_id=1)
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn('Authorized submitted Word evidence', response.json()['content'])
        self.assertTrue(response.json()['extracted'])
        self.assertEqual(digest, response.json()['revision'])
        self.assertNotIn('base64', response.text)
        self.assertEqual(200, self.read(submission_file_id=1,revision=digest).status_code)
        self.assertEqual(409, self.read(submission_file_id=1,revision='0'*64).status_code)
        self.assertIsNone(self.sql('SELECT file_hash FROM submission_files WHERE id=1')[0][0])

    def test_changed_hash_and_source_pointer_are_rejected_before_returning_content(self):
        self.assertEqual(409, self.read(course_file_id=1,revision='0'*64).status_code)
        self.source.write_bytes(b'Synthetic storage corruption')
        self.assertEqual(409, self.read(course_file_id=1).status_code)
        self.source.write_bytes(self.payload)
        original = scoped.read_platform_file
        def change_binding(*args, **kwargs):
            result = original(*args, **kwargs)
            self.sql("UPDATE course_files SET description='Changed during extraction' WHERE id=1")
            return result
        self.patched('classroom_app.services.agent_scoped_read_service.read_platform_file', side_effect=change_binding)
        changed = self.read(course_file_id=1)
        self.assertEqual(409, changed.status_code)
        self.assertNotIn(self.payload.decode(), changed.text)

    def test_membership_is_rechecked_after_extraction_and_no_content_escapes(self):
        original = scoped.read_platform_file
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.sql("UPDATE study_group_members SET status='left' WHERE group_id=1 AND student_id=7")
            return result
        self.patched('classroom_app.services.agent_scoped_read_service.read_platform_file', side_effect=revoke)
        response = self.read(collaboration_file_id=1)
        self.assertEqual(403, response.status_code)
        self.assertNotIn(self.payload.decode(), response.text)

    def test_source_session_revoked_after_snapshot_refuses_disclosure_and_releases_budget(self):
        original = scoped.read_platform_file
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.sql("DELETE FROM user_sessions WHERE session_user_key='student:7'")
            return result
        self.patched('classroom_app.services.agent_scoped_read_service.read_platform_file', side_effect=revoke)
        response = self.read(submission_file_id=1)
        self.assertEqual(401, response.status_code, response.text)
        self.assertNotIn(self.payload.decode(), response.text)
        self.assertEqual(0, self.sql("SELECT COUNT(*) FROM agent_request_budget_leases WHERE status='active'")[0][0])

    def test_stored_path_outside_platform_roots_and_binary_context_transfer_are_rejected(self):
        outside = Path(self.path).parent / 'host-private.txt'
        outside.write_text('Host private data', encoding='utf-8')
        self.sql('UPDATE submission_files SET stored_path=?,file_hash=NULL WHERE id=1', (str(outside),))
        result = self.read(submission_file_id=1)
        self.assertEqual(400, result.status_code)
        self.assertNotIn('Host private data', result.text)
        digest, path = self.blob(b'\x00\xff\x01binary')
        self.sql("UPDATE submission_files SET stored_path=?,original_filename='opaque.bin',file_hash=? WHERE id=1", (str(path),digest))
        self.assertEqual(400, self.read(submission_file_id=1).status_code)

    def test_mcp_public_tool_describes_all_selectors_and_dispatches_scoped_identity(self):
        from classroom_app.services.agent_actor_service import resolve_agent_actor
        with self.connection() as conn:
            actor = resolve_agent_actor(conn, 'student', 7)
        tools = {tool['name']:tool for tool in agent_bridge._mcp_tools(actor)}
        schema = tools['platform_file']['inputSchema']['properties']
        for selector in ('path','material_id','submission_file_id','collaboration_file_id','course_file_id','revision'):
            self.assertIn(selector, schema)
        response = self.client.post('/api/agent-bridge/mcp', json={
            'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'platform_file','arguments':{'submission_file_id':1}}},
            headers={'Authorization':'Bearer '+self.tokens['student']})
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn('Synthetic authorized text', response.text)
        self.assertNotIn(str(self.blobs), response.text)
