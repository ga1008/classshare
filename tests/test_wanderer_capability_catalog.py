"""Discovery must deliver usable, authorized contracts to the next AI turn."""
import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI

from classroom_app.dependencies import get_current_teacher
from classroom_app.routers.social import router
from classroom_app.services import wanderer_capability_catalog as discovery
from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.agent_platform_route_capability import route_key
from tests import test_wanderer_social as social_support
from tests.wanderer_behavior_support import WandererWorldTestCase


def social_app():
    app = FastAPI()
    app.include_router(router)

    @app.get('/api/manage/secret')
    def management():
        return {}

    @app.get('/api/teacher/secret')
    def teacher(user=Depends(get_current_teacher)):
        return {}

    return app


class CatalogSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = social_app()
        cls.items = platform._catalog(cls.app, ())

    def test_actual_bilingual_multiword_queries_find_the_right_operations(self):
        cases = {
            'group members list': ('GET', '/api/social/groups/{group_id}'),
            'conversation messages list': ('GET', '/api/social/conversations/{key}/messages'),
            'send message': ('POST', '/api/social/conversations/{key}/messages'),
            'group message send': ('POST', '/api/social/conversations/{key}/messages'),
            '群聊 发送消息': ('POST', '/api/social/conversations/{key}/messages'),
            '群聊 消息 发送': ('POST', '/api/social/conversations/{key}/messages'),
            '会话 消息 列表 conversation messages list': ('GET', '/api/social/conversations/{key}/messages'),
            '好友 列表': ('GET', '/api/social/friends'),
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                found, mode = discovery.search(self.items, query)
                self.assertEqual(mode, 'all_terms')
                self.assertEqual((found[0]['method'], found[0]['path']), expected)

    def test_exact_alias_case_unicode_and_explicit_fallback(self):
        found, mode = discovery.search(self.items, ' ＳＯＣＩＡＬ．ＳＥＮＤ＿ＭＥＳＳＡＧＥ ')
        self.assertEqual(mode, 'exact')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['method'], 'POST')
        partial = discovery.page(self.items, query='unrecognizedconcept message')
        self.assertEqual(partial['search_mode'], 'partial')
        self.assertTrue(partial['items'])
        self.assertIn('部分匹配', partial['notice'])
        empty = discovery.page(self.items, query='zznothingmatchingzz')
        self.assertEqual(empty['items'], [])
        self.assertIn('query为空', empty['notice'])

    def test_search_and_exact_detail_never_reintroduce_filtered_routes(self):
        for method, path in [('GET', '/api/manage/secret'), ('GET', '/api/teacher/secret')]:
            self.assertFalse(any(item['path'] == path for item in self.items))
            page = discovery.page(self.items, capability=route_key(method, path))
            self.assertEqual(page['items'], [])
        self.assertEqual(discovery.page(self.items, query='secret')['items'], [])

    def test_every_mounted_capability_is_paged_once_without_callback_truncation(self):
        from classroom_app.app import app
        items = list(platform._catalog(app, ())) + runtime.CORE_CAPABILITIES
        keys, offset = [], 0
        for _ in range(len(items) + 1):
            page = discovery.page(items, offset=offset)
            receipt = {'command_id': 'catalog', 'operation_id': 'synthetic-uuid', 'status': 'succeeded',
                       'scope': {'kind': 'public'}, 'result': page}
            projected = runtime._callback_receipt(receipt)
            self.assertEqual(projected, receipt)
            self.assertLess(len(runtime.packed(receipt)), 4000)
            self.assertTrue(page['items'])
            keys.extend(item['key'] for item in page['items'])
            if page['next_offset'] is None:
                break
            self.assertGreater(page['next_offset'], offset)
            self.assertEqual(page['next']['args']['offset'], page['next_offset'])
            offset = page['next_offset']
        self.assertEqual(keys, [item['key'] for item in items])

    def test_oversized_first_schema_is_losslessly_readable_and_advances(self):
        enormous = {'key': 'synthetic.large', 'label': 'large schema', 'body_schema': {
            'properties': {f'field{i}': {'description': '汉字 "quoted" \\ path ' * 100} for i in range(10)}}}
        following = {'key': 'synthetic.next'}
        page = discovery.page([enormous, following])
        first = page['items'][0]
        self.assertEqual(first['detail']['args']['capability'], enormous['key'])
        self.assertIn('synthetic.next', [item['key'] for item in page['items']])
        pieces, offset = [], 0
        while True:
            detail = discovery.page([enormous, following], capability=first['key'], offset=offset)
            receipt = {'command_id': 'detail', 'operation_id': 'synthetic', 'result': detail}
            self.assertEqual(runtime._callback_receipt(receipt), receipt)
            pieces.append(detail['detail_json'])
            if detail['next_offset'] is None:
                break
            self.assertEqual(detail['next']['args']['offset'], detail['next_offset'])
            offset = detail['next_offset']
        self.assertEqual(json.loads(''.join(pieces)), enormous)

    def test_message_schema_uses_native_constants_and_native_argument_envelope(self):
        from classroom_app.services.social_message_service import BODY_MAX, BODY_FORMATS
        result = discovery.page(self.items, capability='social.send_message')['item']
        props = result['body_schema']['properties']
        self.assertEqual(props['body']['maxLength'], BODY_MAX)
        self.assertEqual(props['body_format']['enum'], list(BODY_FORMATS))
        self.assertEqual(result['parameters']['key']['in'], 'path')
        self.assertIn('body', result['args_example']['body'])
        self.assertIn('key', result['args_example']['path_params'])
        self.assertEqual(platform.resolve_wanderer_capability(self.app, result['key'])[0].body_schema,
                         result['body_schema'])


