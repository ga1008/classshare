import tempfile
import unittest
from pathlib import Path

from tools.ui.lint_lq import audit, violations


class LqFoundationGuardTests(unittest.TestCase):
    def test_component_dialog_declarations_do_not_hide_native_calls(self):
        for source in ('export function confirm(props) {}', 'LQ.confirm(props)', 'dialogs.confirm(props)'):
            self.assertEqual(violations('static/js/lq/dialogs.js', source), [])
        for source in ('confirm("x")', 'window.confirm("x")', 'globalThis.alert("x")', 'self.confirm("x")',
                       'export function confirm(props) { window.confirm(props); }'):
            self.assertEqual([item['rule'] for item in violations('static/js/lq/dialogs.js', source)], ['native-confirm'])

    def test_component_macros_and_preview_partials_are_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ('templates/macros/lq/button.html', 'templates/dev/lq_presentation.html')
            for file in paths:
                destination = root / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text('<button class="btn btn-primary">Save</button>', encoding='utf-8')
            report = audit(root, {'entries': []}, [])
            self.assertEqual(report['activeFileCount'], 2)
            self.assertEqual({item['path'] for item in report['blocking']}, set(paths))

    def test_foundations_are_enforced_before_any_page_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for file in ('static/css/lq/base.css', 'static/css/lq/pages/centered.css'):
                destination = root / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text('.sample {color:#fff}', encoding='utf-8')
            report = audit(root, {'entries': []}, [])
            self.assertEqual(report['activeFileCount'], 1)
            self.assertEqual(report['blocking'], [{'path': 'static/css/lq/base.css', 'line': 1, 'rule': 'literal-color'}])

    def test_root_paths_and_nested_modules_are_both_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for file in ('static/js/lq/theme.js', 'static/js/lq/nested/actions.js', 'templates/partials/lq_theme_core.js'):
                destination = root / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text('window.alert("legacy")', encoding='utf-8')
            report = audit(root, {'entries': []}, [])
            self.assertEqual(report['activeFileCount'], 3)
            self.assertEqual(len(report['blocking']), 3)


if __name__ == '__main__':
    unittest.main()
