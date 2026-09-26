"""Published snapshot -> teacher/student deck, using real scoped SQLite reads."""
from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from classroom_app import config
from classroom_app.db.schema_academic_schedule_predictions import ensure_academic_schedule_prediction_schema
from classroom_app.services.academic_schedule_overview_service import build_academic_prediction_overview
from classroom_app.services.academic_schedule_prediction_service import (
    claim_schedule_sync, reconcile_and_publish_snapshot, release_schedule_sync,
)
from classroom_app.services.student_course_schedule_service import build_student_course_schedule_overview
from tests.test_academic_schedule_predictions import SCHEMA, base_snapshot, request, official, slot


EXTRA_SCHEMA = """
ALTER TABLE teachers ADD COLUMN school_name TEXT DEFAULT '';
ALTER TABLE teachers ADD COLUMN college TEXT DEFAULT '';
ALTER TABLE teachers ADD COLUMN department TEXT DEFAULT '';
ALTER TABLE teachers ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE academic_semesters ADD COLUMN school_name TEXT DEFAULT '';
ALTER TABLE academic_semesters ADD COLUMN calendar_sync_status TEXT DEFAULT '';
ALTER TABLE academic_semesters ADD COLUMN calendar_sync_at TEXT DEFAULT '';
ALTER TABLE academic_semesters ADD COLUMN calendar_sync_message TEXT DEFAULT '';
ALTER TABLE academic_semesters ADD COLUMN calendar_source_summary_json TEXT DEFAULT '[]';
ALTER TABLE academic_semesters ADD COLUMN created_at TEXT DEFAULT '';
ALTER TABLE academic_semesters ADD COLUMN updated_at TEXT DEFAULT '';
CREATE TABLE academic_semester_calendar_days(semester_id INTEGER,date TEXT,week_index INTEGER,weekday INTEGER,
 day_type TEXT,label TEXT,source TEXT,source_url TEXT,confidence REAL,metadata_json TEXT);
CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT,description TEXT);
INSERT INTO classes VALUES(1,'甲班',''),(2,'乙班',''),(3,'其他班','');
CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER,enrollment_status TEXT);
INSERT INTO students VALUES(1,1,'active'),(2,2,'active'),(3,3,'active');
CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
INSERT INTO class_offering_class_links VALUES(10,2);
ALTER TABLE courses ADD COLUMN description TEXT DEFAULT '';
ALTER TABLE courses ADD COLUMN credits INTEGER DEFAULT 2;
ALTER TABLE class_offerings ADD COLUMN class_id INTEGER DEFAULT 1;
ALTER TABLE class_offerings ADD COLUMN semester TEXT DEFAULT '2026-2027第一学期';
ALTER TABLE class_offerings ADD COLUMN combined_class_names TEXT DEFAULT '甲班、乙班';
ALTER TABLE class_offerings ADD COLUMN schedule_info TEXT DEFAULT '';
ALTER TABLE class_offerings ADD COLUMN created_at TEXT DEFAULT '';
UPDATE class_offerings SET class_id=3,combined_class_names='其他班' WHERE id=20;
ALTER TABLE class_offering_sessions ADD COLUMN schedule_metadata_json TEXT DEFAULT '{}';
"""


class AcademicScheduleOverviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA + EXTRA_SCHEMA)
        ensure_academic_schedule_prediction_schema(self.conn)
        self.conn.commit()
        self.addCleanup(self.conn.close)
        engine = patch.object(config, 'DB_ENGINE', 'sqlite')
        engine.start()
        self.addCleanup(engine.stop)
        clock = patch('classroom_app.services.academic_schedule_overview_service.china_now',
                      return_value=datetime(2026, 9, 19))
        clock.start()
        self.addCleanup(clock.stop)

    def publish(self, snapshot):
        lease = claim_schedule_sync(self.conn, 1)
        self.conn.commit()
        reconcile_and_publish_snapshot(self.conn, 1, 1, snapshot, lease['token'], now=datetime(2026, 9, 19, tzinfo=timezone.utc))
        release_schedule_sync(self.conn, 1, lease['token'])
        self.conn.commit()

    def teacher(self, **filters):
        return build_academic_prediction_overview(self.conn, 1, year='2026-2027', term='1', **filters)

    @staticmethod
    def lessons(overview):
        return [lesson for week in overview['weeks'] for lesson in week['lessons']]

    def test_teacher_pending_counts_dates_stable_number_and_both_links(self):
        self.publish(base_snapshot([request()]))
        result = self.teacher()
        self.assertEqual((3, 6, 1), tuple(result['summary'][k] for k in ('slot_count', 'total_hours', 'prediction_count')))
        pair = [lesson for lesson in self.lessons(result) if lesson.get('adjustment')]
        self.assertEqual({'2026-09-20', '2026-10-11'}, {item['actual_date'] for item in pair})
        self.assertEqual({'/classroom/10?session_id=101'}, {item['classroom_url'] for item in pair})
        self.assertEqual({1}, {item['session_no'] for item in pair})
        self.assertEqual({3}, {item['session_total'] for item in pair})
        self.assertEqual(6, sum(week['total_hours'] for week in result['weeks']))
        self.assertEqual(19, len(result['weeks']))

    def test_equivalent_shared_semester_does_not_hide_published_teacher_term(self):
        self.publish(base_snapshot())
        self.conn.execute("INSERT INTO academic_semesters(id,teacher_id,school_code,name,start_date,end_date,week_count) VALUES(99,2,'school','2026-2027学年第1学期','2026-08-31','2027-01-10',19)")
        self.assertEqual(1, self.teacher()['selected_term']['semester_id'])

    def test_approved_lag_warning_is_readable_then_ordinary_card_keeps_number(self):
        snapshot = base_snapshot([request(status='approved')])
        self.publish(snapshot)
        result = self.teacher()
        self.assertIn('申请已通过', result['message'])
        self.assertNotIn("{'code'", result['message'])
        self.assertFalse(any(item.get('adjustment') for item in self.lessons(result)))
        snapshot['official'][0] = official('2026-10-11')
        self.publish(snapshot)
        # 调课生效后剩余课次按日期重排：第 1 次课落到最早的剩余日期，10-11 变成最后一次课；
        # 课次序号（材料绑定）不变，只有日期重新分配。
        lessons = self.lessons(self.teacher())
        first = next(item for item in lessons if item['session_id'] == 101)
        self.assertEqual(('2026-09-26', 1), (first['actual_date'], first['session_no']))
        self.assertNotIn('adjustment', first)
        last = next(item for item in lessons if item['actual_date'] == '2026-10-11')
        self.assertEqual((103, 3), (last['session_id'], last['session_no']))
        rows = self.conn.execute("SELECT id, session_date, order_index FROM class_offering_sessions WHERE class_offering_id=10 ORDER BY order_index").fetchall()
        self.assertEqual([(101, '2026-09-26', 1), (102, '2026-09-27', 2), (103, '2026-10-11', 3)], [tuple(row) for row in rows])
        self.assertEqual({901: 101, 902: 102, 903: 103}, {row[1]: row[0] for row in self.conn.execute("SELECT session_id, material_id FROM session_materials")})

    def test_teacher_filters_and_read_only_snapshot(self):
        self.publish(base_snapshot([request()]))
        self.conn.execute('PRAGMA query_only=ON')
        statements = []
        self.conn.set_trace_callback(statements.append)
        result = self.teacher(course='网络', class_label='甲班、乙班')
        self.conn.set_trace_callback(None)
        self.assertEqual(4, len(self.lessons(result)))
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in statements), statements)
        self.assertEqual([], self.lessons(self.teacher(course='不存在')))

    def test_first_snapshot_without_offering_has_create_link_and_no_fabricated_session(self):
        self.conn.execute('DELETE FROM session_materials')
        self.conn.execute('DELETE FROM class_offering_sessions WHERE class_offering_id=10')
        self.conn.execute('DELETE FROM class_offering_class_links WHERE offering_id=10')
        self.conn.execute('DELETE FROM class_offerings WHERE id=10')
        self.publish(base_snapshot([request()]))
        result = self.teacher()
        lessons = self.lessons(result)
        self.assertEqual(4, len(lessons))
        self.assertTrue(all(item['create_url'].startswith('/manage/') for item in lessons))
        self.assertTrue(all(not item['classroom_url'] and item['session_id'] is None for item in lessons))
        self.assertIn('尚未精确关联课堂', result['message'])

    def test_student_projection_counts_and_revocation(self):
        self.publish(base_snapshot([request()]))
        result = build_student_course_schedule_overview(self.conn, 2, now=datetime(2026, 9, 19))
        pair = [lesson for lesson in self.lessons(result) if lesson.get('adjustment')]
        self.assertEqual(2, len(pair))
        self.assertEqual({101}, {lesson['session_id'] for lesson in pair})
        self.assertEqual((6, 1), (result['summary']['total_hours'], result['summary']['prediction_count']))
        self.assertNotIn('秘密', str(result))
        self.conn.execute('DELETE FROM class_offering_class_links WHERE offering_id=10 AND class_id=2')
        self.assertEqual([], build_student_course_schedule_overview(self.conn, 2)['weeks'])

    def test_room_only_and_empty_complete_snapshot_do_not_leave_old_student_cards(self):
        self.publish(base_snapshot([request(proposed=slot('2026-09-20', room='C108'))]))
        result = build_student_course_schedule_overview(self.conn, 1, now=datetime(2026, 9, 19))
        self.assertEqual(3, len(self.lessons(result)))
        self.assertEqual('room', self.lessons(result)[0]['adjustment']['kind'])
        self.publish({'official': [], 'requests': []})
        result = build_student_course_schedule_overview(self.conn, 1, now=datetime(2026, 9, 19))
        self.assertEqual([], self.lessons(result))
        self.assertEqual(0, result['summary']['total_hours'])

    def test_full_combined_classes_and_explicit_clock_do_not_leak_to_proposed_slot(self):
        snapshot = base_snapshot([request()])
        for item in [*snapshot['official'], *snapshot['requests']]:
            item['class_label'] = '远端教学班代号'
        self.publish(snapshot)
        self.conn.execute("ALTER TABLE class_offering_sessions ADD COLUMN academic_time_text TEXT DEFAULT ''")
        self.conn.execute("UPDATE class_offering_sessions SET academic_time_text='第2-3节 09:15–10:50' WHERE id=101")
        # Complete linked classes remain available even when cached text omits one.
        self.conn.execute("UPDATE class_offerings SET combined_class_names='甲班' WHERE id=10")
        for result in [self.teacher(), build_student_course_schedule_overview(self.conn, 2, now=datetime(2026, 9, 19))]:
            pair = [lesson for lesson in self.lessons(result) if lesson.get('adjustment')]
            self.assertEqual({'甲班、乙班'}, {lesson['class_label'] for lesson in pair})
            original = next(lesson for lesson in pair if lesson['adjustment']['endpoint'] == 'original')
            proposed = next(lesson for lesson in pair if lesson['adjustment']['endpoint'] == 'proposed')
            self.assertEqual(('09:15', '10:50', '09:15–10:50'), tuple(original[key] for key in ('start_time', 'end_time', 'time_label')))
            self.assertEqual('', proposed['time_label'])
            self.assertEqual(original['session_no'], proposed['session_no'])
            self.assertEqual(original['classroom_url'], proposed['classroom_url'])
            self.assertNotIn('其他班', str(result))

    def test_platform_teacher_and_student_metadata_agree_after_dated_session_move(self):
        from classroom_app.services.smart_classroom_schedule_sync_service import _load_platform_offering_schedule_items, _build_week_deck
        self.conn.execute("ALTER TABLE class_offering_sessions ADD COLUMN academic_time_text TEXT DEFAULT ''")
        self.conn.execute("UPDATE class_offerings SET combined_class_names='' WHERE id=10")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-21', academic_time_text='09:15-10:50', academic_location='（知新楼B416）网络实验室' WHERE id=101")
        items = _load_platform_offering_schedule_items(self.conn, 1, semester_id=1, year='2026-2027', term='1', week1_monday=date(2026, 8, 31))
        teacher = {'weeks': _build_week_deck(items, max_week=19, cur_week=3, week1_monday=date(2026, 8, 31))}
        student = build_student_course_schedule_overview(self.conn, 2, now=datetime(2026, 9, 19))
        for result in [teacher, student]:
            lesson = next(item for item in self.lessons(result) if item.get('session_id') == 101)
            self.assertEqual(('2026-09-21', 1, 1, 3, '09:15–10:50'), tuple(lesson[key] for key in ('actual_date', 'weekday', 'session_no', 'session_total', 'time_label')))
            self.assertEqual('/classroom/10?session_id=101', lesson['classroom_url'])
            self.assertEqual('甲班、乙班', lesson['class_label'])
            self.assertEqual('（知新楼B416）网络实验室', lesson['classroom'])
            self.assertIn('B416', lesson['classroom_short'])
            self.assertEqual([101], [item['session_id'] for item in result['weeks'][3]['lessons'] if item['weekday'] == 1])
            self.assertNotIn('秘密', str(result))
        # Ordinary imported recurring rows gain a date from the known week anchor,
        # but a period-only source never invents a clock time.
        items[0].pop('_occurrence_metadata')
        recurring = _build_week_deck([items[0]], max_week=19, cur_week=3, week1_monday=date(2026, 8, 31))
        lesson = next(week['lessons'][0] for week in recurring if week['lessons'])
        self.assertTrue(lesson['actual_date'])
        self.assertEqual('', lesson['time_label'])

    def test_stale_period_metadata_and_ambiguous_clock_ranges_stay_unknown(self):
        from classroom_app.services.schedule_lesson_metadata import explicit_lesson_time
        lesson = {'actual_date': '2026-09-20', 'sections': [8, 9]}
        session = {'session_date': '2026-09-20', 'academic_section_text': '8-9',
                   'academic_time_text': '09:15-10:50', 'schedule_metadata_json': '{"section_text":"2-3"}'}
        self.assertEqual('', explicit_lesson_time(lesson, session)['time_label'])
        session.update(schedule_metadata_json='{}', academic_time_text='09:15-10:50 或 14:00-15:40')
        self.assertEqual('', explicit_lesson_time(lesson, session)['time_label'])
        session['academic_time_text'] = '第2-3节 09:15-10:50'
        self.assertEqual('', explicit_lesson_time(lesson, session)['time_label'])
        session['academic_time_text'] = '第8-9节'
        self.assertEqual('', explicit_lesson_time(lesson, session)['time_label'])


if __name__ == '__main__':
    unittest.main()
