"""Real login/tip read units and mutation commits on an owned SQLite DB."""
from contextlib import ExitStack
import asyncio
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from tests.sqlite_database_fixture import isolated_sqlite_database
from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.routers.ui_parts import common,life_tip_pages
from classroom_app.routers import learning
from classroom_app.services import life_tip_service as tips


class LifeTipLoginUnitsTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(isolated_sqlite_database())
        self.stack.enter_context(patch.object(tips,'_seeded',False))
        tips.invalidate_pool_cache()
        with database.get_db_connection() as conn:
            teacher=execute_insert_returning_id(conn,"INSERT INTO teachers(name,email,hashed_password,school_code,department) VALUES('Synthetic teacher','life-tip@example.invalid','unused','owned','computing')",())
            school=execute_insert_returning_id(conn,"INSERT INTO classes(name,created_by_teacher_id) VALUES('Owned tip class',?)",(teacher,))
            student=execute_insert_returning_id(conn,"INSERT INTO students(student_id_number,name,class_id,school_code,department) VALUES('TIP-SYNTHETIC','Synthetic student',?,'owned','computing')",(school,))
            self.student=dict(conn.execute('SELECT * FROM students WHERE id=?',(student,)).fetchone())
            self.teacher={'id':teacher,'role':'teacher','name':'Synthetic teacher'}
            conn.commit()
        self.real_factory=database.get_db_connection
        self.slot=threading.BoundedSemaphore(1)
        self.active=self.peak=self.borrowed=0
        owner=self
        class Tracked:
            def __init__(self,conn):self.conn,self.closed=conn,False
            def __getattr__(self,key):return getattr(self.conn,key)
            def close(self):
                if not self.closed:
                    self.closed=True
                    try:self.conn.close()
                    finally:owner.active-=1;owner.slot.release()
            def __enter__(self):return self
            def __exit__(self,kind,value,tb):
                try:self.conn.rollback() if kind else self.conn.commit()
                finally:self.close()
        def factory():
            if not self.slot.acquire(blocking=False):raise AssertionError('Nested tip/profile pool checkout')
            self.active+=1;self.peak=max(self.peak,self.active);self.borrowed+=1
            try:return Tracked(self.real_factory())
            except BaseException:self.active-=1;self.slot.release();raise
        for module in (database,common,learning,life_tip_pages):
            self.stack.enter_context(patch.object(module,'get_db_connection',factory))

    def test_json_login_closes_profile_connection_before_tip_warm_or_read(self):
        response=common._build_student_login_json_response(student_row=self.student,client_ip='127.0.0.1',
            safe_next='/student/dashboard',login_count=1,access_token='owned-test-existing-token')
        payload=json.loads(response.body)
        self.assertEqual(3,len(payload['login_tip']['tips']))
        self.assertEqual((1,0),(self.peak,self.active));self.assertGreaterEqual(self.borrowed,3)
        self.assertTrue(all({'id','category','text','source_ref','image_url'}<=set(tip) for tip in payload['login_tip']['tips']))

    def test_student_teacher_reveal_preserves_three_tips_without_nested_checkouts(self):
        for user in ({'id':self.student['id'],'role':'student'},self.teacher):
            result=learning.get_cultivation_profile(include_tip=1,include_rank=0,user=user)
            self.assertEqual(3,len(result['login_tip']['tips']))
        self.assertEqual((1,0),(self.peak,self.active))

    def test_real_manual_commit_and_feedback_invalidate_owned_pool(self):
        tips.warm_life_tip_runtime()
        before=tips._get_committed_pool(school_code='owned',department='computing',audience_role='student')
        original={tip['id'] for tip in before}
        created=asyncio.run(life_tip_pages.api_manage_life_tips_create(tip_text='合成提示：先保留输入，再核对本次实验的具体观察。',
            category='学业规则',scope='global',department='',audience='student',user=self.teacher))
        self.assertEqual('success',created['status'])
        after=tips._get_committed_pool(school_code='owned',department='computing',audience_role='student')
        added={tip['id'] for tip in after}-original
        self.assertEqual(1,len(added));tip_id=added.pop()
        voted=asyncio.run(learning.submit_life_tip_feedback(payload=learning.LifeTipFeedbackRequest(tip_id=tip_id,verdict=-1),
            user={'id':self.student['id'],'role':'student'}))
        self.assertEqual(0,voted['weight'])
        self.assertNotIn(tip_id,{tip['id'] for tip in tips._get_committed_pool(school_code='owned',department='computing',audience_role='student')})
        self.assertEqual((1,0),(self.peak,self.active))


class LifeTipStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_warmup_is_off_loop_before_the_next_startup_connection(self):
        from classroom_app import app as application
        entered,release=asyncio.Event(),threading.Event()
        loop=asyncio.get_running_loop()
        main_thread=threading.get_ident();worker_threads=[]
        def warm():
            worker_threads.append(threading.get_ident());loop.call_soon_threadsafe(entered.set)
            if not release.wait(20):raise AssertionError('warm release missing')
            return True
        attendance_connection=MagicMock()
        with patch.object(application,'ensure_runtime_directories'),patch.object(application,'init_database'), \
             patch.object(application.presence_service,'start_presence_runtime'), \
             patch('classroom_app.services.login_attendance_jobs.ensure_tasks') as attendance_tasks, \
             patch.object(tips,'warm_life_tip_runtime',side_effect=warm), \
             patch.object(application,'get_db_connection',side_effect=[attendance_connection,RuntimeError('next startup unit')]) as borrow:
            task=asyncio.create_task(application.startup_event())
            entered_task=asyncio.create_task(entered.wait())
            try:
                done,_=await asyncio.wait((task,entered_task),timeout=10,return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task  # Report an unexpected early startup failure directly.
                self.assertIn(entered_task,done)
                self.assertEqual(1,borrow.call_count)  # Attendance initializes before tip warmup.
                attendance_tasks.assert_called_once_with(attendance_connection.__enter__.return_value)
                attendance_connection.__enter__.return_value.commit.assert_called_once()
                self.assertNotEqual(main_thread,worker_threads[0])
            finally:
                release.set()
                if not entered_task.done():
                    entered_task.cancel()
            with self.assertRaisesRegex(RuntimeError,'next startup unit'):await task


if __name__=='__main__':unittest.main()
