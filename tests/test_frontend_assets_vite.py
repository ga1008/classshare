import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app import frontend_assets


def _write_vite_manifest(tmp_path: Path, manifest):
    static_dir = tmp_path / "static"
    dist_dir = static_dir / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)

    for entry in manifest.values():
        file_name = entry.get("file")
        if file_name:
            (dist_dir / file_name).write_text("// built asset\n", encoding="utf-8")
        for css_file in entry.get("css", []):
            (dist_dir / css_file).write_text("body {}\n", encoding="utf-8")

    manifest_path = dist_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return dist_dir, manifest_path


class ViteFrontendAssetTests(unittest.TestCase):
    def tearDown(self):
        frontend_assets.load_vite_manifest.cache_clear()
        os.environ.pop("LANSHARE_VITE_DEV_SERVER", None)

    def test_vite_entry_tags_include_modulepreload_css_and_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dist_dir, manifest_path = _write_vite_manifest(
                Path(temp_dir),
                {
                    "_shared-runtime.js": {
                        "file": "assets/shared-runtime.js",
                        "css": ["assets/shared-runtime.css"],
                    },
                    "frontend/src/islands/app-shell.tsx": {
                        "file": "assets/app-shell.js",
                        "src": "frontend/src/islands/app-shell.tsx",
                        "isEntry": True,
                        "imports": ["_shared-runtime.js"],
                        "css": ["assets/app-shell.css"],
                    },
                },
            )

            with (
                patch.object(frontend_assets, "VITE_DIST_DIR", dist_dir),
                patch.object(frontend_assets, "VITE_MANIFEST_PATH", manifest_path),
            ):
                frontend_assets.load_vite_manifest.cache_clear()
                tags = str(frontend_assets.vite_entry_tags("frontend/src/islands/app-shell.tsx"))

        self.assertIn('rel="modulepreload"', tags)
        self.assertIn("/static/dist/assets/shared-runtime.js", tags)
        self.assertIn("/static/dist/assets/shared-runtime.css", tags)
        self.assertIn("/static/dist/assets/app-shell.css", tags)
        self.assertIn('script type="module"', tags)
        self.assertIn("/static/dist/assets/app-shell.js", tags)

    def test_vite_entry_tags_support_dev_server(self):
        os.environ["LANSHARE_VITE_DEV_SERVER"] = "http://127.0.0.1:5173/"
        frontend_assets.load_vite_manifest.cache_clear()

        tags = str(frontend_assets.vite_entry_tags("frontend/src/islands/app-shell.tsx"))

        self.assertIn("http://127.0.0.1:5173/@vite/client", tags)
        self.assertIn("http://127.0.0.1:5173/frontend/src/islands/app-shell.tsx", tags)
        self.assertNotIn("/static/dist/", tags)

    def test_vite_entry_tags_reject_path_traversal(self):
        frontend_assets.load_vite_manifest.cache_clear()

        with self.assertRaises(ValueError):
            frontend_assets.vite_entry_tags("../frontend/src/islands/app-shell.tsx")

    def test_load_vite_manifest_fails_when_built_file_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            dist_dir = static_dir / "dist"
            dist_dir.mkdir(parents=True)
            manifest_path = dist_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "frontend/src/islands/app-shell.tsx": {
                            "file": "assets/missing.js",
                            "src": "frontend/src/islands/app-shell.tsx",
                            "isEntry": True,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(frontend_assets, "VITE_DIST_DIR", dist_dir),
                patch.object(frontend_assets, "VITE_MANIFEST_PATH", manifest_path),
            ):
                frontend_assets.load_vite_manifest.cache_clear()
                with self.assertRaises(FileNotFoundError):
                    frontend_assets.load_vite_manifest()


if __name__ == "__main__":
    unittest.main()
