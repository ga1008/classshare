"""Blog lifecycle uses normal Web handlers, real SQL and B observation receipts."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.routers import blog
from classroom_app.services import blog_service, blog_community_service
from tests.test_agent_platform_writes import PlatformWriteFixture
from tests.test_agent_platform_requests import PlatformRequestFixture


class BlogRequestTests(PlatformWriteFixture):
    patched = PlatformRequestFixture.patched
    sql = PlatformRequestFixture.sql
    web = PlatformRequestFixture.web
    dispatch = PlatformRequestFixture.dispatch
    restored_parity = PlatformRequestFixture.restored_parity

    def setUp(self):
        super().setUp()
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=str(Path(temporary.name)/'blog.sqlite')
        target=sqlite3.connect(self.path)
        self.conn.backup(target);target.close();self.conn.close()
        self.conn=sqlite3.connect(self.path,check_same_thread=False);self.conn.row_factory=sqlite3.Row;self.addCleanup(self.conn.close)
        ensure_agent_platform_requests_schema(self.conn)
        self.conn.execute('UPDATE teachers SET is_super_admin=1 WHERE id=7');self.conn.commit()
        self.tokens={role:self.token(user,scopes=['platform:read','platform:write']) for role,user in (('teacher',self.teacher),('student',self.student))}
        self.clock=self.patched('classroom_app.services.blog_service._now_iso',return_value='2026-09-10T10:00:00')
        self.post=blog_service.create_post(self.conn,self.student,title='Student article',content_md='A useful account of learning.',status='published')['id']
        self.private=blog_service.create_post(self.conn,self.student,title='Student private draft',content_md='Private content',status='draft')['id']
        self.comment=blog_service.add_comment(self.conn,self.teacher,self.post,content_md='Teacher reply')['id']
        self.reply=blog_service.add_comment(self.conn,self.student,self.post,content_md='Student reply',parent_comment_id=self.comment)['id']
        self.conn.commit();self.clock.return_value='2026-09-10T11:00:00'
        for name in ('classroom_app.routers.blog.get_db_connection','classroom_app.dependencies.get_db_connection',
                     'classroom_app.database.get_db_connection','classroom_app.services.agent_platform_request_service.get_db_connection'):
            self.patched(name,self.connection)
        def decode(token,_ip):
            if token not in self.tokens:return None
            with self.connection() as conn:
                table='teachers' if token=='teacher' else 'students'
                row=conn.execute(f'SELECT * FROM {table} WHERE id=7').fetchone()
                return {**dict(row),'role':token,'session_id':token+'-session'}
        self.patched('classroom_app.dependencies.verify_token',side_effect=decode)
        self.app=FastAPI();self.app.include_router(blog.router)
        self.client=TestClient(self.app);self.addCleanup(self.client.close)

    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.path,timeout=10);conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:yield conn
        except Exception:conn.rollback();raise
        finally:conn.close()

    def revision(self,post_id):
        return self.sql('SELECT updated_at FROM blog_posts WHERE id=?',(post_id,))[0][0]

    def test_detail_update_cas_and_original_visibility_match_normal_web(self):
        detail=self.restored_parity('student','http.blog.post.detail',path_params={'post_id':self.post})
        self.assertEqual(200,detail['result']['http_status'])
        self.assertEqual(1,len(self.sql('SELECT id FROM blog_post_views WHERE post_id=?',(self.post,))))
        version=self.revision(self.post)
        edited=self.restored_parity('student','http.blog.post.update',path_params={'post_id':self.post},body={'expected_updated_at':version,'title':'Edited title','content_md':'Updated content','tags':['reflection']})
        self.assertEqual(200,edited['result']['http_status'])
        self.assertNotEqual(version,edited['result']['data']['updated_at'])
        stale=self.dispatch('student','http.blog.post.update',path_params={'post_id':self.post},body={'expected_updated_at':version,'title':'Stale overwrite'})
        self.assertEqual(409,stale['result']['http_status']);self.assertEqual('Edited title',self.sql('SELECT title FROM blog_posts WHERE id=?',(self.post,))[0][0])
        with self.assertRaises(HTTPException):self.dispatch('student','http.blog.post.update',path_params={'post_id':self.post},body={'title':'Missing revision'})
        denied=self.restored_parity('teacher','http.blog.post.update',path_params={'post_id':self.post},body={'expected_updated_at':self.revision(self.post),'title':'Wrong author'})
        self.assertEqual(403,denied['result']['http_status'])

    def test_post_delete_cas_and_comment_subtree_cleanup_use_normal_policy(self):
        denied=self.restored_parity('teacher','http.blog.post.delete',path_params={'post_id':self.post},query_params={'expected_updated_at':self.revision(self.post)})
        self.assertEqual(403,denied['result']['http_status'])
        deleted=self.restored_parity('student','http.blog.comment.delete',path_params={'comment_id':self.comment})
        self.assertEqual(200,deleted['result']['http_status']);self.assertEqual(2,deleted['result']['data']['deleted_count'])
        self.assertEqual(0,self.sql('SELECT comment_count FROM blog_posts WHERE id=?',(self.post,))[0][0])
        version=self.revision(self.post)
        stale=self.dispatch('student','http.blog.post.delete',path_params={'post_id':self.post},query_params={'expected_updated_at':'old'})
        self.assertEqual(409,stale['result']['http_status'])
        result=self.restored_parity('student','http.blog.post.delete',path_params={'post_id':self.post},query_params={'expected_updated_at':version})
        self.assertEqual(200,result['result']['http_status']);self.assertEqual([],self.sql('SELECT id FROM blog_posts WHERE id=?',(self.post,)))

    def test_post_and_comment_likes_deduplicate_request_and_obey_normal_visibility(self):
        for key,path in (('http.blog.post.like',{'post_id':self.post}),('http.blog.comment.like',{'comment_id':self.comment})):
            result=self.restored_parity('student',key,path_params=path)
            self.assertEqual(200,result['result']['http_status']);self.assertTrue(result['result']['data']['liked'])
            self.assertFalse(result['verified_business'])
        # A new operation can deliberately toggle. The same operation cannot.
        operation_id=str(uuid.uuid4())
        first=self.dispatch('student','http.blog.post.like',operation_id,path_params={'post_id':self.post})
        second=self.dispatch('student','http.blog.post.like',operation_id,path_params={'post_id':self.post})
        self.assertEqual(first,second);self.assertFalse(second['result']['data']['liked'])

    def test_reports_require_visible_target_and_admin_resolution_is_conditional(self):
        hidden=self.restored_parity('student','http.blog.report.create',body={'target_type':'post','target_id':self.private,'reason_code':'spam'})
        self.assertEqual(200,hidden['result']['http_status'])  # Own draft remains visible to its author.
        with self.connection() as conn:
            conn.execute("UPDATE blog_posts SET author_identity='student:9',author_user_pk=9 WHERE id=?",(self.private,));conn.commit()
        hidden=self.restored_parity('student','http.blog.report.create',body={'target_type':'post','target_id':self.private,'reason_code':'spam'})
        self.assertEqual(400,hidden['result']['http_status'])
        report=self.restored_parity('student','http.blog.report.create',body={'target_type':'comment','target_id':self.comment,'reason_code':'other','details':'Please review this public comment.'})
        self.assertEqual(200,report['result']['http_status']);report_id=report['result']['data']['report']['id']
        denied=self.restored_parity('student','http.blog.report.resolve',path_params={'report_id':report_id},body={'status':'resolved','notes':'Not an administrator'})
        self.assertEqual(403,denied['result']['http_status'])
        accepted=self.restored_parity('teacher','http.blog.report.resolve',path_params={'report_id':report_id},body={'status':'dismissed','notes':'Reviewed the original context.'})
        self.assertEqual(200,accepted['result']['http_status'])
        again=self.dispatch('teacher','http.blog.report.resolve',path_params={'report_id':report_id},body={'status':'resolved'})
        self.assertEqual(400,again['result']['http_status'])

    def test_edit_mention_preserves_durable_reply_effect_and_stale_edit_adds_no_job(self):
        version=self.revision(self.post)
        result=self.restored_parity('student','http.blog.post.update',path_params={'post_id':self.post},body={'expected_updated_at':version,'content_md':'@管家 请帮助核对这份总结。'})
        self.assertEqual(200,result['result']['http_status'])
        rows=self.sql("SELECT task_kind,status FROM scheduled_tasks WHERE task_kind='blog_mention_reply'")
        self.assertEqual([('blog_mention_reply','pending')],[tuple(row) for row in rows])
        rejected=self.dispatch('student','http.blog.post.update',path_params={'post_id':self.post},body={'expected_updated_at':version,'content_md':'@管家 旧编辑不应追加任务。'})
        self.assertEqual(409,rejected['result']['http_status'])
        self.assertEqual(1,len(self.sql("SELECT id FROM scheduled_tasks WHERE task_kind='blog_mention_reply'")))
        with self.connection() as conn:
            conn.execute("UPDATE blog_posts SET author_identity='student:9',author_user_pk=9 WHERE id=?",(self.post,));conn.commit()
        denied=self.restored_parity('student','http.blog.comment.delete',path_params={'comment_id':self.comment})
        self.assertEqual(403,denied['result']['http_status'])
        with self.assertRaises(HTTPException):self.dispatch('student','http.blog.comment.update',path_params={'comment_id':self.comment},body={'content_md':'No normal edit endpoint'})
