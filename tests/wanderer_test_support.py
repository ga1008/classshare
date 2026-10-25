"""Shared fixture for the 散修 tests: an isolated SQLite DB with one synthetic teacher.

Never connects to a real database or model.
"""
from __future__ import annotations

import unittest

from classroom_app import database
from classroom_app.services import cultivation_display_service, wanderer_identity_service, wanderer_memory_service
from tests.sqlite_database_fixture import isolated_sqlite_database


def reset_wanderer_caches() -> None:
    wanderer_identity_service.invalidate_wanderer_cache()
    wanderer_memory_service.invalidate_thread_cache()
    cultivation_display_service.invalidate_emblem_cache()


class WandererDbTestCase(unittest.TestCase):
    seed_on_setup = True

    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        reset_wanderer_caches()
        with database.get_db_connection() as conn:
            conn.execute(
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                ("合成教师", "teacher@example.test", "synthetic-hash"),
            )
            if cls.seed_on_setup:
                from classroom_app.services.wanderer_persona_seeds import seed_wanderers

                cls.seed_report = seed_wanderers(conn)
            conn.commit()

    def setUp(self):
        reset_wanderer_caches()

    @staticmethod
    def connect():
        return database.get_db_connection()

    def wanderer(self, conn, slug: str) -> dict:
        wanderer = wanderer_identity_service.load_wanderer_by_slug(conn, slug)
        self.assertIsNotNone(wanderer, slug)
        return wanderer
