"""Pure presentation contracts. Run through tools/test_backend.py; no app/DB imports."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, nodes
from markupsafe import Markup

from classroom_app.lq import lq_props
from classroom_app.services.manage_nav_service import build_manage_nav

ROOT = Path(__file__).resolve().parents[1]
PILOTS = {
    'manage/courses.html', 'manage/classes.html', 'manage/offering_hub.html',
    'manage/semesters.html', 'manage/textbooks.html', 'manage/lesson_plans.html',
    'manage/materials.html', 'manage/system/users.html',
}


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.tags = []; self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def ids(self, value):
        return [(tag, attrs) for tag, attrs in self.tags if attrs.get('id') == value]


class ManageLqPilotTemplateTests(unittest.TestCase):
    def setUp(self):
        # Keep the real manage shell/macros, but avoid unrelated app partial requirements.
        partials = {f'partials/{name}.html': '' for name in (
            'lq_editor_head', 'ai_workspace_widget', 'vite_islands', 'feedback_modal',
            'teacher_onboarding_modal', 'markdown_assets',
        )}
        partials['pilot-contract.html'] = '''{% extends "manage/layout.html" %}
{% block header_actions %}<form id="action-form"><fieldset disabled><button id="owned-action" type="submit" name="intent" value="save">保存</button></fieldset><input id="retained-input" name="draft" value="原位草稿"></form><iframe id="retained-frame" title="原位预览" src="/preview"></iframe>{% endblock %}
{% block content %}<form id="content-form"><input id="content-input" name="value" value="0"><button id="external-submit" form="action-form" type="submit">提交</button></form>{% endblock %}'''
        self.env = Environment(loader=ChoiceLoader([DictLoader(partials), FileSystemLoader(ROOT / 'templates')]), autoescape=True)
        self.env.globals.update(lq_props=lq_props, asset_url=lambda path: '/static/' + path)
        self.macros = self.env.get_template('macros/manage_page.html').module

    def render_shell(self, pilot=False, embedded=False, admin=False, family=False):
        user = {'id': 73, 'role': 'teacher', 'name': '测试教师', 'email': 'fixture@example.invalid'}
        return self.env.get_template('pilot-contract.html').render(
            lq_pilot_enabled=pilot, embedded_mode=embedded, page_title='课程', active_page='courses',
            user_info=user, manage_nav=build_manage_nav(user, 'courses', is_super_admin=admin),
            lq_family_enabled=lambda key: family and key == 'manage-shell',
        )

    def test_family_shell_preserves_permissions_actions_and_legacy_business_scope(self):
        for admin in (False, True):
            old, new = self.render_shell(admin=admin), self.render_shell(admin=admin, family=True)
            tags = Tags(new)
            body = next(attrs for tag, attrs in tags.tags if tag == 'body')
            self.assertIn('lq-manage-shell', body['class'].split())
            self.assertNotIn('lq-manage-pilot', body['class'].split())
            self.assertIn('js/manage_lq_pilot.js', new)
            self.assertNotIn('const initManageSidebar =', new)
            def links(html):
                return [attrs['href'] for tag, attrs in Tags(html).tags if tag == 'a' and 'manage-nav-item' in attrs.get('class', '').split()]
            self.assertEqual(links(old), links(new))
            for identity in ('owned-action', 'retained-input', 'retained-frame', 'content-form', 'sidebar', 'manage-pilot-main'):
                self.assertEqual(len(tags.ids(identity)), 1, identity)
            self.assertEqual(tags.ids('external-submit')[0][1]['form'], 'action-form')
            self.assertTrue(any(tag == 'fieldset' and 'disabled' in attrs for tag, attrs in tags.tags))

    def test_family_shell_never_changes_embedded_document_protocol(self):
        self.assertEqual(self.render_shell(embedded=True), self.render_shell(embedded=True, family=True))

    def test_legacy_macro_default_and_explicit_off_match_with_raw_template_attrs(self):
        kwargs = dict(description='描述', explain='说明', eyebrow='既有眉题', title_id='old-title', actions=[{'label': '创建', 'id': 'create', 'variant': 'primary', 'attrs': 'data-legacy="yes"'}])
        implicit = str(self.macros.page_head('原页头', **kwargs)).strip()
        explicit = str(self.macros.page_head('原页头', lq_enabled=False, **kwargs)).strip()
        self.assertEqual(implicit, explicit)
        self.assertIn('class="page-head"', implicit)
        self.assertIn('既有眉题', implicit)
        self.assertIn('data-legacy="yes"', implicit)
        self.assertNotIn('lq-page-head', implicit)
        for macro, props in ((self.macros.filter_bar, {'search_id': 'search', 'search_attrs': 'data-legacy="yes"'}), (self.macros.empty_state, {'title': '暂无', 'action_label': '创建', 'action_attrs': 'data-legacy="yes"'})):
            self.assertEqual(str(macro(**props)), str(macro(lq_enabled=False, **props)))

    def test_pilot_page_head_retains_ids_explanation_actions_and_author_caller(self):
        source = '''{% from "macros/manage_page.html" import page_head %}{% call page_head('课程', lq_enabled=true, description='说明', title_id='course-title', explain='帮助', actions=[{'label':'创建','id':'create','variant':'primary'},{'label':'开课','href':'/manage/teaching/offerings','variant':'outline'}]) %}<input id="caller-draft" name="draft" value="0"><iframe id="caller-frame" title="预览" src="/preview"></iframe>{% endcall %}'''
        html = self.env.from_string(source).render(); tags = Tags(html)
        self.assertIn('lq-page-head', html)
        for identity in ('course-title', 'create', 'caller-draft', 'caller-frame'):
            self.assertEqual(1, len(tags.ids(identity)))
        self.assertEqual('button', tags.ids('create')[0][1]['type'])
        self.assertIn('data-explain-toggle', html)
        self.assertIn('data-explain-text="帮助"', html)
        self.assertIn('href="/manage/teaching/offerings"', html)

    def test_pilot_text_and_attrs_do_not_add_raw_html_bypass(self):
        html = str(self.macros.page_head(Markup('<img src=x>'), lq_enabled=True, eyebrow='不再填充'))
        self.assertIn('&lt;img', html); self.assertNotIn('<img', html); self.assertNotIn('不再填充', html)
        for attrs in ('onclick="alert(1)"', {'onclick': 'alert(1)'}, {'style': 'color:red'}):
            with self.subTest(attrs=attrs), self.assertRaises((ValueError, TypeError)):
                self.macros.page_head('标题', lq_enabled=True, actions=[{'label': '动作', 'attrs': attrs}])
        with self.assertRaises(ValueError):
            self.macros.empty_state('暂无', lq_enabled=True, action_label='打开', action_href='javascript:alert(1)')

    def test_filter_compatibility_does_not_create_nested_form_or_submission_owner(self):
        source = '''{% from "macros/manage_page.html" import filter_bar %}<form id="owner">{% call filter_bar('search', lq_enabled=true, search_attrs={'data-filter': 'retained'}) %}<select id="truth" name="course"><option value="0">0</option></select>{% endcall %}</form>'''
        html = self.env.from_string(source).render(); tags = Tags(html)
        self.assertEqual(1, sum(tag == 'form' for tag, _ in tags.tags))
        self.assertEqual('search', tags.ids('search')[0][1]['type'])
        self.assertNotIn('name', tags.ids('search')[0][1])
        self.assertEqual('off', tags.ids('search')[0][1]['autocomplete'])
        self.assertEqual('retained', tags.ids('search')[0][1]['data-filter'])
        self.assertEqual(1, len(tags.ids('truth')))
        self.assertIn('role="group"', html)

    def test_empty_reason_and_explicit_action_do_not_forge_zero_or_retry(self):
        for reason in ('empty', 'no-results', 'error', 'forbidden', 'offline'):
            html = str(self.macros.empty_state('明确原因', description='保留草稿', lq_enabled=True, reason=reason))
            self.assertIn(f'data-reason="{reason}"', html)
            self.assertIn('明确原因', html); self.assertNotIn('<button', html)
        html = str(self.macros.empty_state('失败', lq_enabled=True, reason='error', action_label='重试', action_attrs={'data-retry': 'once'}))
        self.assertIn('data-retry="once"', html)

    def test_all_business_callers_explicitly_opt_in_after_s4_f_package(self):
        # S4 F package (manage-pages family) wired every remaining templates/manage/**
        # page_head caller to `lq_enabled=(lq_pilot_enabled or lq_family_enabled('manage-pages'))`,
        # on top of the 8 S3 pilot callers that stay gated by `lq_pilot_enabled` alone
        # (per runbook: do not re-gate the 9 pilot routes). Total caller count is unchanged;
        # every caller now passes lq_enabled explicitly.
        explicit, count = set(), 0
        for path in (ROOT / 'templates').rglob('*.html'):
            for call in self.env.parse(path.read_text(encoding='utf-8')).find_all(nodes.Call):
                if isinstance(call.node, nodes.Name) and call.node.name == 'page_head':
                    count += 1
                    if any(arg.key == 'lq_enabled' for arg in call.kwargs): explicit.add(path.relative_to(ROOT / 'templates').as_posix())
        self.assertTrue(PILOTS.issubset(explicit))
        self.assertEqual(39, count)
        self.assertEqual(39, len(explicit))

    def test_shell_switch_has_exactly_one_controller_and_each_header_node(self):
        old, pilot = self.render_shell(), self.render_shell(True)
        self.assertIn('const initManageSidebar =', old)
        self.assertNotIn('js/manage_lq_pilot.js', old)
        self.assertNotIn('data-lq-manage-sidebar', old)
        self.assertIn('js/manage_lq_pilot.js', pilot)
        self.assertNotIn('const initManageSidebar =', pilot)
        self.assertNotIn('const REDUCED_MOTION_QUERY =', pilot)
        self.assertNotIn('app-topbar-menu', pilot)
        for html in (old, pilot):
            tags = Tags(html)
            for identity in ('sidebar', 'manageNav', 'manageNavSearch', 'manageNavEmpty', 'sidebarCollapseBtn', 'owned-action', 'retained-input', 'retained-frame', 'content-form'):
                self.assertEqual(1, len(tags.ids(identity)), identity)
            self.assertIn('window.handleFormSubmit', html)
            self.assertEqual('action-form', tags.ids('external-submit')[0][1]['form'])
            self.assertTrue(any(tag == 'fieldset' and 'disabled' in attrs for tag, attrs in tags.tags))
        self.assertIn('data-lq-manage-identity="teacher:73"', pilot)

    def test_nav_uses_same_permission_filtered_canonical_links(self):
        for admin in (False, True):
            old, pilot = self.render_shell(admin=admin), self.render_shell(True, admin=admin)
            def nav_links(html):
                return [a['href'] for tag, a in Tags(html).tags if tag == 'a' and 'manage-nav-item' in a.get('class', '').split()]
            self.assertEqual(nav_links(old), nav_links(pilot))
            self.assertEqual(admin, 'data-nav-domain="admin"' in pilot)
            self.assertNotIn('/manage/teaching/workflow', pilot)
            self.assertIn('data-explain-text=', pilot)

    def test_embedded_keeps_single_document_height_bridge_without_shell_or_adapter(self):
        for pilot in (False, True):
            html = self.render_shell(pilot, embedded=True); tags = Tags(html)
            self.assertNotIn('js/manage_lq_pilot.js', html)
            self.assertEqual([], tags.ids('sidebar'))
            self.assertEqual([], tags.ids('retained-frame'))
            self.assertEqual(1, len(tags.ids('content-form')))
            self.assertIn("type: 'manage-embed-height'", html)
            self.assertIn('window.location.origin', html)
            self.assertIn('window.handleFormSubmit', html)


if __name__ == '__main__':
    unittest.main()
