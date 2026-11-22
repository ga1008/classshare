import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from classroom_app.db.schema_interaction_expression import ensure_interaction_expression_schema
from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
from classroom_app.db import schema_scheduler
from classroom_app.services import interaction_expression_refresh as refresh
from classroom_app.services.learning_progress_service import build_student_class_position
from tests.test_interaction_expression_facts import SCHEMA


# Explicit types keep the same synthetic teaching records usable on both engines.
REFRESH_FIXTURE = """
                ALTER TABLE students ADD COLUMN class_id INTEGER;
                UPDATE students SET class_id=10 WHERE id IN (1,4);
                UPDATE students SET class_id=20 WHERE id=2;
                CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER);
                INSERT INTO class_offerings VALUES(7,10),(8,30);
                CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
                INSERT INTO class_offering_class_links VALUES(7,20);
                ALTER TABLE classroom_live_activities ADD COLUMN status TEXT;
                ALTER TABLE classroom_live_activities ADD COLUMN started_at TEXT;
                ALTER TABLE classroom_live_activities ADD COLUMN closed_at TEXT;
                ALTER TABLE study_groups ADD COLUMN archived_at TEXT;
                ALTER TABLE study_groups ADD COLUMN status TEXT;
                CREATE TABLE study_group_members(group_id INTEGER,student_id INTEGER,joined_at TEXT,left_at TEXT,status TEXT);
                CREATE TABLE polls(id INTEGER PRIMARY KEY,status TEXT,audience_scope TEXT,created_at TEXT,
                    updated_at TEXT,closed_at TEXT,deadline_at TEXT);
                CREATE TABLE poll_assignments(poll_id INTEGER,class_offering_id INTEGER,created_at TEXT);
                CREATE TABLE poll_participants(poll_id INTEGER,student_id INTEGER,created_at TEXT);
                CREATE TABLE poll_ballots(poll_id INTEGER,voter_id INTEGER,created_at TEXT);
                INSERT INTO ai_chat_sessions VALUES(10,7,1,'student');
                INSERT INTO ai_chat_messages VALUES(11,10,'user','课程问题','2026-11-21');
                INSERT INTO classroom_live_activities VALUES(12,7,'qna','active','2026-11-21',NULL);
                INSERT INTO classroom_live_questions VALUES(12,7,1,'2026-11-21','匿名原文','open',1);
"""


class ExpressionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.file = Path(self.enterContext(TemporaryDirectory())) / 'fixture.sqlite'
        self.now = datetime(2026, 11, 22, 10)
        self.enterContext(patch.object(refresh, 'local_now', side_effect=lambda: self.now))
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(REFRESH_FIXTURE)
            ensure_interaction_expression_schema(conn)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.file)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def state(self, conn, offering=7):
        return dict(conn.execute('SELECT * FROM interaction_expression_refresh_state WHERE class_offering_id=?',
                                 (offering,)).fetchone())

    def snapshot(self, conn, student=1):
        return dict(conn.execute('SELECT * FROM interaction_expression_snapshots WHERE class_offering_id=7 AND student_id=?',
                                 (student,)).fetchone())

    def test_backfill_fair_rotation_unchanged_rows_withdrawal_and_departure(self):
        seen = refresh.refresh_due_offerings(connection_factory=self.connect, limit=1)
        self.assertEqual((1, 2), (seen['published'], seen['students']))
        with self.connect() as conn:
            row = self.snapshot(conn)
            self.assertEqual((2, 1), tuple(json.loads(row[view])['facts']['expression_count']
                                          for view in ('self_json', 'teacher_json')))
            self.assertNotIn('匿名原文', row['self_json'])
            self.assertEqual([8], refresh._due_offerings(conn, self.now, 6))
        self.assertEqual(1, refresh.refresh_due_offerings(connection_factory=self.connect, limit=1)['published'])
        self.now += timedelta(seconds=refresh.REFRESH_SECONDS + 1)
        self.assertEqual(2, refresh.refresh_due_offerings(connection_factory=self.connect)['published'])
        with self.connect() as conn:
            self.assertEqual(row, self.snapshot(conn))
            self.assertNotEqual(row['calculated_at'], self.state(conn)['calculated_at'])
            conn.execute("UPDATE classroom_live_questions SET status='hidden'")
            conn.execute('DELETE FROM class_offering_class_links')
            result = refresh.refresh_offering_expression(conn, 7)
            self.assertEqual({'status': 'published', 'students': 1}, result)
            self.assertEqual(1, json.loads(self.snapshot(conn)['self_json'])['facts']['expression_count'])
            self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM interaction_expression_snapshots').fetchone()[0])

    def test_missing_source_preserves_published_course_then_retries_and_doesnt_starve_others(self):
        with self.connect() as conn:
            refresh.refresh_offering_expression(conn, 7)
            row = self.snapshot(conn)
            old_state = self.state(conn)
            conn.execute('ALTER TABLE ai_workspace_course_questions RENAME TO temporarily_unavailable')
        self.now += timedelta(seconds=refresh.REFRESH_SECONDS + 1)
        result = refresh.refresh_due_offerings(connection_factory=self.connect)
        self.assertEqual((1, 1), (result['published'], result['retry']))
        with self.connect() as conn:
            self.assertEqual(row, self.snapshot(conn))
            self.assertEqual(old_state['calculated_at'], self.state(conn)['calculated_at'])
            self.assertEqual('', self.state(conn)['lease_token'])
            conn.execute('ALTER TABLE temporarily_unavailable RENAME TO ai_workspace_course_questions')
        self.assertEqual(0, refresh.refresh_due_offerings(connection_factory=self.connect)['published'])
        self.now += timedelta(seconds=refresh.RETRY_SECONDS + 1)
        self.assertEqual(1, refresh.refresh_due_offerings(connection_factory=self.connect)['published'])

    def test_expired_slow_worker_cannot_publish_or_defer_a_newer_claim(self):
        with self.connect() as conn:
            first = refresh._claim(conn, 7, self.now, force=True)
        with self.connect() as conn:
            self.assertEqual('busy', refresh.refresh_offering_expression(conn, 7)['status'])
        self.now += timedelta(seconds=refresh.LEASE_SECONDS + 1)
        with self.connect() as conn:
            second = refresh._claim(conn, 7, self.now, force=True)
            self.assertNotEqual(first, second)
            self.assertEqual('superseded', refresh._publish(conn, 7, first, {}, calculated_at=self.now, now=self.now)['status'])
            refresh._defer(conn, 7, first, self.now)
            self.assertEqual(second, self.state(conn)['lease_token'])
            self.assertEqual('published', refresh._publish(conn, 7, second, {}, calculated_at=self.now, now=self.now)['status'])

    def test_historical_expression_replay_preserves_actual_rank_and_cultivation_history(self):
        with self.connect() as conn:
            ensure_cultivation_progress_schema(conn, engine='sqlite')
            conn.executescript("""
                CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO classes VALUES(10,'主班'),(20,'合班');
                ALTER TABLE students ADD COLUMN student_id_number TEXT;
                ALTER TABLE students ADD COLUMN name TEXT;
                UPDATE students SET student_id_number='002',name='甲' WHERE id=1;
                UPDATE students SET student_id_number='001',name='乙' WHERE id=2;
                CREATE TABLE learning_certificates(class_offering_id INTEGER,student_id INTEGER,tier INTEGER);
                INSERT INTO learning_certificates VALUES(7,2,3);
                INSERT INTO cultivation_rank_scopes VALUES(7,'published','2026-12-01');
                INSERT INTO learning_progress_snapshots(class_offering_id,student_id,score,rank_basis,components_json)
                  VALUES(7,1,68,'published','{"interaction":4}'),(7,2,68,'published','{"interaction":7}');
                INSERT INTO cultivation_score_events(class_offering_id,student_id,event_type,delta) VALUES(7,1,'gain',4);
                INSERT INTO cultivation_weekly_snapshots(class_offering_id,student_id,week_start,week_end,score)
                  VALUES(7,1,'2026-11-09','2026-11-15',64);
            """)
            tables = ('learning_progress_snapshots', 'cultivation_rank_scopes', 'cultivation_score_events',
                      'cultivation_weekly_snapshots', 'learning_certificates')
            def published_state():
                return {table: list(conn.execute(f'SELECT * FROM {table} ORDER BY rowid')) for table in tables}

            original = published_state()
            rank = build_student_class_position(conn, 7, 1)
            self.assertEqual((2, 68, '乙'), (rank['current']['rank'], rank['current']['score'], rank['leader']['name']))
            for _ in range(2):
                self.assertEqual('published', refresh.refresh_offering_expression(conn, 7)['status'])
                self.assertGreater(json.loads(self.snapshot(conn)['self_json'])['facts']['expression_count'], 0)
                self.assertEqual(original, published_state())
                self.assertEqual(rank, build_student_class_position(conn, 7, 1))

    def test_failed_student_insert_rolls_back_whole_class_and_old_results_survive(self):
        with self.connect() as conn:
            refresh.refresh_offering_expression(conn, 7)
            row = self.snapshot(conn)
            old_state = self.state(conn)
            conn.execute("INSERT INTO ai_chat_messages VALUES(20,10,'user','明日问题','2026-11-22')")
            conn.execute("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON interaction_expression_snapshots
                            WHEN NEW.student_id=2 BEGIN SELECT RAISE(ABORT,'fixture failure'); END""")
        self.now += timedelta(seconds=refresh.REFRESH_SECONDS + 1)
        result = refresh.refresh_due_offerings(connection_factory=self.connect)
        self.assertEqual(1, result['retry'])
        with self.connect() as conn:
            self.assertEqual(row, self.snapshot(conn))
            self.assertEqual(old_state['calculated_at'], self.state(conn)['calculated_at'])
            self.assertEqual('', self.state(conn)['lease_token'])
            # A migration caller can keep its outer transaction alive after an
            # exception, without accidentally publishing a prefix of the class.
            with self.assertRaises(sqlite3.IntegrityError):
                refresh.refresh_offering_expression(conn, 7)
            self.assertEqual(row, self.snapshot(conn))
            self.assertEqual(old_state['calculated_at'], self.state(conn)['calculated_at'])

    def test_startup_dedupes_preserves_an_active_lease_and_rearms_its_failed_timer(self):
        with patch.object(schema_scheduler, '_SCHEMA_READY', False), self.connect() as conn:
            timer = refresh.ensure_interaction_expression_refresh_task(conn)
            conn.execute("UPDATE scheduled_tasks SET status='running',locked_by='other' WHERE id=?", (timer,))
            self.assertEqual(timer, refresh.ensure_interaction_expression_refresh_task(conn))
            row = conn.execute('SELECT status,locked_by FROM scheduled_tasks WHERE id=?', (timer,)).fetchone()
            self.assertEqual(('running', 'other'), tuple(row))
            conn.execute("UPDATE scheduled_tasks SET status='failed',attempt_count=5 WHERE id=?", (timer,))
            self.assertEqual(timer, refresh.ensure_interaction_expression_refresh_task(conn))
            row = conn.execute('SELECT status,attempt_count,recurrence_seconds FROM scheduled_tasks WHERE id=?', (timer,)).fetchone()
            self.assertEqual(('pending', 0, refresh.TICK_SECONDS), tuple(row))
