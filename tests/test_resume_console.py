"""Unit tests for the student resume console (简历管理与优化).

Runs on in-memory SQLite (set ``DB_ENGINE=sqlite`` when invoking unittest).
Covers schema, personal-info validation/CRUD, list-section CRUD, résumé document
CRUD + layout normalization, HTML render (+ optional LibreOffice export),
attachment helpers, nav registry, and the deterministic AI fallbacks.
"""

import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

import classroom_app.db.schema_resume as schema_mod
from classroom_app.db.schema_resume import ensure_resume_schema
from classroom_app.services.resume import resume_profile_service as P
from classroom_app.services.resume import resume_document_service as D
from classroom_app.services.resume import resume_render_service as R
from classroom_app.services.resume import resume_attachment_service as A
from classroom_app.services.resume import resume_nav_service as N
from classroom_app.services.resume import resume_generation_service as G


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
                  "resume_attachments", "resumes"):
            self.assertIn(t, names)


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
        rid = D.create_resume(c, 1, title="我的简历", template_key="classic", layout=self._layout(c))
        got = D.get_resume(c, 1, rid)
        self.assertEqual(got["status"], "rendering")
        self.assertEqual(len(got["layout"]["blocks"]), 4)
        self.assertEqual(len(D.list_resumes(c, 1)), 1)
        D.delete_resume(c, 1, rid)
        with self.assertRaises(ValueError):
            D.get_resume(c, 1, rid)

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
        rid = D.create_resume(c, 1, title="我的简历", template_key="sidebar", layout=self._layout(c))
        resume = D.get_resume(c, 1, rid)
        resume["tech_stack"] = [{"group": "编程语言", "items": ["Python"]}]
        html = R.assemble_resume_html(c, 1, resume)
        self.assertIn("张三", html)
        self.assertIn("外卖系统", html)
        self.assertIn("编程语言", html)
        self.assertTrue(html.strip().startswith("<!DOCTYPE html>"))

    def test_all_templates_render(self):
        c = _conn()
        _full_personal(c)
        for key in ("classic", "sidebar", "modern"):
            rid = D.create_resume(c, 1, title="t", template_key=key, layout=self._layout(c))
            html = R.assemble_resume_html(c, 1, D.get_resume(c, 1, rid))
            self.assertIn("张三", html)


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
        self.assertEqual(labels, ["个人资料", "简历管理"])
        active = [i for g in nav["groups"] for i in g["items"] if i["active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["key"], "personal")


class FallbackTests(unittest.TestCase):
    def test_self_intro_fallback(self):
        bundle = {"personal": {"name": "李四", "expected_position": "前端"},
                  "skill": [{"name": "Vue"}], "experience": [{"title": "官网"}]}
        text = G._fallback_self_intro(bundle, {"major_name": "软件工程"})
        self.assertIn("李四", text)
        self.assertIn("前端", text)

    def test_education_fallback(self):
        edu = G._fallback_education({"major_name": "软件工程", "college": "信息工程学院",
                                     "timeline": {"enrollment_year": 2021, "graduation_year": 2025}})
        self.assertEqual(edu["major"], "软件工程")
        self.assertEqual(edu["start_date"], "2021-09")


@unittest.skipUnless(
    __import__("classroom_app.services.libreoffice_service", fromlist=["resolve_soffice_command"]).resolve_soffice_command(),
    "LibreOffice not installed",
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
