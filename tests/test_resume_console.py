"""Unit tests for the student resume console (简历管理与优化).

Runs on in-memory SQLite (set ``DB_ENGINE=sqlite`` when invoking unittest).
Covers schema, personal-info validation/CRUD, list-section CRUD, résumé document
CRUD + layout normalization, HTML render (+ optional LibreOffice export),
attachment helpers, nav registry, and the deterministic AI fallbacks.
"""

import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

import classroom_app.db.schema_resume as schema_mod
from classroom_app.db.schema_resume import ensure_resume_schema
from classroom_app.services.resume import resume_profile_service as P
from classroom_app.services.resume import resume_document_service as D
from classroom_app.services.resume import resume_render_service as R
from classroom_app.services.resume import resume_attachment_service as A
from classroom_app.services.resume import resume_nav_service as N
from classroom_app.services.resume import resume_generation_service as G
from classroom_app.services.resume import resume_ai_service as AI
from classroom_app.services.resume import resume_import_service as I
from classroom_app.services.resume import resume_readiness_service as Y


def _conn() -> sqlite3.Connection:
    schema_mod._SCHEMA_READY = False
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_resume_schema(c)
    return c


def _full_personal(c, sid=1):
    return P.update_personal_info(c, sid, {
        "name": "张三", "gender": "男", "birthday": "2002-01",
        "email": "z@example.com", "expected_position": "后端开发工程师", "phone": "13800000000",
    })


class SchemaTests(unittest.TestCase):
    def test_tables_created_idempotent(self):
        c = _conn()
        schema_mod._SCHEMA_READY = False
        ensure_resume_schema(c)  # re-run must not raise
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'resume%'").fetchall()}
        for t in ("resume_personal_info", "resume_self_intros", "resume_certificates",
                  "resume_skills", "resume_experiences", "resume_educations",
                  "resume_attachments", "resume_job_targets", "resume_applications", "resumes"):
            self.assertIn(t, names)
        resume_columns = {r[1] for r in c.execute("PRAGMA table_info(resumes)").fetchall()}
        for column in ("source_file_hash", "source_filename", "source_mime_type",
                       "source_file_size", "import_summary_json", "source_context_json"):
            self.assertIn(column, resume_columns)


class PersonalInfoTests(unittest.TestCase):
    def test_required_validation(self):
        c = _conn()
        with self.assertRaises(ValueError):
            P.update_personal_info(c, 1, {"name": "张三"})

    def test_email_format(self):
        c = _conn()
        with self.assertRaises(ValueError):
            P.update_personal_info(c, 1, {"name": "张三", "gender": "男", "birthday": "2002-01",
                                          "email": "not-an-email", "expected_position": "后端"})

    def test_phone_can_be_the_only_contact_method(self):
        c = _conn()
        info = P.update_personal_info(c, 1, {
            "name": "张三", "phone": "13800000000", "expected_position": "后端开发工程师",
        })
        self.assertEqual(info["phone"], "13800000000")

    def test_contact_method_is_required(self):
        c = _conn()
        with self.assertRaisesRegex(ValueError, "联系方式"):
            P.update_personal_info(c, 1, {"name": "张三", "expected_position": "后端开发工程师"})

    def test_create_and_get(self):
        c = _conn()
        _full_personal(c)
        info = P.get_personal_info(c, 1)
        self.assertEqual(info["name"], "张三")
        self.assertEqual(info["expected_position"], "后端开发工程师")
        self.assertEqual(int(info["seeded"]), 1)

    def test_seed_is_graceful_without_platform_tables(self):
        c = _conn()
        info = P.seed_personal_info_from_platform(
            c, 7,
            {"id": 7, "role": "student", "name": "平台学生", "email": "platform@example.com"},
        )
        self.assertIsInstance(info, dict)  # no crash even without students table
        self.assertEqual(info["name"], "平台学生")
        self.assertEqual(info["email"], "platform@example.com")

    def test_career_position_options_prefer_top_paths_and_dedupe(self):
        state = {
            "ok": True,
            "personalized": {"top_paths": [{"tag": "B2", "name": "后端开发工程师", "why": "匹配项目实践"}]},
            "network": {"nodes": [
                {"tag": "A1", "name": "前端开发工程师", "rec": 5},
                {"tag": "B2", "name": "后端开发工程师", "rec": 4, "highlighted": True, "glow": 0.9},
                {"tag": "C1", "name": "后端开发工程师", "rec": 3},
            ]},
        }
        options = P._career_position_options_from_state(state)
        self.assertEqual(options[0]["value"], "后端开发工程师")
        self.assertEqual(len([o for o in options if o["value"] == "后端开发工程师"]), 1)
        self.assertIn("推荐度", options[0]["meta"])

    def test_career_position_options_keep_personalized_order_before_same_tag_nodes(self):
        state = {
            "ok": True,
            "personalized": {"top_paths": [
                {"tag": "B2", "name": "后端开发工程师", "why": "首选"},
                {"tag": "B4", "name": "数据分析师", "why": "次选"},
                {"tag": "C1", "name": "产品经理", "why": "第三选择"},
            ]},
            "network": {"nodes": [
                {"tag": "B2", "name": "项目经理 / 技术管理", "rec": 5, "highlighted": True, "glow": 1},
                {"tag": "B4", "name": "商业分析师", "rec": 5, "highlighted": True, "glow": 1},
            ]},
        }
        options = P._career_position_options_from_state(state)
        self.assertEqual(
            [option["value"] for option in options[:3]],
            ["后端开发工程师", "数据分析师", "产品经理"],
        )


