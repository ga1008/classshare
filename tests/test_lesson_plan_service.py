"""Unit tests for the lesson-plan core service (CRUD / scope / inherit).

Runs on an in-memory SQLite database with a minimal ``teachers`` table so the
org-scope resolution short-circuits on the explicit college/department columns
(no optional academic tables required).
"""

import sqlite3
import unittest

from classroom_app.db.schema_lesson_plans import ensure_lesson_plan_schema
import classroom_app.db.schema_lesson_plans as schema_mod
from classroom_app.services import lesson_plan_service as svc


def _make_conn() -> sqlite3.Connection:
    # Reset the module-level "schema ready" cache so each fresh in-memory DB
    # actually gets the table created.
    schema_mod._SCHEMA_READY = False
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            is_super_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            school_code TEXT DEFAULT 'gxufl',
            school_name TEXT DEFAULT '广西外国语学院',
            college TEXT DEFAULT '',
            department TEXT DEFAULT ''
        )
        """
    )
    ensure_lesson_plan_schema(conn)
    return conn


def _add_teacher(conn, tid, name, college, department, *, super_admin=0):
    conn.execute(
        "INSERT INTO teachers (id, name, username, is_super_admin, is_active, "
        "school_code, school_name, college, department) "
        "VALUES (?, ?, ?, ?, 1, 'gxufl', '广西外国语学院', ?, ?)",
        (tid, name, name, super_admin, college, department),
    )
    return {"id": tid, "name": name, "username": name}


class ScopeNormalizationTests(unittest.TestCase):
    def test_normalize_and_label(self):
        self.assertEqual(svc.normalize_scope_level("DEPARTMENT"), "department")
        self.assertEqual(svc.normalize_scope_level("bogus"), "private")
        self.assertEqual(svc.scope_label("school"), "全校公开")
        self.assertEqual(svc.scope_label(None), "私有")
        values = {opt["value"] for opt in svc.scope_options()}
        self.assertEqual(values, {"private", "department", "college", "school"})


class PayloadNormalizationTests(unittest.TestCase):
    def test_sessions_get_sequential_index(self):
        payload = svc.normalize_lesson_plan_payload(
            {
                "cover": {"course_name": "服务器配置与管理", "extra": "drop me"},
                "sessions": [
                    {"chapter": "第1章", "schedule": {"week_index": "1", "weekday": "1"}},
                    {"chapter": "第2章"},
                ],
            }
        )
        self.assertEqual(payload["cover"]["course_name"], "服务器配置与管理")
        self.assertNotIn("extra", payload["cover"])
        self.assertEqual([s["index"] for s in payload["sessions"]], [1, 2])
        self.assertEqual(payload["sessions"][0]["schedule"]["week_index"], 1)
        self.assertEqual(payload["sessions"][0]["schedule"]["weekday"], 1)

    def test_course_category_short_label_expands_for_cover(self):
        payload = svc.normalize_lesson_plan_payload(
            {
                "cover": {"course_name": "动态web程序设计", "course_category": "专业"},
                "sessions": [],
            }
        )
        self.assertEqual(payload["cover"]["course_category"], "专业限选课程")


    def test_wrapped_alias_payload_is_normalized(self):
        payload = svc.normalize_lesson_plan_payload(
            {
                "lesson_plan": {
                    "metadata": {
                        "courseTitle": "Dynamic Web Programming",
                        "teacherName": "Teacher A",
                        "className": "SE2401",
                        "textbookName": "Web Engineering",
                    },
                    "lessonSessions": [
                        {
                            "topic": "Vue Components",
                            "teachingObjectives": "Understand component composition",
                            "teachingProcess": "Demo and practice",
                            "scheduleText": "week 3 sections 1-2",
                        }
                    ],
                }
            }
        )
        self.assertEqual(payload["cover"]["course_name"], "Dynamic Web Programming")
        self.assertEqual(payload["cover"]["teacher_name"], "Teacher A")
        self.assertEqual(payload["cover"]["class_name"], "SE2401")
        self.assertEqual(payload["cover"]["textbook"], "Web Engineering")
        self.assertEqual(payload["sessions"][0]["chapter"], "Vue Components")
        self.assertEqual(payload["sessions"][0]["objectives"], "Understand component composition")
        self.assertEqual(payload["sessions"][0]["process"], "Demo and practice")
        self.assertEqual(payload["sessions"][0]["schedule"]["text"], "week 3 sections 1-2")


class CoverAutoFillTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.teacher = _add_teacher(self.conn, 1, "Teacher A", "Digital College", "Software")
        self.conn.executescript(
            """
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                credits REAL,
                total_hours INTEGER,
                college TEXT,
                department TEXT,
                school_name TEXT
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT,
                academic_class_name TEXT
            );
            CREATE TABLE textbooks (
                id INTEGER PRIMARY KEY,
                title TEXT,
                publisher TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                course_id INTEGER,
                class_id INTEGER,
                textbook_id INTEGER,
                semester TEXT
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                academic_sync_item_id INTEGER,
                order_index INTEGER
            );
            CREATE TABLE teacher_academic_course_sync_items (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                course_id INTEGER,
                course_name TEXT,
                teaching_class_name TEXT,
                course_nature TEXT,
                course_total_hours_text TEXT,
                total_hours_text TEXT,
                academic_year_name TEXT,
                academic_term_name TEXT,
                synced_at TEXT,
                updated_at TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO courses (id, name, credits, total_hours, college, department, school_name) "
            "VALUES (10, 'Dynamic Web', 2.0, 0, 'Digital College', 'Software', 'GXUFL')"
        )
        self.conn.execute(
            "INSERT INTO classes (id, name, academic_class_name) VALUES (20, 'SE2401', 'SE2401')"
        )
        self.conn.execute(
            "INSERT INTO textbooks (id, title, publisher) VALUES (30, 'Spring Boot', 'PT Press')"
        )
        self.conn.execute(
            "INSERT INTO class_offerings (id, teacher_id, course_id, class_id, textbook_id, semester) "
            "VALUES (40, 1, 10, 20, 30, '2025-2026-2')"
        )
        self.conn.execute(
            "INSERT INTO teacher_academic_course_sync_items ("
            "id, teacher_id, course_id, course_name, teaching_class_name, course_nature, "
            "course_total_hours_text, total_hours_text, academic_year_name, academic_term_name, synced_at, updated_at"
            ") VALUES (50, 1, 10, 'Dynamic Web', 'SE2401', 'Professional Elective', "
            "'32', '', '2025-2026', 'Term 2', '2026-06-25T12:00:00', '2026-06-25T12:00:00')"
        )
        self.conn.execute(
            "INSERT INTO class_offering_sessions (id, class_offering_id, academic_sync_item_id, order_index) "
            "VALUES (60, 40, 50, 1)"
        )

    def tearDown(self):
        self.conn.close()

    def test_cover_reads_sync_item_without_class_offering_id_column(self):
        cover = svc.build_cover_from_offering(self.conn, 40, teacher=self.teacher)

        self.assertEqual(cover["course_name"], "Dynamic Web")
        self.assertEqual(cover["course_category"], "Professional Elective")
        self.assertEqual(cover["total_hours"], "32")
        self.assertEqual(cover["semester_label"], "2025-2026 Term 2")

        plan_id = svc.create_lesson_plan(
            self.conn,
            teacher=self.teacher,
            title="Generated",
            cover=cover,
            sessions=[],
            class_offering_id=40,
            source_type="classroom",
            status="generating",
        )
        self.conn.commit()
        self.assertIsNotNone(svc.get_lesson_plan(self.conn, plan_id))


class CrudAndVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.t1 = _add_teacher(self.conn, 1, "张老师", "数字科技学院", "软件工程系")
        self.t2 = _add_teacher(self.conn, 2, "李老师", "数字科技学院", "软件工程系")  # same dept
        self.t3 = _add_teacher(self.conn, 3, "王老师", "数字科技学院", "网络工程系")  # same college
        self.t4 = _add_teacher(self.conn, 4, "赵老师", "外语学院", "英语系")  # different college

    def tearDown(self):
        self.conn.close()

    def _create(self, teacher, scope_level):
        return svc.create_lesson_plan(
            self.conn,
            teacher=teacher,
            title="教案",
            cover={"course_name": "服务器配置与管理"},
            sessions=[{"chapter": "第1章"}],
            scope_level=scope_level,
            status="ready",
        )

    def test_private_is_owner_only(self):
        self._create(self.t1, "private")
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t1)), 1)
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t2)), 0)

    def test_department_scope_same_dept_visible(self):
        self._create(self.t1, "department")
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t2)), 1)  # same dept
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t3)), 0)  # other dept

    def test_college_scope(self):
        self._create(self.t1, "college")
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t3)), 1)  # same college
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t4)), 0)  # other college

    def test_school_scope_visible_across_colleges(self):
        self._create(self.t1, "school")
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=self.t4)), 1)

    def test_update_content_and_get(self):
        plan_id = self._create(self.t1, "private")
        svc.update_content(
            self.conn,
            plan_id,
            cover={"course_name": "新课程名"},
            sessions=[{"chapter": "第1章"}, {"chapter": "第2章"}],
            status="ready",
        )
        plan = svc.get_lesson_plan(self.conn, plan_id)
        self.assertEqual(plan["cover"]["course_name"], "新课程名")
        self.assertEqual(plan["session_count"], 2)

    def test_tags_dedup_and_limit(self):
        plan_id = self._create(self.t1, "private")
        tags = svc.update_tags(self.conn, plan_id, ["a", "a", "b", ""])
        self.assertEqual(tags, ["a", "b"])

    def test_inherit_clone_rewrites_owner(self):
        src = self._create(self.t1, "school")
        new_id = svc.clone_for_inherit(self.conn, src, teacher=self.t4)
        cloned = svc.get_lesson_plan(self.conn, new_id)
        self.assertEqual(int(cloned["teacher_id"]), 4)
        self.assertEqual(cloned["cover"]["teacher_name"], "赵老师")
        self.assertEqual(cloned["scope_level"], "private")
        self.assertEqual(cloned["inherited_from"], src)
        self.assertIn("继承", cloned["title"])
        # original content preserved
        self.assertEqual(cloned["sessions"][0]["chapter"], "第1章")

    def test_delete(self):
        plan_id = self._create(self.t1, "private")
        svc.delete_lesson_plan(self.conn, plan_id)
        self.assertIsNone(svc.get_lesson_plan(self.conn, plan_id))

    def test_super_admin_sees_all(self):
        admin = _add_teacher(self.conn, 9, "管理员", "外语学院", "英语系", super_admin=1)
        self._create(self.t1, "private")
        self.assertEqual(len(svc.list_lesson_plans(self.conn, teacher=admin)), 1)


if __name__ == "__main__":
    unittest.main()
