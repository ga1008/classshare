import sqlite3
import unittest

from classroom_app.services.agent_bridge_service import mask_sensitive_cell, run_readonly_query


class AgentBridgeServiceTests(unittest.TestCase):
    def _open_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sample_items (id INTEGER PRIMARY KEY, name TEXT, api_token TEXT)")
        conn.executemany(
            "INSERT INTO sample_items (name, api_token) VALUES (?, ?)",
            [(f"item-{idx}", f"token-{idx}") for idx in range(5)],
        )
        return conn

    def test_readonly_query_applies_outer_limit_and_truncation(self):
        conn = self._open_conn()
        try:
            result = run_readonly_query(conn, "SELECT id, name FROM sample_items ORDER BY id", limit=2)

            self.assertEqual(["id", "name"], result["columns"])
            self.assertEqual(2, result["row_count"])
            self.assertTrue(result["truncated"])
            self.assertEqual([1, 2], [row["id"] for row in result["rows"]])
        finally:
            conn.close()

    def test_readonly_query_keeps_named_params_and_masks_sensitive_columns(self):
        conn = self._open_conn()
        try:
            result = run_readonly_query(
                conn,
                "SELECT name, api_token FROM sample_items WHERE name LIKE :keyword ORDER BY id",
                limit=10,
                params={"keyword": "item-%"},
            )

            self.assertEqual(5, result["row_count"])
            self.assertFalse(result["truncated"])
            self.assertEqual("item-0", result["rows"][0]["name"])
            self.assertEqual(mask_sensitive_cell("api_token", "token-0"), result["rows"][0]["api_token"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