class CatalogCommunicationFlowTest(WandererWorldTestCase):
    ident = staticmethod(social_support.WandererSocialTest.ident)
    user_of = staticmethod(social_support.WandererSocialTest.user_of)
    call = social_support.WandererSocialTest.call
    befriend = social_support.WandererSocialTest.befriend
    make_group = social_support.WandererSocialTest.make_group

    def setUp(self):
        super().setUp()
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set, daily_ai_calls_key
        from classroom_app.time_utils import local_iso
        self.real = f'student:{self.student_pk}'
        self.atai = self.set_wanderer('atai', schedule_json=social_support.ALWAYS_AWAKE, behaviors_json={})
        self.lele = self.set_wanderer('lele', schedule_json=social_support.ALWAYS_AWAKE, behaviors_json={})
        self.enterContext(patch.object(social_support.social_push_service, 'push', social_support.PushRecorder()))
        self.enterContext(patch.object(social_support.social_push_service, 'is_app_process', return_value=True))
        with self.connect() as conn:
            social_support.grant_tier(conn, self.offering_id, self.student_pk, 3)
            conn.execute('UPDATE wanderer_agents SET status=\'active\',born_at=?,lifespan_weeks=10,life_penalty_weeks=0,daily_wake_limit=20', (local_iso(),))
            wanderer_runtime_setting_set(conn, 'autonomous_v2_enabled', '1')
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), '0')
            conn.commit()

    def test_discover_callback_then_real_group_send_uses_only_returned_contract(self):
        self._exercise_catalog_delivery()

    def test_detail_card_callback_then_real_group_send_uses_returned_schema(self):
        self._exercise_catalog_delivery(force_detail=True)

    def _exercise_catalog_delivery(self, *, force_detail=False):
        from tests.test_wanderer_runtime_v2 import Caller, command, decision
        gid, key = self.make_group(self.atai, self.lele)
        self.app = social_app()

        def send_from_discovery(payload):
            data = json.loads(payload['system_prompt'].split('<<<data>>>\n')[1].split('\n<<<end>>>')[0])
            receipt = data['receipts'][0]
            self.assertNotIn('truncated', receipt)
            result = receipt['result']
            discovered = result.get('item') or result['items'][0]
            if 'detail' in discovered:
                detail = discovered['detail']
                return decision([command(detail['capability'], detail['args'])], True)
            self.assertEqual(discovered['method'], 'POST')
            self.assertIn('social.send_message', discovered['aliases'])
            self.assertIn('body', discovered['body_schema']['properties'])
            args = copy.deepcopy(discovered['args_example'])
            args['path_params']['key'] = key
            args['body']['body'] = '从能力目录找到接口后，我来和大家继续聊。'
            return decision([command(discovered['key'], args)])

        # A larger real route schema must add a detail turn, never force the
        # caller to guess the body contract or drop the operation altogether.
        inventory = copy.deepcopy(platform._catalog(self.app, ()))
        if force_detail:
            selected = next(item for item in inventory if 'social.send_message' in item['aliases'])
            selected['description'] += '详' * max(0, 2400 - len(discovery._packed(selected)))
            self.assertGreater(len(discovery._packed(selected)), 2200)
            self.assertLess(len(discovery._packed(selected)), 2600)
        caller = Caller(decision([command('platform.capabilities', {'query': '群聊 发送消息'})], True),
                        *([send_from_discovery] * (2 if force_detail else 1)))
        with patch.object(platform, '_app', return_value=self.app), patch.object(platform, '_catalog', return_value=inventory):
            result = asyncio.run(runtime.wake_wanderer(self.connect, self.atai['id'], source='test',
                event_key=self.id(), context={'scope': {'kind': 'conversation', 'key': key}},
                ai_caller=caller, force=True))
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(len(caller.calls), 3 if force_detail else 2)
        self.assertEqual(result['receipts'][-1]['status'], 'succeeded', result)
        with self.connect() as conn:
            rows = conn.execute("SELECT body FROM social_messages WHERE conversation_key=? AND sender_identity=? AND message_type='text'",
                                (key, self.ident(self.atai))).fetchall()
            self.assertEqual([row['body'] for row in rows], ['从能力目录找到接口后，我来和大家继续聊。'])
