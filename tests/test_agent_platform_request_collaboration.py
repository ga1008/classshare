"""Actual normal HTTP/SQL parity for roster, private work and group chat."""
from datetime import datetime

from fastapi import HTTPException

from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests.test_agent_platform_request_learning import LearningRequestFixture


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 9, 10, 12, 0, 0)
        return value.replace(tzinfo=tz) if tz else value


class AgentPlatformRequestCollaborationTests(LearningRequestFixture):
    def setUp(self):
        super().setUp()
        self.patched('classroom_app.services.collaboration_service._now_iso', return_value='2026-09-10T12:00:00')
        self.patched('classroom_app.services.collaboration_service.datetime', FixedClock)
        with self.connection() as conn:
            conn.executescript("""
              ALTER TABLE study_groups ADD COLUMN archived_at TEXT;
              ALTER TABLE study_group_members ADD COLUMN contribution_summary TEXT DEFAULT '';
              ALTER TABLE study_group_members ADD COLUMN contribution_score REAL;
              CREATE TABLE study_group_files(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER,file_hash TEXT,original_filename TEXT,mime_type TEXT,file_size INTEGER,
                description TEXT,uploaded_by_name TEXT,uploaded_by_role TEXT,uploaded_by_user_pk INTEGER,created_at TEXT);
              CREATE TABLE group_submissions(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER,assignment_id TEXT,submitted_by_role TEXT,submitted_by_user_pk INTEGER,
                title TEXT,summary_md TEXT,final_file_id INTEGER,status TEXT,submitted_at TEXT,updated_at TEXT,blog_post_id INTEGER);
              CREATE TABLE peer_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,class_offering_id INTEGER,group_id INTEGER,assignment_id TEXT,reviewer_student_id INTEGER,
                reviewee_student_id INTEGER,responsibility_score INTEGER,collaboration_score INTEGER,quality_score INTEGER,comment TEXT,share_with_reviewee INTEGER,
                status TEXT,created_at TEXT,updated_at TEXT);
              INSERT INTO students(id,name,class_id,enrollment_status,school_code,school_name,college,department,student_id_number)
                VALUES(9,'Third student',30,'active','A','School A','C','D','S009');
              INSERT INTO agent_tasks VALUES(14,'student',9,NULL,'running',NULL);
              INSERT INTO user_sessions SELECT 'student:9','third-session','9','student',expires_at FROM user_sessions WHERE session_user_key='student:7';
            """)
            attempt = create_task_attempt(conn, task_id=14, worker_id='collaboration-fixture', startup_key='third', lease_seconds=300)
            self.tokens['third'] = issue_task_delegation(conn, task_id=14, attempt_id=attempt['id'], fencing_token=attempt['fencing_token'],
                purpose='tools', scopes=['platform:read', 'platform:write'], source_session_id='third-session')['token']
            conn.commit()
        def decode(token, _ip):
            if token not in self.tokens:
                return None
            role = 'teacher' if token in ('teacher', 'other') else 'student'
            pk = 9 if token == 'third' else (8 if token in ('other', 'peer') else 7)
            with self.connection() as conn:
                row = conn.execute('SELECT * FROM ' + ('teachers' if role == 'teacher' else 'students') + ' WHERE id=?', (pk,)).fetchone()
                return {**dict(row), 'role': role}
        self.patched('classroom_app.dependencies.verify_token', side_effect=decode)

    def create(self, members=(7,), cap=3, **extra):
        result = self.restored_parity('teacher', 'http.collaboration.group.create', path_params={'class_offering_id': 1},
            body={'name': 'Synthetic learning group', 'description': 'Keep this description', 'join_policy': 'open',
                'leader_student_id': 7, 'max_members': cap, 'member_student_ids': list(members), **extra})
        self.assertEqual(200, result['result']['http_status'])
        return result['result']['data']['group']['id']

    def operation(self, actor, action, group_id, **kwargs):
        return self.restored_parity(actor, 'http.collaboration.' + action, path_params={'group_id': group_id}, **kwargs)

    def test_group_create_join_manage_remove_add_leave_and_archive_normal_permissions(self):
        group_id = self.create(cap=2)
        for actor in ('peer', 'other'):
            result = self.operation(actor, 'group.update', group_id, body={'name': 'Forbidden edit'})
            self.assertEqual(403, result['result']['http_status'])
        self.operation('peer', 'group.join', group_id)
        denied = self.operation('peer', 'goal.update', group_id, body={'goal_text': 'Not leader'})
        self.assertEqual(403, denied['result']['http_status'])
        self.operation('student', 'goal.update', group_id, body={'goal_text': 'Learn together\nThen review', 'progress_percent': 75})
        self.assertEqual(75, self.sql('SELECT progress_percent FROM study_groups WHERE id=?', (group_id,))[0][0])
        self.operation('teacher', 'group.update', group_id, body={'name': 'Renamed'})
        self.assertEqual('Keep this description', self.sql('SELECT description FROM study_groups WHERE id=?', (group_id,))[0][0])
        denied = self.restored_parity('student', 'http.collaboration.member.remove', path_params={'group_id': group_id, 'student_id': 8})
        self.assertEqual(403, denied['result']['http_status'])
        self.restored_parity('teacher', 'http.collaboration.member.remove', path_params={'group_id': group_id, 'student_id': 8})
        self.operation('teacher', 'member.add', group_id, body={'student_id': 8})
        self.operation('student', 'group.leave', group_id)
        self.assertEqual(8, self.sql('SELECT leader_student_id FROM study_groups WHERE id=?', (group_id,))[0][0])
        self.operation('peer', 'group.leave', group_id)
        self.assertEqual('archived', self.sql('SELECT status FROM study_groups WHERE id=?', (group_id,))[0][0])
        denied = self.operation('third', 'group.join', group_id)
        self.assertEqual(400, denied['result']['http_status'])
        denied = self.restored_parity('student', 'http.collaboration.group.create', path_params={'class_offering_id': 2}, body={'name': 'Foreign classroom'})
        self.assertEqual(403, denied['result']['http_status'])

    def test_teacher_cannot_overfill_by_add_leader_or_initial_assignment_and_lower_bound(self):
        group_id = self.create(members=(7, 8), cap=2)
        for key, payload in (('member.add', {'student_id': 9}), ('group.update', {'leader_student_id': 9})):
            denied = self.operation('teacher', key, group_id, body=payload)
            self.assertEqual(400, denied['result']['http_status'])
        denied = self.restored_parity('teacher', 'http.collaboration.group.create', path_params={'class_offering_id': 1},
            body={'name': 'Overfull', 'max_members': 2, 'member_student_ids': [7, 8, 9]})
        self.assertEqual(400, denied['result']['http_status'])
        self.operation('teacher', 'group.update', group_id, body={'max_members': 3})
        self.operation('third', 'group.join', group_id)
        denied = self.operation('teacher', 'group.update', group_id, body={'max_members': 2})
        self.assertEqual(400, denied['result']['http_status'])
        self.assertEqual(3, self.sql('SELECT max_members FROM study_groups WHERE id=?', (group_id,))[0][0])

    def test_group_submission_requires_leader_complete_payload_and_group_scoped_file(self):
        group_id = self.create(members=(7, 8))
        payload = {'title': 'Our result', 'summary_md': 'Step one\nStep two', 'final_file_id': None}
        denied = self.operation('peer', 'submission.save', group_id, body=payload)
        self.assertEqual(403, denied['result']['http_status'])
        saved = self.operation('student', 'submission.save', group_id, body=payload)
        self.assertEqual(200, saved['result']['http_status'])
        self.assertFalse(saved['verified_business'])
        with self.assertRaises(HTTPException) as caught:
            self.dispatch('student', 'http.collaboration.submission.save', path_params={'group_id': group_id}, body={'title': 'Do not erase prior fields'})
        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual('Step one\nStep two', self.sql('SELECT summary_md FROM group_submissions')[0][0])
        self.sql("INSERT INTO study_groups(id,class_offering_id,name,status,join_policy,max_members) VALUES(20,2,'Other private group','active','locked',3)")
        self.sql("INSERT INTO study_group_files(id,group_id,original_filename) VALUES(20,20,'Other private file.docx')")
        denied = self.operation('student', 'submission.save', group_id, body={**payload, 'final_file_id': 20})
        self.assertEqual(400, denied['result']['http_status'])
        outsider = self.restored_parity('third', 'http.collaboration.snapshot', path_params={'class_offering_id': 1})
        self.assertEqual([], outsider['result']['data']['snapshot']['groups'][0]['submissions'])

    def test_peer_review_private_then_explicit_shared_only_to_reviewee(self):
        group_id = self.create(members=(7, 8, 9))
        payload = {'reviewee_student_id': 8, 'responsibility_score': 5, 'collaboration_score': 4, 'quality_score': 5,
            'comment': 'Private feedback', 'share_with_reviewee': False}
        self.operation('student', 'peer_review.save', group_id, body=payload)
        for actor in ('student', 'peer', 'third', 'teacher'):
            snapshot = self.restored_parity(actor, 'http.collaboration.snapshot', path_params={'class_offering_id': 1})['result']['data']['snapshot']
            reviews = snapshot['groups'][0]['peer_reviews']
            self.assertEqual(actor in ('student', 'teacher'), bool(reviews))
        self.operation('student', 'peer_review.save', group_id, body={**payload, 'share_with_reviewee': True})
        peer = self.restored_parity('peer', 'http.collaboration.snapshot', path_params={'class_offering_id': 1})['result']['data']['snapshot']
        self.assertEqual('Private feedback', peer['groups'][0]['peer_reviews'][0]['comment'])
        third = self.restored_parity('third', 'http.collaboration.snapshot', path_params={'class_offering_id': 1})['result']['data']['snapshot']
        self.assertEqual([], third['groups'][0]['peer_reviews'])
        denied = self.operation('teacher', 'peer_review.save', group_id, body=payload)
        self.assertEqual(403, denied['result']['http_status'])
        denied = self.operation('student', 'peer_review.save', group_id, body={**payload, 'reviewee_student_id': 7})
        self.assertEqual(400, denied['result']['http_status'])

    def test_private_group_chat_read_send_recall_member_role_and_time_bounds(self):
        group_id = self.create(members=(7,))
        for action, kwargs in (('chat.read', {}), ('chat.send', {'body': {'content': 'Not a member'}})):
            denied = self.operation('peer', action, group_id, **kwargs)
            self.assertEqual(403, denied['result']['http_status'])
        sent = self.operation('student', 'chat.send', group_id, body={'content': 'Private group text\nSecond line'})
        message_id = sent['result']['data']['message']['id']
        read = self.operation('teacher', 'chat.read', group_id, query_params={'after_id': 0})
        self.assertEqual('Private group text\nSecond line', read['result']['data']['messages'][0]['content'])
        self.operation('teacher', 'member.add', group_id, body={'student_id': 8})
        for actor in ('peer', 'teacher'):
            denied = self.restored_parity(actor, 'http.collaboration.chat.recall', path_params={'group_id': group_id, 'message_id': message_id})
            self.assertEqual(403, denied['result']['http_status'])
        self.restored_parity('student', 'http.collaboration.chat.recall', path_params={'group_id': group_id, 'message_id': message_id})
        self.assertEqual(('recalled', ''), tuple(self.sql('SELECT message_type,content FROM group_chat_messages WHERE id=?', (message_id,))[0]))
        sent = self.operation('peer', 'chat.send', group_id, body={'content': 'Expired recall'})
        second = sent['result']['data']['message']['id']
        self.sql("UPDATE group_chat_messages SET created_at='2026-09-10T11:58:00' WHERE id=?", (second,))
        denied = self.restored_parity('peer', 'http.collaboration.chat.recall', path_params={'group_id': group_id, 'message_id': second})
        self.assertEqual(400, denied['result']['http_status'])
