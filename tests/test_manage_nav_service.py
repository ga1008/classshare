import unittest
from pathlib import Path

from fastapi.routing import APIRoute

from classroom_app.app import app
from classroom_app.services.manage_nav_service import (
    MANAGE_DOMAIN_ORDER,
    MANAGE_NAV_ITEMS,
    build_manage_nav,
    iter_manage_legacy_redirects,
    iter_platform_manage_routes,
)
from classroom_app.services.platform_knowledge_service import PLATFORM_ROUTES
from classroom_app.dependencies import require_teacher_domain


class ManageNavServiceTests(unittest.TestCase):
    def test_manage_nav_registry_is_complete_and_unique(self):
        keys = [item.key for item in MANAGE_NAV_ITEMS]
        self.assertEqual(len(keys), len(set(keys)))

        legal_domains = {*MANAGE_DOMAIN_ORDER, "admin"}
        for item in MANAGE_NAV_ITEMS:
            with self.subTest(key=item.key):
                self.assertIn(item.domain, legal_domains)
                self.assertTrue(item.href.startswith("/manage/"))
                self.assertTrue(item.label.strip())
                self.assertTrue(item.search_text.strip())
                self.assertTrue(item.ai_hint.strip())
                for legacy_href in item.legacy_hrefs:
                    self.assertTrue(legacy_href.startswith("/manage"))
                    self.assertNotEqual(legacy_href, item.href)

    def test_grading_rubric_entry_sits_under_assessment_plan(self):
        process_items = [item.key for item in MANAGE_NAV_ITEMS if item.group == "过程材料"]
        self.assertIn("assessment_plans", process_items)
        self.assertIn("grading_rubrics", process_items)
        self.assertIn("ordinary_grade_records", process_items)
        self.assertIn("exam_grade_records", process_items)
        self.assertIn("final_grade_transcripts", process_items)
        self.assertIn("teacher_evaluations", process_items)
        self.assertLess(process_items.index("assessment_plans"), process_items.index("grading_rubrics"))
        self.assertLess(process_items.index("grading_rubrics"), process_items.index("ordinary_grade_records"))
        self.assertLess(process_items.index("ordinary_grade_records"), process_items.index("exam_grade_records"))
        self.assertLess(process_items.index("exam_grade_records"), process_items.index("final_grade_transcripts"))
        self.assertLess(process_items.index("final_grade_transcripts"), process_items.index("teacher_evaluations"))

        labels = {
            item.key: item.label
            for item in MANAGE_NAV_ITEMS
            if item.key
            in {
                "assessment_plans",
                "grading_rubrics",
                "ordinary_grade_records",
                "exam_grade_records",
                "final_grade_transcripts",
            }
        }
        self.assertEqual(
            {
                "assessment_plans": "考核计划表",
                "grading_rubrics": "评分细则表",
                "ordinary_grade_records": "平时成绩表",
                "exam_grade_records": "考核登分表",
                "final_grade_transcripts": "期末成绩单",
            },
            labels,
        )
        self.assertEqual({5}, {len(label) for label in labels.values()})

    def test_process_material_nav_keeps_notes_in_popover_not_rail(self):
        nav = build_manage_nav({"id": 1, "role": "teacher"}, "ordinary_grade_records", is_super_admin=False)
        process_items = [
            item
            for domain in nav["domains"]
            for group in domain["groups"]
            if group["label"] == "过程材料"
            for item in group["items"]
        ]
        by_key = {item["key"]: item for item in process_items}

        # Workflow notes/badges stay in the registry (search + popover), but the
        # sidebar renders titles only.
        self.assertEqual("Excel", by_key["ordinary_grade_records"]["nav_badge"])
        self.assertIn("学校模板 Excel", by_key["ordinary_grade_records"]["nav_note"])
        self.assertIn("已绑定试卷", by_key["exam_grade_records"]["nav_note"])
        self.assertIn("同步教务考试名单", by_key["final_grade_transcripts"]["nav_note"])
        self.assertIn("Excel", by_key["ordinary_grade_records"]["search_text"])
        # The hover popover absorbs the workflow note.
        self.assertIn("学校模板 Excel", by_key["ordinary_grade_records"]["help_text"])
        self.assertIn("同步教务考试名单", by_key["final_grade_transcripts"]["help_text"])

        template = Path("templates/manage/layout.html").read_text(encoding="utf-8")
        self.assertIn("manage-nav-item__copy", template)
        self.assertNotIn("manage-nav-item__note", template)
        self.assertNotIn("manage-nav-item__badge", template)
        self.assertIn("explain_attrs(item.label, item.help_text", template)
        # Collapsible category rail contract.
        self.assertIn("manage-nav-group-toggle", template)
        self.assertIn("manage-nav-group-items", template)

    def test_life_tips_lives_under_platform_admin(self):
        life_tips = next(item for item in MANAGE_NAV_ITEMS if item.key == "life_tips")
        self.assertEqual("admin", life_tips.domain)
        self.assertEqual("平台管理", life_tips.group)
        self.assertEqual("super_admin", life_tips.required_flag)

        admin_nav = build_manage_nav({"id": 1, "role": "teacher"}, "life_tips", is_super_admin=True)
        admin_keys = [
            item["key"]
            for domain in admin_nav["domains"]
            if domain["key"] == "admin"
            for group in domain["groups"]
            for item in group["items"]
        ]
        self.assertIn("life_tips", admin_keys)
        self.assertEqual("admin", admin_nav["active_domain"])

        teacher_nav = build_manage_nav({"id": 1, "role": "teacher"}, "workflow", is_super_admin=False)
        teaching_keys = [
            item["key"]
            for domain in teacher_nav["domains"]
            for group in domain["groups"]
            for item in group["items"]
        ]
        self.assertNotIn("life_tips", teaching_keys)

    def test_smart_classroom_and_course_schedule_live_under_academic(self):
        by_key = {item.key: item for item in MANAGE_NAV_ITEMS}
        self.assertEqual("academic", by_key["system_smart_classroom_integrations"].domain)
        self.assertEqual("数据同步", by_key["system_smart_classroom_integrations"].group)
        self.assertEqual("academic", by_key["course_schedule"].domain)

    def test_manage_nav_filters_admin_items_and_marks_active_domain(self):
        teacher_nav = build_manage_nav({"id": 1, "role": "teacher"}, "classrooms", is_super_admin=False)
        self.assertEqual("academic", teacher_nav["active_domain"])
        # Regular teachers keep the clean three-domain shell: no admin tab.
        self.assertEqual(list(MANAGE_DOMAIN_ORDER), [domain["key"] for domain in teacher_nav["domains"]])
        self.assertTrue(any(domain["key"] == "academic" and domain["active"] for domain in teacher_nav["domains"]))

        admin_nav = build_manage_nav({"id": 1, "role": "teacher"}, "system_users", is_super_admin=True)
        self.assertEqual("admin", admin_nav["active_domain"])
        # Super admins get the admin domain as a fourth tab, rendered last.
        self.assertEqual([*MANAGE_DOMAIN_ORDER, "admin"], [domain["key"] for domain in admin_nav["domains"]])
        admin_domain = admin_nav["domains"][-1]
        self.assertTrue(admin_domain["active"])
        self.assertTrue(admin_domain["groups"])
        self.assertIn("system_users", admin_nav["hrefs"])

    def test_manage_legacy_redirects_are_derived_from_registry(self):
        redirects = iter_manage_legacy_redirects()
        by_legacy = {item["legacy_href"]: item["canonical_href"] for item in redirects}
        self.assertEqual("/manage/teaching/offerings", by_legacy["/manage/offerings"])
        self.assertEqual("/manage/academic/classrooms", by_legacy["/manage/classrooms"])
        self.assertEqual("/manage/me/password-resets", by_legacy["/manage/system/password-resets"])

    def test_manage_canonical_and_legacy_routes_are_registered(self):
        paths = {
            route.path
            for route in app.routes
            if isinstance(route, APIRoute) and "GET" in (route.methods or set())
        }
        for item in MANAGE_NAV_ITEMS:
            with self.subTest(href=item.href):
                self.assertIn(item.href, paths)
        for redirect in iter_manage_legacy_redirects():
            with self.subTest(legacy_href=redirect["legacy_href"]):
                self.assertIn(redirect["legacy_href"], paths)

    def test_platform_knowledge_uses_manage_nav_registry(self):
        manage_routes = [route for route in iter_platform_manage_routes()]
        platform_paths = {route["path"] for route in PLATFORM_ROUTES}
        self.assertTrue({route["path"] for route in manage_routes}.issubset(platform_paths))

        route_text = "\n".join(route["path"] for route in PLATFORM_ROUTES)
        self.assertIn("/manage/academic/gongwen", route_text)
        self.assertNotIn("/manage/gongwen", route_text)
        self.assertNotIn("/manage/system/password-resets", route_text)

    def test_teacher_domain_dependency_marks_domain_without_changing_identity(self):
        dependency = require_teacher_domain("academic")
        user = dependency({"id": 7, "role": "teacher", "name": "Teacher"})
        self.assertEqual("academic", user["manage_domain"])
        self.assertEqual("teacher", user["role"])

        with self.assertRaises(ValueError):
            require_teacher_domain("unknown")


if __name__ == "__main__":
    unittest.main()
