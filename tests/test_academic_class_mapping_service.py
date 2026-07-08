import sqlite3
import unittest

from classroom_app.services.academic_class_mapping_service import (
    load_teaching_class_display_mappings,
    refresh_teaching_class_mappings_from_roster,
    resolve_teaching_class_display_name,
    resolve_teaching_class_display_name_from_candidates,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE classes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE teacher_academic_roster_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            sync_item_id INTEGER,
            class_id INTEGER,
            student_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            admin_class_code TEXT NOT NULL DEFAULT '',
            admin_class_name TEXT NOT NULL DEFAULT '',
            student_number TEXT NOT NULL DEFAULT '',
            student_name TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE teacher_academic_roster_sync_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            class_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


class AcademicClassMappingServiceTests(unittest.TestCase):
    def test_refresh_mapping_table_from_roster_memberships(self):
        conn = _make_conn()
        conn.execute("INSERT INTO classes (id, name) VALUES (9, '软工2401班')")
        conn.executemany(
            """
            INSERT INTO teacher_academic_roster_memberships (
                teacher_id, semester_id, sync_item_id, class_id, school_code,
                academic_year, academic_term, course_code, course_name,
                teaching_class_id, teaching_class_name, admin_class_code,
                admin_class_name, student_number, student_name, synced_at
            )
            VALUES (1, 3, 88, 9, 'gxufl', '2025', '12', 'WEB101', '动态Web程序设计',
                    'TC-WEB-1', '动态Web程序设计-0001', 'SE2401', '软工2401班', ?, ?, '2026-07-08T10:00:00')
            """,
            [("20240101", "张三"), ("20240102", "李四")],
        )

        result = refresh_teaching_class_mappings_from_roster(
            conn,
            teacher_id=1,
            semester_id=3,
            synced_at="2026-07-08T10:30:00",
        )

        self.assertEqual(1, result["mapping_count"])
        row = conn.execute("SELECT * FROM teacher_academic_teaching_class_mappings").fetchone()
        self.assertEqual("动态Web程序设计-0001", row["teaching_class_name"])
        self.assertEqual("软工2401班", row["admin_class_name"])
        self.assertEqual(1, row["admin_class_count"])
        self.assertEqual(2, row["student_count"])

        mappings = load_teaching_class_display_mappings(conn, 1)
        self.assertEqual(mappings[("WEB101", "动态Web程序设计-0001")], "软工2401班")
        self.assertEqual(mappings["动态Web程序设计-0001"], "软工2401班")
        self.assertEqual(
            "软工2401班",
            resolve_teaching_class_display_name(
                conn,
                teacher_id=1,
                teaching_class_name="动态Web程序设计-0001",
                course_code="WEB101",
            ),
        )
        conn.close()

    def test_merged_class_display_is_available_but_not_single_only(self):
        conn = _make_conn()
        conn.execute("INSERT INTO classes (id, name) VALUES (9, '软工2401班'), (10, '软工2402班')")
        conn.executemany(
            """
            INSERT INTO teacher_academic_roster_memberships (
                teacher_id, semester_id, sync_item_id, class_id, school_code,
                academic_year, academic_term, course_code, course_name,
                teaching_class_id, teaching_class_name, admin_class_code,
                admin_class_name, student_number, student_name, synced_at
            )
            VALUES (1, 3, 88, ?, 'gxufl', '2025', '12', 'NET101', '计算机网络',
                    'TC-NET-1', '计算机网络-0006', ?, ?, ?, '学生', '2026-07-08T10:00:00')
            """,
            [
                (9, "SE2401", "软工2401班", "20240101"),
                (10, "SE2402", "软工2402班", "20240201"),
            ],
        )

        refresh_teaching_class_mappings_from_roster(
            conn,
            teacher_id=1,
            semester_id=3,
            synced_at="2026-07-08T10:30:00",
        )

        mappings = load_teaching_class_display_mappings(conn, 1)
        self.assertEqual(mappings["计算机网络-0006"], "软工2401班、软工2402班")
        single_only = load_teaching_class_display_mappings(conn, 1, single_only=True)
        self.assertNotIn("计算机网络-0006", single_only)
        conn.close()

    def test_refresh_mapping_from_roster_item_composition_and_aliases(self):
        conn = _make_conn()
        conn.execute(
            """
            INSERT INTO teacher_academic_roster_sync_items (
                id, teacher_id, semester_id, class_id, school_code,
                academic_year, academic_term, course_code, course_name,
                teaching_class_id, teaching_class_name, class_composition, synced_at
            )
            VALUES (77, 1, 3, NULL, 'gxufl', '2025', '12', 'NET201', '计算机网络原理',
                    'TC-NET-6', '网络原理-0006', '网工2303班（专升本）', '2026-07-08T10:00:00')
            """
        )

        result = refresh_teaching_class_mappings_from_roster(
            conn,
            teacher_id=1,
            semester_id=3,
            synced_at="2026-07-08T10:30:00",
        )

        self.assertEqual(1, result["mapping_count"])
        row = conn.execute("SELECT * FROM teacher_academic_teaching_class_mappings").fetchone()
        self.assertEqual("网工2303班（专升本）", row["admin_class_name"])
        self.assertIn("计算机网络原理-0006", row["teaching_class_aliases_json"])
        self.assertIn("网工2303", row["admin_class_aliases_json"])
        self.assertEqual(
            "网工2303班（专升本）",
            resolve_teaching_class_display_name(
                conn,
                teacher_id=1,
                teaching_class_name="计算机网络原理-0006",
                course_code="NET201",
            ),
        )
        self.assertEqual(
            "网工2303班（专升本）",
            resolve_teaching_class_display_name(
                conn,
                teacher_id=1,
                teaching_class_name="网工2303",
            ),
        )
        conn.close()

    def test_existing_mapping_table_gains_alias_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE teacher_academic_teaching_class_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                semester_id INTEGER,
                school_code TEXT NOT NULL DEFAULT 'gxufl',
                academic_year TEXT NOT NULL DEFAULT '',
                academic_term TEXT NOT NULL DEFAULT '',
                course_code TEXT NOT NULL DEFAULT '',
                course_name TEXT NOT NULL DEFAULT '',
                teaching_class_id TEXT NOT NULL DEFAULT '',
                teaching_class_name TEXT NOT NULL DEFAULT '',
                admin_class_id INTEGER,
                admin_class_code TEXT NOT NULL DEFAULT '',
                admin_class_name TEXT NOT NULL DEFAULT '',
                admin_class_ids_json TEXT NOT NULL DEFAULT '[]',
                admin_class_codes_json TEXT NOT NULL DEFAULT '[]',
                admin_class_names_json TEXT NOT NULL DEFAULT '[]',
                admin_class_count INTEGER NOT NULL DEFAULT 0,
                student_count INTEGER NOT NULL DEFAULT 0,
                mapping_status TEXT NOT NULL DEFAULT 'active',
                source_sync_item_ids_json TEXT NOT NULL DEFAULT '[]',
                source_updated_at TEXT,
                synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        refresh_teaching_class_mappings_from_roster(
            conn,
            teacher_id=1,
            synced_at="2026-07-08T10:30:00",
        )

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(teacher_academic_teaching_class_mappings)")}
        self.assertIn("teaching_class_aliases_json", columns)
        self.assertIn("admin_class_aliases_json", columns)
        conn.close()

    def test_candidate_resolver_ignores_stale_raw_display_name(self):
        conn = _make_conn()
        conn.execute(
            """
            INSERT INTO teacher_academic_roster_sync_items (
                id, teacher_id, semester_id, class_id, school_code,
                academic_year, academic_term, course_code, course_name,
                teaching_class_id, teaching_class_name, class_composition, synced_at
            )
            VALUES (88, 1, 3, NULL, 'gxufl', '2025', '12', 'NET201', '计算机网络原理',
                    'TC-NET-6', '计算机网络原理-0006', '网工2303班（专升本）', '2026-07-08T10:00:00')
            """
        )
        refresh_teaching_class_mappings_from_roster(
            conn,
            teacher_id=1,
            semester_id=3,
            synced_at="2026-07-08T10:30:00",
        )

        self.assertEqual(
            "网工2303班（专升本）",
            resolve_teaching_class_display_name_from_candidates(
                conn,
                teacher_id=1,
                teaching_class_names=[
                    "计算机网络原理-0006",
                    "计算机网络原理-0006",
                ],
                course_code="NET201",
                default="计算机网络原理-0006",
            ),
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
