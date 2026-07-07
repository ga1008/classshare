"""Unit tests for the teacher 评学表 (教师评学表 / 过程材料) core service.

Runs on an in-memory SQLite database with a minimal ``teachers`` table so the
org-scope resolution short-circuits on the explicit college/department columns.
Covers scope + rating computation, payload normalization onto the fixed 10-row
template, completeness/missing-field logic, CRUD & visibility, score-band
coercion in the generator, and the pixel-faithful docx export.
"""

import sqlite3
import unittest

from classroom_app.db.schema_teacher_evaluations import ensure_teacher_evaluation_schema
import classroom_app.db.schema_teacher_evaluations as schema_mod
from classroom_app.services import teacher_evaluation_service as svc
from classroom_app.services import teacher_evaluation_generation_service as gen


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
    ensure_teacher_evaluation_schema(conn)
    return conn


def _add_teacher(conn, tid, name, college, department, *, super_admin=0):
    conn.execute(
        "INSERT INTO teachers (id, name, username, email, is_super_admin, is_active, "
        "school_code, school_name, college, department) "
        "VALUES (?, ?, ?, ?, ?, 1, 'gxufl', '广西外国语学院', ?, ?)",
        (tid, name, name, f"{name}@x.cn", super_admin, college, department),
    )
    return {"id": tid, "name": name, "username": name}


def _full_scores(scores):
    return [{"score": s} for s in scores]


class RatingTests(unittest.TestCase):
    def test_rating_bands(self):
        self.assertEqual(svc.compute_rating(95), "优秀")
        self.assertEqual(svc.compute_rating(90), "优秀")
        self.assertEqual(svc.compute_rating(89), "良好")
        self.assertEqual(svc.compute_rating(80), "良好")
        self.assertEqual(svc.compute_rating(79), "一般")
        self.assertEqual(svc.compute_rating(70), "一般")
        self.assertEqual(svc.compute_rating(69), "较差")
        self.assertEqual(svc.compute_rating(0), "较差")


class NormalizeTests(unittest.TestCase):
    def test_snaps_onto_fixed_ten_rows(self):
        result = svc.normalize_evaluation_payload(
            {"course_name": "服务器配置与管理", "二级学院": "信息工程学院"},
            _full_scores([8, 9, 8, 7, 9, 8, 7, 9, 8, 9]),
            "评语",
        )
        self.assertEqual(len(result["items"]), 10)
        self.assertEqual(result["items"][0]["indicator"], svc.EVALUATION_INDICATORS[0][1])
        self.assertEqual(result["items"][0]["group"], "学习态度")
        self.assertEqual(result["items"][9]["group"], "学习效果")
        self.assertEqual(result["score_total"], 82)
        self.assertEqual(result["rating"], "良好")
        self.assertEqual(result["fields"]["college"], "信息工程学院")
        self.assertEqual(result["fields"]["school"], "广西外国语学院")

    def test_scores_clamped_to_0_10(self):
        result = svc.normalize_evaluation_payload({}, _full_scores([15, -3, 8, 8, 8, 8, 8, 8, 8, 8]), "")
        self.assertEqual(result["items"][0]["score"], "10")
        self.assertEqual(result["items"][1]["score"], "0")

    def test_scores_matched_by_leading_number(self):
        items = [{"indicator": "10.活学活用…", "score": 9}, {"indicator": "1.尊敬师长…", "score": 5}]
        result = svc.normalize_evaluation_payload({}, items, "")
        self.assertEqual(result["items"][0]["score"], "5")
        self.assertEqual(result["items"][9]["score"], "9")

    def test_rating_blank_until_all_scored(self):
        result = svc.normalize_evaluation_payload({}, _full_scores([8, 9, 8, 7, 9, 8, 7, 9, 8, ""]), "x")
        self.assertEqual(result["rating"], "")


