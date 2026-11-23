"""Check the shared-progress audit chain under forced, low-volume PG16 writer contention."""

import json
import importlib.util
import os
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
from unittest import TestResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.native_postgres_temp import OwnedPostgresRuntime


def main():
    with OwnedPostgresRuntime(
        ROOT / ".codex-temp/evidence/minigame-progress-pg16", prefix="writers-"
    ) as cluster:
        print("Evidence", cluster.target, flush=True)
        cluster.start()
        settings = {
            "DB_ENGINE": "postgres",
            "DATABASE_URL": f"postgresql://rehearsal_admin@127.0.0.1:{cluster.port}/postgres",
            "POSTGRES_BACKEND_READY": "true",
            "POSTGRES_POOL_ENABLED": "false",
            "SECRET_KEY": secrets.token_hex(32),
            "LANSHARE_DATA_ROOT": str(cluster.runtime),
            "MAIN_DB_PATH": str(cluster.runtime / "unused.sqlite"),
        }
        with patch.dict(os.environ, settings), patch("dotenv.load_dotenv", return_value=False):
            from classroom_app.db.connection import get_db_connection, get_configured_db_engine

            # tests.__init__ is deliberately SQLite-only. Load just the shared scenario.
            spec = importlib.util.spec_from_file_location(
                "owned_game_progress_fixture", ROOT / "tests/test_minigame_progress_concurrency.py"
            )
            fixture = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fixture)
            assert get_configured_db_engine() == "postgres"

            report = fixture.exercise()
            flows = importlib.util.spec_from_file_location(
                "owned_game_progress_flows", ROOT / "tests/test_minigame_runtime.py"
            )
            module = importlib.util.module_from_spec(flows)
            flows.loader.exec_module(module)

            class NativeRoster(module.GameRuntimeTest):
                def prepare_database(self):
                    from classroom_app import config

                    assert config.DB_ENGINE == "postgres" and config.DATABASE_URL == settings["DATABASE_URL"]

            test = NativeRoster("test_shared_progress_locks_roster_in_actor_order_and_retries")
            result = TestResult()
            test.run(result)
            report["normalStartIntentReplay"] = result.wasSuccessful()
            if not result.wasSuccessful():
                for case, trace in result.errors + result.failures:
                    print(case, trace, file=sys.stderr)
                report["passed"] = False
            with get_db_connection() as conn:
                report["postgres"] = conn.execute("SHOW server_version").fetchone()[0]
            cluster.write_evidence_json("result.json", report)
            print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
