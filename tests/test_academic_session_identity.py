import sqlite3
import unittest

from fastapi import HTTPException
from classroom_app.services.academic_session_identity_service import reconcile_existing_academic_sessions
from classroom_app.services.classroom_session_link_service import resolve_requested_classroom_session


class AcademicSessionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE courses(id INTEGER PRIMARY KEY);
            INSERT INTO courses VALUES(1);
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, course_id INTEGER);
            INSERT INTO class_offerings VALUES(1,1),(2,1);
            CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY,class_offering_id INTEGER,
              order_index INTEGER,title TEXT,learning_material_id INTEGER,session_date TEXT,
              academic_section_text TEXT,academic_location TEXT,week_index INTEGER,weekday INTEGER,
              schedule_status TEXT,academic_occurrence_id INTEGER);
            INSERT INTO class_offering_sessions VALUES
              (101,1,1,'第一课',901,'2026-09-20','4-5','A',3,6,'scheduled',NULL),
              (102,1,2,'第二课',902,'2026-09-27','4-5','A',4,6,'scheduled',NULL),
              (201,2,1,'其他课堂',903,'2026-09-20','4-5','A',3,6,'scheduled',NULL);
        ''')

    def slots(self, *days):
        return [{'id': n+1, 'session_date': day, 'section_text': '4-5', 'location': 'A', 'week_index': n+3}
                for n, day in enumerate(days)]

    def run_sync(self, occurrences):
        return reconcile_existing_academic_sessions(self.conn, offering={'id': 1, 'course_id': 1}, occurrences=occurrences)

    def test_cross_date_move_does_not_shift_teaching_identity(self):
        result = self.run_sync(self.slots('2026-09-27', '2026-10-11'))
        first = dict(self.conn.execute('SELECT * FROM class_offering_sessions WHERE id=101').fetchone())
        self.assertEqual(('2026-09-20','第一课',901,1,'scheduled'),
                         (first['session_date'],first['title'],first['learning_material_id'],first['order_index'],first['schedule_status']))
        self.assertTrue(result['warnings'])
        self.assertEqual(3,self.conn.execute('SELECT COUNT(*) FROM class_offering_sessions').fetchone()[0])

    def test_middle_or_first_cancellation_keeps_remaining_ids(self):
        self.run_sync(self.slots('2026-09-27'))
        rows = [tuple(r) for r in self.conn.execute('SELECT id,order_index,title,learning_material_id,schedule_status FROM class_offering_sessions ORDER BY id')]
        self.assertEqual([(101,1,'第一课',901,'cancelled'),(102,2,'第二课',902,'scheduled'),(201,1,'其他课堂',903,'scheduled')], rows)

    def test_contiguous_official_block_preserves_two_real_sessions(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-20',academic_section_text='6-7' WHERE id=102")
        source = self.slots('2026-09-20')
        source[0]['section_text'] = '4-7'
        result = self.run_sync(source)
        self.assertEqual(2,result['updated_count'])
        self.assertEqual(['4-5','6-7'],[r[0] for r in self.conn.execute('SELECT academic_section_text FROM class_offering_sessions WHERE class_offering_id=1 ORDER BY id')])

    def test_ambiguous_existing_sessions_neither_wins(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-20' WHERE id=102")
        result = self.run_sync(self.slots('2026-09-20'))
        self.assertEqual(0,result['updated_count'])
        self.assertTrue(result['warnings'])

    def test_deep_link_is_scoped_to_authorized_offering(self):
        self.assertEqual(102,resolve_requested_classroom_session(self.conn,1,'102')['id'])
        with self.assertRaises(HTTPException) as error:
            resolve_requested_classroom_session(self.conn,1,'201')
        self.assertEqual(404,error.exception.status_code)
        for raw in ('-1','0','foo','1.0','１２３','9999999999999999999999'):
            with self.subTest(raw=raw), self.assertRaises(HTTPException):
                resolve_requested_classroom_session(self.conn,1,raw)


if __name__ == '__main__':
    unittest.main()
