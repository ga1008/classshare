"""Native tests are opt-in and restricted to the explicitly isolated cluster."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.assessment_postgres_rehearsal import (
    apply_assessment_migrations, connect_offline, differences, rehearse, snapshot, validate_target,
)


class TargetGuardTests(unittest.TestCase):
    def test_explicit_cluster_nondefault_port_and_database_prefix_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                validate_target(cluster_dir=root, port=55437, database="lanshare_assessment_rehearsal")
            (root / "PG_VERSION").write_text("16", encoding="ascii")
            for port, database in ((5432, "lanshare_assessment_rehearsal"), (55437, "lanshare"), (55437, "postgres")):
                with self.assertRaises(ValueError):
                    validate_target(cluster_dir=root, port=port, database=database)
            self.assertEqual(root.resolve(), validate_target(cluster_dir=root, port=55437,
                                                            database="lanshare_assessment_rehearsal_tests"))


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires an explicitly created offline PostgreSQL cluster")
class NativePostgresRehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = f"lanshare_assessment_rehearsal_tests_{os.getpid()}"
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        # Exclusive creation: never replace an existing database. Only this
        # process-created synthetic database is dropped in tearDownClass.
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def setUp(self):
        self.conn = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        with self.conn.transaction():
            self.conn.execute("DROP SCHEMA public CASCADE")
            self.conn.execute("CREATE SCHEMA public")
            self.conn.execute("""CREATE TABLE assignments(id TEXT PRIMARY KEY, title TEXT, ordinary_grade_kind_override TEXT);
                CREATE TABLE submissions(id BIGINT PRIMARY KEY, assignment_id TEXT REFERENCES assignments(id),
                    score DOUBLE PRECISION, feedback_md TEXT, answers_json TEXT);
                CREATE TABLE submission_files(id BIGINT PRIMARY KEY, submission_id BIGINT REFERENCES submissions(id), relative_path TEXT);
                CREATE TABLE original_types(id SERIAL PRIMARY KEY, raw_json JSON, normalized_json JSONB, exact_decimal NUMERIC,
                    happened_at TIMESTAMPTZ, content BYTEA, value DOUBLE PRECISION, nullable_value TEXT);
                INSERT INTO assignments VALUES ('legacy','历史期末测验','assignment'), ('plain','普通作业',NULL);
                INSERT INTO submissions VALUES (1,'legacy',82.5,'DO_NOT_REPORT_FEEDBACK','{"q":"DO_NOT_REPORT_ANSWER"}'),
                    (2,'legacy',0,'缺交0',''), (3,'plain',NULL,'未评分',NULL);
                INSERT INTO submission_files VALUES(1,1,'DO_NOT_REPORT_FILENAME.png');
                INSERT INTO original_types(raw_json,normalized_json,exact_decimal,happened_at,content,value,nullable_value)
                VALUES ('{ "b":2, "a":1 }','{"b":2,"a":1}',123456789.123456789,'2026-01-01 00:00:00+08',decode('00ff0a','hex'),'-0',NULL);""")
        self.temp = tempfile.TemporaryDirectory()
        self.backup = Path(self.temp.name) / "synthetic-input.dump"
        self.backup.write_bytes(b"Synthetic read-only source sentinel; not a production restore claim")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_actual_release_ddl_twice_preserves_all_old_fields_and_sequence_state(self):
        result = rehearse(self.conn, backup=self.backup, progress=lambda _: None)
        self.assertEqual("ok", result["status"])
        self.assertEqual([], result["idempotency_differences"])
        self.assertEqual([], result["stages"][0]["old_field_differences"])
        self.assertFalse(result["deployment_gate_complete"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("DO_NOT_REPORT_", rendered)
        self.assertEqual([(None, "assignment"), (None, None)], self.conn.execute(
            "SELECT assessment_kind,ordinary_grade_kind_override FROM assignments ORDER BY id").fetchall())
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])

    def test_grade_changes_and_non_idempotent_new_rows_are_failed_gates(self):
        def destructive(conn):
            apply_assessment_migrations(conn)
            conn.execute("UPDATE submissions SET score=99 WHERE id=1")
            conn.execute("CREATE TABLE IF NOT EXISTS accidental_extra_rows(value INTEGER)")
            conn.execute("INSERT INTO accidental_extra_rows VALUES (1)")
        result = rehearse(self.conn, backup=self.backup, migrate=destructive, progress=lambda _: None)
        self.assertEqual("failed", result["status"])
        self.assertIn("table:submissions", result["stages"][0]["old_field_differences"])
        self.assertIn("table:accidental_extra_rows", result["idempotency_differences"])

    def test_raw_json_schema_sequence_and_relationship_changes_are_detected(self):
        with self.conn.transaction():
            before = snapshot(self.conn)
        with self.conn.transaction():
            self.conn.execute("UPDATE original_types SET raw_json = '{\"b\":2,\"a\":1}'")
            self.conn.execute("UPDATE submission_files SET submission_id=2")
            self.conn.execute("ALTER TABLE submissions ALTER COLUMN feedback_md SET DEFAULT 'unexpected'")
            self.conn.execute("SELECT nextval('original_types_id_seq')")
        with self.conn.transaction():
            changes = differences(before, snapshot(self.conn, baseline=before))
        self.assertIn("table:original_types", changes)
        self.assertIn("table:submission_files", changes)
        self.assertIn("schema:column:submissions.feedback_md", changes)
        self.assertIn("sequence:original_types_id_seq", changes)

    def test_signature_scope_repair_allows_only_exact_scope_values_and_new_marker(self):
        with self.conn.transaction():
            self.conn.execute("""CREATE TABLE electronic_signatures(
                id SERIAL PRIMARY KEY, scope_level TEXT, school_code TEXT, college TEXT, department TEXT,
                name TEXT, updated_at TIMESTAMPTZ DEFAULT '2001-01-01', metadata_json TEXT);
                INSERT INTO electronic_signatures(scope_level,school_code,college,department,name,metadata_json)
                VALUES ('college','school','college','department','DO_NOT_REPORT_SIGNATURE','{ "a": 1 }'),
                       ('department','school','college','','DO_NOT_REPORT_SIGNATURE','{}'),
                       ('department','school','','','DO_NOT_REPORT_SIGNATURE','{}'),
                       ('department','','','','DO_NOT_REPORT_SIGNATURE','{}');""")
        result = rehearse(self.conn, backup=self.backup, progress=lambda _: None)
        self.assertEqual("ok", result["status"])
        self.assertEqual([], result["idempotency_differences"])
        proof = result["stages"][0]["signature_visibility_migration"]
        self.assertEqual([1, 2, 3], [row["id"] for row in proof["actual_changes"]])
        self.assertEqual(["department", "college", "school"], [row["after"] for row in proof["actual_changes"]])
        self.assertEqual(["table:electronic_signatures"], result["stages"][0]["old_field_differences"])
        self.assertNotIn("DO_NOT_REPORT_SIGNATURE", json.dumps(result))
        def corrupt(conn):
            apply_assessment_migrations(conn)
            conn.execute("UPDATE electronic_signatures SET name='wrong' WHERE id=1")
        failed = rehearse(self.conn, backup=self.backup, migrate=corrupt, progress=lambda _: None)
        self.assertEqual("failed", failed["status"])
        self.assertIn("signature_other_columns_changed:1", failed["stages"][0]["signature_visibility_migration"]["blockers"])

    def test_signature_seed_preserves_pg_serial_and_updates_only_changed_metadata(self):
        from classroom_app.db.postgres import LanSharePostgresConnection
        from classroom_app.db.schema_signature_workflow import SIGNATURE_FUNCTION_POINTS, _seed_function_points
        adapter = LanSharePostgresConnection(self.conn)
        with self.conn.transaction():
            self.conn.execute("""CREATE TABLE signature_function_points (
                id SERIAL PRIMARY KEY, point_key TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                module_key TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                required_identities TEXT NOT NULL DEFAULT '', is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            _seed_function_points(adapter, engine="postgres")
            self.conn.execute("UPDATE signature_function_points SET updated_at='2001-01-01',created_at='2000-01-01',is_enabled=0")
        with self.conn.transaction():
            before = snapshot(self.conn)
        for _ in range(2):
            with self.conn.transaction():
                _seed_function_points(adapter, engine="postgres")
            with self.conn.transaction():
                self.assertEqual([], differences(before, snapshot(self.conn)))
        with self.conn.transaction():
            self.conn.execute("UPDATE signature_function_points SET label='old' WHERE point_key=%s", (SIGNATURE_FUNCTION_POINTS[0][0],))
            _seed_function_points(adapter, engine="postgres")
            row = self.conn.execute("SELECT label,is_enabled,created_at::text,updated_at::text FROM signature_function_points WHERE point_key=%s", (SIGNATURE_FUNCTION_POINTS[0][0],)).fetchone()
            self.assertEqual(SIGNATURE_FUNCTION_POINTS[0][1], row[0])
            self.assertEqual(0, row[1])
            self.assertTrue(row[2].startswith("2000-01-01"))
            self.assertFalse(row[3].startswith("2001-01-01"))
            self.assertEqual(len(SIGNATURE_FUNCTION_POINTS), self.conn.execute("SELECT last_value FROM signature_function_points_id_seq").fetchone()[0])
            self.conn.execute("DELETE FROM signature_function_points WHERE point_key=%s", (SIGNATURE_FUNCTION_POINTS[0][0],))
            _seed_function_points(adapter, engine="postgres")
            _seed_function_points(adapter, engine="postgres")
            self.assertEqual(len(SIGNATURE_FUNCTION_POINTS)+1, self.conn.execute("SELECT last_value FROM signature_function_points_id_seq").fetchone()[0])
            self.assertEqual(len(SIGNATURE_FUNCTION_POINTS), self.conn.execute("SELECT count(*) FROM signature_function_points").fetchone()[0])

    def test_agent_migration_preserves_every_old_row_and_proves_actor_pk_and_key_repair(self):
        from tools.agent_authority_migration_rehearsal import valid_report_proof
        with self.conn.transaction():
            self.conn.execute("""
                CREATE TABLE teachers(id BIGINT PRIMARY KEY);
                INSERT INTO teachers VALUES(7),(8);
                CREATE TABLE agent_tasks (
                    id BIGINT PRIMARY KEY, teacher_id BIGINT NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                    created_at TEXT, status TEXT, private_instruction TEXT
                );
                INSERT INTO agent_tasks VALUES(10,7,'2026-09-01','completed','DO_NOT_REPORT_AGENT_TASK'),
                    (11,8,'2026-09-02','failed','DO_NOT_REPORT_AGENT_OTHER');
                CREATE TABLE agent_task_composers (
                    teacher_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY REFERENCES teachers(id) ON DELETE CASCADE,
                    page_label TEXT, updated_at TEXT
                );
                INSERT INTO agent_task_composers VALUES(7,'DO_NOT_REPORT_AGENT_PAGE','2026-09-03');
                CREATE TABLE agent_runtime_api_keys (
                    id BIGINT PRIMARY KEY, provider TEXT NOT NULL, enabled INTEGER NOT NULL,
                    is_active INTEGER NOT NULL, key_encrypted TEXT, updated_at TEXT
                );
                INSERT INTO agent_runtime_api_keys VALUES(1,'deepseek',1,1,'DO_NOT_REPORT_AGENT_CIPHER','2026-09-01'),
                    (2,'deepseek',1,1,'DO_NOT_REPORT_AGENT_CIPHER_2','2026-09-02');
            """)
        result = rehearse(self.conn, backup=self.backup, progress=lambda _: None)
        self.assertEqual("ok", result["status"], {"error_type": result.get("error_type"), "stages": result.get("stages")})
        self.assertEqual([], result["idempotency_differences"])
        first, second = result["stages"]
        self.assertEqual(first["agent_authority_migration"], second["agent_authority_migration"])
        self.assertTrue(valid_report_proof(first["agent_authority_migration"]))
        self.assertEqual(first["agent_authority_migration"]["allowed_differences"], first["old_field_differences"])
        self.assertNotIn("DO_NOT_REPORT", json.dumps(result))
        self.assertEqual([(10, 7, "teacher", 7), (11, 8, "teacher", 8)], self.conn.execute(
            "SELECT id,teacher_id,actor_role,actor_id FROM agent_tasks ORDER BY id").fetchall())
        self.assertEqual([(1, 0), (2, 1)], self.conn.execute("SELECT id,is_active FROM agent_runtime_api_keys ORDER BY id").fetchall())
        self.assertEqual([(7, 'teacher', 7)], self.conn.execute('SELECT teacher_id,actor_role,actor_id FROM agent_task_composers').fetchall())
        self.assertIsNone(self.conn.execute("SELECT to_regclass('public.agent_task_composers_teacher_id_seq')").fetchone()[0])
        self.assertIn('sequence:agent_task_composers_teacher_id_seq', first['old_field_differences'])
        self.conn.execute("INSERT INTO agent_task_composers(teacher_id,actor_role,actor_id) VALUES(NULL,'student',7)")
        self.assertEqual(2, self.conn.execute('SELECT count(*) FROM agent_task_composers').fetchone()[0])
        import psycopg
        with self.assertRaises(psycopg.errors.UniqueViolation), self.conn.transaction():
            self.conn.execute("INSERT INTO agent_task_composers(teacher_id,actor_role,actor_id) VALUES(NULL,'student',7)")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), self.conn.transaction():
            self.conn.execute("INSERT INTO agent_task_composers(teacher_id,actor_role,actor_id) VALUES(999,'teacher',999)")
        self.conn.rollback()  # Close the read transaction before the next rehearsal.

        def corrupt(conn):
            apply_assessment_migrations(conn)
            conn.execute("UPDATE agent_tasks SET source_session_hash='invented' WHERE id=10")

        failed = rehearse(self.conn, backup=self.backup, migrate=corrupt, progress=lambda _: None)
        self.assertEqual("failed", failed["status"])
        self.assertIn("table:agent_tasks", failed["stages"][0]["unexpected_old_field_differences"])


if __name__ == "__main__":
    unittest.main()
