"""Exercise destructive CLI boundaries using only fresh synthetic directories."""
from contextlib import ExitStack, closing
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import (
    db_attachment_restore_plan, db_backup_rollback, db_inventory,
    db_performance_acceptance, db_pg_lab, db_postgres_export,
    db_remediation_plan,
)
from tools.deploy import migration_dry_run
from tools.temp_path_safety import WORKSPACE_OWNER, owned_temp_workspace


MODULES = (
    db_pg_lab, db_backup_rollback, migration_dry_run, db_inventory,
    db_attachment_restore_plan, db_performance_acceptance,
    db_postgres_export, db_remediation_plan,
)


class TemporaryCliPathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.temp_root = self.root / ".codex-temp"
        self.temp_root.mkdir()
        self.evidence = self.temp_root / "keep-evidence.json"
        self.evidence.write_text('"preserve"', encoding="utf-8")
        self.source = self.root / "source-not-opened.db"
        self.source.write_bytes(b"synthetic sentinel; never open as a database")

    def resolver(self, module):
        return getattr(module, "resolve_lab_root", None) or module.resolve_runtime_root

    def patched_roots(self, module):
        stack = ExitStack()
        stack.enter_context(patch.object(module, "REPO_ROOT", self.root))
        stack.enter_context(patch.object(module, "TEMP_ROOT", self.temp_root))
        default = "DEFAULT_LAB_ROOT" if module is db_pg_lab else "DEFAULT_RUNTIME_ROOT"
        stack.enter_context(patch.object(module, default, self.temp_root / "default-lab"))
        return stack

    def invoke_reset(self, module, target):
        if module is db_pg_lab:
            return module.prepare_lab(target, source_db=self.source, clean=True)
        if module is migration_dry_run:
            return module.copy_runtime_db(target, self.source)
        if module is db_inventory:
            return module.copy_sqlite_database(target, self.source)
        function = {
            db_backup_rollback: "run_backup_rollback_drill",
            db_attachment_restore_plan: "build_attachment_restore_plan",
            db_performance_acceptance: "build_performance_acceptance_report",
            db_postgres_export: "build_postgres_export_package",
            db_remediation_plan: "build_remediation_plan",
        }[module]
        return getattr(module, function)(target, source_db=self.source)

    def assert_rejected_by_all(self, path):
        for module in MODULES:
            with self.subTest(module=module.__name__, path=str(path)), self.patched_roots(module):
                with self.assertRaises(ValueError):
                    self.resolver(module)(path)

    def test_accepts_existing_new_default_and_normalized_child_paths(self):
        existing = self.temp_root / "existing"
        existing.mkdir()
        for module in MODULES:
            with self.subTest(module=module.__name__), self.patched_roots(module):
                resolve = self.resolver(module)
                self.assertEqual(resolve(None), self.temp_root / "default-lab")
                self.assertEqual(resolve(existing), existing)
                self.assertEqual(resolve(".codex-temp/new/nested"), self.temp_root / "new/nested")
                self.assertEqual(resolve(existing / ".." / "other"), self.temp_root / "other")

    def test_rejects_whole_temp_root_and_parent_traversal_before_any_reset(self):
        nested = self.temp_root / "lab"
        nested.mkdir()
        for module in MODULES:
            with self.subTest(module=module.__name__), self.patched_roots(module):
                for target in (self.temp_root, Path(".codex-temp"), nested / ".."):
                    with patch.object(module.shutil, "rmtree") as remove, \
                            patch.object(module.sqlite3, "connect") as connect:
                        with self.assertRaises(ValueError):
                            self.invoke_reset(module, target)
                        remove.assert_not_called()
                        connect.assert_not_called()
        self.assertEqual(self.evidence.read_text(encoding="utf-8"), '"preserve"')
        self.assertEqual(self.source.read_bytes(), b"synthetic sentinel; never open as a database")

    def test_rejects_repository_data_and_similarly_named_siblings(self):
        for target in (self.root, self.root / "data", self.root / ".codex-temp-sibling/lab",
                       self.temp_root / ".." / "data"):
            self.assert_rejected_by_all(target)

    def test_all_writers_preserve_unowned_history_and_reserved_evidence(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), self.patched_roots(module):
                for name in ("historical-run", "evidence/new-run", "scratch/new-run"):
                    target = self.temp_root / name
                    target.mkdir(parents=True, exist_ok=True)
                    sentinel = target / "retained.dump"
                    sentinel.write_bytes(b"historical evidence")
                    with patch.object(module.shutil, "rmtree") as remove, \
                            patch.object(module.sqlite3, "connect") as connect:
                        with self.assertRaises(ValueError):
                            self.invoke_reset(module, target)
                        remove.assert_not_called()
                        connect.assert_not_called()
                    self.assertEqual(b"historical evidence", sentinel.read_bytes())

    def workspace_function(self, *, tool="synthetic-tool", body=None):
        def resolve(path):
            from tools.temp_path_safety import resolve_temp_child
            return resolve_temp_child(path, repo_root=self.root, temp_root=self.temp_root,
                                      default=self.temp_root / "owned")

        @owned_temp_workspace(resolve, temp_root=lambda: self.temp_root, tool=tool)
        def run(path, *, source_db=None):
            if body:
                return body(path)
            (path / "generated.txt").write_text("rebuilt", encoding="utf-8")
            return path
        return run

    def test_owned_repeated_run_rebuilds_only_the_same_tools_workspace(self):
        run = self.workspace_function()
        target = self.temp_root / "owned"
        run(target)
        (target / "obsolete.txt").write_text("old disposable output")
        run(target)
        self.assertFalse((target / "obsolete.txt").exists())
        self.assertTrue((target / WORKSPACE_OWNER).is_file())
        self.assertEqual('"preserve"', self.evidence.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "another tool"):
            self.workspace_function(tool="different-tool")(target)
        self.assertEqual("rebuilt", (target / "generated.txt").read_text())

    def test_existing_empty_directory_can_be_claimed_without_deletion(self):
        target = self.temp_root / "empty"
        target.mkdir()
        with patch("tools.temp_path_safety.shutil.rmtree") as remove:
            self.workspace_function()(target)
        remove.assert_not_called()
        self.assertTrue((target / WORKSPACE_OWNER).is_file())

    def test_concurrent_call_cannot_reset_a_running_workspace(self):
        started, finish = threading.Event(), threading.Event()
        errors = []
        target = self.temp_root / "concurrent"
        def body(path):
            (path / "live.txt").write_text("running")
            started.set()
            if not finish.wait(5):
                raise RuntimeError("synthetic worker did not finish")
        run = self.workspace_function(body=body)
        def worker():
            try:
                run(target)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaisesRegex(ValueError, "locked"):
                run(target)
            self.assertEqual("running", (target / "live.txt").read_text())
        finally:
            finish.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([], list(target.parent.glob(".lanshare-workspace-*.lock")))

    def test_nested_owned_workspaces_and_inputs_survive_a_reset_attempt(self):
        run = self.workspace_function()
        target = self.temp_root / "parent"
        run(target)
        with self.assertRaisesRegex(ValueError, "inside another owned run"):
            run(target / "child")
        source = target / "source.db"
        source.write_bytes(b"input must survive")
        with self.assertRaisesRegex(ValueError, "must stay outside"):
            run(target, source_db=source)
        self.assertEqual(b"input must survive", source.read_bytes())
        child = target / "independent-run"
        child.mkdir()
        (child / WORKSPACE_OWNER).write_text("another tool's owner")
        with self.assertRaisesRegex(ValueError, "independently owned"):
            run(target)
        self.assertTrue(source.exists())

    def test_parent_and_child_operations_cannot_adopt_each_others_live_tree(self):
        parent = self.temp_root / "parent-child"
        child = parent / "child"
        def live_child(path):
            (path / "live.txt").write_text("running child")
            with self.assertRaises(ValueError):
                self.workspace_function()(parent)
            self.assertEqual("running child", (path / "live.txt").read_text())
        self.workspace_function(body=live_child)(child)
        self.assertFalse((parent / WORKSPACE_OWNER).exists())

        other_parent = self.temp_root / "reset-gap"
        def parent_during_reset(path):
            # Model the interval after reset removes the marker, while the
            # exact parent lock still protects its operation.
            marker = path / WORKSPACE_OWNER
            previous = marker.read_bytes()
            marker.unlink()
            try:
                with self.assertRaisesRegex(ValueError, "inside another owned run"):
                    self.workspace_function()(path / "child")
            finally:
                marker.write_bytes(previous)
        self.workspace_function(body=parent_during_reset)(other_parent)

    def test_replaced_owned_directory_cannot_be_adopted_by_copying_its_marker(self):
        run = self.workspace_function()
        target = self.temp_root / "owned"
        run(target)
        previous = self.temp_root / "previous"
        target.rename(previous)
        target.mkdir()
        shutil.copyfile(previous / WORKSPACE_OWNER, target / WORKSPACE_OWNER)
        sentinel = target / "retained.txt"
        sentinel.write_text("foreign directory")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            run(target)
        self.assertEqual("foreign directory", sentinel.read_text())

    def test_existing_copy_and_lab_entrypoints_allow_verified_repeated_runs(self):
        source = self.root / "synthetic-source.db"
        with closing(sqlite3.connect(source)) as connection:
            connection.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO teachers(name) VALUES ('synthetic')")
            connection.commit()
        source_bytes = source.read_bytes()
        for module in (db_backup_rollback, migration_dry_run, db_inventory, db_pg_lab):
            with self.subTest(module=module.__name__), self.patched_roots(module):
                target = self.temp_root / module.__name__.rsplit(".", 1)[-1]
                if module is db_backup_rollback:
                    invoke = lambda: module.run_backup_rollback_drill(target, source_db=source)
                elif module is migration_dry_run:
                    invoke = lambda: module.copy_runtime_db(target, source)
                elif module is db_inventory:
                    invoke = lambda: module.copy_sqlite_database(target, source)
                else:
                    invoke = lambda: module.prepare_lab(target, source_db=source, clean=True)
                invoke()
                obsolete = target / "obsolete.txt"
                obsolete.write_text("disposable output from the same tool")
                if module is db_pg_lab:
                    module.prepare_lab(target, source_db=source, clean=False)
                    self.assertTrue(obsolete.exists())
                invoke()
                self.assertFalse(obsolete.exists())
                self.assertTrue((target / WORKSPACE_OWNER).is_file())
                self.assertEqual(source_bytes, source.read_bytes())

    def make_symlink(self, target, link):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Creating symlinks is unavailable: {error}")
        self.addCleanup(link.unlink)

    def test_rejects_symlink_aliases_even_when_target_is_inside_temp_root(self):
        for name, target in (("inside-link", self.temp_root / "real"),
                             ("outside-link", self.root / "external")):
            target.mkdir()
            link = self.temp_root / name
            self.make_symlink(target, link)
            self.assert_rejected_by_all(link)
            self.assert_rejected_by_all(link / "nested")
            self.assert_rejected_by_all(link / ".." / "other")

    def test_rejects_dangling_symlinks_and_redirected_temp_root(self):
        link = self.temp_root / "dangling"
        self.make_symlink(self.root / "does-not-exist", link)
        self.assert_rejected_by_all(link / "new")
        self.temp_root = self.root / "redirected-temp"
        self.make_symlink(self.root / ".codex-temp", self.temp_root)
        self.assert_rejected_by_all(self.temp_root / "new")

    @unittest.skipUnless(os.name == "nt", "Windows junctions only")
    def test_rejects_real_windows_junctions_without_path_is_junction(self):
        import _winapi
        for name, target in (("inside-junction", self.temp_root / "real"),
                             ("outside-junction", self.root / "external")):
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            link = self.temp_root / name
            _winapi.CreateJunction(str(target), str(link))
            self.addCleanup(link.rmdir)
            # The guard must support Python 3.11, which lacks Path.is_junction.
            with patch.object(Path, "is_junction", None, create=True):
                self.assert_rejected_by_all(link)
                self.assert_rejected_by_all(link / "new")
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_non_redirecting_cloud_reparse_tag_does_not_block_valid_paths(self):
        original = Path.lstat

        def cloud_lstat(path, *args, **kwargs):
            if path == self.temp_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0x9000001A)
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", cloud_lstat):
            for module in MODULES:
                with self.subTest(module=module.__name__), self.patched_roots(module):
                    self.assertEqual(self.resolver(module)(None), self.temp_root / "default-lab")

    def test_symlink_metadata_is_rejected_before_resolving_parent_traversal(self):
        alias = self.temp_root / "alias"
        alias.mkdir()
        original = Path.lstat
        for metadata in (SimpleNamespace(st_mode=stat.S_IFLNK),
                         SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA000000C)):
            def alias_lstat(path, *args, **kwargs):
                return metadata if path == alias else original(path, *args, **kwargs)

            with patch.object(Path, "lstat", alias_lstat):
                self.assert_rejected_by_all(alias / "new")
                self.assert_rejected_by_all(alias / ".." / "other")

    @unittest.skipUnless(os.name == "nt", "Windows junctions only")
    def test_rejects_a_real_junction_used_as_the_temp_root(self):
        import _winapi
        actual_root = self.temp_root
        self.temp_root = self.root / "temp-root-junction"
        _winapi.CreateJunction(str(actual_root), str(self.temp_root))
        self.addCleanup(self.temp_root.rmdir)
        self.assert_rejected_by_all(self.temp_root / "new")
        self.assertTrue(self.evidence.is_file())

    def test_direct_script_entrypoints_can_import_shared_guard(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                result = subprocess.run([sys.executable, module.__file__, "--help"], cwd=self.root,
                                        text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_capacity_export_contains_the_export_cli_import_dependencies(self):
        from tools.platform_capacity_linux_lab import REQUIRED_EXPORT
        repository = Path(__file__).resolve().parents[1]
        frozen = self.root / "frozen-export"
        for name in REQUIRED_EXPORT:
            if name.startswith("tools/") and name.endswith(".py"):
                destination = frozen / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / name, destination)
        # Isolated Python prevents the current checkout/PYTHONPATH from hiding
        # an import dependency missing from the frozen source allowlist.
        result = subprocess.run([sys.executable, "-I", str(frozen / "tools/db_postgres_export.py"), "--help"],
                                cwd=self.root, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
