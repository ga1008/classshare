import sqlite3
import unittest

from classroom_app.routers.materials_parts import ai_import
from classroom_app.routers.materials_parts import ai_import_helpers as helpers


class _FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _FakePostgresMaterialImportConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("UPDATE material_ai_import_records"):
            return _FakeCursor(
                {
                    "id": 7,
                    "parse_status": "running",
                    "started_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                    "error_message": "",
                },
                rowcount=1,
            )
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1


class _FakePostgresMaterialImportInsertConnection:
    def __init__(self):
        self.calls = []
        self.row = {
            "id": 9,
            "teacher_id": 3,
            "parent_material_id": None,
            "document_group": "final",
            "document_type": "final_report",
            "document_type_label": "Final report",
            "parse_status": "queued",
            "parse_mode": "ai",
            "source_file_name": "report.docx",
            "source_file_hash": "abc123",
            "source_file_size": 10,
            "source_mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "metadata_json": "{}",
            "error_message": "",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO material_ai_import_records"):
            return _FakeCursor(self.row, rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")


class MaterialAIImportQueueClaimTests(unittest.TestCase):
    def _sqlite_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                parse_status TEXT,
                started_at TEXT,
                updated_at TEXT,
                error_message TEXT
            );
            INSERT INTO material_ai_import_records
                (id, parse_status, started_at, updated_at, error_message)
            VALUES
                (1, 'queued', NULL, '2026-01-01T00:00:00', 'old'),
                (2, 'running', '2026-01-01T00:00:00', '2026-01-01T00:00:00', ''),
                (3, 'completed', '2026-01-01T00:00:00', '2026-01-01T00:00:00', '');
            """
        )
        return conn

    def _sqlite_insert_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER,
                package_material_id INTEGER,
                source_material_id INTEGER,
                parsed_material_id INTEGER,
                parent_material_id INTEGER,
                document_group TEXT,
                document_type TEXT,
                document_type_label TEXT,
                parse_status TEXT,
                parse_mode TEXT,
                extraction_method TEXT,
                source_file_name TEXT,
                source_file_hash TEXT,
                source_file_size INTEGER,
                source_mime_type TEXT,
                metadata_json TEXT,
                content_markdown TEXT,
                parsed_payload_json TEXT,
                export_payload_json TEXT,
                warnings_json TEXT,
                content_quality_status TEXT,
                content_quality_json TEXT,
                error_message TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT,
                completed_at TEXT,
                failed_at TEXT
            );
            """
        )
        return conn

    def test_sqlite_material_import_claim_requires_queued_status(self):
        conn = self._sqlite_conn()
        try:
            claimed = helpers._claim_material_ai_import_record(conn, 1, engine="sqlite")
            running_claim = helpers._claim_material_ai_import_record(conn, 2, engine="sqlite")
            completed_claim = helpers._claim_material_ai_import_record(conn, 3, engine="sqlite")
            rows = conn.execute(
                "SELECT id, parse_status, error_message FROM material_ai_import_records ORDER BY id"
            ).fetchall()

            self.assertEqual(1, claimed["id"])
            self.assertEqual("running", claimed["parse_status"])
            self.assertIsNone(running_claim)
            self.assertIsNone(completed_claim)
            self.assertEqual("running", rows[0]["parse_status"])
            self.assertEqual("", rows[0]["error_message"])
            self.assertEqual("running", rows[1]["parse_status"])
            self.assertEqual("completed", rows[2]["parse_status"])
        finally:
            conn.close()

    def test_postgres_material_import_claim_uses_skip_locked_returning(self):
        conn = _FakePostgresMaterialImportConnection()

        claimed = helpers._claim_material_ai_import_record(conn, 7, engine="postgres")

        self.assertEqual(7, claimed["id"])
        self.assertEqual(1, conn.commits)
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING *", sql)
        self.assertIn("AND parse_status = 'queued'", sql)
        self.assertEqual(7, params[2])

    def test_sqlite_insert_material_import_record_returns_inserted_row(self):
        conn = self._sqlite_insert_conn()
        try:
            row = ai_import._insert_material_ai_import_record(
                conn,
                teacher_id=3,
                parent_material_id=None,
                document_group="final",
                document_type="final_report",
                document_type_label="Final report",
                source_file_name="report.docx",
                source_file_hash="abc123",
                source_file_size=10,
                source_mime_type="application/docx",
                metadata_json="{}",
                now="2026-01-01T00:00:00",
                engine="sqlite",
            )

            self.assertEqual(1, row["id"])
            self.assertEqual("queued", row["parse_status"])
            self.assertEqual("ai", row["parse_mode"])
            self.assertEqual("report.docx", row["source_file_name"])
        finally:
            conn.close()

    def test_postgres_insert_material_import_record_uses_returning(self):
        conn = _FakePostgresMaterialImportInsertConnection()

        row = ai_import._insert_material_ai_import_record(
            conn,
            teacher_id=3,
            parent_material_id=None,
            document_group="final",
            document_type="final_report",
            document_type_label="Final report",
            source_file_name="report.docx",
            source_file_hash="abc123",
            source_file_size=10,
            source_mime_type="application/docx",
            metadata_json="{}",
            now="2026-01-01T00:00:00",
            engine="postgres",
        )

        self.assertEqual(9, row["id"])
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("RETURNING *", sql)
        self.assertEqual(3, params[0])
        self.assertEqual("final", params[2])
        self.assertEqual("queued", row["parse_status"])

    def test_material_import_claim_unknown_engine_fails_fast(self):
        conn = self._sqlite_conn()
        try:
            with self.assertRaises(ValueError):
                helpers._claim_material_ai_import_record(conn, 1, engine="mysql")
        finally:
            conn.close()

    def test_material_import_insert_unknown_engine_fails_fast(self):
        conn = self._sqlite_insert_conn()
        try:
            with self.assertRaises(ValueError):
                ai_import._insert_material_ai_import_record(
                    conn,
                    teacher_id=3,
                    parent_material_id=None,
                    document_group="final",
                    document_type="final_report",
                    document_type_label="Final report",
                    source_file_name="report.docx",
                    source_file_hash="abc123",
                    source_file_size=10,
                    source_mime_type="application/docx",
                    metadata_json="{}",
                    now="2026-01-01T00:00:00",
                    engine="mysql",
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
