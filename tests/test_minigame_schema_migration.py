import asyncio
import io
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from classroom_app import config
from classroom_app.db.connection import get_db_connection
from classroom_app.services import minigame_platform as platform
from tools.db import migrate_minigames as cli


class MinigameSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.path = root / "隔离 # 路径" / "游戏.db"
        self.enterContext(patch.object(config, "DB_ENGINE", "sqlite"))
        self.enterContext(patch.object(config, "DB_PATH", self.path))

    def migrate(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(args)
        return code, output.getvalue()

    def test_game_schema_failure_leaves_classroom_startup_available(self):
        application = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.object(
                platform, "schema_installed", side_effect=OSError("fixture unavailable")
            ),
            self.assertLogs(platform.__name__, level="ERROR"),
        ):
            asyncio.run(platform.start_minigames(application))
        self.assertFalse(application.state.minigames_ready)

    def test_status_does_not_create_directories_or_database(self):
        self.assertEqual(self.migrate()[0], 1)
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE unrelated(id INTEGER)")
        previous = self.path.read_bytes()
        self.assertIn("mg_games", self.migrate()[1])
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_repeated_apply_preserves_data_and_reads_current_configuration(self):
        self.assertEqual(self.migrate("--apply"), (0, "sqlite minigames: ready\n"))
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO mg_games(id,owner_teacher_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("retained", 88, "重复迁移前的数据", 2, 3),
            )
        self.assertEqual(self.migrate("--apply")[0], 0)
        self.assertTrue(platform.schema_installed())
        with get_db_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT title FROM mg_games").fetchone()[0],
                "重复迁移前的数据",
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM mg_admin_serial").fetchone()[0], 1
            )
        with patch.object(config, "DB_PATH", self.path.with_name("新的配置.db")):
            self.assertEqual(self.migrate()[0], 1)
            self.assertFalse(platform.schema_installed())

    def test_status_catches_partial_schema_and_apply_repairs_known_additions(self):
        self.assertEqual(self.migrate("--apply")[0], 0)
        with get_db_connection() as conn:
            conn.execute("DROP TABLE mg_admin_plans")
            conn.execute("DROP INDEX idx_mg_preview_age")
            conn.execute("ALTER TABLE mg_rt_activity DROP COLUMN chat_enabled")
            conn.execute("DELETE FROM mg_admin_serial")
        code, report = self.migrate()
        self.assertEqual(code, 1)
        for item in (
            "mg_admin_plans",
            "idx_mg_preview_age",
            "mg_rt_activity.chat_enabled",
        ):
            self.assertIn(item, report)
        self.assertEqual(self.migrate("--apply")[0], 0)
        with get_db_connection() as conn:
            conn.execute("DELETE FROM mg_admin_serial")
        self.assertIn("mg_admin_serial[1]", self.migrate()[1])
        self.assertEqual(self.migrate("--apply")[0], 0)

    def test_upgrade_keeps_existing_current_version_and_requires_explicit_source_license(self):
        self.assertEqual(self.migrate("--apply")[0], 0)
        with get_db_connection() as conn:
            conn.execute("INSERT INTO mg_games(id,owner_teacher_id,title,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         ("legacy", 5, "旧版本", "listed", "legacy-1", 100, 100))
            conn.execute("INSERT INTO mg_versions(id,game_id,release_version,status,created_at) VALUES(?,?,?,?,?)",
                         ("legacy-1", "legacy", "1.0.0", "ready", 100))
            for table in ("mg_review_notices", "mg_content_reviews", "mg_version_authorship", "mg_discovery_preferences"):
                conn.execute("DROP TABLE " + table)
        self.assertEqual(self.migrate()[0], 1)
        self.assertEqual(self.migrate("--apply")[0], 0)
        with get_db_connection() as conn:
            state = conn.execute("""SELECT g.current_version_id,g.status,v.release_version,COALESCE(a.allow_remix,0)
                FROM mg_games g JOIN mg_versions v ON v.id=g.current_version_id
                LEFT JOIN mg_version_authorship a ON a.version_id=v.id WHERE g.id=?""", ("legacy",)).fetchone()
            self.assertEqual(tuple(state), ("legacy-1", "listed", "1.0.0", 0))

    def test_early_ddl_failure_rolls_back_the_entire_first_install(self):
        with patch(
            "classroom_app.services.minigame_packages.ensure_package_schema",
            side_effect=RuntimeError("注入"),
        ):
            with self.assertRaisesRegex(RuntimeError, "注入"):
                self.migrate("--apply")
        with get_db_connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM sqlite_schema WHERE name GLOB 'mg_*'"
                ).fetchone()[0],
                0,
            )
        self.assertEqual(self.migrate("--apply")[0], 0)

    def test_unrepairable_shape_rejects_transaction_before_commit(self):
        self.assertEqual(self.migrate("--apply")[0], 0)
        with get_db_connection() as conn:
            conn.execute("ALTER TABLE mg_games DROP COLUMN summary")
            conn.execute("DROP INDEX idx_mg_preview_age")
        with self.assertRaisesRegex(RuntimeError, "mg_games.summary"):
            self.migrate("--apply")
        code, report = self.migrate()
        self.assertEqual(code, 1)
        self.assertIn("mg_games.summary", report)
        self.assertIn("idx_mg_preview_age", report)

    def test_concurrent_first_install_waits_for_same_write_transaction(self):
        barrier = Barrier(2)

        def apply():
            with get_db_connection() as conn:
                barrier.wait(timeout=5)
                platform.ensure_all_schema(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(apply) for _ in range(2)]
            for future in futures:
                future.result(timeout=15)
        self.assertEqual(self.migrate()[0], 0)
