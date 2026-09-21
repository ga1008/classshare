"""Real Jinja rendering of both Profile shells; no app, HTTP or DB is opened.

Run through tools/test_backend.py so optional application imports in the route
selection check cannot load dotenv credentials or connect to PostgreSQL.
"""
from contextlib import nullcontext
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, nodes
from markupsafe import Markup
from starlette.requests import Request

from classroom_app.lq import lq_props
from classroom_app.lq_dialogs import lq_dialog_props
from classroom_app.services.manage_nav_service import build_manage_nav


ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ('overview', 'portfolio', 'signatures', 'settings', 'security', 'notifications', 'private', 'email')


def template_environment():
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True)
    env.filters['datetime_format'] = lambda value: str(value)
    env.globals.update(
        lq_props=lq_props, lq_dialog_props=lq_dialog_props,
        asset_url=lambda value: '/static/' + value,
        static_asset_revision=lambda: 'profile-contract',
        vite_entry_tags=lambda entry: Markup('<script type="module" src="/fixture-vite/' + entry + '"></script>'),
        resolve_user_ui_preferences=lambda *_: {'enabled': True, 'available': True, 'palette_key': 'indigo',
            'appearance': 'auto', 'glass': 'tinted', 'version': 3, 'context_token': 'synthetic-profile-context', 'presets': []},
        lq_family_enabled=lambda *_: False,
        site_record={},
    )
    return env


