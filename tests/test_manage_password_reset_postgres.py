from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.routers.ui_parts import manage_pages


class ManagePasswordResetPostgresTests(unittest.TestCase):
    def test_password_reset_login_summary_uses_postgres_date_cast(self):
        with patch.object(manage_pages, "get_configured_db_engine", return_value="postgres"):
            sql = manage_pages._password_reset_login_summary_sql()

        self.assertIn("logged_at::date = CURRENT_DATE", sql)
        self.assertNotIn("date('now'", sql)


if __name__ == "__main__":
    unittest.main()
