"""Normal poll management plus student/class/owner SQL authorization parity."""
from fastapi import HTTPException

from tests.test_agent_platform_requests import PlatformRequestFixture


class AgentPlatformRequestPollTests(PlatformRequestFixture):
    def setUp(self):
        super().setUp()
        self.patched('classroom_app.services.poll_service._now_iso',return_value='2026-09-10T10:00:00')
        self.sql("ALTER TABLE students ADD COLUMN avatar_file_hash TEXT DEFAULT ''")
        self.sql("ALTER TABLE message_center_notifications ADD COLUMN email_status TEXT DEFAULT 'not_required'")
        for field in ('email_job_id','email_queued_at','email_sent_at'):
            self.sql(f'ALTER TABLE message_center_notifications ADD COLUMN {field} TEXT')

    @staticmethod
    def fields(**updates):
        return {'title':'Managed fixture poll','description':'Fixture description','vote_type':'single',
                'deadline_at':None,'allow_change':False,'max_changes':0,'result_visibility':'after_close',**updates}

    def test_management_reads_and_creation_match_teacher_admin_and_student_permissions(self):
        for role in ('teacher','other','student'):
            self.restored_parity(role,'http.polls.manage.list')
            self.restored_parity(role,'http.polls.manage.offerings')
            self.restored_parity(role,'http.polls.candidates',path_params={'class_offering_id':1})
        body={**self.fields(),'options':['One','Two'],'status':'draft','class_offering_ids':[1,2]}
        created=self.restored_parity('teacher','http.polls.manage.create',body=body)
        self.assertEqual(200,created['result']['http_status'])
        poll_id=created['result']['data']['poll']['id']
        self.assertEqual({1,2},{row[0] for row in self.sql('SELECT class_offering_id FROM poll_assignments WHERE poll_id=?',(poll_id,))})
        denied=self.restored_parity('other','http.polls.manage.create',body=body)
        self.assertEqual(403,denied['result']['http_status'])
        denied=self.restored_parity('student','http.polls.manage.create',body=body)
        self.assertEqual(400,denied['result']['http_status'])

    def test_management_create_assign_open_vote_close_result_and_delete_is_complete(self):
        created=self.restored_parity('teacher','http.polls.manage.create',body={**self.fields(),'options':['One','Two'],'status':'draft'})
        poll_id=created['result']['data']['poll']['id']
        self.restored_parity('teacher','http.polls.assignments',path_params={'poll_id':poll_id},body={'class_offering_ids':[1]})
        updated=self.restored_parity('teacher','http.polls.update',path_params={'poll_id':poll_id},body=self.fields(title='Final question'))
        self.assertEqual(200,updated['result']['http_status'])
        self.restored_parity('teacher','http.polls.status',path_params={'poll_id':poll_id},body={'status':'active'})
        visible=self.restored_parity('student','http.polls.detail',path_params={'poll_id':poll_id})
        self.assertFalse(visible['result']['data']['poll']['show_results'])
        option_id=self.sql('SELECT id FROM poll_options WHERE poll_id=? ORDER BY id',(poll_id,))[0][0]
        vote=self.restored_parity('student','http.polls.vote',path_params={'poll_id':poll_id},body={'option_ids':[option_id]})
        self.assertEqual(200,vote['result']['http_status'])
        for role in ('student','other'):
            denied=self.restored_parity(role,'http.polls.status',path_params={'poll_id':poll_id},body={'status':'closed'})
            self.assertEqual(403,denied['result']['http_status'])
        self.restored_parity('teacher','http.polls.status',path_params={'poll_id':poll_id},body={'status':'closed'})
        result=self.restored_parity('student','http.polls.detail',path_params={'poll_id':poll_id})
        self.assertTrue(result['result']['data']['poll']['show_results'])
        self.assertEqual(1,result['result']['data']['poll']['total_voters'])
        self.restored_parity('teacher','http.polls.delete',path_params={'poll_id':poll_id})
        for table in ('polls','poll_assignments','poll_options','poll_ballots','poll_votes','poll_participants'):
            self.assertEqual([],self.sql(f'SELECT id FROM {table} WHERE '+('id' if table=='polls' else 'poll_id')+'=?',(poll_id,)))

    def test_student_classroom_create_requires_eligible_peers_and_owner_can_manage(self):
        body={**self.fields(),'options':['One','Two'],'participant_ids':[8],'status':'draft'}
        created=self.restored_parity('student','http.polls.classroom.create',path_params={'class_offering_id':1},body=body)
        self.assertEqual(200,created['result']['http_status'])
        poll_id=created['result']['data']['poll']['id']
        self.assertEqual({7,8},{row[0] for row in self.sql('SELECT student_id FROM poll_participants WHERE poll_id=?',(poll_id,))})
        self.restored_parity('student','http.polls.status',path_params={'poll_id':poll_id},body={'status':'active'})
        denied=self.restored_parity('student','http.polls.classroom.create',path_params={'class_offering_id':2},body=body)
        self.assertEqual(403,denied['result']['http_status'])
        denied=self.restored_parity('student','http.polls.classroom.create',path_params={'class_offering_id':1},body={**body,'participant_ids':[]})
        self.assertEqual(400,denied['result']['http_status'])

    def test_full_put_and_typed_options_reject_implicit_policy_reset_before_admission(self):
        for body in ({'title':'Only title'}, {**self.fields(),'options':['One',{'label':'not typed'}]},
                     {**self.fields(),'options':['One','Two\x00']}):
            with self.assertRaises(HTTPException) as denied:
                self.dispatch('teacher','http.polls.update',path_params={'poll_id':1},body=body)
            self.assertEqual(400,denied.exception.status_code)
        self.assertEqual([],self.sql('SELECT id FROM agent_platform_requests'))
