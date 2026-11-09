"""Protocol repair races against an explicitly owned synthetic PostgreSQL DB."""
import asyncio
import json
import os
import threading
import unittest
from unittest.mock import patch

from tests import test_wanderer_world_postgres as world_fixture
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services import wanderer_resource_service as resources
from classroom_app.services.wanderer_settings_service import daily_ai_calls, daily_ai_calls_key, wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'), 'Requires owned offline PostgreSQL')
class WandererProtocolRepairPostgresTest(unittest.TestCase):
    setUpClass = classmethod(world_fixture.WandererWorldPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(world_fixture.WandererWorldPostgresTest.tearDownClass.__func__)
    connect = world_fixture.WandererWorldPostgresTest.connect
    race = world_fixture.WandererWorldPostgresTest.race

    def setUp(self):
        world_fixture.WandererWorldPostgresTest.setUp(self)
        self.enterContext(patch.dict(os.environ,{'WANDERER_ENABLED':'true'}))
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_agents SET daily_wake_limit=20')
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), '0')
            wanderer_runtime_setting_set(conn, 'daily_ai_call_cap', '100')
            wanderer_runtime_setting_set(conn, 'enabled', '1')
            wanderer_runtime_setting_set(conn, 'autonomous_v2_enabled', '1')
            conn.commit()

    def test_pause_switches_do_not_create_or_charge_recovery_events(self):
        self.failed('paused')
        with patch.dict(os.environ,{'WANDERER_ENABLED':'false'}):
            self.assertEqual(self.recover(),0)
        for name in ('enabled','autonomous_v2_enabled'):
            with self.connect() as conn:
                wanderer_runtime_setting_set(conn,name,'0')
                conn.commit()
            self.assertEqual(self.recover(),0)
            with self.connect() as conn:
                wanderer_runtime_setting_set(conn,name,'1')
                conn.commit()
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='protocol_repair'").fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0],0)
        self.assertEqual(self.recover(),1)

    def failed(self, key, *, owner=1, consumed=False):
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,owner,'group_joined',key,{'scope':{'kind':'public'}})
            run=runtime._claim(conn,event['event_id'])
            conn.execute("UPDATE wanderer_turns SET dispatched_at=?,prompt_version='autonomous-world-v2.1' WHERE turn_id=?",
                         (local_iso(),run['turn_id']))
            runtime._finish_in_connection(conn,run,'failed',error=runtime.PROTOCOL_PARSE_ERRORS[0])
            if consumed:
                self.assertTrue(resources.consume(conn,owner,'ai_wake',1,'wake:'+run['turn_id'])['ok'])
                wanderer_runtime_setting_set(conn,daily_ai_calls_key(),'1')
            conn.commit()
            return run

    def recover(self, _=None, *, limit=12):
        with self.connect() as conn:
            recovered=runtime.recover_protocol_failures(conn,limit=limit)
            conn.commit()
            return recovered

    def test_same_failure_races_create_one_repair_and_only_one_new_paid_dispatch(self):
        original=self.failed('race',consumed=True)
        self.assertEqual(sum(self.race(2,self.recover)),1)
        with self.connect() as conn:
            events=conn.execute("SELECT * FROM wanderer_wake_events WHERE source='protocol_repair'").fetchall()
            self.assertEqual(len(events),1)
            repair=events[0]['event_id']
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_commands').fetchone()[0],0)
            self.assertEqual(resources.snapshot(conn,1,'ai_wake')['used'],1)
            self.assertEqual(daily_ai_calls(conn),1)
        calls=[];lock=threading.Lock()
        async def caller(*_args,**_kwargs):
            with lock:calls.append(1)
            await asyncio.sleep(.1)
            return {'status':'success','response_json':{'protocol':'wanderer.v1','commands':[],
                                                       'callback':{'requested':False,'collect':[]}}}
        with patch.object(runtime,'build_runtime_prompt',return_value='Synthetic protocol repair decision'):
            results=self.race(2,lambda _:asyncio.run(runtime._process(self.connect,repair,ai_caller=caller)))
            replay=asyncio.run(runtime._process(self.connect,original['event_id'],ai_caller=caller))
            completed=asyncio.run(runtime._process(self.connect,repair,ai_caller=caller))
        self.assertEqual(len(calls),1)
        self.assertEqual(sum(row['ai_calls'] for row in results),1)
        self.assertEqual(replay['callback_event_id'],repair)
        self.assertEqual((completed['status'],completed['ai_calls']),('ok',0))
        with self.connect() as conn:
            self.assertEqual(resources.snapshot(conn,1,'ai_wake')['used'],2)
            self.assertEqual(daily_ai_calls(conn),2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_commands').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_turns').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT state FROM wanderer_turns WHERE turn_id=?',(original['turn_id'],)).fetchone()[0],'failed')

    def test_skip_locked_recovers_next_source_without_waiting_for_locked_event(self):
        first=self.failed('locked',owner=1)
        second=self.failed('unlocked',owner=2)
        with self.connect() as owner:
            owner.execute('UPDATE wanderer_wake_events SET updated_at=? WHERE event_id=?',('2000',first['event_id']))
            self.assertEqual(self.recover(limit=1),1)
            with self.connect() as conn:
                parent=conn.execute("SELECT parent_turn_id FROM wanderer_wake_events WHERE source='protocol_repair'").fetchone()[0]
                self.assertEqual(parent,second['turn_id'])
            owner.rollback()
        self.assertEqual(self.recover(limit=1),1)

    def test_accepted_commands_existing_repairs_and_invalid_authority_are_excluded(self):
        cases={name:self.failed(name) for name in ('accepted','command','repaired','epoch','authority','unknown','provider','valid')}
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_turns SET output_json=? WHERE turn_id=?",(json.dumps({'commands':[]}),cases['accepted']['turn_id']))
            conn.execute("INSERT INTO wanderer_commands(operation_id,turn_id,command_id,capability,args_hash,command_json,created_at,updated_at) VALUES('old-command',?,'c','fixture','hash','{}',?,?)",
                         (cases['command']['turn_id'],local_iso(),local_iso()))
            runtime._enqueue_protocol_repair(conn,cases['repaired'],runtime.PROTOCOL_PARSE_ERRORS[0],{'type':'not_retained'})
            conn.execute('UPDATE wanderer_turns SET life_epoch=life_epoch+1 WHERE turn_id=?',(cases['epoch']['turn_id'],))
            conn.execute('UPDATE wanderer_turns SET authority_version=authority_version+1 WHERE turn_id=?',(cases['authority']['turn_id'],))
            conn.execute("UPDATE wanderer_wake_events SET source='unrecognized' WHERE event_id=?",(cases['unknown']['event_id'],))
            conn.execute("UPDATE wanderer_turns SET error='provider timeout' WHERE turn_id=?",(cases['provider']['turn_id'],))
            conn.commit()
        self.assertEqual(self.recover(),1)
        self.assertEqual(self.recover(),0)
        with self.connect() as conn:
            parents={r[0] for r in conn.execute("SELECT parent_turn_id FROM wanderer_wake_events WHERE source='protocol_repair'")}
            self.assertEqual(parents,{cases['valid']['turn_id'],cases['repaired']['turn_id']})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_commands').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0],0)

    def test_stale_authority_before_live_source_does_not_starve_limit_one(self):
        stale=self.failed('stale')
        live=self.failed('live')
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_wake_events SET updated_at=? WHERE event_id=?',('2000',stale['event_id']))
            conn.execute('UPDATE wanderer_turns SET life_epoch=life_epoch+1 WHERE turn_id=?',(stale['turn_id'],))
            conn.commit()
        self.assertEqual(self.recover(limit=1),1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT parent_turn_id FROM wanderer_wake_events WHERE source='protocol_repair'").fetchone()[0],live['turn_id'])

    def test_revoked_group_scope_is_blocked_without_erasing_original_failure_or_starving(self):
        revoked=self.failed('revoked')
        live=self.failed('next')
        from classroom_app.db import schema_social
        with patch.object(schema_social,'_SCHEMA_READY',False),self.connect() as conn:
            schema_social.ensure_social_schema(conn,engine='postgres')
            conn.execute('UPDATE wanderer_wake_events SET context_json=?,updated_at=? WHERE event_id=?',
                         (json.dumps({'conversation_key':'group:999'}),'2000',revoked['event_id']))
            conn.commit()
        with patch.object(schema_social,'_SCHEMA_READY',True):
            self.assertEqual(self.recover(limit=1),0)
            self.assertEqual(self.recover(limit=1),1)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT state FROM wanderer_wake_events WHERE event_id=?',(revoked['event_id'],)).fetchone()[0],'blocked')
            row=conn.execute('SELECT state,error FROM wanderer_turns WHERE turn_id=?',(revoked['turn_id'],)).fetchone()
            self.assertEqual((row['state'],row['error']),('failed',runtime.PROTOCOL_PARSE_ERRORS[0]))
            self.assertEqual(conn.execute("SELECT parent_turn_id FROM wanderer_wake_events WHERE source='protocol_repair'").fetchone()[0],live['turn_id'])
