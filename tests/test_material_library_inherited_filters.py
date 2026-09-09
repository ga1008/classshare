import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.routers.materials_parts import common


class MaterialLibraryInheritedFilterTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(
            """
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY, parent_id INTEGER, root_id INTEGER,
                teacher_id INTEGER, name TEXT, material_path TEXT, node_type TEXT,
                scope_level TEXT DEFAULT 'private', school_code TEXT DEFAULT '',
                school_name TEXT DEFAULT '', college TEXT DEFAULT '', department TEXT DEFAULT '',
                updated_at TEXT DEFAULT '', created_at TEXT DEFAULT ''
            );
            CREATE TABLE course_material_assignments (material_id INTEGER, class_offering_id INTEGER);
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE academic_semesters (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER,
                semester_id INTEGER, semester TEXT
            );
            INSERT INTO courses VALUES (101, 'Python 程序设计'), (102, '计算机网络原理');
            INSERT INTO classes VALUES (201, '软件一班'), (202, '计科二班');
            INSERT INTO academic_semesters VALUES (1, '2026-2027-1');
            INSERT INTO class_offerings VALUES (301, 201, 101, 1, ''), (302, 202, 102, 1, '');
            INSERT INTO course_materials
                (id, parent_id, root_id, teacher_id, name, material_path, node_type,
                 scope_level, school_code, school_name, college, department)
            VALUES (1, NULL, 1, 10, 'course', 'course', 'folder',
                    'department', 'gxufl', '广外', '数字科技学院', '网络工程系');
            """
        )
        self.add_material(2, 1, "course/lesson_3", node_type="folder")
        self.add_material(3, 2, "course/lesson_3/lesson_3.html")
        self.add_material(4, 1, "course/lesson_4", node_type="folder")

    def add_material(self, material_id, parent_id, path, *, root_id=1, teacher_id=10, node_type="file"):
        self.conn.execute(
            """
            INSERT INTO course_materials (id, parent_id, root_id, teacher_id, name, material_path, node_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (material_id, parent_id, root_id, teacher_id, path.rsplit("/", 1)[-1], path, node_type),
        )

    def assign(self, material_id, offering_id):
        self.conn.execute("INSERT INTO course_material_assignments VALUES (?, ?)", (material_id, offering_id))

    def rows(self, *ids):
        placeholders = ",".join("?" for _ in ids)
        return self.conn.execute(f"SELECT * FROM course_materials WHERE id IN ({placeholders}) ORDER BY id", ids).fetchall()

    def filter(self, rows, **filters):
        values = dict(scope_filter="all", school="", college="", department="", course="", class_name="")
        values.update(filters)
        return common._apply_material_library_filters(rows, teacher_id=10, **values)

    def test_git_descendants_inherit_course_and_class_without_duplicate_assignments(self):
        self.assign(1, 301)
        self.assign(3, 301)

        attached = common._attach_material_assignment_facets(self.conn, self.rows(2, 3, 4))
        filtered = self.filter(attached, course="Python 程序设计", class_name="软件一班")

        self.assertEqual([2, 3, 4], [row["id"] for row in filtered])
        self.assertEqual(["Python 程序设计"], attached[1]["assigned_course_names"])
        self.assertEqual(["Python 程序设计 / 软件一班 / 2026-2027-1"], attached[1]["assigned_offering_labels"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM course_material_assignments").fetchone()[0])
        facets = common._build_material_filter_facets(attached, teacher_id=10)
        self.assertEqual(["Python 程序设计"], facets["courses"])
        self.assertEqual(["软件一班"], facets["classes"])

    def test_nested_assignment_augments_ancestors_but_never_siblings_or_other_roots(self):
        self.assign(1, 301)
        self.assign(2, 302)
        self.add_material(5, 1, "course/lesson_30")
        self.add_material(6, None, "course", root_id=6, teacher_id=20, node_type="folder")
        self.add_material(7, 6, "course/lesson_3/lesson_3.html", root_id=6, teacher_id=20)

        attached = common._attach_material_assignment_facets(self.conn, self.rows(3, 4, 5, 7))
        by_id = {row["id"]: row for row in attached}

        self.assertEqual({"Python 程序设计", "计算机网络原理"}, set(by_id[3]["assigned_course_names"]))
        self.assertEqual(["Python 程序设计"], by_id[4]["assigned_course_names"])
        self.assertEqual(["Python 程序设计"], by_id[5]["assigned_course_names"])
        self.assertEqual([], by_id[7]["assigned_course_names"])

    def test_unassigned_parent_does_not_gain_descendant_or_sibling_assignment(self):
        self.assign(2, 302)

        attached = common._attach_material_assignment_facets(self.conn, self.rows(1, 3, 4))

        self.assertEqual([], attached[0]["assigned_course_names"])
        self.assertEqual(["计算机网络原理"], attached[1]["assigned_course_names"])
        self.assertEqual([], attached[2]["assigned_course_names"])

    def test_effective_scope_and_organization_filtering_do_not_modify_stored_access(self):
        original = dict(self.rows(3)[0])
        attached = common._attach_material_assignment_facets(self.conn, [original])

        filtered = self.filter(attached, scope_filter="department", school="广外", college="数字科技学院", department="网络工程系")

        self.assertEqual([3], [row["id"] for row in filtered])
        self.assertEqual("department", attached[0]["scope_level"])
        self.assertEqual(10, attached[0]["teacher_id"])
        self.assertEqual("private", original["scope_level"])
        self.assertEqual("", original["department"])
        self.assertEqual(original, dict(self.rows(3)[0]))

    def test_existing_visibility_gate_still_excludes_inaccessible_descendants(self):
        self.assign(1, 301)
        with patch.object(common, "_material_visibility_condition", return_value=("m.scope_level = 'public'", [])):
            rows = common._list_material_rows_for_parent(self.conn, 99, self.rows(1)[0])
        self.assertEqual([], rows)

    def test_bulk_decoration_uses_two_queries_for_many_git_files(self):
        self.assign(1, 301)
        for material_id in range(10, 110):
            self.add_material(material_id, 2, f"course/lesson_3/asset_{material_id}.js")
        rows = self.rows(*range(10, 110))
        queries = []
        self.conn.set_trace_callback(queries.append)
        try:
            attached = common._attach_material_assignment_facets(self.conn, rows)
        finally:
            self.conn.set_trace_callback(None)

        self.assertEqual(2, len(queries))
        self.assertEqual(100, len(attached))
        self.assertTrue(all(row["assigned_course_names"] == ["Python 程序设计"] for row in attached))


if __name__ == "__main__":
    unittest.main()
