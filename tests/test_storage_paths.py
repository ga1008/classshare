import shutil
import unittest

from classroom_app.storage_paths import relative_path_variants, resolve_migrated_file_path
from tools import db_inventory


class StoragePathResolutionTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_inventory.TEMP_ROOT / "unit-storage-paths"
        if self.runtime_root.exists():
            shutil.rmtree(self.runtime_root)
        self.submissions_root = self.runtime_root / "data" / "files" / "submissions"
        self.submissions_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.runtime_root.exists():
            shutil.rmtree(self.runtime_root)

    def test_percent_encoded_percent_variant_resolves_legacy_submission_path(self):
        target = self.submissions_root / "6" / "25" / "129" / "ipconfig %25USERPROFILE%25.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ipconfig", encoding="utf-8")

        resolved = resolve_migrated_file_path(
            "/app/data/files/submissions/6/25/129/ipconfig %USERPROFILE%.txt",
            active_root=self.submissions_root,
            markers=("files/submissions",),
        )

        self.assertEqual(target, resolved)
        self.assertIn("6/25/129/ipconfig %25USERPROFILE%25.txt", relative_path_variants("6/25/129/ipconfig %USERPROFILE%.txt"))


if __name__ == "__main__":
    unittest.main()
