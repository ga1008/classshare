import json
import sqlite3
import unittest
from contextlib import closing

from classroom_app.services.interaction_expression_facts import load_expression_facts


SCHEMA = """
            CREATE TABLE students(id INTEGER PRIMARY KEY,enrollment_status TEXT,control_mode TEXT DEFAULT 'human');
            INSERT INTO students(id,enrollment_status) VALUES(1,'active'),(2,'active'),(3,'active'),(4,'wanderer');
            CREATE TABLE teachers(id INTEGER PRIMARY KEY);
            INSERT INTO teachers VALUES(1);
            CREATE TABLE wanderer_agents(student_id INTEGER PRIMARY KEY);
            CREATE TABLE chat_logs(id INTEGER PRIMARY KEY,class_offering_id INTEGER,user_id TEXT,user_role TEXT,
                logged_at TEXT,message TEXT,quote_message_id INTEGER,message_type TEXT);
            CREATE TABLE study_groups(id INTEGER PRIMARY KEY,class_offering_id INTEGER,assignment_id TEXT,updated_at TEXT);
            CREATE TABLE group_chat_messages(group_id INTEGER,sender_role TEXT,sender_user_pk INTEGER,created_at TEXT,
                content TEXT,message_type TEXT,recalled_at TEXT);
            CREATE TABLE ai_chat_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,user_pk INTEGER,user_role TEXT);
            CREATE TABLE ai_chat_messages(id INTEGER PRIMARY KEY,session_id INTEGER,role TEXT,message TEXT,timestamp TEXT);
            CREATE TABLE ai_workspace_sessions(id INTEGER PRIMARY KEY,user_pk INTEGER,user_role TEXT);
            CREATE TABLE ai_workspace_messages(id INTEGER PRIMARY KEY,session_id INTEGER,role TEXT,message TEXT,created_at TEXT,request_id TEXT);
            CREATE TABLE ai_workspace_course_questions(message_id INTEGER PRIMARY KEY,class_offering_id INTEGER,student_id INTEGER);
            CREATE TABLE classroom_live_activities(id INTEGER PRIMARY KEY,class_offering_id INTEGER,kind TEXT);
            CREATE TABLE classroom_live_responses(activity_id INTEGER,student_id INTEGER,created_at TEXT,response_text TEXT,is_anonymous INTEGER);
            CREATE TABLE classroom_live_questions(activity_id INTEGER,class_offering_id INTEGER,student_id INTEGER,created_at TEXT,
                question_text TEXT,status TEXT,is_anonymous INTEGER);
            CREATE TABLE peer_reviews(class_offering_id INTEGER,group_id INTEGER,assignment_id TEXT,reviewer_student_id INTEGER,
                reviewee_student_id INTEGER,created_at TEXT,comment TEXT,status TEXT,responsibility_score INTEGER,
                collaboration_score INTEGER,quality_score INTEGER,is_auto_default INTEGER);
            CREATE TABLE classroom_behavior_events(class_offering_id INTEGER,user_pk INTEGER,action_type TEXT);
"""


