"""Unit tests for the assessment-plan (考核计划表 / 过程材料) core service.

Runs on an in-memory SQLite database with a minimal ``teachers`` table so the
org-scope resolution short-circuits on the explicit college/department columns.
Covers scope normalization, payload normalization + score balance, CRUD &
visibility, docx export, and the docx signature-image extractor.
"""

import sqlite3
import unittest
from pathlib import Path

from classroom_app.db.schema_assessment_plans import ensure_assessment_plan_schema
import classroom_app.db.schema_assessment_plans as schema_mod
from classroom_app.services import assessment_plan_service as svc
from classroom_app.services import assessment_plan_generation_service as gen
from classroom_app.services import assessment_plan_import_service as imp


def _make_conn() -> sqlite3.Connection:
    schema_mod._SCHEMA_READY = False
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            email TEXT,
            is_super_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            school_code TEXT DEFAULT 'gxufl',
            school_name TEXT DEFAULT '广西外国语学院',
            college TEXT DEFAULT '',
            department TEXT DEFAULT ''
        )
        """
    )
    ensure_assessment_plan_schema(conn)
    return conn


def _add_teacher(conn, tid, name, college, department, *, super_admin=0):
    conn.execute(
        "INSERT INTO teachers (id, name, username, email, is_super_admin, is_active, "
        "school_code, school_name, college, department) "
        "VALUES (?, ?, ?, ?, ?, 1, 'gxufl', '广西外国语学院', ?, ?)",
        (tid, name, name, f"{name}@x.cn", super_admin, college, department),
    )
    return {"id": tid, "name": name, "username": name}


class ScopeNormalizationTests(unittest.TestCase):
    def test_normalize_and_label(self):
        self.assertEqual(svc.normalize_scope_level("DEPARTMENT"), "department")
        self.assertEqual(svc.normalize_scope_level("bogus"), "private")
        self.assertEqual(svc.scope_label("school"), "全校公开")
        values = {opt["value"] for opt in svc.scope_options()}
        self.assertEqual(values, {"private", "department", "college", "school"})


class NormalizeTests(unittest.TestCase):
    def test_balanced_when_items_sum_100(self):
        result = svc.normalize_plan_payload(
            {"course_name": "服务器配置与管理", "assessment_type": "考查"},
            [
                {"assessment_form": "机试", "content": "A", "score": "60"},
                {"assessment_form": "机试", "content": "B", "score": "40"},
            ],
        )
        self.assertEqual(result["score_total"], 100)
        self.assertTrue(result["score_balanced"])
        self.assertEqual(result["fields"]["total_score"], "100")
        # 考查 forces non-written mode.
        self.assertEqual(result["fields"]["assessment_mode"], "non_written")

    def test_unbalanced_is_flagged_not_rewritten(self):
        result = svc.normalize_plan_payload(
            {"course_name": "X"},
            [{"assessment_form": "机试", "content": "A", "score": "50"}],
        )
        self.assertEqual(result["score_total"], 50)
        self.assertFalse(result["score_balanced"])
        # Original score preserved (never silently changed).
        self.assertEqual(result["items"][0]["score"], "50")

    def test_empty_items_fall_back_to_default_100(self):
        result = svc.normalize_plan_payload({"course_name": "服务器配置与管理"}, [])
        self.assertTrue(result["items"])
        self.assertEqual(result["score_total"], 100)


class OfferingFieldTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.teacher = _add_teacher(self.conn, 1, "张海林", "数字科技学院", "软件工程系")
        self.conn.executescript(
            """
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                college TEXT,
                department TEXT,
                school_name TEXT
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT,
                academic_class_name TEXT,
                academic_major TEXT,
                major TEXT,
                department TEXT,
                description TEXT,
                academic_metadata_json TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                course_id INTEGER,
                class_id INTEGER,
                teacher_id INTEGER,
                semester TEXT,
                academic_teaching_class_name TEXT
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                session_date TEXT,
                order_index INTEGER,
                schedule_status TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO courses (id, name, college, department, school_name) VALUES (10, '动态 web 程序设计', '数字科技学院', '软件工程系', '广西外国语学院')"
        )
        self.conn.execute(
            "INSERT INTO classes (id, name, academic_class_name, academic_major, major, department, description, academic_metadata_json) "
            "VALUES (20, '2401班', '2401班', '软件工程', '软件工程', '软件工程系', '专升本班级', '{}')"
        )
        self.conn.execute(
            "INSERT INTO class_offerings (id, course_id, class_id, teacher_id, semester, academic_teaching_class_name) "
            "VALUES (30, 10, 20, 1, '2025-2026-2', '动态 web 程序设计-0001')"
        )
        self.conn.executemany(
            "INSERT INTO class_offering_sessions (id, class_offering_id, session_date, order_index, schedule_status) VALUES (?, 30, ?, ?, ?)",
            [
                (1, "2026-06-20", 1, "scheduled"),
                (2, "2026-06-27", 2, "cancelled"),
                (3, "2026-06-26", 3, "scheduled"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_prefill_uses_department_class_label_and_last_course_day(self):
        fields = svc.build_fields_from_offering(self.conn, 30, teacher=self.teacher)
        self.assertEqual(fields["course_name"], "动态 web 程序设计")
        self.assertEqual(fields["class_name"], "软工2401班（专升本）")
        self.assertNotIn("动态 web 程序设计-0001", fields["class_name"])
        self.assertEqual(fields["date"], "2026年06月26日")


class GenerationItemFilterTests(unittest.TestCase):
    def test_process_scores_are_replaced_by_final_exam_items(self):
        warnings: list[str] = []
        items = gen._final_exam_items_or_seed(
            [
                {"assessment_form": "平时成绩（考勤、课堂表现、作业）", "content": "整学期学习表现", "score": "20"},
                {"assessment_form": "阶段性实验项目（上机实操）", "content": "6 次实验完成情况", "score": "30"},
                {"assessment_form": "机试", "content": "期末 Web 部署与数据库综合任务", "score": "50"},
            ],
            fields={"course_name": "服务器配置与管理", "assessment_method": "机试"},
            classroom_context={},
            prompt="",
            warnings=warnings,
        )
        joined = "\n".join(f"{item.get('assessment_form')} {item.get('content')}" for item in items)
        self.assertNotIn("平时", joined)
        self.assertNotIn("考勤", joined)
        self.assertNotIn("阶段性", joined)
        self.assertEqual(sum(float(item["score"]) for item in items), 100.0)
        self.assertTrue(warnings)


class CrudAndVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.t1 = _add_teacher(self.conn, 1, "张老师", "数字科技学院", "软件工程系")
        self.t2 = _add_teacher(self.conn, 2, "李老师", "数字科技学院", "软件工程系")
        self.t3 = _add_teacher(self.conn, 3, "王老师", "数字科技学院", "网络工程系")
        self.t4 = _add_teacher(self.conn, 4, "赵老师", "外语学院", "英语系")

    def tearDown(self):
        self.conn.close()

    def _create(self, teacher, scope_level):
        return svc.create_assessment_plan(
            self.conn,
            teacher=teacher,
            title="课程考核计划表",
            fields={"course_name": "服务器配置与管理", "class_name": "软工2406"},
            items=[{"assessment_form": "机试", "content": "A", "score": "100"}],
            scope_level=scope_level,
            status="ready",
        )

    def test_private_is_owner_only(self):
        self._create(self.t1, "private")
        self.assertEqual(len(svc.list_assessment_plans(self.conn, teacher=self.t1)), 1)
        self.assertEqual(len(svc.list_assessment_plans(self.conn, teacher=self.t2)), 0)

    def test_department_scope(self):
        self._create(self.t1, "department")
        self.assertEqual(len(svc.list_assessment_plans(self.conn, teacher=self.t2)), 1)
        self.assertEqual(len(svc.list_assessment_plans(self.conn, teacher=self.t3)), 0)

    def test_school_scope_visible_across_colleges(self):
        self._create(self.t1, "school")
        self.assertEqual(len(svc.list_assessment_plans(self.conn, teacher=self.t4)), 1)

    def test_update_content_recomputes_score(self):
        plan_id = self._create(self.t1, "private")
        normalized = svc.update_content(
            self.conn,
            plan_id,
            fields={"course_name": "新课程"},
            items=[
                {"assessment_form": "机试", "content": "A", "score": "30"},
                {"assessment_form": "机试", "content": "B", "score": "70"},
            ],
        )
        self.assertTrue(normalized["score_balanced"])
        plan = svc.get_assessment_plan(self.conn, plan_id)
        self.assertEqual(plan["fields"]["course_name"], "新课程")
        self.assertEqual(plan["score_total"], 100)

    def test_inherit_clone_rewrites_owner(self):
        src = self._create(self.t1, "school")
        new_id = svc.clone_for_inherit(self.conn, src, teacher=self.t4)
        cloned = svc.get_assessment_plan(self.conn, new_id)
        self.assertEqual(int(cloned["teacher_id"]), 4)
        self.assertEqual(cloned["fields"]["examiner_name"], "赵老师")
        self.assertEqual(cloned["fields"]["reviewer_name"], "")
        self.assertEqual(cloned["scope_level"], "private")
        self.assertEqual(cloned["inherited_from"], src)

    def test_delete(self):
        plan_id = self._create(self.t1, "private")
        svc.delete_assessment_plan(self.conn, plan_id)
        self.assertIsNone(svc.get_assessment_plan(self.conn, plan_id))

    def test_super_admin_sees_all(self):
        admin = _add_teacher(self.conn, 9, "管理员", "外语学院", "英语系", super_admin=1)
        self._create(self.t1, "private")
        self.assertEqual(len(svc.list_assessment_plans(self.conn, teacher=admin)), 1)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.teacher = _add_teacher(self.conn, 1, "张海林", "数字科技学院", "软件工程系")

    def tearDown(self):
        self.conn.close()

    def test_export_docx_produces_valid_zip(self):
        plan_id = svc.create_assessment_plan(
            self.conn,
            teacher=self.teacher,
            title="服务器配置与管理 考核计划表",
            fields={
                "course_name": "服务器配置与管理",
                "class_name": "软工2406班",
                "examiner_name": "张海林",
                "academic_year": "2025-2026",
                "semester": "第一学期",
                "assessment_type": "考试",
            },
            items=[
                {"assessment_form": "机试", "content": "Linux 用户与目录管理", "score": "50"},
                {"assessment_form": "机试", "content": "Web 与数据库部署", "score": "50"},
            ],
            status="ready",
        )
        plan = svc.get_assessment_plan(self.conn, plan_id)
        content, filename = svc.export_plan_docx(self.conn, plan)
        self.assertTrue(content.startswith(b"PK"))  # docx is a zip
        self.assertTrue(filename.endswith(".docx"))
        self.assertGreater(len(content), 2000)

    def test_missing_reviewer_is_explicit_in_export_fields(self):
        plan_id = svc.create_assessment_plan(
            self.conn,
            teacher=self.teacher,
            title="课程考核计划表",
            fields={"course_name": "服务器配置与管理", "class_name": "软工2406班", "examiner_name": "张海林"},
            items=[{"assessment_form": "机试", "content": "Linux 用户与目录管理", "score": "100"}],
            status="ready",
        )
        plan = svc.get_assessment_plan(self.conn, plan_id)
        fields = svc.build_export_fields(self.conn, plan)
        self.assertEqual(fields["reviewer_name"], "【系主任未填写】")
        self.assertIn("签名库", fields["reviewer_missing_notice"])


class SignatureExtractionTests(unittest.TestCase):
    SAMPLE = (
        r"C:\Users\AngelWei\Nutstore\1\我的坚果云\广外\1 软件工程系\0- 2025-2026-1"
        r"\0-服务器配置与管理\0-期末材料\软工2406班（专升本）"
        r"\2. 课程考核计划表（非笔试考核）-《服务器配置与管理》-机试-软工2406班+软工2407班+软工2408班.docx"
    )

    def test_extract_from_real_docx_if_available(self):
        path = Path(self.SAMPLE)
        if not path.is_file():
            self.skipTest("sample docx not available in this environment")
        images = imp.extract_docx_signature_images(path)
        # The official template carries two handwritten signatures.
        self.assertGreaterEqual(len(images), 1)
        for image in images:
            self.assertIn(image["role"], {"examiner", "reviewer", "unknown"})
            self.assertTrue(image["data"])


if __name__ == "__main__":
    unittest.main()
