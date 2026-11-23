"""Run the game business contract against freshly created native PostgreSQL databases."""

import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest import defaultTestLoader, TextTestRunner
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.native_postgres_temp import OwnedPostgresRuntime


def main():
    import psycopg

    with OwnedPostgresRuntime(
        ROOT / ".codex-temp/evidence/minigames-v21-runtime", prefix="rules-rooms-"
    ) as cluster:
        cluster.start()
        admin = f"postgresql://rehearsal_admin@127.0.0.1:{cluster.port}/postgres"
        os.environ.update(
            DB_ENGINE="postgres",
            DATABASE_URL=admin,
            POSTGRES_POOL_ENABLED="false",
            POSTGRES_BACKEND_READY="true",
            LANSHARE_DATA_ROOT=str(cluster.runtime),
        )
        with patch("dotenv.load_dotenv", return_value=False):
            spec = importlib.util.spec_from_file_location(
                "synthetic_game_contract", ROOT / "tests/test_minigame_runtime.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        class NativePG(module.GameRuntimeTest):
            def prepare_database(self):
                from classroom_app import config

                database = "synth_" + uuid.uuid4().hex
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute(
                        psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(database))
                    )
                self.enterContext(
                    patch.object(config, "DATABASE_URL", admin.rsplit("/", 1)[0] + "/" + database)
                )

        result = TextTestRunner(verbosity=2).run(defaultTestLoader.loadTestsFromTestCase(NativePG))
        report = {
            "engine": "owned-native-postgres",
            "tests": result.testsRun,
            "ok": result.wasSuccessful(),
            "errors": len(result.errors),
            "failures": len(result.failures),
        }
        (cluster.target / "result.json").write_text(json.dumps(report, indent=2), encoding="utf8")
        print("Owned evidence", cluster.target)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
