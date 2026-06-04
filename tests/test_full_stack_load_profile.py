import unittest
import sqlite3
import tempfile
from pathlib import Path

from tools.full_stack_load_test import (
    AssignmentTarget,
    MaterialTarget,
    ScenarioContext,
    SeededStudent,
    TargetOffering,
    assert_safe_runtime_root,
    build_final_report,
    build_gate_summary,
    discover_assignment_target,
    resolve_scenario_concurrency,
    resolve_student_count,
)


class FullStackLoadProfileTests(unittest.TestCase):
    def test_classroom_200_profile_resolves_default_student_count(self):
        self.assertEqual(resolve_student_count("classroom-200", None), 200)
        self.assertEqual(resolve_student_count("classroom-200", 12), 12)
        self.assertEqual(resolve_student_count("custom", None), 100)
        self.assertEqual(resolve_scenario_concurrency("classroom-200", None, student_count=200), 50)
        self.assertEqual(resolve_scenario_concurrency("classroom-200", 500, student_count=200), 200)

    def test_runtime_root_rejects_real_data_directory(self):
        source_db = Path("C:/repo/lanshare/data/classroom.db")
        with self.assertRaises(ValueError):
            assert_safe_runtime_root(Path("C:/repo/lanshare/data/p11-runtime"), source_db=source_db)

    def test_report_redacts_password_sample_and_exposes_profile(self):
        offering = TargetOffering(
            id=1,
            class_id=2,
            teacher_id=3,
            class_name="Class",
            course_name="Course",
            assignment_count=1,
            material_assignment_count=1,
            course_file_count=1,
            chat_log_count=1,
        )
        ctx = ScenarioContext(
            base_url="http://127.0.0.1:8000",
            ws_url="ws://127.0.0.1:8000",
            offering=offering,
            students=[],
            assignment_target=AssignmentTarget(id=1, title="Homework", is_exam=False),
            material_target=MaterialTarget(root_id=1, file_id=2, file_name="demo.pdf"),
            ai_mode="mock",
        )
        report = build_final_report(
            started_at="2026-06-04T00:00:00",
            completed_at="2026-06-04T00:00:01",
            duration_seconds=1.0,
            ctx=ctx,
            profile_name="classroom-200",
            scenario_concurrency=50,
            students=[
                SeededStudent(
                    index=1,
                    student_pk=10,
                    student_id_number="LS001",
                    name="Student",
                    password="plain-password",
                )
            ],
            credentials_path="",
            action_summary={},
            scenario_results=[{"success": True}],
            server_snapshot={"metrics": {"runtime": {"http": {"status_counts": {"200": 1}}}}},
            data_safety={"writes_to_source_db": False},
            artifact_dir="",
            kept_artifacts=False,
            process_logs={},
        )

        self.assertEqual(report["load_profile"]["profile"], "classroom-200")
        self.assertEqual(report["load_profile"]["scenario_concurrency"], 50)
        self.assertEqual(report["credential_sample"][0]["password"], "[REDACTED]")

    def test_gate_summary_fails_on_5xx_or_action_failure(self):
        gate = build_gate_summary(
            action_summary={"login": {"attempts": 1, "failures": 1, "p95_ms": 10}},
            scenario_results=[{"success": True}],
            server_snapshot={"metrics": {"runtime": {"http": {"status_counts": {"200": 1, "500": 1}}}}},
            min_success_rate=99.0,
            max_http_5xx=0,
            max_action_p95_ms=0.0,
        )

        self.assertFalse(gate["passed"])
        self.assertIn("action_failures", gate["failures"])
        self.assertIn("http_5xx", gate["failures"])

    def test_assignment_target_prefers_regular_homework_over_exam(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "classroom.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE assignments (
                        id INTEGER PRIMARY KEY,
                        class_offering_id INTEGER,
                        title TEXT,
                        status TEXT,
                        due_at TEXT,
                        exam_paper_id INTEGER
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?)",
                    (1, 7, "Exam", "published", "", 101),
                )
                conn.execute(
                    "INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?)",
                    (2, 7, "Homework", "published", "", None),
                )
                conn.commit()
            finally:
                conn.close()

            target = discover_assignment_target(db_path, 7)

        self.assertIsNotNone(target)
        self.assertEqual(target.id, 2)
        self.assertFalse(target.is_exam)


if __name__ == "__main__":
    unittest.main()
