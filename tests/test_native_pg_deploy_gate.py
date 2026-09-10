import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.deploy.validate_native_pg_rehearsal import (
    REPORT_CONTRACT, REQUIRED_MIGRATION_FILES, file_hash, migration_source_hashes, validate_report,
)
from tools.agent_authority_migration_rehearsal import prove as prove_agent_authority

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
        empty_agent_proof = prove_agent_authority({"tables": {}, "authority_tables": {}}, {"tables": {}, "authority_tables": {}})
        for phase in ("incremental", "full_startup"):
            self.report[phase]["agent_authority_migration_per_pass"] = [empty_agent_proof, copy.deepcopy(empty_agent_proof)]
            self.report[phase]["unexpected_old_differences_per_pass"] = [[], []]

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

    def test_only_proven_signature_scope_migration_is_allowed(self):
        from tests.test_signature_visibility_rehearsal import SignatureVisibilityRehearsalTests
        from tools.signature_visibility_rehearsal import prove
        fixture = SignatureVisibilityRehearsalTests()
        fixture.setUp()
        proof = prove(fixture.before, fixture.after)
        for phase in ("incremental", "full_startup"):
            self.report[phase]["old_differences_per_pass"] = [proof["allowed_differences"]] * 2
            self.report[phase]["unexpected_old_differences_per_pass"] = [[], []]
            self.report[phase]["signature_visibility_migration_per_pass"] = [proof, copy.deepcopy(proof)]
        self.report["full_startup"]["all_old_fields_and_sequences_unchanged"] = False
        self.report["full_startup"]["all_old_fields_except_verified_signature_scope_and_sequences_unchanged"] = True
        self.assertEqual("ok", self.validate()["status"])
        for field, value in (("field", "updated_at"), ("after", "platform")):
            changed = copy.deepcopy(self.report)
            for item in changed["incremental"]["signature_visibility_migration_per_pass"]:
                item["expected_changes"][0][field] = value
                item["actual_changes"][0][field] = value
            self.assertEqual("failed", self.validate(changed)["status"])
        changed = copy.deepcopy(self.report)
        changed["incremental"]["old_differences_per_pass"][0].append("sequence:old_seq")
        self.assertEqual("failed", self.validate(changed)["status"])

    def test_agent_proof_is_required_even_if_old_projection_has_no_differences(self):
        self.report["incremental"].pop("agent_authority_migration_per_pass")
        self.assertIn("incremental_differences_or_missing_checks", self.validate()["blockers"])

    def test_agent_nullable_pk_and_row_changes_require_two_identical_exact_proofs(self):
        from tests.test_agent_authority_migration_rehearsal import migration_fixture
        before, after = migration_fixture()
        agent = prove_agent_authority(before, after)
        for phase in ("incremental", "full_startup"):
            self.report[phase]["agent_authority_migration_per_pass"] = [agent, copy.deepcopy(agent)]
            self.report[phase]["old_differences_per_pass"] = [agent["allowed_differences"], list(agent["allowed_differences"])]
        self.report["full_startup"]["all_old_fields_and_sequences_unchanged"] = False
        self.report["full_startup"]["all_old_fields_except_verified_migrations_and_sequences_unchanged"] = True
        self.assertEqual("ok", self.validate()["status"])
        changed = copy.deepcopy(self.report)
        changed["incremental"]["agent_authority_migration_per_pass"][1] = prove_agent_authority(after, after)
        self.assertEqual("failed", self.validate(changed)["status"])
        changed = copy.deepcopy(self.report)
        for item in changed["incremental"]["agent_authority_migration_per_pass"]:
            item["allowed_differences"].append("sequence:agent_tasks_id_seq")
        changed["incremental"]["old_differences_per_pass"][0].append("sequence:agent_tasks_id_seq")
        changed["incremental"]["old_differences_per_pass"][1].append("sequence:agent_tasks_id_seq")
        self.assertEqual("failed", self.validate(changed)["status"])

    def test_agent_and_signature_proofs_combine_without_expanding_whitelists(self):
        from tests.test_agent_authority_migration_rehearsal import migration_fixture
        from tests.test_signature_visibility_rehearsal import SignatureVisibilityRehearsalTests
        from tools.signature_visibility_rehearsal import prove as prove_signature
        before, after = migration_fixture()
        agent = prove_agent_authority(before, after)
        fixture = SignatureVisibilityRehearsalTests()
        fixture.setUp()
        signature = prove_signature(fixture.before, fixture.after)
        order = lambda item: ({"table": 0, "schema": 1}.get(item.split(":", 1)[0], 2), item)
        allowed = sorted(set(agent["allowed_differences"] + signature["allowed_differences"]), key=order)
        for phase in ("incremental", "full_startup"):
            self.report[phase]["agent_authority_migration_per_pass"] = [agent, copy.deepcopy(agent)]
            self.report[phase]["signature_visibility_migration_per_pass"] = [signature, copy.deepcopy(signature)]
            self.report[phase]["old_differences_per_pass"] = [allowed, list(allowed)]
        self.report["full_startup"]["all_old_fields_and_sequences_unchanged"] = False
        self.report["full_startup"]["all_old_fields_except_verified_migrations_and_sequences_unchanged"] = True
        self.assertEqual("ok", self.validate()["status"])
        self.report["full_startup"]["old_differences_per_pass"][1].append("table:submissions")
        self.assertEqual("failed", self.validate()["status"])


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

    def test_quiesced_cutover_requires_report_and_runs_after_build_before_up(self):
        script = (REPO / "deployment/deploy_remote.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('[switch]$QuiesceForMigration', script)
        self.assertIn('$QuiesceForMigration -and ($SkipDatabaseBackup -or', script)
        self.assertIn('Unexpected source mount', script)
        self.assertLess(script.index('bash deployment/docker/build_app.sh'), script.index('  run_quiesced_migration\n'))
        self.assertLess(script.index('  run_quiesced_migration\n'), script.index('"${compose_cmd[@]}" up -d'))
        self.assertIn('--lock-wait-timeout=5s', script)
        self.assertIn('docker image tag "$old_image" "$rollback_tag"', script)

    def test_quiesced_cutover_failure_does_not_continue_to_migration_or_service_start(self):
        bash = shutil.which("bash")
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.is_file():
            bash = str(git_bash)
        if not bash:
            self.skipTest("Local Bash is required to exercise the remote-script contract")
        script = (REPO / "deployment/deploy_remote.ps1").read_text(encoding="utf-8-sig")
        remote = script.split("    $remoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        parsed = subprocess.run([bash, "-n"], input=remote, text=True, capture_output=True)
        self.assertEqual(0, parsed.returncode, parsed.stderr)
        cutover = remote.split('run_quiesced_migration() {\n', 1)[1].split('\nif [ "$quiesce_for_migration"', 1)[0]
        # Execute only the isolated cutover function with a fake Compose command.
        # No Docker, database, network or real deployment directories are used.
        harness = r'''
set -euo pipefail
remote_path="$PWD"
backup_dir="$PWD"
ts=synthetic
writer_services=(app ai mailer blog-crawler scheduler agent-worker)
compose_cmd=(fake_compose)
fake_compose() {
  echo "$1" >> events
  if [ "$1" = stop ] && [ "$FAIL_AT" = stop ]; then return 31; fi
  if [ "$1" = ps ]; then return 0; fi
  if [ "$1" = exec ]; then
    if [ "$FAIL_AT" = backup ]; then return 32; fi
    printf 'synthetic PostgreSQL dump\n'
  fi
  if [ "$1" = run ]; then
    cat >/dev/null
    if [ "$FAIL_AT" = migration ]; then return 33; fi
  fi
}
'''
        harness += 'run_quiesced_migration() {\n' + cutover
        harness += '\nrun_quiesced_migration\necho started >> events\n'
        for failure, code in (("stop", 31), ("backup", 32), ("migration", 33), ("none", 0)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "docker.env").write_text("POSTGRES_USER=synthetic\nPOSTGRES_DB=synthetic\n", encoding="utf-8")
                result = subprocess.run([bash], input=harness, cwd=temp, text=True,
                                        capture_output=True, env={**os.environ, "FAIL_AT": failure})
                self.assertEqual(code, result.returncode, result.stderr)
                events = (root / "events").read_text().splitlines()
                self.assertEqual(failure == "none", "started" in events)
                self.assertEqual(failure in ("migration", "none"), "run" in events)

    def test_empty_backup_pruning_succeeds_without_hiding_deletion_failure(self):
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if not bash.is_file():
            self.skipTest("Local Git Bash is required for the backup retention contract")
        script = (REPO / "deployment/deploy_remote.ps1").read_text(encoding="utf-8-sig")
        body = script.split('prune_backup_files() {\n', 1)[1].split('\necho "Pruning old backups;', 1)[0]
        harness = 'set -euo pipefail\nkeep_backups=2\nprune_backup_files() {\n' + body
        harness += '\nshopt -s nullglob\n'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            empty = subprocess.run([str(bash)], cwd=temp, input=harness + 'prune_backup_files "$PWD"/db-*.sql.gz\necho done\n',
                                   capture_output=True, text=True)
            self.assertEqual((0, "done"), (empty.returncode, empty.stdout.strip()), empty.stderr)
            for index in range(3):
                file = root / f"db-{index}.sql.gz"
                file.write_text("synthetic backup", encoding="utf-8")
                os.utime(file, (100 + index, 100 + index))
            blocked = subprocess.run([str(bash)], cwd=temp, input=harness + 'rm() { return 43; }\nprune_backup_files "$PWD"/db-*.sql.gz\necho unexpected\n',
                                     capture_output=True, text=True)
            self.assertEqual(43, blocked.returncode)
            self.assertNotIn("unexpected", blocked.stdout)
            self.assertEqual(3, len(list(root.glob("db-*"))))
            retained = subprocess.run([str(bash)], cwd=temp, input=harness + 'prune_backup_files "$PWD"/db-*.sql.gz\n',
                                      capture_output=True, text=True)
            self.assertEqual(0, retained.returncode, retained.stderr)
            self.assertEqual({"db-1.sql.gz", "db-2.sql.gz"}, {file.name for file in root.glob("db-*")})


if __name__ == "__main__":
    unittest.main()
