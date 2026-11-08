"""Trusted transport metadata and observation recovery, without network I/O."""
import os
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
from classroom_app.services import trusted_network_service as network
from classroom_app.services import login_attendance_access_service as access
from classroom_app.db.schema_login_attendance import ensure_login_attendance_schema


def request(peer='192.0.2.5',scheme='https',**headers):
    return Request({'type':'http','method':'POST','path':'/api/classrooms/10/login-attendance/sessions/21/rounds',
        'query_string':b'','scheme':scheme,'server':('platform.test',443),'client':(peer,12345),
        'headers':[(k.replace('_','-').encode(),str(v).encode()) for k,v in {'host':'platform.test',**headers}.items()]})


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p=patch.dict(os.environ,{'TRUSTED_PROXY_HOSTS':'','TRUSTED_PROXY_CIDRS':'127.0.0.1/32,::1/128'})
        p.start();self.addCleanup(p.stop)

    def test_headers_from_untrusted_peer_cannot_change_ip(self):
        r=request(x_forwarded_for='198.51.100.10',x_real_ip='198.51.100.11')
        self.assertEqual({'ip':'192.0.2.5','source':'direct'},network.client_network(r))

    def test_proxy_chain_uses_nearest_untrusted_hop(self):
        r=request(peer='127.0.0.1',x_forwarded_for='203.0.113.7, 192.0.2.5, 127.0.0.1')
        self.assertEqual('192.0.2.5',network.client_network(r)['ip'])

    def test_origin_checks_scheme_and_port_and_default_port_equivalence(self):
        network.require_same_origin(request(origin='https://platform.test:443'))
        network.require_same_origin(request(host='platform.test:28769',origin='https://platform.test:28769'))
        for origin in ['http://platform.test','https://platform.test:28769','null','https://evil.test']:
            with self.assertRaises(HTTPException):network.require_same_origin(request(origin=origin))
        with self.assertRaises(HTTPException):network.require_same_origin(request(sec_fetch_site='cross-site'))

    async def test_scheme_middleware_retains_peer_and_refuses_untrusted_tls_header(self):
        seen=[]
        async def app(scope,receive,send):seen.append(scope)
        middleware=network.TrustedProxySchemeMiddleware(app)
        for peer,expected in [('127.0.0.1','https'),('192.0.2.5','http')]:
            r=request(peer=peer,scheme='http',x_forwarded_proto='https')
            await middleware(r.scope,None,None)
            self.assertEqual(expected,seen[-1]['scheme'])
            self.assertEqual(peer,seen[-1]['client'][0])


class AccessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('CREATE TABLE class_offerings(id INTEGER PRIMARY KEY);CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY);')
        ensure_login_attendance_schema(self.conn,engine='sqlite');self.conn.commit()
        self.user={'id':1,'role':'student','session_id':'synthetic-session'}
        self.addCleanup(access._recent.clear);access._recent.clear()
        p=patch.dict(os.environ,{'LOGIN_ATTENDANCE_ENABLED':'1','TRUSTED_PROXY_HOSTS':''})
        p.start();self.addCleanup(p.stop)

    @contextmanager
    def database(self):
        yield self.conn

    def test_failed_insert_is_not_cached_and_retry_succeeds_once(self):
        with patch.object(access,'get_db_connection',side_effect=RuntimeError('Synthetic unavailable')):
            failed=request();access.observe_authenticated_request(failed,self.user)
        self.assertTrue(failed.state.attendance_collection_unavailable)
        self.assertEqual(0,len(access._recent))
        with patch.object(access,'get_db_connection',self.database):
            access.observe_authenticated_request(request(),self.user)
            access.observe_authenticated_request(request(),self.user)
        self.assertEqual(1,self.conn.execute('SELECT COUNT(*) FROM login_attendance_access').fetchone()[0])
        saved=dict(self.conn.execute('SELECT * FROM login_attendance_access').fetchone())
        self.assertNotIn('synthetic-session',str(saved))

    def test_login_evidence_failure_does_not_rollback_original_login(self):
        self.conn.execute('CREATE TABLE synthetic_auth(id INTEGER PRIMARY KEY)')
        self.conn.execute('INSERT INTO synthetic_auth VALUES(1)')
        with patch.object(access,'record_access',side_effect=RuntimeError('Synthetic failure')):
            access.record_login_observation(self.conn,student_id=1,client_ip='192.0.2.5',login_method='password')
        self.conn.commit()
        self.assertEqual(1,self.conn.execute('SELECT COUNT(*) FROM synthetic_auth').fetchone()[0])
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM login_attendance_access').fetchone()[0])

    def test_tabs_and_arbitrary_paths_do_not_multiply_presence_writes(self):
        with patch.object(access,'get_db_connection',self.database),patch.object(access.time,'time',return_value=1800000000):
            for offering in range(200):
                req=request();req.scope['path']=f'/api/classrooms/{offering}/anything'
                access.observe_authenticated_request(req,self.user)
        self.assertEqual(1,len(access._recent))
        self.assertEqual(1,self.conn.execute('SELECT COUNT(*) FROM login_attendance_access').fetchone()[0])

    def test_cached_session_of_inactive_student_creates_no_attendance_fact(self):
        from classroom_app import dependencies
        req=request();req.state.auth_verified=True;req.state.auth_user=self.user
        with (patch.object(dependencies,'validate_authenticated_user_identity',side_effect=HTTPException(403,'Inactive')),
              patch.object(access,'observe_authenticated_request') as collector):
            self.assertEqual(self.user,dependencies.get_active_user_from_request(req))
            collector.assert_not_called()
