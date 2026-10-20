"""Reverse timetable links use only isolated synthetic directory metadata."""
import sqlite3
import unittest

from classroom_app.db.schema_academic_teacher_timetables import ensure_academic_teacher_timetable_schema
from classroom_app.services.academic_teacher_identity_service import load_teacher_timetable_link_summaries


class TeacherTimetableIdentitySummaryTests(unittest.TestCase):
    def open_fixture(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def setUp(self):
        self.conn = self.open_fixture()
        self.addCleanup(self.conn.close)
        statements = (
            """CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT,is_active INTEGER DEFAULT 1,
               school_code TEXT,school_name TEXT,college TEXT,department TEXT,employee_number TEXT DEFAULT '',
               cancelled_at TEXT)""",
            """CREATE TABLE teacher_organization_memberships(id INTEGER PRIMARY KEY,teacher_id INTEGER,
               school_code TEXT,school_name TEXT,college TEXT,department TEXT,is_primary INTEGER DEFAULT 0,
               is_active INTEGER DEFAULT 1,updated_at TEXT,employee_number TEXT DEFAULT '')""",
            """CREATE TABLE teacher_academic_system_credentials(id INTEGER PRIMARY KEY,teacher_id INTEGER,
               school_code TEXT,username TEXT,enabled INTEGER,last_status TEXT,last_verified_at TEXT)""",
        )
        for statement in statements:
            self.conn.execute(statement)
        ensure_academic_teacher_timetable_schema(self.conn)
        self.account(1, "gxufl", "ADMIN")
        self.account(2, "gxufl", "000123")
        self.account(3, "other", "000123")
        self.cached("gxufl", "000123")
        self.cached("other", "000123")
        self.conn.commit()

    def account(self, teacher_id, school, number):
        self.conn.execute(
            """INSERT INTO teachers(id,name,school_code,school_name,college,department,employee_number)
               VALUES(?,'合成平台姓名',?,?,'合成学院','合成系',?)""",
            (teacher_id, school, school + "学校", number),
        )
        self.conn.execute(
            """INSERT INTO teacher_organization_memberships
               (id,teacher_id,school_code,school_name,college,department,is_primary,updated_at,employee_number)
               VALUES(?,?,?,?, '合成学院','合成系',1,'2026-10-21',?)""",
            (teacher_id, teacher_id, school, school + "学校", number),
        )

    def cached(self, school, number, *, term="2026-2027-1", timetable=True):
        self.conn.execute(
            """INSERT INTO academic_teacher_directory
               (school_code,teacher_no,name,college_name,title,profile_json,synced_at)
               VALUES(?,?,'缓存教师姓名','缓存学院','讲师','not-decoded','2026-10-21')
               ON CONFLICT(school_code,teacher_no) DO NOTHING""",
            (school, number),
        )
        if timetable:
            self.conn.execute(
                """INSERT INTO academic_teacher_timetables
                   (school_code,teacher_no,term_code,profile_json,snapshot_json,lessons_json,summary_json,synced_at)
                   VALUES(?,?,?,'not-decoded','not-decoded','not-decoded','not-decoded','2026-10-21')""",
                (school, number, term),
            )

    def summaries(self, ids=(2,), actor=1):
        return load_teacher_timetable_link_summaries(self.conn, ids, actor_teacher_id=actor)

    def test_immediate_exact_link_has_local_only_navigation_and_no_snapshot_decoding(self):
        summary = self.summaries()[2]
        self.assertEqual(("linked", 1, 0), (summary["status"], summary["linked_count"], summary["ambiguous_count"]))
        item = summary["items"][0]
        self.assertEqual(("000123", "缓存教师姓名", True), (item["teacher_no"], item["name"], item["can_view"]))
        self.assertIn("teacher_no=000123", item["query_url"])
        self.assertIn("local_only=1", item["query_url"])
        self.assertEqual("/api/manage/academic/teachers/000123/timetable?year=2026-2027&term=1", item["detail_url"])
        self.assertNotIn("profile_json", item)

    def test_creation_after_cache_and_number_change_are_visible_without_sync(self):
        self.cached("gxufl", "000456")
        self.account(4, "gxufl", "000456")
        self.assertEqual("linked", self.summaries((4,))[4]["status"])
        self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='000789' WHERE teacher_id=4")
        self.assertEqual("unlinked", self.summaries((4,))[4]["status"])
        self.cached("gxufl", "000789")
        self.assertEqual("000789", self.summaries((4,))[4]["items"][0]["teacher_no"])

    def test_revoke_deactivate_cancel_and_delete_never_return_old_links(self):
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=0 WHERE teacher_id=2")
        self.assertEqual([], self.summaries()[2]["items"])
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=1 WHERE teacher_id=2")
        self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=2")
        self.assertEqual("inactive", self.summaries()[2]["status"])
        self.assertEqual([], self.summaries()[2]["items"])
        self.conn.execute("UPDATE teachers SET cancelled_at='2026-10-21' WHERE id=2")
        self.assertEqual("cancelled", self.summaries()[2]["status"])
        self.conn.execute("DELETE FROM teacher_organization_memberships WHERE teacher_id=2")
        self.conn.execute("DELETE FROM teachers WHERE id=2")
        self.assertEqual("not_found", self.summaries()[2]["status"])

    def test_same_number_different_school_and_actor_scope_do_not_grant_access(self):
        summaries = self.summaries((2, 3))
        self.assertEqual("linked", summaries[3]["status"])
        other = summaries[3]["items"][0]
        self.assertFalse(other["can_view"])
        self.assertFalse(other["can_view_school"])
        self.assertFalse(other["query_supported"])
        self.assertEqual(("", "", ""), (other["name"], other["query_url"], other["detail_url"]))
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=0 WHERE teacher_id=1")
        self.assertFalse(self.summaries()[2]["items"][0]["can_view"])
        self.assertFalse(self.summaries(actor=None)[2]["items"][0]["can_view"])
        self.assertEqual("gxufl", summaries[2]["items"][0]["school_code"])
        self.assertTrue(summaries[2]["items"][0]["can_view_school"])

    def test_conflicting_legacy_credential_is_explicit_and_suppresses_navigation(self):
        self.account(4, "gxufl", "")
        self.conn.execute(
            """INSERT INTO teacher_academic_system_credentials
               VALUES(4,4,'gxufl','000123',1,'verified','2026-10-21')"""
        )
        summary = self.summaries()[2]
        self.assertEqual(("ambiguous", 0, 1), (summary["status"], summary["linked_count"], summary["ambiguous_count"]))
        self.assertFalse(summary["items"][0]["can_view"])
        self.assertEqual("", summary["items"][0]["query_url"])

    def test_legacy_verified_identity_is_compatible_but_explicit_number_supersedes_it(self):
        self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='' WHERE teacher_id=2")
        self.conn.execute(
            """INSERT INTO teacher_academic_system_credentials
               VALUES(2,2,'gxufl','000123',1,'verified','2026-10-21')"""
        )
        self.assertEqual("verified_academic_account", self.summaries()[2]["items"][0]["account_link_source"])
        self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='000456' WHERE teacher_id=2")
        self.assertEqual("unlinked", self.summaries()[2]["status"])
        self.assertEqual("000456", self.summaries()[2]["items"][0]["employee_number"])

    def test_name_never_matches_and_leading_zero_is_not_discarded(self):
        self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='123' WHERE teacher_id=2")
        self.conn.execute("UPDATE teachers SET name='缓存教师姓名' WHERE id=2")
        self.assertEqual("unlinked", self.summaries()[2]["status"])

    def test_unicode_directory_number_and_duplicate_canonical_identity_are_safe(self):
        self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='AB001' WHERE teacher_id=2")
        self.cached("gxufl", "ａｂ００１")
        self.assertEqual("linked", self.summaries()[2]["status"])
        self.cached("gxufl", "AB001")
        self.assertEqual("ambiguous", self.summaries()[2]["status"])

    def test_directory_without_timetable_has_no_view_url_and_latest_term_is_selected(self):
        self.conn.execute("DELETE FROM academic_teacher_timetables WHERE school_code='gxufl'")
        item = self.summaries()[2]["items"][0]
        self.assertEqual("linked", item["account_status"])
        self.assertFalse(item["has_timetable"])
        self.assertTrue(item["can_view_school"])
        self.assertEqual("", item["query_url"])
        self.cached("gxufl", "000123", term="2025-2026-2")
        self.cached("gxufl", "000123", term="2026-2027-1")
        item = self.summaries()[2]["items"][0]
        self.assertEqual(("2026-2027", "1", 2), (item["year"], item["term"], item["cached_term_count"]))

    def test_batch_query_count_does_not_grow_per_account(self):
        for teacher_id in range(10, 60):
            number = str(teacher_id).zfill(6)
            self.account(teacher_id, "gxufl", number)
            self.cached("gxufl", number)
        queries = []
        original_execute = self.conn.execute
        sqlite_trace = hasattr(self.conn, "set_trace_callback")
        if sqlite_trace:
            self.conn.set_trace_callback(queries.append)
        else:
            def record(sql, *args, **kwargs):
                queries.append(sql)
                return original_execute(sql, *args, **kwargs)
            self.conn.execute = record
        try:
            result = self.summaries(range(10, 60))
        finally:
            if sqlite_trace:
                self.conn.set_trace_callback(None)
            else:
                self.conn.execute = original_execute
        self.assertEqual(50, sum(item["linked_count"] for item in result.values()))
        self.assertLessEqual(len(queries), 12)


if __name__ == "__main__":
    unittest.main()
