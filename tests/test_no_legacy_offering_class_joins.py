"""Guard: offering↔student resolution must go through the membership link.

Any SQL equating an offering alias's class_id with a student-side class_id
must carry the ``class_offering_class_links`` membership fallback (the
``cocl_m``/``st_m`` correlated EXISTS produced by offering_membership_service).
A bare equality silently shrinks combined-class offerings (合班课堂) back to
the primary class. See docs/combined-class-offering-plan-2026-08.md.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "classroom_app"

OFFERING_ALIASES = {"o", "co"}
STUDENT_ALIASES = {"s", "st", "stu", "roster", "scored_student"}

ATOM = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.class_id\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.class_id\b"
)

# Files allowed to contain the bare equality (the membership machinery itself).
ALLOWED_FILES = {
    "db/schema_offering_class_links.py",
    "services/offering_membership_service.py",
}

MEMBERSHIP_MARKERS = ("cocl_m", "st_m")
WINDOW = 400


class NoLegacyOfferingClassJoinTests(unittest.TestCase):
    def test_every_offering_student_join_uses_membership_links(self):
        violations: list[str] = []
        for path in APP_DIR.rglob("*.py"):
            relative = path.relative_to(APP_DIR).as_posix()
            if relative in ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for match in ATOM.finditer(text):
                a, b = match.group(1), match.group(2)
                pair = {a, b}
                if not (pair & OFFERING_ALIASES and pair & STUDENT_ALIASES):
                    continue
                window = text[max(0, match.start() - WINDOW): match.end() + WINDOW]
                if any(marker in window for marker in MEMBERSHIP_MARKERS):
                    continue
                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(f"classroom_app/{relative}:{line_number} -> {match.group(0)}")
        self.assertEqual(
            [],
            violations,
            "发现未走 membership link 的课堂↔学生 join（合班课堂会漏学生），"
            "请改用 offering_membership_service 的 SQL 片段：\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
