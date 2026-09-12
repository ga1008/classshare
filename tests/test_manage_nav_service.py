import unittest
from pathlib import Path

from fastapi.routing import APIRoute

from classroom_app.app import app
from classroom_app.core import templates
from classroom_app.services.manage_nav_service import (
    ARCHIVE_STEP_TOTAL,
    MANAGE_DOMAIN_HOME_KEYS,
    MANAGE_DOMAIN_META,
    MANAGE_DOMAIN_ORDER,
    MANAGE_NAV_ITEMS,
    build_dashboard_domain_cards,
    build_manage_nav,
    canonical_manage_href,
    iter_archive_steps,
    iter_manage_legacy_redirects,
    iter_platform_manage_routes,
)
from classroom_app.services.platform_knowledge_service import PLATFORM_ROUTES
from classroom_app.dependencies import require_teacher_domain


class ManageNavServiceTests(unittest.TestCase):
    def test_registered_navigation_icons_render_without_fallback(self):
        render_icon = templates.get_template("macros/manage_icons.html").module.manage_icon
        fallback = str(render_icon("unknown")).strip()
        for item in MANAGE_NAV_ITEMS:
            with self.subTest(key=item.key, icon=item.icon):
                self.assertNotEqual(fallback, str(render_icon(item.icon)).strip())

    def test_manage_nav_registry_is_complete_and_unique(self):
        keys = [item.key for item in MANAGE_NAV_ITEMS]
        self.assertEqual(len(keys), len(set(keys)))

        legal_domains = {*MANAGE_DOMAIN_ORDER, "admin"}
        for item in MANAGE_NAV_ITEMS:
            with self.subTest(key=item.key):
                self.assertIn(item.domain, legal_domains)
                if item.domain == "home":
                    self.assertEqual("/dashboard", item.href)
                else:
                    self.assertTrue(item.href.startswith("/manage/"))
                self.assertTrue(item.label.strip())
                self.assertTrue(item.search_text.strip())
                self.assertTrue(item.ai_hint.strip())
                for legacy_href in item.legacy_hrefs:
                    self.assertTrue(legacy_href.startswith("/manage"))
                    self.assertNotEqual(legacy_href, item.href)

    def test_six_domains_follow_teacher_lifecycle(self):
        self.assertEqual(("home", "teaching", "library", "archive", "academic", "me"), MANAGE_DOMAIN_ORDER)
        by_domain: dict[str, list[str]] = {}
        for item in MANAGE_NAV_ITEMS:
            by_domain.setdefault(item.domain, []).append(item.key)
        self.assertEqual(["home"], by_domain["home"])
        self.assertEqual(
            ["offering_hub", "offering_merge", "workflow", "semesters", "offerings", "ai", "classes"],
            by_domain["teaching"],
        )
        self.assertEqual(
            ["material_hub", "courses", "textbooks", "exams", "materials", "lesson_plans", "polls"],
            by_domain["library"],
        )
        self.assertEqual(
            [
                "academic_overview",
                "course_schedule",
                "system_academic_integrations",
                "system_smart_classroom_integrations",
                "classrooms",
                "gongwen",
                "system_gongwen_integrations",
            ],
            by_domain["academic"],
        )
        self.assertEqual(
            ["teacher_profile", "work_inbox", "me_settings", "me_security", "me_notifications", "me_email", "signatures", "signature_workflows", "teacher_credentials", "system_password_resets"],
            by_domain["me"],
        )
        self.assertTrue(all(item.required_flag == "super_admin" for item in MANAGE_NAV_ITEMS if item.domain == "admin"))
        # 域身份只用 tone 令牌名表达，不再硬编码色值。
        for meta in MANAGE_DOMAIN_META.values():
            self.assertNotIn("accent", meta)
            self.assertTrue(meta["tone"])
        # 教学域首页 = 课堂管理。
        self.assertEqual("offering_hub", MANAGE_DOMAIN_HOME_KEYS["teaching"])
        self.assertEqual("/manage/teaching/classroom-hub", canonical_manage_href("offering_hub"))

    def test_archive_domain_orders_nine_steps_by_pipeline(self):
        steps = iter_archive_steps()
        self.assertEqual(
            [
                "assessment_plans",
                "grading_rubrics",
                "ordinary_grade_records",
                "exam_grade_records",
                "final_grade_transcripts",
                "academic_grade_registers",
                "academic_exam_analyses",
                "teacher_evaluations",
                "postclass_materials",
            ],
            [item.key for item in steps],
        )
        self.assertEqual(list(range(1, 10)), [item.step for item in steps])
        self.assertEqual(9, ARCHIVE_STEP_TOTAL)
        for item in steps:
            with self.subTest(key=item.key):
                self.assertEqual("archive", item.domain)
                self.assertTrue(item.href.startswith("/manage/archive/"))

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
            if domain["key"] == "archive"
            for group in domain["groups"]
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
        # Six-domain accordion contract; the shell carries no inline styles or domain tabs.
        self.assertIn("manage-nav-domain-toggle", template)
        self.assertIn("manage-nav-domain-items", template)
        self.assertNotIn("manage-domain-tab", template)
        self.assertNotIn("<style", template)

    def test_life_tips_lives_under_platform_admin(self):
        life_tips = next(item for item in MANAGE_NAV_ITEMS if item.key == "life_tips")
        self.assertEqual("admin", life_tips.domain)
        self.assertEqual("平台管理", life_tips.group)
        self.assertEqual("super_admin", life_tips.required_flag)
        self.assertEqual("/manage/system/life-tips", life_tips.href)
        self.assertIn("/manage/teaching/life-tips", life_tips.legacy_hrefs)

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
        self.assertEqual("/manage/academic/smart-classroom", by_key["system_smart_classroom_integrations"].href)
        self.assertEqual("/manage/academic/course-schedule", by_key["course_schedule"].href)

    def test_manage_nav_filters_admin_items_and_marks_active_domain(self):
        teacher_nav = build_manage_nav({"id": 1, "role": "teacher"}, "classrooms", is_super_admin=False)
        self.assertEqual("academic", teacher_nav["active_domain"])
        # 普通教师看到六个域，没有平台域。
        self.assertEqual(list(MANAGE_DOMAIN_ORDER), [domain["key"] for domain in teacher_nav["domains"]])
        self.assertTrue(any(domain["key"] == "academic" and domain["active"] for domain in teacher_nav["domains"]))
        me_keys = [
            item["key"]
            for domain in teacher_nav["domains"]
            if domain["key"] == "me"
            for group in domain["groups"]
            for item in group["items"]
        ]
        for personal_key in ("teacher_profile", "signatures", "teacher_credentials", "system_password_resets"):
            self.assertIn(personal_key, me_keys)
        self.assertNotIn("system_users", teacher_nav["hrefs"])

        admin_nav = build_manage_nav({"id": 1, "role": "teacher"}, "system_users", is_super_admin=True)
        self.assertEqual("admin", admin_nav["active_domain"])
        self.assertEqual([*MANAGE_DOMAIN_ORDER, "admin"], [domain["key"] for domain in admin_nav["domains"]])
        admin_domain = admin_nav["domains"][-1]
        self.assertTrue(admin_domain["active"])
        self.assertTrue(admin_domain["groups"])
        self.assertIn("system_users", admin_nav["hrefs"])
        # 每个域带条目数与域首页链接，供侧栏手风琴与首页域卡使用。
        for domain in admin_nav["domains"]:
            self.assertGreater(domain["item_count"], 0)
            self.assertTrue(domain["href"].startswith("/"))

    def test_dashboard_domain_cards_follow_registry(self):
        cards = build_dashboard_domain_cards()
        self.assertEqual(["teaching", "library", "archive", "academic", "me"], [card["domain"] for card in cards])
        self.assertEqual("/manage/teaching/classroom-hub", cards[0]["href"])
        admin_cards = build_dashboard_domain_cards(is_super_admin=True)
        self.assertEqual("admin", admin_cards[-1]["domain"])
        for card in admin_cards:
            self.assertTrue(card["tone"])
            self.assertTrue(card["actions"])

    def test_library_domain_hosts_material_hub_and_categories(self):
        by_key = {item.key: item for item in MANAGE_NAV_ITEMS}
        self.assertEqual("library", by_key["material_hub"].domain)
        self.assertEqual("/manage/library", by_key["material_hub"].href)
        self.assertEqual("archive", by_key["postclass_materials"].domain)
        self.assertEqual("/manage/library/courses", by_key["courses"].href)
        self.assertEqual("/manage/library/exams", by_key["exams"].href)
        self.assertIn("/manage/teaching/exams", by_key["exams"].legacy_hrefs)

        nav = build_manage_nav({"id": 1, "role": "teacher"}, "material_hub", is_super_admin=False)
        self.assertEqual("library", nav["active_domain"])
        categories = nav["library_categories"]
        keys = [category["key"] for category in categories]
        self.assertIn("learning_docs", keys)
        self.assertIn("postclass", keys)
        self.assertIn("gongwen", keys)
        self.assertEqual(len(keys), len(set(keys)))

    def test_manage_legacy_redirects_are_derived_from_registry(self):
        redirects = iter_manage_legacy_redirects()
        by_legacy = {item["legacy_href"]: item["canonical_href"] for item in redirects}
        self.assertEqual("/manage/teaching/offerings", by_legacy["/manage/offerings"])
        self.assertEqual("/manage/academic/classrooms", by_legacy["/manage/classrooms"])
        self.assertEqual("/manage/me/password-resets", by_legacy["/manage/system/password-resets"])
        self.assertEqual("/manage/library/exams", by_legacy["/manage/teaching/exams"])
        self.assertEqual("/manage/archive/ordinary-grade-records", by_legacy["/manage/teaching/ordinary-grade-records"])
        self.assertEqual("/manage/academic/smart-classroom", by_legacy["/manage/teaching/smart-classroom-integrations"])
        self.assertEqual("/manage/system/life-tips", by_legacy["/manage/teaching/life-tips"])

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
        # 教学域入口直接落在课堂管理，不经过 301。
        self.assertIn("/manage/teaching", paths)
        self.assertIn("/manage", paths)

    def test_platform_knowledge_uses_manage_nav_registry(self):
        manage_routes = [route for route in iter_platform_manage_routes()]
        platform_paths = {route["path"] for route in PLATFORM_ROUTES}
        self.assertTrue({route["path"] for route in manage_routes}.issubset(platform_paths))
        self.assertNotIn("/dashboard", {route["path"] for route in manage_routes})

        route_text = "\n".join(route["path"] for route in PLATFORM_ROUTES)
        self.assertIn("/manage/academic/gongwen", route_text)
        self.assertIn("/manage/archive/teacher-evaluations", route_text)
        self.assertNotIn("/manage/gongwen", route_text)
        self.assertNotIn("/manage/system/password-resets", route_text)
        self.assertNotIn("/manage/teaching/teacher-evaluations", route_text)

    def test_teacher_domain_dependency_marks_domain_without_changing_identity(self):
        dependency = require_teacher_domain("academic")
        user = dependency({"id": 7, "role": "teacher", "name": "Teacher"})
        self.assertEqual("academic", user["manage_domain"])
        self.assertEqual("teacher", user["role"])
        for domain in ("home", "teaching", "library", "archive", "academic", "me", "admin"):
            with self.subTest(domain=domain):
                require_teacher_domain(domain)

        with self.assertRaises(ValueError):
            require_teacher_domain("unknown")


if __name__ == "__main__":
    unittest.main()