def profile_context(role, section, *, empty=False):
    """Deterministic view-model, including nonempty loop/falsey/escaped values."""
    in_shell = role == 'teacher'
    user = {'id': 73, 'role': role, 'name': '合成 <姓名>', 'email': 'profile@example.test', 'is_super_admin': False}
    profile = {**user, 'nickname': '身份 & 昵称', 'display_role': '角色 <说明>', 'role_label': '教师' if in_shell else '学生',
               'class_name': '' if in_shell else '合成班级', 'student_id_number': '' if in_shell else 'S-073',
               'avatar_url': '/api/profile/avatar?role=' + role + '&user_id=73', 'today_mood': '保持好奇',
               'homepage_url': 'https://example.test/?a=1&b=2', 'phone': '10000', 'wechat': 'fixture-wechat',
               'qq': '10001', 'description': '第一行\n第二行 <不应成为标签>', 'password_updated_at': '2026-09-01T08:00:00',
               'completion': {'percent': 0, 'completed': 0, 'total': 6}}
    if empty:
        profile.update(email='', today_mood='', homepage_url='', password_updated_at='')
    level = {'theme': 'mortal', 'short_name': '入门', 'level_name': '合成境界'}
    cultivation = {'highest_level': level, 'score': 0, 'progress_percent': 0, 'sect_level_label': '合成学堂',
                   'course_count': 1, 'breakthrough_ready': False, 'next_stage_name': '下一阶段',
                   'generating_stage_exam': False, 'rank_notice': '零分是真实数值', 'breakthrough_course_count': 0,
                   'certificate_count': 0, 'courses': [{'class_offering_id': 41, 'class_name': '合成班级',
                   'teacher_name': '合成教师', 'sect_name': '合成学堂', 'course_name': '课程 <标题>',
                   'current_level': level, 'score': 0, 'progress_percent': 0, 'eligible_stage': None,
                   'next_stage': {'name': '下一阶段'}, 'stages': [{'status': 'locked', 'short_name': '未达成', 'progress_percent': 0}]}]}
    overview = {'headline': '合成个人总览', 'metric_cards': [{'label': '真实零值', 'value': 0, 'note': '没有省略的 0'}],
                'charts': [{'id': 'fixture-chart', 'title': '合成图表', 'values': [0, None, 2]}],
                'recent_items': [{'type': '通知', 'title': '待办 <标题>', 'subtitle': '保留消息链接', 'is_unread': True,
                                  'href': '/profile?section=notifications#profile-message-center', 'created_at': '2026-09-01T08:00:00'}],
                'security_summary': {'total_logins': 0, 'last_login': {'logged_at': '2026-09-01T08:00:00',
                                     'device_label': '合成设备', 'ip_address': '127.0.0.1'}},
                'cultivation': None if in_shell else cultivation}
    item = {'id': 21, 'artifact_label': '作品', 'teacher_recommended': True, 'featured': True,
            'href': '/blog/posts/31?source=profile', 'title': '作品 <标题>', 'summary': '首行\n第二行 & 摘要',
            'student_reflection': '保留换行\n复盘', 'evidence_notes': '证据 <内容>', 'visibility': 'teacher',
            'visibility_label': '教师可见', 'ability_tags': ['协作'], 'course_name': '合成课程', 'score_label': '0 分'}
    portfolio = {'title': '合成成长档案', 'subtitle': '作品与证据', 'next_action': '继续整理',
                 'summary': {'item_count': 1, 'reflection_count': 1, 'featured_count': 1, 'candidate_count': 1},
                 'stats': [{'value': 0, 'label': '零值统计', 'hint': '保留零'}],
                 'candidates': [{'artifact_label': '文章', 'recommended_reason': '合成推荐', 'title': '候选作品',
                                 'course_name': '合成课程', 'score_label': '0 分', 'source_type': 'blog_post', 'source_id': 31}],
                 'items': [item], 'featured_items': [item], 'visibility_options': [{'value': 'teacher', 'label': '教师可见'}],
                 'ability_options': ['协作', '表达'], 'abilities': [{'label': '协作', 'value': 0, 'evidence': '合成证据', 'percent': 0}],
                 'timeline': [{'importance': 'normal', 'href': '/blog/posts/31', 'label': '成果', 'title': '合成作品',
                               'description': '时间线说明', 'occurred_at': '2026-09-01T08:00:00'}]}
    if empty:
        overview.update(charts=[], recent_items=[], security_summary=None, cultivation=None)
        for key in ('candidates', 'items', 'featured_items', 'abilities', 'timeline'):
            portfolio[key] = []
        portfolio['summary'] = dict.fromkeys(portfolio['summary'], 0)
    # These are view models, not permission enforcement: route/service tests own
    # role normalization. Both templates are intentionally exercised for all old
    # sections, including branches which authorized routes normally normalize.
    nav = [{'section': name, 'href': ('/manage/me' if name == 'overview' else '/manage/me/' + name) if in_shell
            else '/profile?section=' + name, 'label': name, 'short_label': name, 'active': section == name,
            'badge': 2 if name == 'notifications' else None} for name in SECTIONS]
    context = {'profile': profile, 'overview': overview, 'portfolio': portfolio, 'nav_items': nav,
               'active_section': section, 'notification_unread_count': 2, 'private_unread_count': 0}
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'http', 'server': ('testserver', 80),
                       'path': '/manage/me' if in_shell else '/profile', 'root_path': '', 'query_string': b'', 'headers': []})
    return {**context, 'profile_context': deepcopy(context), 'request': request, 'user_info': user,
            'profile_in_shell': in_shell, 'profile_section_base': '/manage/me/' if in_shell else '/profile?section=',
            'initial_tab': 'private_message' if section == 'private' else 'grading',
            'initial_contact': 'teacher:91', 'initial_scope': 41, 'page_title': '个人中心', 'embedded_mode': False,
            'manage_nav': build_manage_nav(user, 'teacher_profile', is_super_admin=False), 'lq_pilot_enabled': False}


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags, self.profile_tags, self.text, self.contents = [], [], [], {}
        self._profile_sections = 0
        self._capture = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'section' and ('data-profile-root' in attrs or self._profile_sections):
            self._profile_sections += 1
        if self._profile_sections:
            self.profile_tags.append((tag, attrs))
        if tag in ('textarea', 'script') and attrs.get('id'):
            self._capture = attrs['id']
            self.contents[self._capture] = ''

    def handle_data(self, data):
        self.text.append(data)
        if self._capture:
            self.contents[self._capture] += data

    def handle_endtag(self, tag):
        if tag == 'section' and self._profile_sections:
            self._profile_sections -= 1
        if tag in ('textarea', 'script'):
            self._capture = None

    def by_id(self, identity):
        return [(tag, attrs) for tag, attrs in self.tags if attrs.get('id') == identity]

    def with_attr(self, name):
        return [(tag, attrs) for tag, attrs in self.tags if name in attrs]


