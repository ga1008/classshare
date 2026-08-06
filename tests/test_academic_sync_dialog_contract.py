import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AcademicSyncDialogContractTests(unittest.TestCase):
    def test_review_uses_course_navigation_and_secondary_field_diff(self):
        template = (ROOT / "templates/partials/academic_sync_dialog.html").read_text(encoding="utf-8")
        self.assertIn("data-academic-sync-course-list", template)
        self.assertIn("data-academic-sync-course-header", template)
        self.assertIn("data-academic-sync-detail", template)
        self.assertIn("data-academic-sync-detail-local", template)
        self.assertIn("data-academic-sync-detail-remote", template)
        self.assertIn("data-explain-title=\"同步差异状态\"", template)
        self.assertNotIn("课堂关系保护已开启", template)

    def test_conflicts_require_explicit_local_or_remote_choices(self):
        script = (ROOT / "static/js/academic_sync_dialog.js").read_text(encoding="utf-8")
        self.assertIn("choices[field.name] = item.requires_confirmation", script)
        self.assertIn("? null", script)
        self.assertIn("data-academic-sync-choice=\"local\"", script)
        self.assertIn("data-academic-sync-choice=\"remote\"", script)
        self.assertIn("field_choices: fieldChoices", script)
        self.assertIn("apply.disabled = running || unresolved > 0", script)
        self.assertIn("待确认课程已置顶", script)

    def test_review_workspace_has_independent_scroll_regions(self):
        styles = (ROOT / "static/css/ui-system.src.css").read_text(encoding="utf-8")
        self.assertIn(".academic-sync-review-workspace", styles)
        self.assertIn(".academic-sync-review-nav > nav", styles)
        self.assertIn(".academic-sync-review-detail .academic-sync-dialog__diff-list", styles)
        self.assertIn("scrollbar-width: thin", styles)


if __name__ == "__main__":
    unittest.main()
