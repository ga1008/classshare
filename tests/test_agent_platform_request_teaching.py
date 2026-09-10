"""Teaching setup parity with normal handlers, real transactions and authority."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlencode
import uuid
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.db.schema_session_learning_materials import ensure_session_learning_materials_schema
from classroom_app.routers.manage_parts import classes_courses_classes as classes, classes_courses_courses as courses, classes_courses_offerings as offerings, base_resource_modes
from classroom_app.services import agent_platform_request_registry as registry
from classroom_app.services.agent_platform_multipart_service import form_values
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation, create_persistent_authorization, verify_task_delegation
from tests.test_agent_platform_writes import PlatformWriteFixture
from tests.test_agent_platform_requests import PlatformRequestFixture


class TeachingRequestTests(PlatformWriteFixture):
    patched = PlatformRequestFixture.patched
    sql = PlatformRequestFixture.sql
    web = PlatformRequestFixture.web
    dispatch = PlatformRequestFixture.dispatch

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / 'teaching.sqlite')
        target = sqlite3.connect(self.path); self.conn.backup(target); target.close(); self.conn.close()
        self.connect = sqlite3.connect
        self.conn = self.connect(self.path, check_same_thread=False); self.conn.row_factory = sqlite3.Row; self.addCleanup(self.conn.close)
        ensure_agent_platform_requests_schema(self.conn)
        with patch('classroom_app.db.schema_session_learning_materials._SCHEMA_READY', False):
            ensure_session_learning_materials_schema(self.conn)
        self.conn.executescript("""
          INSERT INTO classes(id,name,created_by_teacher_id) VALUES(31,'Second own class',7),(32,'Private other class',8);
          INSERT INTO courses(id,name,created_by_teacher_id) VALUES(21,'Private other course',8);
          UPDATE courses SET scope_level='private',owner_role='teacher',owner_user_pk=8 WHERE id=21;
          INSERT INTO academic_semesters(id,teacher_id,name,start_date,end_date) VALUES(90,7,'Term','2026-09-01','2027-01-30');
          INSERT INTO textbooks(id,teacher_id,title) VALUES(91,7,'Teaching book');
          INSERT INTO course_lessons(course_id,order_index,title,content,section_count) VALUES(20,1,'First lesson','Learn the basics',2),(20,2,'Second lesson','Practice the basics',2);
          UPDATE courses SET total_hours=4 WHERE id=20;
        """)
        self.tokens = {role: self.token(user, scopes=['platform:read','platform:write']) for role,user in (('teacher', self.teacher), ('student', self.student))}
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.conn.execute("INSERT INTO user_sessions(session_user_key,session_id,user_id,role,expires_at) VALUES('teacher:8','other-session','8','teacher',?)", (expiry,))
        self.conn.execute("INSERT INTO agent_tasks(id,task_uuid,teacher_id,actor_role,actor_id,teacher_name,task_type,title,private_instruction,status) VALUES(12,'other-task',8,'teacher',8,'Other teacher','general','Other task','Test','running')")
        attempt = create_task_attempt(self.conn,task_id=12,worker_id='teaching-fixture',startup_key='other')
        self.tokens['other'] = issue_task_delegation(self.conn,task_id=12,attempt_id=attempt['id'],fencing_token=attempt['fencing_token'],purpose='tools',scopes=['platform:read','platform:write'],source_session_id='other-session')['token']
        create_persistent_authorization(self.conn, actor_role='student',actor_id=7,source_session_id='student-session',scopes=['platform:read'],intent_reference='student-fixture',ttl_seconds=600)
        self.conn.commit()
        self.patched('classroom_app.routers.manage_parts.classes_courses_classes.local_iso',return_value='2026-09-10T12:00:00.000001+08:00')
        for module in ('classes_courses_classes','classes_courses_courses','classes_courses_offerings','base_resource_modes','common'):
            self.patched('classroom_app.routers.manage_parts.'+module+'.get_db_connection',self.connection)
        for name in ('classroom_app.dependencies.get_db_connection','classroom_app.database.get_db_connection','classroom_app.services.agent_platform_request_service.get_db_connection'):
            self.patched(name,self.connection)
        def decode(token,_ip):
            if token not in self.tokens: return None
            role = 'student' if token == 'student' else 'teacher'
            with self.connection() as conn:
                row = conn.execute('SELECT * FROM '+('students' if role == 'student' else 'teachers')+' WHERE id=?',(8 if token == 'other' else 7,)).fetchone()
                return {**dict(row),'role':role}
        self.patched('classroom_app.dependencies.verify_token',side_effect=decode)
        self.app = FastAPI()
        for module in (classes,courses,offerings,base_resource_modes): self.app.include_router(module.router,prefix='/api/manage')
        self.client = TestClient(self.app); self.addCleanup(self.client.close)
        def isolated_connect(database,*args,**kwargs):
            if str(database) not in (self.path, ':memory:'): raise AssertionError('Unexpected SQLite path outside teaching fixture')
            return self.connect(database,*args,**kwargs)
        self.patched('sqlite3.connect',side_effect=isolated_connect)
        for name in ('classroom_app.db.connection.get_db_connection','classroom_app.db.connection.connect_postgres'):
            self.patched(name,side_effect=AssertionError('Unexpected database connector outside teaching fixture'))

    @contextmanager
    def connection(self):
        conn = self.connect(self.path, timeout=10); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try: yield conn
        except Exception: conn.rollback(); raise
        finally: conn.close()

    def parity(self, role, key, **kwargs):
        operation,_ = registry.resolve_capability(self.app,key)
        path,query,body,normalized = registry.arguments(operation,**kwargs)
        content_type = 'application/json'
        if operation.transport == 'form':
            body = urlencode(form_values(normalized['body'])).encode(); content_type = 'application/x-www-form-urlencoded'
        snapshot = self.connect(':memory:')
        with self.connection() as conn: conn.backup(snapshot)
        try:
            normal = self.web(role,operation.method,path+('?' + query.decode() if query else ''),content=body,headers={'content-type':content_type})
            with self.connection() as conn: snapshot.backup(conn)
        finally: snapshot.close()
        result = self.dispatch(role,key,**kwargs)
        self.assertEqual(normal.status_code,result['result']['http_status'],(normal.text,result))
        if normal.status_code == 200: self.assertEqual(normal.json(),result['result']['data'])
        self.assertFalse(result['verified_business'])
        return result

    def assert_status(self, status, result):
        self.assertEqual(status,result['result']['http_status'],result)

    def schedule(self):
        return {'class_id':30,'class_ids':[30,31],'course_id':20,'semester_id':90,'textbook_id':91,
                'first_class_date':'2026-09-10','weekly_schedule':[{'weekday':3,'section_count':2}],'schedule_source':'fixed_cycle'}

    def test_custom_class_and_course_creations_keep_current_owner_and_normal_validation(self):
        created = self.parity('teacher','http.teaching.class.create',body={'class_name':'New custom class','description':'New learning community'})
        self.assert_status(200,created)
        class_id = created['result']['data']['class']['id']
        self.assertEqual((7,'teacher',7),tuple(self.sql('SELECT created_by_teacher_id,owner_role,owner_user_pk FROM classes WHERE id=?',(class_id,))[0]))
        self.assert_status(400,self.parity('teacher','http.teaching.class.create',body={'class_name':'New custom class'}))
        self.assert_status(403,self.parity('student','http.teaching.class.create',body={'class_name':'Student forbidden'}))
        course = self.parity('teacher','http.teaching.course.create',body={'name':'New course','credits':'2.5','description':'Course basics'})
        self.assert_status(200,course)
        self.assertEqual(7,self.sql('SELECT created_by_teacher_id FROM courses WHERE id=?',(course['result']['data']['course_id'],))[0][0])
        self.assert_status(400,self.parity('teacher','http.teaching.course.create',body={'name':'   '}))

    def test_new_course_plan_and_input_rejection_cannot_replace_existing_lessons(self):
        result = self.parity('teacher','http.teaching.course.plan.create',body={'name':'Planned course','total_hours':2,
            'lessons':[{'title':'Lesson','content':'Do practical work','section_count':2}]})
        self.assert_status(200,result)
        self.assertEqual(1,len(self.sql('SELECT id FROM course_lessons WHERE course_id=?',(result['result']['data']['course_id'],))))
        self.assert_status(400,self.parity('teacher','http.teaching.course.plan.create',body={'name':'Bad hours','total_hours':5,
            'lessons':[{'title':'Lesson','content':'Content','section_count':2}]}))
        for key,body in [('http.teaching.course.plan.create',{'name':'Overwrite','course_id':20,'lessons':[]}),
                         ('http.teaching.offering.plan.create',{**self.schedule(),'offering_id':40,'expected_plan_revision':'0'*64})]:
            with self.assertRaises(HTTPException) as caught: self.dispatch('teacher',key,body=body)
            self.assertEqual(400,caught.exception.status_code)
        self.assertEqual(2,len(self.sql('SELECT id FROM course_lessons WHERE course_id=20')))

    def test_student_enrollment_uses_normal_class_permission_and_does_not_duplicate_accounts(self):
        body={'name':'New student','student_id_number':'S100','email':'student@example.test'}
        created=self.parity('teacher','http.teaching.student.create',path_params={'class_id':30},body=body)
        self.assert_status(200,created)
        student_id=created['result']['data']['student']['id']
        self.assertEqual((30,None),tuple(self.sql('SELECT class_id,hashed_password FROM students WHERE id=?',(student_id,))[0]))
        self.assert_status(400,self.parity('teacher','http.teaching.student.create',path_params={'class_id':30},body=body))
        self.assert_status(404,self.parity('other','http.teaching.student.create',path_params={'class_id':30},body={**body,'student_id_number':'S101'}))
        self.assert_status(403,self.parity('student','http.teaching.student.create',path_params={'class_id':30},body={**body,'student_id_number':'S102'}))

    def test_student_suspend_restore_permanently_retires_old_grants_and_stale_versions(self):
        suspended=self.parity('teacher','http.teaching.student.status',path_params={'student_id':7},body={'enrollment_status':'suspended','expected_updated_at':'legacy'})
        self.assert_status(200,suspended)
        version=suspended['result']['data']['student']['enrollment_status_updated_at']
        self.assertEqual(0,len(self.sql("SELECT session_id FROM user_sessions WHERE session_user_key='student:7'")))
        for table in ('agent_task_delegations','agent_persistent_authorizations'):
            self.assertEqual(('revoked','student_enrollment_changed'),tuple(self.sql('SELECT status,revoke_reason FROM '+table+" WHERE actor_role='student' AND actor_id=7")[0]))
        restored=self.parity('teacher','http.teaching.student.status',path_params={'student_id':7},body={'enrollment_status':'active','expected_updated_at':version})
        self.assert_status(200,restored)
        self.assertNotEqual(version,restored['result']['data']['student']['enrollment_status_updated_at'])
        with self.connection() as conn:
            with self.assertRaises(HTTPException): verify_task_delegation(conn,self.tokens['student'],purpose='tools')
            verify_task_delegation(conn,self.tokens['teacher'],purpose='tools')
        stale=self.parity('teacher','http.teaching.student.status',path_params={'student_id':7},body={'enrollment_status':'suspended','expected_updated_at':version})
        self.assert_status(409,stale)
        self.assertEqual('active',self.sql('SELECT enrollment_status FROM students WHERE id=7')[0][0])

    def test_preview_new_schedule_and_read_revision_keep_original_web_plan(self):
        statements=[]
        @contextmanager
        def traced_connection():
            with self.connection() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        guard=self.patched('classroom_app.routers.manage_parts.classes_courses_offerings.get_db_connection',traced_connection)
        preview=self.parity('teacher','http.teaching.offering.preview',body=self.schedule())
        self.assert_status(200,preview)
        self.assertFalse(any(s.lstrip().upper().startswith(('UPDATE ','INSERT ','DELETE ','CREATE ','ALTER ','COMMIT')) for s in statements), statements)
        revision=preview['result']['data']['plan_revision']
        self.assertEqual(0,len(self.sql('SELECT id FROM class_offering_sessions WHERE class_offering_id=40')))
        self.sql("UPDATE course_lessons SET title='Updated source' WHERE course_id=20 AND order_index=1")
        stale=self.parity('teacher','http.teaching.offering.plan.create',body={**self.schedule(),'expected_plan_revision':revision})
        self.assert_status(409,stale)
        self.assertEqual(1,len(self.sql('SELECT id FROM class_offerings')))
        current=self.parity('teacher','http.teaching.offering.preview',body=self.schedule())
        created=self.parity('teacher','http.teaching.offering.plan.create',body={**self.schedule(),'expected_plan_revision':current['result']['data']['plan_revision']})
        self.assert_status(200,created)
        offering_id=created['result']['data']['offering_id']
        self.assertEqual(2,len(self.sql('SELECT id FROM class_offering_sessions WHERE class_offering_id=?',(offering_id,))))
        self.assertEqual([30,31],[r[0] for r in self.sql('SELECT class_id FROM class_offering_class_links WHERE offering_id=? ORDER BY class_id',(offering_id,))])

    def test_basic_classroom_binding_replay_and_foreign_selection_are_normal(self):
        body={'class_id':31,'class_ids':'31,30','course_id':20,'semester_id':90,'textbook_id':91}
        created=self.parity('teacher','http.teaching.offering.create',body=body)
        self.assert_status(200,created)
        repeat=self.dispatch('teacher','http.teaching.offering.create',body=body,operation_id=created['operation_id'])
        self.assertEqual(created,repeat)
        self.assertEqual(2,len(self.sql('SELECT id FROM class_offerings')))
        self.assert_status(403,self.parity('student','http.teaching.offering.create',body=body))
        denied=self.parity('teacher','http.teaching.offering.create',body={**body,'course_id':21})
        self.assertIn(denied['result']['http_status'],(403,404))
        with self.assertRaises(HTTPException) as caught: self.dispatch('teacher','http.teaching.offering.delete',path_params={'offering_id':40})
        self.assertEqual(404,caught.exception.status_code)

    def test_enrollment_commit_failure_rolls_back_status_sessions_and_both_grants(self):
        attempts=[]
        class CommitFailure(sqlite3.Connection):
            def commit(self):
                attempts.append(True)
                raise RuntimeError('Synthetic commit failure')
        @contextmanager
        def failed_connection():
            conn=self.connect(self.path,check_same_thread=False,factory=CommitFailure);conn.row_factory=sqlite3.Row
            try:yield conn
            except Exception:conn.rollback();raise
            finally:conn.close()
        self.patched('classroom_app.routers.manage_parts.classes_courses_classes.get_db_connection',failed_connection)
        with TestClient(self.app,raise_server_exceptions=False) as client:
            client.cookies.set('access_token','teacher')
            response=client.post('/api/manage/students/7/status',data={'enrollment_status':'suspended','expected_updated_at':'legacy'})
        self.assertEqual(500,response.status_code)
        self.assertEqual(1,len(attempts))
        self.assertEqual('active',self.sql('SELECT enrollment_status FROM students WHERE id=7')[0][0])
        self.assertEqual(1,len(self.sql("SELECT session_id FROM user_sessions WHERE session_user_key='student:7'")))
        for table in ('agent_task_delegations','agent_persistent_authorizations'):
            self.assertEqual('active',self.sql('SELECT status FROM '+table+" WHERE actor_role='student' AND actor_id=7")[0][0])

    def test_parallel_normal_status_forms_accept_only_one_of_the_same_revision(self):
        def submit(note):
            with TestClient(self.app) as client:
                client.cookies.set('access_token','teacher')
                return client.post('/api/manage/students/7/status',data={'enrollment_status':'suspended','enrollment_note':note,'expected_updated_at':'legacy'}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(submit,['First change','Second change']))
        self.assertEqual([200,409],sorted(results))
        self.assertEqual('suspended',self.sql('SELECT enrollment_status FROM students WHERE id=7')[0][0])
        self.assertEqual('revoked',self.sql("SELECT status FROM agent_persistent_authorizations WHERE actor_role='student'")[0][0])

    def test_status_permission_is_checked_before_locks_and_again_after_waiting(self):
        from classroom_app.services import account_credentials_service as authority
        original=authority.prepare_credentials_change
        with patch.object(authority,'prepare_credentials_change',side_effect=AssertionError('Foreign actors must not lock this student')):
            self.assert_status(404,self.parity('other','http.teaching.student.status',path_params={'student_id':7},body={'enrollment_status':'suspended','expected_updated_at':'legacy'}))
            self.assert_status(403,self.parity('student','http.teaching.student.status',path_params={'student_id':7},body={'enrollment_status':'suspended','expected_updated_at':'legacy'}))
        def ownership_changed_while_waiting(conn,**kwargs):
            # A deterministic committed-state interleaving, not a parallel-Pg claim.
            conn.execute('UPDATE classes SET created_by_teacher_id=8,owner_role=\'teacher\',owner_user_pk=8 WHERE id=30')
            conn.commit()
            original(conn,**kwargs)
        with patch.object(authority,'prepare_credentials_change',side_effect=ownership_changed_while_waiting):
            response=self.web('teacher','POST','/api/manage/students/7/status',data={'enrollment_status':'suspended','expected_updated_at':'legacy'})
        self.assertEqual(404,response.status_code,response.text)
        self.assertEqual('active',self.sql('SELECT enrollment_status FROM students WHERE id=7')[0][0])
        self.assertEqual('active',self.sql("SELECT status FROM agent_persistent_authorizations WHERE actor_role='student'")[0][0])

    def planned_offering(self):
        body = self.schedule()
        preview = self.web('teacher', 'POST', '/api/manage/class_offerings/preview', json=body)
        self.assertEqual(200, preview.status_code, preview.text)
        created = self.web('teacher', 'POST', '/api/manage/class_offerings/save', json={**body, 'expected_plan_revision': preview.json()['plan_revision']})
        self.assertEqual(200, created.status_code, created.text)
        return {**body, 'offering_id': created.json()['offering_id']}

    def edit_preview(self, body):
        preview = self.parity('teacher', 'http.teaching.offering.preview', body=body)
        self.assert_status(200, preview)
        return preview['result']['data']

    def test_edit_preview_is_owned_and_detects_existing_target_changes(self):
        body = self.planned_offering()
        preview = self.edit_preview(body)
        self.assert_status(404, self.parity('other', 'http.teaching.offering.preview', body=body))
        self.assert_status(403, self.parity('student', 'http.teaching.offering.preview', body=body))
        self.sql("UPDATE class_offering_sessions SET schedule_note='Teacher correction' WHERE class_offering_id=?", (body['offering_id'],))
        self.assert_status(409, self.parity('teacher', 'http.teaching.offering.plan.update', body={**body, 'expected_plan_revision': preview['plan_revision']}))
        current = self.edit_preview(body)
        self.assertNotEqual(preview['plan_revision'], current['plan_revision'])
        self.assert_status(200, self.parity('teacher', 'http.teaching.offering.plan.update', body={**body, 'expected_plan_revision': current['plan_revision']}))
        self.assertEqual(409, self.web('teacher', 'POST', '/api/manage/class_offerings/save', json=body).status_code)

    def test_legacy_primary_only_installation_preview_never_creates_material_binding_table(self):
        body = self.planned_offering()
        self.sql('DROP TABLE class_offering_learning_materials')
        preview = self.edit_preview(body)
        self.assertEqual(0, preview['edit_impact']['protected_session_count'])
        self.assert_status(200,self.parity('teacher','http.teaching.offering.plan.update',body={**body,'expected_plan_revision':preview['plan_revision']}))
        self.assertFalse(self.sql("SELECT name FROM sqlite_master WHERE name='class_offering_learning_materials'"))

    def attach_session_history(self, body, order_index=2):
        offering_id = body['offering_id']
        session_id = self.sql('SELECT id FROM class_offering_sessions WHERE class_offering_id=? AND order_index=?', (offering_id, order_index))[0][0]
        self.sql("INSERT INTO course_materials(id,teacher_id,root_id,name,material_path,node_type,preview_type) VALUES(800,7,800,'Original lesson','old.md','file','markdown')")
        self.sql('INSERT INTO class_offering_learning_materials(class_offering_id,session_id,material_id,sort_order,created_by_teacher_id) VALUES(?,?,800,0,7)', (offering_id, session_id))
        self.sql('INSERT INTO learning_material_progress(class_offering_id,session_id,student_id,material_id,view_count) VALUES(?,?,7,800,3)', (offering_id, session_id))
        self.sql("INSERT INTO session_material_generation_tasks(class_offering_id,session_id,teacher_id,status,generated_material_id,result_payload_json) VALUES(?,?,7,'completed',800,'{\"artifact\":800}')", (offering_id, session_id))
        self.sql("INSERT INTO smart_classroom_checkin_sessions(class_offering_id,session_id,teacher_id,platform_code,remote_checkin_id) VALUES(?,?,7,'fixture','checkin-original')", (offering_id, session_id))
        return session_id

    def test_shorter_plan_cancels_original_id_without_deleting_any_domain_history(self):
        body = self.planned_offering()
        session_id = self.attach_session_history(body)
        self.sql('DELETE FROM course_lessons WHERE course_id=20 AND order_index=2')
        self.sql('UPDATE courses SET total_hours=2 WHERE id=20')
        preview = self.edit_preview(body)
        self.assertEqual(1, preview['edit_impact']['canceled_count'])
        self.assertFalse(preview['edit_impact']['blockers'])
        result = self.parity('teacher', 'http.teaching.offering.plan.update', body={**body, 'expected_plan_revision': preview['plan_revision']})
        self.assert_status(200, result)
        row = self.sql('SELECT schedule_status,title,content FROM class_offering_sessions WHERE id=?', (session_id,))[0]
        self.assertEqual(('cancelled','Second lesson','Practice the basics'), tuple(row))
        for table in ('class_offering_learning_materials','learning_material_progress','session_material_generation_tasks','smart_classroom_checkin_sessions'):
            self.assertEqual(1, len(self.sql('SELECT * FROM '+table+' WHERE session_id=?', (session_id,))))
        self.assertEqual('{"artifact":800}', self.sql('SELECT result_payload_json FROM session_material_generation_tasks WHERE session_id=?', (session_id,))[0][0])

    def test_used_session_content_cannot_be_repurposed_but_dates_can_be_corrected(self):
        body = self.planned_offering()
        session_id = self.attach_session_history(body)
        rescheduled = {**body, 'first_class_date':'2026-09-17'}
        preview = self.edit_preview(rescheduled)
        self.assertFalse(preview['edit_impact']['blockers'])
        self.assert_status(200, self.parity('teacher', 'http.teaching.offering.plan.update', body={**rescheduled, 'expected_plan_revision':preview['plan_revision']}))
        self.assertEqual('2026-09-24', self.sql('SELECT session_date FROM class_offering_sessions WHERE id=?', (session_id,))[0][0])
        self.sql("UPDATE course_lessons SET title='Different lesson' WHERE course_id=20 AND order_index=2")
        changed = self.edit_preview(rescheduled)
        self.assertTrue(changed['edit_impact']['blockers'])
        self.assert_status(409, self.parity('teacher', 'http.teaching.offering.plan.update', body={**rescheduled, 'expected_plan_revision': changed['plan_revision']}))
        self.assertEqual('Second lesson', self.sql('SELECT title FROM class_offering_sessions WHERE id=?',(session_id,))[0][0])
        self.assertEqual(1, len(self.sql('SELECT id FROM learning_material_progress WHERE session_id=?',(session_id,))))
        self.sql("INSERT INTO course_materials(id,teacher_id,root_id,name,material_path,node_type,preview_type) VALUES(801,7,801,'Other material','other.md','file','markdown')")
        self.sql("UPDATE course_lessons SET title='Second lesson',learning_material_id=801 WHERE course_id=20 AND order_index=2")
        material_change = self.edit_preview(rescheduled)
        self.assertTrue(material_change['edit_impact']['blockers'])
        self.assert_status(409, self.parity('teacher', 'http.teaching.offering.plan.update', body={**rescheduled, 'expected_plan_revision':material_change['plan_revision']}))
        self.assertEqual([800], [r[0] for r in self.sql('SELECT material_id FROM class_offering_learning_materials WHERE session_id=?',(session_id,))])

    def test_existing_plan_cannot_silently_move_history_to_different_class_composition(self):
        body = self.planned_offering()
        changed = {**body, 'class_ids':[30]}
        preview = self.edit_preview(changed)
        self.assertTrue(preview['edit_impact']['blockers'])
        self.assert_status(409, self.parity('teacher', 'http.teaching.offering.plan.update', body={**changed, 'expected_plan_revision': preview['plan_revision']}))
        self.assertEqual([30,31], [row[0] for row in self.sql('SELECT class_id FROM class_offering_class_links WHERE offering_id=? ORDER BY class_id',(body['offering_id'],))])
        # Switching the primary label within the same member set is harmless
        # and retains the original Web operation.
        primary = {**body, 'class_id':31, 'class_ids':[31,30]}
        preview = self.edit_preview(primary)
        self.assertFalse(preview['edit_impact']['blockers'])
        self.assert_status(200, self.parity('teacher','http.teaching.offering.plan.update',body={**primary,'expected_plan_revision':preview['plan_revision']}))
        self.assertEqual(31, self.sql('SELECT class_id FROM class_offerings WHERE id=?',(body['offering_id'],))[0][0])

    def test_parallel_plan_edits_accept_exactly_one_original_snapshot(self):
        body = self.planned_offering()
        changed = {**body, 'first_class_date':'2026-09-17'}
        payload = {**changed, 'expected_plan_revision': self.edit_preview(changed)['plan_revision']}
        def save(_):
            with TestClient(self.app) as client:
                client.cookies.set('access_token','teacher')
                return client.post('/api/manage/class_offerings/save', json=payload).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([200,409], sorted(pool.map(save, range(2))))
        self.assertEqual(2, len(self.sql('SELECT id FROM class_offering_sessions WHERE class_offering_id=?',(body['offering_id'],))))

    def test_plan_commit_failure_preserves_classroom_sessions_and_old_revision(self):
        body = self.planned_offering()
        changed = {**body, 'first_class_date':'2026-09-17'}
        payload = {**changed, 'expected_plan_revision':self.edit_preview(changed)['plan_revision']}
        before = [dict(row) for row in self.sql('SELECT * FROM class_offering_sessions WHERE class_offering_id=?',(body['offering_id'],))]
        class CommitFailure(sqlite3.Connection):
            def commit(self): raise RuntimeError('Synthetic plan commit failure')
        @contextmanager
        def failed_connection():
            conn=self.connect(self.path, factory=CommitFailure); conn.row_factory=sqlite3.Row
            try: yield conn
            except Exception: conn.rollback(); raise
            finally: conn.close()
        with patch.object(offerings, 'get_db_connection', failed_connection):
            self.assertEqual(500, self.web('teacher','POST','/api/manage/class_offerings/save',json=payload).status_code)
        self.assertEqual(before, [dict(row) for row in self.sql('SELECT * FROM class_offering_sessions WHERE class_offering_id=?',(body['offering_id'],))])
        self.assertEqual(payload['expected_plan_revision'], self.edit_preview(changed)['plan_revision'])

    def test_waiting_for_plan_lock_does_not_block_the_web_event_loop(self):
        from threading import Event
        import httpx
        body = self.planned_offering()
        payload = {**body, 'expected_plan_revision':self.edit_preview(body)['plan_revision']}
        waiting, release = Event(), Event()
        original = offerings.lock_plan_row
        def delayed_lock(conn, table, row_id):
            if table == 'courses':
                waiting.set()
                if not release.wait(3): raise RuntimeError('Isolated lock wait timeout')
            return original(conn, table, row_id)
        @self.app.get('/fixture/ping')
        async def ping(): return {'ok': True}
        async def probe():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://isolated',cookies={'access_token':'teacher'}) as client:
                task=asyncio.create_task(client.post('/api/manage/class_offerings/save',json=payload))
                try:
                    for _ in range(100):
                        if waiting.is_set(): break
                        await asyncio.sleep(0.01)
                    self.assertTrue(waiting.is_set())
                    response=await asyncio.wait_for(client.get('/fixture/ping'),timeout=0.5)
                    self.assertEqual({'ok':True},response.json())
                finally: release.set()
                self.assertEqual(200,(await task).status_code)
        with patch.object(offerings,'lock_plan_row',side_effect=delayed_lock): asyncio.run(probe())
