import unittest
from unittest.mock import patch

from classroom_app.services import (
    academic_course_exam_sync_service,
    academic_course_sync_service,
    academic_exam_roster_sync_service,
    academic_roster_sync_service,
)


class FakeCursor:
    def __init__(self, *, row=None, rows=None, rowcount=1, lastrowid=0):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, *, columns=None, returning_id=77):
        self.columns = set(columns or ())
        self.returning_id = returning_id
        self.execute_calls = []

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, params))
        normalized = " ".join(str(sql).split())
        if "information_schema.columns" in normalized:
            return FakeCursor(rows=[{"column_name": column} for column in self.columns])
        if "RETURNING id" in normalized:
            return FakeCursor(row={"id": self.returning_id})
        return FakeCursor(rowcount=1)


class AcademicSyncPostgresWriteTests(unittest.TestCase):
    def test_course_exam_schema_postgres_validates_without_sqlite_ddl(self):
        required = {
            "id",
            "teacher_id",
            "semester_id",
            "class_offering_id",
            "course_id",
            "class_id",
            "school_code",
            "academic_year",
            "academic_term",
            "exam_key",
            "course_code",
            "course_name",
            "teaching_class_name",
            "starts_at",
            "ends_at",
            "sync_status",
            "synced_at",
        }
        conn = FakeConnection(columns=required)

        with patch.object(academic_course_exam_sync_service, "get_configured_db_engine", return_value="postgres"):
            academic_course_exam_sync_service.ensure_course_exam_schema(conn)

        sql_text = "\n".join(str(sql) for sql, _ in conn.execute_calls)
        self.assertIn("information_schema.columns", sql_text)
        self.assertNotIn("CREATE TABLE", sql_text.upper())

    def test_course_occurrence_postgres_uses_on_conflict_do_nothing(self):
        conn = FakeConnection()
        item = academic_course_sync_service.AcademicCourseScheduleItem(
            academic_year="2026",
            academic_term="1",
            course_name="Networks",
            course_code="NET101",
            teaching_class_name="NET-1",
            weeks_text="1",
            weekday=1,
            weekday_label="Mon",
            section_text="1-2",
            location="Room 101",
        )

        with patch.object(academic_course_sync_service, "get_configured_db_engine", return_value="postgres"), patch.object(
            academic_course_sync_service, "_parse_week_numbers", return_value=[1]
        ), patch.object(
            academic_course_sync_service, "_date_for_academic_week", return_value="2026-09-01"
        ), patch.object(
            academic_course_sync_service, "_parse_section_range", return_value=(1, 2, 2)
        ), patch.object(
            academic_course_sync_service, "_is_non_periodic_weeks", return_value=False
        ):
            count = academic_course_sync_service._insert_academic_occurrences(
                conn,
                teacher_id=3,
                semester={"id": 9, "name": "2026 Fall"},
                course_id=6,
                sync_item_id=44,
                item=item,
                synced_at="2026-09-01T08:00:00",
            )

        self.assertEqual(1, count)
        sql = str(conn.execute_calls[0][0])
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("DO NOTHING", sql)
        self.assertNotIn("INSERT OR IGNORE", sql)

    def test_roster_item_postgres_upsert_returns_id(self):
        conn = FakeConnection(returning_id=701)
        roster = academic_roster_sync_service.AcademicTeachingClassRoster(
            teaching_class_id="TC1",
            teaching_class_name="Teaching Class",
            academic_year="2026",
            academic_term="1",
            course_code="NET101",
            course_name="Networks",
        )

        with patch.object(academic_roster_sync_service, "get_configured_db_engine", return_value="postgres"):
            item_id = academic_roster_sync_service._upsert_roster_item(
                conn,
                teacher_id=3,
                semester={"id": 9},
                roster=roster,
                course_id=6,
                synced_at="2026-09-01T08:00:00",
                source_url="https://academic.example.test",
            )

        self.assertEqual(701, item_id)
        self.assertIn("RETURNING id", str(conn.execute_calls[0][0]))

    def test_exam_roster_item_postgres_upsert_returns_id(self):
        conn = FakeConnection(returning_id=801)
        course = academic_exam_roster_sync_service.AcademicExamCourse(
            exam_course_key="EXAM1",
            academic_year="2026",
            academic_term="1",
            course_code="NET101",
            course_name="Networks",
        )

        with patch.object(academic_exam_roster_sync_service, "get_configured_db_engine", return_value="postgres"):
            item_id = academic_exam_roster_sync_service._upsert_exam_roster_item(
                conn,
                teacher_id=3,
                semester={"id": 9},
                context={"class_offering_id": 10, "course_id": 6, "class_id": 5},
                course=course,
                students=[],
                synced_at="2026-09-01T08:00:00",
            )

        self.assertEqual(801, item_id)
        self.assertIn("RETURNING id", str(conn.execute_calls[0][0]))


if __name__ == "__main__":
    unittest.main()
