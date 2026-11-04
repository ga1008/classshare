from __future__ import annotations

import sqlite3
import unittest

from tools.repair_course_file_scopes import apply_repairs, plan_repairs


class RepairCourseFileScopesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, course_id INTEGER, class_id INTEGER);
            INSERT INTO class_offerings VALUES(13,19,27);
            INSERT INTO class_offerings VALUES(6,1,6);
            INSERT INTO class_offerings VALUES(20,2,40);
            INSERT INTO class_offerings VALUES(21,2,41);
            CREATE TABLE course_files(
                id INTEGER PRIMARY KEY, course_id INTEGER, file_name TEXT, scope_level TEXT,
                is_public TEXT, is_teacher_resource TEXT, class_offering_id INTEGER,
                class_id INTEGER, department TEXT
            );
            INSERT INTO course_files VALUES(56,19,'lesson.html','private','true','false',NULL,NULL,'合成系');
            INSERT INTO course_files VALUES(55,1,'network.html','private','true','false',6,NULL,'合成系');
            INSERT INTO course_files VALUES(60,2,'multi.html','private','true','false',NULL,NULL,'合成系');
            INSERT INTO course_files VALUES(61,3,'orphan.html','private','true','false',NULL,NULL,'');
            INSERT INTO course_files VALUES(62,19,'teacher-only.html','private','true','true',NULL,NULL,'合成系');
            INSERT INTO course_files VALUES(63,19,'draft.html','private','false','false',NULL,NULL,'合成系');
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_single_offering_course_adopts_its_classroom(self):
        plans = {plan["id"]: plan for plan in plan_repairs(self.conn)}

        self.assertEqual("classroom", plans[56]["scope_level"])
        self.assertEqual(13, plans[56]["class_offering_id"])
        self.assertEqual(27, plans[56]["class_id"])

    def test_existing_offering_keeps_its_class(self):
        plans = {plan["id"]: plan for plan in plan_repairs(self.conn)}

        self.assertEqual("classroom", plans[55]["scope_level"])
        self.assertEqual(6, plans[55]["class_offering_id"])
        self.assertEqual(6, plans[55]["class_id"])

    def test_ambiguous_course_falls_back_to_department_or_school(self):
        plans = {plan["id"]: plan for plan in plan_repairs(self.conn)}

        self.assertEqual("department", plans[60]["scope_level"])
        self.assertIsNone(plans[60]["class_offering_id"])
        self.assertEqual("school", plans[61]["scope_level"])

    def test_teacher_private_and_unpublished_rows_are_untouched(self):
        plans = {plan["id"] for plan in plan_repairs(self.conn)}

        self.assertNotIn(62, plans)
        self.assertNotIn(63, plans)

    def test_apply_is_idempotent(self):
        plans = plan_repairs(self.conn)

        self.assertEqual(len(plans), apply_repairs(self.conn, plans))
        self.assertEqual([], plan_repairs(self.conn))
        row = self.conn.execute("SELECT scope_level, class_id FROM course_files WHERE id = 56").fetchone()
        self.assertEqual(("classroom", 27), (row["scope_level"], row["class_id"]))


if __name__ == "__main__":
    unittest.main()
