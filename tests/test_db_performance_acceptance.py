import shutil
import sqlite3
import unittest
import json

from tools import db_performance_acceptance


class DatabasePerformanceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_performance_acceptance.TEMP_ROOT / "unit-db-performance"
        self.source_root = db_performance_acceptance.TEMP_ROOT / "unit-db-performance-source"
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.source_db = self.source_root / "source.db"
        conn = sqlite3.connect(self.source_db)
        try:
            conn.execute(
                """
                CREATE TABLE submissions (
                    id INTEGER PRIMARY KEY,
                    assignment_id INTEGER,
                    student_pk_id INTEGER,
                    status TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE submission_files (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER,
                    stored_path TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO submissions (assignment_id, student_pk_id, status) VALUES (1, 2, 'submitted')"
            )
            conn.execute("INSERT INTO submission_files (submission_id, stored_path) VALUES (1, 'a.txt')")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def test_report_recommends_missing_indexes_for_hot_paths(self):
        report = db_performance_acceptance.build_performance_acceptance_report(
            self.runtime_root,
            source_db=self.source_db,
        )

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["safety"]["production_data_modified"])
        by_table = {item["table"]: item for item in report["missing_index_recommendations"]}
        self.assertIn("submissions", by_table)
        self.assertIn("submission_files", by_table)
        self.assertIn("CREATE INDEX CONCURRENTLY", by_table["submissions"]["postgres_index_sql"])

    def test_runtime_root_must_stay_under_codex_temp(self):
        unsafe = db_performance_acceptance.REPO_ROOT / "data" / "performance"

        with self.assertRaises(ValueError):
            db_performance_acceptance.resolve_runtime_root(unsafe)

    def test_postgres_performance_report_marks_baseline_recorded(self):
        postgres_report = self.source_root / "postgres-performance.json"
        postgres_report.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "postgres_baseline_recorded": True,
                    "remote_docker_load_test_recorded": False,
                    "query_results": [{"name": "submissions_lookup", "elapsed_ms": 12}],
                }
            ),
            encoding="utf-8",
        )

        report = db_performance_acceptance.build_performance_acceptance_report(
            self.runtime_root,
            source_db=self.source_db,
            postgres_performance_report=postgres_report,
        )

        self.assertTrue(report["acceptance_gates"]["postgres_baseline_recorded"])
        self.assertFalse(report["acceptance_gates"]["remote_docker_load_test_recorded"])


if __name__ == "__main__":
    unittest.main()
