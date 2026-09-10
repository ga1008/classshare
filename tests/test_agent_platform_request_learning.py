"""Todo and student invitation parity through actual routers, services and SQL."""
from datetime import datetime, timezone, timedelta
import json
import sqlite3
from unittest.mock import patch
import uuid

from classroom_app.db import schema_scheduler, schema_study_group_scheme
from classroom_app.routers import learning, collaboration
from classroom_app.services import agent_platform_request_registry as registry
from classroom_app.services.agent_platform_request_learning import build_capabilities
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from classroom_app.services import academic_course_exam_sync_service
from tests.test_agent_platform_requests import PlatformRequestFixture


class LearningRequestFixture(PlatformRequestFixture):
    def setUp(self):
        super().setUp()
        self.patched('classroom_app.routers.learning.get_db_connection',self.connection)
        self.patched('classroom_app.routers.collaboration.get_db_connection',self.connection)
        self.patched('classroom_app.db.schema_scheduler._SCHEMA_READY',False)
        self.patched('classroom_app.db.schema_study_group_scheme._SCHEMA_READY',False)
        for target in ('classroom_app.db.schema_scheduler.get_configured_db_engine',
                       'classroom_app.db.schema_study_group_scheme.get_configured_db_engine',
                       'classroom_app.services.academic_course_exam_sync_service.get_configured_db_engine'):
            self.patched(target,return_value='sqlite')
        fixed=datetime(2026,9,10,12,0,tzinfo=timezone(timedelta(hours=8)))
        self.patched('classroom_app.services.todo_service.china_now',return_value=fixed)
        self.patched('classroom_app.services.collaboration_service._now_iso',return_value=fixed.isoformat())
        with self.connection() as conn:
            conn.executescript("""
              ALTER TABLE students ADD COLUMN avatar_file_hash TEXT DEFAULT '';
              ALTER TABLE class_offerings ADD COLUMN semester_id INTEGER;
              ALTER TABLE class_offerings ADD COLUMN semester TEXT DEFAULT '';
              CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,start_date TEXT,end_date TEXT);
              CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,order_index INTEGER,title TEXT,session_date TEXT,weekday INTEGER,week_index INTEGER);
              CREATE TABLE assignments(id INTEGER PRIMARY KEY,class_offering_id INTEGER,title TEXT,status TEXT,exam_paper_id INTEGER,due_at TEXT,starts_at TEXT,created_at TEXT);
              CREATE TABLE exam_papers(id INTEGER PRIMARY KEY,title TEXT);
              CREATE TABLE submissions(id INTEGER PRIMARY KEY,assignment_id INTEGER,student_pk_id INTEGER,status TEXT,score REAL,resubmission_allowed INTEGER,resubmission_due_at TEXT,is_absence_score INTEGER);
              CREATE TABLE learning_stage_exam_attempts(id INTEGER PRIMARY KEY,assignment_id INTEGER,exam_paper_id INTEGER,class_offering_id INTEGER,student_id INTEGER,status TEXT,generated_at TEXT);
              CREATE TABLE classroom_todos(id INTEGER PRIMARY KEY AUTOINCREMENT,class_offering_id INTEGER,owner_role TEXT NOT NULL,owner_user_pk INTEGER NOT NULL,
                title TEXT NOT NULL,notes TEXT DEFAULT '',start_at TEXT,due_at TEXT,completed_at TEXT,created_at TEXT,updated_at TEXT,deleted_at TEXT,metadata_json TEXT DEFAULT '{}');
              CREATE TABLE study_groups(id INTEGER PRIMARY KEY AUTOINCREMENT,class_offering_id INTEGER,assignment_id TEXT,name TEXT,description TEXT,status TEXT,join_policy TEXT,
                max_members INTEGER,leader_student_id INTEGER,created_by_role TEXT,created_by_user_pk INTEGER,created_at TEXT,updated_at TEXT);
              CREATE TABLE study_group_members(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER,student_id INTEGER,member_role TEXT,status TEXT,added_by_role TEXT,added_by_user_pk INTEGER,
                joined_at TEXT,updated_at TEXT,left_at TEXT,UNIQUE(group_id,student_id));
              ALTER TABLE message_center_notifications ADD COLUMN email_status TEXT DEFAULT 'not_required';
              ALTER TABLE message_center_notifications ADD COLUMN email_job_id INTEGER;
              ALTER TABLE message_center_notifications ADD COLUMN email_queued_at TEXT;
              ALTER TABLE message_center_notifications ADD COLUMN email_sent_at TEXT;
            """)
            schema_scheduler.ensure_scheduler_schema(conn)
            schema_study_group_scheme.ensure_study_group_scheme_schema(conn)
            academic_course_exam_sync_service.ensure_course_exam_schema(conn)
            conn.execute("INSERT INTO agent_tasks VALUES(13,'student',8,NULL,'running',NULL)")
            conn.execute("INSERT INTO user_sessions SELECT 'student:8','peer-session','8','student',expires_at FROM user_sessions WHERE session_user_key='student:7'")
            attempt=create_task_attempt(conn,task_id=13,worker_id='learning-fixture',startup_key='peer',lease_seconds=300)
            self.tokens['peer']=issue_task_delegation(conn,task_id=13,attempt_id=attempt['id'],fencing_token=attempt['fencing_token'],
                purpose='tools',scopes=['platform:read','platform:write'],source_session_id='peer-session')['token']
            conn.commit()
        def decode(token,_ip):
            if token not in self.tokens:return None
            role='teacher' if token in ('teacher','other') else 'student'
            with self.connection() as conn:
                row=conn.execute('SELECT * FROM '+('teachers' if role=='teacher' else 'students')+' WHERE id=?',(8 if token in ('other','peer') else 7,)).fetchone()
                return {**dict(row),'role':role}
        self.patched('classroom_app.dependencies.verify_token',side_effect=decode)
        for module in (learning,collaboration):self.app.include_router(module.router)
        additions=build_capabilities(registry.RequestCapability,registry._spec,registry.ID)
        if not any(item.key==additions[0].key for item in registry.CAPABILITIES):
            self.patched('classroom_app.services.agent_platform_request_registry.CAPABILITIES',registry.CAPABILITIES+additions)


