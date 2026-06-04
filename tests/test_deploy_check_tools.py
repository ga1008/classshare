import json
import tempfile
import unittest
from pathlib import Path

from tools.deploy.check_manifest import check_manifest
from tools.deploy.migration_dry_run import REPO_ROOT, resolve_runtime_root


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


if __name__ == "__main__":
    unittest.main()

