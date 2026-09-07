import sqlite3
import unittest

from classroom_app.db.schema_signature_workflow import SIGNATURE_FUNCTION_POINTS, _seed_function_points


class SignatureSeedIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""CREATE TABLE signature_function_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT, point_key TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL, module_key TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            required_identities TEXT NOT NULL DEFAULT '', is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        _seed_function_points(self.conn, engine="sqlite")
        self.key = SIGNATURE_FUNCTION_POINTS[0][0]
        self.conn.execute("UPDATE signature_function_points SET updated_at='2001-01-01',created_at='2000-01-01'")
        self.conn.execute("UPDATE signature_function_points SET is_enabled=0 WHERE point_key=?", (self.key,))

    def tearDown(self):
        self.conn.close()

    def sequence(self):
        return self.conn.execute("SELECT seq FROM sqlite_sequence WHERE name='signature_function_points'").fetchone()[0]

    def test_two_unchanged_startups_preserve_every_field_and_autoincrement(self):
        before = self.conn.execute("SELECT * FROM signature_function_points ORDER BY id").fetchall()
        sequence = self.sequence()
        for _ in range(2):
            _seed_function_points(self.conn, engine="sqlite")
            self.assertEqual(before, self.conn.execute("SELECT * FROM signature_function_points ORDER BY id").fetchall())
            self.assertEqual(sequence, self.sequence())

    def test_changed_metadata_updates_only_that_row_without_new_identity_or_reenabling(self):
        sequence = self.sequence()
        self.conn.execute("UPDATE signature_function_points SET label='old',required_identities='old' WHERE point_key=?", (self.key,))
        _seed_function_points(self.conn, engine="sqlite")
        row = self.conn.execute("SELECT label,required_identities,is_enabled,created_at,updated_at FROM signature_function_points WHERE point_key=?", (self.key,)).fetchone()
        self.assertEqual(SIGNATURE_FUNCTION_POINTS[0][1], row[0])
        self.assertEqual(SIGNATURE_FUNCTION_POINTS[0][4], row[1])
        self.assertEqual((0, "2000-01-01"), row[2:4])
        self.assertNotEqual("2001-01-01", row[4])
        self.assertEqual(len(SIGNATURE_FUNCTION_POINTS)-1, self.conn.execute("SELECT count(*) FROM signature_function_points WHERE updated_at='2001-01-01'").fetchone()[0])
        self.assertEqual(sequence, self.sequence())

    def test_missing_seed_is_inserted_once_and_existing_rows_are_untouched(self):
        self.conn.execute("DELETE FROM signature_function_points WHERE point_key=?", (self.key,))
        old_rows = self.conn.execute("SELECT * FROM signature_function_points ORDER BY id").fetchall()
        sequence = self.sequence()
        _seed_function_points(self.conn, engine="sqlite")
        self.assertEqual(sequence+1, self.sequence())
        self.assertEqual(old_rows, self.conn.execute("SELECT * FROM signature_function_points WHERE point_key<>? ORDER BY id", (self.key,)).fetchall())
        _seed_function_points(self.conn, engine="sqlite")
        self.assertEqual(sequence+1, self.sequence())
        self.assertEqual(len(SIGNATURE_FUNCTION_POINTS), self.conn.execute("SELECT count(*) FROM signature_function_points").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
