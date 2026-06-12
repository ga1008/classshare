import unittest

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

    def test_manage_nav_filters_admin_items_and_marks_active_domain(self):
        teacher_nav = build_manage_nav({"id": 1, "role": "teacher"}, "classrooms", is_super_admin=False)
        self.assertEqual("academic", teacher_nav["active_domain"])
        self.assertEqual([], teacher_nav["admin_groups"])
        self.assertTrue(any(domain["key"] == "academic" and domain["active"] for domain in teacher_nav["domains"]))

        admin_nav = build_manage_nav({"id": 1, "role": "teacher"}, "system_users", is_super_admin=True)
        self.assertEqual("admin", admin_nav["active_domain"])
        self.assertTrue(admin_nav["admin_groups"])
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
