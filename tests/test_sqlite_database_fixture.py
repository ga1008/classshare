import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app import config, database
from classroom_app.db import schema_ai_jobs, schema_offering_class_links, schema_scheduler
from classroom_app.services import student_points_service
from tests.sqlite_database_fixture import isolated_sqlite_database


class IsolatedSQLiteDatabaseTests(unittest.TestCase):
    def test_nested_databases_do_not_reuse_ready_flags_or_rows(self):
        original = (config.DB_ENGINE, config.DB_PATH, database.DB_PATH)
        with isolated_sqlite_database() as outer:
            with database.get_db_connection() as conn:
                conn.execute("INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                             ("Fixture owner", "owned-fixture@example.test", "unused"))
                student_points_service.ensure_points_schema(conn)
            outer_ai_cache = set(schema_ai_jobs._SCHEMA_READY_ENGINES)
            outer_link_cache = set(schema_offering_class_links._READY_KEYS)
            self.assertTrue(schema_scheduler._SCHEMA_READY)
            self.assertTrue(student_points_service._SCHEMA_READY)

            with isolated_sqlite_database() as inner:
                self.assertNotEqual(inner, outer)
                self.assertFalse(student_points_service._SCHEMA_READY)
                with database.get_db_connection() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM assignment_group_bindings").fetchone()[0], 0)
                    student_points_service.ensure_points_schema(conn)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_point_ledger").fetchone()[0], 0)
                schema_ai_jobs._SCHEMA_READY_ENGINES.add("fixture-only")
                schema_offering_class_links._READY_KEYS.add("fixture-only")

            self.assertFalse(inner.parent.exists())
            self.assertEqual(config.DB_PATH, outer)
            self.assertEqual(schema_ai_jobs._SCHEMA_READY_ENGINES, outer_ai_cache)
            self.assertEqual(schema_offering_class_links._READY_KEYS, outer_link_cache)
            self.assertTrue(student_points_service._SCHEMA_READY)
            with database.get_db_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0], 1)
        self.assertFalse(outer.parent.exists())
        self.assertEqual((config.DB_ENGINE, config.DB_PATH, database.DB_PATH), original)

    def test_failed_startup_restores_configuration_caches_and_removes_database(self):
        original = (config.DB_ENGINE, config.DB_PATH, database.DB_PATH, config.DATABASE_URL)
        paths = []

        def fail_startup():
            paths.append(Path(config.DB_PATH))
            self.assertEqual(config.DB_ENGINE, "sqlite")
            self.assertEqual(config.DATABASE_URL, "")
            self.assertFalse(schema_scheduler._SCHEMA_READY)
            self.assertEqual(schema_ai_jobs._SCHEMA_READY_ENGINES, set())
            raise RuntimeError("synthetic initialization failure")

        prior_cache = {"prior-database"}
        with patch.object(schema_scheduler, "_SCHEMA_READY", True), \
                patch.object(schema_ai_jobs, "_SCHEMA_READY_ENGINES", prior_cache), \
                patch.object(database, "init_database", side_effect=fail_startup):
            with self.assertRaisesRegex(RuntimeError, "synthetic initialization failure"):
                with isolated_sqlite_database():
                    self.fail("A failed initializer must not yield the fixture")
            self.assertTrue(schema_scheduler._SCHEMA_READY)
            self.assertEqual(schema_ai_jobs._SCHEMA_READY_ENGINES, {"prior-database"})
            self.assertEqual(prior_cache, {"prior-database"})
        self.assertEqual((config.DB_ENGINE, config.DB_PATH, database.DB_PATH, config.DATABASE_URL), original)
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].parent.exists())

    def test_incomplete_initialization_fails_before_any_test_can_seed_data(self):
        original_path = config.DB_PATH
        with patch.object(database, "init_database"):
            with self.assertRaisesRegex(RuntimeError, "Incomplete test fixture schema"):
                with isolated_sqlite_database():
                    self.fail("Missing core tables must not be accepted")
        self.assertEqual(config.DB_PATH, original_path)
