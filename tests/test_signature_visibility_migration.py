from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db.schema_signature_workflow import migrate_signature_visibility_levels
from classroom_app.services import signature_identity_service


class SignatureVisibilityMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE electronic_signatures (
            id INTEGER PRIMARY KEY, scope_level TEXT, school_code TEXT, college TEXT, department TEXT,
            subject_role TEXT DEFAULT 'teacher', subject_id INTEGER DEFAULT 1,
            identity_category TEXT DEFAULT '', identity_verified INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active', deleted_at TEXT, updated_at TEXT DEFAULT 'original')""")
        self.addCleanup(self.conn.close)

    def test_legacy_scopes_change_once_and_future_missing_fields_do_not_expand(self):
        originals = [
            (1, "college", "school-a", "college-a", "department-a"),
            (2, "department", "school-a", "college-a", ""),
            (3, "department", "school-a", "", ""),
            (4, "department", "", "", ""),
            (5, "personal", "school-a", "college-a", ""),
            (6, "platform", "", "", ""),
        ]
        self.conn.executemany("INSERT INTO electronic_signatures (id,scope_level,school_code,college,department) VALUES (?,?,?,?,?)", originals)
        migrate_signature_visibility_levels(self.conn, engine="sqlite")
        self.assertEqual(["department", "college", "school", "department", "personal", "platform"],
                         [row[0] for row in self.conn.execute("SELECT scope_level FROM electronic_signatures ORDER BY id")])
        self.assertEqual(["original"] * 6, [row[0] for row in self.conn.execute("SELECT updated_at FROM electronic_signatures")])
        self.conn.execute("UPDATE electronic_signatures SET scope_level='college' WHERE id=1")
        self.conn.execute("INSERT INTO electronic_signatures (id,scope_level,school_code,college,department) VALUES (7,'department','school-a','college-a','')")
        migrate_signature_visibility_levels(self.conn, engine="sqlite")
        self.assertEqual("college", self.conn.execute("SELECT scope_level FROM electronic_signatures WHERE id=1").fetchone()[0])
        self.assertEqual("department", self.conn.execute("SELECT scope_level FROM electronic_signatures WHERE id=7").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])

    def test_identity_change_does_not_rewrite_visibility_organization(self):
        self.conn.execute("INSERT INTO electronic_signatures (id,scope_level,school_code,college,department) VALUES (1,'department','school-a','college-a','department-a')")
        with patch.object(signature_identity_service, "get_account_identity", return_value="dean"):
            signature_identity_service.sync_identity_for_signature(self.conn, 1)
        row = self.conn.execute("SELECT identity_category, department, scope_level FROM electronic_signatures WHERE id=1").fetchone()
        self.assertEqual(("dean", "department-a", "department"), tuple(row))
        signature_identity_service.propagate_account_identity(self.conn, "teacher", 1, "principal")
        row = self.conn.execute("SELECT identity_category, department, scope_level FROM electronic_signatures WHERE id=1").fetchone()
        self.assertEqual(("principal", "department-a", "department"), tuple(row))


if __name__ == "__main__":
    unittest.main()
