import asyncio
from datetime import datetime, timedelta
import json
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.test_agent_platform_writes import PlatformWriteFixture, Request
from classroom_app import dependencies
from classroom_app.db import schema_ai_jobs, schema_study_group_scheme
from classroom_app.routers.homework_parts import assignments
from classroom_app.routers import learning, smart_classroom, report_card
from classroom_app.routers.materials_parts import final_materials
from classroom_app.services import agent_platform_broker as broker, agent_platform_write_service as writes
from classroom_app.services.assignment_creation_service import create_assignment_record
from classroom_app.services.assignment_management_service import load_assignment_row, assignment_revision
from classroom_app.services.assignment_read_service import get_assignment_details, list_classroom_assignments


class AssignmentAgentTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        with patch.object(schema_ai_jobs, "_SCHEMA_READY_ENGINES", set()), patch.object(schema_study_group_scheme, "_SCHEMA_READY", False):
            schema_ai_jobs.ensure_ai_job_schema(self.conn, engine="sqlite")
            schema_study_group_scheme.ensure_study_group_scheme_schema(self.conn)
        self.assignment_id = create_assignment_record(self.conn, teacher_id=7, course_id=20,
            data={"title": "Draft assignment", "class_offering_id": 40, "requirements_md": "Original instructions", "rubric_md": "Original rubric"})["id"]
        self.conn.commit()

    def params(self, **extra):
        return {"assignment_id": self.assignment_id, "expected_revision": assignment_revision(load_assignment_row(self.conn, self.assignment_id)), **extra}

    def test_publication_notifications_reminders_and_receipt_share_transaction(self):
        token = self.token()
        params = self.params(availability_mode="deadline", due_at=(datetime.now()+timedelta(days=4)).isoformat(timespec="seconds"))
        result = writes.dispatch_write(self.conn, token, "publish", "publish_assignment", params)
        self.assertEqual("published", result["result"]["assignment_status"])
        self.assertEqual(2, self.count("message_center_notifications"))
        self.assertEqual(2, self.count("scheduled_tasks"))
        self.conn.rollback()
        self.assertEqual("new", load_assignment_row(self.conn, self.assignment_id)["status"])
        self.assertEqual((0,0,0), tuple(self.count(table) for table in ("message_center_notifications","scheduled_tasks","agent_action_executions")))
        writes.dispatch_write(self.conn, token, "publish", "publish_assignment", params)
        self.conn.commit()
        replay = writes.dispatch_write(self.conn, token, "publish", "publish_assignment", params)
        self.assertTrue(replay["replayed"])
        self.assertEqual(2, self.count("message_center_notifications"))

    def test_edit_preserves_defaults_clears_explicit_fields_and_rejects_stale_revision(self):
        token = self.token()
        params = self.params(title="Revised", rubric_md="", allowed_file_types=[])
        result = writes.dispatch_write(self.conn, token, "edit", "update_assignment_settings", params)
        self.conn.commit()
        row = load_assignment_row(self.conn, self.assignment_id)
        self.assertEqual(("Revised", "", "Original instructions", "new"), tuple(row[key] for key in ("title","rubric_md","requirements_md","status")))
        with self.assertRaises(HTTPException) as stale:
            writes.dispatch_write(self.conn, token, "stale", "update_assignment_settings", params)
        self.assertEqual(409, stale.exception.status_code)
        self.conn.rollback()
        self.assertEqual(result["result"]["revision"], self.params()["expected_revision"])
        with self.assertRaises(HTTPException):
            writes.dispatch_write(self.conn, self.token(self.student), "student-edit", "publish_assignment", self.params())
        self.conn.rollback()

    def test_normal_web_update_uses_same_service_effects_and_owner_policy(self):
        with patch.object(assignments, "get_db_connection", self.connection):
            result = asyncio.run(assignments.update_assignment(str(self.assignment_id), Request({"title":"Web updated", "status":"published"}), self.teacher))
        self.assertEqual("published", result["assignment_status"])
        self.assertEqual(2, self.count("message_center_notifications"))
        self.conn.execute("UPDATE courses SET created_by_teacher_id=8 WHERE id=20")
        self.conn.execute("UPDATE class_offerings SET teacher_id=8 WHERE id=40")
        self.conn.commit()
        with self.assertRaises(HTTPException) as denied:
            writes.dispatch_write(self.conn, self.token(), "owner-change", "publish_assignment", self.params())
        self.assertEqual(403, denied.exception.status_code)
        self.conn.rollback()

    def test_student_reads_only_own_submission_and_masks_unreleased_group_score_without_writes(self):
        self.conn.execute("UPDATE assignments SET status='published' WHERE id=?", (self.assignment_id,))
        for sid, name, score in ((7,"mine",91),(9,"another student private answer",99)):
            self.conn.execute("INSERT INTO submissions(assignment_id,student_pk_id,student_name,submitted_at,status,answers_json,score,feedback_md) VALUES(?,?,'Student','2026-09-10T12:00:00','graded',?,?,?)", (self.assignment_id,sid,name,score,'private feedback'))
        self.conn.execute("INSERT INTO assignment_group_bindings(assignment_id,class_offering_id,scheme_id,created_by_teacher_id) VALUES(?,40,100,7)", (str(self.assignment_id),))
        self.conn.commit()
        statements=[]
        self.conn.set_trace_callback(statements.append)
        result = get_assignment_details(self.conn, assignment_id=str(self.assignment_id), user=self.student)
        self.conn.set_trace_callback(None)
        self.assertEqual("mine", result["submission"]["answers_json"])
        self.assertIsNone(result["submission"]["score"])
        self.assertEqual("", result["submission"]["feedback_md"])
        self.assertNotIn("another student", json.dumps(result))
        self.assertNotIn("rubric_md", result["assignment"])
        self.assertFalse(any(sql.lstrip().split(" ",1)[0].upper() in {"CREATE","ALTER","INSERT","UPDATE","DELETE","COMMIT"} for sql in statements))

    def test_teacher_and_student_actual_broker_read_match_web_and_reject_removed_membership(self):
        self.conn.execute("UPDATE assignments SET status='published' WHERE id=?", (self.assignment_id,))
        self.conn.commit()
        app = FastAPI()
        app.include_router(assignments.router, prefix="/api")
        for target in ("classroom_app.database.get_db_connection", "classroom_app.dependencies.get_db_connection", "classroom_app.services.agent_platform_broker.get_db_connection", "classroom_app.routers.homework_parts.assignments.get_db_connection"):
            item=patch(target,self.connection); item.start(); self.addCleanup(item.stop)
        with TestClient(app) as client:
            for user in (self.teacher,self.student):
                app.dependency_overrides[dependencies.get_current_user] = lambda user=user: user
                normal = client.get(f"/api/assignments/{self.assignment_id}/details")
                self.assertEqual(200,normal.status_code)
                app.dependency_overrides.clear()
                token = self.token(user, scopes=["platform:read"])
                agent = asyncio.run(broker.dispatch_read(app,token,"assignment.details",path_params={"assignment_id":self.assignment_id}))
                self.assertEqual(normal.json(),agent["data"])
            self.conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(31,'Other class',8)")
            self.conn.execute("UPDATE students SET class_id=31 WHERE id=7")
            self.conn.commit()
            with self.assertRaises(HTTPException):
                asyncio.run(broker.dispatch_read(app,token,"assignment.details",path_params={"assignment_id":self.assignment_id}))

    def test_list_hides_unpublished_and_other_student_personal_trials(self):
        self.assertEqual([],list_classroom_assignments(self.conn,class_offering_id=40,user=self.student)["items"])
        self.conn.execute("UPDATE assignments SET status='published' WHERE id=?",(self.assignment_id,))
        self.conn.execute("INSERT INTO learning_stage_exam_attempts(class_offering_id,student_id,stage_key,assignment_id) VALUES(40,9,'foundation',?)",(self.assignment_id,))
        self.conn.commit()
        for user in (self.teacher,self.student):
            self.assertEqual([],list_classroom_assignments(self.conn,class_offering_id=40,user=user)["items"])
            with self.assertRaises(HTTPException):
                get_assignment_details(self.conn,assignment_id=str(self.assignment_id),user=user)

    def broker_app(self):
        app=FastAPI()
        app.include_router(assignments.router,prefix="/api")
        for module in (learning, smart_classroom, report_card, final_materials):
            app.include_router(module.router)
        for target in ("classroom_app.database.get_db_connection", "classroom_app.dependencies.get_db_connection", "classroom_app.services.agent_platform_broker.get_db_connection",
                       "classroom_app.routers.homework_parts.assignments.get_db_connection", "classroom_app.routers.learning.get_db_connection", "classroom_app.routers.smart_classroom.get_db_connection", "classroom_app.routers.report_card.get_db_connection", "classroom_app.routers.materials_parts.final_materials.get_db_connection"):
            item=patch(target,self.connection);item.start();self.addCleanup(item.stop)
        return app

    def test_my_courses_uses_shared_membership_and_pagination_without_other_actor_rows(self):
        self.conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(31,'Other',8)")
        self.conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(41,20,31,8)")
        self.conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(42,20,30,7)")
        self.conn.commit()
        app=self.broker_app()
        for user in (self.teacher,self.student):
            token=self.token(user,scopes=["platform:read"])
            result=asyncio.run(broker.dispatch_read(app,token,"classroom.my_courses",query_params={"limit":1}))['data']
            self.assertEqual(1,len(result['offerings']));self.assertTrue(result['has_more'])
            self.assertNotEqual(41,result['offerings'][0]['id'])
            page2=asyncio.run(broker.dispatch_read(app,token,"classroom.my_courses",query_params={"limit":1,"offset":1}))['data']
            self.assertNotEqual(result['offerings'][0]['id'],page2['offerings'][0]['id'])
        self.conn.execute("UPDATE students SET class_id=31 WHERE id=7")
        self.conn.commit()
        # A fresh current-user query follows the new classroom membership.
        self.assertEqual([41],[x['id'] for x in learning.list_my_classrooms(user=self.student)['offerings']])

    def test_attendance_fixed_query_disables_ai_and_returns_only_student_personal_view(self):
        app=self.broker_app(); token=self.token(self.student,scopes=["platform:read"])
        from classroom_app.services import smart_classroom_checkin_sync_service as attendance
        statements=[]; self.conn.set_trace_callback(statements.append)
        with patch.object(attendance,"attach_student_attendance_ai_advice",side_effect=AssertionError("read must not call model")):
            result=asyncio.run(broker.dispatch_read(app,token,"classroom.attendance",path_params={"class_offering_id":40}))['data']
        self.conn.set_trace_callback(None)
        self.assertEqual('student',result['viewer_role']);self.assertEqual([],result['students'])
        self.assertEqual(7,result['personal']['student_id'])
        self.assertEqual('skipped',result['ai_advice']['status'])
        self.assertFalse(any(sql.lstrip().split(' ',1)[0].upper() in {'CREATE','ALTER','INSERT','UPDATE','DELETE','COMMIT'} for sql in statements))

    def test_learning_snapshot_distinguishes_dirty_from_ready_without_peer_recalculation(self):
        from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
        from classroom_app.services import learning_progress_service as progress
        ensure_cultivation_progress_schema(self.conn,engine='sqlite')
        app=self.broker_app();token=self.token(self.student,scopes=["platform:read"])
        def read():
            return asyncio.run(broker.dispatch_read(app,token,"student.learning_snapshot",path_params={"class_offering_id":40}))['data']
        with patch.object(progress,'refresh_student_learning_state',side_effect=AssertionError('pure read cannot refresh')):
            self.assertEqual({'status':'success','snapshot_status':'refresh_required','progress':None,'message':'学习进度快照等待正常后台刷新；当前没有可确认的新结果。'},read())
            self.conn.execute("INSERT INTO learning_progress_snapshots(class_offering_id,student_id,score,metrics_json,dirty) VALUES(40,7,55,?,0)",(json.dumps({'score':55,'components':{},'material':{},'assignments':{},'interactions':{}}),))
            self.conn.commit()
            ready=read();self.assertEqual('ready',ready['snapshot_status']);self.assertEqual(55,ready['progress']['score'])
            self.conn.execute('UPDATE learning_progress_snapshots SET dirty=1');self.conn.commit()
            self.assertIsNone(read()['progress'])

    def test_teacher_statistics_and_student_report_use_normal_readonly_grade_projection(self):
        from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
        ensure_grade_publication_schema(self.conn,engine='sqlite')
        self.conn.execute("UPDATE assignments SET status='published' WHERE id=?",(self.assignment_id,))
        self.conn.execute("INSERT INTO submissions(assignment_id,student_pk_id,student_name,submitted_at,status,score,feedback_md) VALUES(?,7,'Student','2026-09-10T12:00:00','graded',88,'Feedback')",(self.assignment_id,))
        self.conn.commit(); app=self.broker_app()
        teacher_token=self.token(scopes=['platform:read'])
        stats=asyncio.run(broker.dispatch_read(app,teacher_token,'course.assignment_stats',path_params={'course_id':20},query_params={'class_offering_id':40}))['data']
        self.assertEqual(88,stats['assignments'][0]['avg_score'])
        student_token=self.token(self.student,scopes=['platform:read'])
        with TestClient(app) as client:
            app.dependency_overrides[dependencies.get_current_user]=lambda:self.student
            normal=client.get('/api/report-card?class_offering_id=40').json()
            app.dependency_overrides.clear()
        agent=asyncio.run(broker.dispatch_read(app,student_token,'student.report_card',query_params={'class_offering_id':40}))['data']
        self.assertEqual(normal,agent);self.assertEqual(88,agent['report_card']['courses'][0]['records'][0]['my_score'])
        with self.assertRaises(HTTPException):
            asyncio.run(broker.dispatch_read(app,teacher_token,'student.report_card',query_params={'class_offering_id':40}))

    def test_weights_and_personal_score_events_match_web_with_pure_role_scoped_reads(self):
        from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
        ensure_cultivation_progress_schema(self.conn,engine='sqlite')
        for sid in (7,9):
            self.conn.execute("INSERT INTO cultivation_score_events(class_offering_id,student_id,event_type,delta,created_at) VALUES(40,?,'material',3,?)",(sid,datetime.now().isoformat()))
        self.conn.commit();app=self.broker_app()
        for user,key,path in ((self.teacher,'classroom.learning_weights','weights'),(self.student,'student.score_events','score-events')):
            token=self.token(user,scopes=['platform:read'])
            with TestClient(app) as client:
                app.dependency_overrides[dependencies.get_current_user]=lambda user=user:user
                normal=client.get(f'/api/classrooms/40/learning/{path}').json()
                app.dependency_overrides.clear()
            statements=[];self.conn.set_trace_callback(statements.append)
            result=asyncio.run(broker.dispatch_read(app,token,key,path_params={'class_offering_id':40}))['data']
            self.conn.set_trace_callback(None)
            self.assertEqual(normal,result)
            if user['role']=='student':self.assertEqual(1,len(result['events']))
            self.assertFalse(any(sql.lstrip().split(' ',1)[0].upper() in {'CREATE','ALTER','INSERT','UPDATE','DELETE','COMMIT'} for sql in statements))
            opposite='student.score_events' if user['role']=='teacher' else 'classroom.learning_weights'
            with self.assertRaises(HTTPException):asyncio.run(broker.dispatch_read(app,token,opposite,path_params={'class_offering_id':40}))

    def test_grade_publication_preview_and_status_match_web_and_do_not_publish_or_leak(self):
        from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
        ensure_grade_publication_schema(self.conn,engine='sqlite')
        payload={'fields':{'class_offering_id':40},'structured':{'students':[{'student_number':f'S{sid}','ordinary_score':80,'final_score':90} for sid in (7,9)]}}
        self.conn.execute("INSERT INTO course_materials(id,teacher_id,material_path,name) VALUES(501,7,'fixture','Transcript')")
        self.conn.execute("INSERT INTO material_ai_import_records(id,teacher_id,package_material_id,parse_status,document_group,document_type,export_payload_json) VALUES(502,7,501,'completed','final_material','final_grade_transcript',?)",(json.dumps(payload),))
        self.conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id) VALUES(501,40,7)")
        self.conn.commit();app=self.broker_app();token=self.token(scopes=['platform:read'])
        for key,suffix,query in (('classroom.grade_publication','',{}),('classroom.grade_publication_preview','/preview',{'material_id':502})):
            with TestClient(app) as client:
                app.dependency_overrides[dependencies.get_current_user]=lambda:self.teacher
                normal=client.get(f'/api/classrooms/40/grade-publication{suffix}',params=query)
                self.assertEqual(200,normal.status_code);app.dependency_overrides.clear()
            statements=[];self.conn.set_trace_callback(statements.append)
            result=asyncio.run(broker.dispatch_read(app,token,key,path_params={'class_offering_id':40},query_params=query))['data']
            self.conn.set_trace_callback(None)
            self.assertEqual(normal.json(),result)
            if suffix:self.assertEqual([86,86],[x['overall_score'] for x in result['preview']['students']])
            self.assertFalse(any(sql.lstrip().split(' ',1)[0].upper() in {'CREATE','ALTER','INSERT','UPDATE','DELETE','COMMIT'} for sql in statements))
        self.assertEqual(0,self.count('grade_publications'))
        student_token=self.token(self.student,scopes=['platform:read'])
        with self.assertRaises(HTTPException):asyncio.run(broker.dispatch_read(app,student_token,'classroom.grade_publication_preview',path_params={'class_offering_id':40},query_params={'material_id':502}))
        self.conn.execute('UPDATE class_offerings SET teacher_id=8 WHERE id=40');self.conn.commit()
        with self.assertRaises(HTTPException):asyncio.run(broker.dispatch_read(app,token,'classroom.grade_publication',path_params={'class_offering_id':40}))
