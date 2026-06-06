import unittest

from classroom_app import config
from classroom_app.db.connection import begin_immediate_transaction, execute_insert_returning_id
from classroom_app.db.errors import DatabaseConfigurationError, DatabaseConnectionError
from classroom_app.db.postgres import (
    LanSharePostgresConnection,
    connect_postgres,
    qmark_to_psycopg,
    sqlite_compatible_dict_row,
    validate_database_url,
)


class FakeCursor:
    def __init__(self, row=None):
        self.executemany_calls = []
        self.lastrowid = 0
        self._row = row or {"id": 12}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def executemany(self, sql, params):
        self.executemany_calls.append((sql, params))

    def fetchone(self):
        return self._row


class FakeRawConnection:
    def __init__(self):
        self.execute_calls = []
        self.cursor_obj = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))
        if str(sql).strip().upper().startswith("INSERT"):
            cursor = FakeCursor({"id": 12})
            cursor.lastrowid = 12
            return cursor
        return {"sql": sql, "params": params}

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeRows:
    dict_row = object()


class FakeDriver:
    rows = FakeRows()

    def __init__(self, raw_connection=None, fail=False):
        self.raw_connection = raw_connection or FakeRawConnection()
        self.fail = fail
        self.connect_calls = []

    def connect(self, *args, **kwargs):
        self.connect_calls.append((args, kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return self.raw_connection


class FakeColumn:
    def __init__(self, name):
        self.name = name


class FakeDescriptionCursor:
    description = (FakeColumn("id"), FakeColumn("name"))


class PostgresAdapterTests(unittest.TestCase):
    def test_qmark_to_psycopg_preserves_literals_and_comments(self):
        sql = "SELECT '?' AS literal, col FROM t WHERE id = ? AND note = '-- ?' -- ?\nAND x = ?"

        converted = qmark_to_psycopg(sql)

        self.assertIn("SELECT '?' AS literal", converted)
        self.assertIn("id = %s", converted)
        self.assertIn("note = '-- ?'", converted)
        self.assertIn("-- ?", converted)
        self.assertTrue(converted.endswith("AND x = %s"))

    def test_validate_database_url_requires_postgres_scheme(self):
        with self.assertRaises(DatabaseConfigurationError):
            validate_database_url("")
        with self.assertRaises(DatabaseConfigurationError):
            validate_database_url("sqlite:///classroom.db")
        self.assertEqual("postgresql://host/db", validate_database_url("postgresql://host/db"))

    def test_connection_facade_translates_execute_and_closes_context(self):
        raw = FakeRawConnection()
        conn = LanSharePostgresConnection(raw)

        with conn as active:
            result = active.execute("SELECT * FROM teachers WHERE id = ?", (3,))

        self.assertEqual("SELECT * FROM teachers WHERE id = %s", result["sql"])
        self.assertEqual((3,), result["params"])
        self.assertTrue(raw.committed)
        self.assertTrue(raw.closed)

    def test_connection_facade_rolls_back_on_context_error(self):
        raw = FakeRawConnection()
        conn = LanSharePostgresConnection(raw)

        with self.assertRaises(RuntimeError):
            with conn:
                raise RuntimeError("fail")

        self.assertTrue(raw.rolled_back)
        self.assertTrue(raw.closed)

    def test_begin_immediate_transaction_only_executes_for_sqlite(self):
        raw = FakeRawConnection()

        begin_immediate_transaction(raw, engine="sqlite")
        begin_immediate_transaction(raw, engine="postgres")

        self.assertEqual([("BEGIN IMMEDIATE", None)], raw.execute_calls)

    def test_begin_immediate_transaction_rejects_unknown_engine(self):
        with self.assertRaises(DatabaseConfigurationError):
            begin_immediate_transaction(FakeRawConnection(), engine="mysql")

    def test_execute_insert_returning_id_uses_lastrowid_for_sqlite(self):
        raw = FakeRawConnection()

        inserted_id = execute_insert_returning_id(raw, "INSERT INTO t (name) VALUES (?)", ("A",), engine="sqlite")

        self.assertEqual(12, inserted_id)
        self.assertEqual(("INSERT INTO t (name) VALUES (?)", ("A",)), raw.execute_calls[0])

    def test_execute_insert_returning_id_appends_returning_for_postgres(self):
        raw = FakeRawConnection()

        inserted_id = execute_insert_returning_id(raw, "INSERT INTO t (name) VALUES (?)", ("A",), engine="postgres")

        self.assertEqual(12, inserted_id)
        self.assertIn("RETURNING id", raw.execute_calls[0][0])

    def test_sqlite_compatible_postgres_row_supports_key_and_index_access(self):
        row_maker = sqlite_compatible_dict_row(FakeDescriptionCursor())

        row = row_maker((12, "Teacher"))

        self.assertEqual(12, row["id"])
        self.assertEqual(12, row[0])
        self.assertEqual("Teacher", row["name"])
        self.assertEqual("Teacher", row[1])
        self.assertEqual({"id": 12, "name": "Teacher"}, dict(row))

    def test_connect_postgres_applies_session_settings(self):
        original_url = config.DATABASE_URL
        raw = FakeRawConnection()
        driver = FakeDriver(raw_connection=raw)
        config.DATABASE_URL = "postgresql://user@db.example/lanshare"
        try:
            conn = connect_postgres(driver=driver)
        finally:
            config.DATABASE_URL = original_url

        self.assertIs(conn.raw_connection, raw)
        connect_args, connect_kwargs = driver.connect_calls[0]
        self.assertEqual(("postgresql://user@db.example/lanshare",), connect_args)
        self.assertFalse(connect_kwargs["autocommit"])
        self.assertEqual(10, connect_kwargs["connect_timeout"])
        self.assertIs(sqlite_compatible_dict_row, connect_kwargs["row_factory"])
        setting_names = [params[0] for sql, params in raw.execute_calls if sql.startswith("SELECT set_config")]
        self.assertIn("statement_timeout", setting_names)
        self.assertIn("lock_timeout", setting_names)
        self.assertIn("idle_in_transaction_session_timeout", setting_names)
        self.assertIn("application_name", setting_names)

    def test_connect_postgres_redacts_url_on_connection_failure(self):
        original_url = config.DATABASE_URL
        config.DATABASE_URL = "postgresql://user@db.example/lanshare"
        try:
            with self.assertRaises(DatabaseConnectionError) as ctx:
                connect_postgres(driver=FakeDriver(fail=True))
        finally:
            config.DATABASE_URL = original_url

        self.assertIn("postgresql://***:***@db.example/lanshare", str(ctx.exception))
        self.assertNotIn("user@", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
