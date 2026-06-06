import json
import shutil
import unittest
from pathlib import Path

from tools import db_cutover_gate


class DatabaseCutoverGateTests(unittest.TestCase):
    def setUp(self):
        self.report_root = db_cutover_gate.REPO_ROOT / ".codex-temp" / "unit-cutover-gate"
        self.target_root = db_cutover_gate.REPO_ROOT / ".codex-temp" / "unit-cutover-targets"
        for path in (self.report_root, self.target_root):
            if path.exists():
                shutil.rmtree(path)
        self.report_root.mkdir(parents=True, exist_ok=True)
        self.target_root.mkdir(parents=True, exist_ok=True)
        for relative in db_cutover_gate.REPORT_PATHS.values():
            (self.report_root / relative).parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for path in (self.report_root, self.target_root):
            if path.exists():
                shutil.rmtree(path)

    def _write_report(self, key: str, payload: dict):
        path = self.report_root / db_cutover_gate.REPORT_PATHS[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_ready_reports(self):
        self._write_report("migration_readiness", {"status": "ok", "foreign_key_violations": 0})
        self._write_report("file_integrity", {"status": "ok", "missing_references": 0})
        self._write_report(
            "pg_lab",
            {"status": "ok", "postgres_target": {"actual_postgres_data_load_executed": True}},
        )
        self._write_report("postgres_preflight", {"status": "ok", "postgres_cutover_requested": True, "blockers": []})
        self._write_report(
            "backup_rollback",
            {"status": "ok", "sqlite_restore_drill_executed": True, "postgres_restore_drill_executed": True},
        )
        self._write_report(
            "performance_acceptance",
            {
                "status": "ok",
                "acceptance_gates": {
                    "postgres_baseline_recorded": True,
                    "remote_docker_load_test_recorded": True,
                },
            },
        )
        (self.target_root / "T01-test.md").write_text("## 执行记录\n已完成\n", encoding="utf-8")

    def test_gate_reports_ready_when_all_required_reports_pass(self):
        self._write_ready_reports()

        report = db_cutover_gate.build_cutover_gate_report(
            report_root=self.report_root,
            target_root=self.target_root,
        )

        self.assertEqual("ready", report["status"])
        self.assertEqual([], report["blockers"])
        self.assertFalse(report["safety"]["cutover_executed"])

    def test_pre_cutover_phase_allows_sqlite_docker_env_before_stage_four(self):
        self._write_ready_reports()
        self._write_report("postgres_preflight", {"status": "ok", "postgres_cutover_requested": False, "blockers": []})

        report = db_cutover_gate.build_cutover_gate_report(
            report_root=self.report_root,
            target_root=self.target_root,
            phase="pre-cutover",
        )

        self.assertEqual("ready", report["status"])
        blocker_ids = {item["id"] for item in report["blockers"]}
        warning_ids = {item["id"] for item in report["warnings"]}
        self.assertNotIn("CUT-R005", blocker_ids)
        self.assertIn("CUT-W004", warning_ids)

    def test_pre_cutover_phase_blocks_if_docker_env_was_switched_too_early(self):
        self._write_ready_reports()

        report = db_cutover_gate.build_cutover_gate_report(
            report_root=self.report_root,
            target_root=self.target_root,
            phase="pre-cutover",
        )

        self.assertEqual("blocked", report["status"])
        blocker_ids = {item["id"] for item in report["blockers"]}
        self.assertIn("CUT-R011", blocker_ids)

    def test_gate_blocks_known_unresolved_migration_risks(self):
        self._write_ready_reports()
        self._write_report("migration_readiness", {"status": "ok", "foreign_key_violations": 2})
        self._write_report("file_integrity", {"status": "ok", "missing_references": 4})
        self._write_report(
            "backup_rollback",
            {"status": "ok", "sqlite_restore_drill_executed": True, "postgres_restore_drill_executed": False},
        )

        report = db_cutover_gate.build_cutover_gate_report(
            report_root=self.report_root,
            target_root=self.target_root,
        )

        self.assertEqual("blocked", report["status"])
        blocker_ids = {item["id"] for item in report["blockers"]}
        self.assertIn("CUT-R002", blocker_ids)
        self.assertIn("CUT-R003", blocker_ids)
        self.assertIn("CUT-R008", blocker_ids)

    def test_remediation_plan_can_downgrade_foreign_key_blocker_to_warning(self):
        self._write_ready_reports()
        self._write_report("migration_readiness", {"status": "ok", "foreign_key_violations": 2})
        remediation_path = self.report_root / db_cutover_gate.OPTIONAL_REPORT_PATHS["remediation_plan"]
        remediation_path.parent.mkdir(parents=True, exist_ok=True)
        remediation_path.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "apply_to_copy": True,
                    "cutover_effect": {"foreign_key_blocker_cleared_on_copy": True},
                }
            ),
            encoding="utf-8",
        )

        report = db_cutover_gate.build_cutover_gate_report(
            report_root=self.report_root,
            target_root=self.target_root,
        )

        blocker_ids = {item["id"] for item in report["blockers"]}
        warning_ids = {item["id"] for item in report["warnings"]}
        self.assertNotIn("CUT-R002", blocker_ids)
        self.assertIn("CUT-W002", warning_ids)

    def test_attachment_restore_plan_can_downgrade_file_blocker_to_warning(self):
        self._write_ready_reports()
        self._write_report("file_integrity", {"status": "ok", "missing_references": 3})
        attachment_path = self.report_root / db_cutover_gate.OPTIONAL_REPORT_PATHS["attachment_restore_plan"]
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        attachment_path.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "cutover_effect": {
                        "file_blocker_cleared": True,
                        "all_missing_files_restored": False,
                        "accepted_exception_manifest_valid": True,
                    },
                }
            ),
            encoding="utf-8",
        )

        report = db_cutover_gate.build_cutover_gate_report(
            report_root=self.report_root,
            target_root=self.target_root,
        )

        blocker_ids = {item["id"] for item in report["blockers"]}
        warning_ids = {item["id"] for item in report["warnings"]}
        self.assertNotIn("CUT-R003", blocker_ids)
        self.assertIn("CUT-W003", warning_ids)


if __name__ == "__main__":
    unittest.main()
