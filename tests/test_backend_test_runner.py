import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from tools.isolated_environment import guard_dotenv_loading
from tools.test_backend import isolate_environment, reject_postgres


WORKER_PARENT_CODE = r'''
import subprocess, sys
from unittest.mock import patch
from tools import test_backend
worker_code = sys.argv.pop(1)
real_popen = subprocess.Popen
def launch_worker(command, **kwargs):
    if len(command) > 1 and str(command[1]).endswith('test_backend.py'):
        return real_popen([command[0], "-c", worker_code, *command[2:]], **kwargs)
    return real_popen(command, **kwargs)
with patch.object(test_backend.subprocess, "Popen", launch_worker):
    raise SystemExit(test_backend._cli_main())
'''


def isolation_status(stdout):
    return next(json.loads(line) for line in stdout.splitlines()
                if line.startswith('{"unit_test_isolation":'))


def evidence_directory():
    from tools import test_backend
    test_backend.REPORT_PARENT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="runner-proof-", dir=test_backend.REPORT_PARENT)


class BackendTestIsolationTests(unittest.TestCase):
    def test_removes_inherited_database_and_native_probe_opt_ins(self):
        inherited = {"DATABASE_URL": "postgresql://invalid.invalid/do-not-connect", "PGSERVICE": "real",
                     "MAIN_DB_PATH": "real.db", "RUN_LOCAL_PG_CAREER_PROBE": "1",
                     "ASSESSMENT_REHEARSAL_TEST_CLUSTER": "real-cluster",
                     "LANSHARE_WHITEBOARD_TEST_DATABASE_URL": "postgresql://invalid.invalid/real",
                     "MP_PHASE1_POSTGRES_TEACHER_DSN": "postgresql://invalid.invalid/teacher",
                     "MP_PHASE1_STUDENT_TEST_DSN": "postgresql://invalid.invalid/student"}
        with patch.dict(os.environ, inherited, clear=True):
            isolate_environment(Path("isolated"))
            self.assertEqual(os.environ["DB_ENGINE"], "sqlite")
            self.assertEqual(os.environ["DATABASE_URL"], "")
            self.assertEqual(os.environ["PYTHON_DOTENV_DISABLED"], "1")
            self.assertEqual(os.environ["MAIN_DB_PATH"], str(Path("isolated/db/classroom.db")))
            for key in inherited.keys() - {"MAIN_DB_PATH", "DATABASE_URL"}:
                self.assertNotIn(key, os.environ)
        with self.assertRaisesRegex(RuntimeError, "forbids real PostgreSQL"):
            reject_postgres("credentials must never be interpolated into the error")

    def test_entrypoint_overrides_hostile_inherited_configuration_before_import(self):
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, DB_ENGINE="postgres", DATABASE_URL="postgresql://invalid.invalid/do-not-connect",
                   MAIN_DB_PATH=str(repo / "data/db/classroom.db"), PYTHON_DOTENV_DISABLED="0")
        result = subprocess.run([sys.executable, str(repo / "tools/test_backend.py"), "--check-only"],
                                cwd=repo, env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = isolation_status(result.stdout)
        self.assertTrue(status["unit_test_isolation"])
        self.assertEqual(status["engine"], "sqlite")
        self.assertEqual(status["dotenv_loaders"], "blocked_before_app_import")
        self.assertFalse(Path(status["runtime"]).is_relative_to(repo / "data"))
        self.assertEqual(status["postgres_connections"], "forbidden")
        self.assertFalse(Path(status["runtime"]).exists(), "The CLI parent must remove its finished worker runtime")

    def test_worker_exit_cleans_leaked_handles_and_nested_temps_but_keeps_evidence(self):
        """Exercise actual child exit, including Windows-open DB/file handles."""
        repo = Path(__file__).resolve().parents[1]
        worker_code = r'''
import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from tools import test_backend
held = []
class LeakingFixture(unittest.TestCase):
    def runTest(self):
        directory = Path(tempfile.mkdtemp(prefix="nested-leak-"))
        held.append((directory / "open.txt").open("w"))
        held.append(sqlite3.connect(directory / "open.db"))
        nested = subprocess.check_output([sys.executable, "-c",
            "import tempfile; print(tempfile.mkdtemp(prefix='child-leak-'))"], text=True).strip()
        print(json.dumps({"fixture_temps": [str(directory), nested]}), flush=True)
        if os.environ.get("FIXTURE_FAIL") == "1":
            self.fail("intentional failure with open disposable handles")
with patch.object(unittest.defaultTestLoader, "discover", return_value=unittest.TestSuite([LeakingFixture()])):
    raise SystemExit(test_backend.main())
'''
        with evidence_directory() as folder:
            evidence = Path(folder)
            sentinel = evidence / "deployment-backup.dump"
            sentinel.write_bytes(b"must survive unit cleanup")
            for should_fail in (False, True):
                with self.subTest(failure=should_fail):
                    report = evidence / f"result-{should_fail}.json"
                    env = dict(os.environ, FIXTURE_FAIL="1" if should_fail else "0")
                    completed = subprocess.run(
                        [sys.executable, "-c", WORKER_PARENT_CODE, worker_code, "--report", str(report)],
                        cwd=repo, env=env, text=True, capture_output=True, timeout=30,
                    )
                    self.assertEqual(completed.returncode, int(should_fail), completed.stderr)
                    runtime = Path(isolation_status(completed.stdout)["runtime"])
                    temps = next(json.loads(line)["fixture_temps"] for line in completed.stdout.splitlines()
                                 if line.startswith('{"fixture_temps":'))
                    self.assertTrue(all(Path(path).is_relative_to(runtime / "tmp") for path in temps))
                    self.assertFalse(runtime.exists())
                    summary = json.loads(report.read_text(encoding="utf-8"))
                    self.assertEqual(summary["tests"], 1)
                    self.assertEqual(summary["status"], "failed" if should_fail else "passed")
                    self.assertEqual(len(summary["failures"]), int(should_fail))
                    self.assertEqual(len(summary["run_id"]), 32)
                    self.assertEqual(summary["cleanup"], "removed")
            self.assertEqual(sentinel.read_bytes(), b"must survive unit cleanup")

    def test_worker_refuses_to_put_evidence_inside_its_disposable_runtime(self):
        from tools import test_backend
        repo = Path(__file__).resolve().parents[1]
        runtime = test_backend._new_runtime()
        token = (runtime / test_backend.OWNER_FILE).read_text(encoding="ascii")
        self.addCleanup(test_backend._remove_owned_runtime, runtime, token)
        completed = subprocess.run(
            [sys.executable, str(repo / "tools/test_backend.py"), "--_worker-runtime", str(runtime),
             "--_worker-token", token, "--check-only", "--report", str(runtime / "must-retain.json")],
            cwd=repo, text=True, capture_output=True, timeout=20,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("outside the disposable runtime", completed.stderr)
        self.assertTrue(runtime.exists(), "A worker does not own cleanup of a caller-supplied path")

    def test_report_cannot_target_another_runs_scratch(self):
        from tools import test_backend
        runtime = test_backend._new_runtime()
        token = (runtime / test_backend.OWNER_FILE).read_text(encoding="ascii")
        self.addCleanup(test_backend._remove_owned_runtime, runtime, token)
        report = runtime / "older-evidence.json"
        report.write_text('{"status":"passed","run_id":"old"}', encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(test_backend.REPO / "tools/test_backend.py"), "--check-only", "--report", str(report)],
            cwd=test_backend.REPO, text=True, capture_output=True, timeout=20,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("all scratch directories", completed.stderr)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["run_id"], "old")

    def test_initialization_failure_replaces_stale_passed_report(self):
        from tools import test_backend
        worker_code = r'''
import os, unittest
from unittest.mock import patch
from tools import test_backend
error = SystemExit(0) if os.environ.get('FIXTURE_ZERO_EXIT') == '1' else RuntimeError('synthetic discovery failure')
with patch.object(unittest.defaultTestLoader, "discover", side_effect=error):
    raise SystemExit(test_backend.main())
'''
        with evidence_directory() as folder:
            for zero_exit in (False, True):
                with self.subTest(system_exit_zero=zero_exit):
                    report = Path(folder) / "result.json"
                    report.write_text('{"status":"passed","run_id":"old"}', encoding="utf-8")
                    completed = subprocess.run(
                        [sys.executable, "-c", WORKER_PARENT_CODE, worker_code, "--report", str(report)],
                        cwd=test_backend.REPO, env=dict(os.environ, FIXTURE_ZERO_EXIT="1" if zero_exit else "0"),
                        text=True, capture_output=True, timeout=30,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    summary = json.loads(report.read_text(encoding="utf-8"))
                    self.assertEqual(summary["status"], "failed")
                    self.assertNotEqual(summary["run_id"], "old")
                    self.assertIn("SystemExit: 0" if zero_exit else "synthetic discovery failure", summary["error"])
                    self.assertEqual(summary["cleanup"], "removed")
                    self.assertFalse(Path(summary["runtime"]).exists())

    def test_report_replace_failure_after_allocation_still_cleans_owned_runtime(self):
        from tools import test_backend

        evidence = Path(self.enterContext(evidence_directory()))
        runtime_parent = evidence / "owned-runtimes"
        older = runtime_parent / "unit-other-run"
        older.mkdir(parents=True)
        sentinel = older / "must-retain.dump"
        sentinel.write_bytes(b"unrelated retained evidence")
        report = evidence / "result.json"
        allocated = []
        real_new_runtime = test_backend._new_runtime
        real_replace = Path.replace

        def allocate(run_id):
            runtime = real_new_runtime(run_id)
            allocated.append(runtime)
            return runtime

        def fail_after_allocation(path, target):
            if allocated and Path(target) == report:
                raise PermissionError("synthetic report replace denied")
            return real_replace(path, target)

        with patch.object(test_backend, "RUNTIME_PARENT", runtime_parent), \
                patch.object(test_backend, "_new_runtime", side_effect=allocate), \
                patch.object(test_backend, "_run_tests") as run_tests, \
                patch.object(Path, "replace", fail_after_allocation), \
                patch.object(sys, "argv", ["test_backend.py", "--check-only", "--report", str(report)]):
            with self.assertRaisesRegex(PermissionError, "synthetic report replace denied"):
                test_backend.main()
        run_tests.assert_not_called()
        self.assertEqual(len(allocated), 1)
        self.assertFalse(allocated[0].exists())
        self.assertEqual(sentinel.read_bytes(), b"unrelated retained evidence")
        self.assertEqual(list(evidence.glob(".result.json.*.tmp")), [])
        self.assertNotEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "passed")

    def test_worker_report_replace_failure_exits_before_parent_cleanup(self):
        from tools import test_backend

        worker_code = r'''
from pathlib import Path
from unittest.mock import patch
from tools import test_backend
with patch.object(Path, "replace", side_effect=PermissionError("synthetic worker report replace denied")):
    raise SystemExit(test_backend.main())
'''
        evidence = Path(self.enterContext(evidence_directory()))
        report = evidence / "result.json"
        report.write_text('{"status":"passed","run_id":"old"}', encoding="utf-8")
        sentinel = evidence / "deployment-backup.dump"
        sentinel.write_bytes(b"unrelated retained evidence")
        completed = subprocess.run(
            [sys.executable, "-c", WORKER_PARENT_CODE, worker_code, "--check-only", "--report", str(report)],
            cwd=test_backend.REPO, text=True, capture_output=True, timeout=30,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("synthetic worker report replace denied", completed.stderr)
        summary = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "failed")
        self.assertNotEqual(summary["run_id"], "old")
        self.assertNotEqual(summary["worker_exit_code"], 0)
        self.assertTrue(summary["worker_exit_confirmed"])
        self.assertEqual(summary["cleanup"], "removed")
        self.assertFalse(Path(summary["runtime"]).exists())
        self.assertEqual(sentinel.read_bytes(), b"unrelated retained evidence")
        self.assertEqual(list(evidence.glob(".result.json.*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Exercise actual Windows junction and process-tree semantics")
    def test_allocation_refuses_junction_before_creating_any_workspace(self):
        from tools import test_backend
        with evidence_directory() as folder:
            base = Path(folder)
            target = base / "retained"
            target.mkdir()
            sentinel = target / "backup.dump"
            sentinel.write_bytes(b"must survive")
            link = base / "redirect"
            created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
            for entrypoint in (test_backend.main, test_backend._cli_main):
                with self.subTest(entrypoint=entrypoint.__name__):
                    with patch.object(test_backend, "RUNTIME_PARENT", link / "backend-tests"), \
                            patch.object(sys, "argv", ["test_backend.py", "--check-only", "--report", str(base / "failure.json")]):
                        if entrypoint is test_backend.main:
                            with self.assertRaisesRegex(ValueError, "symlink or junction"):
                                entrypoint()
                        else:
                            self.assertEqual(entrypoint(), 1)
                    self.assertEqual(list(target.iterdir()), [sentinel])
                    self.assertEqual(json.loads((base / "failure.json").read_text(encoding="utf-8"))["status"], "failed")

    @unittest.skipUnless(os.name == "nt", "Exercise actual Windows taskkill of the owned worker tree")
    def test_interrupt_stops_real_descendants_before_cleanup_and_records_new_run(self):
        from tools import test_backend
        worker_code = r'''
import os, subprocess, sys, time, unittest
from pathlib import Path
from unittest.mock import patch
from tools import test_backend
child_code = "import json,os,sys,time; f=open(sys.argv[1],'w'); open(sys.argv[2],'w').write(json.dumps({'pid':os.getpid(),'held':sys.argv[1]})); time.sleep(90)"
class WaitingFixture(unittest.TestCase):
    def runTest(self):
        held = Path(os.environ['TMP']) / 'grandchild-open.txt'
        subprocess.Popen([sys.executable, '-c', child_code, str(held), os.environ['INTERRUPT_READY']])
        time.sleep(90)
with patch.object(unittest.defaultTestLoader, 'discover', return_value=unittest.TestSuite([WaitingFixture()])):
    raise SystemExit(test_backend.main())
'''
        parent_code = r'''
import _thread, os, subprocess, sys, threading, time
from pathlib import Path
from unittest.mock import patch
from tools import test_backend
worker_code = sys.argv.pop(1)
real_popen = subprocess.Popen
def launch_worker(command, **kwargs):
    if len(command) > 1 and str(command[1]).endswith('test_backend.py'):
        return real_popen([command[0], '-c', worker_code, *command[2:]], **kwargs)
    return real_popen(command, **kwargs)
def interrupt_when_ready():
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if Path(os.environ['INTERRUPT_READY']).is_file():
            _thread.interrupt_main()
            return
        time.sleep(0.02)
threading.Thread(target=interrupt_when_ready, daemon=True).start()
real_stop = test_backend._stop_worker_tree
def stop_owned_tree(worker):
    stopped = real_stop(worker)
    return stopped and os.environ.get('UNCERTAIN_STOP') != '1'
with patch.object(test_backend.subprocess, 'Popen', launch_worker), \
     patch.object(test_backend, '_stop_worker_tree', stop_owned_tree):
    raise SystemExit(test_backend._cli_main())
'''
        with evidence_directory() as folder:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            for uncertain in (False, True):
                with self.subTest(exit_confirmation=not uncertain):
                    report = Path(folder) / f"result-{uncertain}.json"
                    report.write_text('{"status":"passed","run_id":"old"}', encoding="utf-8")
                    ready = Path(folder) / f"ready-{uncertain}.json"
                    completed = subprocess.run(
                        [sys.executable, "-c", parent_code, worker_code, "--report", str(report)],
                        cwd=test_backend.REPO,
                        env=dict(os.environ, INTERRUPT_READY=str(ready), UNCERTAIN_STOP="1" if uncertain else "0"),
                        text=True, capture_output=True, timeout=40,
                    )
                    self.assertEqual(completed.returncode, 130, completed.stderr)
                    summary = json.loads(report.read_text(encoding="utf-8"))
                    self.assertEqual(summary["status"], "interrupted")
                    self.assertNotEqual(summary["run_id"], "old")
                    self.assertEqual(summary["worker_exit_confirmed"], not uncertain)
                    self.assertEqual(summary["cleanup"], "retained" if uncertain else "removed")
                    runtime = Path(summary["runtime"])
                    self.assertEqual(runtime.exists(), uncertain)
                    child_pid = json.loads(ready.read_text(encoding="utf-8"))["pid"]
                    handle = kernel.OpenProcess(0x00100000, False, child_pid)
                    if handle:
                        try:
                            self.assertNotEqual(kernel.WaitForSingleObject(handle, 0), 258, "Grandchild must have exited")
                        finally:
                            kernel.CloseHandle(handle)
                    if uncertain:
                        # This test independently observed the owned descendants
                        # exit; the runner correctly retained its uncertain run.
                        test_backend._remove_owned_runtime(runtime, summary["run_id"])

    def test_private_worker_entry_refuses_existing_evidence_without_owner(self):
        from tools import test_backend
        with tempfile.TemporaryDirectory(prefix="unit-", dir=test_backend.RUNTIME_PARENT) as folder:
            runtime = Path(folder)
            backup = runtime / "backup.dump"
            backup.write_bytes(b"retained evidence")
            completed = subprocess.run(
                [sys.executable, str(test_backend.REPO / "tools/test_backend.py"), "--_worker-runtime", str(runtime),
                 "--_worker-token", "not-owned", "--check-only"],
                cwd=test_backend.REPO, text=True, capture_output=True, timeout=20,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("owned runtime token", completed.stderr)
            self.assertEqual(backup.read_bytes(), b"retained evidence")
            self.assertFalse((runtime / "db").exists())

    def test_both_dotenv_loaders_are_blocked_but_explicit_values_parser_remains_usable(self):
        import dotenv
        import dotenv.main

        sentinel = "LQ_TEST_RUNNER_FAKE_ENV_SENTINEL"
        with tempfile.TemporaryDirectory() as folder:
            fake_env = Path(folder) / ".env"
            fake_env.write_text(f"{sentinel}=loaded\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), ExitStack() as guards:
                guard_dotenv_loading(guards)
                self.assertFalse(dotenv.load_dotenv(fake_env, override=True))
                self.assertFalse(dotenv.main.load_dotenv(fake_env, override=True))
                self.assertNotIn(sentinel, os.environ)
                self.assertEqual(dotenv.dotenv_values(fake_env)[sentinel], "loaded")
                self.assertNotIn(sentinel, os.environ)

    def test_entrypoint_does_not_load_fake_dotenv_during_real_config_import(self):
        repo = Path(__file__).resolve().parents[1]
        sentinel = "LQ_TEST_RUNNER_FAKE_ENV_SENTINEL"
        with tempfile.TemporaryDirectory() as folder:
            fake_env = Path(folder) / ".env"
            fake_env.write_text(
                f"{sentinel}=loaded\n"
                "MAIN_SHARE_DIR=must-not-be-loaded-from-dotenv\n"
                "MP_PHASE1_POSTGRES_TEACHER_DSN=must-not-enable-native-probe\n",
                encoding="utf-8",
            )
            code = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
fake_env = sys.argv[2]
import dotenv
import dotenv.main
dotenv.find_dotenv = lambda *args, **kwargs: fake_env
dotenv.main.find_dotenv = lambda *args, **kwargs: fake_env
from tools import test_backend
sys.argv = ['test_backend.py', '--check-only']
status = test_backend.main()
from classroom_app import config
assert 'LQ_TEST_RUNNER_FAKE_ENV_SENTINEL' not in os.environ
assert 'MP_PHASE1_POSTGRES_TEACHER_DSN' not in os.environ
assert 'MAIN_SHARE_DIR' not in os.environ
assert Path(config.SHARE_DIR).is_relative_to(Path(config.DATA_DIR))
print(json.dumps({'fake_dotenv_not_loaded': True}))
raise SystemExit(status)
"""
            env = dict(os.environ, PYTHON_DOTENV_DISABLED="0")
            env.pop(sentinel, None)
            result = subprocess.run([sys.executable, "-c", code, str(repo), str(fake_env)],
                                    cwd=repo, env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(isolation_status(result.stdout)["dotenv_loaders"], "blocked_before_app_import")
        self.assertTrue(json.loads(lines[-1])["fake_dotenv_not_loaded"])

    def test_synthetic_entrypoints_guard_both_loaders_before_first_app_import(self):
        repo = Path(__file__).resolve().parents[1]
        code = """
import importlib.abc, json, os, runpy, sys
from pathlib import Path
script, fake_env, arguments = sys.argv[1:]
import dotenv
import dotenv.main

class StopBeforeApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'classroom_app':
            return None
        dotenv.load_dotenv(fake_env, override=True)
        dotenv.main.load_dotenv(fake_env, override=True)
        assert 'LQ_TEST_RUNNER_FAKE_ENV_SENTINEL' not in os.environ
        assert os.environ['DB_ENGINE'] == 'sqlite'
        assert not os.environ.get('DATABASE_URL')
        assert 'PGSERVICE' not in os.environ
        import asyncio, psycopg
        for connect in (psycopg.connect, psycopg.Connection.connect):
            try:
                connect('postgresql://invalid.invalid/do-not-connect')
            except RuntimeError as error:
                assert 'forbids real PostgreSQL' in str(error)
            else:
                raise AssertionError('A PostgreSQL entrypoint was left usable')
        try:
            asyncio.run(psycopg.AsyncConnection.connect('postgresql://invalid.invalid/do-not-connect'))
        except RuntimeError as error:
            assert 'forbids real PostgreSQL' in str(error)
        else:
            raise AssertionError('The async PostgreSQL entrypoint was left usable')
        runtime = Path(os.environ['LANSHARE_DATA_ROOT']).resolve()
        assert Path(os.environ['MAIN_DB_PATH']).resolve() == runtime / 'db/classroom.db'
        print(json.dumps({'dotenv_guard_before_application': True}))
        raise SystemExit(0)

sys.meta_path.insert(0, StopBeforeApplication())
sys.argv = [script, *json.loads(arguments)]
runpy.run_path(script, run_name='__main__')
raise AssertionError('entrypoint did not reach the application import boundary')
"""
        with tempfile.TemporaryDirectory(dir=repo / ".codex-temp") as folder:
            base = Path(folder)
            fake_env = base / "fake.env"
            fake_env.write_text("LQ_TEST_RUNNER_FAKE_ENV_SENTINEL=loaded\n", encoding="utf-8")
            entries = [
                ("tests/e2e/scripts/serve_ui_v3.py", "serve", True),
                ("tests/e2e/scripts/prepare_ui_v3_runtime.py", "ui-v3", False),
                ("tests/e2e/scripts/prepare_p03_runtime.py", "p03", False),
                ("tools/ui/prepare_lq_pages.py", "lq-pages", True),
            ]
            for script, name, needs_fixture in entries:
                with self.subTest(entrypoint=script):
                    runtime = base / name
                    if needs_fixture:
                        runtime.mkdir()
                        (runtime / "fixture.json").write_text(json.dumps({
                            "uiV3Synthetic": True, "databasePath": str(runtime / "db/classroom.db"),
                        }), encoding="utf-8")
                    arguments = [str(runtime)] if name == "lq-pages" else ["--runtime-root", str(runtime)]
                    if name == "serve":
                        arguments += ["--port", "8158"]
                    elif name == "p03":
                        arguments += ["--synthetic"]
                    env = dict(os.environ, PYTHON_DOTENV_DISABLED="0", PGSERVICE="do-not-connect",
                               DATABASE_URL="postgresql://invalid.invalid/do-not-connect")
                    env.pop("LQ_TEST_RUNNER_FAKE_ENV_SENTINEL", None)
                    result = subprocess.run(
                        [sys.executable, "-c", code, str(repo / script), str(fake_env), json.dumps(arguments)],
                        cwd=repo, env=env, text=True, capture_output=True, timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(json.loads(result.stdout.strip())["dotenv_guard_before_application"])
