"""全局搜索服务的单元测试（sqlite）：范围隔离、可见性、通配转义。"""

import os
import unittest
from datetime import datetime

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.global_search_service import search_everything

TEACHER_MINE = 967
TEACHER_OTHER = 977
CLASS_MINE = 961
CLASS_OTHER = 962
COURSE_MINE = 961
COURSE_OTHER = 962
OFFERING_MINE = 961
OFFERING_OTHER = 962
STUDENT_ID = 9801
MATERIAL_ID = 9811
ASSIGNMENT_PUBLISHED = 9061
ASSIGNMENT_DRAFT = 9062
ASSIGNMENT_OTHER = 9063
BLOG_PUBLIC = 9601
BLOG_DRAFT = 9602

STUDENT_USER = {"id": STUDENT_ID, "role": "student", "name": "小明"}
TEACHER_USER = {"id": TEACHER_MINE, "role": "teacher", "name": "李老师"}


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM blog_posts WHERE id IN (?, ?)", (BLOG_PUBLIC, BLOG_DRAFT)),
        ("DELETE FROM course_material_assignments WHERE class_offering_id IN (?, ?)", (OFFERING_MINE, OFFERING_OTHER)),
        ("DELETE FROM course_materials WHERE id = ?", (MATERIAL_ID,)),
        ("DELETE FROM assignments WHERE id IN (?, ?, ?)", (ASSIGNMENT_PUBLISHED, ASSIGNMENT_DRAFT, ASSIGNMENT_OTHER)),
        ("DELETE FROM students WHERE id = ?", (STUDENT_ID,)),
        ("DELETE FROM class_offerings WHERE id IN (?, ?)", (OFFERING_MINE, OFFERING_OTHER)),
        ("DELETE FROM courses WHERE id IN (?, ?)", (COURSE_MINE, COURSE_OTHER)),
        ("DELETE FROM classes WHERE id IN (?, ?)", (CLASS_MINE, CLASS_OTHER)),
        ("DELETE FROM teachers WHERE id IN (?, ?)", (TEACHER_MINE, TEACHER_OTHER)),
    ):
        try:
            conn.execute(sql, params)
        except Exception:
            pass


def _seed(conn):
    now = datetime.now().isoformat(timespec="seconds")
    for tid, name in ((TEACHER_MINE, "李老师"), (TEACHER_OTHER, "别班老师")):
        conn.execute(
            """
            INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tid, name, f"teacher{tid}@example.test", "hashed", "gxufl", "测试学院", 1),
        )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_MINE, "计网2201班", TEACHER_MINE))
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_OTHER, "外语2202班", TEACHER_OTHER))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_MINE, "计算机网络原理", TEACHER_MINE))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_OTHER, "计算机网络实践", TEACHER_OTHER))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_MINE, CLASS_MINE, COURSE_MINE, TEACHER_MINE),
    )
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_OTHER, CLASS_OTHER, COURSE_OTHER, TEACHER_OTHER),
    )
    conn.execute(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        (STUDENT_ID, "S9801", "小明", CLASS_MINE),
    )
    conn.execute(
        """
        INSERT INTO course_materials (id, teacher_id, material_path, name, node_type, file_ext)
        VALUES (?, ?, ?, ?, 'file', 'md')
        """,
        (MATERIAL_ID, TEACHER_MINE, "search-test/tcp.md", "TCP网络握手精讲"),
    )
    conn.execute(
        """
        INSERT INTO course_material_assignments (material_id, class_offering_id, assigned_by_teacher_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (MATERIAL_ID, OFFERING_MINE, TEACHER_MINE, now),
    )
    for aid, offering, course, title, status in (
        (ASSIGNMENT_PUBLISHED, OFFERING_MINE, COURSE_MINE, "网络分层作业", "published"),
        (ASSIGNMENT_DRAFT, OFFERING_MINE, COURSE_MINE, "网络草稿作业", "new"),
        (ASSIGNMENT_OTHER, OFFERING_OTHER, COURSE_OTHER, "网络别班作业", "published"),
    ):
        conn.execute(
            """
            INSERT INTO assignments (id, course_id, class_offering_id, title, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (aid, course, offering, title, status, now),
        )
    for bid, title, status, visibility in (
        (BLOG_PUBLIC, "网络世界漫游指南", "published", "public"),
        (BLOG_DRAFT, "网络未发布草稿", "draft", "public"),
    ):
        conn.execute(
            """
            INSERT INTO blog_posts (id, author_identity, author_role, author_user_pk, author_display_name,
                                    title, content_md, status, visibility, created_at)
            VALUES (?, 'teacher:967', 'teacher', ?, '李老师', ?, '正文', ?, ?, ?)
            """,
            (bid, TEACHER_MINE, title, status, visibility, now),
        )


class GlobalSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            _seed(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def _titles(self, payload, kind):
        for group in payload["groups"]:
            if group["kind"] == kind:
                return [item["title"] for item in group["results"]]
        return []

    def test_student_scope_isolation(self):
        with get_db_connection() as conn:
            payload = search_everything(conn, STUDENT_USER, "网络")

        self.assertIn("计算机网络原理", self._titles(payload, "classroom"))
        self.assertIn("TCP网络握手精讲", self._titles(payload, "material"))
        assignments = self._titles(payload, "assignment")
        self.assertIn("网络分层作业", assignments)
        # 别班作业与未发布草稿绝不出现。
        self.assertNotIn("网络别班作业", assignments)
        self.assertNotIn("网络草稿作业", assignments)
        blogs = self._titles(payload, "blog")
        self.assertIn("网络世界漫游指南", blogs)
        self.assertNotIn("网络未发布草稿", blogs)

    def test_teacher_sees_own_draft_but_not_other_offering(self):
        with get_db_connection() as conn:
            payload = search_everything(conn, TEACHER_USER, "网络")
        assignments = self._titles(payload, "assignment")
        self.assertIn("网络草稿作业", assignments)
        self.assertNotIn("网络别班作业", assignments)

    def test_wildcards_are_escaped(self):
        with get_db_connection() as conn:
            payload = search_everything(conn, STUDENT_USER, "%%")
        # 通配符被转义成字面量，不会变成 match-all。
        self.assertEqual(payload["total"], 0)

    def test_short_query_returns_empty(self):
        with get_db_connection() as conn:
            payload = search_everything(conn, STUDENT_USER, "网")
        self.assertEqual(payload["groups"], [])
        self.assertEqual(payload["total"], 0)


class GlobalSearchApiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        from classroom_app.app import app
        from classroom_app.dependencies import get_current_user

        with get_db_connection() as conn:
            _cleanup(conn)
            _seed(conn)
            conn.commit()
        self._app = app
        self._dep = get_current_user
        app.dependency_overrides[get_current_user] = lambda: dict(STUDENT_USER)

    def tearDown(self):
        self._app.dependency_overrides.pop(self._dep, None)
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_api_returns_grouped_results(self):
        from fastapi.testclient import TestClient

        client = TestClient(self._app)
        resp = client.get("/api/global-search", params={"q": "网络"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "success")
        self.assertGreaterEqual(payload["total"], 3)
        kinds = {group["kind"] for group in payload["groups"]}
        self.assertIn("classroom", kinds)
        self.assertIn("assignment", kinds)


if __name__ == "__main__":
    unittest.main()
