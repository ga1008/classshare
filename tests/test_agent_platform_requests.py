"""Real normal HTTP handlers and SQL, using only synthetic users/content.

Only JWT decoding and DB connection factories are replaced. Business access
checks, FastAPI dependencies, business writes/commits and the durable ledger run.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx

from classroom_app import dependencies
from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.db import schema_polls
from classroom_app.routers import blog, message_center, polls
from classroom_app.services import agent_platform_request_service as service
from classroom_app.services import agent_platform_request_registry as registry
from classroom_app.services import agent_platform_request_context as context
from classroom_app.services import blog_service, message_center_service
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests.test_agent_delegation_service import fixture_connection


class PlatformRequestFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / 'requests.sqlite')
        for target in ('classroom_app.services.organization_scope_service.get_configured_db_engine',
                       'classroom_app.services.message_center_service.get_configured_db_engine',
                       'classroom_app.db.schema_polls.get_configured_db_engine'):
            self.patched(target, return_value='sqlite')
        self.patched('classroom_app.db.schema_polls._SCHEMA_READY', False)
        conn = fixture_connection(self.path)
        conn.executescript("""
          ALTER TABLE students ADD COLUMN student_id_number TEXT DEFAULT 'S007';
          INSERT INTO students(id,name,class_id,enrollment_status,school_code,school_name,college,department,student_id_number) VALUES(8,'Other student',30,'active','A','School A','C','D','S008');
          CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT);
          CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT);
          CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER,class_id INTEGER,course_id INTEGER);
          CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
          INSERT INTO classes VALUES(30,'Class A'),(31,'Class B');
          INSERT INTO courses VALUES(1,'Course A'),(2,'Course B');
          INSERT INTO class_offerings VALUES(1,7,30,1),(2,8,31,2);
          CREATE TABLE blog_posts(id INTEGER PRIMARY KEY,author_identity TEXT,status TEXT,visibility TEXT,
              visible_class_id INTEGER,visible_class_offering_id INTEGER,visible_user_identities_json TEXT,bookmark_count INTEGER DEFAULT 0);
          INSERT INTO blog_posts(id,author_identity,status,visibility) VALUES(1,'teacher:8','published','public'),(2,'teacher:8','draft','private');
          CREATE TABLE blog_bookmarks(id INTEGER PRIMARY KEY AUTOINCREMENT,post_id INTEGER,user_identity TEXT,user_role TEXT,user_pk INTEGER,created_at TEXT);
          CREATE TABLE blog_follows(id INTEGER PRIMARY KEY AUTOINCREMENT,user_identity TEXT,user_role TEXT,user_pk INTEGER,
              target_type TEXT,target_key TEXT,created_at TEXT,UNIQUE(user_identity,target_type,target_key));
          CREATE TABLE message_center_notifications(id INTEGER PRIMARY KEY,recipient_identity TEXT,recipient_role TEXT,recipient_user_pk INTEGER,
              category TEXT,severity TEXT,actor_identity TEXT,actor_role TEXT,actor_user_pk INTEGER,actor_display_name TEXT,
              title TEXT,body_preview TEXT,link_url TEXT,class_offering_id INTEGER,ref_type TEXT,ref_id TEXT,metadata_json TEXT,read_at TEXT,created_at TEXT);
          INSERT INTO message_center_notifications VALUES(1,'teacher:7','teacher',7,'agent_task','normal','','',NULL,'Agent','Teacher secret','','',NULL,'agent','1','{}',NULL,'now');
          INSERT INTO message_center_notifications VALUES(2,'student:7','student',7,'agent_task','normal','','',NULL,'Agent','Student secret','','',NULL,'agent','2','{}',NULL,'now');
          CREATE TABLE private_messages(id INTEGER PRIMARY KEY,conversation_key TEXT,class_offering_id INTEGER,
              sender_identity TEXT,sender_role TEXT,sender_user_pk INTEGER,sender_display_name TEXT,
              recipient_identity TEXT,recipient_role TEXT,recipient_user_pk INTEGER,recipient_display_name TEXT,content TEXT,read_at TEXT,created_at TEXT);
          CREATE TABLE private_message_blocks(id INTEGER PRIMARY KEY,owner_identity TEXT,owner_role TEXT,owner_user_pk INTEGER,
              blocked_identity TEXT,blocked_role TEXT,blocked_user_pk INTEGER,blocked_display_name TEXT,created_at TEXT,UNIQUE(owner_identity,blocked_identity));
          CREATE TABLE private_message_ai_jobs(id INTEGER PRIMARY KEY,requester_identity TEXT,conversation_key TEXT);
        """)
        key = message_center_service.build_conversation_key('student:7','teacher:7',None)
        conn.execute("INSERT INTO private_messages VALUES(1,?,NULL,'teacher:7','teacher',7,'Teacher','student:7','student',7,'Student','Synthetic private body',NULL,'now')",(key,))
        schema_polls.ensure_poll_schema(conn)
        conn.executescript("""
          INSERT INTO polls(id,owner_role,owner_user_pk,title,status,audience_scope,result_visibility) VALUES(1,'teacher',7,'Synthetic poll','active','custom','after_vote'),(2,'teacher',8,'Other poll','draft','custom','after_vote');
          INSERT INTO poll_options(id,poll_id,label) VALUES(1,1,'One'),(2,1,'Two');
          INSERT INTO poll_assignments(poll_id,class_offering_id) VALUES(1,1),(2,2);
          INSERT INTO poll_participants(poll_id,student_id) VALUES(1,7);
        """)
        ensure_agent_platform_requests_schema(conn)
        self.tokens, self.attempts = {}, {}
        for role,task_id,session in (('teacher',10,'teacher-session'),('student',11,'student-session'),('other',12,'other-session')):
            attempt=create_task_attempt(conn,task_id=task_id,worker_id='request-fixture',startup_key=role,lease_seconds=300)
            self.attempts[role]=attempt
            self.tokens[role]=issue_task_delegation(conn,task_id=task_id,attempt_id=attempt['id'],fencing_token=attempt['fencing_token'],
                purpose='tools',scopes=['platform:read','platform:write'],source_session_id=session)['token']
        conn.commit()
        conn.close()
        for target in ('classroom_app.database.get_db_connection','classroom_app.dependencies.get_db_connection',
                       'classroom_app.services.agent_platform_request_service.get_db_connection',
                       'classroom_app.routers.blog.get_db_connection','classroom_app.routers.message_center.get_db_connection',
                       'classroom_app.routers.polls.get_db_connection'):
            self.patched(target,self.connection)
        def decode(token,_ip):
            if token not in self.tokens: return None
            with self.connection() as conn:
                role='teacher' if token=='other' else token
                row=conn.execute('SELECT * FROM '+('teachers' if role=='teacher' else 'students')+' WHERE id=?',(8 if token=='other' else 7,)).fetchone()
                return {**dict(row),'role':role}
        self.patched('classroom_app.dependencies.verify_token',side_effect=decode)
        self.patched('classroom_app.routers.polls._broadcast_poll_changed',new=AsyncMock())
        self.patched('classroom_app.services.message_center_service._now_iso',return_value='2026-09-10T10:00:00+00:00')
        self.app=FastAPI()
        for module in (blog,message_center,polls): self.app.include_router(module.router)
        self.client=TestClient(self.app)
        self.addCleanup(self.client.close)

    def patched(self,*args,**kwargs):
        item=patch(*args,**kwargs)
        result=item.start()
        self.addCleanup(item.stop)
        return result

    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.path,timeout=10)
        conn.row_factory=sqlite3.Row
        try: yield conn
        finally: conn.close()

    def sql(self,statement,args=()):
        with self.connection() as conn:
            rows=conn.execute(statement,args).fetchall()
            conn.commit()
            return rows

    def web(self,role,method,path,**kwargs):
        self.client.cookies.set('access_token',role)
        return self.client.request(method,path,**kwargs)

    def dispatch(self,role,key,operation_id=None,**kwargs):
        return asyncio.run(service.dispatch_platform_request(self.app,self.tokens[role],key,operation_id or str(uuid.uuid4()),**kwargs))

    def assert_parity(self,role,key,**kwargs):
        operation,_=registry.resolve_capability(self.app,key)
        path,query,body,_=registry.arguments(operation,**kwargs)
        normal=self.web(role,operation.method,path+('?' + query.decode() if query else ''),content=body,
                        headers={'content-type':'application/json'} if body else {})
        delegated=self.dispatch(role,key,**kwargs)
        self.assertEqual(normal.status_code,delegated['result']['http_status'])
        if normal.status_code==200: self.assertEqual(normal.json(),delegated['result']['data'])
        self.assertFalse(delegated['verified_business'])
        return delegated

    def restored_parity(self,role,key,**kwargs):
        """Both transports start from identical synthetic DB contents."""
        operation,_=registry.resolve_capability(self.app,key)
        path,query,body,_=registry.arguments(operation,**kwargs)
        snapshot=sqlite3.connect(':memory:')
        with self.connection() as conn:conn.backup(snapshot)
        try:
            normal=self.web(role,operation.method,path+('?' + query.decode() if query else ''),content=body,
                headers={'content-type':'application/json'} if body else {})
            with self.connection() as conn:snapshot.backup(conn)
        finally:snapshot.close()
        delegated=self.dispatch(role,key,**kwargs)
        self.assertEqual(normal.status_code,delegated['result']['http_status'])
        if normal.status_code==200:
            self.assertEqual(normal.json(),delegated['result']['data'])
            self.assertEqual('observed_http_result',delegated['status'])
        return delegated


class AgentPlatformRequestsTests(PlatformRequestFixture):
    def test_registry_is_exact_fail_closed_and_parameters_are_bounded(self):
        expected={item.key for item in registry.CAPABILITIES if item.module in {'blog','message_center','polls'}}
        self.assertEqual(expected,{item['key'] for item in registry.platform_request_catalog(self.app)})
        for key,kw in [('unregistered',{}),('http.messages.read',{'body':{'notification_ids':[]}}),
                      ('http.blog.follow.set',{'body':{'target_type':'author','target_key':'\ud800','following':True}}),
                      ('http.blog.bookmark.toggle',{'path_params':{'post_id':True}}),
                      ('http.blog.follows.list',{'query_params':{'Authorization':'x'}}),
                      ('http.blog.follows.list',{'path_params':[]})]:
            with self.subTest(key=key),self.assertRaises(HTTPException): self.dispatch('student',key,**kw)
        self.assertEqual(0,len(self.sql('SELECT id FROM agent_platform_requests')))
        operation=registry.CAPABILITIES[0]
        with patch.object(registry,'CAPABILITIES',(replace(operation,source_sha256='0'*64),)):
            with self.assertRaises(HTTPException) as failure: self.dispatch('student',operation.key,path_params={'post_id':1})
            self.assertEqual(503,failure.exception.status_code)
            self.assertFalse(registry.platform_request_catalog(self.app,include_blocked=True)[0]['executable'])

    def test_blog_follow_set_and_list_match_normal_actor_scoping(self):
        for role in ('teacher','student'):
            result=self.assert_parity(role,'http.blog.follow.set',body={'target_type':'author','target_key':'teacher:8','following':True})
            self.assertEqual('observed_http_result',result['status'])
            self.assert_parity(role,'http.blog.follows.list')
        self.assertEqual({'teacher:7','student:7'},{row['user_identity'] for row in self.sql('SELECT user_identity FROM blog_follows')})
        self.assert_parity('student','http.blog.follow.set',body={'target_type':'author','target_key':'teacher:8','following':False})
        self.assertEqual(['teacher:7'],[row[0] for row in self.sql('SELECT user_identity FROM blog_follows')])

    def test_bookmark_permissions_match_web_and_stable_id_prevents_toggle_repeat(self):
        normal=self.web('student','POST','/api/blog/posts/1/bookmark')
        self.sql('DELETE FROM blog_bookmarks')
        self.sql('UPDATE blog_posts SET bookmark_count=0')
        operation_id=str(uuid.uuid4())
        result=self.dispatch('student','http.blog.bookmark.toggle',operation_id,path_params={'post_id':1})
        self.assertEqual(normal.json(),result['result']['data'])
        self.assertEqual(result,self.dispatch('student','http.blog.bookmark.toggle',operation_id,path_params={'post_id':1}))
        self.assertEqual(1,self.sql('SELECT bookmark_count FROM blog_posts WHERE id=1')[0][0])
        for role in ('student','other','teacher'):
            normal=self.web(role,'POST','/api/blog/posts/2/bookmark')
            result=self.dispatch(role,'http.blog.bookmark.toggle',path_params={'post_id':2})
            self.assertEqual(normal.status_code,result['result']['http_status'])
        self.assertEqual(403,self.web('student','POST','/api/blog/posts/2/bookmark').status_code)

    def test_notifications_mark_only_current_actor_ids(self):
        for role,own in (('teacher',1),('student',2)):
            data={'notification_ids':[1,2],'include_private':False}
            normal=self.web(role,'POST','/api/message-center/read',json=data)
            self.sql('UPDATE message_center_notifications SET read_at=NULL')
            result=self.dispatch(role,'http.messages.read',body=data)
            self.assertEqual(normal.json(),result['result']['data'])
            self.assertEqual([own],[row[0] for row in self.sql('SELECT id FROM message_center_notifications WHERE read_at IS NOT NULL')])
            self.sql('UPDATE message_center_notifications SET read_at=NULL')

    def test_private_block_add_list_remove_follow_web_self_ownership(self):
        for role in ('teacher','student'):
            added=self.assert_parity(role,'http.messages.blocks.add',body={'contact_identity':'student:8'})
            self.assertEqual(200,added['result']['http_status'])
            self.assert_parity(role,'http.messages.blocks.list')
        normal=self.web('student','DELETE','/api/message-center/private/blocks',params={'contact_identity':'student:8'})
        self.sql("INSERT INTO private_message_blocks(owner_identity,owner_role,owner_user_pk,blocked_identity,blocked_role,blocked_user_pk,blocked_display_name,created_at) VALUES('student:7','student',7,'student:8','student',8,'Other','now')")
        result=self.dispatch('student','http.messages.blocks.remove',query_params={'contact_identity':'student:8'})
        self.assertEqual(normal.json(),result['result']['data'])
        self.assertEqual(['teacher:7'],[row[0] for row in self.sql('SELECT owner_identity FROM private_message_blocks')])
        normal=self.web('student','POST','/api/message-center/private/blocks',json={'contact_identity':'student:7'})
        result=self.dispatch('student','http.messages.blocks.add',body={'contact_identity':'student:7'})
        self.assertEqual(normal.status_code,result['result']['http_status'])
        self.assertGreaterEqual(normal.status_code,400)

    def test_private_open_is_write_and_normal_conversation_identity_is_used(self):
        query={'contact':'teacher:7'}
        normal=self.web('student','GET','/api/message-center/private/conversation',params=query)
        self.sql('UPDATE private_messages SET read_at=NULL')
        result=self.dispatch('student','http.messages.private.open',query_params=query)
        self.assertEqual(normal.json(),result['result']['data'])
        self.assertIsNotNone(self.sql('SELECT read_at FROM private_messages WHERE id=1')[0][0])
        self.assert_parity('other','http.messages.private.open',query_params={'contact':'student:7'})
        self.sql("UPDATE agent_task_delegations SET scopes_json='[\"platform:read\"]' WHERE actor_role='student'")
        with self.assertRaises(HTTPException) as denied: self.dispatch('student','http.messages.private.open',query_params=query)
        self.assertEqual(403,denied.exception.status_code)

    def test_poll_read_and_vote_enforce_normal_participation_and_class_access(self):
        for role in ('teacher','student','other'):
            self.assert_parity(role,'http.polls.detail',path_params={'poll_id':1})
            self.assert_parity(role,'http.polls.detail',path_params={'poll_id':2})
            self.assert_parity(role,'http.polls.snapshot',path_params={'class_offering_id':1})
        normal=self.web('student','POST','/api/polls/1/vote',json={'option_ids':[1]})
        self.sql('DELETE FROM poll_votes')
        self.sql('DELETE FROM poll_ballots')
        result=self.dispatch('student','http.polls.vote',path_params={'poll_id':1},body={'option_ids':[1]})
        self.assertEqual(normal.json(),result['result']['data'])
        self.assertEqual(1,len(self.sql('SELECT id FROM poll_votes')))
        for role in ('teacher','other'): self.assert_parity(role,'http.polls.vote',path_params={'poll_id':1},body={'option_ids':[1]})

    def test_http_facts_do_not_claim_business_completion_or_follow_redirects(self):
        for response,status,followup in [
            (httpx.Response(202,json={'status':'ok','job_id':12}),'submitted','needs_job_tracker_not_completed'),
            (httpx.Response(200,text='<html>Login</html>',headers={'content-type':'text/html'}),'uncertain','needs_response_adapter'),
            (httpx.Response(303,headers={'location':'https://external.invalid'}),'uncertain','needs_interaction'),
            (httpx.Response(500,json={'secret':'never returned'}),'uncertain','reconcile_http_error_no_automatic_retry')]:
            actual,data=service._observation(response)
            self.assertEqual((status,followup),(actual,data['follow_up']))
            self.assertFalse(data['verified_business'])
            if response.status_code!=202: self.assertNotIn('data',data)

    def test_admission_is_committed_before_business_and_contains_no_bearer(self):
        original=blog.toggle_bookmark
        seen=[]
        def inspect_admission(conn,user,post_id):
            with self.connection() as observer:
                rows=observer.execute('SELECT * FROM agent_platform_requests').fetchall()
                self.assertEqual(1,len(rows))
                row=dict(rows[0])
                self.assertEqual('executing',row['status'])
                self.assertEqual('student',row['actor_role'])
                self.assertEqual(7,row['actor_id'])
                self.assertNotIn(self.tokens['student'],json.dumps(row))
                self.assertNotIn('student-session',json.dumps(row))
                seen.append(row)
            return original(conn,user,post_id)
        with patch.object(blog,'toggle_bookmark',side_effect=inspect_admission):
            result=self.dispatch('student','http.blog.bookmark.toggle',path_params={'post_id':1})
        self.assertEqual(seen[0]['id'],result['request_id'])
        self.assertEqual('observed_http_result',result['status'])

    def test_commit_then_exception_stays_uncertain_and_cannot_repeat_with_new_uuid(self):
        original=blog.toggle_bookmark
        def committed_then_lost(conn,user,post_id):
            original(conn,user,post_id)
            conn.commit()
            raise RuntimeError('synthetic response failure after business commit')
        operation_id=str(uuid.uuid4())
        with patch.object(blog,'toggle_bookmark',side_effect=committed_then_lost),self.assertRaises(RuntimeError):
            self.dispatch('student','http.blog.bookmark.toggle',operation_id,path_params={'post_id':1})
        replay=self.dispatch('student','http.blog.bookmark.toggle',operation_id,path_params={'post_id':1})
        self.assertEqual('uncertain',replay['status'])
        with self.assertRaises(HTTPException) as blocked:
            self.dispatch('student','http.blog.bookmark.toggle',path_params={'post_id':1})
        self.assertEqual(409,blocked.exception.status_code)
        self.assertEqual(1,self.sql('SELECT bookmark_count FROM blog_posts WHERE id=1')[0][0])

    def test_concurrent_same_operation_reuses_durable_executing_receipt(self):
        original=blog.toggle_bookmark
        started,release=threading.Event(),threading.Event()
        calls=[]
        def slow(conn,user,post_id):
            calls.append(1)
            started.set()
            if not release.wait(3): raise RuntimeError('fixture release missing')
            return original(conn,user,post_id)
        async def exercise():
            operation_id=str(uuid.uuid4())
            first=asyncio.create_task(service.dispatch_platform_request(self.app,self.tokens['student'],'http.blog.bookmark.toggle',operation_id,path_params={'post_id':1}))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,1))
                second=await service.dispatch_platform_request(self.app,self.tokens['student'],'http.blog.bookmark.toggle',operation_id,path_params={'post_id':1})
                self.assertEqual('executing',second['status'])
            finally: release.set()
            finished=await first
            self.assertEqual(second['request_id'],finished['request_id'])
        with patch.object(blog,'toggle_bookmark',side_effect=slow): asyncio.run(exercise())
        self.assertEqual(1,len(calls))
        self.assertEqual(1,self.sql('SELECT bookmark_count FROM blog_posts WHERE id=1')[0][0])

    def test_timeout_or_repeated_cancel_retains_capacity_until_actual_thread_finishes(self):
        original=blog.toggle_bookmark
        for cancel in (False,True):
            with self.subTest(cancel=cancel):
                started,release=threading.Event(),threading.Event()
                capacity=threading.BoundedSemaphore(1)
                identities=[]
                def slow(conn,user,post_id):
                    identities.append(context._request_identity.get())
                    started.set()
                    if not release.wait(3): raise RuntimeError('fixture release missing')
                    return original(conn,user,post_id)
                async def exercise():
                    work=asyncio.create_task(service.dispatch_platform_request(self.app,self.tokens['student'],'http.blog.bookmark.toggle',str(uuid.uuid4()),path_params={'post_id':1}))
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait,1))
                        if cancel: work.cancel()
                        await asyncio.sleep(.05)
                        if cancel: work.cancel()
                        await asyncio.sleep(.01)
                        self.assertFalse(work.done())
                        self.assertFalse(capacity.acquire(blocking=False))
                        self.assertTrue(identities[0].active)
                    finally: release.set()
                    if cancel:
                        with self.assertRaises(asyncio.CancelledError): await work
                    else:
                        with self.assertRaises(HTTPException) as timedout: await work
                        self.assertEqual(504,timedout.exception.status_code)
                    self.assertFalse(identities[0].active)
                    self.assertTrue(capacity.acquire(blocking=False))
                    capacity.release()
                with patch.object(blog,'toggle_bookmark',side_effect=slow),patch.object(service,'_CAPACITY',capacity),patch.object(service,'WAIT_TIMEOUT_SECONDS',.02):
                    asyncio.run(exercise())
        self.assertEqual(['observed_http_result']*2,[row[0] for row in self.sql('SELECT status FROM agent_platform_requests')])

    def test_revoked_authority_settles_host_fact_but_receives_no_private_response(self):
        original=blog.list_follows
        def revoke_after_read(conn,user):
            data=original(conn,user)
            self.sql("DELETE FROM user_sessions WHERE session_user_key='student:7'")
            return data
        operation_id=str(uuid.uuid4())
        with patch.object(blog,'list_follows',side_effect=revoke_after_read),self.assertRaises(HTTPException) as denied:
            self.dispatch('student','http.blog.follows.list',operation_id)
        self.assertEqual(401,denied.exception.status_code)
        row=self.sql('SELECT status,settled_at,result_json FROM agent_platform_requests')[0]
        self.assertEqual('observed_http_result',row['status'])
        self.assertIsNotNone(row['settled_at'])
        self.assertEqual(200,json.loads(row['result_json'])['http_status'])
        with self.assertRaises(HTTPException): service.get_platform_request_receipt(self.tokens['student'],operation_id)

    def test_operation_nonce_route_body_and_active_identity_cannot_fall_back_to_cookie(self):
        captured=[]
        original=blog.list_follows
        def capture(conn,user):
            captured.append(context._request_identity.get())
            return original(conn,user)
        with patch.object(blog,'list_follows',side_effect=capture): self.dispatch('student','http.blog.follows.list')
        identity=captured[0]
        self.assertFalse(identity.active)
        scope={'type':'http','method':identity.method,'path':identity.path,'query_string':identity.query_string,
               'headers':[(b'cookie',b'access_token=teacher')],'route':identity.route,
               context._SCOPE_KEY:(identity.nonce,identity.operation_id,identity.body_hash)}
        for change in ({},{context._SCOPE_KEY:(object(),identity.operation_id,identity.body_hash)},
                       {context._SCOPE_KEY:(identity.nonce,str(uuid.uuid4()),identity.body_hash)},
                       {context._SCOPE_KEY:(identity.nonce,identity.operation_id,'0'*64)},
                       {'method':'POST'},{'path':'/api/polls/1'},{'route':object()},{'query_string':b'x=1'}):
            active=replace(identity,active=bool(change))
            token=context._request_identity.set(active)
            try:
                with self.assertRaises(HTTPException) as denied: dependencies.get_current_user_optional(Request({**scope,**change}))
                self.assertEqual(403,denied.exception.status_code)
            finally: context._request_identity.reset(token)
        token=context._request_identity.set(replace(identity,active=True,attempt_id='forged'))
        try:
            with self.assertRaises(HTTPException) as denied: dependencies.get_current_user_optional(Request(scope))
            self.assertEqual(401,denied.exception.status_code)
        finally: context._request_identity.reset(token)

    def test_shadow_route_does_not_receive_admitted_identity(self):
        from starlette.routing import Route
        calls=[]
        async def unexpected(request):
            calls.append(1)
            return httpx.Response(200)
        self.app.router.routes.insert(0,Route('/api/{rest:path}',unexpected,methods=['POST']))
        with self.assertRaises(HTTPException) as denied: self.dispatch('student','http.blog.bookmark.toggle',path_params={'post_id':1})
        self.assertEqual(403,denied.exception.status_code)
        self.assertEqual([],calls)
        self.assertEqual('uncertain',self.sql('SELECT status FROM agent_platform_requests')[0][0])

    def test_oversized_body_cannot_escape_receipt_bound(self):
        with patch.object(blog,'list_follows',return_value=['x'*(service.MAX_RESPONSE_BYTES+1)]),self.assertRaises(ValueError):
            self.dispatch('student','http.blog.follows.list')
        row=self.sql('SELECT status,result_json FROM agent_platform_requests')[0]
        self.assertEqual('uncertain',row['status'])
        self.assertLess(len(row['result_json']),1024)

    def additional_student_task(self,task_id,*,now=None,session='student-session'):
        with self.connection() as conn:
            conn.execute("INSERT INTO agent_tasks VALUES(?,'student',7,NULL,'running',NULL)",(task_id,))
            attempt=create_task_attempt(conn,task_id=task_id,worker_id='extra-fixture',startup_key=str(task_id),lease_seconds=300,now=now)
            token=issue_task_delegation(conn,task_id=task_id,attempt_id=attempt['id'],fencing_token=attempt['fencing_token'],
                purpose='tools',scopes=['platform:read','platform:write'],source_session_id=session,now=now)['token']
            conn.commit()
            return token

    def test_uncertain_write_blocks_other_tasks_and_sessions_until_review_then_fresh_grant(self):
        original=blog.toggle_bookmark
        def fail_after_commit(conn,user,post_id):
            original(conn,user,post_id)
            conn.commit()
            raise RuntimeError('synthetic lost response')
        with patch.object(blog,'toggle_bookmark',side_effect=fail_after_commit),self.assertRaises(RuntimeError):
            self.dispatch('student','http.blog.bookmark.toggle',path_params={'post_id':1})
        self.sql("UPDATE user_sessions SET session_id='replacement-student' WHERE session_user_key='student:7'")
        existing=self.additional_student_task(13,session='replacement-student')
        def call(token): return asyncio.run(service.dispatch_platform_request(self.app,token,'http.blog.bookmark.toggle',str(uuid.uuid4()),path_params={'post_id':1}))
        with self.assertRaises(HTTPException) as blocked: call(existing)
        self.assertEqual(409,blocked.exception.status_code)
        # Admission rules are tested independently of the separately tested
        # current-user reconciliation service; this is an explicit DB fixture.
        now=int(time.time())
        self.sql("UPDATE agent_platform_requests SET reconciliation_status='cleared',reconciliation_resolution='not_occurred',reconciled_at=?",(now,))
        with self.assertRaises(HTTPException) as blocked: call(existing)
        self.assertEqual(409,blocked.exception.status_code)
        fresh=self.additional_student_task(14,now=now+2,session='replacement-student')
        result=call(fresh)
        self.assertEqual('observed_http_result',result['status'])
        self.assertEqual(0,self.sql('SELECT bookmark_count FROM blog_posts WHERE id=1')[0][0])

    def test_pure_read_can_retry_after_uncertainty_with_another_operation_id(self):
        with patch.object(blog,'list_follows',side_effect=RuntimeError('synthetic read failure')),self.assertRaises(RuntimeError):
            self.dispatch('student','http.blog.follows.list')
        result=self.dispatch('student','http.blog.follows.list')
        self.assertEqual('observed_http_result',result['status'])
        self.assertEqual([0,0],[row[0] for row in self.sql('SELECT mutates FROM agent_platform_requests')])

    def test_cancel_during_file_preparation_never_admits_or_starts_business(self):
        original=registry.CAPABILITIES[0]
        operation=replace(original,transport='form',allows_files=True)
        started,release=threading.Event(),threading.Event()
        capacity=threading.BoundedSemaphore(1)
        def delayed_snapshot(_conn,_grant,_operation,normalized,_files):
            started.set()
            if not release.wait(3):raise RuntimeError('fixture release missing')
            return b'','application/x-www-form-urlencoded',normalized
        async def exercise():
            call=asyncio.create_task(service.dispatch_platform_request(self.app,self.tokens['student'],operation.key,
                str(uuid.uuid4()),path_params={'post_id':1},files=[]))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,1))
                call.cancel()
                await asyncio.sleep(.03)
                self.assertFalse(call.done())
                self.assertFalse(capacity.acquire(blocking=False))
            finally:release.set()
            with self.assertRaises(asyncio.CancelledError):await call
        with patch.object(registry,'CAPABILITIES',(operation,)),patch.object(service,'_CAPACITY',capacity),\
             patch('classroom_app.services.agent_platform_multipart_service.encode_form_upload',side_effect=delayed_snapshot):
            asyncio.run(exercise())
        self.assertEqual([],self.sql('SELECT id FROM agent_platform_requests'))
        self.assertEqual(0,self.sql('SELECT bookmark_count FROM blog_posts WHERE id=1')[0][0])


if __name__=='__main__': unittest.main()
