"""好友入口与社交前端合同 (docs/social-friends-groups-2026-10-27.md §9).

Real Jinja (no app, HTTP or database): every authenticated topbar variant
renders exactly one 好友 entry carrying the client hooks and one module
script, only for real students/teachers; static sources keep the LQ rules
(no native confirm, no legacy toast, no literal colours / z-index, no blur).
"""
from pathlib import Path
import re
import unittest

from jinja2 import ChoiceLoader, DictLoader

from tests.test_lq_navbar_shell import FIXTURES, Document, NavbarShellRenderingTests
from tests.test_profile_template_contract import template_environment

ROOT = Path(__file__).resolve().parents[1]
CLIENT = 'js/social/social_client.js'
SOCIAL_JS = sorted((ROOT / 'static/js/social').glob('*.js'))
SOCIAL_CSS = ROOT / 'static/css/lq/pages/social.css'


class SocialEntryRenderingTests(NavbarShellRenderingTests):
    """Reuses the navbar shell fixture renderer (synthetic profile view-model)."""

    def assert_one_entry(self, doc, role):
        entries = doc.attr('data-social-entry')
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual('button', entry.tag)
        self.assertEqual('button', entry.attrs['type'])
        self.assertEqual(f'{role}:73', entry.attrs['data-social-me'])
        self.assertEqual('dialog', entry.attrs['aria-haspopup'])
        for key in ('data-social-css', 'data-social-marked', 'data-social-markdown', 'data-social-polyfills'):
            self.assertTrue(entry.attrs[key].startswith('/static/'), key)
        self.assertIn('css/lq/pages/social.css', entry.attrs['data-social-css'])
        badges = [node for node in doc.attr('data-social-unread') if entry in node.ancestors()]
        self.assertEqual(1, len(badges))
        self.assertIn('hidden', badges[0].attrs)
        scripts = doc.scripts(CLIENT)
        self.assertEqual(1, len(scripts))
        self.assertEqual('module', scripts[0].attrs['type'])

    def test_navbar_legacy_and_lq_shell_have_one_entry_per_role(self):
        for role in ('student', 'teacher'):
            for families in ('', 'navbar-shell'):
                with self.subTest(role=role, families=families):
                    _, doc = self.render('navbar-contract.html', role=role, families=families)
                    self.assert_unique_document(doc)
                    self.assert_one_entry(doc, role)

    def test_condensed_lq_navbar_keeps_entry_next_to_notifications(self):
        _, doc = self.render('navbar-contract.html', role='student', families='navbar-shell')
        entry = doc.attr('data-social-entry')[0]
        pane = doc.identity('navbar-topbar--lq-actions')
        self.assertIn(pane, entry.ancestors())
        self.assertIn('lq-btn', entry.attrs['class'].split())

    def test_manage_legacy_and_lq_shell_have_one_entry(self):
        for families in ('', 'manage-shell'):
            with self.subTest(families=families):
                _, doc = self.render('manage-contract.html', role='teacher', families=families)
                self.assert_unique_document(doc)
                self.assert_one_entry(doc, 'teacher')

    def test_wanderer_and_other_roles_get_no_entry_or_client(self):
        for user in ({'role': 'student', 'id': 73, 'enrollment_status': 'wanderer'},
                     {'role': 'student', 'id': 73, 'is_wanderer': True},
                     {'role': 'guest', 'id': 73}, {'role': 'teacher', 'id': None}):
            with self.subTest(user=user):
                _, doc = self.render('navbar-contract.html', role='student', user_info={**user, 'name': '合成'})
                self.assertFalse(doc.attr('data-social-entry'))
                self.assertFalse(doc.scripts(CLIENT))


class SocialEntryMacroTests(unittest.TestCase):
    def setUp(self):
        self.env = template_environment()
        self.env.loader = ChoiceLoader([DictLoader({
            'entry.html': "{% from 'partials/social_entry.html' import social_entry %}{{ social_entry(user, variant) }}",
        }), self.env.loader])

    def test_every_variant_renders_the_same_hooks(self):
        for variant in ('lq', 'legacy', 'manage', 'classroom'):
            with self.subTest(variant=variant):
                html = self.env.get_template('entry.html').render(user={'role': 'teacher', 'id': 5, 'name': '甲'}, variant=variant)
                doc = Document(html)
                self.assertEqual(1, len(doc.attr('data-social-entry')))
                self.assertEqual('teacher:5', doc.attr('data-social-entry')[0].attrs['data-social-me'])
                self.assertEqual(1, len(doc.attr('data-social-unread')))
                self.assertEqual(1, len(doc.scripts(CLIENT)))
                self.assertIn('好友', html)

    def test_classroom_topbar_calls_the_entry(self):
        source = (ROOT / 'templates/classroom_main_v4.html').read_text(encoding='utf-8')
        self.assertIn("social_entry(user_info, 'classroom')", source)


class SocialStaticSourceTests(unittest.TestCase):
    RULES = {
        'native-confirm': re.compile(r"(?<![\w$.])(?:window\.)?(?:confirm|alert)\s*\("),
        'legacy-toast': re.compile(r"\bshowToast\s*\("),
        'literal-color': re.compile(r"#[\da-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(\s*[\d.]"),
        'literal-z-index': re.compile(r"z-index\s*:\s*-?\d"),
        'blur-host': re.compile(r"backdrop-filter\s*:\s*(?!none)"),
    }

    def test_sources_exist(self):
        names = {path.name for path in SOCIAL_JS}
        self.assertTrue({'social_client.js', 'social_upload.js', 'social_lan.js', 'sha256.js', 'friends_modal.js',
                         'chat_modal.js', 'chat_composer.js', 'chat_messages.js', 'chat_sidebar.js'} <= names)
        self.assertTrue(SOCIAL_CSS.exists())

    def test_lq_rules_hold_in_social_sources(self):
        for path in [*SOCIAL_JS, SOCIAL_CSS]:
            text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding='utf-8'), flags=re.S)
            for name, pattern in self.RULES.items():
                if name == 'native-confirm':
                    # `confirm(` from lq/dialogs.js is the shared dialog, imported by name.
                    text_checked = re.sub(r"\bawait confirm\(|\bconfirm\(\{", "", text)
                else:
                    text_checked = text
                with self.subTest(path=path.name, rule=name):
                    self.assertIsNone(pattern.search(text_checked))

    def test_client_contract_constants(self):
        client = (ROOT / 'static/js/social/social_client.js').read_text(encoding='utf-8')
        self.assertIn('PING_MS = 25_000', client)
        self.assertIn('/ws/social', client)
        self.assertIn('AUTH_CLOSE_CODE = 1008', client)
        upload = (ROOT / 'static/js/social/social_upload.js').read_text(encoding='utf-8')
        self.assertIn('WHOLE_DIGEST_LIMIT = 64 * 1024 * 1024', upload)
        lan = (ROOT / 'static/js/social/social_lan.js').read_text(encoding='utf-8')
        self.assertIn('iceServers: []', lan)
        self.assertIn('LAN_CONNECT_TIMEOUT_MS = 8000', lan)
        self.assertIn('LAN_MIN_BYTES = 5 * 1024 * 1024', lan)
        self.assertIn('BACKFILL_RATE = 512 * 1024', lan)


del NavbarShellRenderingTests  # not collected twice here; the subclass re-runs its shell checks with the entry present

if __name__ == '__main__':
    unittest.main()
