"""Current-user profile/portfolio parity with actual normal HTTP and SQL."""
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers import profile
from tests.test_agent_platform_requests import PlatformRequestFixture


class AgentPlatformRequestProfileTests(PlatformRequestFixture):
    def setUp(self):
        super().setUp()
        self.patched('classroom_app.routers.profile.get_db_connection',self.connection)
        self.patched('classroom_app.services.profile_service._now_iso',return_value='2026-09-10T10:00:00')
        self.patched('classroom_app.services.portfolio_service._now_iso',return_value='2026-09-10T10:00:00')
        with self.connection() as conn:
            for table in ('teachers','students'):
                for field in ('email','phone','wechat','qq','homepage_url','password_updated_at','profile_info','nickname',
                              'description','identity_category','avatar_file_hash','avatar_mime_type','avatar_updated_at',
                              'today_mood','today_mood_updated_at','created_at','gender'):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT DEFAULT ''")
            conn.executescript("""
              ALTER TABLE blog_posts ADD COLUMN author_role TEXT;
              ALTER TABLE blog_posts ADD COLUMN author_user_pk INTEGER;
              ALTER TABLE blog_posts ADD COLUMN title TEXT;
              ALTER TABLE blog_posts ADD COLUMN content_md TEXT;
              ALTER TABLE blog_posts ADD COLUMN summary TEXT;
              ALTER TABLE blog_posts ADD COLUMN created_at TEXT;
              ALTER TABLE blog_posts ADD COLUMN updated_at TEXT;
              INSERT INTO blog_posts(id,author_identity,status,visibility,author_role,author_user_pk,title,summary,content_md,created_at,updated_at) VALUES
                (10,'student:7','published','private','student',7,'My learning','Own summary','Own article','2026-09-09','2026-09-09'),
                (11,'student:8','published','private','student',8,'Other private learning','Other summary','Other article','2026-09-09','2026-09-09');
              CREATE TABLE assignments(id INTEGER PRIMARY KEY,title TEXT,course_id INTEGER,class_offering_id INTEGER,exam_paper_id INTEGER);
              CREATE TABLE submissions(id INTEGER PRIMARY KEY,assignment_id INTEGER,student_pk_id INTEGER,status TEXT,score REAL,feedback_md TEXT,submitted_at TEXT,is_absence_score INTEGER);
              CREATE TABLE learning_certificates(id INTEGER PRIMARY KEY,class_offering_id INTEGER,student_id INTEGER,level_name TEXT,tier INTEGER,title TEXT,certificate_code TEXT,issued_at TEXT);
              CREATE TABLE learning_material_progress(class_offering_id INTEGER,student_id INTEGER,material_id INTEGER,completed INTEGER,active_seconds INTEGER);
              CREATE TABLE student_feedback_review_notes(id INTEGER PRIMARY KEY,student_id INTEGER,submission_id INTEGER,question_key TEXT,status TEXT,reviewed_at TEXT,mastered_at TEXT,updated_at TEXT);
              CREATE TABLE student_portfolio_items(id INTEGER PRIMARY KEY AUTOINCREMENT,student_id INTEGER NOT NULL,class_offering_id INTEGER,course_id INTEGER,
                source_type TEXT,source_id TEXT,title TEXT,summary TEXT,artifact_type TEXT,visibility TEXT,featured INTEGER DEFAULT 0,teacher_recommended INTEGER DEFAULT 0,
                teacher_recommended_by INTEGER,teacher_recommended_at TEXT,sort_order INTEGER,created_at TEXT,updated_at TEXT,metadata_json TEXT,
                UNIQUE(student_id,source_type,source_id));
              CREATE TABLE student_portfolio_reflections(id INTEGER PRIMARY KEY AUTOINCREMENT,portfolio_item_id INTEGER UNIQUE,student_id INTEGER,reflection_text TEXT,
                ability_tags_json TEXT,evidence_notes TEXT,updated_at TEXT);
              CREATE TABLE student_growth_events(id INTEGER PRIMARY KEY AUTOINCREMENT,student_id INTEGER,class_offering_id INTEGER,event_type TEXT,source_type TEXT,source_id TEXT,
                title TEXT,description TEXT,occurred_at TEXT,importance TEXT,metadata_json TEXT);
            """)
            conn.commit()
        self.app.include_router(profile.router)

    def test_portfolio_read_and_add_only_own_source_then_record_growth_event(self):
        result=self.restored_parity('student','http.profile.portfolio.read')
        self.assertEqual(200,result['result']['http_status'])
        self.assertEqual(['10'],[item['source_id'] for item in result['result']['data']['portfolio']['candidates']])
        denied=self.restored_parity('teacher','http.profile.portfolio.read')
        self.assertEqual(403,denied['result']['http_status'])
        denied=self.restored_parity('student','http.profile.portfolio.add',body={'source_type':'blog_post','source_id':11})
        self.assertEqual(400,denied['result']['http_status'])
        added=self.restored_parity('student','http.profile.portfolio.add',body={'source_type':'blog_post','source_id':10,'featured':True})
        self.assertEqual(200,added['result']['http_status'])
        row=self.sql('SELECT student_id,source_id,visibility,featured FROM student_portfolio_items')[0]
        self.assertEqual((7,'10','private',1),tuple(row))
        self.assertEqual('portfolio_added',self.sql('SELECT event_type FROM student_growth_events')[0][0])

    def test_portfolio_update_full_reflection_and_remove_preserve_owner_bounds(self):
        created=self.dispatch('student','http.profile.portfolio.add',body={'source_type':'blog_post','source_id':10})
        item_id=created['result']['data']['id']
        payload={'title':'Reviewed learning','summary':'Summary\nSecond line','visibility':'teachers','featured':True,'sort_order':1,
                 'reflection':'A concrete reflection','ability_tags':'表达总结,复盘改进','evidence_notes':'Own article'}
        updated=self.restored_parity('student','http.profile.portfolio.update',path_params={'item_id':item_id},body=payload)
        self.assertEqual(200,updated['result']['http_status'])
        self.assertEqual('teachers',self.sql('SELECT visibility FROM student_portfolio_items')[0][0])
        self.assertEqual('A concrete reflection',self.sql('SELECT reflection_text FROM student_portfolio_reflections')[0][0])
        with self.assertRaises(HTTPException) as missing:
            self.dispatch('student','http.profile.portfolio.update',path_params={'item_id':item_id},body={'title':'Do not erase reflection'})
        self.assertEqual(400,missing.exception.status_code)
        denied=self.restored_parity('teacher','http.profile.portfolio.remove',path_params={'item_id':item_id})
        self.assertEqual(403,denied['result']['http_status'])
        removed=self.restored_parity('student','http.profile.portfolio.remove',path_params={'item_id':item_id})
        self.assertEqual(200,removed['result']['http_status'])
        self.assertEqual([],self.sql('SELECT id FROM student_portfolio_items'))
        self.assertEqual([],self.sql('SELECT id FROM student_portfolio_reflections'))
        self.assertEqual(['portfolio_added','portfolio_removed'],[row[0] for row in self.sql('SELECT event_type FROM student_growth_events ORDER BY id')])

    def test_same_numbered_teacher_and_student_mood_match_normal_web_and_clear(self):
        for role in ('teacher','student'):
            result=self.restored_parity(role,'http.profile.mood.update',body={'mood':role+' synthetic mood'})
            self.assertEqual(200,result['result']['http_status'])
            self.assertEqual(role,result['result']['data']['profile']['role'])
        self.assertEqual('teacher synthetic mood',self.sql('SELECT today_mood FROM teachers WHERE id=7')[0][0])
        self.assertEqual('student synthetic mood',self.sql('SELECT today_mood FROM students WHERE id=7')[0][0])
        self.restored_parity('student','http.profile.mood.update',body={'mood':''})
        self.assertEqual('',self.sql('SELECT today_mood FROM students WHERE id=7')[0][0])
        for key in ('/api/profile/password','http.profile.email-configs','http.profile.identities'):
            with self.assertRaises(HTTPException):self.dispatch('student',key)