class InteractionExpressionFactsTests(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(sqlite3.connect(':memory:')))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def facts(self, **options):
        return load_expression_facts(self.db, 7, [1], **options)[1]

    def test_ai_uses_attributed_user_messages_without_imports_telemetry_or_other_courses(self):
        self.db.executescript("""
            INSERT INTO ai_chat_sessions VALUES(10,7,1,'student'),(11,8,1,'student'),(12,7,1,'teacher');
            INSERT INTO ai_chat_messages VALUES
              (1,10,'user','子网如何划分？','2026-11-01'),(2,10,'user','追问该例子','2026-11-01'),
              (3,10,'assistant','不应计数','2026-11-02'),(4,11,'user','跨课','2026-11-02'),
              (5,12,'user','教师','2026-11-02');
            INSERT INTO classroom_behavior_events VALUES(7,1,'ai_question'),(7,1,'ai_question');
            INSERT INTO ai_workspace_sessions VALUES(41,1,'student'),(42,2,'student');
            INSERT INTO ai_workspace_messages VALUES
              (1,41,'user','私有未归课','2026-11-02','a1'),(2,41,'user','解释协议','2026-11-03','a2'),
              (3,41,'user','课堂约束','2026-11-03','a3'),(4,41,'user','导入','2026-11-04','legacy:1'),
              (5,42,'user','另一个人','2026-11-05','b1'),(6,41,'assistant','回答','2026-11-06','a2');
            INSERT INTO ai_workspace_course_questions VALUES(2,7,1),(3,7,1),(4,7,1),(5,7,1),(6,7,1);
        """)
        self.assertEqual(2, self.facts()['expression_count'])
        own = self.facts(include_private=True)
        self.assertEqual((2, 2, 2), (own['participation_count'], own['expression_count'], own['expression_days']))
        self.assertEqual(1, own['source_counts']['legacy_ai'])
        self.assertEqual(1, own['source_counts']['workspace_ai'])
        self.assertEqual((4, 2, 2), (own['ai_message_count'], own['legacy_ai_message_count'],
                                    own['workspace_ai_message_count']))
        self.assertNotIn('子网', json.dumps(own, ensure_ascii=False))
        self.db.execute('DELETE FROM ai_workspace_course_questions WHERE message_id IN (2,3)')
        self.assertEqual(1, self.facts(include_private=True)['expression_count'])

    def test_free_discussion_and_a_course_targeted_reply_are_distinct_and_retries_share_the_target(self):
        self.db.executescript("""
            INSERT INTO chat_logs VALUES
              (11,7,'2','student','2026-11-01','问题',NULL,'text'),
              (12,8,'2','student','2026-11-01','跨课提问',NULL,'text'),
              (13,7,'1','student','2026-11-01','想问这个',NULL,'text'),
              (14,7,'1','student','2026-11-01','重复自己',13,'rich'),
              (15,7,'1','student','2026-11-01','坏的跨课引用',12,'rich'),
              (16,7,'1','student','2026-11-01','回应',11,'rich'),
              (17,7,'1','student','2026-11-01','补充',11,'rich'),
              (18,7,'1','student','2026-11-02','☀️',11,'rich');
        """)
        result = self.facts()
        self.assertEqual((2, 1), (result['participation_count'], result['expression_count']))
        self.assertEqual(1, result['source_counts']['discussion_reply'])
        self.assertEqual(1, result['source_counts']['discussion'])

    def test_generated_upload_prompts_only_count_as_participation_unless_student_adds_their_own_text(self):
        self.db.executescript("""
            INSERT INTO ai_chat_sessions VALUES(10,7,1,'student');
            INSERT INTO ai_chat_messages VALUES
              (1,10,'user','请结合我上传的图片内容帮我分析或回答。','2026-11-01');
            INSERT INTO ai_workspace_sessions VALUES(41,1,'student');
            INSERT INTO ai_workspace_messages VALUES
              (2,41,'user','请根据当前附件回答，并说明图片中的关键内容。','2026-11-02','a1');
            INSERT INTO ai_workspace_course_questions VALUES (2,7,1);
        """)
        result = self.facts()
        self.assertEqual((2, 0, 2), (result['participation_count'], result['expression_count'], result['ai_message_count']))
        for name in ('legacy_ai', 'workspace_ai'):
            self.assertEqual((1, 0), (result['source_counts'][name], result['expression_source_counts'][name]))
        self.db.execute("INSERT INTO ai_chat_messages VALUES(3,10,'user','为什么这里选择全双工？','2026-11-01')")
        result = self.facts()
        self.assertEqual((2, 1, 1), (result['participation_count'], result['expression_count'],
                                     result['expression_source_counts']['legacy_ai']))

    def test_free_group_needs_two_people_with_text_on_the_same_day_and_recall_reverses_it(self):
        self.db.executescript("""
            INSERT INTO study_groups VALUES (11,7,NULL,'2026-10-01'),(12,7,'exam','2026-10-01'),(13,8,NULL,'2026-10-01');
            INSERT INTO group_chat_messages VALUES
              (11,'student',1,'2026-11-01','这是实验结果','text',NULL),
              (11,'student',1,'2026-11-01','再次说明','text',NULL),
              (11,'student',2,'2026-11-01','收到结果','text',NULL),
              (11,'student',3,'2026-11-01','🆗','text',NULL),
              (11,'student',4,'2026-11-01','自动消息','text',NULL),
              (11,'student',1,'2026-11-02','补充说明','text',NULL),
              (11,'student',2,'2026-11-02','不算贴纸','sticker',NULL),
              (11,'teacher',2,'2026-11-02','教师','text',NULL),
              (12,'student',1,'2026-11-02','组内作业','text',NULL),
              (13,'student',1,'2026-11-01','跨课','text',NULL);
        """)
        before = self.facts()
        self.assertEqual((3, 2), (before['participation_count'], before['expression_count']))
        for source in ('group_discussion', 'group_exchange', 'group_task_discussion'):
            self.assertEqual(1, before['source_counts'][source])
        self.db.execute("UPDATE group_chat_messages SET recalled_at='2026-11-03' WHERE sender_user_pk=2")
        after = self.facts()
        self.assertEqual((3, 1, 0), (after['participation_count'], after['expression_count'],
                                     after['source_counts']['group_exchange']))
        self.db.execute("UPDATE study_groups SET assignment_id='later-task',updated_at='2026-12-01' WHERE id=11")
        self.assertEqual(1, self.facts()['expression_count'])

    def test_qna_response_and_review_visibility_and_work_units(self):
        self.db.executescript("""
            INSERT INTO classroom_live_activities VALUES(101,7,'qna'),(102,7,'quiz'),(103,7,'quiz'),(104,7,'qna');
            INSERT INTO classroom_live_questions VALUES
              (101,7,1,'2026-11-01','疑问','open',1),(101,7,1,'2026-11-01','追问','addressed',1),
              (104,7,1,'2026-11-01','已隐藏','hidden',0);
            INSERT INTO classroom_live_responses VALUES(102,1,'2026-11-01','',0),(103,1,'2026-11-01','另附说明',1);
            INSERT INTO study_groups VALUES(11,7,'a','2026-10-01');
            INSERT INTO peer_reviews VALUES
              (7,11,'a',1,2,'2026-11-01','有人工文字且贡献分已自动补齐','submitted',4,5,3,1),
              (7,11,'a',1,3,'2026-11-01','同任务给第二位同学的文字','submitted',5,4,5,0),
              (7,11,'b',1,2,'2026-11-02','','submitted',0,0,0,1),
              (7,11,'c',2,1,'2026-11-02','收到的评语','submitted',5,5,5,0);
        """)
        public = self.facts()
        self.assertEqual((1, 0), (public['participation_count'], public['expression_count']))
        own = self.facts(include_anonymous=True, include_private=True)
        self.assertEqual((4, 3, 1), (own['participation_count'], own['expression_count'], own['expression_days']))
        self.assertEqual(1, own['source_counts']['peer_review'])
        self.db.execute("UPDATE classroom_live_questions SET status='hidden'")
        self.assertEqual(2, self.facts(include_anonymous=True, include_private=True)['expression_count'])

    def test_partial_legacy_schema_is_reported_and_repeated_roster_ids_do_not_add_work(self):
        self.db.execute('DROP TABLE ai_workspace_course_questions')
        result = load_expression_facts(self.db, 7, [1, 1, 2], include_private=True)
        self.assertEqual({1, 2}, set(result))
        self.assertEqual(['workspace_ai'], result[1]['unavailable_sources'])
        self.assertEqual({}, load_expression_facts(self.db, 7, []))
        self.assertIn('workspace_ai', self.facts()['unavailable_sources'])

    def test_group_exchange_works_across_roster_batch_boundaries(self):
        self.db.executescript("""
            INSERT INTO students(id,enrollment_status) VALUES(300, 'active');
            INSERT INTO study_groups VALUES(14,7,NULL,'2026-10-01');
            INSERT INTO group_chat_messages VALUES(14,'student',1,'2026-12-01','甲','text',NULL),
                                                  (14,'student',300,'2026-12-01','乙','text',NULL);
        """)
        facts = load_expression_facts(self.db, 7, range(1, 301))
        self.assertEqual((1, 1), (facts[1]['expression_count'], facts[300]['expression_count']))

    def test_quoted_authors_and_group_partners_must_be_real_humans(self):
        self.db.executescript("""
            INSERT INTO students VALUES(5,'active','ai'),(6,'active','human');
            INSERT INTO wanderer_agents VALUES(6);
            INSERT INTO chat_logs VALUES
              (11,7,'4','student','2026-11-01','机器人1',NULL,'text'),
              (12,7,'5','student','2026-11-01','机器人2',NULL,'text'),
              (13,7,'6','student','2026-11-01','机器人3',NULL,'text'),
              (14,7,'匿名别名','student','2026-11-01','旧匿名未映射',NULL,'text'),
              (15,7,'999','student','2026-11-01','旧数据数字无账号',NULL,'text'),
              (16,7,'2','student','2026-11-01','已撤回类型',NULL,'recalled'),
              (17,7,'1','teacher','2026-11-01','同 PK 不同身份',NULL,'text'),
              (18,7,'999','teacher','2026-11-01','未映射教师',NULL,'text'),
              (19,7,'2','system','2026-11-01','系统事件',NULL,'text');
            INSERT INTO chat_logs SELECT 100+id,7,'1','student',logged_at,'回应',id,'rich' FROM chat_logs;
            INSERT INTO study_groups VALUES(11,7,NULL,'2026-10-01');
            INSERT INTO group_chat_messages SELECT 11,'student',id,'2026-11-01','文本','text',NULL
              FROM students WHERE id IN (1,4,5,6);
        """)
        result = self.facts()
        self.assertEqual((1, 0), (result['source_counts']['discussion_reply'], result['source_counts']['group_exchange']))
        self.assertEqual(1, result['expression_count'])
        self.db.execute('DELETE FROM chat_logs WHERE id=17')
        self.assertEqual(0, self.facts()['expression_count'])
