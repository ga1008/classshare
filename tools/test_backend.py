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
from contextlib import ExitStack


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.isolated_environment import (
    guard_dotenv_loading, guard_postgres_connections,
    isolate_sqlite_environment as isolate_environment,
    reject_postgres,  # Retain the tested runner import interface.
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    with tempfile.TemporaryDirectory(prefix="lanshare-unit-", ignore_cleanup_errors=True) as folder, ExitStack() as guards:
        runtime = Path(folder).resolve()
        (runtime / "db").mkdir()
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
            return 0
        suite = unittest.defaultTestLoader.discover(str(REPO / "tests"), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
