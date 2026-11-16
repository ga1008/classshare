"""Probe exit/report contract, with no database connections or native commands."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from tools import wanderer_native_pg_probe as probe
from tools.native_postgres_temp import OwnedPostgresRuntime


class WandererNativeProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(probe, "ROOT", Path(self.temp)))
        self.enterContext(patch("tools.isolated_environment.isolate_sqlite_environment"))
        self.enterContext(patch("tools.isolated_environment.guard_dotenv_loading"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.start = self.enterContext(patch.object(OwnedPostgresRuntime, "start"))
        self.run_tests = self.enterContext(patch.object(probe, "_run_tests"))
        self.run_tests.return_value = {"status": "passed", "tests": 3, "errors": 0, "failures": 0, "skipped": 0}

    def report(self):
        return json.loads(next(Path(self.temp).glob(".codex-temp/*/result.json")).read_text())

    def test_default_success_removes_runtime_and_records_cleanup(self):
        self.assertEqual(0, probe.main([]))
        result = self.report()
        self.assertEqual("passed", result["status"])
        self.assertEqual("removed", result["cleanup"]["status"])
        self.assertTrue(result["cluster_stopped"])
        self.assertFalse(Path(result["cluster"]).exists())

    def test_failed_suite_still_cleans_and_returns_failure(self):
        self.run_tests.return_value["status"] = "failed"
        self.run_tests.return_value["failures"] = 1
        self.assertEqual(1, probe.main([]))
        self.assertEqual("failed", self.report()["status"])
        self.assertEqual("removed", self.report()["cleanup"]["status"])

    def test_start_error_and_interrupt_always_write_result_and_cleanup(self):
        for error in (RuntimeError("start failure"), KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(error=type(error).__name__):
                with tempfile.TemporaryDirectory() as other, patch.object(probe, "ROOT", Path(other)):
                    self.start.side_effect = error
                    with self.assertRaises(type(error)):
                        probe.main([])
                    result = json.loads(next(Path(other).glob(".codex-temp/*/result.json")).read_text())
                    self.assertEqual("error" if isinstance(error, RuntimeError) else "interrupted", result["status"])
                    self.assertEqual("removed", result["cleanup"]["status"])

    def test_cleanup_failure_cannot_be_reported_as_a_passing_probe(self):
        with patch("tools.native_postgres_temp.shutil.rmtree", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                probe.main([])
        result = self.report()
        self.assertEqual("error", result["status"])
        self.assertEqual("failed", result["cleanup"]["status"])
        self.assertTrue(Path(result["cluster"]).is_dir())

    def test_keep_runtime_flag_is_explicit_and_recorded(self):
        self.assertEqual(0, probe.main(["--keep-runtime"]))
        result = self.report()
        self.assertEqual("retained", result["cleanup"]["status"])
        self.assertTrue(Path(result["cluster"]).is_dir())

    def test_sigterm_handler_unwinds_and_restores_original_handler(self):
        original = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(SystemExit) as raised:
            with probe._termination_as_exception():
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual(128 + signal.SIGTERM, raised.exception.code)
        self.assertEqual(original, signal.getsignal(signal.SIGTERM))

    def test_final_report_does_not_write_to_replaced_evidence_directory(self):
        changed = []
        def replace_target(owned):
            owned._command_log.close()
            owned.target.rename(Path(self.temp) / "moved-owned-evidence")
            owned.target.mkdir()
            sentinel = owned.target / "result.json"
            sentinel.write_text("unrelated result")
            changed.append(sentinel)
            return {"status": "passed"}
        self.run_tests.side_effect = replace_target
        with self.assertRaises(ValueError):
            probe.main([])
        self.assertEqual("unrelated result", changed[0].read_text())


class WandererNativeProbeSuiteTests(unittest.TestCase):
    def test_probe_runs_all_existing_suites_and_persona_lock_regression_in_its_owned_cluster(self):
        with tempfile.TemporaryDirectory() as folder, OwnedPostgresRuntime(Path(folder)) as owned:
            connection = MagicMock()
            connection.execute.return_value.fetchone.return_value = (str(owned.cluster), "127.0.0.1", str(owned.port))
            with patch("psycopg.connect") as connect, patch.dict(os.environ, {}), \
                    patch.object(unittest.defaultTestLoader, "loadTestsFromName") as load:
                connect.return_value.__enter__.return_value = connection
                load.side_effect = lambda _module: unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
                result = probe._run_tests(owned)
            expected = (
                "tests.test_wanderer_world_postgres", "tests.test_wanderer_economy_native_postgres",
                "tests.test_agent_authority_postgres", "tests.test_wanderer_persona_refresh_postgres",
            )
            self.assertEqual(load.call_args_list, [call(module) for module in expected])
            self.assertEqual(result["modules"], list(expected))
            self.assertEqual(result["tests"], 4)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
