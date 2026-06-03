import unittest

from fastapi import APIRouter


class ArchitectureImportCompatibilityTests(unittest.TestCase):
    def test_database_legacy_public_imports_remain_available(self):
        from classroom_app import database

        legacy_names = (
            "get_db_connection",
            "init_database",
            "repair_user_sessions_storage",
            "save_user_session",
            "get_user_session",
            "list_user_sessions",
            "list_user_session_roles",
            "delete_user_sessions",
        )

        for name in legacy_names:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(database, name, None)))

    def test_router_legacy_imports_still_expose_router_objects(self):
        from classroom_app.routers import homework, manage, materials, ui

        expected_prefixes = {
            homework: "/api",
            manage: "/api/manage",
            materials: "",
            ui: "",
        }

        for module, prefix in expected_prefixes.items():
            with self.subTest(module=module.__name__):
                self.assertIsInstance(module.router, APIRouter)
                self.assertEqual(prefix, module.router.prefix)


if __name__ == "__main__":
    unittest.main()
