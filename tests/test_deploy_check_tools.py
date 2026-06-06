import json
import tempfile
import unittest
from pathlib import Path

from tools.deploy.check_manifest import check_manifest
from tools.deploy.health_backend_check import build_health_backend_report
from tools.deploy.migration_dry_run import REPO_ROOT, resolve_runtime_root
from tools.deploy.postgres_preflight import check_postgres_preflight


class DeployCheckToolsTests(unittest.TestCase):
    def test_manifest_check_fails_when_required_entry_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist_dir = root / "dist"
            dist_dir.mkdir()
            manifest_path = dist_dir / "manifest.json"
            manifest_path.write_text(json.dumps({}), encoding="utf-8")

            report = check_manifest(
                manifest_path=manifest_path,
                dist_dir=dist_dir,
                required_entries=("frontend/src/islands/app-shell.tsx",),
            )

        self.assertEqual("failed", report["status"])
        self.assertEqual(["frontend/src/islands/app-shell.tsx"], report["missing_entries"])

    def test_manifest_check_fails_when_built_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist_dir = root / "dist"
            dist_dir.mkdir()
            manifest_path = dist_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "frontend/src/islands/app-shell.tsx": {
                            "src": "frontend/src/islands/app-shell.tsx",
                            "file": "assets/app-shell-missing.js",
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = check_manifest(
                manifest_path=manifest_path,
                dist_dir=dist_dir,
                required_entries=("frontend/src/islands/app-shell.tsx",),
            )

        self.assertEqual("failed", report["status"])
        self.assertEqual(["assets/app-shell-missing.js"], report["missing_files"])

    def test_manifest_check_passes_for_present_required_entry_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist_dir = root / "dist"
            (dist_dir / "assets").mkdir(parents=True)
            (dist_dir / "assets" / "app-shell.js").write_text("console.log('ok')", encoding="utf-8")
            manifest_path = dist_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "frontend/src/islands/app-shell.tsx": {
                            "src": "frontend/src/islands/app-shell.tsx",
                            "file": "assets/app-shell.js",
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = check_manifest(
                manifest_path=manifest_path,
                dist_dir=dist_dir,
                required_entries=("frontend/src/islands/app-shell.tsx",),
            )

        self.assertEqual("ok", report["status"])
        self.assertEqual([], report["missing_entries"])
        self.assertEqual([], report["missing_files"])

    def test_migration_dry_run_runtime_must_stay_under_codex_temp(self):
        unsafe = REPO_ROOT / "data" / "not-a-dry-run"

        with self.assertRaises(ValueError):
            resolve_runtime_root(str(unsafe))

    def test_postgres_preflight_passes_for_sqlite_default_overlay_readiness(self):
        report = check_postgres_preflight()

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["postgres_cutover_requested"])
        self.assertFalse(report["production_data_modified"])
        warning_ids = {item["id"] for item in report["warnings"]}
        self.assertIn("PGD-W002", warning_ids)

    def test_postgres_preflight_blocks_incomplete_postgres_cutover_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "docker.env"
            env_file.write_text(
                "\n".join(
                    [
                        "DB_ENGINE=postgres",
                        "DATABASE_URL=",
                        "POSTGRES_PASSWORD=replace-me",
                        "POSTGRES_BACKEND_READY=false",
                    ]
                ),
                encoding="utf-8",
            )

            report = check_postgres_preflight(env_file=env_file)

        self.assertEqual("failed", report["status"])
        blocker_ids = {item["id"] for item in report["blockers"]}
        self.assertIn("PGD-R004", blocker_ids)
        self.assertIn("PGD-R005", blocker_ids)
        self.assertIn("PGD-R006", blocker_ids)
        self.assertIn("PGD-R007", blocker_ids)

    def test_postgres_preflight_redacts_api_keys_and_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "docker.env"
            env_file.write_text(
                "\n".join(
                    [
                        "DB_ENGINE=sqlite",
                        "DEEPSEEK_API_KEY=sk-real-value",
                        "ARK_API_KEY=uuid-real-value",
                        "ACADEMIC_GXUFL_USERNAME=teacher-login",
                        "ACADEMIC_GXUFL_PASSWORD=teacher-password",
                        "NORMAL_FLAG=true",
                    ]
                ),
                encoding="utf-8",
            )

            report = check_postgres_preflight(env_file=env_file)

        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("sk-real-value", rendered)
        self.assertNotIn("uuid-real-value", rendered)
        self.assertNotIn("teacher-login", rendered)
        self.assertNotIn("teacher-password", rendered)
        self.assertEqual("***", report["checked_env"]["DEEPSEEK_API_KEY"])
        self.assertEqual("***", report["checked_env"]["ACADEMIC_GXUFL_USERNAME"])
        self.assertEqual("true", report["checked_env"]["NORMAL_FLAG"])

    def test_health_backend_check_passes_for_expected_postgres(self):
        report = build_health_backend_report(
            {
                "status": "ok",
                "database_backend": {
                    "engine": "postgres",
                    "configured": True,
                    "details": "postgresql://***:***@db/lanshare",
                },
            },
            expected_engine="postgres",
        )

        self.assertEqual("ok", report["status"])
        self.assertEqual("postgres", report["observed"]["engine"])
        self.assertFalse(report["safety"]["contains_plaintext_database_url"])

    def test_health_backend_check_fails_on_engine_mismatch(self):
        report = build_health_backend_report(
            {
                "status": "ok",
                "database_backend": {
                    "engine": "sqlite",
                    "configured": True,
                    "details": "data/classroom.db",
                },
            },
            expected_engine="postgres",
        )

        self.assertEqual("failed", report["status"])
        failure_ids = {item["id"] for item in report["failures"]}
        warning_ids = {item["id"] for item in report["warnings"]}
        self.assertIn("PGH-R002", failure_ids)
        self.assertIn("PGH-W001", warning_ids)

    def test_health_backend_check_redacts_plaintext_database_url_in_report(self):
        report = build_health_backend_report(
            {
                "status": "ok",
                "database_backend": {
                    "engine": "postgres",
                    "configured": True,
                    "details": "postgresql://lan:secret-password@db/lanshare",
                },
            },
            expected_engine="postgres",
        )

        self.assertEqual("failed", report["status"])
        failure_ids = {item["id"] for item in report["failures"]}
        self.assertIn("PGH-R004", failure_ids)
        self.assertTrue(report["safety"]["contains_plaintext_database_url"])
        self.assertNotIn("secret-password", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
