import tempfile
import unittest
from pathlib import Path

from classroom_app import config, database
from classroom_app.db import schema_offering_class_links
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.db.schema import init_database
from classroom_app.services import offering_membership_service as membership


class OfferingMembershipServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_engine = config.DB_ENGINE
        self.original_path = config.DB_PATH
        self.original_database_path = database.DB_PATH
        config.DB_ENGINE = "sqlite"
        config.DB_PATH = Path(self.temp_dir.name) / "classroom.db"
        database.DB_PATH = config.DB_PATH
        schema_offering_class_links._READY_KEYS.clear()
        init_database()
        with database.get_db_connection() as conn:
            self.teacher_id = execute_insert_returning_id(
                conn,
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                ("测试教师", "membership@example.com", "x"),
            )
            self.semester_id = execute_insert_returning_id(
                conn,
                "INSERT INTO academic_semesters (teacher_id, name, start_date, end_date) VALUES (?, ?, ?, ?)",
                (self.teacher_id, "2026-2027学年第1学期", "2026-08-31", "2027-01-10"),
            )
            self.course_id = execute_insert_returning_id(
                conn,
                "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                ("Python程序设计", self.teacher_id),
            )
            self.class_a = self._create_class(conn, "网工2401")
            self.class_b = self._create_class(conn, "网工2402")
            self.class_c = self._create_class(conn, "软工2403")
            self.offering_id = self._create_offering(conn, self.class_a)
            self.student_a = self._create_student(conn, "2400000001", "学生甲", self.class_a)
            self.student_b = self._create_student(conn, "2400000002", "学生乙", self.class_b)
            self.student_b2 = self._create_student(
                conn, "2400000003", "学生丙", self.class_b, enrollment_status="withdrawn"
            )
            conn.commit()
        # init_database above already consumed the reset and ran ensure before
        # the fixture offering existed — reset again so each test exercises the
        # ensure+backfill path against the fully seeded database.
        schema_offering_class_links._READY_KEYS.clear()

    def tearDown(self):
        schema_offering_class_links._READY_KEYS.clear()
        config.DB_ENGINE = self.original_engine
        config.DB_PATH = self.original_path
        database.DB_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _create_class(self, conn, name):
        return execute_insert_returning_id(
            conn,
            "INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)",
            (name, self.teacher_id),
        )

    def _create_offering(self, conn, class_id, course_id=None):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO class_offerings (class_id, course_id, teacher_id, semester, semester_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (class_id, course_id or self.course_id, self.teacher_id, "2026-2027学年第1学期", self.semester_id),
        )

    def _create_student(self, conn, number, name, class_id, enrollment_status="active"):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO students (student_id_number, name, class_id, enrollment_status)
            VALUES (?, ?, ?, ?)
            """,
            (number, name, class_id, enrollment_status),
        )

    def _links(self, conn, offering_id):
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM class_offering_class_links WHERE offering_id = ? ORDER BY id",
                (offering_id,),
            ).fetchall()
        ]

    def test_backfill_creates_primary_link_idempotently(self):
        with database.get_db_connection() as conn:
            membership.offering_class_ids(conn, self.offering_id)
            first = self._links(conn, self.offering_id)
            schema_offering_class_links._READY_KEYS.clear()
            membership.offering_class_ids(conn, self.offering_id)
            second = self._links(conn, self.offering_id)
            conn.commit()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["class_id"], self.class_a)
        self.assertEqual(first[0]["is_primary"], 1)
        self.assertEqual(first[0]["source"], "backfill")
        self.assertEqual(len(second), 1)

    def test_replace_links_multi_class_updates_offering_cache(self):
        with database.get_db_connection() as conn:
            result = membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            offering = dict(
                conn.execute(
                    "SELECT * FROM class_offerings WHERE id = ?", (self.offering_id,)
                ).fetchone()
            )
            links = self._links(conn, self.offering_id)
            conn.commit()
        self.assertTrue(result["is_combined"])
        self.assertEqual(result["primary_class_id"], self.class_a)
        self.assertEqual(offering["class_id"], self.class_a)
        self.assertEqual(offering["is_combined"], 1)
        self.assertEqual(offering["combined_class_names"], "网工2401·网工2402")
        self.assertEqual(len(links), 2)
        self.assertEqual(sum(1 for link in links if link["is_primary"]), 1)

    def test_replace_links_rejects_class_covered_by_other_offering(self):
        with database.get_db_connection() as conn:
            other_offering = self._create_offering(conn, self.class_b)
            conn.commit()
            with self.assertRaises(membership.OfferingMembershipError) as ctx:
                membership.replace_offering_class_links(
                    conn,
                    offering_id=self.offering_id,
                    teacher_id=self.teacher_id,
                    class_ids=[self.class_a, self.class_b],
                )
        self.assertIn("网工2402", str(ctx.exception))
        self.assertIn(f"#{other_offering}", str(ctx.exception))

    def test_same_class_different_course_is_not_a_conflict(self):
        with database.get_db_connection() as conn:
            other_course = execute_insert_returning_id(
                conn,
                "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                ("数据结构", self.teacher_id),
            )
            self._create_offering(conn, self.class_b, course_id=other_course)
            result = membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            conn.commit()
        self.assertTrue(result["is_combined"])

    def test_load_offering_students_unions_linked_classes(self):
        with database.get_db_connection() as conn:
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            active_students = membership.load_offering_students(conn, self.offering_id)
            all_students = membership.load_offering_students(
                conn, self.offering_id, active_only=False
            )
            conn.commit()
        self.assertEqual({item["name"] for item in active_students}, {"学生甲", "学生乙"})
        self.assertEqual(len(all_students), 3)

    def test_offering_student_where_falls_back_to_primary_class(self):
        with database.get_db_connection() as conn:
            membership.offering_class_ids(conn, self.offering_id)
            conn.execute(
                "DELETE FROM class_offering_class_links WHERE offering_id = ?",
                (self.offering_id,),
            )
            fragment = membership.offering_student_where()
            rows = conn.execute(
                f"""
                SELECT s.name FROM class_offerings o
                JOIN students s ON {fragment}
                WHERE o.id = ?
                """,
                (self.offering_id,),
            ).fetchall()
            conn.commit()
        self.assertEqual({row["name"] for row in rows}, {"学生甲"})

    def test_student_offering_where_discovers_via_secondary_class(self):
        with database.get_db_connection() as conn:
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            fragment = membership.student_offering_where()
            rows = conn.execute(
                f"SELECT o.id FROM class_offerings o WHERE {fragment}",
                (self.class_b, self.class_b),
            ).fetchall()
            conn.commit()
        self.assertEqual([int(row["id"]) for row in rows], [self.offering_id])

    def test_display_name_uses_cache_then_links_then_primary(self):
        with database.get_db_connection() as conn:
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            offering = dict(
                conn.execute(
                    "SELECT * FROM class_offerings WHERE id = ?", (self.offering_id,)
                ).fetchone()
            )
            combined_name = membership.offering_display_class_name(conn, offering)
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a],
            )
            offering = dict(
                conn.execute(
                    "SELECT * FROM class_offerings WHERE id = ?", (self.offering_id,)
                ).fetchone()
            )
            single_name = membership.offering_display_class_name(conn, offering)
            conn.commit()
        self.assertEqual(combined_name, "网工2401·网工2402")
        self.assertEqual(single_name, "网工2401")

    def test_replace_links_validates_input(self):
        with database.get_db_connection() as conn:
            with self.assertRaises(membership.OfferingMembershipError):
                membership.replace_offering_class_links(
                    conn,
                    offering_id=self.offering_id,
                    teacher_id=self.teacher_id,
                    class_ids=[],
                )
            with self.assertRaises(membership.OfferingMembershipError):
                membership.replace_offering_class_links(
                    conn,
                    offering_id=self.offering_id,
                    teacher_id=self.teacher_id,
                    class_ids=[self.class_a],
                    primary_class_id=self.class_b,
                )
            with self.assertRaises(membership.OfferingMembershipError):
                membership.replace_offering_class_links(
                    conn,
                    offering_id=self.offering_id,
                    teacher_id=self.teacher_id,
                    class_ids=[999999],
                )

    def test_student_dashboard_discovers_combined_offering(self):
        from classroom_app.services.dashboard_service import _load_student_offerings

        with database.get_db_connection() as conn:
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            conn.commit()
            offerings_for_b = _load_student_offerings(conn, self.student_b)
            offerings_for_withdrawn = _load_student_offerings(conn, self.student_b2)
        self.assertEqual([int(item["id"]) for item in offerings_for_b], [self.offering_id])
        self.assertEqual(offerings_for_withdrawn, [])

    def test_teacher_offering_rows_expose_combined_fields(self):
        from classroom_app.routers.ui_parts.common import _load_teacher_offering_rows

        with database.get_db_connection() as conn:
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
            )
            conn.commit()
            rows = _load_teacher_offering_rows(conn, self.teacher_id)
        offering = next(item for item in rows if int(item["id"]) == self.offering_id)
        self.assertEqual(offering["is_combined"], 1)
        self.assertEqual(offering["class_ids"], [self.class_a, self.class_b])
        self.assertEqual(offering["class_name"], "网工2401·网工2402")

    def test_primary_switch_keeps_invariant(self):
        with database.get_db_connection() as conn:
            membership.replace_offering_class_links(
                conn,
                offering_id=self.offering_id,
                teacher_id=self.teacher_id,
                class_ids=[self.class_a, self.class_b],
                primary_class_id=self.class_b,
            )
            offering = dict(
                conn.execute(
                    "SELECT * FROM class_offerings WHERE id = ?", (self.offering_id,)
                ).fetchone()
            )
            links = self._links(conn, self.offering_id)
            conn.commit()
        self.assertEqual(offering["class_id"], self.class_b)
        primary_links = [link for link in links if link["is_primary"]]
        self.assertEqual(len(primary_links), 1)
        self.assertEqual(primary_links[0]["class_id"], self.class_b)


if __name__ == "__main__":
    unittest.main()
