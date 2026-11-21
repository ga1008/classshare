"""Teacher allocation against the full application schema and actual offering membership."""
import unittest

from fastapi import HTTPException

from classroom_app.database import get_db_connection
from classroom_app.services import collaboration_service as groups
from classroom_app.services import group_assignment_service as assignments
from tests.sqlite_database_fixture import isolated_sqlite_database


class ClassroomSchemeAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())

    def setUp(self):
        self.conn = get_db_connection()
        self.addCleanup(self.conn.close)
        self.teacher = {"role": "teacher", "id": 711}
        self.conn.execute("INSERT INTO teachers (id,email,name,hashed_password,is_active) VALUES (711,'qa@example.invalid','QA','x',1)")
        self.conn.execute("INSERT INTO courses (id,name,created_by_teacher_id) VALUES (711,'Python',711)")
        self.conn.execute("INSERT INTO classes (id,name,created_by_teacher_id) VALUES (711,'A',711),(712,'B',711),(713,'Other',711)")
        self.conn.execute("INSERT INTO class_offerings (id,course_id,class_id,teacher_id) VALUES (711,711,711,711)")
        self.conn.execute("INSERT INTO class_offering_class_links (offering_id,class_id,teacher_id) VALUES (711,712,711)")
        self.conn.executemany("INSERT INTO students (id,name,student_id_number,class_id) VALUES (?,?,?,?)",
                              [(i, f"QA {i}", f"QA{i}", 711 if i % 2 else 712) for i in range(1, 65)])
        self.conn.execute("INSERT INTO classroom_retake_students (class_offering_id,student_id,status) VALUES (711,64,'confirmed')")
        self.conn.execute("INSERT INTO students (id,name,student_id_number,class_id) VALUES (65,'Other','QA65',713)")
        self.conn.execute("INSERT INTO students (id,name,student_id_number,class_id,enrollment_status) VALUES (66,'Left','QA66',711,'inactive')")
        self.payload = {"allocation_mode": "all", "name": "期末", "min_members": 4, "max_members": 5, "group_count": 13}

    def create(self, **settings):
        return groups.create_group_scheme(self.conn, 711, self.teacher, {**self.payload, **settings})

    @staticmethod
    def places(scheme):
        return {m["student_id"]: g["id"] for g in scheme["groups"] for m in g["members"]}

    def test_combined_class_balanced_once_replay_and_changed_request(self):
        scheme = self.create(request_id="same-click")
        self.assertEqual(sorted(g["member_count"] for g in scheme["groups"]), [4] * 2 + [5] * 11)
        self.assertEqual(set(self.places(scheme)), set(range(1, 64)))
        self.assertEqual(scheme["exempt_students"][0]["student_id"], 64)
        replay = self.create(request_id="same-click")
        self.assertEqual((replay["id"], self.places(replay)), (scheme["id"], self.places(scheme)))
        with self.assertRaises(HTTPException) as error:
            self.create(request_id="same-click", name="另一个方案")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(groups.fill_scheme_groups(self.conn, scheme["id"], self.teacher), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM group_schemes").fetchone()[0], 1)

    def test_fill_preserves_selected_students_leader_and_manual_exemption(self):
        scheme = self.create(allocation_mode="self")
        gid = scheme["groups"][0]["id"]
        for sid in (1, 64):
            scheme = groups.teacher_assign_to_scheme_group(self.conn, gid, self.teacher, sid)
        groups.assign_scheme_leader(self.conn, gid, self.teacher, 64)
        self.assertEqual(scheme["total_students"], 64)
        self.assertEqual(groups.fill_scheme_groups(self.conn, scheme["id"], self.teacher), 62)
        fresh = groups._serialize_scheme(self.conn, scheme, self.teacher)
        self.assertEqual((fresh["ungrouped_count"], self.places(fresh)[1], self.places(fresh)[64]), (0, gid, gid))
        self.assertEqual(fresh["groups"][0]["leader_student_id"], 64)
        self.assertEqual(sorted(g["member_count"] for g in fresh["groups"]), [4] + [5] * 12)

    def test_limits_and_insufficient_capacity_leave_no_partial_writes(self):
        for invalid in ({"group_count": 12}, {"group_count": "1.5"}, {"max_members": 13}, {"allocation_mode": "invalid"}):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException):
                self.create(**invalid)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM group_schemes").fetchone()[0], 0)
        scheme = self.create(allocation_mode="self")
        self.conn.execute("UPDATE study_groups SET max_members=4 WHERE scheme_id=?", (scheme["id"],))
        with self.assertRaises(HTTPException) as error:
            groups.fill_scheme_groups(self.conn, scheme["id"], self.teacher)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM study_group_members").fetchone()[0], 0)

    def test_exempt_self_join_and_student_bulk_action_permissions(self):
        scheme = self.create(allocation_mode="self")
        exempt = {"role": "student", "id": 64}
        view = groups._serialize_scheme(self.conn, groups._load_scheme(self.conn, scheme["id"]), exempt)
        self.assertFalse(view["can_random_join"])
        self.assertFalse(view["can_fill"])
        with self.assertRaises(HTTPException) as denied:
            groups.fill_scheme_groups(self.conn, scheme["id"], exempt)
        self.assertEqual(denied.exception.status_code, 403)
        inclusive = self.create(allocation_mode="self", include_retake=True)
        joined = groups.random_join_scheme(self.conn, inclusive["id"], exempt)
        self.assertEqual(joined["scheme"]["my_group_id"], joined["group_id"])

    def test_archive_keeps_stored_members_when_current_class_changes(self):
        scheme = self.create()
        groups.close_group_scheme(self.conn, scheme["id"], self.teacher)
        self.conn.execute("UPDATE students SET class_id=713 WHERE id=1")
        self.conn.execute("INSERT INTO students (id,name,student_id_number,class_id) VALUES (67,'New','QA67',711)")
        snapshot = groups.load_collaboration_snapshot(self.conn, 711, self.teacher)
        archived = snapshot["schemes"][0]
        self.assertTrue(archived["is_history"])
        self.assertEqual((archived["grouped_count"], archived["total_students"], archived["ungrouped_count"]), (63, 63, 0))
        self.assertEqual(self.places(archived), self.places(scheme))
        self.assertFalse(archived["can_fill"])

    def test_pause_only_stops_new_student_draws_and_can_be_reopened(self):
        scheme = self.create(allocation_mode="self")
        sid = scheme["id"]
        first, second = ({"role": "student", "id": i} for i in (1, 2))
        joined = groups.random_join_scheme(self.conn, sid, first)
        groups.set_scheme_admission(self.conn, sid, self.teacher, False)
        self.assertEqual(groups.random_join_scheme(self.conn, sid, first)["group_id"], joined["group_id"])
        with self.assertRaises(HTTPException) as denied:
            groups.random_join_scheme(self.conn, sid, second)
        self.assertEqual(denied.exception.status_code, 409)
        other = next(g for g in scheme["groups"] if g["id"] != joined["group_id"])
        groups.teacher_assign_to_scheme_group(self.conn, other["id"], self.teacher, 3)
        groups.set_scheme_admission(self.conn, sid, self.teacher, True)
        self.assertTrue(groups.random_join_scheme(self.conn, sid, second)["scheme"]["admission_open"])
        groups.close_group_scheme(self.conn, sid, self.teacher)
        with self.assertRaises(HTTPException):
            groups.set_scheme_admission(self.conn, sid, self.teacher, True)

    def test_fill_does_not_place_students_into_a_group_that_started_work(self):
        scheme = self.create(allocation_mode="self", max_members=6)
        gid = scheme["groups"][0]["id"]
        groups.teacher_assign_to_scheme_group(self.conn, gid, self.teacher, 1)
        self.conn.execute("INSERT INTO assignments (id,course_id,class_offering_id,title,status) VALUES (811,711,711,'QA','published')")
        assignments.bind_assignment_to_scheme(self.conn, assignment_id="811", class_offering_id=711,
                                              scheme_id=scheme["id"], teacher_id=711)
        self.conn.execute("""INSERT INTO submissions (assignment_id,student_pk_id,student_name,status,submitted_at)
                             VALUES ('811',1,'QA 1','submitted',CURRENT_TIMESTAMP)""")
        self.assertEqual(groups.fill_scheme_groups(self.conn, scheme["id"], self.teacher), 62)
        view = groups._serialize_scheme(self.conn, groups._load_scheme(self.conn, scheme["id"]), self.teacher)
        self.assertTrue(view["groups"][0]["roster_locked"])
        self.assertEqual(view["groups"][0]["member_count"], 1)
        self.assertEqual(view["grouped_count"], 63)
