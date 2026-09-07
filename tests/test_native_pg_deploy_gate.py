import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.deploy.validate_native_pg_rehearsal import (
    REPORT_CONTRACT, REQUIRED_MIGRATION_FILES, file_hash, migration_source_hashes, validate_report,
)

REPO = Path(__file__).resolve().parents[1]


class NativePostgresDeployGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in REQUIRED_MIGRATION_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# synthetic migration source\n", encoding="utf-8")
        self.backup = self.root / "source.dump"
        self.backup.write_bytes(b"PGDMP synthetic contract-test sentinel")
        run = {"status": "ok", "required_table_count": 2, "present_required_table_count": 2,
               "index_failed": 0, "skipped_step_count": 0}
        self.report = {
            "report_contract": REPORT_CONTRACT, "status": "ok", "database_engine": "postgres",
            "source_export": {"remote_local_hash_equal": True, "unchanged_after_rehearsal": True,
                              "sha256": file_hash(self.backup), "bytes": self.backup.stat().st_size},
            "clean_restore": {"restored_again_from_original_dump": True,
                              "baseline_equals_initial_unmigrated_restore": True, "no_timestamp_or_sequence_whitelist": True},
            "incremental": {"status": "ok", "database_preservation_passed": True,
                            "old_differences_per_pass": [[], []], "idempotency_differences": []},
            "full_startup": {"strict_status": "ok", "fresh_process_per_pass": True,
                             "all_old_fields_and_sequences_unchanged": True,
                             "old_differences_per_pass": [[], []], "idempotency_differences": [], "runs": [run, run.copy()]},
            "isolation": {"dedicated_new_cluster": True, "dedicated_new_database": True,
                          "production_tables_modified": False, "app_or_workers_started": False},
            "migration_source_sha256": migration_source_hashes(self.root),
        }

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, report=None):
        return validate_report(self.report if report is None else report, repo_root=self.root, backup_file=self.backup)

    def test_requires_both_passed_native_phases_and_matching_explicit_backup(self):
        self.assertEqual("ok", self.validate()["status"])
        for field, value in (("database_engine", "sqlite"), ("status", "failed"), ("report_contract", None)):
            report = copy.deepcopy(self.report)
            report[field] = value
            self.assertEqual("failed", self.validate(report)["status"])
        self.backup.write_bytes(b"PGDMP different snapshot")
        self.assertIn("backup_digest_mismatch", self.validate()["blockers"])

    def test_missing_or_nonzero_checks_and_skipped_startup_steps_are_rejected(self):
        for phase, field, value in (
            ("incremental", "old_differences_per_pass", [[]]),
            ("incremental", "idempotency_differences", ["table:submissions"]),
            ("full_startup", "strict_status", "failed"),
            ("full_startup", "old_differences_per_pass", [[], ["sequence:old_seq"]]),
            ("full_startup", "fresh_process_per_pass", False),
            ("full_startup", "runs", []),
        ):
            report = copy.deepcopy(self.report)
            report[phase][field] = value
            self.assertEqual("failed", self.validate(report)["status"], (phase, field))
        report = copy.deepcopy(self.report)
        report["full_startup"]["runs"][1]["skipped_step_count"] = 1
        self.assertIn("startup_run_2_incomplete", self.validate(report)["blockers"])

    def test_stale_source_new_module_or_removed_hash_cannot_reuse_old_report(self):
        source = self.root / REQUIRED_MIGRATION_FILES[0]
        source.write_text("# changed migration dependency\n", encoding="utf-8")
        self.assertIn("migration_source_digest_mismatch", self.validate()["blockers"])
        source.write_text("# synthetic migration source\n", encoding="utf-8")
        extra = self.root / "classroom_app/db/new_migration.py"
        extra.write_text("# additive schema is still unverified\n", encoding="utf-8")
        self.assertIn("migration_source_set_mismatch", self.validate()["blockers"])
        report = copy.deepcopy(self.report)
        report.pop("migration_source_sha256")
        self.assertEqual("failed", self.validate(report)["status"])


@unittest.skipUnless(os.name == "nt" and (REPO / "deployment/deploy_remote.ps1").is_file(),
                     "Requires Windows and the intentionally local, gitignored deployment script")
class PowerShellDeployContractTests(unittest.TestCase):
    def test_isolated_manifest_protects_certificates_and_runtime_but_includes_vite_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {
                ".gitignore": "node_modules/\nstatic/dist/\n.codex-temp/\n", "tracked.py": "# code",
                "static/dist/manifest.json": "{}", "static/dist/assets/app.js": "console.log('build')",
                "tools/cert-backup-0829/server.key": "not an actual key",
                "tools/cert-new-0829/server.crt": "not an actual certificate",
                "docs/ai-vision-old-benchmark.md": "old report", "data/student.json": "not actual data",
                ".codex-temp/session.json": "not actual data", "node_modules/dummy.js": "ignored dependency",
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "add", ".gitignore", "tracked.py"], check=True, capture_output=True)
            script_path = str(REPO / "deployment/deploy_remote.ps1").replace("'", "''")
            root_path = str(root).replace("'", "''")
            code = f"""$ast=[System.Management.Automation.Language.Parser]::ParseFile('{script_path}',[ref]$null,[ref]$null)
$wanted=@('Normalize-ArchivePath','Test-ProtectedDeployPath','Get-DeployFileList')
$ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $wanted}},$false) | ForEach-Object {{Invoke-Expression $_.Extent.Text}}
@(Get-DeployFileList -RepoRoot '{root_path}') | ConvertTo-Json -Compress
"""
            result = subprocess.run(["powershell", "-NoProfile", "-Command", code], capture_output=True,
                                    text=True, encoding="utf-8", check=True)
            self.assertEqual({".gitignore", "tracked.py", "static/dist/manifest.json", "static/dist/assets/app.js"},
                             set(json.loads(result.stdout.strip())))

    def test_native_parameters_are_not_overwritten_and_dryrun_precedes_remote_upload(self):
        script = (REPO / "deployment/deploy_remote.ps1").read_text(encoding="utf-8")
        self.assertIn('[string]$MigrationReport = ""', script)
        self.assertIn('[string]$MigrationBackup = ""', script)
        self.assertNotIn('$migrationReport = Join-Path', script)
        self.assertIn('elseif ($configuredPostgres)', script)
        self.assertLess(script.index('if ($DryRun) {'), script.index('& scp @sshBaseArgs'))


if __name__ == "__main__":
    unittest.main()
