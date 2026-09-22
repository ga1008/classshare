"""S4 shell composition contracts: real Jinja, no application/HTTP/database.

The synthetic Profile view model is shared with its frozen C0 rendering gate.
All parent templates, partials, macros, icons and shell props are real sources.
"""
from collections import Counter
from html.parser import HTMLParser
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, DictLoader
from starlette.requests import Request

from classroom_app.lq_migration import lq_family_enabled
from tests.test_profile_template_contract import profile_context, template_environment


ACTION_MARKUP = '''
<form id="retained-form" method="post" action="/fixture/save?scope=7" enctype="multipart/form-data" data-original-form="yes">
  <label for="retained-input">草稿</label><input id="retained-input" name="draft" value="{{ draft }}" required>
  <fieldset disabled><button id="retained-disabled" type="submit" name="intent" value="disabled" disabled aria-disabled="true">不可操作</button></fieldset>
  <button id="retained-submit" type="submit" name="intent" value="save" formaction="/fixture/save?scope=7&amp;mode=manual" formmethod="post">保存</button>
</form>
<iframe id="retained-frame" title="只读预览" src="/fixture/preview?scope=7&amp;mode=readonly" loading="lazy" sandbox="allow-same-origin" data-original-frame="yes"></iframe>
'''
BODY_MARKUP = '''<h1 id="retained-heading">正文</h1><textarea id="business-draft" name="business_draft" form="retained-form">首行\n第二行 &amp; 草稿</textarea><button id="external-submit" type="submit" form="retained-form" name="intent" value="external">外部保存</button>'''
FIXTURES = {
    'navbar-contract.html': '''{% extends "base_navbar.html" %}
{% block title %}<em>{{ fixture_title }}</em>{% endblock %}
{% block body_class %}custom-page existing-role{% endblock %}
{% block navbar_actions %}''' + ACTION_MARKUP + '''{% endblock %}
{% block main_content %}''' + BODY_MARKUP + '''{% endblock %}''',
    'manage-contract.html': '''{% extends "manage/layout.html" %}
{% block body_class %}custom-manage existing-role{% endblock %}
{% block header_actions %}''' + ACTION_MARKUP + '''{% endblock %}
{% block content %}''' + BODY_MARKUP + '''{% endblock %}''',
}
PRESETS = [{'key': key, 'name': key} for key in ('teal', 'indigo', 'sky', 'mint', 'violet', 'rose')]


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.text = tag, dict(attrs), parent, ''

    def ancestors(self):
        node = self.parent
        while node:
            yield node
            node = node.parent


class Document(HTMLParser):
    """Inspect authored nesting, including nodes inside native dialog panes."""
    VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1] if self.stack else None)
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node.text += data

    def attr(self, key, value=None):
        return [node for node in self.nodes if key in node.attrs and (value is None or node.attrs[key] == value)]

    def identity(self, value):
        matches = self.attr('id', value)
        if len(matches) != 1:
            raise AssertionError(f'Expected one #{value}, got {len(matches)}')
        return matches[0]

    def scripts(self, suffix):
        return [node for node in self.nodes if node.tag == 'script' and node.attrs.get('src', '').endswith(suffix)]


