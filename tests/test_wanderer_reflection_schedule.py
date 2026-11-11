"""Daily scheduler admission and actual queue execution, using synthetic data only."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services import scheduled_task_service as scheduler
from classroom_app.services import wanderer_behavior_service as behavior
from classroom_app.services import wanderer_reflection_service as reflection
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.wanderer_relationship_service import affinity_state
from classroom_app.services.wanderer_settings_service import daily_ai_calls_key, wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso, local_now
from tests.test_wanderer_runtime_v2 import Caller, command, decision
from tests.wanderer_test_support import WandererDbTestCase


class ReflectionScheduleTests(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict('os.environ', {'WANDERER_ENABLED': 'true'}))
        with self.connect() as conn:
            for table in ('wanderer_affinity_rating_operations', 'wanderer_affinity_ratings',
                          'wanderer_reflection_dispatches', 'wanderer_reflection_sessions',
                          'wanderer_commands', 'wanderer_turns', 'wanderer_wake_events',
                          'wanderer_runtime_leases', 'wanderer_daily_reflections',
                          'wanderer_resource_reservations', 'wanderer_resource_usage',
                          'wanderer_relationship_changes', 'wanderer_relationship_versions',
                          'wanderer_relationships', 'social_friendships', 'social_blocks', 'scheduled_tasks'):
                conn.execute(f'DELETE FROM {table}')
            conn.execute("UPDATE wanderer_agents SET status='active',daily_wake_limit=0,born_at=?,"
                         'lifespan_weeks=20,life_penalty_weeks=0', (local_iso(),))
            for key, value in {'autonomous_v2_enabled': '1', 'enabled': '1', 'reflection_enabled': '1',
                               'reflection_batch_size': '2', 'reflection_hour': '23:30',
                               'daily_ai_call_cap': '0', daily_ai_calls_key(): '0'}.items():
                wanderer_runtime_setting_set(conn, key, value)
            conn.commit()

    def test_every_active_agent_is_queued_once_and_drains_across_worker_batches(self):
        # This time is well before the worker's clock so every staggered batch is due.
        admission_time = local_now() - timedelta(hours=1)
        with self.connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM wanderer_agents').fetchone()[0]
        self.assertGreater(count, 2)
        queued = reflection.enqueue_daily_reflections(self.connect, now=admission_time)
        again = reflection.enqueue_daily_reflections(self.connect, now=admission_time)
        self.assertEqual(queued['queued'], count)
        self.assertEqual(again['queued'], 0)
        self.assertEqual(again['existing'], count)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_sessions').fetchone()[0], count)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0], 0)
            groups = conn.execute("SELECT run_at,COUNT(*) FROM wanderer_wake_events GROUP BY run_at").fetchall()
            self.assertTrue(all(row[1] <= 2 for row in groups))
        caller = Caller(*[decision() for _ in range(count)])
        for _ in range(count):
            drained = asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=caller, limit=3))
            if not drained['processed']:
                break
        self.assertEqual(len(caller.calls), count)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_reflection_sessions WHERE status='applied' AND ai_calls=1").fetchone()[0], count)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT value FROM wanderer_runtime_settings WHERE key=?', (daily_ai_calls_key(),)).fetchone()[0], '0')
        self.assertEqual(reflection.enqueue_daily_reflections(self.connect, now=admission_time)['queued'], 0)

    def test_queued_reflection_rates_friend_and_stops_after_third_request(self):
        with self.connect() as conn:
            me, friend = self.wanderer(conn, 'lele'), self.wanderer(conn, 'momo')
            conn.execute("UPDATE wanderer_agents SET status='paused' WHERE id<>?", (me['id'],))
            a, b = sorted([f"student:{me['student_id']}", f"student:{friend['student_id']}"])
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) "
                         "VALUES(?,?,'accepted',?,?)", (a, b, a, local_iso()))
            conn.commit()
        peer = f"student:{friend['student_id']}"
        admitted = reflection.enqueue_daily_reflections(self.connect)
        self.assertEqual(admitted['queued'], 1)
        caller = Caller(decision([], True), decision([], True),
            decision([command('relationship.rate', {'peer_ref': peer, 'grade': 'very_good', 'expected_revision': 0}),
                      command('memory.write', {'content': '今天认真回顾了好友相处，感到很愉快。', 'location': '每日自省'}, 'remember')], True))
        result = asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=caller))
        self.assertEqual(result['ai_calls'], 3, result)
        self.assertEqual(asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller()))['ai_calls'], 0)
        with self.connect() as conn:
            self.assertEqual(affinity_state(conn, me['id'], peer)['affinity'], 12)
            row = conn.execute('SELECT status,ai_calls FROM wanderer_reflection_sessions').fetchone()
            self.assertEqual(tuple(row), ('applied', 3))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_affinity_ratings').fetchone()[0], 1)
            from classroom_app.services.wanderer_admin_service import reflection_page
            page = reflection_page(conn, me['id'])
            self.assertIn('今天认真回顾了好友相处', page['items'][0]['summary'])

    def test_disabled_admission_and_completed_legacy_reflection_are_preserved(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, 'reflection_enabled', '0')
            conn.commit()
        self.assertEqual(reflection.enqueue_daily_reflections(self.connect)['queued'], 0)
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, 'reflection_enabled', '1')
            me = self.wanderer(conn, 'lele')
            conn.execute("UPDATE wanderer_agents SET status='paused' WHERE id<>?", (me['id'],))
            row = reflection.ensure_reflection(conn, me, local_iso()[:10])
            conn.execute("UPDATE wanderer_daily_reflections SET status='applied' WHERE id=?", (row['id'],))
            conn.commit()
        result = reflection.enqueue_daily_reflections(self.connect)
        self.assertEqual((result['queued'], result['existing']), (0, 1))

    def test_revived_old_daily_session_does_not_block_every_other_agent_or_grant_again(self):
        with self.connect() as conn:
            from classroom_app.services import wanderer_daily_reflection_service as daily
            me = self.wanderer(conn, 'lele')
            event = runtime.enqueue_wake_event(conn, me['id'], 'reflection', local_iso()[:10], {'scope': {'kind': 'private'}})
            reflection.ensure_reflection(conn, me, local_iso()[:10])
            daily.register_session(conn, me, local_iso()[:10], event)
            conn.execute('UPDATE wanderer_agents SET life_epoch=life_epoch+1 WHERE id=?', (me['id'],))
            count = conn.execute('SELECT COUNT(*) FROM wanderer_agents').fetchone()[0]
            conn.commit()
        result = reflection.enqueue_daily_reflections(self.connect)
        self.assertEqual((result['queued'], result['existing']), (count-1, 1))
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_sessions').fetchone()[0], count)
            old = conn.execute('SELECT life_epoch,ai_calls FROM wanderer_reflection_sessions WHERE wanderer_id=?', (me['id'],)).fetchone()
            self.assertEqual(tuple(old), (me['life_epoch'], 0))

    def test_model_retry_uses_same_free_session_and_clears_only_active_error(self):
        with self.connect() as conn:
            me = self.wanderer(conn, 'lele')
            conn.execute("UPDATE wanderer_agents SET status='paused' WHERE id<>?", (me['id'],))
            conn.commit()
        def unavailable(_payload):
            raise TimeoutError('synthetic provider timeout')
        reflection.enqueue_daily_reflections(self.connect)
        first = asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller(unavailable)))
        self.assertEqual(first['ai_calls'], 1)
        with self.connect() as conn:
            row = conn.execute('SELECT status,ai_calls,error FROM wanderer_daily_reflections').fetchone()
            self.assertEqual(tuple(row)[:2], ('running', 1))
            self.assertIn('synthetic provider timeout', row['error'])
            conn.execute("UPDATE wanderer_wake_events SET run_at=? WHERE source='model_retry'", (local_iso(),))
            conn.commit()
        retry = asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller(decision([], True), decision([], True))))
        self.assertEqual(retry['ai_calls'], 2)
        with self.connect() as conn:
            row = conn.execute('SELECT status,ai_calls,error FROM wanderer_daily_reflections').fetchone()
            self.assertEqual(tuple(row), ('applied', 3, ''))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_turns WHERE error='synthetic provider timeout'").fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0], 0)
        self.assertEqual(asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller()))['ai_calls'], 0)

    def test_third_free_request_failure_never_grants_retry(self):
        with self.connect() as conn:
            me = self.wanderer(conn, 'lele')
            conn.execute("UPDATE wanderer_agents SET status='paused' WHERE id<>?", (me['id'],))
            conn.commit()
        def unavailable(_payload):
            raise TimeoutError('synthetic third request failure')
        reflection.enqueue_daily_reflections(self.connect)
        result = asyncio.run(runtime.drain_wake_events(self.connect,
            ai_caller=Caller(decision([], True), decision([], True), unavailable)))
        self.assertEqual(result['ai_calls'], 3)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='model_retry'").fetchone()[0], 0)
            row = conn.execute('SELECT status,ai_calls,error FROM wanderer_daily_reflections').fetchone()
            self.assertEqual(tuple(row)[:2], ('partial', 3))
            self.assertIn('synthetic third request failure', row['error'])

    def test_model_retry_then_protocol_repair_closes_both_failures_in_three_free_calls(self):
        with self.connect() as conn:
            me = self.wanderer(conn, 'lele')
            conn.execute("UPDATE wanderer_agents SET status='paused' WHERE id<>?", (me['id'],))
            conn.commit()
        def unavailable(_payload):
            raise TimeoutError('synthetic provider timeout')
        reflection.enqueue_daily_reflections(self.connect)
        asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller(unavailable)))
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_wake_events SET run_at=? WHERE source='model_retry'", (local_iso(),))
            conn.commit()
        retry = asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller({'invalid': 'decision'}, decision([], True))))
        self.assertEqual(retry['ai_calls'], 2)
        with self.connect() as conn:
            row = conn.execute('SELECT status,ai_calls,error,applied_json FROM wanderer_daily_reflections').fetchone()
            self.assertEqual(tuple(row)[:3], ('applied', 3, ''))
            self.assertIn('"unresolved_events":0', row['applied_json'])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_turns WHERE state='failed'").fetchone()[0], 2)
        self.assertEqual(asyncio.run(runtime.drain_wake_events(self.connect, ai_caller=Caller()))['ai_calls'], 0)

    def test_handler_returns_configured_wall_clock_and_success_preserves_it(self):
        with self.connect() as conn:
            task_id = scheduler.schedule_task(conn, task_kind='wanderer_daily_reflection',
                run_at=local_now()-timedelta(minutes=1), recurrence_seconds=86400, dedupe_key='test:reflection')
            conn.commit()
        task = scheduler._claim_due_tasks(1)[0]
        finish = datetime(2026, 11, 10, 23, 54, 50)
        with patch.object(reflection, 'local_now', return_value=finish):
            result = asyncio.run(reflection.handle_wanderer_daily_reflection(task))
        self.assertEqual(result.next_run_at, datetime(2026, 11, 11, 23, 30))
        scheduler._mark_success(task, result)
        with self.connect() as conn:
            row = conn.execute('SELECT run_at,last_result FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
        self.assertEqual(row['run_at'], '2026-11-11T23:30:00')
        self.assertIn('queued=', row['last_result'])

    def test_old_handler_cannot_overwrite_changed_daily_clock(self):
        with self.connect() as conn:
            task_id = scheduler.schedule_task(conn, task_kind='wanderer_daily_reflection',
                run_at=local_now()-timedelta(minutes=1), recurrence_seconds=86400, dedupe_key='test:clock')
            conn.commit()
        old = scheduler._claim_due_tasks(1)[0]
        replacement = local_now() + timedelta(hours=2)
        with self.connect() as conn:
            scheduler.schedule_task(conn, task_kind='wanderer_daily_reflection', run_at=replacement,
                recurrence_seconds=86400, dedupe_key='test:clock', replace=True)
            conn.commit()
        scheduler._mark_success(old, scheduler.ScheduledTaskResult('old result', replacement+timedelta(days=1)))
        with self.connect() as conn:
            row = conn.execute('SELECT run_at,status,last_result FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
        self.assertEqual(row['run_at'], replacement.isoformat(timespec='seconds'))
        self.assertEqual(row['status'], 'pending')
        self.assertNotEqual(row['last_result'], 'old result')

    def test_upgrade_repairs_future_drift_but_keeps_due_retry_and_running_work(self):
        now = datetime(2026, 11, 10, 12, 0)
        with patch.object(behavior, 'local_now', return_value=now):
            with self.connect() as conn:
                task_id = behavior.ensure_wanderer_scheduled_tasks(conn)['reflection']
                conn.commit()
            for status, run_at, attempts, expected in (
                ('pending', '2026-11-10T23:54:50', 0, '2026-11-10T23:30:00'),
                ('pending', '2026-11-10T11:30:00', 0, '2026-11-10T11:30:00'),
                ('pending', '2026-11-10T12:01:00', 1, '2026-11-10T12:01:00'),
                ('running', '2026-11-10T23:54:50', 0, '2026-11-10T23:54:50'),
            ):
                with self.subTest(status=status, run_at=run_at, attempts=attempts), self.connect() as conn:
                    conn.execute('UPDATE scheduled_tasks SET status=?,run_at=?,attempt_count=? WHERE id=?',
                                 (status, run_at, attempts, task_id))
                    behavior.ensure_wanderer_scheduled_tasks(conn)
                    self.assertEqual(conn.execute('SELECT run_at FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()[0], expected)
                    conn.commit()