class SectionCrudTests(unittest.TestCase):
    def test_each_section_crud(self):
        c = _conn()
        payloads = {
            "education": {
                "kind": "university", "school": "广西外国语学院", "major": "软件工程",
                "start_date": "2021-09", "end_date": "2025-06",
            },
            "experience": {"kind": "project", "title": "校园外卖", "start_date": "2023-03", "end_date": "2023-06"},
            "skill": {"name": "Python", "acquired_date": "2023-09", "expiry_date": "2028-09"},
            "certificate": {"name": "英语四级", "acquired_date": "2022-06"},
            "self_intro": {"content_md": "我是张三"},
        }
        for section, payload in payloads.items():
            new_id = P.create_section_item(c, 1, section, payload)
            self.assertGreater(new_id, 0)
            items = P.list_section(c, 1, section)
            self.assertEqual(len(items), 1)
            P.update_section_item(c, 1, section, new_id, {**payload})
            P.delete_section_item(c, 1, section, new_id)
            self.assertEqual(len(P.list_section(c, 1, section)), 0)

    def test_required_missing_raises(self):
        c = _conn()
        with self.assertRaises(ValueError):
            P.create_section_item(c, 1, "certificate", {"acquired_date": "2022"})
        with self.assertRaises(ValueError):
            P.create_section_item(c, 1, "skill", {"name": "Python"})
        with self.assertRaises(ValueError):
            P.create_section_item(c, 1, "education", {"school": "广外"})

    def test_experience_date_order(self):
        c = _conn()
        with self.assertRaises(ValueError):
            P.create_section_item(c, 1, "experience",
                                  {"title": "x", "start_date": "2023-06", "end_date": "2023-01"})

    def test_unknown_section(self):
        c = _conn()
        with self.assertRaises(ValueError):
            P.list_section(c, 1, "bogus")

    def test_owner_isolation(self):
        c = _conn()
        sid = P.create_section_item(c, 1, "skill", {"name": "Go", "acquired_date": "2024-01"})
        self.assertEqual(len(P.list_section(c, 2, "skill")), 0)
        with self.assertRaises(ValueError):
            P.get_section_item(c, 2, "skill", sid)


class SelfIntroLifecycleTests(unittest.TestCase):
    def test_placeholder_then_finish(self):
        c = _conn()
        pid = P.create_self_intro_placeholder(c, 1)
        row = P.get_section_item(c, 1, "self_intro", pid)
        self.assertEqual(row["status"], "generating")
        P.finish_self_intro(c, pid, content_md="完整介绍", title="AI 介绍", status="ready")
        row = P.get_section_item(c, 1, "self_intro", pid)
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["content_md"], "完整介绍")