class NavbarShellRenderingTests(unittest.TestCase):
    def setUp(self):
        self.env = template_environment()
        self.env.loader = ChoiceLoader([DictLoader(FIXTURES), self.env.loader])
        self.env.globals['lq_family_enabled'] = lq_family_enabled
        self.env.globals['resolve_user_ui_preferences'] = lambda *_: {
            'enabled': True, 'available': True, 'palette_key': 'rose', 'appearance': 'dark',
            'glass': 'off', 'version': 4, 'context_token': 'synthetic-navbar-context', 'presets': PRESETS,
        }

    def render(self, template='navbar-contract.html', *, role='student', path='/fixture', families='', pilot=False, **extra):
        section = extra.pop('section', 'overview')
        context = profile_context(role, section)
        url = urlsplit(path)
        context.update(request=Request({'type': 'http', 'method': 'GET', 'scheme': 'http',
            'server': ('testserver', 80), 'path': url.path, 'root_path': '',
            'query_string': url.query.encode(), 'headers': []}), lq_pilot_enabled=pilot,
            fixture_title='标题 <img src=x onerror=alert(1)> & 原文', draft='原位 <草稿> & 0')
        context.update(extra)
        with patch.dict(os.environ, {}, clear=False):
            if families is None:
                os.environ.pop('LANSHARE_LQ_FAMILIES', None)
            else:
                os.environ['LANSHARE_LQ_FAMILIES'] = families
            html = self.env.get_template(template).render(**context)
        return html, Document(html)

    def assert_unique_document(self, doc):
        for tag in ('html', 'head', 'body'):
            self.assertEqual(1, sum(node.tag == tag for node in doc.nodes), tag)
        counts = Counter(node.attrs['id'] for node in doc.nodes if 'id' in node.attrs)
        self.assertEqual({}, {key: count for key, count in counts.items() if count > 1})

    def assert_single_preferences(self, doc):
        self.assertEqual(1, len(doc.attr('data-ui-preferences-details')))
        self.assertEqual(1, len(doc.attr('data-ui-palette-select')))
        self.assertEqual({'appearance', 'glass', 'backdrop'}, {node.attrs['data-ui-preference-select'] for node in doc.attr('data-ui-preference-select')})
        self.assertEqual(3, len(doc.attr('data-ui-preference-select')))
        self.assertEqual(['backdrop_color'], [node.attrs['data-ui-preference-input'] for node in doc.attr('data-ui-preference-input')])
        self.assertEqual(1, len(doc.scripts('js/user_ui_preferences.js')))

    def test_navbar_default_off_unknown_flags_and_missing_helper_retain_legacy(self):
        for role in ('student', 'teacher'):
            for families in (None, '', '0', '*', 'navbar', 'manage-shell', 'profile,messages'):
                with self.subTest(role=role, families=families):
                    html, doc = self.render(role=role, families=families)
                    self.assert_unique_document(doc)
                    self.assertFalse(doc.attr('data-lq-navbar-topbar'))
                    self.assertFalse(doc.scripts('js/navbar_lq.js'))
                    self.assertEqual(1, len([node for node in doc.nodes if 'navbar' in node.attrs.get('class', '').split()]))
                    self.assertIn('const topbarMenus =', html)
                    self.assertEqual(int(role == 'student'), len(doc.attr('data-app-bottomnav')))
        del self.env.globals['lq_family_enabled']
        self.assertFalse(self.render(families='navbar-shell')[1].attr('data-lq-navbar-topbar'))

    def test_role_navigation_custom_body_class_and_default_actions_have_one_owner(self):
        for role in ('student', 'teacher'):
            for template in ('navbar-contract.html', 'base_navbar.html'):
                with self.subTest(role=role, template=template):
                    html, doc = self.render(template, role=role, families='navbar-shell')
                    self.assert_unique_document(doc)
                    self.assertEqual(1, len(doc.attr('data-lq-navbar-topbar')))
                    self.assertEqual(1, len(doc.scripts('js/navbar_lq.js')))
                    module = doc.scripts('js/navbar_lq.js')[0]
                    self.assertEqual('module', module.attrs['type'])
                    self.assertEqual('render', module.attrs['blocking'])
                    self.assertTrue(any(node.tag == 'head' for node in module.ancestors()))
                    self.assertNotIn('const topbarMenus =', html)
                    self.assertEqual(1, len(doc.attr('data-lq-navbar-content')))
                    self.assert_single_preferences(doc)
                    pane = doc.identity('navbar-topbar--lq-actions')
                    self.assertEqual('dialog', pane.tag)
                    self.assertIn('open', pane.attrs)  # Authored fallback is reachable without its module.
                    self.assertEqual('navbar-topbar--lq-actions', doc.attr('data-lq-pane-open', 'actions')[0].attrs['aria-controls'])
                    links = {node.attrs['href'] for node in doc.nodes if node.tag == 'a' and pane in node.ancestors()}
                    expected = {'/learning-path', '/wrong-book', '/career-path', '/resume'} if role == 'student' else {'/manage'}
                    self.assertTrue(expected <= links)
                    self.assertEqual(role == 'student', '/learning-path' in links)
                    self.assertEqual(int(role == 'student'), len(doc.attr('data-navbar-dock')))
                    if template == 'navbar-contract.html':
                        self.assertEqual('custom-page existing-role', doc.nodes[[node.tag for node in doc.nodes].index('body')].attrs['class'])
                    else:
                        self.assertEqual(int(role == 'teacher'), len(doc.attr('href', '/manage/teaching')))

    def test_authored_actions_retain_forms_iframe_native_attributes_and_external_form_owner(self):
        for template, role, flag in (('navbar-contract.html', 'student', 'navbar-shell'),
                                     ('navbar-contract.html', 'teacher', 'navbar-shell'),
                                     ('manage-contract.html', 'teacher', 'manage-shell')):
            old = self.render(template, role=role)[1]
            _, new = self.render(template, role=role, families=flag)
            with self.subTest(template=template, role=role):
                self.assert_unique_document(new)
                for identity in ('retained-form', 'retained-input', 'retained-disabled', 'retained-submit',
                                 'retained-frame', 'business-draft', 'external-submit'):
                    before, after = old.identity(identity), new.identity(identity)
                    self.assertEqual((before.tag, before.attrs, before.text), (after.tag, after.attrs, after.text), identity)
                form = new.identity('retained-form')
                self.assertFalse(any(node.tag == 'form' for node in form.ancestors()))
                self.assertIn(form, new.identity('retained-submit').ancestors())
                self.assertIn('disabled', new.identity('retained-disabled').attrs)
                self.assertTrue(any(node.tag == 'fieldset' and 'disabled' in node.attrs for node in new.identity('retained-disabled').ancestors()))
                self.assertFalse(any(node.tag == 'form' for node in new.identity('retained-frame').ancestors()))
                self.assertEqual(form.attrs['id'], new.identity('external-submit').attrs['form'])
                self.assertEqual('首行\n第二行 & 草稿', new.identity('business-draft').text)
                self.assertEqual('原位 <草稿> & 0', new.identity('retained-input').attrs['value'])
                self.assertTrue(any(node.tag == 'dialog' for node in form.ancestors()))

    def test_titles_are_text_and_real_shell_icons_have_registered_paths(self):
        html, doc = self.render(families='navbar-shell')
        title = next(node for node in doc.nodes if 'lq-navbar-title' in node.attrs.get('class', '').split())
        self.assertEqual('标题 <img src=x onerror=alert(1)> & 原文', title.text)
        self.assertFalse(any(title in node.ancestors() for node in doc.nodes))
        self.assertNotIn('<img src=x', html)
        icons = [node for node in doc.nodes if node.tag == 'svg' and 'lq-icon' in node.attrs.get('class', '').split()]
        self.assertGreaterEqual(len(icons), 8)
        for icon in icons:
            self.assertEqual('true', icon.attrs.get('aria-hidden'))
            self.assertTrue(any(icon in node.ancestors() and node.tag in ('path', 'circle', 'rect', 'line', 'polyline', 'polygon') for node in doc.nodes))
        for node in doc.nodes:
            if node.tag in ('a', 'button', 'form'):
                self.assertFalse(any(parent.tag == node.tag for parent in node.ancestors()), node.attrs)

    def test_unknown_named_icon_does_not_inject_markup_or_remove_native_navigation(self):
        # The generated registry uses a fixed circle-help glyph for unknown
        # names; it never interpolates the name into SVG/HTML. Link text owns
        # the accessible name, including when an unregistered name is supplied.
        render = self.env.from_string('''{% from 'macros/lq/shells.html' import lq_dock %}
            {{ lq_dock('unknown-icon-dock', items=[{'key':'profile', 'label':'个人中心',
                'href':'/profile', 'icon':icon_name}], mode='navigation') }}''')
        html = render.render(icon_name='not-a-registered-icon')
        doc = Document(html)
        icons = [node for node in doc.nodes if node.tag == 'svg']
        self.assertEqual(1, len(icons))
        self.assertEqual('true', icons[0].attrs['aria-hidden'])
        link = doc.attr('href', '/profile')[0]
        self.assertEqual('个人中心', link.text.strip())
        self.assertEqual('a', link.tag)
        self.assertEqual(html, render.render(icon_name='x"><img src=x onerror=alert(1)>'))

    def test_profile_dock_uses_request_section_without_losing_message_context(self):
        for section in ('overview', 'portfolio', 'signatures', 'settings', 'security', 'notifications', 'private', 'email'):
            with self.subTest(section=section):
                path = f'/profile?section={section}&tab=private_message&contact=teacher%3A91&scope=41'
                _, doc = self.render('profile.html', families='navbar-shell', path=path, section=section)
                self.assert_unique_document(doc)
                dock = doc.identity('navbar-dock')
                current = [node for node in doc.attr('aria-current', 'page') if dock in node.ancestors()]
                expected = '/message-center' if section in ('notifications', 'private') else '/profile'
                self.assertEqual([expected], [node.attrs.get('href') for node in current])
                if section in ('notifications', 'private'):
                    self.assertEqual(1, len(doc.attr('data-message-center-app')))
                    root = doc.attr('data-message-center-app')[0]
                    self.assertEqual('teacher:91', root.attrs['data-initial-contact'])
                    self.assertEqual('41', root.attrs['data-initial-scope'])

    def test_dock_selects_only_matching_route_and_never_creates_teacher_dock(self):
        for path, expected in (('/dashboard', '/dashboard'), ('/', '/dashboard'),
                               ('/message-center', '/message-center'), ('/learning-path/41', '/learning-path'),
                               ('/profile?section=unknown', '/profile'), ('/profile/security', '/profile'),
                               ('/report-card', None), ('/profile-copy?section=private', None)):
            with self.subTest(path=path):
                _, doc = self.render(path=path, families='navbar-shell')
                dock = doc.identity('navbar-dock')
                active = [node.attrs.get('href') for node in doc.attr('aria-current', 'page') if dock in node.ancestors()]
                self.assertEqual([] if expected is None else [expected], active)
                self.assertEqual(4, len([node for node in doc.nodes if node.tag == 'a' and dock in node.ancestors()]))
                self.assertFalse(self.render(role='teacher', path=path, families='navbar-shell')[1].attr('data-app-bottomnav'))

    def test_report_pilot_and_family_combinations_render_one_topbar_with_separate_module_owners(self):
        card = {'summary': {'record_total': 0, 'overall_avg': None, 'top_band_count': 0, 'pending_total': 0},
                'selected_assessment_kind': '', 'selected_class_offering_id': 41, 'courses': [],
                'published_grades': [], 'personal_records': [], 'charts': []}
        for families in ('', 'manage-shell', 'navbar-shell', 'manage-shell,navbar-shell'):
            for pilot in (False, True):
                with self.subTest(families=families, pilot=pilot):
                    html, doc = self.render('report_card.html', families=families, pilot=pilot,
                                           path='/report-card', report_card=card)
                    self.assert_unique_document(doc)
                    navbar = 'navbar-shell' in families.split(',')
                    self.assertEqual(1, len([node for node in doc.nodes if 'app-topbar' in node.attrs.get('class', '').split()]))
                    self.assertEqual(int(navbar), len(doc.attr('data-lq-navbar-topbar')))
                    self.assertEqual(int(pilot and not navbar), len(doc.attr('data-lq-report-card-topbar')))
                    self.assertEqual(int(navbar), len(doc.scripts('js/navbar_lq.js')))
                    self.assertEqual(int(pilot), len(doc.scripts('js/report_card.js')))
                    self.assertFalse(doc.scripts('js/manage_lq_pilot.js'))
                    self.assertEqual(1, len(doc.attr('data-report-chart-data')))
                    self.assertEqual(1, len(doc.attr('data-app-bottomnav')))
                    if navbar or pilot:
                        self.assert_single_preferences(doc)
                        self.assertNotIn('const topbarMenus =', html)
                    self.assertIn('/report-card?assessment_kind=homework&amp;class_offering_id=41', html)

    def test_pilot_flag_on_other_paths_or_teacher_cannot_activate_report_shell(self):
        for role, path in (('student', '/profile'), ('student', '/report-card-copy'), ('teacher', '/report-card')):
            with self.subTest(role=role, path=path):
                _, doc = self.render(role=role, path=path, pilot=True)
                self.assertFalse(doc.attr('data-lq-report-card-topbar'))
                self.assertFalse(doc.attr('data-lq-navbar-topbar'))

    def test_manage_shell_flag_keeps_custom_body_and_embedded_mode_outside_new_shell(self):
        for families in ('', 'navbar-shell', 'manage-shell', 'manage-shell,navbar-shell'):
            for embedded in (False, True):
                with self.subTest(families=families, embedded=embedded):
                    _, doc = self.render('manage-contract.html', role='teacher', families=families, embedded_mode=embedded)
                    self.assert_unique_document(doc)
                    body = next(node for node in doc.nodes if node.tag == 'body')
                    self.assertIn('custom-manage', body.attrs['class'].split())
                    enabled = 'manage-shell' in families.split(',') and not embedded
                    self.assertEqual(enabled, 'lq-manage-shell' in body.attrs['class'].split())
                    self.assertEqual(int(enabled), len(doc.scripts('js/manage_lq_pilot.js')))
                    self.assertFalse(doc.scripts('js/navbar_lq.js'))
                    if enabled:
                        self.assert_single_preferences(doc)

    def test_both_real_homepages_have_one_preference_control_for_each_switch_mode(self):
        for role, template in (('student', 'dashboard.html'), ('teacher', 'dashboard_teacher.html')):
            for families in ('', 'navbar-shell', 'manage-shell', 'navbar-shell,manage-shell'):
                with self.subTest(role=role, families=families):
                    _, doc = self.render(template, role=role, families=families, path='/dashboard',
                        dashboard_initial_filter='all', dashboard_initial_search='', dashboard_theme=role,
                        dashboard_initial_visible_count=0, dashboard_initial_results_summary='',
                        dashboard_can_create_todo=True, dashboard_workspace={}, dashboard_empty_state={},
                        dashboard_filters=[], dashboard_semester_options=[], dashboard_quick_actions=[],
                        dashboard_domain_cards=[], class_offerings=[], dashboard_summary={},
                        dashboard_semester_calendar={}, cultivation_profile={},
                        dashboard_work_inbox={'items': [], 'sources': [], 'total': 0})
                    self.assert_unique_document(doc)
                    self.assert_single_preferences(doc)
                    self.assertEqual(1, len(doc.attr('data-dashboard-root')))


if __name__ == '__main__':
    unittest.main()
