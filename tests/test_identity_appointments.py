"""Multi-identity appointments with tenure (任职身份).

Covers: replace-all writes with validation, effective-identity windows,
legacy-column fallback, seniority-based primary recompute propagating to
bound signatures, automatic expiry demotion, and the bulk lookup used by
signature-point pickers.
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db import schema_signature_workflow
from classroom_app.services import signature_identity_service as ids


class IdentityAppointmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY, name TEXT,
                is_super_admin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO teachers VALUES (1, '陈忠伟', 0, 1);
            INSERT INTO teachers VALUES (2, '覃雅妮', 0, 1);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE electronic_signatures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject_name TEXT NOT NULL DEFAULT '',
                subject_role TEXT NOT NULL DEFAULT 'teacher',
                owner_role TEXT NOT NULL,
                owner_id INTEGER,
                owner_name_snapshot TEXT NOT NULL DEFAULT '',
                scope_level TEXT NOT NULL DEFAULT 'department',
                status TEXT NOT NULL DEFAULT 'active',
                updated_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE signature_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature_id INTEGER,
                action TEXT NOT NULL DEFAULT 'use',
                context_type TEXT NOT NULL DEFAULT '',
                context_id TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE signature_access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature_id INTEGER NOT NULL,
                requester_teacher_id INTEGER,
                owner_role TEXT NOT NULL DEFAULT '',
                owner_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                request_note TEXT NOT NULL DEFAULT '',
                review_note TEXT NOT NULL DEFAULT '',
                context_type TEXT NOT NULL DEFAULT '',
                context_id TEXT NOT NULL DEFAULT '',
                context_label TEXT NOT NULL DEFAULT '',
                requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                reviewed_by_teacher_id INTEGER
            );
            """
        )
        schema_signature_workflow._SCHEMA_READY = False
        with patch.object(schema_signature_workflow, "get_configured_db_engine", return_value="sqlite"):
            schema_signature_workflow.ensure_signature_workflow_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, subject_id,
                owner_role, owner_id, identity_category
            ) VALUES ('陈忠伟', '陈忠伟', 'teacher', 1, 'teacher', 1, '')
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        schema_signature_workflow._SCHEMA_READY = False

    def _account_identity(self, teacher_id: int = 1) -> str:
        row = self.conn.execute(
            "SELECT identity_category FROM teachers WHERE id = ?", (teacher_id,)
        ).fetchone()
        return row["identity_category"] or ""

    def _signature_identity(self) -> str:
        row = self.conn.execute(
            "SELECT identity_category FROM electronic_signatures WHERE id = 1"
        ).fetchone()
        return row["identity_category"] or ""

    def test_set_and_list_roundtrip_with_primary_recompute(self) -> None:
        items = ids.set_identity_appointments(
            self.conn,
            "teacher",
            1,
            [
                {"identity_category": "teacher"},
                {"identity_category": "department_head", "term_start": "2026-01-01", "term_end": "2027-12-31"},
            ],
        )
        self.assertEqual(2, len(items))
        # Seniority: department_head outranks teacher → primary identity.
        self.assertEqual("department_head", self._account_identity())
        # Bound signature follows the primary identity.
        self.assertEqual("department_head", self._signature_identity())
        self.assertEqual(
            ["department_head", "teacher"],
            ids.effective_identity_categories(self.conn, "teacher", 1),
        )

    def test_validation_rules(self) -> None:
        with self.assertRaises(ValueError):
            ids.set_identity_appointments(self.conn, "teacher", 1, [{"identity_category": "bogus"}])
        with self.assertRaises(ValueError):
            ids.set_identity_appointments(
                self.conn, "teacher", 1,
                [{"identity_category": "teacher"}, {"identity_category": "teacher"}],
            )
        with self.assertRaises(ValueError):
            ids.set_identity_appointments(
                self.conn, "teacher", 1,
                [{"identity_category": "dean", "term_start": "2027-01-01", "term_end": "2026-01-01"}],
            )
        with self.assertRaises(ValueError):
            ids.set_identity_appointments(
                self.conn, "teacher", 1,
                [{"identity_category": "dean", "term_end": "2026/12/31"}],
            )

    def test_term_window_excludes_future_and_past(self) -> None:
        ids.set_identity_appointments(
            self.conn,
            "teacher",
            1,
            [
                {"identity_category": "dean", "term_start": "2099-01-01"},
                {"identity_category": "counselor", "term_end": "2020-01-01"},
                {"identity_category": "teacher"},
            ],
        )
        self.assertEqual(
            ["teacher"], ids.effective_identity_categories(self.conn, "teacher", 1)
        )
        self.assertEqual("teacher", self._account_identity())

    def test_legacy_fallback_without_appointments(self) -> None:
        self.conn.execute("UPDATE teachers SET identity_category = 'counselor' WHERE id = 2")
        self.assertEqual(
            ["counselor"], ids.effective_identity_categories(self.conn, "teacher", 2)
        )

    def test_expiry_demotes_primary_and_signature(self) -> None:
        ids.set_identity_appointments(
            self.conn,
            "teacher",
            1,
            [
                {"identity_category": "department_head", "term_end": "2026-12-31"},
                {"identity_category": "teacher"},
            ],
        )
        self.assertEqual("department_head", self._account_identity())
        expired = ids.expire_identity_appointments(self.conn, today="2027-01-01")
        self.assertEqual(1, expired)
        rows = ids.list_identity_appointments(self.conn, "teacher", 1)
        by_category = {item["identity_category"]: item for item in rows}
        self.assertEqual("expired", by_category["department_head"]["status"])
        # NOTE: recompute uses actual today; the still-active 'teacher'
        # appointment has no term so it is effective regardless.
        self.assertEqual("teacher", self._account_identity())
        self.assertEqual("teacher", self._signature_identity())
        # Idempotent second sweep.
        self.assertEqual(0, ids.expire_identity_appointments(self.conn, today="2027-01-01"))

    def test_bulk_lookup_mixes_appointments_and_legacy(self) -> None:
        ids.set_identity_appointments(
            self.conn, "teacher", 1, [{"identity_category": "vice_dean"}]
        )
        self.conn.execute("UPDATE teachers SET identity_category = 'teacher' WHERE id = 2")
        result = ids.effective_identities_bulk(
            self.conn, [("teacher", 1), ("teacher", 2), ("teacher", 999)]
        )
        self.assertEqual(["vice_dean"], result[("teacher", 1)])
        self.assertEqual(["teacher"], result[("teacher", 2)])
        self.assertEqual([], result[("teacher", 999)])


if __name__ == "__main__":
    unittest.main()
