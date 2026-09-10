"""Real HTTP/SQL/byte-snapshot import authorization using synthetic storage.

Only the Linux atomic publication syscall layer is replaced in these portable
HTTP tests. Its actual no-follow/no-overwrite implementation runs separately in
the Linux probe. The source reader, grants, original file policy and bytes are real.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import unittest

from classroom_app.services import agent_platform_download_service as downloads
from classroom_app.services import agent_task_service
from tests.test_agent_scoped_file_types import AgentScopedFileTypesTests


class AgentPlatformDownloadTests(AgentScopedFileTypesTests):
    def setUp(self):
        super().setUp()
        self.workspaces = Path(self.path).parent / 'task-workspaces'
        for task_id in (10, 11, 12, 13, 19):
            (self.workspaces / 'tasks' / str(task_id)).mkdir(parents=True)
        self.patched('classroom_app.services.agent_platform_download_service.AGENT_TASK_WORKSPACE_ROOT', self.workspaces)
        self.patched('classroom_app.services.agent_platform_download_service.allowed_file_roots', return_value=[self.blobs,self.workspaces])
        self.publications = []
        def publish(root, snapshot, filename, *, authorize):
            authorize()
            self.publications.append((root, snapshot['sha256'], filename))
            relative = Path('inputs') / (snapshot['sha256'] + '.fixture')
            target = root / relative
            target.parent.mkdir(exist_ok=True)
            snapshot['stream'].seek(0)
            target.write_bytes(snapshot['stream'].read())
            return relative.as_posix()
        self.publish = publish
        self.patched('classroom_app.services.agent_platform_download_service.publish_snapshot', side_effect=publish)

    def download(self, role='student', **arguments):
        self.client.cookies.set('access_token', 'teacher')
        return self.client.post('/api/agent-bridge/download', json=arguments,
            headers={'Authorization':'Bearer '+self.tokens[role]})

    def test_binary_source_stays_out_of_response_and_import_uses_current_task(self):
        binary = b'\x00\xff\x01synthetic-private-binary' * 100
        digest, path = self.blob(binary)
        self.sql("UPDATE submission_files SET stored_path=?,original_filename='analysis.xlsx',file_size=?,file_hash=? WHERE id=1",
                 (str(path),len(binary),digest))
        response = self.download(submission_file_id=1,revision=digest)
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        target = self.workspaces / 'tasks' / '11' / result['path']
        self.assertEqual(binary,target.read_bytes())
        self.assertEqual(digest,result['sha256'])
        self.assertEqual(len(binary),result['size'])
        self.assertEqual('analysis.xlsx',result['filename'])
        self.assertTrue(result['input_file'])
        for hidden in (str(path),str(self.workspaces),'synthetic-private-binary','_source_binding','base64'):
            self.assertNotIn(hidden,response.text)
        self.assertEqual(200,self.download(submission_file_id=1,revision=digest).status_code)
        self.assertEqual(1,len(list((self.workspaces/'tasks'/'11'/'inputs').iterdir())))

    def test_current_normal_file_policy_still_rejects_same_numbered_role_and_foreign_files(self):
        for role, selectors, status in (
            ('student',{'course_file_id':2},200), ('teacher',{'course_file_id':2},200),
            ('student',{'course_file_id':3},403),
            ('student',{'collaboration_file_id':2},403), ('teacher',{'submission_file_id':2},404),
            ('other',{'submission_file_id':1},403)):
            with self.subTest(role=role,selectors=selectors):
                kind,file_id=next(iter(selectors.items()))
                normal={'course_file_id':f'/download/course_file/{file_id}',
                        'collaboration_file_id':f'/api/collaboration/files/{file_id}/download',
                        'submission_file_id':f'/submissions/download/{file_id}'}[kind]
                self.assertEqual(status,self.web(role,'GET',normal).status_code)
                result=self.download(role,**selectors)
                self.assertEqual(status,result.status_code,result.text)
        self.assertEqual(2,len(self.publications))

    def test_strict_selection_and_expected_hash_cannot_choose_destination_or_switch_actor(self):
        for invalid in ({}, {'submission_file_id':True}, {'submission_file_id':'1'},
                        {'submission_file_id':1,'course_file_id':1}, {'submission_file_id':1,'path':'x'},
                        {'submission_file_id':1,'parent_task_id':10}, {'submission_file_id':1,'destination':'/tmp'},
                        {'submission_file_id':1,'revision':'not-a-hash'}):
            with self.subTest(invalid=invalid):
                self.assertEqual(422,self.download(**invalid).status_code)
        self.assertEqual(409,self.download(submission_file_id=1,revision='0'*64).status_code)
        self.assertFalse(self.publications)

    def test_after_snapshot_membership_revocation_prevents_publication(self):
        original=downloads.source_snapshot
        @contextmanager
        def revoke(path):
            with original(path) as snapshot:
                self.sql("UPDATE study_group_members SET status='left' WHERE student_id=7 AND group_id=1")
                yield snapshot
        self.patched('classroom_app.services.agent_platform_download_service.source_snapshot', side_effect=revoke)
        response=self.download(collaboration_file_id=1)
        self.assertEqual(403,response.status_code,response.text)
        self.assertFalse(self.publications)
        self.assertEqual(0,self.sql("SELECT COUNT(*) FROM agent_request_budget_leases WHERE status='active'")[0][0])

    def test_after_snapshot_source_binding_change_prevents_publication(self):
        original=downloads.source_snapshot
        @contextmanager
        def mutate(path):
            with original(path) as snapshot:
                self.sql("UPDATE course_files SET description='source changed' WHERE id=1")
                yield snapshot
        self.patched('classroom_app.services.agent_platform_download_service.source_snapshot', side_effect=mutate)
        self.assertEqual(409,self.download(course_file_id=1).status_code)
        self.assertFalse(self.publications)

    def test_revocation_at_final_disclosure_prevents_file_and_releases_capacity(self):
        def revoke(*args, **kwargs):
            self.sql("DELETE FROM user_sessions WHERE session_user_key='student:7'")
            return self.publish(*args, **kwargs)
        self.patched('classroom_app.services.agent_platform_download_service.publish_snapshot', side_effect=revoke)
        response=self.download(submission_file_id=1)
        self.assertEqual(401,response.status_code,response.text)
        self.assertNotIn('inputs/',response.text)
        self.assertFalse(list(self.workspaces.rglob('*.fixture')))
        self.assertEqual(0,self.sql("SELECT COUNT(*) FROM agent_request_budget_leases WHERE status='active'")[0][0])
        self.assertTrue(downloads._DOWNLOAD_CAPACITY.acquire(blocking=False))
        downloads._DOWNLOAD_CAPACITY.release()

    def test_revocation_on_replay_preserves_the_previously_authorized_file(self):
        response = self.download(submission_file_id=1)
        self.assertEqual(200, response.status_code, response.text)
        target = self.workspaces/'tasks'/'11'/response.json()['path']
        before = target.read_bytes()
        def revoke(*args, **kwargs):
            self.sql("DELETE FROM user_sessions WHERE session_user_key='student:7'")
            return self.publish(*args, **kwargs)
        self.patched('classroom_app.services.agent_platform_download_service.publish_snapshot', side_effect=revoke)
        self.assertEqual(401,self.download(submission_file_id=1).status_code)
        self.assertEqual(before,target.read_bytes())

    def test_parent_copy_requires_actual_same_actor_ancestry_and_terminal_state(self):
        self.sql('ALTER TABLE agent_tasks ADD COLUMN parent_task_id INTEGER')
        self.sql("INSERT INTO agent_tasks(id,actor_role,actor_id,status) VALUES(19,'student',7,'completed')")
        self.sql('UPDATE agent_tasks SET parent_task_id=19 WHERE id=11')
        source=self.workspaces/'tasks'/'19'/'report.xlsx'
        source.write_bytes(b'\x00\xffprevious owned synthetic workbook')
        good=self.download(path='report.xlsx',parent_task_id=19)
        self.assertEqual(200,good.status_code,good.text)
        self.assertEqual(source.read_bytes(),(self.workspaces/'tasks'/'11'/good.json()['path']).read_bytes())
        self.assertEqual(403,self.download(path='report.xlsx',parent_task_id=10).status_code)
        self.sql("UPDATE agent_tasks SET status='running' WHERE id=19")
        self.assertEqual(409,self.download(path='report.xlsx',parent_task_id=19).status_code)
        for path in ('../19/report.xlsx','.secret','credentials.json','a\\b'):
            self.assertIn(self.download(path=path).status_code,(400,403))

    def test_source_bounds_and_storage_integrity_apply_before_workspace_write(self):
        self.patched('classroom_app.services.agent_platform_download_service.MAX_DOWNLOAD_BYTES',len(self.payload)-1)
        self.assertEqual(413,self.download(submission_file_id=1).status_code)
        self.assertFalse(self.publications)

    def test_source_hash_mismatch_and_outside_stored_path_are_denied(self):
        self.source.write_bytes(b'synthetic storage corruption')
        self.assertEqual(409,self.download(submission_file_id=1).status_code)
        outside=Path(self.path).parent/'private.bin'
        outside.write_bytes(b'\x00private synthetic bytes')
        self.sql('UPDATE submission_files SET stored_path=?,file_hash=NULL WHERE id=1',(str(outside),))
        self.assertEqual(403,self.download(submission_file_id=1).status_code)
        self.assertFalse(self.publications)

    def test_mcp_dispatch_imports_file_but_import_is_not_a_new_generated_artifact(self):
        response=self.client.post('/api/agent-bridge/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':'platform_download','arguments':{'course_file_id':1}}},
            headers={'Authorization':'Bearer '+self.tokens['student']})
        self.assertEqual(200,response.status_code,response.text)
        self.assertFalse(response.json()['result']['isError'],response.text)
        result=json.loads(response.json()['result']['content'][0]['text'])
        self.assertEqual(self.sha,result['sha256'])
        self.patched('classroom_app.services.agent_task_service.AGENT_TASK_WORKSPACE_ROOT',self.workspaces)
        (self.workspaces/'tasks'/'11'/'new-report.md').write_text('Generated synthetic report')
        files=agent_task_service.collect_task_workspace_artifacts(11)
        self.assertEqual(['new-report.md'],[entry['path'] for entry in files])


def load_tests(loader, tests, pattern):
    # Reuse the existing fixture without running its already covered inherited
    # tests a second time in the project-wide discovery count.
    return unittest.TestSuite(AgentPlatformDownloadTests(name) for name,value in AgentPlatformDownloadTests.__dict__.items()
                              if name.startswith('test_') and callable(value))
