"""Tests for group-scheme member removal +少人组 redistribution.

Exercises the real SQL against in-memory SQLite. ``ensure_classroom_access`` is
patched to a no-op so the tests stay focused on the grouping logic.
"""

import os
import sqlite3

from classroom_app.db.schema_offering_class_links import ensure_offering_class_links_schema
import unittest
from unittest.mock import patch
from fastapi import HTTPException

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.db import schema_study_group_scheme as scheme_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_retake import ensure_retake_schema
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
        ensure_retake_schema(self.conn, engine="sqlite", force=True)
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
        ensure_offering_class_links_schema(self.conn, force=True, engine="sqlite")
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
            with self.assertRaises(HTTPException):
                cs.redistribute_scheme_groups(self.conn, SCHEME_ID, self.teacher)

    def test_rebalance_keeps_fourteen_groups_leaders_and_prior_work(self):
        self._add_students(63)
        groups = self._make_scheme(4, 5, 14)
        sid = 1
        for gid, size in zip(groups, [5] * 8 + [4] * 5 + [3]):
            self._join(gid, range(sid, sid + size))
            self.conn.execute("UPDATE study_groups SET leader_student_id = ? WHERE id = ?", (sid, gid))
            cs._sync_leader_role(self.conn, gid, sid)
            sid += size
        self.conn.execute("INSERT INTO group_chat_messages(group_id,sender_user_pk,content) VALUES(?,63,'旧讨论')", (groups[-1],))
        before = list(self.conn.execute("SELECT id, leader_student_id FROM study_groups ORDER BY id"))
        result = cs.redistribute_scheme_groups(self.conn, SCHEME_ID, self.teacher)
        self.assertEqual((result["group_count"], sorted(self._group_sizes())), (14, [4] * 7 + [5] * 7))
        after = list(self.conn.execute("SELECT id, leader_student_id FROM study_groups ORDER BY id"))
        self.assertEqual(before, after)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM study_group_members WHERE status='removed'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT group_id FROM group_chat_messages").fetchone()[0], groups[-1])
        cs.redistribute_scheme_groups(self.conn, SCHEME_ID, self.teacher)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM study_group_members WHERE status='removed'").fetchone()[0], 1)

    def test_infeasible_roster_change_keeps_original_members_and_empty_groups(self):
        self._add_students(5)
        first, second, empty = self._make_scheme(2, 3, 3)
        self._join(first, [1, 2, 3])
        self._join(second, [4, 5])
        with self.assertRaises(HTTPException) as error:
            cs.redistribute_scheme_groups(self.conn, SCHEME_ID, self.teacher)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(sorted(self._group_sizes()), [0, 2, 3])
        self.assertIsNotNone(self.conn.execute("SELECT id FROM study_groups WHERE id=?", (empty,)).fetchone())

    def test_random_join_balances_and_request_replay_keeps_leader(self):
        self._add_students(8)
        groups = self._make_scheme(2, 4, 3)
        with patch.object(cs, "ensure_classroom_access", return_value={}):
            for sid in range(1, 9):
                student = {"role": "student", "id": sid}
                joined = cs.random_join_scheme(self.conn, SCHEME_ID, student)
                self.assertEqual(joined["group_id"], cs.random_join_scheme(self.conn, SCHEME_ID, student)["group_id"])
            self.assertEqual(sorted(self._group_sizes()), [2, 3, 3])
            gid = groups[0]
            sid = self.conn.execute("SELECT student_id FROM study_group_members WHERE group_id=?", (gid,)).fetchone()[0]
            cs.assign_scheme_leader(self.conn, gid, self.teacher, sid)
            cs.teacher_assign_to_scheme_group(self.conn, gid, self.teacher, sid)
            self.assertEqual(cs._member_row(self.conn, gid, sid)["member_role"], "leader")

    def test_closed_scheme_rejects_edits_and_chat_but_preserves_history(self):
        self._add_students(3)
        gid = self._make_scheme(2, 3, 1)[0]
        self._join(gid, [1, 2])
        student = {"role": "student", "id": 1}
        with patch.object(cs, "ensure_classroom_access", return_value={}):
            posted = cs.post_group_chat(self.conn, gid, student, "原有对话")
            cs.close_group_scheme(self.conn, SCHEME_ID, self.teacher)
            for change in (
                lambda: cs.add_group_member(self.conn, gid, self.teacher, 3),
                lambda: cs.remove_group_member(self.conn, gid, self.teacher, 2),
                lambda: cs.leave_group(self.conn, gid, student),
                lambda: cs.assign_scheme_leader(self.conn, gid, self.teacher, 1),
                lambda: cs.nominate_group_leader(self.conn, gid, student, 1),
                lambda: cs.auto_assign_scheme_leaders(self.conn, SCHEME_ID, self.teacher),
                lambda: cs.set_group_goal_progress(self.conn, gid, self.teacher, {"progress_percent": 80}),
                lambda: cs.update_group(self.conn, gid, self.teacher, {"name": "其他"}),
                lambda: cs.post_group_chat(self.conn, gid, student, "新消息"),
                lambda: cs.recall_group_chat_message(self.conn, gid, posted["id"], student),
            ):
                with self.assertRaises(HTTPException):
                    change()
            history = cs.list_group_chat(self.conn, gid, student)
            self.assertEqual([m["content"] for m in history["messages"]], ["原有对话"])
            self.assertEqual(self._group_sizes(), [2])

    def test_member_card_confined_to_current_offering(self):
        self._add_students(1)
        self.conn.execute("UPDATE students SET class_id = 17 WHERE id=1")
        with self.assertRaises(HTTPException) as error:
            cs.load_member_public_card(self.conn, CLASS_OFFERING_ID, 1, self.teacher)
        self.assertEqual(error.exception.status_code, 400)

    def test_auto_leaders_use_one_snapshot_query_and_keep_designated_leaders(self):
        self._add_students(6)
        first, second = self._make_scheme(2, 4, 2)
        self._join(first, [1, 2, 3])
        self._join(second, [4, 5, 6])
        self.conn.execute("CREATE TABLE learning_progress_snapshots (class_offering_id INTEGER, student_id INTEGER, score REAL, metrics_json TEXT)")
        self.conn.executemany("INSERT INTO learning_progress_snapshots VALUES (?,?,?,?)", [
            (CLASS_OFFERING_ID, 1, 99, '{"score":50,"components":{"task":18}}'),
            (CLASS_OFFERING_ID, 2, 48, '{"score":50,"components":{"interaction":13,"task":5}}'),
            (CLASS_OFFERING_ID, 3, 49, '{}'),
        ])
        cs.assign_scheme_leader(self.conn, second, self.teacher, 5)
        sql = []
        self.conn.set_trace_callback(sql.append)
        result = cs.auto_assign_scheme_leaders(self.conn, SCHEME_ID, self.teacher)
        self.conn.set_trace_callback(None)
        self.assertEqual([g["leader_student_id"] for g in result["scheme"]["groups"]], [2, 5])
        self.assertEqual(result["assigned"], 1)
        self.assertEqual(sum("JOIN learning_progress_snapshots" in statement for statement in sql), 1)

    def test_invite_group_management_follows_current_leader_after_exit(self):
        self._add_students(3)
        gid = self.conn.execute(
            """INSERT INTO study_groups(class_offering_id,name,join_policy,created_by_role,created_by_user_pk,leader_student_id)
               VALUES (?,'组队','invite','student',1,1)""", (CLASS_OFFERING_ID,),
        ).lastrowid
        self._join(gid, [1, 2, 3])
        former, successor = ({"role": "student", "id": sid} for sid in (1, 2))
        with patch.object(cs, "ensure_classroom_access", return_value={}), patch.object(cs, "_notify_teacher"):
            cs.leave_group(self.conn, gid, former)
            with self.assertRaises(HTTPException) as error:
                cs.remove_group_member(self.conn, gid, former, 3)
            self.assertEqual(error.exception.status_code, 403)
            entry = cs._serialize_student_group(self.conn, cs._load_group(self.conn, gid), successor)
            self.assertTrue(entry["can_remove_members"] and entry["can_invite"])
            cs.remove_group_member(self.conn, gid, successor, 3)
            self.assertFalse(cs._is_active_member(self.conn, gid, 3))

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
