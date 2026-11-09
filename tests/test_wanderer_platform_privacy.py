"""Private thoughts may use native owner-only services without publishing them."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI

from classroom_app.routers import resume_console, career_path
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services.agent_platform_route_capability import route_key
from classroom_app.services.wanderer_platform_audience import OWN_PRIVATE_WRITES, content_audience
from tests.test_wanderer_platform_execution import fixture_app
from tests.wanderer_test_support import WandererDbTestCase


class WandererPlatformPrivacyTests(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.app = fixture_app()
        self.app.router.routes = [route for route in self.app.router.routes
                                 if not getattr(route, 'path', '').startswith('/api/resume/')]
        self.app.include_router(resume_console.router)
        self.app.include_router(career_path.router)
        with self.connect() as conn:
            self.w = self.wanderer(conn, 'lele')
            conn.execute('DELETE FROM wanderer_runtime_leases WHERE wanderer_id=?', (self.w['id'],))
            event = runtime.enqueue_wake_event(conn, self.w['id'], 'privacy-test', uuid.uuid4().hex,
                                               {'scope': {'kind': 'private'}})
            self.run = runtime._claim(conn, event['event_id'])
            self.personal = resume_console.profile._ensure_personal_row(conn, self.w['student_id'])
            conn.execute('CREATE TABLE IF NOT EXISTS synthetic_owned_items(id INTEGER PRIMARY KEY,owner_id INTEGER,name TEXT,version INTEGER)')
            conn.execute('DELETE FROM synthetic_owned_items')
            conn.commit()

    def execute(self, method, path, args, ident=None):
        command = {'id': ident or uuid.uuid4().hex, 'capability': route_key(method, path), 'args': args}
        with patch.object(platform, '_app', return_value=self.app), patch.object(resume_console, 'get_db_connection', self.connect):
            return asyncio.run(runtime.execute_command(self.connect, self.run, command))

    def test_private_thought_updates_native_resume_with_original_version_guard(self):
        payload = {**self.personal, 'name': 'Synthetic', 'email': 'synthetic@example.invalid',
                   'expected_position': 'Synthetic private career intention'}
        result = self.execute('POST', '/api/resume/personal', {'body': payload})
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['scope']['kind'], 'private')
        self.assertEqual(result['scope']['audience'], 'self')
        with self.connect() as conn:
            actual = resume_console.profile.get_personal_info(conn, self.w['student_id'])
            self.assertEqual(actual['expected_position'], payload['expected_position'])
            self.assertEqual(actual['revision'], self.personal['revision'] + 1)
        stale = self.execute('POST', '/api/resume/personal', {'body': payload})
        self.assertEqual(stale['result']['result']['http_status'], 409, stale)
        self.assertNotEqual(stale['status'], 'succeeded')

    def test_unknown_public_destination_cannot_become_conversation_from_model_key(self):
        self.run['scope'] = {'kind': 'conversation', 'key': 'group:999', 'restricted_sources': True}
        result = self.execute('POST', '/api/owned-items', {'body': {'name': 'must not escape'}, 'key': 'group:999'})
        self.assertEqual(result['status'], 'denied', result)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM synthetic_owned_items').fetchone()[0], 0)

    def test_reviewed_destination_metadata_has_no_prefix_or_profile_fallback(self):
        for module, pairs in OWN_PRIVATE_WRITES.items():
            for method, path in pairs:
                with self.subTest(method=method, path=path):
                    operation, _ = platform.resolve_wanderer_capability(self.app, route_key(method, path))
                    self.assertEqual(content_audience(operation), 'own_private')
        for path in ('/api/resume/future-share', '/api/profile/mood', '/api/profile'):
            operation = SimpleNamespace(module='classroom_app.routers.resume_console', method='POST', path=path)
            self.assertNotEqual(content_audience(operation), 'own_private')
            self.assertEqual(runtime._platform_scope({'args': {'key': 'group:999'}}, operation=operation), {'kind': 'private'})
