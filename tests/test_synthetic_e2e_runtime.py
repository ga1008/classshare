"""The optional browser fixture must never reach the local database-copy path."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("synthetic_p03_seed", ROOT / "tests/e2e/scripts/prepare_p03_runtime.py")
SEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEED)


class SyntheticE2eRuntimeTests(unittest.TestCase):
    def test_synthetic_database_never_resolves_or_copies_local_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-temp") as temporary:
            runtime = Path(temporary) / "fresh-runtime"
            with patch.object(SEED, "_source_db_path", side_effect=AssertionError("must not read local data")):
                database = SEED._copy_runtime_db(runtime, synthetic=True)
            self.assertEqual(database, runtime / "db/classroom.db")
            self.assertTrue(database.is_file())

    def test_synthetic_rejects_existing_root_and_outside_paths_without_deletion(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-temp") as temporary:
            sentinel = Path(temporary) / "preserve.txt"
            sentinel.write_text("existing", encoding="utf-8")
            for runtime in [Path(temporary), ROOT / ".codex-temp", ROOT / "synthetic-not-allowed"]:
                with self.assertRaises(SystemExit):
                    SEED._copy_runtime_db(runtime, synthetic=True)
            self.assertEqual("existing", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
