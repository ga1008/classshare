"""Real ordinary-message transactions under verified AI authority, without a model/network."""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from classroom_app.services import social_message_service as social
from classroom_app.services import message_center_service as legacy
from classroom_app.services import wanderer_message_delivery_service as delivery
from classroom_app.services.social_common import dm_key, SocialError
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict
from classroom_app.services.wanderer_resource_service import snapshot
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso
from tests import test_wanderer_legacy_delivery as native_support
from tests.wanderer_behavior_support import WandererWorldTestCase


class WandererDeliveryGuardTest(WandererWorldTestCase):
    native_operation = native_support.WandererLegacyDeliveryTest.native_operation

    def setUp(self):
        super().setUp()
        self.a = self.set_wanderer('atai', daily_wake_limit=100)
        self.b = self.set_wanderer('lele', daily_wake_limit=100)
        self.c = self.set_wanderer('xiaoman', daily_wake_limit=100)
        self.user = build_wanderer_user_dict(self.a)
        self.sender, self.peer = f"student:{self.a['student_id']}", f"student:{self.b['student_id']}"
        self.key = dm_key(self.sender, self.peer)
        self.native_lock, self.native_source, self.native_fingerprint = Lock(), None, None
        from classroom_app.services import social_push_service
        self.enterContext(patch.object(social_push_service, 'push', return_value=0))
        self.enterContext(patch.object(social_push_service, 'is_app_process', return_value=True))
        with self.connect() as conn:
            from classroom_app.db.schema_wanderer_delivery import ensure_wanderer_delivery_schema
            ensure_wanderer_delivery_schema(conn)
            for table in ('wanderer_message_deliveries', 'wanderer_private_message_operations',
                          'wanderer_wake_events', 'wanderer_turns', 'wanderer_commands', 'wanderer_runtime_leases'):
                conn.execute(f'DELETE FROM {table}')
            wanderer_runtime_setting_set(conn, 'autonomous_v2_enabled', '1')
            for peer in (self.peer, f"student:{self.c['student_id']}"):
                conn.execute('INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) VALUES(?,?,?,?,?)',
                             (*sorted([self.sender, peer]), 'accepted', self.sender, local_iso()))
            conn.commit()

    def native_send(self, *, operation_id=None, key=None, body='这次见闻已经发送，我想听听你的想法。', **extra):
        operation_id = operation_id or str(uuid.uuid4())
        with self.native_operation(operation_id), self.connect() as conn:
            result = social.send_message(conn, self.user, key or self.key,
                                         {'body': body, 'client_nonce': operation_id, **extra})
            conn.commit()
            return result

    def legacy_send(self, *, operation_id=None, body='这次见闻已经发送，我想听听你的想法。'):
        with self.native_operation(operation_id or str(uuid.uuid4())), self.connect() as conn:
            result = legacy.create_private_message(conn, self.user, contact_identity=self.peer, content=body)
            conn.commit()
            return result

    def new_chain(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_runtime_leases SET lease_until='2000-01-01'")
            conn.execute("UPDATE wanderer_turns SET state='completed'")
            conn.commit()
        self.native_source = None

    def source(self):
        with self.native_operation(str(uuid.uuid4())):
            return self.native_source

    def normalize(self, operation_id, *, channel='social', receipt=None):
        from classroom_app.services.wanderer_platform_results import normalize_platform_result
        path = '/api/social/conversations/{key}/messages' if channel == 'social' else '/api/message-center/private/messages'
        args = {'path_params': {'key': self.key}, 'body': {'body': '这次见闻已经发送，我想听听你的想法。'}}
        if channel == 'legacy':
            args = {'body': {'contact_identity': self.peer, 'content': '这次见闻已经发送，我想听听你的想法。'}}
        with self.connect() as conn:
            return normalize_platform_result(conn, self.native_source,
                SimpleNamespace(path=path, method='POST', mutates=True), args,
                {'operation_id': operation_id, 'status': 'uncertain', 'result': {}, **(receipt or {})})

    def test_different_commands_and_same_operation_replay_have_one_delivery(self):
        operation_id = str(uuid.uuid4())
        with patch.object(social, '_wake_wanderers') as wake:
            first = self.native_send(operation_id=operation_id)
            duplicate = self.native_send()
            retry = self.native_send(operation_id=operation_id)
        self.assertEqual([first['id']] * 3, [first['id'], duplicate['id'], retry['id']])
        self.assertEqual(wake.call_count, 1)
        self.assertTrue(first['delivery']['created'])
        for result in (duplicate, retry):
            self.assertTrue(result['delivery']['reused'])
            self.assertFalse(result['delivery']['created'])
        with self.connect() as conn:
            self.assertEqual(snapshot(conn, self.a['id'], 'social_message')['used'], 3000000)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM social_messages').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_message_deliveries').fetchone()[0], 2)
        with self.assertRaises(ValueError):
            self.native_send(operation_id=operation_id, body='不同内容不可冒充同一次操作')

    def test_unrelated_event_reuses_only_until_new_peer_message_or_window_expires(self):
        first = self.native_send()
        self.new_chain()
        self.assertEqual(self.native_send()['id'], first['id'])
        with self.connect() as conn:
            social.send_message(conn, build_wanderer_user_dict(self.b), self.key, {'body': '我有一个新问题'})
            conn.commit()
        self.new_chain()
        second = self.native_send()
        self.assertNotEqual(second['id'], first['id'])
        self.new_chain()
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_message_deliveries SET created_at='2000-01-01'")
            conn.commit()
        self.assertNotEqual(self.native_send()['id'], second['id'])

    def test_callback_chain_remembers_original_send_even_after_recent_window(self):
        first = self.native_send()
        with self.connect() as conn:
            chain = conn.execute('SELECT chain_id FROM wanderer_turns WHERE turn_id=?', (self.native_source.turn_id,)).fetchone()[0]
            conn.execute("UPDATE wanderer_message_deliveries SET created_at='2000-01-01'")
            conn.commit()
        self.new_chain()
        self.source()
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_turns SET chain_id=? WHERE turn_id=?', (chain, self.native_source.turn_id))
            conn.commit()
        self.assertEqual(self.native_send()['id'], first['id'])

    def test_repeat_receipt_survives_exhausted_rate_and_communication_budget(self):
        from classroom_app.services.wanderer_resource_service import consume
        first = self.native_send()
        with self.connect() as conn:
            remaining = snapshot(conn, self.a['id'], 'social_message')['remaining']
            consume(conn, self.a['id'], 'social_message', remaining, 'fixture-exhaust')
            conn.commit()
        # Replaying a completed message is not a new rate-limited delivery.
        with patch.object(social, '_enforce_rate_limit', side_effect=AssertionError('must not apply to replay')):
            repeated = self.native_send()
        self.assertEqual(first['id'], repeated['id'])
        self.assertTrue(repeated['delivery']['reused'])

    def test_different_conversations_reply_targets_and_human_repeats_remain_possible(self):
        first = self.native_send()
        elsewhere = self.native_send(key=dm_key(self.sender, f"student:{self.c['student_id']}"))
        quoted = self.native_send(reply_to_id=first['id'])
        self.assertEqual(len({first['id'], elsewhere['id'], quoted['id']}), 3)
        # The same ordinary account path without verified AI authority behaves as before.
        with self.connect() as conn:
            one = social.send_message(conn, self.user, self.key, {'body': '普通会话重复文本', 'client_nonce': 'human-one'})
            two = social.send_message(conn, self.user, self.key, {'body': '普通会话重复文本', 'client_nonce': 'human-two'})
            conn.commit()
        self.assertNotEqual(one['id'], two['id'])
        self.assertNotIn('delivery', one)
        self.assertNotIn('delivery', two)

    def test_group_duplicates_are_scoped_to_the_actual_group(self):
        from classroom_app.services.social_group_service import create_group
        with self.connect() as conn:
            first_group = create_group(conn, self.user, '第一合成群', [self.peer])
            second_group = create_group(conn, self.user, '第二合成群', [self.peer])
            conn.commit()
        first = self.native_send(key=first_group['conversation_key'])
        replay = self.native_send(key=first_group['conversation_key'])
        second = self.native_send(key=second_group['conversation_key'])
        self.assertEqual(first['id'], replay['id'])
        self.assertNotEqual(first['id'], second['id'])

    def test_group_rejoin_and_left_membership_do_not_expose_old_receipts(self):
        from classroom_app.services.social_group_service import create_group
        from classroom_app.services.social_common import now_iso
        with self.connect() as conn:
            group = create_group(conn, self.user, '重新入群合成场景', [self.peer])
            conn.commit()
        key, operation_id = group['conversation_key'], str(uuid.uuid4())
        first = self.native_send(key=key, operation_id=operation_id)
        with self.connect() as conn:
            conn.execute('UPDATE social_group_members SET joined_at=? WHERE group_id=? AND member_identity=?',
                         (now_iso(), group['id'], self.sender))
            conn.commit()
            self.assertEqual(delivery.recent_deliveries(conn, self.a, {'kind': 'conversation', 'key': key})['items'], [])
        with self.assertRaises(ValueError):
            self.native_send(key=key, operation_id=operation_id)
        newer = self.native_send(key=key)
        self.assertNotEqual(first['id'], newer['id'])
        with self.connect() as conn:
            conn.execute('UPDATE social_group_members SET left_at=? WHERE group_id=? AND member_identity=?',
                         (now_iso(), group['id'], self.sender))
            conn.commit()
            self.assertEqual(delivery.recent_deliveries(conn, self.a, {'kind': 'conversation', 'key': key})['items'], [])
        with self.assertRaises(SocialError):
            self.native_send(key=key)

    def test_recalled_or_deleted_messages_cannot_be_reported_as_delivered(self):
        operation_id = str(uuid.uuid4())
        first = self.native_send(operation_id=operation_id)
        with self.connect() as conn:
            social.recall_message(conn, self.user, first['id'])
            conn.commit()
        self.assertEqual(self.normalize(operation_id)['status'], 'uncertain')
        self.assertEqual(self.normalize(operation_id, receipt={'status': 'observed_http_result',
            'result': {'http_status': 200, 'data': {'id': first['id']}}})['status'], 'uncertain')
        with self.assertRaises(ValueError):
            self.native_send(operation_id=operation_id)
        self.assertNotEqual(self.native_send()['id'], first['id'])
        private_op = str(uuid.uuid4())
        private = self.legacy_send(operation_id=private_op)
        with self.connect() as conn:
            conn.execute('DELETE FROM private_messages WHERE id=?', (private['message']['id'],))
            conn.commit()
        self.assertEqual(self.normalize(private_op, channel='legacy')['status'], 'uncertain')

    def test_current_acl_is_required_for_replay_and_receipt_recovery(self):
        operation_id = str(uuid.uuid4())
        self.native_send(operation_id=operation_id)
        with self.connect() as conn:
            conn.execute("UPDATE social_friendships SET status='deleted'")
            conn.commit()
        with self.assertRaises(SocialError):
            self.native_send(operation_id=operation_id)
        with self.assertRaises(SocialError):
            self.normalize(operation_id)

    def test_life_epoch_does_not_reuse_previous_life_messages(self):
        operation_id = str(uuid.uuid4())
        first = self.native_send(operation_id=operation_id)
        self.new_chain()
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_agents SET life_epoch=life_epoch+1,authority_version=authority_version+1 WHERE id=?', (self.a['id'],))
            conn.commit()
        self.assertNotEqual(self.native_send()['id'], first['id'])
        with self.assertRaises(ValueError):
            self.native_send(operation_id=operation_id)

    def test_native_concurrent_distinct_operations_only_charge_and_wake_once(self):
        self.source()
        ready = Barrier(2)
        def send(_):
            ready.wait(timeout=10)
            return self.native_send()
        with patch.object(social, '_wake_wanderers') as wake, ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertCountEqual([r['delivery']['reused'] for r in results], [False, True])
        self.assertEqual(wake.call_count, 1)
        with self.connect() as conn:
            self.assertEqual(snapshot(conn, self.a['id'], 'social_message')['used'], 3000000)

    def test_legacy_concurrent_distinct_operations_deliver_and_charge_once(self):
        self.source()
        ready = Barrier(2)
        def send(_):
            ready.wait(timeout=10)
            return self.legacy_send()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(results[0]['message']['id'], results[1]['message']['id'])
        self.assertCountEqual([r.get('replayed', False) for r in results], [False, True])
        with self.connect() as conn:
            self.assertEqual(snapshot(conn, self.a['id'], 'social_message')['used'], 3000000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_social_inbox WHERE source='legacy'").fetchone()[0], 1)

    def test_lost_http_receipt_recovers_only_committed_native_and_legacy_rows(self):
        native_op, legacy_op = str(uuid.uuid4()), str(uuid.uuid4())
        native = self.native_send(operation_id=native_op)
        private = self.legacy_send(operation_id=legacy_op)
        for op, channel, mid in ((native_op, 'social', native['id']), (legacy_op, 'legacy', private['message']['id'])):
            result = self.normalize(op, channel=channel)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['delivery']['message_id'], mid)
            self.assertTrue(result['delivery']['recovered_from_committed_message'])
            self.assertEqual(self.normalize(str(uuid.uuid4()), channel=channel)['status'], 'uncertain')

    def test_real_routes_surface_reuse_receipts_for_both_channels(self):
        from classroom_app.routers.social import router as social_router
        from classroom_app.routers.message_center import router as legacy_router
        from classroom_app.services import wanderer_platform_service as platform
        app = FastAPI(); app.include_router(social_router); app.include_router(legacy_router)
        source = self.source()
        for capability, args in (
            ('social.send_message', {'path_params': {'key': self.key}, 'body': {'body': '本次回调只需发送一次'}}),
            ('legacy.send_message', {'body': {'contact_identity': self.peer, 'content': '本次回调只需发送一次'}}),
        ):
            async def execute(operation_id):
                return await platform.execute_platform_command(wanderer_id=self.a['id'], turn_id=source.turn_id,
                    life_epoch=source.life_epoch, fencing_token=source.fencing_token, authority_version=source.authority_version,
                    app=app, conn_factory=self.connect, operation_id=operation_id,
                    command={'id': 'send', 'capability': capability, 'args': args})
            op = str(uuid.uuid4())
            first = asyncio.run(execute(op))
            cross_op = asyncio.run(execute(str(uuid.uuid4())))
            same_op = asyncio.run(execute(op))
            self.assertEqual(first['status'], 'succeeded', first)
            self.assertEqual(cross_op['status'], 'succeeded', cross_op)
            self.assertTrue(cross_op['delivery']['reused'])
            # Engine-level operation replay may return the original receipt, but never sends again.
            self.assertEqual(first['completion_evidence']['id'], same_op['completion_evidence']['id'])
            self.assertEqual(first['completion_evidence']['id'], cross_op['completion_evidence']['id'])

    def test_fresh_readable_receipts_survive_intermediate_read_only_callbacks(self):
        first = self.native_send()
        # Callback replaced its immediate receipts with a read-only operation; fetch actual rows anyway.
        with self.connect() as conn:
            result = delivery.recent_deliveries(conn, self.a, {'kind': 'conversation', 'key': self.key})
            self.assertEqual(result['items'][0]['id'], first['id'])
            self.assertTrue(result['items'][0]['created_at'])
            self.assertEqual(result['items'][0]['status'], 'sent')
            self.assertEqual(delivery.recent_deliveries(conn, self.a, {'kind': 'public'})['items'], [])
            other = delivery.recent_deliveries(conn, self.a, {'kind': 'conversation', 'key': dm_key(self.peer, f"student:{self.c['student_id']}")})
            self.assertEqual(other['items'], [])
            social.recall_message(conn, self.user, first['id'])
            self.assertEqual(delivery.recent_deliveries(conn, self.a, {'kind': 'conversation', 'key': self.key})['items'], [])
            conn.commit()

    def _runtime_repeat(self, channel):
        from classroom_app.routers.social import router as social_router
        from classroom_app.routers.message_center import router as legacy_router
        from classroom_app.services import wanderer_platform_service as platform, wanderer_runtime_service as runtime
        from tests.test_wanderer_runtime_v2 import Caller, command, decision
        app = FastAPI(); app.include_router(social_router); app.include_router(legacy_router)
        body = '完整三回合中，这段内容只能真正发送一次。'
        if channel == 'legacy':
            key = legacy.build_conversation_key(self.sender, self.peer, None)
            send = command('legacy.send_message', {'body': {'contact_identity': self.peer, 'content': body}})
        else:
            from classroom_app.services.social_group_service import create_group
            with self.connect() as conn:
                key = create_group(conn, self.user, '回调链合成群', [self.peer])['conversation_key']
                conn.commit()
            send = command('social.send_message', {'path_params': {'key': key}, 'body': {'body': body}})
        def second(payload):
            self.assertIn(body, payload['system_prompt'])
            return decision([command('self.status', ident='read_status')], True)
        def third(payload):
            data = json.loads(payload['system_prompt'].split('<<<data>>>\n')[1].split('\n<<<end>>>')[0])
            self.assertEqual(data['receipts'][0]['command_id'], 'read_status')
            self.assertIn('【本会话当前可确认的本人已发消息】', payload['system_prompt'])
            # The middle turn no longer contains the send receipt, yet actual delivery is refreshed.
            self.assertIn(body, payload['system_prompt'].split('【本会话当前可确认的本人已发消息】')[1].split('\n')[1])
            return decision([send])
        caller = Caller(decision([send], True), second, third)
        with patch.object(platform, '_app', return_value=app):
            result = asyncio.run(runtime.wake_wanderer(self.connect, self.a['id'], source='test',
                event_key=self.id(), context={'conversation_key': key}, ai_caller=caller, force=True))
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['ai_calls'], 3)
        self.assertEqual(len(caller.calls), 3)
        self.assertTrue(result['receipts'][-1]['result']['delivery']['reused'])
        with self.connect() as conn:
            table = 'private_messages' if channel == 'legacy' else 'social_messages'
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE conversation_key=? AND sender_identity=?',
                                         (key, self.sender)).fetchone()[0], 1)
            self.assertEqual(snapshot(conn, self.a['id'], 'social_message')['used'], 3000000)
            self.assertEqual(snapshot(conn, self.a['id'], 'ai_wake')['used'], 3)

    def test_group_runtime_send_read_repeat_has_fresh_receipt_and_one_delivery(self):
        self._runtime_repeat('group')

    def test_legacy_runtime_send_read_repeat_has_fresh_receipt_and_one_delivery(self):
        self._runtime_repeat('legacy')

    def test_unverified_human_guard_does_not_initialize_ai_tables(self):
        import sqlite3
        with sqlite3.connect(':memory:') as conn:
            conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE else sqlite3.SQLITE_OK)
            self.assertIsNone(delivery.prepare_social(conn, {'id': 1, 'role': 'student'}, {'key': 'x'}, {'body': 'hello'}))
            self.assertEqual(conn.execute('SELECT name FROM sqlite_master').fetchall(), [])
