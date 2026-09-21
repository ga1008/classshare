"""S4 actual template contracts; isolated entry point never needs a DB."""
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lq_centered_renderer', ROOT / 'tests/e2e/scripts/render_lq_centered.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))
    def tag(self, tag):
        return [attrs for name, attrs in self.nodes if name == tag]
    def identity(self, identity):
        return next(attrs for _, attrs in self.nodes if attrs.get('id') == identity)

class LqCenteredTests(unittest.TestCase):
    def test_status_outcomes_use_registered_semantic_icons_without_unknown_fallback(self):
        from jinja2 import Environment, FileSystemLoader
        icons = Environment(loader=FileSystemLoader(ROOT / 'templates')).get_template('macros/lq/icons.generated.html').module
        fallback = str(icons.lq_icon('circle-question-mark')).strip()
        for success, name in [(True, 'circle-check'), (False, 'circle-alert')]:
            with self.subTest(success=success):
                expected = str(icons.lq_icon(name)).strip()
                self.assertNotEqual(expected, fallback)
                html = renderer.render_page('status', success=success)
                self.assertIn(expected, html)
                self.assertNotIn(fallback, html)

    def test_all_documents_have_one_accessible_main_and_no_inline_style_in_new_branch(self):
        for page in renderer.PAGES:
            with self.subTest(page=page):
                doc = Document(renderer.render_page(page))
                self.assertEqual(len(doc.tag('main')), 1)
                self.assertEqual(doc.tag('main')[0]['id'], 'centered-main')
                self.assertEqual(len(doc.tag('h1')), 1)
                self.assertFalse(doc.tag('style'))
                self.assertFalse([attrs for _, attrs in doc.nodes if 'style' in attrs])
                ids = [attrs['id'] for _, attrs in doc.nodes if 'id' in attrs]
                self.assertEqual(len(ids), len(set(ids)))
                for label in doc.tag('label'):
                    if 'for' in label:
                        self.assertIn(label['for'], ids)

    def test_old_branch_and_missing_flag_keep_legacy_presentations(self):
        for page in renderer.PAGES:
            with self.subTest(page=page):
                html = renderer.render_page(page, False)
                doc = Document(html)
                self.assertNotIn('data-lq-centered', html)
                self.assertNotIn('/pages/login.css', html)
                self.assertNotIn('/pages/status.css', html)
                self.assertTrue(any('card' in a.get('class', '') for _, a in doc.nodes))
        # An isolated legacy renderer has no family global at all.
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(ROOT / 'templates'))
        env.globals.update(asset_url=lambda name: '/static/' + name, static_asset_revision=lambda: '')
        html = env.get_template('teacher_register_v4.html').render(site_record={}, request=None)
        self.assertNotIn('data-lq-centered', html)

    def test_login_native_actions_names_and_next_survive_without_javascript(self):
        for page, identity, action in [('student_login_v4', 'student-password-login-form', '/student/login'), ('teacher_login_v4', 'teacher-login-form', '/teacher/login')]:
            for enabled in (True, False):
                doc = Document(renderer.render_page(page, enabled))
                self.assertEqual(doc.identity(identity)['action'], action)
                self.assertEqual(doc.identity(identity)['method'], 'post')
                nexts = [a for a in doc.tag('input') if a.get('name') == 'next']
                self.assertTrue(nexts)
                self.assertTrue(all(a['value'] == '/materials/42?source=login' for a in nexts))
                self.assertNotIn('value', doc.identity('password'))

    def test_server_failure_retains_identifier_but_never_password_and_escapes_error(self):
        for page, identity, key in [('student_login_v4', 'identifier', 'login_identifier'), ('teacher_login_v4', 'email', 'login_email')]:
            doc = Document(renderer.render_page(page, login_error='<script>bad</script>', **{key: '\"<retained>'}))
            self.assertEqual(doc.identity(identity)['value'], '\"<retained>')
            feedback = [a for _, a in doc.nodes if 'data-login-feedback' in a and 'hidden' not in a]
            self.assertEqual(len(feedback), 1)
            self.assertEqual(feedback[0]['role'], 'alert')
            self.assertTrue(any(a.get('aria-describedby') == feedback[0]['id'] for a in doc.tag('form')))
            self.assertNotIn('value', doc.identity('password'))

    def test_student_initial_thick_teacher_thick_and_status_safe_sources(self):
        for name in ('student_login_v4', 'teacher_login_v4'):
            doc = Document(renderer.render_page(name))
            card = next(a for _, a in doc.nodes if 'data-lq-login-card' in a)
            self.assertIn('lq-glass--thick', card['class'])
            self.assertNotIn('lq-glass--clear', card['class'])
        doc = Document(renderer.render_page('session_expired'))
        self.assertIn('/student/login?next=%2Fmaterials%2F42', [a.get('href') for a in doc.tag('a')])
        doc = Document(renderer.render_page('permission_denied'))
        self.assertIn('/teacher/login?next=%2Fmanage%2Fcourses', [a.get('href') for a in doc.tag('a')])
        with self.assertRaises(ValueError):
            renderer.render_page('error', back_url='javascript:alert(1)')

if __name__ == '__main__':
    unittest.main()
