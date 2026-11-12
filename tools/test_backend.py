"""Run unit tests without loading application data or PostgreSQL credentials.

Native PostgreSQL rehearsals have separate, explicit disposable-cluster entry
points. This runner never enables them, even when their opt-ins are inherited.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
import shutil
import signal
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.isolated_environment import (
    guard_dotenv_loading, guard_postgres_connections,
    isolate_sqlite_environment as isolate_environment,
    reject_postgres,  # Retain the tested runner import interface.
)
from tools.temp_path_safety import resolve_temp_child

RUNTIME_PARENT = REPO / ".codex-temp" / "scratch" / "backend-tests"
SCRATCH_ROOT = REPO / ".codex-temp" / "scratch"
REPORT_PARENT = REPO / ".codex-temp" / "evidence" / "backend-tests"
OWNER_FILE = ".unit-test-owner"


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--report", type=Path, help="Retained JSON evidence, outside the disposable runtime")
    parser.add_argument("--_worker-runtime", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_worker-token", help=argparse.SUPPRESS)
    return parser.parse_args()


def _new_runtime(run_id: str | None = None) -> Path:
    # Keep even an interrupted test's scratch separate from retained reports,
    # release inputs and the project root. Never adopt or sweep older runs.
    resolve_temp_child(RUNTIME_PARENT / "unit-allocation-check", repo_root=REPO,
                       temp_root=RUNTIME_PARENT, default=RUNTIME_PARENT)
    RUNTIME_PARENT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="unit-", dir=RUNTIME_PARENT))
    try:
        (directory / OWNER_FILE).write_text(run_id or uuid.uuid4().hex, encoding="ascii")
    except BaseException:
        shutil.rmtree(directory)
        raise
    return directory


def _remove_owned_runtime(runtime: Path, run_id: str) -> None:
    checked = resolve_temp_child(runtime, repo_root=REPO, temp_root=RUNTIME_PARENT, default=RUNTIME_PARENT)
    marker = checked / OWNER_FILE
    if marker.is_symlink() or marker.read_text(encoding="ascii") != run_id:
        raise ValueError("Refusing cleanup after unit test runtime ownership changed")
    shutil.rmtree(checked)


def _report_path(raw: Path | None) -> Path | None:
    if raw is None:
        return None
    target = (raw if raw.is_absolute() else REPO / raw).resolve()
    if target.is_relative_to(SCRATCH_ROOT.resolve()):
        raise ValueError("Test evidence must be outside the disposable runtime and all scratch directories")
    return target


def _record(args, run_id: str, status: str, **details) -> dict:
    return {"kind": "isolated_backend_tests", "run_id": run_id, "pattern": args.pattern,
            "status": status, "updated_at": datetime.now(timezone.utc).isoformat(),
            "business_data_used": False, "postgres_connections": "forbidden", **details}


def _save_report(report: dict, path: Path | None, *, force: bool = False) -> None:
    if path is None:
        if not force:
            return
        path = REPORT_PARENT / f"{report['run_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            pending = Path(stream.name)
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _current_report(path: Path | None, run_id: str) -> dict | None:
    try:
        value = json.loads((path or REPORT_PARENT / f"{run_id}.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("run_id") == run_id else None
    except (OSError, ValueError):
        return None


def _worker_runtime(args) -> Path:
    runtime = resolve_temp_child(args._worker_runtime, repo_root=REPO,
                                 temp_root=RUNTIME_PARENT, default=RUNTIME_PARENT)
    marker = runtime / OWNER_FILE
    if (not args._worker_token or len(args._worker_token) != 32
            or any(char not in "0123456789abcdef" for char in args._worker_token)
            or not runtime.name.startswith("unit-")
            or marker.is_symlink() or not marker.is_file()
            or marker.read_text(encoding="ascii") != args._worker_token):
        raise ValueError("Unit test worker requires its parent's owned runtime token")
    return runtime


def _run_tests(args, runtime: Path, run_id: str, report_path: Path | None) -> int:
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    with ExitStack() as guards:
        (runtime / "db").mkdir(exist_ok=True)
        temporary = runtime / "tmp"
        temporary.mkdir(exist_ok=True)
        # Cover both Python's cached choice and subprocesses inheriting TEMP.
        # This belongs to this unit-test worker, not shared app isolation code.
        guards.enter_context(patch.dict(os.environ, {name: str(temporary) for name in ("TMP", "TEMP", "TMPDIR")}))
        guards.enter_context(patch.object(tempfile, "tempdir", str(temporary)))
        isolate_environment(runtime)
        guard_dotenv_loading(guards)
        # Guard the driver and pool connection class before importing any app or
        # test module. Individual adapter tests can still install fake drivers.
        guard_postgres_connections(guards)
        from classroom_app import config
        if config.DB_ENGINE != "sqlite" or config.DATABASE_URL or Path(config.DB_PATH).resolve() != runtime / "db" / "classroom.db":
            raise RuntimeError("Unit test configuration did not resolve to the isolated runtime")
        print(json.dumps({"unit_test_isolation": True, "dotenv_loaders": "blocked_before_app_import", "engine": config.DB_ENGINE,
                          "runtime": str(runtime), "postgres_connections": "forbidden"}), flush=True)
        if args.check_only:
            _save_report(_record(args, run_id, "checked", tests=0), report_path)
            return 0
        suite = unittest.defaultTestLoader.discover(str(REPO / "tests"), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        _save_report(_record(
            args, run_id, "passed" if result.wasSuccessful() else "failed", tests=result.testsRun,
            failures=[{"test": str(test), "traceback": detail} for test, detail in result.failures],
            errors=[{"test": str(test), "traceback": detail} for test, detail in result.errors],
            skipped=[{"test": str(test), "reason": reason} for test, reason in result.skipped],
        ), report_path, force=not result.wasSuccessful())
        return 0 if result.wasSuccessful() else 1


def main() -> int:
    """In-process interface retained for isolation/loader contract tests."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _arguments()
    report_path = _report_path(args.report)
    run_id = uuid.uuid4().hex
    runtime = None
    owns_runtime = not args._worker_runtime
    _save_report(_record(args, run_id, "running"), report_path)
    try:
        if args._worker_runtime:
            runtime = _worker_runtime(args)
            run_id = args._worker_token
        else:
            runtime = _new_runtime(run_id)
        _save_report(_record(args, run_id, "running", runtime=str(runtime)), report_path)
        return _run_tests(args, runtime, run_id, report_path)
    except BaseException as error:
        _save_report(_record(args, run_id, "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                             error=f"{type(error).__name__}: {error}", runtime=str(runtime) if runtime else None),
                     report_path, force=True)
        raise
    finally:
        if owns_runtime and runtime is not None:
            try:
                _remove_owned_runtime(runtime, run_id)
            except BaseException as error:
                report = _current_report(report_path, run_id) or _record(args, run_id, "failed")
                report.update(status="failed", cleanup="retained", runtime=str(runtime),
                              cleanup_error=f"{type(error).__name__}: {error}")
                _save_report(report, report_path, force=True)
                raise


def _wait_for_worker(worker) -> int:
    # A bounded wait also lets Python-delivered interrupts run on Windows.
    while True:
        try:
            return worker.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass


def _stop_worker_tree(worker) -> bool:
    """Stop only this runner's tree; uncertainty must retain its runtime."""
    if os.name == "nt":
        try:
            stopped = subprocess.run(["taskkill.exe", "/PID", str(worker.pid), "/T", "/F"],
                                     capture_output=True, timeout=15)
            if stopped.returncode != 0:
                return False
            worker.wait(timeout=10)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.killpg(worker.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            worker.poll()
            try:
                os.killpg(worker.pid, 0)
            except ProcessLookupError:
                worker.wait(timeout=1)
                return True
            time.sleep(0.05)
        os.killpg(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)
        try:
            os.killpg(worker.pid, 0)
        except ProcessLookupError:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def _cli_main() -> int:
    args = _arguments()
    if args._worker_runtime:
        return main()
    # The parent imports no application modules. Delete only our new runtime,
    # after the worker exits and Windows releases its DB/log handles. Never
    # sweep .codex-temp, deployment evidence, or somebody else's temporary root.
    report_path = _report_path(args.report)
    run_id = uuid.uuid4().hex
    runtime = None
    worker = None
    exit_confirmed = False
    status_error = None
    exit_code = 1
    _save_report(_record(args, run_id, "running"), report_path)
    try:
        runtime = _new_runtime(run_id)
        command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
                   "--_worker-runtime", str(runtime.resolve()), "--_worker-token", run_id]
        worker = subprocess.Popen(command, cwd=REPO, start_new_session=os.name != "nt")
        exit_code = _wait_for_worker(worker)
        exit_confirmed = True
    except BaseException as error:
        status_error = error
        exit_code = 130 if isinstance(error, KeyboardInterrupt) else 1
        exit_confirmed = worker is None or _stop_worker_tree(worker)
    report = _current_report(report_path, run_id)
    if report is None:
        status = ("checked" if args.check_only else "passed") if report_path is None and exit_code == 0 else "failed"
        report = _record(args, run_id, status)
    if status_error is not None:
        report.update(status="interrupted" if isinstance(status_error, KeyboardInterrupt) else "failed",
                      error=f"{type(status_error).__name__}: {status_error}")
    elif report["status"] == "running" or exit_code != 0 and report["status"] in {"passed", "checked"}:
        report.update(status="failed", error="Worker exited without a matching successful result")
    if report["status"] in {"failed", "interrupted"} and exit_code == 0:
        exit_code = 130 if report["status"] == "interrupted" else 1
    report.update(worker_exit_code=exit_code, worker_exit_confirmed=exit_confirmed,
                  runtime=str(runtime) if runtime else None)
    if runtime is not None and exit_confirmed:
        try:
            _remove_owned_runtime(runtime, run_id)
            report["cleanup"] = "removed"
        except BaseException as error:
            report.update(status="failed", cleanup="retained", cleanup_error=f"{type(error).__name__}: {error}")
            exit_code = exit_code or 1
    elif runtime is not None:
        report.update(cleanup="retained", cleanup_error="Owned worker tree exit could not be confirmed")
        exit_code = exit_code or 1
    report["runner_exit_code"] = exit_code
    _save_report(report, report_path, force=exit_code != 0)
    if status_error is not None:
        print(f"Backend test runner: {type(status_error).__name__}: {status_error}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(_cli_main())