class AgentPlatformRequestLearningTests(LearningRequestFixture):
    def test_account_todo_teacher_ownership_student_denial_and_complete_delete(self):
        created=self.restored_parity('teacher','http.todos.account.create',body={'title':'Private planning'})
        self.assertEqual(200,created['result']['http_status'])
        todo_id=created['result']['data']['id']
        for role in ('other','student'):
            denied=self.restored_parity(role,'http.todos.account.update',path_params={'todo_id':todo_id},body={'completed':True})
            self.assertIn(denied['result']['http_status'],(403,404))
        done=self.restored_parity('teacher','http.todos.account.update',path_params={'todo_id':todo_id},body={'completed':True,'priority':'high'})
        self.assertEqual(200,done['result']['http_status'])
        self.assertIsNotNone(self.sql('SELECT completed_at FROM classroom_todos WHERE id=?',(todo_id,))[0][0])
        self.restored_parity('teacher','http.todos.account.delete',path_params={'todo_id':todo_id})
        self.assertIsNotNone(self.sql('SELECT deleted_at FROM classroom_todos WHERE id=?',(todo_id,))[0][0])
        denied=self.restored_parity('student','http.todos.account.create',body={'title':'Student cannot create account todo'})
        self.assertEqual(403,denied['result']['http_status'])

    def test_classroom_todo_create_read_update_delete_and_foreign_class_are_normal(self):
        created=self.restored_parity('student','http.todos.classroom.create',path_params={'class_offering_id':1},body={'title':'Student task','notes':'Read lesson','priority':'high'})
        self.assertEqual(200,created['result']['http_status'])
        todo_id=created['result']['data']['id']
        for role in ('teacher','student','peer'):
            read=self.restored_parity(role,'http.todos.classroom.list',path_params={'class_offering_id':1})
            self.assertEqual(200,read['result']['http_status'])
            titles=[item['title'] for item in read['result']['data']['todo_overview']['items']]
            self.assertEqual(role=='student','Student task' in titles)
        for role in ('teacher','peer'):
            denied=self.restored_parity(role,'http.todos.classroom.update',path_params={'class_offering_id':1,'todo_id':todo_id},body={'completed':True})
            self.assertEqual(404,denied['result']['http_status'])
        self.restored_parity('student','http.todos.classroom.update',path_params={'class_offering_id':1,'todo_id':todo_id},body={'completed':True})
        self.restored_parity('student','http.todos.classroom.delete',path_params={'class_offering_id':1,'todo_id':todo_id})
        denied=self.restored_parity('student','http.todos.classroom.list',path_params={'class_offering_id':2})
        self.assertEqual(403,denied['result']['http_status'])

    def test_student_group_create_snapshot_candidates_and_recipient_accept(self):
        created=self.restored_parity('student','http.collaboration.student_group.create',path_params={'class_offering_id':1},body={'name':'Fixture invite group','invitee_student_ids':[8]})
        self.assertEqual(200,created['result']['http_status'])
        group_id=created['result']['data']['group']['id']
        invitation_id=self.sql('SELECT id FROM group_invitations WHERE group_id=?',(group_id,))[0][0]
        for role in ('student','peer','teacher'):
            self.restored_parity(role,'http.collaboration.snapshot',path_params={'class_offering_id':1})
            self.restored_parity(role,'http.collaboration.invite_candidates',path_params={'class_offering_id':1})
        for role in ('student','teacher'):
            denied=self.restored_parity(role,'http.collaboration.invitation.accept',path_params={'invitation_id':invitation_id})
            self.assertEqual(403,denied['result']['http_status'])
        accepted=self.restored_parity('peer','http.collaboration.invitation.accept',path_params={'invitation_id':invitation_id})
        self.assertEqual(200,accepted['result']['http_status'])
        self.assertEqual('accepted',self.sql('SELECT status FROM group_invitations WHERE id=?',(invitation_id,))[0][0])
        self.assertEqual({7,8},{row[0] for row in self.sql("SELECT student_id FROM study_group_members WHERE group_id=? AND status='active'",(group_id,))})

    def test_student_group_invite_leader_class_checks_and_recipient_decline(self):
        created=self.restored_parity('student','http.collaboration.student_group.create',path_params={'class_offering_id':1},body={'name':'Empty invite group'})
        group_id=created['result']['data']['group']['id']
        denied=self.restored_parity('peer','http.collaboration.group.invite',path_params={'group_id':group_id},body={'invitee_student_ids':[7]})
        self.assertEqual(403,denied['result']['http_status'])
        invited=self.restored_parity('student','http.collaboration.group.invite',path_params={'group_id':group_id},body={'invitee_student_ids':[8]})
        self.assertEqual(200,invited['result']['http_status'])
        invitation_id=self.sql('SELECT id FROM group_invitations WHERE group_id=?',(group_id,))[0][0]
        declined=self.restored_parity('peer','http.collaboration.invitation.decline',path_params={'invitation_id':invitation_id})
        self.assertEqual(200,declined['result']['http_status'])
        self.assertEqual('declined',self.sql('SELECT status FROM group_invitations WHERE id=?',(invitation_id,))[0][0])
        self.assertEqual([7],[row[0] for row in self.sql("SELECT student_id FROM study_group_members WHERE group_id=? AND status='active'",(group_id,))])
        denied=self.restored_parity('student','http.collaboration.student_group.create',path_params={'class_offering_id':2},body={'name':'Foreign classroom'})
        self.assertEqual(403,denied['result']['http_status'])

    def test_todo_reminder_is_persisted_then_completion_cancels_it_and_nullable_fields_clear(self):
        created=self.restored_parity('teacher','http.todos.account.create',body={'title':'Timed planning','notes':'First line\nSecond line',
            'class_offering_id':1,'start_at':'2026-10-01T08:00','due_at':'2026-10-02T08:00',
            'reminder_enabled':True,'reminder_lead_minutes':60,'email_reminder_enabled':False})
        self.assertEqual(200,created['result']['http_status'])
        todo_id=created['result']['data']['id']
        scheduled=self.sql("SELECT owner_role,owner_user_pk,status FROM scheduled_tasks WHERE task_kind='todo_due_reminder'")
        self.assertEqual(1,len(scheduled))
        self.assertEqual(('teacher',7,'pending'),tuple(scheduled[0]))
        self.restored_parity('teacher','http.todos.account.update',path_params={'todo_id':todo_id},body={'completed':True,'notes':'',
            'start_at':None,'due_at':None,'class_offering_id':None})
        row=self.sql('SELECT notes,start_at,due_at,class_offering_id FROM classroom_todos WHERE id=?',(todo_id,))[0]
        self.assertEqual(('',None,None,None),tuple(row))
        self.assertEqual('cancelled',self.sql("SELECT status FROM scheduled_tasks WHERE task_kind='todo_due_reminder'")[0][0])
