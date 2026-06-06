import sqlite3
import unittest

from classroom_app.db import migration_registry
from classroom_app.db.row import row_to_mapping, rows_to_mappings
from classroom_app.db.sql import (
    current_timestamp_sql,
    for_update_skip_locked_clause,
    insert_ignore_sql,
    insert_returning_id_sql,
    insert_update_on_conflict_sql,
    limit_offset_clause,
    placeholder,
    postgres_claim_jobs_sql,
    postgres_singleton_status_index_sql,
    quote_identifier,
)


class DatabaseSqlHelperTests(unittest.TestCase):
    def test_placeholders_are_engine_specific(self):
        self.assertEqual("?", placeholder("sqlite", 1))
        self.assertEqual("$2", placeholder("postgres", 2))
        self.assertEqual("LIMIT $1 OFFSET $2", limit_offset_clause("postgres", limit_index=1, offset_index=2))

    def test_identifier_quoting_rejects_unsafe_names(self):
        self.assertEqual('"assignments"', quote_identifier("assignments"))
        self.assertEqual('"public"."assignments"', quote_identifier("public.assignments"))
        with self.assertRaises(ValueError):
            quote_identifier("assignments; drop table teachers")

    def test_insert_returning_id_sql_documents_id_strategy(self):
        sqlite_stmt = insert_returning_id_sql("sqlite", "teachers", ("email", "name"))
        postgres_stmt = insert_returning_id_sql("postgres", "teachers", ("email", "name"))

        self.assertEqual(
            'INSERT INTO "teachers" ("email", "name") VALUES (?, ?)',
            sqlite_stmt.sql,
        )
        self.assertEqual("cursor.lastrowid", sqlite_stmt.id_strategy)
        self.assertEqual(
            'INSERT INTO "teachers" ("email", "name") VALUES ($1, $2) RETURNING "id"',
            postgres_stmt.sql,
        )
        self.assertEqual("returning", postgres_stmt.id_strategy)

    def test_insert_ignore_requires_postgres_conflict_columns(self):
        self.assertEqual(
            'INSERT OR IGNORE INTO "teachers" ("email") VALUES (?)',
            insert_ignore_sql("sqlite", "teachers", ("email",)).sql,
        )
        with self.assertRaises(ValueError):
            insert_ignore_sql("postgres", "teachers", ("email",))
        self.assertEqual(
            'INSERT INTO "teachers" ("email") VALUES ($1) ON CONFLICT ("email") DO NOTHING',
            insert_ignore_sql("postgres", "teachers", ("email",), conflict_columns=("email",)).sql,
        )

    def test_upsert_and_queue_lock_helpers(self):
        stmt = insert_update_on_conflict_sql(
            "postgres",
            "email_outbox",
            ("dedupe_key", "status"),
            conflict_columns=("dedupe_key",),
            update_columns=("status",),
        )
        self.assertIn('ON CONFLICT ("dedupe_key") DO UPDATE SET "status" = excluded."status"', stmt.sql)
        self.assertEqual("", for_update_skip_locked_clause("sqlite"))
        self.assertEqual("FOR UPDATE SKIP LOCKED", for_update_skip_locked_clause("postgres"))
        self.assertEqual("CURRENT_TIMESTAMP", current_timestamp_sql("sqlite"))
        self.assertEqual("now()", current_timestamp_sql("postgres"))

    def test_postgres_claim_jobs_sql_uses_skip_locked_and_returning(self):
        stmt = postgres_claim_jobs_sql(
            "email_outbox",
            claim_status="sending",
            eligible_where_sql='"status" = $1',
            locked_at_column="locked_at",
            updated_at_column="updated_at",
            order_columns=(("created_at", "ASC"), ("id", "ASC")),
            limit_placeholder_index=2,
        )

        self.assertIn("FOR UPDATE SKIP LOCKED", stmt.sql)
        self.assertIn("RETURNING *", stmt.sql)
        self.assertIn('"status" = \'sending\'', stmt.sql)

    def test_postgres_singleton_status_index_sql(self):
        stmt = postgres_singleton_status_index_sql("agent_tasks")

        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS", stmt.sql)
        self.assertIn("WHERE \"status\" = 'running'", stmt.sql)

    def test_schema_migrations_sql_is_engine_specific(self):
        sqlite_sql = migration_registry.schema_migrations_table_sql("sqlite")
        postgres_sql = migration_registry.schema_migrations_table_sql("postgres")
        self.assertIn("success INTEGER NOT NULL", sqlite_sql)
        self.assertIn("success boolean NOT NULL", postgres_sql)
        self.assertIn("PRIMARY KEY (version, db_engine)", postgres_sql)

    def test_row_to_mapping_supports_sqlite_row_dict_and_tuple(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT 1 AS id, 'teacher' AS role").fetchone()
        finally:
            conn.close()

        self.assertEqual({"id": 1, "role": "teacher"}, row_to_mapping(row))
        self.assertEqual({"id": 2}, row_to_mapping({"id": 2}))
        self.assertEqual({"id": 3, "role": "student"}, row_to_mapping((3, "student"), ("id", "role")))
        self.assertEqual([{"id": 4}], rows_to_mappings([(4,)], ("id",)))


if __name__ == "__main__":
    unittest.main()
