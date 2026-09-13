"""Private attendance archive discovery, including exact counts beyond the preview limit."""
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services.material_hub_service import _search_attendance_reports


class AttendanceMaterialHubTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        engine = patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite")
        engine.start()
        self.addCleanup(engine.stop)
        self.ctx = {"teacher_id": 1, "teacher_name": "合成教师", "super_admin": True}

    def create_tables(self):
        self.conn.executescript("""
            CREATE TABLE smart_attendance_source_bindings(id INTEGER PRIMARY KEY,owner_teacher_id INTEGER,
                remote_course_name TEXT,remote_course_id TEXT,remote_class_name TEXT,academic_year TEXT,academic_term INTEGER);
            CREATE TABLE attendance_reports(id INTEGER PRIMARY KEY,binding_id INTEGER,updated_at TEXT,deleted_at TEXT);
            CREATE TABLE attendance_report_versions(id INTEGER PRIMARY KEY,report_id INTEGER,source_file_hash TEXT);
        """)

    def add(self, report_id, *, owner=1, name="合成课程", deleted=None, cached=True):
        self.conn.execute("INSERT INTO smart_attendance_source_bindings VALUES(?,?,?,?,?,?,?)", (report_id, owner, name, "SYN-1", "合成教学班", "2025-2026", 2))
        self.conn.execute("INSERT INTO attendance_reports VALUES(?,?,?,?)", (report_id, report_id, "2026-09-13T12:00:00", deleted))
        if cached:
            self.conn.execute("INSERT INTO attendance_report_versions VALUES(?,?,?)", (report_id, report_id, "a" * 64))

    def test_absent_new_schema_is_empty_without_poisoning_transaction(self):
        self.assertEqual(([], 0), _search_attendance_reports(self.conn, self.ctx, []))
        self.assertEqual(1, self.conn.execute("SELECT 1").fetchone()[0])

    def test_exact_total_owner_only_soft_delete_and_cached_original(self):
        self.create_tables()
        for report_id in range(1, 36):
            self.add(report_id)
        self.add(36, owner=2)
        self.add(37, deleted="2026-09-13")
        self.add(38, cached=False)
        rows, total = _search_attendance_reports(self.conn, self.ctx, [])
        self.assertEqual(35, total)
        self.assertEqual(30, len(rows))
        self.assertTrue(all(row["scope_key"] == "private" for row in rows))
        self.assertTrue(all("/manage/archive/attendance-reports/" in row["url"] for row in rows))
        self.assertNotIn("student_number", str(rows))

    def test_search_treats_percent_and_underscore_as_literal(self):
        self.create_tables()
        self.add(1, name="100%_合成课程")
        self.add(2, name="100其他合成课程")
        rows, total = _search_attendance_reports(self.conn, self.ctx, ["%_"])
        self.assertEqual(1, total)
        self.assertEqual("/manage/archive/attendance-reports/1", rows[0]["url"])


if __name__ == "__main__":
    unittest.main()
