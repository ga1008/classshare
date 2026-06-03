import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FACADE_FILE_BUDGETS = {
    "classroom_app/database.py": 400,
    "classroom_app/routers/materials.py": 300,
    "classroom_app/routers/homework.py": 300,
    "classroom_app/routers/manage.py": 300,
    "classroom_app/routers/ui.py": 300,
}

DB_MODULE_LINE_BUDGET = 2500
ROUTER_PART_LINE_BUDGET = 1800


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class ArchitectureFileBudgetTests(unittest.TestCase):
    def test_legacy_database_and_router_facades_stay_small(self):
        for rel_path, budget in FACADE_FILE_BUDGETS.items():
            with self.subTest(path=rel_path):
                self.assertLessEqual(_line_count(REPO_ROOT / rel_path), budget)

    def test_split_database_modules_stay_under_budget(self):
        db_modules = sorted((REPO_ROOT / "classroom_app" / "db").glob("*.py"))
        self.assertGreater(len(db_modules), 4)

        for path in db_modules:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertLessEqual(_line_count(path), DB_MODULE_LINE_BUDGET)

    def test_split_router_parts_stay_under_budget(self):
        router_parts = sorted((REPO_ROOT / "classroom_app" / "routers").glob("*_parts/*.py"))
        self.assertGreater(len(router_parts), 20)

        for path in router_parts:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertLessEqual(_line_count(path), ROUTER_PART_LINE_BUDGET)


if __name__ == "__main__":
    unittest.main()
