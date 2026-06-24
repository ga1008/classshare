"""Tests for group-scheme member removal +少人组 redistribution.

Exercises the real SQL against in-memory SQLite. ``ensure_classroom_access`` is
patched to a no-op so the tests stay focused on the grouping logic.
"""

import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.db import schema_study_group_scheme as scheme_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.services import collaboration_service as cs

CLASS_OFFERING_ID = 100
CLASS_ID = 1
TEACHER_ID = 7
SCHEME_ID = 500


class SchemeRedistributionTests(unittest.TestCase):
    def setUp(self):
        scheme_schema._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_classroom_activity_schema(self.conn)
        scheme_schema.ensure_study_group_scheme_schema(self.conn)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY, name TEXT, student_id_number TEXT,
                class_id INTEGER, avatar_file_hash TEXT, enrollment_status TEXT DEFAULT 'active'
            )
            """
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS class_offerings (id INTEGER PRIMARY KEY, class_id INTEGER, teacher_id INTEGER, course_id INTEGER)"
        )
        self.conn.execute("CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS classes (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, title TEXT)")
        self.conn.execute("INSERT INTO courses (id, name) VALUES (1, '课程')")
        self.conn.execute("INSERT INTO classes (id, name) VALUES (?, '班级')", (CLASS_ID,))
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, teacher_id, course_id) VALUES (?, ?, ?, 1)",
            (CLASS_OFFERING_ID, CLASS_ID, TEACHER_ID),
        )
        self.conn.commit()
        self.teacher = {"role": "teacher", "id": TEACHER_ID}

    def tearDown(self):
        self.conn.close()

    def _add_students(self, n):
        for sid in range(1, n + 1):
            self.conn.execute(
                "INSERT INTO students (id, name, student_id_number, class_id) VALUES (?, ?, ?, ?)",
                (sid, f"学生{sid}", f"S{sid:03d}", CLASS_ID),
            )
        self.conn.commit()

    def _make_scheme(self, min_m, max_m, group_count):
        self.conn.execute(
            """
            INSERT INTO group_schemes (id, class_offering_id, name, min_members, max_members,
                                       group_count, status, created_by_teacher_id)
            VALUES (?, ?, '随机分组', ?, ?, ?, 'active', ?)
            """,
            (SCHEME_ID, CLASS_OFFERING_ID, min_m, max_m, group_count, TEACHER_ID),
        )
        group_ids = []
        for index in range(1, group_count + 1):
            cur = self.conn.execute(
                """
                INSERT INTO study_groups (class_offering_id, name, status, join_policy, max_members,
                                          created_by_role, created_by_user_pk, scheme_id, group_index)
                VALUES (?, ?, 'active', 'scheme_random', ?, 'teacher', ?, ?, ?)
                """,
                (CLASS_OFFERING_ID, f"第 {index} 组", max_m, TEACHER_ID, SCHEME_ID, index),
            )
            group_ids.append(cur.lastrowid)
        self.conn.commit()
        return group_ids

    def _join(self, group_id, student_ids):
        for sid in student_ids:
            self.conn.execute(
                "INSERT INTO study_group_members (group_id, student_id, member_role, status) VALUES (?, ?, 'member', 'active')",
                (group_id, sid),
            )
        self.conn.commit()

    def _group_sizes(self):
        rows = self.conn.execute(
            """
            SELECT g.id, COUNT(m.id) AS c
            FROM study_groups g
            LEFT JOIN study_group_members m ON m.group_id = g.id AND m.status = 'active'
            WHERE g.scheme_id = ?
            GROUP BY g.id
            """,
            (SCHEME_ID,),
        ).fetchall()
        return [int(r["c"]) for r in rows]

    # --- pure helper -----------------------------------------------------
    def test_resolve_group_count(self):
        self.assertEqual(cs._resolve_redistribution_group_count(7, 2, 3), 3)   # [3,2,2]
        self.assertEqual(cs._resolve_redistribution_group_count(6, 2, 3), 2)   # [3,3]
        self.assertEqual(cs._resolve_redistribution_group_count(4, 2, 3), 2)   # [2,2]
        self.assertEqual(cs._resolve_redistribution_group_count(2, 2, 3), 1)   # [2]
        self.assertEqual(cs._resolve_redistribution_group_count(1, 2, 3), 1)   # solo, unavoidable

    # --- redistribution --------------------------------------------------
    def test_redistribute_fixes_single_person_group(self):
        self._add_students(7)
        g1, g2, g3 = self._make_scheme(min_m=2, max_m=3, group_count=3)
        self._join(g1, [1, 2, 3])
        self._join(g2, [4, 5, 6])
        self._join(g3, [7])  # deficient single-person group
        with patch.object(cs, "ensure_classroom_access", return_value={}):
            scheme = cs.redistribute_scheme_groups(self.conn, SCHEME_ID, self.teacher)
        self.conn.commit()
        sizes = sorted(self._group_sizes())
        # 7 students, 2-3 per group -> [2,2,3]; every group within bounds.
        self.assertEqual(sizes, [2, 2, 3])
        for size in sizes:
            self.assertGreaterEqual(size, 2)
            self.assertLessEqual(size, 3)
        # All 7 students still assigned exactly once.
        total = self.conn.execute(
            "SELECT COUNT(*) AS c FROM study_group_members m JOIN study_groups g ON g.id=m.group_id WHERE g.scheme_id=? AND m.status='active'",
            (SCHEME_ID,),
        ).fetchone()["c"]
        self.assertEqual(total, 7)
        self.assertFalse(scheme["needs_redistribute"])

    def test_redistribute_blocked_when_ungrouped_exist(self):
        self._add_students(7)
        g1, g2, g3 = self._make_scheme(min_m=2, max_m=3, group_count=3)
        self._join(g1, [1, 2, 3])
        self._join(g2, [4, 5])
        # students 6,7 ungrouped
        with patch.object(cs, "ensure_classroom_access", return_value={}):
            with self.assertRaises(Exception):
                cs.redistribute_scheme_groups(self.conn, SCHEME_ID, self.teacher)

    def test_needs_redistribute_flag(self):
        self._add_students(7)
        g1, g2, g3 = self._make_scheme(min_m=2, max_m=3, group_count=3)
        self._join(g1, [1, 2, 3])
        self._join(g2, [4, 5, 6])
        self._join(g3, [7])
        with patch.object(cs, "ensure_classroom_access", return_value={}):
            scheme = cs._serialize_scheme(self.conn, cs._load_scheme(self.conn, SCHEME_ID), self.teacher)
        self.assertTrue(scheme["needs_redistribute"])
        self.assertEqual(scheme["deficient_group_count"], 1)

    def test_teacher_remove_member_makes_ungrouped(self):
        self._add_students(6)
        g1, g2, g3 = self._make_scheme(min_m=2, max_m=3, group_count=3)
        self._join(g1, [1, 2, 3])
        self._join(g2, [4, 5, 6])
        with patch.object(cs, "ensure_classroom_access", return_value={}):
            cs.remove_group_member(self.conn, g1, self.teacher, 3)
            scheme = cs._serialize_scheme(self.conn, cs._load_scheme(self.conn, SCHEME_ID), self.teacher)
        self.conn.commit()
        ungrouped_ids = {s["student_id"] for s in scheme["ungrouped_students"]}
        self.assertIn(3, ungrouped_ids)
        self.assertEqual(scheme["ungrouped_count"], 1)


if __name__ == "__main__":
    unittest.main()
