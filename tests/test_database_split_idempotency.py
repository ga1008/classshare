import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app import config, database
from classroom_app.db.errors import DatabaseConfigurationError


KEY_BUSINESS_TABLES = (
    "teachers",
    "students",
    "classes",
    "courses",
    "class_offerings",
    "assignments",
    "submissions",
    "course_materials",
    "course_material_assignments",
    "exam_papers",
)


class DatabaseSplitIdempotencyTests(unittest.TestCase):
    def _snapshot_counts(self, db_path: Path) -> dict[str, int | None]:
        conn = sqlite3.connect(db_path)
        try:
            counts: dict[str, int | None] = {}
            for table in KEY_BUSINESS_TABLES:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    counts[table] = None
                    continue
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            return counts
        finally:
            conn.close()

    def test_split_database_init_is_second_run_idempotent_on_temp_db(self):
        original_config_db_path = config.DB_PATH
        original_database_db_path = database.DB_PATH

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db" / "classroom.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            config.DB_PATH = db_path
            database.DB_PATH = db_path
            try:
                database.init_database()
                first_counts = self._snapshot_counts(db_path)

                database.init_database()
                second_counts = self._snapshot_counts(db_path)
                gc.collect()

                conn = sqlite3.connect(db_path)
                try:
                    quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
                finally:
                    conn.close()

                self.assertEqual("ok", quick_check)
                self.assertEqual(first_counts, second_counts)
            finally:
                config.DB_PATH = original_config_db_path
                database.DB_PATH = original_database_db_path

    def test_split_connection_preserves_sqlite_pragmas(self):
        original_config_db_path = config.DB_PATH
        original_database_db_path = database.DB_PATH

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db" / "classroom.db"
            config.DB_PATH = db_path
            database.DB_PATH = db_path
            try:
                conn = database.get_db_connection()
                try:
                    self.assertIs(conn.row_factory, sqlite3.Row)
                    self.assertEqual(1, int(conn.execute("PRAGMA foreign_keys").fetchone()[0]))
                    self.assertGreaterEqual(int(conn.execute("PRAGMA busy_timeout").fetchone()[0]), 1000)
                    self.assertEqual("wal", str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower())
                finally:
                    conn.close()
                    gc.collect()
            finally:
                config.DB_PATH = original_config_db_path
                database.DB_PATH = original_database_db_path

    def test_split_connection_context_manager_closes_handle_on_exit(self):
        original_config_db_path = config.DB_PATH
        original_database_db_path = database.DB_PATH

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db" / "classroom.db"
            config.DB_PATH = db_path
            database.DB_PATH = db_path
            try:
                with database.get_db_connection() as conn:
                    self.assertEqual(1, int(conn.execute("SELECT 1").fetchone()[0]))

                with self.assertRaises(sqlite3.ProgrammingError):
                    conn.execute("SELECT 1")
            finally:
                config.DB_PATH = original_config_db_path
                database.DB_PATH = original_database_db_path

    def test_postgres_engine_requires_explicit_database_url_without_sqlite_fallback(self):
        original_engine = config.DB_ENGINE
        original_database_url = config.DATABASE_URL
        original_postgres_ready = config.POSTGRES_BACKEND_READY
        config.DB_ENGINE = "postgres"
        config.DATABASE_URL = ""
        config.POSTGRES_BACKEND_READY = False
        try:
            with self.assertRaises(DatabaseConfigurationError):
                database.get_db_connection()
        finally:
            config.DB_ENGINE = original_engine
            config.DATABASE_URL = original_database_url
            config.POSTGRES_BACKEND_READY = original_postgres_ready

    def test_postgres_engine_uses_adapter_only_when_explicitly_ready(self):
        original_engine = config.DB_ENGINE
        original_database_url = config.DATABASE_URL
        original_postgres_ready = config.POSTGRES_BACKEND_READY
        sentinel_connection = object()
        config.DB_ENGINE = "postgres"
        config.DATABASE_URL = "postgresql://user@db.example/lanshare"
        config.POSTGRES_BACKEND_READY = True
        try:
            with patch("classroom_app.db.connection.connect_postgres", return_value=sentinel_connection) as mocked:
                self.assertIs(database.get_db_connection(), sentinel_connection)
                mocked.assert_called_once_with()
        finally:
            config.DB_ENGINE = original_engine
            config.DATABASE_URL = original_database_url
            config.POSTGRES_BACKEND_READY = original_postgres_ready

    def test_unknown_database_engine_fails_closed(self):
        original_engine = config.DB_ENGINE
        config.DB_ENGINE = "mysql"
        try:
            with self.assertRaises(DatabaseConfigurationError):
                database.get_db_connection()
        finally:
            config.DB_ENGINE = original_engine


if __name__ == "__main__":
    unittest.main()
