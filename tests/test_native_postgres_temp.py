"""Own-cluster shutdown/cleanup boundaries; native commands are simulated."""
import json
import os
from pathlib import Path
import subprocess
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.native_postgres_temp import OwnedPostgresRuntime


class OwnedPostgresRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.calls = []
        self.command_failure = None
        self.native = self.enterContext(patch("tools.native_postgres_temp.subprocess.run", side_effect=self.run_pg))

    def run_pg(self, args, **kwargs):
        self.calls.append((args, kwargs))
        cluster = Path(args[args.index("-D") + 1])
        operation = "initdb" if Path(args[0]).stem == "initdb" else args[-1]
        if operation == "initdb":
            (cluster / "PG_VERSION").write_text("16")
            (cluster / "synthetic-data").write_text("disposable")
        elif operation == "start":
            port = args[args.index("-o") + 1].split()[-1]
            (cluster / "postmaster.pid").write_text(f"12345\n{cluster}\n0\n{port}\n\n127.0.0.1\n")
            (cluster.parent / "postgres.log").write_text("synthetic server started\n")
        if operation == self.command_failure:
            if operation == "start":
                raise subprocess.TimeoutExpired(args, 45)
            return subprocess.CompletedProcess(args, 1)
        if operation == "stop":
            (cluster / "postmaster.pid").unlink()
        status = 0 if (cluster / "postmaster.pid").exists() else 3
        return subprocess.CompletedProcess(args, status if operation == "status" else 0)

    def create(self, **kwargs):
        return OwnedPostgresRuntime(self.root, pg_ctl=Path("pg_ctl.exe"), **kwargs)

    def test_success_removes_only_created_data_and_keeps_evidence_backup_and_other_runs(self):
        older = self.root / "older-cluster"
        older.mkdir()
        sentinel = older / "business-sentinel"
        sentinel.write_text("must remain")
        with self.create() as owned:
            owned.start()
            (owned.target / "source.dump").write_bytes(b"PGDMP retained")
            (owned.target / "native-report.json").write_text('{"passed":true}')
            (owned.runtime / "classroom.db").write_bytes(b"synthetic")
        self.assertFalse(owned.cluster.exists())
        self.assertFalse(owned.runtime.exists())
        self.assertEqual("must remain", sentinel.read_text())
        self.assertEqual(b"PGDMP retained", (owned.target / "source.dump").read_bytes())
        self.assertTrue((owned.target / "native-report.json").is_file())
        self.assertTrue((owned.target / "postgres.log").is_file())
        self.assertTrue((owned.target / "cluster-commands.log").is_file())
        self.assertEqual("removed", json.loads((owned.target / "cleanup.json").read_text())["status"])
        self.assertEqual(["start", "status", "stop", "status"], [args[-1] for args, _ in self.calls[1:]])
        count = len(self.calls)
        self.assertEqual("removed", owned.cleanup()["status"])
        self.assertEqual(count, len(self.calls))

    def test_error_and_keyboard_interrupt_stop_and_remove_own_data_without_masking_original(self):
        for error in (ValueError("test failed"), KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(error=type(error).__name__):
                owned = self.create()
                with self.assertRaises(type(error)):
                    with owned:
                        owned.start()
                        raise error
                self.assertEqual("removed", owned.cleanup_result["status"])
                self.assertTrue(owned.cleanup_result["cluster_stopped"])

    def test_start_timeout_still_stops_the_server_that_started(self):
        self.command_failure = "start"
        owned = self.create()
        with self.assertRaises(subprocess.TimeoutExpired):
            with owned:
                owned.start()
        self.assertEqual("removed", owned.cleanup_result["status"])
        self.assertIn("stop", [args[-1] for args, _ in self.calls])

    def test_failed_initialization_cleans_partial_data_without_touching_a_server(self):
        self.command_failure = "initdb"
        owned = self.create()
        with self.assertRaises(subprocess.CalledProcessError):
            with owned:
                owned.start()
        self.assertEqual("removed", owned.cleanup_result["status"])
        self.assertEqual(1, len(self.calls))

    def test_stop_or_status_failure_is_reported_and_preserves_both_trees(self):
        for operation in ("stop", "status"):
            with self.subTest(operation=operation):
                owned = self.create()
                self.command_failure = operation
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    with owned:
                        owned.start()
                self.assertTrue(owned.cluster.is_dir())
                self.assertTrue(owned.runtime.is_dir())
                report = json.loads((owned.target / "cleanup.json").read_text())
                self.assertEqual("failed", report["status"])
                self.assertFalse(report["cluster_stopped"])
                self.assertTrue(report["errors"])

    def test_keep_runtime_stops_the_server_but_retains_data(self):
        with self.create(keep_runtime=True) as owned:
            owned.start()
        self.assertEqual("retained", owned.cleanup_result["status"])
        self.assertTrue(owned.cleanup_result["cluster_stopped"])
        self.assertTrue(owned.cluster.is_dir())
        self.assertTrue(owned.runtime.is_dir())
        self.assertFalse((owned.cluster / "postmaster.pid").exists())

    def test_mismatched_owner_or_replaced_directory_prevents_commands_and_deletion(self):
        for change in ("marker", "directory"):
            with self.subTest(change=change):
                owned = self.create()
                if change == "marker":
                    owned._marker.write_text('{"token":"another-run"}')
                else:
                    owned.cluster.rename(owned.target / "previous-cluster")
                    owned.cluster.mkdir()
                before = len(self.calls)
                self.assertEqual("failed", owned.cleanup()["status"])
                self.assertEqual(before, len(self.calls))
                self.assertTrue(owned.cluster.is_dir())
                self.assertTrue(owned.runtime.is_dir())

    def test_pid_pointing_elsewhere_never_stops_or_deletes_that_process(self):
        owned = self.create()
        owned.start()
        pid = owned.cluster / "postmaster.pid"
        pid.write_text(pid.read_text().replace(str(owned.cluster), str(self.root / "foreign")))
        self.assertEqual("failed", owned.cleanup()["status"])
        self.assertNotIn("stop", [args[-1] for args, _ in self.calls])
        self.assertTrue(owned.cluster.exists())

    def test_nested_link_detection_preserves_both_trees_before_any_delete(self):
        owned = self.create()
        suspect = owned.runtime / "external-link"
        suspect.write_text("link sentinel")
        from tools.native_postgres_temp import _is_link
        with patch("tools.native_postgres_temp._is_link", side_effect=lambda path: path == suspect or _is_link(path)):
            result = owned.cleanup()
        self.assertEqual("failed", result["status"])
        self.assertTrue(owned.cluster.is_dir())
        self.assertTrue(owned.runtime.is_dir())

    def test_locked_runtime_is_reported_instead_of_ignored(self):
        owned = self.create()
        with patch("tools.native_postgres_temp.shutil.rmtree", side_effect=PermissionError("locked")):
            self.assertEqual("failed", owned.cleanup()["status"])
        self.assertTrue(owned.cluster.is_dir())
        self.assertIn("locked", (owned.target / "cleanup.json").read_text())

    def test_native_commands_ignore_inherited_pg_settings_without_replacing_temp_settings(self):
        with patch.dict(os.environ, {"PGHOST": "unrelated.invalid", "PGDATA": "unrelated", "TMP": "caller-temp"}):
            with self.create() as owned:
                owned.start()
        for _, options in self.calls:
            self.assertNotIn("PGHOST", options["env"])
            self.assertNotIn("PGDATA", options["env"])
            self.assertEqual("caller-temp", options["env"]["TMP"])

    def test_prefix_cannot_escape_artifact_root(self):
        with self.assertRaises(ValueError):
            self.create(prefix="../foreign")
        self.assertEqual([], list(self.root.iterdir()))

    def test_port_or_marker_creation_failure_leaves_no_owned_directory(self):
        with patch("tools.native_postgres_temp.socket.socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError("no sockets")
            with self.assertRaisesRegex(OSError, "no sockets"):
                self.create()
        self.assertEqual([], list(self.root.iterdir()))
        with patch("tools.native_postgres_temp.json.dump", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.create()
        self.assertEqual([], list(self.root.iterdir()))

    def test_reparse_tag_rejects_junction_on_python_311_but_allows_cloud_placeholders(self):
        from tools.native_postgres_temp import _is_link
        for tag, expected in ((0xA0000003, True), (0xA000000C, True), (0x9000001A, False)):
            with self.subTest(tag=tag), patch.object(Path, "lstat", return_value=SimpleNamespace(
                    st_mode=stat.S_IFDIR, st_reparse_tag=tag)):
                self.assertEqual(expected, _is_link(self.root))

    def test_existing_report_or_test_log_is_never_overwritten(self):
        owned = self.create()
        for name in ("cleanup.json", "result.json", "tests.log"):
            (owned.target / name).write_text("unrelated evidence")
        for name in ("cleanup.json", "result.json"):
            with self.assertRaises(FileExistsError):
                owned.write_evidence_json(name, {"replacement": True})
        with self.assertRaises(FileExistsError):
            owned.open_test_log()
        self.assertEqual("failed", owned.cleanup()["status"])
        for name in ("cleanup.json", "result.json", "tests.log"):
            self.assertEqual("unrelated evidence", (owned.target / name).read_text())

    def test_replaced_evidence_directory_prevents_any_report_or_test_log_write(self):
        owned = self.create()
        # Windows otherwise prevents renaming the open owned command log. The
        # report is written after cleanup closes it, so exercise that boundary.
        owned._command_log.close()
        owned.target.rename(self.root / "moved-owned-evidence")
        owned.target.mkdir()
        with self.assertRaises(ValueError):
            owned.write_evidence_json("result.json", {})
        with self.assertRaises(ValueError):
            owned.open_test_log()
        self.assertEqual("failed", owned.cleanup()["status"])
        self.assertEqual([], list(owned.target.iterdir()))

    def test_replaced_postgres_log_prevents_start(self):
        owned = self.create()
        log = owned.target / "postgres.log"
        log.rename(owned.target / "original-postgres.log")
        log.write_text("unrelated log")
        with self.assertRaisesRegex(ValueError, "log identity"):
            owned.start()
        self.assertEqual([], self.calls)
        self.assertEqual("removed", owned.cleanup()["status"])
        self.assertEqual("unrelated log", log.read_text())

    def test_command_output_uses_owned_handle_even_if_log_path_is_replaced(self):
        owned = self.create()
        log = owned.target / "cluster-commands.log"
        original = owned.target / "original-commands.log"
        try:
            log.rename(original)
        except PermissionError:
            # Windows denies replacement while the exclusive owned handle is
            # live; this is also an effective protection of the output path.
            self.assertEqual("removed", owned.cleanup()["status"])
            self.assertTrue(log.is_file())
            self.assertFalse(original.exists())
            return
        log.write_bytes(b"unrelated log")
        def output(args, **options):
            options["stdout"].write(b"owned command evidence")
            options["stdout"].flush()
            return subprocess.CompletedProcess(args, 3)
        self.native.side_effect = output
        owned._invoke(owned.pg_ctl, ["-D", owned.cluster, "status"])
        self.assertEqual("removed", owned.cleanup()["status"])
        self.assertEqual(b"unrelated log", log.read_bytes())
        self.assertEqual(b"owned command evidence", original.read_bytes())


if __name__ == "__main__":
    unittest.main()
