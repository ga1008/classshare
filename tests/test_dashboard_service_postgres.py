from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.services.dashboard_service import _query_scalar, _teacher_today_login_count_sql


class _FakePostgresScalarCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakePostgresScalarConnection:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return _FakePostgresScalarCursor(self.row)


class DashboardServicePostgresTests(unittest.TestCase):
    def test_query_scalar_accepts_postgres_dict_count_row(self):
        conn = _FakePostgresScalarConnection({"row_count": "12"})

        value = _query_scalar(conn, "SELECT COUNT(*) AS row_count FROM students", ())

        self.assertEqual(12, value)

    def test_query_scalar_accepts_sqlite_tuple_row(self):
        conn = _FakePostgresScalarConnection((7,))

        value = _query_scalar(conn, "SELECT COUNT(*) FROM students", ())

        self.assertEqual(7, value)

    def test_teacher_today_login_count_sql_uses_postgres_date_cast(self):
        with patch("classroom_app.services.dashboard_service.get_configured_db_engine", return_value="postgres"):
            sql = _teacher_today_login_count_sql()

        self.assertIn("logged_at::date = CURRENT_DATE", sql)
        self.assertNotIn("date('now'", sql)


if __name__ == "__main__":
    unittest.main()
