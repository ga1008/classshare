"""C1 opt-in full Jinja contracts; execute via tools/test_backend.py only."""
import json
import unittest

from test_profile_template_contract import Document, profile_context, template_environment, SECTIONS


class LqProfilePresentationTests(unittest.TestCase):
    def render(self, role, section, flag=True, *, omit_flag=False, empty=False):
        env = template_environment()
        env.globals['resolve_user_ui_preferences'] = lambda *_: {
            'enabled': True, 'available': True, 'palette_key': 'rose', 'appearance': 'auto', 'glass': 'off',
            'version': 12, 'context_token': 'same-person-context',
            'presets': [{'key': key, 'name': key} for key in ('teal', 'indigo', 'sky', 'mint', 'violet', 'rose')]}
        ctx = profile_context(role, section, empty=empty)
        if not omit_flag:
            ctx['profile_lq_enabled'] = flag
        ctx['profile_section_hrefs'] = {key: ('/manage/me' if key == 'overview' else '/manage/me/' + key)
            if role == 'teacher' else '/profile?section=' + key for key in (*SECTIONS, 'appearance')}
        if section == 'appearance':
            ctx['nav_items'].append({'section': 'appearance', 'label': '外观', 'short_label': '外观',
                                    'href': ctx['profile_section_hrefs']['appearance'], 'active': True, 'badge': None})
        html = env.get_template('manage/profile.html' if role == 'teacher' else 'profile.html').render(**ctx)
        return html, Document(html), ctx

    def test_opt_in_real_shells_have_single_owner_and_page_css(self):
        for role in ('student', 'teacher'):
            for section in (*SECTIONS, 'appearance'):
                with self.subTest(role=role, section=section):
                    html, doc, ctx = self.render(role, section)
                    self.assertEqual(len(doc.with_attr('data-lq-profile')), 1)
                    self.assertEqual(len(doc.with_attr('data-profile-root')), 1)
                    self.assertEqual(len(doc.by_id('profile-context-json')), 1)
                    self.assertEqual(json.loads(doc.contents['profile-context-json']), ctx['profile_context'])
                    self.assertEqual(len([a for tag, a in doc.tags if tag == 'link' and a.get('href', '').endswith('/css/lq/pages/profile.css')]), 1)
                    scripts = [a['src'] for tag, a in doc.tags if tag == 'script' and 'src' in a]
                    for name in ('/js/profile.js', '/js/user_ui_preferences.js'):
                        self.assertEqual(sum(src.endswith(name) for src in scripts), 1)
                    self.assertFalse([a for _, a in doc.profile_tags if a.get('role') in ('tab', 'tablist', 'tabpanel')])
                    self.assertEqual(bool(doc.with_attr('data-lq-profile-content')), section not in ('notifications', 'private'))

    def test_flag_false_and_missing_keep_old_c0_presentation(self):
        for role in ('student', 'teacher'):
            for section in SECTIONS:
                off, doc, _ = self.render(role, section, False)
                undefined, _, _ = self.render(role, section, omit_flag=True)
                self.assertEqual(off, undefined)
                self.assertFalse(doc.with_attr('data-lq-profile'))
                self.assertNotIn('css/lq/pages/profile.css', off)
                self.assertIn('class="profile-hero profile-reveal"', off)
                self.assertNotIn('class="lq-profile-head"', off)

    def test_native_business_fields_options_and_values_equal_old_output(self):
        attrs = ('id', 'name', 'type', 'value', 'readonly', 'disabled', 'required', 'autocomplete', 'maxlength', 'min', 'max', 'accept', 'multiple', 'rows', 'checked', 'selected')
        def fields(doc):
            return [(tag, {k: v for k, v in a.items() if k in attrs}) for tag, a in doc.profile_tags if tag in ('input', 'textarea', 'select', 'option', 'form')]
        for role in ('student', 'teacher'):
            for section in ('settings', 'security', 'email', 'portfolio', 'signatures'):
                with self.subTest(role=role, section=section):
                    _, new, _ = self.render(role, section)
                    _, old, _ = self.render(role, section, False)
                    self.assertEqual(fields(new), fields(old))
                    self.assertEqual({k: v for k, v in new.contents.items() if k != 'profile-context-json'},
                                     {k: v for k, v in old.contents.items() if k != 'profile-context-json'})

    def test_canonical_hero_hrefs_and_real_navigation_state(self):
        for role in ('student', 'teacher'):
            html, doc, ctx = self.render(role, 'settings')
            hrefs = [a.get('href') for tag, a in doc.profile_tags if tag == 'a']
            self.assertIn(ctx['profile_section_hrefs']['overview'], hrefs)
            self.assertNotIn('/manage/me/overview', hrefs)
            active = [a for tag, a in doc.profile_tags if tag == 'a' and a.get('aria-current') == 'page']
            self.assertEqual(len(active), 0 if role == 'teacher' else 1)
            if active:
                self.assertEqual(active[0]['href'], '/profile?section=settings')

    def test_appearance_exists_in_ssr_before_global_auto_init_without_a_second_form(self):
        for role in ('student', 'teacher'):
            html, doc, _ = self.render(role, 'appearance')
            self.assertEqual(len(doc.with_attr('data-ui-preference-input')), 4)
            self.assertEqual(len(doc.with_attr('data-ui-preference-choice')), 6)
            self.assertEqual(len(doc.with_attr('data-ui-preference-primary-status')), 1)
            self.assertFalse([(tag, a) for tag, a in doc.profile_tags if tag == 'form'])
            # The existing script is a head module, so parser completion (not
            # source tag order) guarantees SSR controls exist before execution.
            script = next(a for tag, a in doc.tags if tag == 'script' and a.get('src', '').endswith('/js/user_ui_preferences.js'))
            self.assertEqual(script.get('type'), 'module')
            self.assertNotIn('async', script)

    def test_signature_placeholder_and_message_subtree_remain_owned_by_existing_modules(self):
        for role in ('student', 'teacher'):
            html, doc, _ = self.render(role, 'signatures')
            self.assertEqual(len(doc.with_attr('data-signature-app')), 1)
            self.assertIn('正在读取签名数据', html)
            self.assertEqual(sum(tag == 'script' and a.get('src', '').endswith('/js/profile_signatures.js') for tag, a in doc.tags), 1)
            for section in ('notifications', 'private'):
                new, _, _ = self.render(role, section)
                old, _, _ = self.render(role, section, False)
                start = '<section id="profile-message-center"'
                # Everything from the unchanged message root to the JSON tail
                # has the same authored markup; common hero/nav is separate.
                self.assertEqual(new[new.index(start):], old[old.index(start):])

    def test_growth_zero_values_stay_in_native_disclosure_without_claiming_component_migration(self):
        html, doc, _ = self.render('student', 'overview')
        self.assertTrue(any(tag == 'details' and a.get('class') == 'lq-profile-head__growth' for tag, a in doc.tags))
        self.assertIn('0 分', html)
        self.assertIn('可破境课堂</dt><dd>0</dd>', html)
        self.assertTrue(doc.with_attr('data-profile-chart'))
        _, teacher, _ = self.render('teacher', 'overview')
        progress = teacher.with_attr('data-profile-completion-bar')
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0][0], 'progress')
        self.assertIn('value', progress[0][1])


if __name__ == '__main__':
    unittest.main()