class ProfileTemplateContractTests(unittest.TestCase):
    def setUp(self):
        self.env = template_environment()

    def render(self, role, section, *, empty=False):
        context = profile_context(role, section, empty=empty)
        name = 'manage/profile.html' if role == 'teacher' else 'profile.html'
        html = self.env.get_template(name).render(**context)
        return html, Document(html), context

    def test_all_old_sections_render_once_in_both_real_parent_shells(self):
        for role in ('student', 'teacher'):
            for section in SECTIONS:
                for empty in (False, True):
                    with self.subTest(role=role, section=section, empty=empty):
                        html, doc, context = self.render(role, section, empty=empty)
                        for tag in ('html', 'body'):
                            self.assertEqual(1, sum(name == tag for name, _ in doc.tags), tag)
                        main_class = 'manage-main' if role == 'teacher' else 'main-content'
                        self.assertEqual(1, sum(name == 'main' and main_class in attrs.get('class', '').split() for name, attrs in doc.tags))
                        self.assertEqual(1, len(doc.with_attr('data-profile-root')))
                        self.assertEqual(1, len(doc.by_id('profile-context-json')))
                        self.assertEqual(context['profile_context'], json.loads(doc.contents['profile-context-json']))
                        self.assertEqual(1, len(doc.by_id('sidebar')) if role == 'teacher' else
                                         sum('app-topbar' in attrs.get('class', '').split() for _, attrs in doc.tags))
                        self.assertEqual(role == 'student', any('profile-nav' in attrs.get('class', '').split() for _, attrs in doc.tags))
                        scripts = [attrs['src'] for tag, attrs in doc.tags if tag == 'script' and 'src' in attrs]
                        for suffix in ('js/profile.js', 'js/echarts.min.js', 'js/auth.js', 'js/ls_date_picker.js', 'js/user_ui_preferences.js'):
                            self.assertEqual(1, sum(src.endswith(suffix) for src in scripts), suffix)
                        self.assertEqual(section == 'signatures', any(src.endswith('js/profile_signatures.js') for src in scripts))
                        message = section in ('notifications', 'private')
                        for island in ('message-center-page', 'message-center-workspace-sync'):
                            self.assertEqual(int(message), sum(attrs.get('data-lanshare-island') == island for _, attrs in doc.tags))
                            self.assertEqual(int(message), sum(src.endswith('/' + island + '.tsx') for src in scripts))
                        self.assertFalse(any(src.endswith('/js/message_center.js') for src in scripts))
                        self.assertIn('合成 &lt;姓名&gt;', html)
                        self.assertNotIn('<姓名>', html)

    def test_settings_keep_original_form_fields_and_role_specific_identity_hooks(self):
        for role in ('student', 'teacher'):
            with self.subTest(role=role):
                _, doc, _ = self.render(role, 'settings')
                self.assertEqual(1, len(doc.by_id('profile-basic-form')))
                expected = {'nickname', 'email', 'phone', 'wechat', 'qq', 'homepage_url'}
                if role == 'teacher': expected.add('description')
                self.assertEqual(expected, {attrs['name'] for tag, attrs in doc.profile_tags if tag in ('input', 'textarea') and 'name' in attrs})
                self.assertIn('readonly', doc.by_id('profile-name')[0][1])
                self.assertEqual(int(role == 'student'), len(doc.by_id('profile-student-id')))
                self.assertEqual(int(role == 'teacher'), len(doc.by_id('profile-identity-editor')))
                if role == 'teacher': self.assertEqual('第一行\n第二行 <不应成为标签>', doc.contents['profile-description'])
                self.assertEqual('image/png,image/jpeg,image/gif', doc.by_id('profile-avatar-input')[0][1]['accept'])

    def test_password_and_email_forms_keep_native_constraints_and_no_cross_section_forms(self):
        for role in ('student', 'teacher'):
            _, doc, _ = self.render(role, 'security')
            self.assertEqual(1, len(doc.by_id('profile-password-form')))
            for identity, field in (('profile-current-password', 'current_password'), ('profile-new-password', 'new_password'), ('profile-confirm-password', 'confirm_password')):
                attrs = doc.by_id(identity)[0][1]
                self.assertEqual(field, attrs['name']); self.assertEqual('password', attrs['type']); self.assertIn('required', attrs)
            self.assertEqual([], doc.by_id('profile-basic-form'))
            _, email_doc, _ = self.render(role, 'email')
            self.assertEqual(int(role == 'teacher'), len(email_doc.by_id('profile-email-form')))
        attrs = email_doc.by_id('profile-email-smtp-password')[0][1]
        self.assertNotIn('value', attrs); self.assertNotIn('required', attrs)
        self.assertEqual({'smtp', 'imap'}, {attrs['data-profile-email-test'] for _, attrs in email_doc.with_attr('data-profile-email-test')})

    def test_portfolio_preserves_live_form_names_anchors_and_textarea_newlines(self):
        _, doc, _ = self.render('student', 'portfolio')
        for anchor in ('candidates', 'collection', 'featured', 'abilities', 'timeline'):
            self.assertEqual(1, len(doc.by_id('portfolio-' + anchor)))
        form = doc.with_attr('data-portfolio-item-form')
        self.assertEqual(1, len(form)); self.assertEqual('21', form[0][1]['data-item-id'])
        self.assertEqual('首行\n第二行 & 摘要', doc.contents['portfolio-summary-21'])
        self.assertEqual('保留换行\n复盘', doc.contents['portfolio-reflection-21'])
        self.assertEqual({'title', 'visibility', 'summary', 'reflection', 'ability_tags', 'evidence_notes', 'featured'},
                         {attrs['name'] for tag, attrs in doc.profile_tags if tag in ('input', 'select', 'textarea') and 'name' in attrs})
        self.assertEqual('blog_post', doc.with_attr('data-portfolio-add')[0][1]['data-source-type'])

    def test_messages_keep_single_native_owner_inputs_context_and_existing_missing_attachment_boundary(self):
        for role in ('student', 'teacher'):
            for section in ('notifications', 'private'):
                with self.subTest(role=role, section=section):
                    _, doc, _ = self.render(role, section)
                    root = doc.by_id('profile-message-center')
                    self.assertEqual(1, len(root)); attrs = root[0][1]
                    self.assertEqual(section, attrs['data-message-center-mode'])
                    self.assertEqual('teacher:91', attrs['data-initial-contact']); self.assertEqual('41', attrs['data-initial-scope'])
                    self.assertEqual('private_message' if section == 'private' else 'grading', attrs['data-initial-tab'])
                    for identity in ('tabs', 'search', 'filter', 'mark-read', 'feed', 'private-panel', 'contact-search', 'contact-select', 'contact-current',
                                     'block-list', 'block-count', 'conversation-header', 'conversation-body', 'compose-form', 'compose-input', 'editor-toolbar', 'emoji-trigger'):
                        self.assertEqual(1, len(doc.by_id('message-center-' + identity)), identity)
                    self.assertEqual(1, len(doc.with_attr('data-send-button')))
                    self.assertEqual('submit', doc.with_attr('data-send-button')[0][1]['type'])
                    self.assertNotIn('name', doc.by_id('message-center-compose-input')[0][1])
                    # C2 restored the attachment port: exactly one hidden file
                    # input with an accessible name and one hidden preview node.
                    file_inputs = doc.by_id('message-center-file-input')
                    self.assertEqual(1, len(file_inputs))
                    self.assertEqual('file', file_inputs[0][1].get('type'))
                    self.assertIn('hidden', file_inputs[0][1])
                    self.assertTrue(file_inputs[0][1].get('aria-label'))
                    self.assertEqual(1, len(doc.by_id('message-center-attachment-preview')))

    def test_signature_placeholder_styles_and_scripts_stay_conditional(self):
        for role in ('student', 'teacher'):
            html, doc, _ = self.render(role, 'signatures')
            self.assertEqual(1, len(doc.with_attr('data-signature-app')))
            self.assertIn('正在读取签名数据', html)
            self.assertEqual(1, html.count('.psig-shell{display:grid;gap:16px}'))
            self.assertEqual(1, sum(attrs.get('href') == '/static/css/signature_scope_fields.css' for _, attrs in doc.tags))
            other, _, _ = self.render(role, 'overview')
            self.assertNotIn('.psig-shell{display:grid;gap:16px}', other)

    def test_partial_dispatch_is_fixed_and_shells_have_literal_parents(self):
        # Structural assertions complement real rendered contracts above; they
        # prevent request-controlled template paths or reintroducing dual extends.
        for path, parent in (('profile.html', 'base_navbar.html'), ('manage/profile.html', 'manage/layout.html')):
            tree = self.env.parse((ROOT / 'templates' / path).read_text(encoding='utf-8'))
            extends = list(tree.find_all(nodes.Extends))
            self.assertEqual(1, len(extends)); self.assertIsInstance(extends[0].template, nodes.Const)
            self.assertEqual(parent, extends[0].template.value)
        tree = self.env.parse((ROOT / 'templates/partials/profile/body.html').read_text(encoding='utf-8'))
        for include in tree.find_all(nodes.Include):
            self.assertIsInstance(include.template, nodes.Const)

    def test_route_selects_shell_without_altering_context_or_business_parameters(self):
        from classroom_app.routers import profile as route
        from classroom_app.routers.ui_parts import common
        template_service = Jinja2Templates(env=self.env)
        for role in ('student', 'teacher'):
            for section in SECTIONS:
                with self.subTest(role=role, section=section):
                    context = profile_context(role, section)
                    with patch.object(route, 'templates', template_service), \
                         patch.object(route, 'get_db_connection', return_value=nullcontext(object())), \
                         patch.object(route, 'build_profile_page_context', return_value=context['profile_context']) as build, \
                         patch.object(common, '_build_manage_template_context', return_value={'manage_nav': context['manage_nav'], 'embedded_mode': False}):
                        response = route._render_profile(context['request'], context['user_info'], section=section,
                            tab='private_message', contact='teacher:91', scope=41, in_shell=role == 'teacher')
                    self.assertEqual('manage/profile.html' if role == 'teacher' else 'profile.html', response.template.name)
                    self.assertEqual(section, response.context['active_section'])
                    self.assertEqual('teacher:91', response.context['initial_contact']); self.assertEqual(41, response.context['initial_scope'])
                    self.assertEqual('all' if section == 'notifications' else 'private_message', response.context['initial_tab'])
                    self.assertEqual(1, build.call_count)
                    self.assertEqual(1, len(Document(response.body.decode()).with_attr('data-profile-root')))

    def test_real_service_section_fallbacks_still_render_the_correct_thin_shell(self):
        from classroom_app.routers import profile as route
        from classroom_app.routers.ui_parts import common
        from classroom_app.services import profile_service as service
        template_service = Jinja2Templates(env=self.env)
        for role, requested, expected in (
            ('student', 'email', 'settings'), ('teacher', 'portfolio', 'overview'),
            ('teacher', 'signatures', 'overview'), ('student', '../../settings', 'overview'),
            ('teacher', 'unregistered', 'overview'),
        ):
            with self.subTest(role=role, requested=requested):
                context = profile_context(role, expected)
                with patch.object(route, 'templates', template_service), \
                     patch.object(route, 'get_db_connection', return_value=nullcontext(object())), \
                     patch.object(service, 'get_user_profile', return_value=context['profile']), \
                     patch.object(service, 'build_profile_overview', return_value=context['overview']), \
                     patch.object(service, 'build_profile_nav', return_value=context['nav_items']), \
                     patch.object(common, '_build_manage_template_context', return_value={'manage_nav': context['manage_nav'], 'embedded_mode': False}):
                    response = route._render_profile(context['request'], context['user_info'], section=requested,
                        tab='all', contact='', scope=None, in_shell=role == 'teacher')
                self.assertEqual(expected, response.context['active_section'])
                doc = Document(response.body.decode())
                self.assertEqual(expected, doc.with_attr('data-profile-root')[0][1]['data-active-section'])
                self.assertEqual(expected == 'settings', bool(doc.by_id('profile-basic-form')))
                self.assertFalse(doc.with_attr('data-signature-app'))


if __name__ == '__main__':
    unittest.main()
