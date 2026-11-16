"""Test a newly owned loopback PG cluster; retain evidence and remove its data."""
from contextlib import contextmanager
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.native_postgres_temp import OwnedPostgresRuntime

NATIVE_TEST_MODULES = (
    "tests.test_wanderer_world_postgres",
    "tests.test_wanderer_economy_native_postgres",
    "tests.test_agent_authority_postgres",
    "tests.test_wanderer_persona_refresh_postgres",
)


@contextmanager
def _termination_as_exception():
    def interrupted(number, _frame):
        raise SystemExit(128 + number)
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _run_tests(owned):
    import psycopg
    with psycopg.connect(host="127.0.0.1", port=owned.port, user="rehearsal_admin",
                         dbname="postgres", autocommit=True, connect_timeout=5) as conn:
        identity = conn.execute(
            "SELECT current_setting('data_directory'), current_setting('listen_addresses'), current_setting('port')"
        ).fetchone()
        if (Path(identity[0]).resolve() != owned.cluster or identity[1] != "127.0.0.1"
                or int(identity[2]) != owned.port):
            raise RuntimeError("Synthetic cluster identity mismatch")
        conn.execute("CREATE DATABASE lanshare_assessment_rehearsal TEMPLATE template0")
    os.environ.update(ASSESSMENT_REHEARSAL_TEST_CLUSTER=str(owned.cluster),
                      ASSESSMENT_REHEARSAL_TEST_PORT=str(owned.port))
    suite = unittest.TestSuite()
    for module in NATIVE_TEST_MODULES:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(module))
    with owned.open_test_log() as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return {"status": "passed" if result.wasSuccessful() else "failed", "tests": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
            "modules": list(NATIVE_TEST_MODULES)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-runtime", action="store_true",
                        help="Stop the owned server but keep cluster/runtime data for debugging")
    args = parser.parse_args(argv)
    from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading
    owned = OwnedPostgresRuntime(ROOT / ".codex-temp", keep_runtime=args.keep_runtime,
                                 prefix="wanderer-native-pg-" + time.strftime("%Y%m%d-%H%M%S") + "-")
    report = {"kind": "synthetic_native_postgres", "cluster": str(owned.cluster),
              "loopback_only": True, "port": owned.port, "business_data_used": False}
    try:
        with _termination_as_exception(), owned:
            (owned.runtime / "db").mkdir()
            isolate_sqlite_environment(owned.runtime)
            guard_dotenv_loading()
            owned.start()
            report.update(_run_tests(owned))
        return 0 if report["status"] == "passed" else 1
    except BaseException as exc:
        report.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "error",
                      error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["cleanup"] = owned.cleanup_result
        report["cluster_stopped"] = bool(owned.cleanup_result and owned.cleanup_result["cluster_stopped"])
        try:
            owned.write_evidence_json("result.json", report)
        finally:
            print(json.dumps(report))


if __name__ == "__main__":
    raise SystemExit(main())
