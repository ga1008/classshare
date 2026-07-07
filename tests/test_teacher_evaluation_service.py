"""Unit tests for the teacher 评学表 (教师评学表 / 过程材料) core service.

Runs on an in-memory SQLite database with a minimal ``teachers`` table so the
org-scope resolution short-circuits on the explicit college/department columns.
Covers scope + rating computation, payload normalization onto the fixed 10-row
template, completeness/missing-field logic, CRUD & visibility, score-band
coercion in the generator, and the pixel-faithful docx export.
"""

import io
import sqlite3
import unittest
import zipfile
from unittest.mock import patch

from classroom_app.routers import teacher_evaluations as router_mod
from classroom_app.db.schema_teacher_evaluations import ensure_teacher_evaluation_schema
import classroom_app.db.schema_teacher_evaluations as schema_mod
from classroom_app.services.class_label_service import build_academic_class_label
from classroom_app.services import teacher_evaluation_service as svc
from classroom_app.services import teacher_evaluation_generation_service as gen
from classroom_app.services.teacher_evaluation_text_service import split_analysis_blocks


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


class _ConnCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def _add_offering_context(conn, *, offering_id=10, teacher_id=1):
    conn.execute(
        """
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT,
            college TEXT,
            department TEXT,
            school_name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE classes (
            id INTEGER PRIMARY KEY,
            name TEXT,
            academic_class_name TEXT,
            academic_major TEXT,
            major TEXT,
            department TEXT,
            description TEXT,
            academic_metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE class_offerings (
            id INTEGER PRIMARY KEY,
            class_id INTEGER,
            course_id INTEGER,
            teacher_id INTEGER,
            semester TEXT,
            academic_teaching_class_name TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO courses (id, name, college, department, school_name) "
        "VALUES (1, '动态Web程序设计', '信息工程学院', '网络工程系', '广西外国语学院')"
    )
    conn.execute(
        "INSERT INTO classes (id, name, academic_class_name, academic_major, major, department, description, academic_metadata_json) "
        "VALUES (1, '2502班', '2502班', '网络工程', '网络工程', '网络工程系', '', '{}')"
    )
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id, semester, academic_teaching_class_name) "
        "VALUES (?, 1, 1, ?, '2025-2026-2', '动态Web程序设计·网工2502班')",
        (offering_id, teacher_id),
    )


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

    def test_prefill_uses_administrative_class_label_not_teaching_code(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        _add_offering_context(conn, offering_id=10, teacher_id=1)
        fields = svc.build_fields_from_offering(conn, 10, teacher=teacher)
        self.assertEqual(fields["class_name"], "网工2502班")
        self.assertNotIn("动态Web程序设计", fields["class_name"])

    def test_class_label_adds_suffix_for_numeric_admin_fragment(self):
        label = build_academic_class_label(
            {
                "class_department": "软件工程系",
                "academic_class_name": "2401",
                "course_name": "动态Web程序设计",
            }
        )
        self.assertEqual(label, "软工2401班")

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

    def test_prompts_require_public_classroom_voice(self):
        self.assertIn("真实课堂", gen._system_prompt())
        prompt = gen._user_prompt({}, {}, {}, "")
        self.assertIn("不得在 analysis 中说", prompt)
        self.assertIn("平台、系统、同步", prompt)

    def test_clean_analysis_rewrites_platform_evidence_as_classroom_evidence(self):
        out = gen._clean_analysis(
            "学生在平台上的表现较好，平台互动记录偏少，平台同步的出勤情况稳定；"
            "线上作业提交率较高，系统考试成绩较好，建议使用平台功能持续跟踪。由 AI 助教自动生成。"
        )
        for forbidden in ("平台", "系统", "同步", "线上", "在线", "功能", "AI", "助教", "自动生成"):
            self.assertNotIn(forbidden, out)
        self.assertIn("学生平时表现", out)
        self.assertIn("课堂互动", out)
        self.assertIn("实际出勤情况", out)
        self.assertIn("平时作业完成率", out)
        self.assertIn("课程考试", out)

    def test_rewrite_clean_analysis_can_exceed_default_300_limit(self):
        text = "该班学习状态稳定。" + "后续建议加强项目实践。" * 60
        self.assertEqual(len(gen._clean_analysis(text, limit=1200)), len(text))


class AnalysisTextTests(unittest.TestCase):
    def test_split_analysis_blocks_detects_inline_numbered_points(self):
        blocks = split_analysis_blocks("整体表现良好。1.课堂参与积极。2.建议增加项目实践。")
        self.assertEqual(blocks, ["整体表现良好。", "1.课堂参与积极。", "2.建议增加项目实践。"])

    def test_split_analysis_blocks_detects_inline_chinese_numbered_points(self):
        blocks = split_analysis_blocks("整体表现良好。一、课堂参与积极。二、建议增加项目实践。")
        self.assertEqual(blocks, ["整体表现良好。", "一、课堂参与积极。", "二、建议增加项目实践。"])


class GenerateRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_route_applies_modal_field_overrides(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        _add_offering_context(conn, offering_id=10, teacher_id=1)
        payload = {
            "class_offering_id": 10,
            "prompt": "该班作业完成度较好。",
            "fields": {
                "college": "人工智能学院",
                "teacher_title": "副教授",
                "course_name": "动态Web程序设计A",
                "class_name": "网工2502班",
                "ignored": "不会写入",
            },
        }

        def fake_generation_job(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        with patch.object(router_mod, "get_db_connection", return_value=_ConnCtx(conn)), \
                patch.object(router_mod, "run_generation_job", new=fake_generation_job), \
                patch.object(router_mod.asyncio, "create_task") as create_task:
            result = await router_mod.generate_from_classroom(_JsonRequest(payload), user=teacher)

        evaluation = svc.get_evaluation(conn, result["id"])
        self.assertEqual(evaluation["fields"]["college"], "人工智能学院")
        self.assertEqual(evaluation["fields"]["teacher_title"], "副教授")
        self.assertEqual(evaluation["fields"]["course_name"], "动态Web程序设计A")
        self.assertEqual(evaluation["fields"]["class_name"], "网工2502班")
        self.assertNotIn("ignored", evaluation["fields"])
        scheduled = create_task.call_args.args[0]
        self.assertEqual(scheduled["kwargs"]["field_overrides"]["college"], "人工智能学院")
        self.assertEqual(scheduled["kwargs"]["field_overrides"]["teacher_title"], "副教授")

    async def test_retry_route_reuses_existing_generation_fields(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        _add_offering_context(conn, offering_id=10, teacher_id=1)
        evaluation_id = svc.create_evaluation(
            conn,
            teacher=teacher,
            title="动态Web程序设计A（按班级生成）",
            fields={
                "course_name": "动态Web程序设计A",
                "class_name": "网工2502班",
                "college": "人工智能学院",
                "teacher_name": "张老师",
                "teacher_title": "副教授",
            },
            items=[],
            class_offering_id=10,
            source_type="classroom",
            status="failed",
        )
        conn.commit()

        def fake_generation_job(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        with patch.object(router_mod, "get_db_connection", return_value=_ConnCtx(conn)), \
                patch.object(router_mod, "run_generation_job", new=fake_generation_job), \
                patch.object(router_mod.asyncio, "create_task") as create_task:
            result = await router_mod.retry_evaluation(evaluation_id, user=teacher)

        self.assertTrue(result["ok"])
        scheduled = create_task.call_args.args[0]
        self.assertEqual(scheduled["kwargs"]["field_overrides"]["college"], "人工智能学院")
        self.assertEqual(scheduled["kwargs"]["field_overrides"]["teacher_title"], "副教授")
        self.assertEqual(scheduled["kwargs"]["field_overrides"]["course_name"], "动态Web程序设计A")

    async def test_rewrite_analysis_route_updates_only_analysis(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        evaluation_id = svc.create_evaluation(
            conn,
            teacher=teacher,
            title="动态Web程序设计（按班级生成）",
            fields={
                "course_name": "动态Web程序设计",
                "class_name": "网工2502班",
                "college": "信息工程学院",
                "teacher_name": "张老师",
                "evaluate_date": "2026年06月20日",
            },
            items=_full_scores([8, 8, 7, 8, 7, 8, 6, 8, 9, 7]),
            analysis="旧分析。",
            status="ready",
        )
        conn.commit()
        captured = {}

        async def fake_rewrite(context, prompt):
            captured["context"] = context
            captured["prompt"] = prompt
            return "整体学习态度端正。\n1.课堂参与较好。\n2.后续建议增加项目实践。"

        with patch.object(router_mod, "get_db_connection", return_value=_ConnCtx(conn)), \
                patch.object(router_mod, "rewrite_analysis_with_ai", new=fake_rewrite):
            result = await router_mod.rewrite_analysis(
                evaluation_id,
                _JsonRequest({"prompt": "写得更详细，约600字"}),
                user=teacher,
            )

        evaluation = svc.get_evaluation(conn, evaluation_id)
        self.assertTrue(result["ok"])
        self.assertEqual(captured["prompt"], "写得更详细，约600字")
        self.assertEqual(captured["context"]["score_total"], 76)
        self.assertEqual([item["score"] for item in evaluation["items"]], ["8", "8", "7", "8", "7", "8", "6", "8", "9", "7"])
        self.assertEqual(evaluation["fields"]["class_name"], "网工2502班")
        self.assertIn("课堂参与较好", evaluation["analysis"])
        self.assertIn("1.课堂参与较好。", result["analysis_blocks"])

    async def test_rewrite_analysis_route_failure_keeps_existing_analysis(self):
        conn = _make_conn()
        teacher = _add_teacher(conn, 1, "张老师", "信息工程学院", "软件工程系")
        evaluation_id = svc.create_evaluation(
            conn,
            teacher=teacher,
            title="动态Web程序设计",
            fields={"course_name": "动态Web程序设计", "class_name": "网工2502班", "college": "信息工程学院",
                    "teacher_name": "张老师", "evaluate_date": "2026年06月20日"},
            items=_full_scores([8] * 10),
            analysis="保持不变的旧分析。",
            status="ready",
        )
        conn.commit()

        async def failing_rewrite(context, prompt):
            raise RuntimeError("模型暂时不可用")

        with patch.object(router_mod, "get_db_connection", return_value=_ConnCtx(conn)), \
                patch.object(router_mod, "rewrite_analysis_with_ai", new=failing_rewrite):
            with self.assertRaises(router_mod.HTTPException) as raised:
                await router_mod.rewrite_analysis(evaluation_id, _JsonRequest({"prompt": "重写"}), user=teacher)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(svc.get_evaluation(conn, evaluation_id)["analysis"], "保持不变的旧分析。")


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

    def test_export_docx_splits_inline_numbered_analysis_points(self):
        evaluation = {
            "id": "x",
            "title": "动态Web程序设计 教师评学表",
            "fields": {"course_name": "动态Web程序设计", "class_name": "网工2502班", "college": "信息工程学院",
                       "teacher_name": "张老师", "teacher_title": "讲师", "evaluate_date": "2026年07月07日",
                       "academic_year": "2025-2026", "semester": "第二学期"},
            "items": svc.normalize_evaluation_payload({}, _full_scores([8, 8, 7, 8, 7, 8, 6, 8, 9, 7]), "")["items"],
            "analysis": "整体表现良好。1.课堂参与积极。2.建议增加项目实践。",
            "rating": "一般",
        }
        artifact = svc.export_evaluation_artifact(evaluation, requested_format="docx")
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as docx:
            document_xml = docx.read("word/document.xml").decode("utf-8")
        self.assertIn("<w:t>整体表现良好。</w:t>", document_xml)
        self.assertIn("<w:t>1.课堂参与积极。</w:t>", document_xml)
        self.assertIn("<w:t>2.建议增加项目实践。</w:t>", document_xml)


if __name__ == "__main__":
    unittest.main()
