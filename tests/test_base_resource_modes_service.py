import json
import sqlite3
import unittest

from classroom_app.services.base_resource_modes_service import (
    build_class_delete_blockers,
    build_course_delete_blockers,
    build_exam_delete_blockers,
    build_material_delete_blockers,
    build_textbook_delete_blockers,
    raise_if_delete_blocked,
    serialize_exam_attributes,
    update_class_attributes,
    update_course_attributes,
    update_exam_attributes,
    update_textbook_attributes,
)


class BaseResourceModesServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1,
                is_super_admin INTEGER DEFAULT 0,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_by_teacher_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                major TEXT DEFAULT '',
                enrollment_year INTEGER,
                expected_graduation_year INTEGER,
                program_duration_years INTEGER,
                owner_role TEXT DEFAULT 'teacher',
                owner_user_pk INTEGER,
                scope_level TEXT DEFAULT 'school',
                academic_source TEXT DEFAULT '',
                academic_class_code TEXT DEFAULT '',
                academic_class_name TEXT DEFAULT '',
                academic_grade TEXT DEFAULT '',
                academic_major TEXT DEFAULT '',
                academic_sync_at TEXT,
                academic_sync_message TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                archived_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                student_id_number TEXT,
                name TEXT,
                class_id INTEGER,
                email TEXT,
                enrollment_status TEXT DEFAULT 'active'
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                description TEXT,
                sect_name TEXT,
                department TEXT,
                credits REAL,
                total_hours INTEGER,
                created_by_teacher_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                owner_role TEXT DEFAULT 'teacher',
                owner_user_pk INTEGER,
                scope_level TEXT DEFAULT 'school',
                academic_source TEXT DEFAULT '',
                academic_course_code TEXT DEFAULT '',
                academic_sync_at TEXT,
                academic_sync_message TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                archived_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE course_lessons (
                id INTEGER PRIMARY KEY,
                course_id INTEGER,
                order_index INTEGER,
                title TEXT,
                content TEXT,
                section_count INTEGER,
                source_type TEXT,
                learning_material_id INTEGER
            );
            CREATE TABLE textbooks (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                title TEXT,
                authors_json TEXT DEFAULT '[]',
                publisher TEXT DEFAULT '',
                publication_date TEXT,
                introduction TEXT DEFAULT '',
                catalog_text TEXT DEFAULT '',
                attachment_name TEXT DEFAULT '',
                attachment_path TEXT DEFAULT '',
                attachment_size INTEGER DEFAULT 0,
                attachment_mime_type TEXT DEFAULT '',
                tags_json TEXT DEFAULT '[]',
                owner_role TEXT DEFAULT 'teacher',
                owner_user_pk INTEGER,
                scope_level TEXT DEFAULT 'private',
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                published_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                archived_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                textbook_id INTEGER
            );
            CREATE TABLE exam_papers (
                id TEXT PRIMARY KEY,
                teacher_id INTEGER,
                title TEXT,
                description TEXT,
                questions_json TEXT,
                exam_config_json TEXT,
                status TEXT,
                tags_json TEXT DEFAULT '[]',
                owner_role TEXT DEFAULT 'teacher',
                owner_user_pk INTEGER,
                scope_level TEXT DEFAULT 'department',
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                ai_gen_task_id TEXT,
                ai_gen_status TEXT,
                ai_gen_error TEXT,
                published_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                archived_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE assignments (
                id TEXT PRIMARY KEY,
                course_id INTEGER,
                exam_paper_id TEXT,
                class_offering_id INTEGER,
                title TEXT,
                requirements_md TEXT,
                rubric_md TEXT
            );
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT,
                status TEXT
            );
            CREATE TABLE submission_drafts (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT,
                student_pk_id INTEGER
            );
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                parent_id INTEGER,
                root_id INTEGER,
                material_path TEXT,
                name TEXT,
                node_type TEXT,
                file_hash TEXT
            );
            CREATE TABLE course_material_assignments (
                id INTEGER PRIMARY KEY,
                material_id INTEGER,
                class_offering_id INTEGER,
                assigned_by_teacher_id INTEGER
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                learning_material_id INTEGER
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                package_material_id INTEGER,
                source_material_id INTEGER,
                parsed_material_id INTEGER,
                parent_material_id INTEGER
            );
            CREATE TABLE session_material_generation_tasks (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                session_id INTEGER,
                teacher_id INTEGER,
                generated_material_id INTEGER
            );
            """
        )
        self.conn.execute(
            """
            INSERT INTO teachers (id, school_code, school_name, college, department)
            VALUES (1, 'gxufl', 'GXUFL', 'info', 'network')
            """
        )
        self.conn.execute(
            """
            INSERT INTO classes (
                id, name, description, created_by_teacher_id, school_code, school_name,
                college, department, major, owner_user_pk, scope_level, created_at, updated_at
            )
            VALUES (10, 'JWS2302', 'old desc', 1, 'gxufl', 'GXUFL', 'info', 'network',
                    'software', 1, 'school', '2026-01-01', '2026-01-01')
            """
        )
        self.conn.execute(
            """
            INSERT INTO students (id, student_id_number, name, class_id, email, enrollment_status)
            VALUES (100, 'S001', 'Alice', 10, 'a@example.com', 'active')
            """
        )
        self.conn.execute(
            """
            INSERT INTO courses (
                id, name, description, sect_name, department, credits, total_hours,
                created_by_teacher_id, school_code, school_name, college, owner_user_pk,
                scope_level, created_at, updated_at
            )
            VALUES (20, 'Network', 'old course', '', 'network', 2, 2, 1,
                    'gxufl', 'GXUFL', 'info', 1, 'school', '2026-01-01', '2026-01-01')
            """
        )
        self.conn.execute(
            """
            INSERT INTO course_lessons (id, course_id, order_index, title, content, section_count, source_type)
            VALUES (1, 20, 1, 'Lesson 1', 'keep me', 2, 'manual')
            """
        )
        self.conn.execute(
            """
            INSERT INTO textbooks (
                id, teacher_id, title, authors_json, publisher, introduction, catalog_text,
                attachment_name, attachment_path, attachment_size, tags_json, owner_user_pk,
                scope_level, school_code, school_name, college, department, created_at, updated_at
            )
            VALUES (30, 1, 'Old Book', '["A"]', 'Old Press', 'keep intro', 'keep catalog',
                    'book.pdf', '/old/book.pdf', 12, '["old"]', 1, 'private',
                    'gxufl', 'GXUFL', 'info', 'network', '2026-01-01', '2026-01-01')
            """
        )
        self.conn.execute(
            """
            INSERT INTO exam_papers (
                id, teacher_id, title, description, questions_json, exam_config_json, status,
                tags_json, owner_user_pk, scope_level, school_code, school_name, college,
                department, created_at, updated_at
            )
            VALUES ('paper-1', 1, 'Paper', 'old desc', '{"pages":[]}', '{"ai_grading_enabled":true}',
                    'draft', '["old"]', 1, 'department', 'gxufl', 'GXUFL', 'info',
                    'network', '2026-01-01', '2026-01-01')
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_class_attribute_update_does_not_touch_students(self):
        class_row = self.conn.execute("SELECT * FROM classes WHERE id = 10").fetchone()

        update_class_attributes(
            self.conn,
            class_row=class_row,
            teacher_id=1,
            payload={"name": "JWS2302-A", "description": "new desc", "scope_level": "department"},
        )

        student = self.conn.execute("SELECT * FROM students WHERE id = 100").fetchone()
        updated = self.conn.execute("SELECT * FROM classes WHERE id = 10").fetchone()
        self.assertEqual("Alice", student["name"])
        self.assertEqual("S001", student["student_id_number"])
        self.assertEqual("JWS2302-A", updated["name"])
        self.assertEqual("department", updated["scope_level"])

    def test_course_attribute_update_does_not_touch_lesson_content(self):
        course_row = self.conn.execute("SELECT * FROM courses WHERE id = 20").fetchone()

        update_course_attributes(
            self.conn,
            course_row=course_row,
            teacher_id=1,
            payload={"name": "Network Plus", "credits": 3, "scope_level": "private"},
        )

        lesson = self.conn.execute("SELECT * FROM course_lessons WHERE course_id = 20").fetchone()
        updated = self.conn.execute("SELECT * FROM courses WHERE id = 20").fetchone()
        self.assertEqual("keep me", lesson["content"])
        self.assertEqual("Network Plus", updated["name"])
        self.assertEqual("private", updated["scope_level"])

    def test_textbook_attribute_update_does_not_touch_content_or_attachment(self):
        textbook_row = self.conn.execute("SELECT * FROM textbooks WHERE id = 30").fetchone()

        update_textbook_attributes(
            self.conn,
            textbook_row=textbook_row,
            teacher_id=1,
            payload={
                "title": "New Book",
                "authors": ["B"],
                "tags": ["shared"],
                "scope_level": "school",
            },
        )

        updated = self.conn.execute("SELECT * FROM textbooks WHERE id = 30").fetchone()
        self.assertEqual("keep intro", updated["introduction"])
        self.assertEqual("keep catalog", updated["catalog_text"])
        self.assertEqual("/old/book.pdf", updated["attachment_path"])
        self.assertEqual("New Book", updated["title"])
        self.assertEqual("school", updated["scope_level"])

    def test_exam_attribute_update_does_not_touch_questions(self):
        paper_row = self.conn.execute("SELECT * FROM exam_papers WHERE id = 'paper-1'").fetchone()

        update_exam_attributes(
            self.conn,
            paper_row=paper_row,
            teacher_id=1,
            payload={"tags": ["final"], "status": "published", "scope_level": "school"},
        )

        updated = self.conn.execute("SELECT * FROM exam_papers WHERE id = 'paper-1'").fetchone()
        self.assertEqual({"pages": []}, json.loads(updated["questions_json"]))
        self.assertEqual(["final"], json.loads(updated["tags_json"]))
        self.assertEqual("published", updated["status"])
        self.assertEqual("school", updated["scope_level"])

    def test_exam_attributes_report_content_lock_from_submissions_and_drafts(self):
        self.conn.execute("INSERT INTO assignments (id, exam_paper_id) VALUES ('a1', 'paper-1')")
        self.conn.execute("INSERT INTO submissions (id, assignment_id, status) VALUES (1, 'a1', 'submitted')")
        self.conn.execute("INSERT INTO submission_drafts (id, assignment_id, student_pk_id) VALUES (1, 'a1', 100)")
        paper_row = self.conn.execute("SELECT * FROM exam_papers WHERE id = 'paper-1'").fetchone()

        attributes = serialize_exam_attributes(self.conn, paper_row, 1)

        self.assertTrue(attributes["permissions"]["content_locked"])
        self.assertEqual(1, attributes["stats"]["submission_count"])
        self.assertEqual(1, attributes["stats"]["draft_count"])

    def test_class_delete_blockers_include_students_and_classroom_history(self):
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (90, 10, 20, 1)"
        )
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, exam_paper_id, class_offering_id) VALUES ('a-class', 20, 'paper-1', 90)"
        )
        self.conn.execute("INSERT INTO submissions (id, assignment_id, status) VALUES (2, 'a-class', 'submitted')")

        blockers = build_class_delete_blockers(self.conn, 10)

        self.assertEqual(1, blockers["学生"])
        self.assertEqual(1, blockers["课堂"])
        self.assertEqual(1, blockers["作业"])
        self.assertEqual(1, blockers["学生提交"])
        with self.assertRaisesRegex(Exception, "不能直接删除"):
            raise_if_delete_blocked("班级", blockers)

    def test_course_delete_blockers_include_assignments_and_files(self):
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, exam_paper_id) VALUES ('a-course', 20, 'paper-1')"
        )
        self.conn.execute(
            "CREATE TABLE course_files (id INTEGER PRIMARY KEY, course_id INTEGER, file_hash TEXT)"
        )
        self.conn.execute("INSERT INTO course_files (id, course_id, file_hash) VALUES (1, 20, 'hash-1')")

        blockers = build_course_delete_blockers(self.conn, 20)

        self.assertEqual(1, blockers["作业"])
        self.assertEqual(1, blockers["课程文件"])

    def test_textbook_delete_blockers_include_classroom_bindings(self):
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, textbook_id) VALUES (91, 10, 20, 1, 30)"
        )

        blockers = build_textbook_delete_blockers(self.conn, 30)

        self.assertEqual({"课堂绑定": 1}, blockers)

    def test_exam_delete_blockers_include_all_assignment_history(self):
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, exam_paper_id) VALUES ('a-exam', 20, 'paper-1')"
        )
        self.conn.execute("INSERT INTO submission_drafts (id, assignment_id, student_pk_id) VALUES (2, 'a-exam', 100)")

        blockers = build_exam_delete_blockers(self.conn, "paper-1")

        self.assertEqual(1, blockers["作业"])
        self.assertEqual(1, blockers["提交草稿"])

    def test_material_delete_blockers_cover_subtree_references(self):
        self.conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, parent_id, root_id, material_path, name, node_type, file_hash)
            VALUES (70, 1, NULL, 70, 'folder', 'folder', 'folder', NULL)
            """
        )
        self.conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, parent_id, root_id, material_path, name, node_type, file_hash)
            VALUES (71, 1, 70, 70, 'folder/readme.md', 'readme.md', 'file', 'hash-2')
            """
        )
        self.conn.execute(
            "INSERT INTO course_material_assignments (id, material_id, class_offering_id, assigned_by_teacher_id) VALUES (1, 71, 90, 1)"
        )
        self.conn.execute(
            "INSERT INTO course_lessons (id, course_id, order_index, title, content, section_count, source_type, learning_material_id) VALUES (2, 20, 2, 'L2', '', 1, 'manual', 71)"
        )
        self.conn.execute(
            "INSERT INTO material_ai_import_records (id, source_material_id) VALUES (1, 71)"
        )
        material = self.conn.execute("SELECT * FROM course_materials WHERE id = 70").fetchone()

        blockers = build_material_delete_blockers(self.conn, material)

        self.assertEqual(1, blockers["课堂材料分配"])
        self.assertEqual(1, blockers["课程课次引用"])
        self.assertEqual(1, blockers["AI导入记录"])


if __name__ == "__main__":
    unittest.main()