class CompletenessTests(unittest.TestCase):
    def test_missing_fields_lists_gaps(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        eid = svc.create_evaluation(conn, teacher=teacher, title="t", fields={}, items=[])
        evaluation = svc.get_evaluation(conn, eid)
        missing = svc.missing_fields(evaluation)
        self.assertIn("课程名称", missing)
        self.assertTrue(any("评价得分" in m for m in missing))
        self.assertIn("学习情况分析与教学改革建议", missing)
        self.assertFalse(evaluation["is_complete"])

    def test_complete_when_all_present(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        eid = svc.create_evaluation(
            conn,
            teacher=teacher,
            title="服务器配置与管理 评学表",
            fields={"course_name": "服务器配置与管理", "class_name": "软工231", "college": "信息工程学院",
                    "teacher_name": "张老师", "evaluate_date": "2026年06月20日"},
            items=_full_scores([8, 9, 8, 7, 9, 8, 7, 9, 8, 9]),
            analysis="本学期整体表现良好。",
        )
        evaluation = svc.get_evaluation(conn, eid)
        self.assertEqual(svc.missing_fields(evaluation), [])
        self.assertTrue(evaluation["is_complete"])
        self.assertEqual(evaluation["rating"], "良好")


class CrudVisibilityTests(unittest.TestCase):
    def test_private_hidden_from_others_shared_visible(self):
        conn = _make_conn()
        owner = _add_teacher(conn, 1, "A", "信息工程学院", "软件工程系")
        other = _add_teacher(conn, 2, "B", "信息工程学院", "软件工程系")
        private_id = svc.create_evaluation(conn, teacher=owner, title="private", fields={}, items=[])
        shared_id = svc.create_evaluation(
            conn, teacher=owner, title="shared", fields={}, items=[], scope_level="department"
        )
        ids = {c["id"] for c in svc.list_evaluations(conn, teacher=other)}
        self.assertIn(shared_id, ids)
        self.assertNotIn(private_id, ids)

    def test_clone_for_inherit_resets_owner(self):
        conn = _make_conn()
        owner = _add_teacher(conn, 1, "A", "信息工程学院", "软件工程系")
        other = _add_teacher(conn, 2, "B老师", "信息工程学院", "软件工程系")
        src = svc.create_evaluation(
            conn, teacher=owner, title="源", fields={"course_name": "X", "teacher_name": "A"},
            items=_full_scores([8] * 10), analysis="a", scope_level="college",
        )
        new_id = svc.clone_for_inherit(conn, src, teacher=other)
        clone = svc.get_evaluation(conn, new_id)
        self.assertEqual(clone["fields"]["teacher_name"], "B老师")
        self.assertEqual(clone["teacher_id"], 2)
        self.assertEqual(clone["scope_level"], "private")


class GeneratorBandTests(unittest.TestCase):
    def test_total_coerced_into_60_95(self):
        low = gen._coerce_scores([1] * 10)
        high = gen._coerce_scores([10] * 10)
        self.assertGreaterEqual(sum(low), 60)
        self.assertLessEqual(sum(low), 95)
        self.assertGreaterEqual(sum(high), 60)
        self.assertLessEqual(sum(high), 95)
        self.assertTrue(all(1 <= s <= 10 for s in low + high))

    def test_clean_analysis_strips_markdown_and_system_words(self):
        out = gen._clean_analysis("## 标题\n- 要点 **粗体** 由教学辅助系统生成")
        self.assertNotIn("#", out)
        self.assertNotIn("*", out)
        self.assertNotIn("教学辅助系统", out)


class ExportTests(unittest.TestCase):
    def test_export_docx_bytes(self):
        evaluation = {
            "id": "x",
            "title": "服务器配置与管理 教师评学表",
            "fields": {"course_name": "服务器配置与管理", "class_name": "软工231", "college": "信息工程学院",
                       "teacher_name": "张老师", "teacher_title": "讲师", "evaluate_date": "2026年06月20日",
                       "academic_year": "2025-2026", "semester": "第二学期"},
            "items": svc.normalize_evaluation_payload({}, _full_scores([8, 9, 8, 7, 9, 8, 7, 9, 8, 9]), "")["items"],
            "analysis": "本学期该班级学习态度端正。\n1.课堂参与积极。\n2.自主学习仍需加强。",
            "rating": "良好",
        }
        artifact = svc.export_evaluation_artifact(evaluation, requested_format="docx")
        self.assertTrue(artifact.filename.endswith(".docx"))
        self.assertGreater(len(artifact.content), 5000)
        self.assertEqual(artifact.content[:2], b"PK")


if __name__ == "__main__":
    unittest.main()
