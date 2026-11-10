"""Independent-connection reflection admission and scheduler fencing."""
import asyncio
from datetime import timedelta
import os
import threading
import unittest
from unittest.mock import patch

from classroom_app.db import schema_scheduler, schema_social
from classroom_app.db.schema_social import ensure_social_schema
from classroom_app.services import scheduled_task_service as scheduler
from classroom_app.services import wanderer_daily_reflection_service as daily
from classroom_app.services import wanderer_reflection_service as reflection
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.wanderer_settings_service import daily_ai_calls_key, wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso, local_now
from tests import test_wanderer_world_postgres as fixture


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'), 'Requires owned offline PostgreSQL')
class ReflectionSchedulePostgresTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WandererWorldPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.WandererWorldPostgresTest.tearDownClass.__func__)
    connect = fixture.WandererWorldPostgresTest.connect
    race = fixture.WandererWorldPostgresTest.race

    def setUp(self):
        fixture.WandererWorldPostgresTest.setUp(self)
        self.enterContext(patch.dict(os.environ, {'WANDERER_ENABLED': 'true'}))
        self.enterContext(patch.object(schema_scheduler, '_SCHEMA_READY', False))
        self.enterContext(patch.object(schema_social, '_SCHEMA_READY', False))
        self.enterContext(patch.object(scheduler, 'get_db_connection', self.connect))
        with self.connect() as conn:
            ensure_social_schema(conn, engine='postgres')
            schema_scheduler.ensure_scheduler_schema(conn)
            for key, value in {'enabled': '1', 'autonomous_v2_enabled': '1', 'reflection_enabled': '1',
                               'reflection_batch_size': '1', 'daily_ai_call_cap': '0',
                               daily_ai_calls_key(): '0'}.items():
                wanderer_runtime_setting_set(conn, key, value)
            conn.execute('UPDATE wanderer_agents SET daily_wake_limit=0')
            conn.commit()

    def test_parallel_all_roster_admission_and_claim_never_duplicate_session_or_free_call(self):
        now = local_now()-timedelta(minutes=10)
        results = self.race(4, lambda _: reflection.enqueue_daily_reflections(self.connect, now=now))
        self.assertEqual(sum(item['queued'] for item in results), 3)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_sessions').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_daily_reflections').fetchone()[0], 3)
            events = [dict(row) for row in conn.execute('SELECT * FROM wanderer_wake_events ORDER BY wanderer_id')]
            self.assertEqual(len(events), 3)
        calls, lock = [], threading.Lock()
        async def caller(*_args, **_kwargs):
            with lock:
                calls.append(1)
            await asyncio.sleep(.05)
            return {'status': 'success', 'response_json': {'protocol': 'wanderer.v1', 'commands': [],
                                                        'callback': {'requested': False, 'collect': []}}}
        with patch.object(runtime, 'build_runtime_prompt', return_value='Synthetic daily reflection'):
            for event in events:
                result = self.race(2, lambda _: asyncio.run(runtime._process(self.connect, event['event_id'], ai_caller=caller)))
                self.assertEqual(sum(item['ai_calls'] for item in result), 1)
        self.assertEqual(len(calls), 3)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_reflection_sessions WHERE ai_calls=1 AND status='applied'").fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0], 0)

    def test_independent_admin_replacement_fences_old_absolute_reschedule(self):
        with self.connect() as conn:
            task_id = scheduler.schedule_task(conn, task_kind='wanderer_daily_reflection',
                run_at=local_now()-timedelta(minutes=1), recurrence_seconds=86400, dedupe_key='synthetic:daily')
            conn.commit()
        claimed = scheduler._claim_due_tasks(1)[0]
        replacement = local_now()+timedelta(hours=2)
        with self.connect() as administrator:
            scheduler.schedule_task(administrator, task_kind='wanderer_daily_reflection', run_at=replacement,
                recurrence_seconds=86400, dedupe_key='synthetic:daily', replace=True)
            administrator.commit()
        scheduler._mark_success(claimed, scheduler.ScheduledTaskResult('stale', replacement+timedelta(days=1)))
        with self.connect() as observer:
            row = observer.execute('SELECT run_at,status,last_result FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
            self.assertEqual(row['run_at'], replacement.isoformat(timespec='seconds'))
            self.assertEqual(row['status'], 'pending')
            self.assertNotEqual(row['last_result'], 'stale')

    def test_duplicate_free_dispatch_and_final_slot_are_serialized(self):
        reflection.enqueue_daily_reflections(self.connect, now=local_now()-timedelta(minutes=10))
        with self.connect() as conn:
            event = dict(conn.execute('SELECT * FROM wanderer_wake_events WHERE wanderer_id=1').fetchone())
            actor = dict(conn.execute('SELECT * FROM wanderer_agents WHERE id=1').fetchone())
        def reserve(index):
            with self.connect() as conn:
                result = daily.reserve_free_call(conn, {**event, 'wanderer': actor, 'turn_id': f'synthetic-{index}'})
                conn.commit()
                return result
        results = self.race(8, reserve)
        self.assertEqual(sum(item['ok'] for item in results), 3)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT ai_calls FROM wanderer_reflection_sessions WHERE wanderer_id=1').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches WHERE wanderer_id=1').fetchone()[0], 3)
