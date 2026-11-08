"""Real ASGI routes with a synthetic in-memory database and fixed identities."""
from contextlib import contextmanager
import sqlite3
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from classroom_app.routers import login_attendance as api
from classroom_app.services.trusted_network_service import TrustedProxySchemeMiddleware
from classroom_app.services import approval_request_types  # noqa: F401; normal app bootstrap
from test_login_attendance_service import LoginAttendanceServiceTests as Fixture


class AttendanceRouterTests(unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)
        replacement=sqlite3.connect(':memory:',check_same_thread=False)
        replacement.row_factory=sqlite3.Row;self.conn.backup(replacement)
        self.conn=replacement;self.addCleanup(replacement.close)
        self.conn.execute('CREATE TABLE message_center_notifications(id INTEGER PRIMARY KEY,recipient_role TEXT,recipient_user_pk INTEGER,ref_type TEXT,ref_id TEXT,email_status TEXT,email_queued_at TEXT,email_sent_at TEXT)')
        self.conn.commit()
        app=FastAPI();app.include_router(api.router);app.include_router(api.attention_router);app.include_router(api.archive_router)
        app.add_middleware(TrustedProxySchemeMiddleware)
        app.dependency_overrides[api.get_current_teacher]=lambda:self.user
        app.dependency_overrides[api.get_current_user]=lambda:self.identity
        self.identity={'id':1,'role':'student','name':'Synthetic'}
        patcher=patch.object(api,'get_db_connection',self.database)
        patcher.start();self.addCleanup(patcher.stop)
        self.client=TestClient(app,base_url='https://platform.test:28769',client=('127.0.0.1',40000))
        self.addCleanup(self.client.close)
        self.base='/api/classrooms/10/login-attendance'
        self.headers={'Origin':'https://platform.test:28769'}

    @contextmanager
    def database(self):
        try:yield self.conn
        except BaseException:self.conn.rollback();raise

    def create(self):
        response=self.client.post(self.base+'/sessions/21/rounds',headers=self.headers,
            json={'request_key':'synthetic-http-key','networks':['192.0.2.0/24']})
        self.assertEqual(200,response.status_code,response.text)
        return response.json()

    def test_actual_routes_preserve_scope_revisions_and_private_projection(self):
        rnd=self.create()
        self.assertEqual(6,len(rnd['records']))
        student=self.client.get(self.base+'/sessions/21/me').json()
        self.assertEqual(1,student['student_id']);self.assertFalse(student['can_appeal'])
        forbidden=self.client.post(self.base+f"/rounds/{rnd['id']}/appeals",headers=self.headers,json={'reason':'Too early'})
        self.assertEqual(409,forbidden.status_code,forbidden.text)
        conflict=self.client.post(self.base+f"/rounds/{rnd['id']}/end",headers=self.headers,json={'revision':0})
        self.assertEqual(409,conflict.status_code)
        ended=self.client.post(self.base+f"/rounds/{rnd['id']}/end",headers=self.headers,json={'revision':rnd['revision']})
        self.assertEqual(200,ended.status_code,ended.text);self.assertEqual('settling',ended.json()['state'])
        detail=self.client.get(self.base+f"/rounds/{rnd['id']}/students/1")
        self.assertEqual(200,detail.status_code,detail.text);self.assertIsNone(detail.json()['pending_appeal'])
        other=self.client.get(f"/api/classrooms/11/login-attendance/rounds/{rnd['id']}")
        self.assertEqual(404,other.status_code)

    def test_bad_origin_wrong_scheme_and_stale_selection_rejected(self):
        for origin in ['https://platform.test','http://platform.test:28769','null']:
            response=self.client.post(self.base+'/sessions/21/rounds',headers={'Origin':origin},
                json={'request_key':'synthetic-http-key','networks':['192.0.2.0/24']})
            self.assertEqual(403,response.status_code)
        rnd=self.create();entry=rnd['records'][0]
        response=self.client.patch(self.base+f"/rounds/{rnd['id']}/records",headers=self.headers,
            json={'records':[{'student_id':entry['student_id'],'revision':0,'status':'ABSENT'}],'reason':'Confirmed individually'})
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual('conflict',response.json()['results'][0]['result'])
        self.assertEqual('PENDING',response.json()['round']['records'][0]['status'])

    def test_other_teacher_cannot_read_or_mutate(self):
        rnd=self.create();self.user={'id':8,'role':'teacher'}
        for path in ['/sessions/21',f"/rounds/{rnd['id']}",f"/rounds/{rnd['id']}/students/1"]:
            self.assertEqual(403,self.client.get(self.base+path).status_code)
        response=self.client.post(self.base+f"/rounds/{rnd['id']}/void",headers=self.headers,
            json={'revision':rnd['revision'],'reason':'Unauthorized'})
        self.assertEqual(403,response.status_code)

    def test_identity_scoped_attendance_projections_are_never_http_cached(self):
        rnd=self.create()
        paths=[self.base+'/sessions/21', self.base+'/sessions/21/me',
               self.base+f"/rounds/{rnd['id']}/students/1",self.base+'/history',
               '/api/login-attendance/attention','/api/login-attendance/archive/history']
        for path in paths:
            with self.subTest(path=path):
                response=self.client.get(path)
                self.assertEqual(200,response.status_code,response.text)
                self.assertEqual('private, no-store',response.headers['Cache-Control'])
                self.assertEqual({'Cookie','Authorization'},set(response.headers['Vary'].split(', ')))

    def test_durable_appeal_attention_tab_claim_and_manual_resolution(self):
        rnd=self.create()
        ended=self.client.post(self.base+f"/rounds/{rnd['id']}/end",headers=self.headers,json={'revision':rnd['revision']})
        self.assertEqual(200,ended.status_code)
        with patch.object(api.workflow,'_notify',return_value=0):
            created=self.client.post(self.base+f"/rounds/{rnd['id']}/appeals",headers=self.headers,json={'reason':'Please verify'})
            self.assertEqual(200,created.status_code,created.text)
            request_id=created.json()['request']['id']
            repeated=self.client.post(self.base+f"/rounds/{rnd['id']}/appeals",headers=self.headers,json={'reason':'Please verify'})
            self.assertEqual(409,repeated.status_code)
            items=self.client.get('/api/login-attendance/attention').json()['items']
            self.assertEqual(1,len(items));self.assertEqual('unavailable',items[0]['email_delivery']['email_status'])
            url=f'/api/login-attendance/attention/{request_id}'
            first=self.client.post(url+'/claim',headers=self.headers,json={'owner':'synthetic-tab-one'})
            second=self.client.post(url+'/claim',headers=self.headers,json={'owner':'synthetic-tab-two'})
            self.assertTrue(first.json()['claimed']);self.assertFalse(second.json()['claimed'])
            detail=self.client.get(self.base+f"/rounds/{rnd['id']}/students/1").json()
            self.assertEqual(request_id,detail['pending_appeal']['id'])
            self.assertFalse(self.client.get(self.base+'/sessions/21/me').json()['can_appeal'])
            changed=self.client.patch(self.base+f"/rounds/{rnd['id']}/records",headers=self.headers,
                json={'records':[{'student_id':1,'revision':detail['record']['revision'],'status':'PRESENT'}],'reason':'Teacher verified'})
            self.assertEqual(200,changed.status_code,changed.text)
            self.assertEqual([],self.client.get('/api/login-attendance/attention').json()['items'])
            state=self.conn.execute('SELECT status FROM approval_requests WHERE id=?',(request_id,)).fetchone()[0]
            self.assertEqual('cancelled',state)
