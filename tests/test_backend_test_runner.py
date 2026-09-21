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


def isolation_status(stdout):
    return next(json.loads(line) for line in stdout.splitlines()
                if line.startswith('{"unit_test_isolation":'))


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
