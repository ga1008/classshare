"""Profile family routing/nav contracts; run only via tools/test_backend.py.

Real route dependencies and section normalization; profile aggregates are
replaced because these tests must not query an application database.
"""
from contextlib import ExitStack, nullcontext
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from classroom_app.routers import profile as route
from classroom_app.routers.ui_parts import common
from classroom_app.services import profile_service as service
from classroom_app.services.manage_nav_service import build_manage_nav


class ProfileFamilyRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'LANSHARE_LQ_FAMILIES': 'profile'}))
        self.stack.enter_context(patch.object(route, 'get_db_connection', return_value=nullcontext(object())))
        self.stack.enter_context(patch.object(service, '_notification_unread_count', return_value=3))
        self.stack.enter_context(patch.object(service, '_private_unread_count', return_value=0))
        self.stack.enter_context(patch.object(service, 'get_user_profile', side_effect=lambda conn, user: dict(user)))
        self.stack.enter_context(patch.object(service, 'build_profile_overview', return_value={'original': 'overview'}))
        self.stack.enter_context(patch.object(common, '_build_manage_template_context', return_value={}))
        self.rendered = []

        def render(request, name, context):
            self.rendered.append((name, context))
            return HTMLResponse(context['active_section'])

        self.stack.enter_context(patch.object(route.templates, 'TemplateResponse', side_effect=render))
        self.app = FastAPI()
        self.app.include_router(route.router)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def identity(self, role):
        self.app.dependency_overrides[route.get_current_user] = lambda: {'role': role, 'id': 73, 'name': '合成身份'}

    def test_appearance_uses_real_fixed_teacher_route_and_both_canonical_shells(self):
        for role, url, template in (
            ('student', '/profile?section=appearance', 'profile.html'),
            ('teacher', '/manage/me/appearance', 'manage/profile.html'),
        ):
            with self.subTest(role=role):
                self.identity(role)
                self.assertEqual(200, self.client.get(url).status_code)
                name, context = self.rendered[-1]
                self.assertEqual(template, name)
                self.assertEqual('appearance', context['active_section'])
                self.assertTrue(context['profile_lq_enabled'])
                self.assertEqual('appearance', next(item['section'] for item in context['nav_items'] if item['active']))
                self.assertEqual('/manage/me' if role == 'teacher' else '/profile?section=overview', context['profile_section_hrefs']['overview'])
                self.assertNotIn('/manage/me/overview', context['profile_section_hrefs'].values())
                self.assertEqual({'original': 'overview'}, context['overview'])

    def test_flag_off_hides_both_nav_entries_and_resolves_old_links_to_settings(self):
        os.environ['LANSHARE_LQ_FAMILIES'] = 'manage-shell,navbar-shell'
        for role, url in (('student', '/profile?section=appearance'), ('teacher', '/manage/me/appearance')):
            with self.subTest(role=role):
                self.identity(role)
                self.assertEqual(200, self.client.get(url).status_code)
                context = self.rendered[-1][1]
                self.assertEqual('settings', context['active_section'])
                self.assertFalse(context['profile_lq_enabled'])
                self.assertNotIn('appearance', [item['section'] for item in context['nav_items']])
        self.assertEqual('/manage/me/settings', route.shell_profile_href('appearance'))
        nav = build_manage_nav({'role': 'teacher', 'id': 73}, 'me_settings', is_super_admin=True)
        self.assertNotIn('me_appearance', nav['hrefs'])
        self.assertIn('me_settings', nav['hrefs'])

    def test_manage_nav_appearance_is_visible_for_regular_teacher_only_when_enabled(self):
        for admin in (False, True):
            nav = build_manage_nav({'role': 'teacher', 'id': 73}, 'me_appearance', is_super_admin=admin)
            self.assertEqual('/manage/me/appearance', nav['hrefs']['me_appearance'])
            self.assertEqual('me', next(domain['key'] for domain in nav['domains'] if domain['active']))

    def test_messages_and_profile_have_independent_server_flags_and_keep_recipient_scope(self):
        for role, url in (('student', '/profile?section=private'), ('teacher', '/manage/me/private')):
            self.identity(role)
            for families, profile_on, messages_on in (('', False, False), ('profile', True, False), ('messages', False, True), ('profile,messages', True, True)):
                with self.subTest(role=role, families=families):
                    os.environ['LANSHARE_LQ_FAMILIES'] = families
                    response = self.client.get(url + ('&' if '?' in url else '?') + 'contact=teacher%3A91&scope=41&tab=private_message')
                    self.assertEqual(200, response.status_code)
                    context = self.rendered[-1][1]
                    self.assertEqual(profile_on, context['profile_lq_enabled'])
                    self.assertEqual(messages_on, context['messages_lq_enabled'])
                    self.assertEqual('teacher:91', context['initial_contact'])
                    self.assertEqual(41, context['initial_scope'])
                    self.assertEqual('private_message', context['initial_tab'])

    def test_teacher_legacy_redirect_preserves_query_and_overview_is_registered(self):
        self.identity('teacher')
        for section, expected in (('appearance', '/manage/me/appearance'), ('overview', '/manage/me')):
            response = self.client.get(f'/profile?section={section}&tab=all&contact=teacher%3A91&scope=41&source=a%2Fb', follow_redirects=False)
            self.assertEqual(302, response.status_code)
            self.assertEqual(expected + '?tab=all&contact=teacher%3A91&scope=41&source=a%2Fb', response.headers['location'])
        self.assertEqual(200, self.client.get('/manage/me').status_code)
        self.assertEqual(404, self.client.get('/manage/me/overview').status_code)
        self.assertEqual(404, self.client.get('/manage/me/unknown-section').status_code)

    def test_new_teacher_route_keeps_real_teacher_dependency(self):
        self.identity('student')
        self.assertEqual(403, self.client.get('/manage/me/appearance').status_code)
        self.assertEqual([], self.rendered)
        self.assertEqual(200, self.client.get('/profile?section=appearance').status_code)

    def test_bootstrap_uses_same_family_flag_and_preserves_roles(self):
        for role in ('student', 'teacher'):
            self.identity(role)
            for enabled in (True, False):
                os.environ['LANSHARE_LQ_FAMILIES'] = 'profile' if enabled else ''
                response = self.client.get('/api/profile/bootstrap?section=appearance')
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual('appearance' if enabled else 'settings', body['active_section'])
                self.assertEqual(role, body['profile']['role'])
                self.assertEqual(enabled, any(item['section'] == 'appearance' for item in body['nav_items']))

    def test_existing_role_fallbacks_and_unknown_section_still_apply(self):
        for role, section, expected in (
            ('student', 'email', 'settings'), ('teacher', 'portfolio', 'overview'),
            ('teacher', 'signatures', 'overview'), ('student', '../../appearance', 'overview'),
        ):
            self.identity(role)
            response = self.client.get('/api/profile/bootstrap', params={'section': section})
            self.assertEqual(200, response.status_code)
            self.assertEqual(expected, response.json()['active_section'])