class DocumentTests(unittest.TestCase):
    def _layout(self, c):
        eid = P.create_section_item(
            c, 1, "education",
            {"kind": "university", "school": "广外", "major": "软工", "start_date": "2021-09", "end_date": "2025-06"},
        )
        xid = P.create_section_item(c, 1, "experience",
                                    {"kind": "project", "title": "外卖系统", "start_date": "2023-03",
                                     "end_date": "2023-06", "content": "FastAPI 后端"})
        sid = P.create_section_item(c, 1, "skill", {"name": "Python", "acquired_date": "2023-09"})
        return {"personal_fields": ["gender", "email", "expected_position"],
                "blocks": [{"type": "education", "ids": [eid]},
                           {"type": "experience", "ids": [xid]},
                           {"type": "skill_cert", "skill_ids": [sid], "cert_ids": []},
                           {"type": "tech_stack"}]}

    def test_create_get_list_delete(self):
        c = _conn()
        _full_personal(c)
        rid = D.create_resume(
            c, 1, title="我的简历", template_key="classic",
            target_position="Java 后端开发工程师", layout=self._layout(c),
            source_context={"source": "career", "career_tag": "A1", "resume_text": "must not persist"},
        )
        got = D.get_resume(c, 1, rid)
        self.assertEqual(got["status"], "rendering")
        self.assertEqual(got["target_position"], "Java 后端开发工程师")
        self.assertEqual(len(got["layout"]["blocks"]), 4)
        self.assertEqual(got["source_context"], {"source": "career", "career_tag": "A1"})
        listed = D.list_resumes(c, 1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["target_position"], "Java 后端开发工程师")
        self.assertEqual(listed[0]["source_context"]["career_tag"], "A1")
        D.delete_resume(c, 1, rid)
        with self.assertRaises(ValueError):
            D.get_resume(c, 1, rid)

    def test_import_resume_placeholder_and_summary(self):
        c = _conn()
        # File-store binding is covered separately; this test only inspects
        # the placeholder's persistence contract.
        with patch("classroom_app.services.file_service.lock_global_file_references"):
            rid = D.create_import_resume(
                c, 1, filename="resume.pdf", file_hash="abc123", mime_type="application/pdf", file_size=128,
            )
        row = D.get_resume(c, 1, rid)
        self.assertEqual(row["status"], "parsing")
        self.assertEqual(row["source_filename"], "resume.pdf")
        self.assertEqual(row["import_summary"]["source"], "import")
        listed = D.list_resumes(c, 1)
        self.assertEqual(listed[0]["import_summary"]["source_filename"], "resume.pdf")

    def test_layout_normalization_drops_bad_blocks(self):
        c = _conn()
        rid = D.create_resume(c, 1, title="t", template_key="classic",
                              layout={"blocks": [{"type": "bogus"}, {"type": "education", "ids": ["3", "x"]}]})
        layout = D.get_resume(c, 1, rid)["layout"]
        self.assertEqual([b["type"] for b in layout["blocks"]], ["education"])
        self.assertEqual(layout["blocks"][0]["ids"], [3])

    def test_render_html_contains_content(self):
        c = _conn()
        _full_personal(c)
        rid = D.create_resume(
            c, 1, title="我的简历", template_key="sidebar",
            target_position="Python 后端开发工程师", layout=self._layout(c),
        )
        resume = D.get_resume(c, 1, rid)
        resume["tech_stack"] = [{"group": "编程语言", "items": ["Python"]}]
        html = R.assemble_resume_html(c, 1, resume)
        self.assertIn("张三", html)
        self.assertIn("Python 后端开发工程师", html)
        self.assertIn("外卖系统", html)
        self.assertIn("编程语言", html)
        self.assertTrue(html.strip().startswith("<!DOCTYPE html>"))

    def test_optimized_summary_is_per_resume_and_export_source(self):
        c = _conn()
        _full_personal(c)
        rid = D.create_resume(
            c, 1, title="前端求职版", template_key="classic",
            target_position="前端开发工程师", layout=self._layout(c),
        )
        resume = D.get_resume(c, 1, rid)
        resume["optimized_summary_md"] = "面向前端开发岗位，掌握 Vue 与 Python，具备外卖系统项目实践，关注交互实现与协作交付。"
        resume["tech_stack"] = [{"group": "前端相关", "items": ["Vue"]}]
        html = R.assemble_resume_html(c, 1, resume)
        self.assertIn("前端开发工程师", html)
        self.assertIn("面向前端开发岗位", html)
        D.save_optimization(
            c, rid,
            target_position="前端开发工程师",
            optimized_summary_md=resume["optimized_summary_md"],
            optimization_notes={"items": ["摘要已按岗位重写"]},
            render_html=html,
            tech_stack=resume["tech_stack"],
        )
        saved = D.get_resume(c, 1, rid)
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["optimization_notes"]["items"], ["摘要已按岗位重写"])
        self.assertIn("面向前端开发岗位", saved["render_html"])

    def test_all_templates_render(self):
        c = _conn()
        _full_personal(c)
        for key in ("classic", "sidebar", "modern"):
            rid = D.create_resume(c, 1, title="t", template_key=key, layout=self._layout(c))
            html = R.assemble_resume_html(c, 1, D.get_resume(c, 1, rid))
            self.assertIn("张三", html)


