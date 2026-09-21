import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from classroom_app import frontend_assets
from classroom_app.services.deployment_cache_service import static_asset_cache_control
from tools.publish_static_assets import publish_static_assets, seed_legacy_vite_assets


ROOT = Path(__file__).resolve().parents[1]


class StaticAssetGraphTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "static"
        files = {
            "js/first.js": "import './auth.js?v=old'; export { toast } from './ui.js?v=one';\n",
            "js/second.js": "import './auth.js?v=other'; export { toast } from './ui.js?v=two';\n",
            "js/auth.js": "globalThis.authLoads = (globalThis.authLoads || 0) + 1;\n",
            "js/ui.js": "export const toast = () => 'ok';\n",
            "css/tailwind-app.css": "@font-face {src:url('../fonts/local.woff2')}" + "\n/* padding */" * 100,
            "fonts/local.woff2": "sample-font",
            "css/ui-system.src.css": "not-published",
            "js/example.test.js": "not-published",
            "img/life_tips/live.png": "user-managed-media",
            "dist/assets/shell-abcdefgh.js": "// built Vite chunk\n",
            "dist/manifest.json": json.dumps({"shell": {"file": "assets/shell-abcdefgh.js"}}),
            "vendor/manifest.json": json.dumps({"tailwind_app": {"path": "css/tailwind-app.css"}}),
        }
        for name, content in files.items():
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        self.build()
        frontend_assets.load_static_asset_graph.cache_clear()
        frontend_assets.load_frontend_asset_manifest.cache_clear()
        frontend_assets.load_vite_manifest.cache_clear()

    def tearDown(self):
        frontend_assets.load_static_asset_graph.cache_clear()
        frontend_assets.load_frontend_asset_manifest.cache_clear()
        frontend_assets.load_vite_manifest.cache_clear()
        self.temporary.cleanup()

    def build(self):
        subprocess.run(
            ["node", str(ROOT / "tools/build_static_assets.mjs"), str(self.root)],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        return json.loads((self.root / "assets/manifest.json").read_text(encoding="utf-8"))

    def test_snapshot_is_deterministic_and_preserves_relative_graph_and_gzip(self):
        first = self.build()
        os.utime(self.root / "js/ui.js", (1, 1))
        second = self.build()
        self.assertEqual(first, second)
        directory = self.root / "assets" / first["revision"]
        self.assertIn("'./auth.js'", (directory / "js/first.js").read_text())
        self.assertNotIn("?v=", (directory / "js/second.js").read_text())
        css = (directory / "css/tailwind-app.css").read_bytes()
        self.assertEqual(css, gzip.decompress((directory / "css/tailwind-app.css.gz").read_bytes()))
        self.assertTrue((directory / "fonts/local.woff2").is_file())
        self.assertFalse((directory / "img/life_tips/live.png").exists())
        self.assertFalse((directory / "css/ui-system.src.css").exists())
        self.assertFalse((directory / "js/example.test.js").exists())

    def test_child_only_change_versions_parent_and_retains_previous_graph(self):
        first = self.build()
        old_parent = self.root / first["entries"]["js/first.js"]
        old_content = old_parent.read_bytes()
        (self.root / "js/ui.js").write_text("export const toast = () => 'new';", encoding="utf-8")
        second = self.build()
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertNotEqual(first["entries"]["js/first.js"], second["entries"]["js/first.js"])
        self.assertEqual(old_content, old_parent.read_bytes())

    def test_aliases_and_manifest_caches_rotate_with_deployment_release(self):
        with patch.object(frontend_assets, "STATIC_DIR", self.root), patch.object(
            frontend_assets, "ASSET_MANIFEST_PATH", self.root / "vendor/manifest.json"
        ), patch.dict(os.environ, {"LANSHARE_RELEASE_ID": "release-a"}):
            before = frontend_assets.asset_url("tailwind_app")
            self.assertEqual(before, frontend_assets.asset_url("css/tailwind-app.css"))
            self.assertNotIn("?v=", before)
            (self.root / "css/tailwind-app.css").write_text("body {color:red}", encoding="utf-8")
            self.build()
            with patch.dict(os.environ, {"LANSHARE_RELEASE_ID": "release-b"}):
                after = frontend_assets.asset_url("tailwind_app")
                self.assertNotEqual(before, after)
                self.assertIn(frontend_assets.static_asset_revision(), after)

    def test_unbuilt_sources_revalidate_and_malformed_hashes_are_not_immutable(self):
        revision = self.build()["revision"]
        self.assertIn("immutable", static_asset_cache_control(f"assets/{revision}/js/ui.js"))
        for path in ["js/ui.js?v=manual", "assets/manifest.json", "assets/short/js/ui.js", f"assets/{revision}/../ui.js"]:
            self.assertNotIn("immutable", static_asset_cache_control(path))

    def test_present_manifest_never_silently_mixes_an_unbuilt_code_entry(self):
        (self.root / "js/late.js").write_text("export const late = true;", encoding="utf-8")
        with patch.object(frontend_assets, "STATIC_DIR", self.root), patch.object(
            frontend_assets, "ASSET_MANIFEST_PATH", self.root / "vendor/manifest.json"
        ):
            with self.assertRaisesRegex(FileNotFoundError, "absent from the built graph"):
                frontend_assets.asset_url("js/late.js")
            self.assertEqual("/static/img/missing.png", frontend_assets.asset_url("img/missing.png"))

    def test_missing_relative_module_fails_build_before_publishing_manifest(self):
        before = (self.root / "assets/manifest.json").read_bytes()
        (self.root / "js/ui.js").write_text("import './missing.js';", encoding="utf-8")
        with self.assertRaises(subprocess.CalledProcessError):
            self.build()
        self.assertEqual(before, (self.root / "assets/manifest.json").read_bytes())

    def test_publisher_retains_old_graph_and_rejects_immutable_collisions(self):
        output = Path(self.temporary.name) / "published"
        first = self.build()
        self.assertGreater(publish_static_assets(self.root, output), 0)
        self.assertEqual(0, publish_static_assets(self.root, output))
        (self.root / "js/ui.js").write_text("export const toast = () => 'new';", encoding="utf-8")
        self.build()
        self.assertGreater(publish_static_assets(self.root, output), 0)
        self.assertTrue((output / first["entries"]["js/ui.js"]).is_file())
        self.assertFalse((output / "img").exists())
        current = self.build()
        (output / current["entries"]["js/ui.js"]).write_text("corrupt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "collision"):
            publish_static_assets(self.root, output)

    def test_vite_manifest_cache_uses_release_id(self):
        with patch.object(frontend_assets, "VITE_DIST_DIR", self.root / "dist"), patch.object(
            frontend_assets, "VITE_MANIFEST_PATH", self.root / "dist/manifest.json"
        ), patch.dict(os.environ, {"LANSHARE_RELEASE_ID": "vite-a"}):
            self.assertIn("shell", frontend_assets.load_vite_manifest())
            (self.root / "dist/manifest.json").write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"LANSHARE_RELEASE_ID": "vite-b"}):
                self.assertEqual({}, frontend_assets.load_vite_manifest())

    def test_first_upgrade_seeds_pre_graph_vite_chunks_before_publishing_new_image(self):
        output = Path(self.temporary.name) / "first-upgrade-volume"
        old_chunk = b"export const release = 'pre-S0';"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            member = tarfile.TarInfo("./assets/lazy-oldhash123.js")
            member.size = len(old_chunk)
            archive.addfile(member, io.BytesIO(old_chunk))
        stream.seek(0)
        # The old image has no native graph manifest or publisher at all.
        self.assertEqual(1, seed_legacy_vite_assets(stream, output))
        publish_static_assets(self.root, output)
        self.assertEqual(old_chunk, (output / "dist/assets/lazy-oldhash123.js").read_bytes())
        self.assertTrue((output / "dist/assets/shell-abcdefgh.js").is_file())
        self.assertTrue((output / self.build()["entries"]["js/ui.js"]).is_file())

    def test_legacy_seed_rejects_archive_escape_and_links(self):
        for name, kind in [("../escape-12345678.js", tarfile.REGTYPE), ("link-12345678.js", tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "../protected"
                    archive.addfile(member)
                stream.seek(0)
                with self.assertRaises(ValueError):
                    seed_legacy_vite_assets(stream, Path(self.temporary.name) / "rejected")
        self.assertFalse((Path(self.temporary.name) / "rejected").exists())

    def test_legacy_seed_cannot_claim_success_for_an_empty_export(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w"):
            pass
        stream.seek(0)
        with self.assertRaisesRegex(ValueError, "no recognizable"):
            seed_legacy_vite_assets(stream, Path(self.temporary.name) / "empty-export")


if __name__ == "__main__":
    unittest.main()
