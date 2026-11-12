"""Exercise release migration wiring without opening a database connection.

The real attendance schema entrypoints emit PostgreSQL DDL into a recorder;
native PostgreSQL validity and two-pass preservation are separate release gates.
"""
import importlib
import unittest
from unittest.mock import patch

from tools.assessment_postgres_rehearsal import apply_assessment_migrations


class _Rows:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _RecordingConnection:
    def __init__(self):
        self.row_factory = object()
        self.statements = []
        self.introspection_row_factories = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if "FROM information_schema.columns" in sql:
            self.introspection_row_factories.append(self.row_factory)
            if "table_name='login_attendance_rounds'" in sql:
                # Existing rounds predate the additive settlement generation.
                return _Rows([{"column_name": "id"}, {"column_name": "revision"}])
            if "table_name='login_attendance_policies'" in sql:
                # Existing policies predate the optional network restriction.
                return _Rows([{"column_name": "class_offering_id"}, {"column_name": "networks_json"}])
            if "table_name='smart_attendance_session_links'" in sql:
                return _Rows([{"column_name": "alignment_json"}, {"column_name": "course_link_revision"}])
            raise AssertionError(f"Unexpected schema introspection: {sql}")
        if sql.startswith("SELECT to_regclass("):
            return _Rows([(None,)])
        return _Rows()


class AttendanceMigrationCoverageTests(unittest.TestCase):
    def setUp(self):
        # Keep these tests focused on the attendance release chain. All four
        # attendance functions below still run their actual PostgreSQL branch.
        for target in (
            "classroom_app.services.approval_workflow_schema.ensure_approval_workflow_schema",
            "classroom_app.db.schema_behavior_inbox.ensure_behavior_inbox_schema",
            "classroom_app.db.schema_behavior_collection_health.ensure_behavior_collection_health_schema",
            "classroom_app.db.schema_retake.ensure_retake_schema",
            "classroom_app.db.schema_ai_jobs.ensure_ai_job_schema",
            "classroom_app.db.schema_grade_publications.ensure_grade_publication_schema",
        ):
            self.enterContext(patch(target))
        self.connection = _RecordingConnection()

    def _record_entrypoints(self):
        calls = []
        for domain in ("login_attendance", "smart_classroom_write", "smart_classroom_sessions", "smart_classroom_auto_sync"):
            module = importlib.import_module(f"classroom_app.db.schema_{domain}")
            name = f"ensure_{domain}_schema"
            original = getattr(module, name)

            def invoke(conn, *, engine, _domain=domain, _original=original):
                calls.append((_domain, engine))
                return _original(conn, engine=engine)

            self.enterContext(patch.object(module, name, side_effect=invoke))
        return calls

    def test_real_entrypoints_follow_startup_order_and_include_both_new_ledgers(self):
        from classroom_app.db.postgres import sqlite_compatible_dict_row

        calls = self._record_entrypoints()
        original_factory = self.connection.row_factory
        apply_assessment_migrations(self.connection)
        self.assertEqual([
            ("login_attendance", "postgres"),
            ("smart_classroom_write", "postgres"),
            ("smart_classroom_sessions", "postgres"),
            ("smart_classroom_auto_sync", "postgres"),
        ], calls)
        statements = self.connection.statements
        created = [sql.split()[5] for sql in statements if sql.startswith("CREATE TABLE IF NOT EXISTS ")]
        self.assertIn("login_attendance_settlement_runs", created)
        self.assertIn("smart_attendance_student_corrections", created)
        self.assertLess(created.index("login_attendance_rounds"), created.index("login_attendance_settlement_runs"))
        self.assertLess(created.index("smart_attendance_sync_runs"), created.index("smart_attendance_student_corrections"))
        self.assertLess(created.index("smart_classroom_session_grants"), created.index("smart_attendance_student_corrections"))
        self.assertIn("ALTER TABLE login_attendance_rounds ADD COLUMN settlement_generation INTEGER NOT NULL DEFAULT 0", statements)
        self.assertIn("ALTER TABLE login_attendance_policies ADD COLUMN enforce_network INTEGER NOT NULL DEFAULT 0", statements)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_login_attendance_access_received ON login_attendance_access(student_id,received_at_ms)", statements)
        for index in ("idx_attendance_settlement_round", "idx_smart_student_correction_round",
                      "idx_smart_student_correction_offering", "idx_smart_student_correction_session"):
            self.assertTrue(any(sql.startswith(f"CREATE INDEX IF NOT EXISTS {index} ") for sql in statements), index)
        self.assertEqual([sqlite_compatible_dict_row] * 3, self.connection.introspection_row_factories)
        self.assertIs(original_factory, self.connection.row_factory)

    def test_schema_failure_propagates_and_restores_snapshot_row_contract(self):
        original_factory = self.connection.row_factory
        failure = RuntimeError("synthetic schema failure")
        with patch("classroom_app.db.schema_smart_classroom_sessions.ensure_smart_classroom_sessions_schema", side_effect=failure), \
                patch("classroom_app.db.schema_smart_classroom_auto_sync.ensure_smart_classroom_auto_sync_schema") as auto_sync:
            with self.assertRaisesRegex(RuntimeError, "synthetic schema failure"):
                apply_assessment_migrations(self.connection)
            auto_sync.assert_not_called()
        self.assertIs(original_factory, self.connection.row_factory)


if __name__ == "__main__":
    unittest.main()
