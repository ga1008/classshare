"""Opt-in preferences gates in an exclusively created native PostgreSQL DB.

Uses ASSESSMENT_REHEARSAL_TEST_CLUSTER / PORT and optional ADMIN_DATABASE.
connect_offline validates the explicit loopback cluster; no application DSN,
application startup, restored business records or existing test DB is used.
"""

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.postgres_schema import REQUIRED_POSTGRES_COLUMNS
from classroom_app.db.schema_user_ui_preferences import ensure_user_ui_preferences_schema
from classroom_app.services import user_ui_preferences_service as svc
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER") and os.environ.get("ASSESSMENT_REHEARSAL_TEST_PORT"),
                     "Requires an explicit isolated loopback PostgreSQL cluster and port")
class UIPreferencesPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_uiprefs_" + uuid.uuid4().hex[:12]
        cls.created = False
        admin_database = os.environ.get("ASSESSMENT_REHEARSAL_TEST_ADMIN_DATABASE", "lanshare_assessment_rehearsal")
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database=admin_database)
        cls.admin.autocommit = True
        try:
            cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')
            cls.created = True
        except BaseException:
            cls.admin.close()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.created:
                cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def connection(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        conn = LanSharePostgresConnection(raw)
        if conn.execute("SELECT current_database()").fetchone()[0] != self.database:
            conn.close()
            raise RuntimeError("Refusing a database not exclusively created by this fixture")
        conn.commit()
        return conn

    def setUp(self):
        self.conn = self.connection()
        self.addCleanup(self.conn.close)
        self.conn.execute("DROP SCHEMA public CASCADE")
        self.conn.execute("CREATE SCHEMA public")
        ensure_user_ui_preferences_schema(self.conn, engine="postgres")
        self.conn.commit()
        self.student = {"role": "student", "id": 17}
        self.teacher = {"role": "teacher", "id": 17}

    def save(self, changes, version, user=None):
        result = svc.update_ui_preferences(self.conn, user or self.student, changes=changes, version=version)
        self.conn.commit()
        return result

    def test_new_and_legacy_schema_are_nullable_idempotent_and_preserve_old_rows(self):
        columns = {row["column_name"] for row in self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='user_ui_preferences'").fetchall()}
        self.assertEqual(columns, set(REQUIRED_POSTGRES_COLUMNS["user_ui_preferences"]))
        self.conn.execute("DROP TABLE user_ui_preferences")
        self.conn.execute("""CREATE TABLE user_ui_preferences (
            user_role TEXT NOT NULL CHECK(user_role IN ('student','teacher')),
            user_pk BIGINT NOT NULL CHECK(user_pk > 0), palette_key TEXT NOT NULL DEFAULT 'indigo',
            version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_role,user_pk))""")
        self.conn.execute("INSERT INTO user_ui_preferences VALUES('teacher',17,'sky',9,'2026-09-20 00:00:00')")
        for _ in range(2):
            ensure_user_ui_preferences_schema(self.conn, engine="postgres")
            self.conn.commit()
        row = self.conn.execute("SELECT palette_key,appearance,glass,version,updated_at FROM user_ui_preferences").fetchone()
        self.assertEqual(tuple(row.values()), ("sky", None, None, 9, "2026-09-20 00:00:00"))
        nullable = self.conn.execute("SELECT column_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='user_ui_preferences' AND column_name IN ('appearance','glass')").fetchall()
        self.assertEqual(len(nullable), 2)
        self.assertTrue(all(row["is_nullable"] == "YES" and row["column_default"] is None for row in nullable))

    def test_default_reads_work_in_read_only_transaction_without_creating_rows(self):
        self.conn.execute("SET TRANSACTION READ ONLY")
        for user, palette in ((self.student, "indigo"), (self.teacher, "teal")):
            current = svc.get_ui_preferences(self.conn, user)
            self.assertEqual((current["palette_key"], current["appearance"], current["glass"], current["version"]),
                             (palette, "auto", "tinted", 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)

    def test_teacher_first_appearance_and_old_client_sql_preserve_new_fields(self):
        first = self.save({"appearance": "dark", "glass": "off"}, 0, self.teacher)
        self.assertEqual((first["palette_key"], first["version"]), ("teal", 1))
        self.save({"palette_key": "mint"}, 0, self.student)
        self.assertNotEqual(first["context_token"], svc.preference_context_token(self.student))
        self.conn.execute("UPDATE user_ui_preferences SET palette_key=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE user_role=? AND user_pk=? AND version=? RETURNING version",
                          ("rose", "teacher", 17, 1)).fetchone()
        self.conn.commit()
        current = svc.get_ui_preferences(self.conn, self.teacher)
        self.assertEqual((current["palette_key"], current["appearance"], current["glass"], current["version"]),
                         ("rose", "dark", "off", 2))
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student)["palette_key"], "mint")

    def race(self, version, changes):
        barrier = threading.Barrier(2)

        def write(payload):
            try:
                with self.connection() as conn:
                    barrier.wait(timeout=5)
                    current = svc.update_ui_preferences(conn, self.student, changes=payload, version=version)
                return "saved", current
            except svc.PreferenceConflict as exc:
                return "conflict", exc.current

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, payload) for payload in changes]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(status for status, _ in outcomes), ["conflict", "saved"])
        current = svc.get_ui_preferences(self.conn, self.student)
        self.assertEqual(current["version"], version + 1)
        for _, observed in outcomes:
            self.assertEqual(observed, current)
        return current

    def test_concurrent_first_insert_has_one_winner_and_one_conflict(self):
        current = self.race(0, ({"appearance": "dark"}, {"glass": "off"}))
        self.assertIn((current["appearance"], current["glass"]), {("dark", "tinted"), ("auto", "off")})

    def test_concurrent_same_field_update_has_one_winner_and_one_conflict(self):
        self.save({"appearance": "auto"}, 0)
        current = self.race(1, ({"appearance": "dark"}, {"appearance": "light"}))
        self.assertIn(current["appearance"], {"dark", "light"})

    def test_concurrent_different_fields_still_use_whole_row_cas(self):
        self.save({"appearance": "auto"}, 0)
        current = self.race(1, ({"appearance": "dark"}, {"glass": "off"}))
        self.assertIn((current["appearance"], current["glass"]), {("dark", "tinted"), ("auto", "off")})

    def test_transaction_failure_rolls_back_every_changed_field_and_version(self):
        before = self.save({"palette_key": "sky", "appearance": "light", "glass": "tinted"}, 0)
        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            with self.connection() as conn:
                svc.update_ui_preferences(conn, self.student, changes={"palette_key": "rose", "appearance": "dark", "glass": "off"}, version=1)
                raise RuntimeError("synthetic rollback")
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student), before)


if __name__ == "__main__":
    unittest.main()
