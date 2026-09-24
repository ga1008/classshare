"""Combined-class display identity without changing classroom membership."""
import sqlite3
import unittest

from classroom_app.services.schedule_lesson_metadata import load_offering_class_labels


class ScheduleLessonMetadataTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings(id INTEGER, class_id INTEGER, teacher_id INTEGER, combined_class_names TEXT);
            CREATE TABLE class_offering_class_links(offering_id INTEGER, class_id INTEGER);
        ''')
        self.addCleanup(self.conn.close)

    def label(self, names, cached):
        self.conn.executemany('INSERT INTO classes VALUES(?,?)', enumerate(names, 1))
        self.conn.execute('INSERT INTO class_offerings VALUES(10,1,7,?)', (cached,))
        self.conn.executemany('INSERT INTO class_offering_class_links VALUES(10,?)', [(i,) for i in range(1, len(names) + 1)])
        return load_offering_class_labels(self.conn, [10], teacher_id=7)[10]

    def test_middle_dot_cache_and_linked_names_are_each_shown_once(self):
        self.assertEqual('计算机2601班、软件2602班',
                         self.label(['计算机2601班', '软件2602班'], '计算机2601班·软件2602班'))

    def test_full_width_and_spacing_variants_use_actual_class_spelling(self):
        self.assertEqual('AI 2601班（专升本）、软件2602班',
                         self.label(['AI 2601班（专升本）', '软件2602班'],
                                    'ＡＩ２６０１班(专升本) · 软件 2602 班；AI 2601班（专升本）'))

    def test_old_list_delimiters_keep_uncached_and_cached_only_combined_classes(self):
        self.assertEqual('甲班、乙班、丙班、丁班',
                         self.label(['甲班', '乙班', '丁班'], '甲班，乙班;丙班、甲班'))

    def test_middle_dot_in_real_name_and_parenthesized_punctuation_are_preserved(self):
        self.assertEqual('设计·实验班、软件（中外,合作）班、AI / 国际班',
                         self.label(['设计·实验班', '软件（中外,合作）班', 'AI / 国际班'],
                                    '设计·实验班·软件（中外,合作）班·AI / 国际班'))

    def test_empty_cache_keeps_all_linked_classes_and_teacher_scope(self):
        self.assertEqual('甲班、乙班', self.label(['甲班', '乙班'], ''))
        self.assertEqual({}, load_offering_class_labels(self.conn, [10], teacher_id=8))
        self.assertEqual({}, load_offering_class_labels(self.conn, [], teacher_id=7))


if __name__ == '__main__':
    unittest.main()
