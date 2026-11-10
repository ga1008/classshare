"""Cancellation acceptance with isolated storage and synthetic model responses."""
import asyncio
from unittest.mock import patch

from classroom_app.services import wanderer_behavior_service as behavior
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_reflection_service import run_reflection
from classroom_app.services.wanderer_settings_service import (
    daily_ai_calls, daily_ai_calls_key, wanderer_runtime_setting_set,
)
from classroom_app.time_utils import local_iso, local_now
from tests.test_wanderer_runtime_v2 import Caller, command, decision
from tests.wanderer_test_support import WandererDbTestCase


class RuntimeCancellationTests(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict('os.environ', {'WANDERER_ENABLED': 'true'}))
        with self.connect() as conn:
            for table in (
                'wanderer_affinity_rating_operations', 'wanderer_affinity_ratings',
                'wanderer_reflection_dispatches', 'wanderer_reflection_sessions',
                'wanderer_commands', 'wanderer_turns', 'wanderer_wake_events',
                'wanderer_runtime_leases', 'wanderer_daily_reflections',
                'wanderer_resource_reservations', 'wanderer_resource_usage',
                'wanderer_memory_sources', 'wanderer_episodic_memories',
                'social_friendships', 'social_blocks',
            ):
                conn.execute(f'DELETE FROM {table}')
            self.w = self.wanderer(conn, 'lele')
            self.friend = self.wanderer(conn, 'momo')
            self.peer = f"student:{self.friend['student_id']}"
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=100,status='active',born_at=?,"
                         'lifespan_weeks=20,life_penalty_weeks=0 WHERE id=?', (local_iso(), self.w['id']))
            for key, value in ((daily_ai_calls_key(), '0'), ('daily_ai_call_cap', '100'),
                               ('autonomous_v2_enabled', '1'), ('enabled', '1')):
                wanderer_runtime_setting_set(conn, key, value)
            conn.commit()

    def wake(self, caller, key='cancelled-root'):
        return runtime.wake_wanderer(self.connect, self.w['id'], source='test', event_key=key,
                                     context={}, ai_caller=caller, force=True)

    def assert_settled_once(self, *, free=False):
        with self.connect() as conn:
            turns = [dict(r) for r in conn.execute('SELECT * FROM wanderer_turns').fetchall()]
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]['state'], 'completed')
            self.assertIsNotNone(turns[0]['completed_at'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_sources').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_commands WHERE state='succeeded'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE state='completed'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE state='pending' AND source='callback'").fetchone()[0], 1)
            self.assertEqual(daily_ai_calls(conn), 0 if free else 1)
            lease = conn.execute('SELECT lease_until FROM wanderer_runtime_leases').fetchone()[0]
            self.assertLessEqual(lease, local_iso())

    def test_cancelled_model_wait_settles_once_keeps_heartbeat_and_queues_callback(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            started, stopped = asyncio.Event(), asyncio.Event()
            calls = []
            real_heartbeat = runtime._heartbeat

            async def heartbeat(*args):
                started.set()
                try:
                    await real_heartbeat(*args)
                finally:
                    stopped.set()

            async def caller(endpoint, payload, **kwargs):
                calls.append(payload)
                entered.set()
                await release.wait()
                return {'status': 'success', 'response_json': decision([
                    command('memory.write', {'content': '取消请求没有抹去这一轮的真实结果。', 'location': '个人空间'})
                ], True)}

            with patch.object(runtime, '_heartbeat', heartbeat):
                task = asyncio.create_task(self.wake(caller))
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                try:
                    self.assertFalse(task.done())
                    self.assertFalse(stopped.is_set())
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertTrue(stopped.is_set())
            self.assertEqual(len(calls), 1)
            self.assert_settled_once()
            # A fresh queue worker resumes only the persisted continuation.
            followup = Caller(decision())
            await runtime.drain_wake_events(self.connect, ai_caller=followup)
            self.assertEqual(len(followup.calls), 1)
            replay = Caller()
            await self.wake(replay)
            self.assertEqual(replay.calls, [])
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_sources').fetchone()[0], 1)
                self.assertEqual(daily_ai_calls(conn), 2)

        asyncio.run(scenario())

    def test_cancel_during_accepted_free_command_preserves_three_call_entitlement(self):
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_agents SET daily_wake_limit=0 WHERE id=?', (self.w['id'],))
            wanderer_runtime_setting_set(conn, 'daily_ai_call_cap', '0')
            a, b = sorted([f"student:{self.w['student_id']}", self.peer])
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) "
                         "VALUES(?,?,'accepted',?,?)", (a, b, a, local_iso()))
            conn.commit()

        async def scenario():
            accepted, release = asyncio.Event(), asyncio.Event()
            execute = runtime.execute_command
            caller = Caller(decision([command('memory.write', {
                'content': '自省接受的记录只保存一次。', 'location': '个人空间'})], True))

            async def finish_receipt(*args, **kwargs):
                receipt = await execute(*args, **kwargs)
                accepted.set()
                await release.wait()
                return receipt

            with patch.object(runtime, 'execute_command', finish_receipt):
                ctx = ActionContext(self.connect, self.w, ai_caller=caller, now=local_now())
                task = asyncio.create_task(run_reflection(ctx))
                await asyncio.wait_for(accepted.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assert_settled_once(free=True)
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT ai_calls FROM wanderer_reflection_sessions').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0], 1)
            remaining = Caller(decision([command('relationship.rate', {
                'peer_ref': self.peer, 'grade': 'good', 'expected_revision': 0})], True), decision([], True))
            await runtime.drain_wake_events(self.connect, ai_caller=remaining)
            self.assertEqual(len(remaining.calls), 2)
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT ai_calls FROM wanderer_reflection_sessions').fetchone()[0], 3)
                self.assertEqual(conn.execute('SELECT status FROM wanderer_reflection_sessions').fetchone()[0], 'applied')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0], 3)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_sources').fetchone()[0], 1)
                self.assertEqual(daily_ai_calls(conn), 0)
            again = Caller()
            await runtime.drain_wake_events(self.connect, ai_caller=again)
            self.assertFalse(again.calls)

        asyncio.run(scenario())

    def test_tick_timeout_finishes_current_decision_without_starting_callback(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []

            async def caller(endpoint, payload, **kwargs):
                calls.append(payload)
                entered.set()
                await release.wait()
                return {'status': 'success', 'response_json': decision([
                    command('memory.write', {'content': '定时器超时前已开始的这一轮仍然结算。', 'location': '个人空间'})
                ], True)}

            async def run_one(*args, **kwargs):
                return await self.wake(caller)

            with patch.object(behavior, 'tick_max_seconds', return_value=0.08), \
                    patch.object(behavior, 'select_tick_wanderers', return_value=[(self.w, False)]), \
                    patch.object(behavior, '_run_one', run_one):
                task = asyncio.create_task(behavior.run_wanderer_tick_once(
                    self.connect, wanderer_ids=[self.w['id']], social_upkeep=False, force=True))
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.sleep(0.10)
                release.set()
                result = await asyncio.wait_for(task, 2)
            self.assertEqual(result['status'], 'timeout')
            self.assertEqual(len(calls), 1)
            self.assert_settled_once()

        asyncio.run(scenario())

    def test_targeted_tick_does_not_consume_global_queue(self):
        with self.connect() as conn:
            event = runtime.enqueue_wake_event(conn, self.w['id'], 'inbox', 'owned-by-wake-worker', {})
            conn.commit()
        caller = Caller(decision())
        with patch.object(behavior, 'select_tick_wanderers', return_value=[]):
            asyncio.run(behavior.run_wanderer_tick_once(self.connect, wanderer_ids=[self.friend['id']],
                ai_caller=caller, force=True, social_upkeep=False))
        self.assertFalse(caller.calls)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT state FROM wanderer_wake_events WHERE event_id=?',
                                         (event['event_id'],)).fetchone()[0], 'pending')

    def test_cancelled_slot_wait_does_not_dispatch_or_spend(self):
        async def scenario():
            with patch.object(runtime, '_SLOT_LOOP', asyncio.get_running_loop()), \
                    patch.object(runtime, '_SLOTS', asyncio.Semaphore(0)):
                caller = Caller()
                task = asyncio.create_task(self.wake(caller))
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(caller.calls)
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_turns').fetchone()[0], 0)
                self.assertEqual(daily_ai_calls(conn), 0)

        asyncio.run(scenario())

    def test_internally_cancelled_turn_is_propagated_without_spinning(self):
        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError

        async def scenario():
            with patch.object(runtime, '_process', cancelled):
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(self.wake(Caller()), 0.5)

        asyncio.run(scenario())