class ReadinessTests(unittest.TestCase):
    def test_readiness_tracks_missing_next_actions(self):
        c = _conn()
        data = Y.build_resume_readiness(c, 1)
        self.assertLess(data["score"], 60)
        self.assertTrue(any(item["kind"] == "personal" for item in data["next_actions"]))
        _full_personal(c)
        P.create_section_item(c, 1, "self_intro", {"content_md": "面向后端开发岗位，具备项目实践。"})
        data = Y.build_resume_readiness(c, 1)
        self.assertGreaterEqual(data["score"], 40)
        self.assertTrue(any(check["key"] == "self_intro" and check["status"] == "done" for check in data["checks"]))

    def test_builder_validation_requires_target_personal_and_real_content(self):
        c = _conn()
        result = Y.validate_resume_build(c, 1, target_position="", layout={"blocks": []})
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["key"] == "target_position" for item in result["missing"]))

        _full_personal(c)
        thin = Y.validate_resume_build(c, 1, target_position="后端开发工程师", layout={"blocks": [{"type": "tech_stack"}]})
        self.assertFalse(thin["ok"])
        self.assertTrue(any(item["key"] == "content" for item in thin["missing"]))

        sid = P.create_section_item(c, 1, "skill", {"name": "Python", "acquired_date": "2024-01"})
        ok = Y.validate_resume_build(
            c,
            1,
            target_position="后端开发工程师",
            layout={"blocks": [{"type": "skill_cert", "skill_ids": [sid], "cert_ids": []}]},
        )
        self.assertTrue(ok["ok"])


class AttachmentTests(unittest.TestCase):
    def _insert(self, c, sid, kind, owner_id, h="abc"):
        from classroom_app.db.connection import execute_insert_returning_id
        return execute_insert_returning_id(
            c,
            "INSERT INTO resume_attachments (student_id, owner_kind, owner_id, file_hash, "
            "original_filename, mime_type, file_size) VALUES (?, ?, ?, ?, 'a.png', 'image/png', 100)",
            (sid, kind, owner_id, h),
        )

    def test_list_and_batch_and_delete(self):
        c = _conn()
        a1 = self._insert(c, 1, "certificate", 5, "h1")
        self._insert(c, 1, "certificate", 5, "h2")
        self.assertEqual(len(A.list_attachments(c, 1, "certificate", 5)), 2)
        grouped = A.list_attachments_for_owners(c, 1, "certificate", [5, 9])
        self.assertEqual(len(grouped[5]), 2)
        self.assertEqual(grouped[9], [])
        A.delete_attachment(c, 1, a1)
        self.assertEqual(len(A.list_attachments(c, 1, "certificate", 5)), 1)

    def test_bad_owner_kind(self):
        c = _conn()
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            A.list_attachments(c, 1, "bogus", 1)

    def test_data_uri_missing_returns_none(self):
        self.assertIsNone(A.attachment_data_uri("deadbeef-nope", "image/png"))


