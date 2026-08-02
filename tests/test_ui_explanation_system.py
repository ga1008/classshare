from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from classroom_app.services.manage_nav_service import build_manage_nav
from tools.ui.audit_ui_copy import scan


ROOT = Path(__file__).resolve().parents[1]


class UiExplanationSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = Environment(
            loader=FileSystemLoader(ROOT / "templates"),
            autoescape=select_autoescape(("html", "xml")),
        )

    def test_shared_asset_loads_explanation_module(self) -> None:
        partial = (ROOT / "templates/partials/ui_system_assets.html").read_text(encoding="utf-8")
        self.assertIn("js/ui_explanation.js", partial)
        for template_name in (
            "base.html",
            "manage/layout.html",
            "resume/layout.html",
            "assessment_plan_editor.html",
            "exam_editor.html",
            "exam_take.html",
            "lesson_plan_editor.html",
            "teacher_evaluation_editor.html",
        ):
            source = (ROOT / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("partials/ui_system_assets.html", source, template_name)

    def test_jinja_macro_escapes_structured_links_and_uses_required_defaults(self) -> None:
        template = self.environment.from_string(
            """
            {% from "macros/ui_explanation.html" import explain_attrs %}
            <button {{ explain_attrs(
                '同步说明',
                '后台运行。',
                links=[{'label': '账号', 'href': '/manage/me/credentials'}]
            ) }}></button>
            """
        )
        rendered = template.render()
        self.assertIn("data-explain", rendered)
        self.assertIn('data-explain-title="同步说明"', rendered)
        # Defaults stay implicit (runtime falls back to 2000/650/auto) to keep markup lean.
        self.assertNotIn("data-explain-delay", rendered)
        self.assertNotIn("data-explain-long-press", rendered)
        self.assertNotIn("data-explain-placement", rendered)
        self.assertIn("&#34;label&#34;", rendered)
        self.assertNotIn('<script', rendered.lower())

    def test_jinja_macro_emits_only_overridden_timings(self) -> None:
        template = self.environment.from_string(
            """
            {% from "macros/ui_explanation.html" import explain_attrs %}
            <button {{ explain_attrs('说明', '文本', placement='right', delay=1200, long_press=900) }}></button>
            """
        )
        rendered = template.render()
        self.assertIn('data-explain-placement="right"', rendered)
        self.assertIn('data-explain-delay="1200"', rendered)
        self.assertIn('data-explain-long-press="900"', rendered)

    def test_changed_templates_compile(self) -> None:
        for template_name in (
            "manage/layout.html",
            "manage/academic_final_materials.html",
            "manage/assessment_plans.html",
            "manage/materials.html",
            "manage/teacher_evaluations.html",
            "manage/classes.html",
            "manage/classrooms.html",
            "manage/courses.html",
            "manage/exams.html",
            "manage/signatures.html",
            "manage/textbooks.html",
            "manage/ai.html",
            "manage/offerings.html",
            "manage/semesters.html",
            "manage/workflow.html",
            "manage/polls.html",
            "manage/life_tips.html",
        ):
            self.environment.get_template(template_name)

    def test_manage_navigation_exposes_dedicated_help_contract(self) -> None:
        nav = build_manage_nav({"id": 1, "role": "teacher"}, "materials")
        items = [
            item
            for domain in nav["domains"]
            for group in domain["groups"]
            for item in group["items"]
        ]
        materials = next(item for item in items if item["key"] == "materials")
        self.assertTrue(materials["help_text"])
        # Fallback strips the "标签：" prefix so the popover title is not repeated.
        self.assertEqual(materials["ai_hint"], "材料：" + materials["help_text"])
        self.assertFalse(materials["help_text"].startswith("材料："))

    def test_runtime_is_delegated_lazy_and_replaces_legacy_css_tooltips(self) -> None:
        script = (ROOT / "static/js/ui_explanation.js").read_text(encoding="utf-8")
        css = (ROOT / "static/css/ui-system.src.css").read_text(encoding="utf-8")
        self.assertIn("const DEFAULT_DELAY_MS = 2000", script)
        self.assertIn("const DEFAULT_LONG_PRESS_MS = 650", script)
        self.assertIn("document.createElement('aside')", script)
        self.assertIn("window.LanShareExplanation", script)
        self.assertIn("[data-lp-tip]", script)
        self.assertNotIn("[data-lp-tip]::after", css)
        self.assertIn("backdrop-filter: blur(26px)", css)

    def test_copy_auditor_covers_all_ui_source_families(self) -> None:
        candidates, summary = scan()
        self.assertGreaterEqual(summary["files_scanned"], 350)
        self.assertGreaterEqual(summary["candidate_count"], 200)
        roots = summary["by_root"]
        self.assertIn("templates", roots)
        self.assertIn("static", roots)
        self.assertIn("frontend", roots)
        self.assertTrue(any(item.priority == "P1" for item in candidates))

    def test_acceptance_plan_preserves_critical_instructions(self) -> None:
        plan = (ROOT / "docs/ui-copy-simplification-plan.md").read_text(encoding="utf-8")
        self.assertIn("必须持续可见", plan)
        self.assertIn("最终验收标准", plan)
        self.assertIn("P1/P2 候选全部标记", plan)


if __name__ == "__main__":
    unittest.main()
