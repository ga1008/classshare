import unittest
from unittest.mock import patch

from classroom_app.services import (
    base_resource_modes_service,
    organization_management_service,
    organization_scope_service,
)


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakePostgresConnection:
    def __init__(self, existing_tables=None):
        self.existing_tables = set(existing_tables or {"teachers"})
        self.calls = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if "information_schema.tables" in normalized:
            return FakeCursor({"exists": 1} if params[1] in self.existing_tables else None)
        raise AssertionError(f"Unexpected SQL: {normalized}")


class PostgresMetadataHelperTests(unittest.TestCase):
    def _assert_uses_information_schema(self, module, table_name="teachers"):
        conn = FakePostgresConnection(existing_tables={table_name})
        with patch.object(module, "get_configured_db_engine", return_value="postgres"):
            self.assertTrue(module._table_exists(conn, table_name))
            self.assertFalse(module._table_exists(conn, "missing_table"))

        sql_text = "\n".join(sql for sql, _ in conn.calls)
        self.assertIn("information_schema.tables", sql_text)
        self.assertNotIn("sqlite_master", sql_text)
        self.assertNotIn("PRAGMA", sql_text)

    def test_base_resource_table_exists_uses_postgres_metadata(self):
        self._assert_uses_information_schema(base_resource_modes_service)

    def test_organization_management_table_exists_uses_postgres_metadata(self):
        self._assert_uses_information_schema(organization_management_service)

    def test_organization_scope_table_exists_uses_postgres_metadata(self):
        self._assert_uses_information_schema(organization_scope_service)


if __name__ == "__main__":
    unittest.main()