class NavTests(unittest.TestCase):
    def test_groups_and_active(self):
        nav = N.build_resume_nav("personal")
        labels = [g["label"] for g in nav["groups"]]
        self.assertEqual(labels, ["求职工作台", "个人资料", "简历管理"])
        active = [i for g in nav["groups"] for i in g["items"] if i["active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["key"], "personal")

    def test_home_is_first_navigation_item(self):
        nav = N.build_resume_nav("home")
        self.assertEqual(nav["groups"][0]["items"][0]["key"], "home")
        self.assertTrue(nav["groups"][0]["items"][0]["active"])

    def test_job_analysis_is_in_workbench_group(self):
        nav = N.build_resume_nav("job_targets")
        workbench_items = nav["groups"][0]["items"]
        self.assertEqual([item["key"] for item in workbench_items[:3]], ["home", "job_targets", "applications"])
        self.assertTrue(workbench_items[1]["active"])


class FallbackTests(unittest.TestCase):
    def test_self_intro_fallback(self):
        bundle = {"personal": {"name": "李四", "expected_position": "前端"},
                  "skill": [{"name": "Vue"}], "experience": [{"title": "官网"}]}
        text = G._fallback_self_intro(bundle, {"major_name": "软件工程"})
        self.assertIn("前端", text)
        self.assertIn("Vue", text)
        self.assertLessEqual(len(text), 181)
        self.assertNotIn("希望", text)
        self.assertNotIn("贵单位", text)

    def test_self_intro_fallback_ignores_test_background(self):
        bundle = {"personal": {"expected_position": "前端开发工程师"},
                  "skill": [], "experience": []}
        text = G._fallback_self_intro(bundle, {"major_name": "Regression"})
        self.assertIn("前端开发工程师", text)
        self.assertNotIn("Regression", text)
        self.assertNotIn("专业背景", text)

    def test_self_intro_fallback_uses_education_major(self):
        bundle = {"personal": {"expected_position": "前端开发工程师"},
                  "education": [{"major": "软件工程"}], "skill": [], "experience": []}
        text = G._fallback_self_intro(bundle, {})
        self.assertIn("软件工程", text)
        self.assertIn("前端开发工程师", text)

    def test_mock_ai_summary_is_rejected(self):
        self.assertFalse(AI._resume_summary_is_useful("这是用于压测的 mock AI 响应。", "前端开发工程师"))
        self.assertTrue(AI._resume_summary_is_useful(
            "面向前端开发工程师岗位，掌握 Vue 与组件化开发，具备项目实践和协作交付意识。",
            "前端开发工程师",
        ))

    def test_targeted_summary_fallback_ignores_test_background(self):
        bundle = {"personal": {}, "skill": [], "experience": []}
        text = AI._fallback_targeted_summary(bundle, {"major_name": "Regression"}, "前端开发工程师")
        self.assertIn("求职目标为前端开发工程师", text)
        self.assertNotIn("Regression", text)
        self.assertNotIn("背景，", text)

    def test_targeted_summary_fallback_uses_real_major(self):
        bundle = {"personal": {}, "education": [{"major": "软件工程"}], "skill": [], "experience": []}
        text = AI._fallback_targeted_summary(bundle, {}, "前端开发工程师")
        self.assertIn("软件工程", text)
        self.assertIn("前端开发工程师", text)

    def test_compact_resume_intro_removes_heading_and_long_tail(self):
        text = G._compact_resume_intro("## 自我介绍\n具备软件工程背景，掌握 Vue。关注代码质量与协作交付。多余内容很多很多很多。", limit=38)
        self.assertNotIn("#", text)
        self.assertNotIn("自我介绍", text)
        self.assertLessEqual(len(text), 39)

    def test_education_fallback(self):
        edu = G._fallback_education({"major_name": "软件工程", "college": "信息工程学院",
                                     "timeline": {"enrollment_year": 2021, "graduation_year": 2025}})
        self.assertEqual(edu["major"], "软件工程")
        self.assertEqual(edu["start_date"], "2021-09")


class ResumeImportTests(unittest.TestCase):
    def test_normalize_import_payload_keeps_partial_but_real_resume_items(self):
        payload = I.normalize_resume_import_payload({
            "personal": {"姓名": "王五", "邮箱": "w@example.com", "求职意向": "后端开发工程师"},
            "skills": ["Python", {"name": "FastAPI", "level": "熟悉"}],
            "experience": [{"title": "校园服务平台", "start_date": "2024.03", "end_date": "至今"}],
            "education": [{"school": "广西外国语学院", "major": "软件工程", "start_date": "2021年9月"}],
        })
        self.assertEqual(payload["personal"]["name"], "王五")
        self.assertEqual(payload["personal"]["expected_position"], "后端开发工程师")
        self.assertEqual(payload["skill"][0]["name"], "Python")
        self.assertEqual(payload["experience"][0]["start_date"], "2024-03")
        self.assertEqual(payload["experience"][0]["end_date"], "至今")
        self.assertEqual(payload["education"][0]["start_date"], "2021-09")

    def test_import_merge_fills_blanks_adds_new_and_records_conflicts(self):
        c = _conn()
        _full_personal(c)
        existing_skill = P.create_section_item(c, 1, "skill", {"name": "Python", "acquired_date": "2023-01"})
        existing_exp = P.create_section_item(
            c, 1, "experience",
            {"kind": "project", "title": "校园服务平台", "start_date": "2024-03", "end_date": "2024-06"},
        )
        payload = I.normalize_resume_import_payload({
            "personal": {"phone": "13900000000", "wechat": "wx-import", "email": "other@example.com"},
            "skill": [{"name": "Python", "level": "熟练"}, {"name": "Docker"}],
            "certificate": [{"name": "英语四级"}],
            "experience": [{
                "title": "校园服务平台",
                "start_date": "2024-03",
                "end_date": "2024-06",
                "content": "负责后端接口开发",
            }],
        })
        summary = I.merge_resume_import_payload(c, 1, payload, source_filename="resume.pdf")
        info = P.get_personal_info(c, 1)
        self.assertEqual(info["phone"], "13800000000")
        self.assertEqual(info["wechat"], "wx-import")
        self.assertEqual(info["email"], "z@example.com")
        self.assertEqual(len(P.list_section(c, 1, "skill")), 2)
        self.assertEqual(len(P.list_section(c, 1, "certificate")), 1)
        updated_skill = P.get_section_item(c, 1, "skill", existing_skill)
        self.assertEqual(updated_skill["level"], "熟练")
        updated_exp = P.get_section_item(c, 1, "experience", existing_exp)
        self.assertEqual(updated_exp["content"], "负责后端接口开发")
        self.assertIn("personal", summary["updated"])
        self.assertIn("skill", summary["added"])
        self.assertTrue(any(c["section"] == "personal" and c["field"] == "email" for c in summary["conflicts"]))

    def test_accept_import_conflict_updates_data_and_render(self):
        c = _conn()
        _full_personal(c)
        rid = D.create_resume(
            c,
            1,
            title="导入简历",
            template_key="classic",
            target_position="后端开发工程师",
            layout={"personal_fields": ["email"], "blocks": []},
        )
        D.save_import_result(
            c,
            rid,
            title="导入简历",
            target_position="后端开发工程师",
            template_key="classic",
            layout={"personal_fields": ["email"], "blocks": []},
            render_html="old",
            tech_stack=[],
            import_summary={
                "source": "import",
                "conflicts": [{
                    "section": "personal",
                    "field": "email",
                    "existing": "z@example.com",
                    "incoming": "new@example.com",
                }],
            },
        )
        result = I.accept_import_conflict(c, 1, rid, 0)
        self.assertTrue(result["changed"])
        self.assertTrue(result["summary"]["conflicts"][0]["accepted"])
        self.assertEqual(P.get_personal_info(c, 1)["email"], "new@example.com")
        saved = D.get_resume(c, 1, rid)
        self.assertIn("new@example.com", saved["render_html"])


@unittest.skipUnless(
    __import__("classroom_app.services.libreoffice_service", fromlist=["soffice_is_runnable"]).soffice_is_runnable(),
    "LibreOffice not installed or not runnable in this environment",
)
class ExportTests(unittest.TestCase):
    def test_pdf_and_docx_bytes(self):
        c = _conn()
        _full_personal(c)
        rid = D.create_resume(c, 1, title="t", template_key="classic",
                              layout={"personal_fields": ["email"], "blocks": []})
        html = R.assemble_resume_html(c, 1, D.get_resume(c, 1, rid))
        pdf = R.export_resume_bytes(html, "pdf")
        docx = R.export_resume_bytes(html, "docx")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(docx.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
