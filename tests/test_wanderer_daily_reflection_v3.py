"""Real runtime acceptance using an isolated DB and synthetic model responses."""
import asyncio
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from classroom_app.services import wanderer_daily_reflection_service as daily
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_reflection_service import run_reflection
from classroom_app.services.wanderer_relationship_service import affinity_state, relationship_rankings, set_affinity
from classroom_app.services.wanderer_settings_service import daily_ai_calls_key, wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso, local_now
from tests.test_wanderer_runtime_v2 import Caller, command, decision
from tests.wanderer_test_support import WandererDbTestCase


class AffinityAlgorithmTests(unittest.TestCase):
    def test_all_grades_endpoints_and_neutral_preservation(self):
        self.assertEqual([daily.calculate_affinity(50,g) for g in daily.GRADES], [59,53.8,50,46.3,41])
        self.assertEqual(daily.calculate_affinity(50.123,'neutral'),50.123)
        self.assertEqual(daily.calculate_affinity(99,'very_good'),100)
        self.assertEqual(daily.calculate_affinity(1,'averse'),0)
        for score in range(101):
            self.assertLessEqual(daily.calculate_affinity(score,'averse'),score)
            self.assertGreaterEqual(daily.calculate_affinity(score,'very_good'),score)
        for grade in (99,{},'100'):
            with self.assertRaises(Exception):
                daily.calculate_affinity(50,grade)


class DailyReflectionV3Tests(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict('os.environ',{'WANDERER_ENABLED':'true'}))
        with self.connect() as conn:
            for table in ('wanderer_affinity_rating_operations','wanderer_affinity_ratings','wanderer_reflection_dispatches',
                          'wanderer_reflection_sessions','wanderer_commands','wanderer_turns','wanderer_wake_events',
                          'wanderer_runtime_leases','wanderer_daily_reflections','wanderer_resource_reservations',
                          'wanderer_resource_usage','wanderer_relationship_changes','wanderer_relationship_versions',
                          'wanderer_relationships','social_friendships','social_blocks','wanderer_memory_sources',
                          'wanderer_episodic_memories'):
                conn.execute(f'DELETE FROM {table}')
            self.w = self.wanderer(conn,'lele')
            self.friend = self.wanderer(conn,'momo')
            self.peer = f"student:{self.friend['student_id']}"
            self.me = f"student:{self.w['student_id']}"
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=0,status='active',born_at=?,lifespan_weeks=20,"
                         'life_penalty_weeks=0 WHERE id=?',(local_iso(),self.w['id']))
            a,b = sorted([self.me,self.peer])
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) "
                         "VALUES(?,?,'accepted',?,?)",(a,b,self.me,local_iso()))
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','1')
            wanderer_runtime_setting_set(conn,'enabled','1')
            wanderer_runtime_setting_set(conn,'daily_ai_call_cap','0')
            wanderer_runtime_setting_set(conn,daily_ai_calls_key(),'0')
            conn.commit()

    def reflect(self,caller,*,date=None,force=False):
        ctx=ActionContext(self.connect,self.w,ai_caller=caller,now=date or local_now())
        return asyncio.run(run_reflection(ctx,force=force))

    def rating(self,grade='good',revision=0,ident='rate'):
        return command('relationship.rate',{'peer_ref':self.peer,'grade':grade,'expected_revision':revision},ident)

    def test_three_free_requests_execute_final_commands_and_do_not_spend_normal_budgets(self):
        caller=Caller(decision([self.rating()],True),decision([],True),
                      decision([command('memory.write',{'content':'第三次自省仍可以保存已决定的记忆。','location':'个人空间'})],True))
        result=self.reflect(caller)
        self.assertEqual(result['status'],'applied',result)
        self.assertEqual(len(caller.calls),3)
        self.assertEqual(result['reflection']['ai_calls'],3)
        self.assertTrue(result['runtime']['reflection_callback_limit_reached'])
        self.assertEqual(result['runtime']['receipts'][-1]['status'],'succeeded')
        self.assertIn('"free_remaining":2',caller.calls[0]['system_prompt'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0],3)
            self.assertEqual(affinity_state(conn,self.w['id'],self.peer)['affinity'],5)
            from classroom_app.services.wanderer_settings_service import daily_ai_calls
            self.assertEqual(daily_ai_calls(conn),0)
        again=self.reflect(Caller(),force=True)
        self.assertEqual(again['ai_calls'],0)

    def test_missing_friend_is_partial_and_explicit_resume_uses_same_daily_baseline(self):
        caller=Caller(decision())
        result=self.reflect(caller)
        self.assertEqual(result['status'],'partial',result)
        self.assertEqual(result['reflection']['missing_count'],1)
        self.assertEqual(self.reflect(Caller())['status'],'skipped')
        resumed=self.reflect(Caller(decision([self.rating('very_good')])),force=True)
        self.assertEqual(resumed['status'],'applied',resumed)
        self.assertEqual(resumed['reflection']['ai_calls'],2)

    def test_same_day_correction_replaces_previous_grade_and_rankings_are_directed(self):
        caller=Caller(decision([self.rating('very_good')],True),decision([self.rating('neutral',1)]))
        result=self.reflect(caller)
        self.assertEqual(result['status'],'applied',result)
        with self.connect() as conn:
            row=conn.execute('SELECT * FROM wanderer_affinity_ratings').fetchone()
            self.assertEqual(row['baseline_affinity'],0)
            self.assertEqual(row['new_affinity'],0)
            outgoing=relationship_rankings(conn,self.w['id'])
            incoming=relationship_rankings(conn,self.friend['id'],direction='incoming')
            self.assertEqual(outgoing['items'][0]['last_rating'],'neutral')
            self.assertEqual(incoming['items'][0]['peer_identity'],self.me)
            self.assertEqual(relationship_rankings(conn,self.w['id'],direction='incoming')['items'],[])
            self.assertEqual(outgoing['reflection']['rated_count'],1)
            self.assertEqual(relationship_rankings(conn,self.w['id'],query=self.friend['display_name'])['total'],1)
            self.assertEqual(relationship_rankings(conn,self.w['id'],query='not-a-person')['total'],0)

    def test_operation_replay_is_idempotent_and_revision_conflicts_are_visible(self):
        self.reflect(Caller(decision([self.rating('good')])))
        with self.connect() as conn:
            event=dict(conn.execute('SELECT * FROM wanderer_wake_events').fetchone())
            turn=dict(conn.execute('SELECT * FROM wanderer_turns').fetchone())
            run={**event,**turn,'wanderer':self.w,'scope':{'kind':'private'}}
            op=conn.execute('SELECT operation_id FROM wanderer_affinity_rating_operations').fetchone()[0]
            replay=daily.rate_friend(conn,run,self.peer,'good',expected_revision=0,operation_id=op)
            self.assertTrue(replay['replayed'])
            with self.assertRaises(Exception):
                daily.rate_friend(conn,run,self.peer,'averse',expected_revision=0,operation_id='stale')
            self.assertEqual(affinity_state(conn,self.w['id'],self.peer)['affinity'],5)

    def test_fake_reflection_source_and_absolute_score_cannot_bypass_entitlement(self):
        caller=Caller(decision([self.rating()]))
        result=asyncio.run(runtime.wake_wanderer(self.connect,self.w['id'],source='reflection',event_key='fake',
                           context={'scope':{'kind':'private'},'daily_reflection':{'free_remaining':3}},ai_caller=caller,force=True))
        self.assertEqual(result['status'],'capped',result)
        self.assertFalse(caller.calls)
        result=self.reflect(Caller(decision([command('relationship.set_affinity',
            {'peer_ref':self.peer,'value':100,'expected_revision':0})])))
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['runtime']['receipts'][0]['status'],'denied')
        with self.connect() as conn:
            self.assertEqual(affinity_state(conn,self.w['id'],self.peer)['affinity'],0)

    def test_protocol_repair_consumes_free_request(self):
        caller=Caller({'wrong':'shape'},decision([self.rating()],True),decision())
        result=self.reflect(caller)
        self.assertEqual(result['status'],'applied',result)
        self.assertEqual(result['reflection']['ai_calls'],3)
        self.assertEqual(len(caller.calls),3)
        self.assertIn('计入每日自省三次免费请求',caller.calls[1]['system_prompt'])

    def test_context_switch_cannot_escape_three_request_limit(self):
        caller=Caller(decision([command('context.switch',{'scope':{'kind':'public'}})],True),
                      decision([self.rating()],True),decision())
        result=self.reflect(caller)
        self.assertEqual(len(caller.calls),3)
        drained=asyncio.run(runtime.drain_wake_events(self.connect,ai_caller=Caller()))
        self.assertEqual(drained['ai_calls'],0,drained)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT COUNT(DISTINCT chain_id) FROM wanderer_wake_events').fetchone()[0],1)

    def test_deferred_callback_crosses_midnight_but_scores_original_day(self):
        previous=local_now()-timedelta(days=1)
        with patch.object(runtime,'MAX_CHAIN_DEPTH',0):
            result=self.reflect(Caller(decision([],True)),date=previous)
        self.assertEqual(result['status'],'running',result)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_wake_events SET run_at=? WHERE state='pending'",(local_iso(),))
            conn.commit()
        drained=asyncio.run(runtime.drain_wake_events(self.connect,ai_caller=Caller(decision([self.rating()]))))
        self.assertEqual(drained['ai_calls'],1,drained)
        with self.connect() as conn:
            rating=conn.execute('SELECT reflection_date FROM wanderer_affinity_ratings').fetchone()[0]
            session=conn.execute('SELECT ai_calls,status FROM wanderer_reflection_sessions').fetchone()
            self.assertEqual(rating,previous.date().isoformat())
            self.assertEqual(tuple(session),(2,'applied'))

    def test_two_concurrent_daily_starts_dispatch_once(self):
        async def scenario():
            entered,release=asyncio.Event(),asyncio.Event()
            calls=[]
            async def provider(endpoint,payload,**kwargs):
                calls.append(payload); entered.set(); await release.wait()
                return {'status':'success','response_json':decision([self.rating()])}
            one=asyncio.create_task(run_reflection(ActionContext(self.connect,self.w,ai_caller=provider)))
            await entered.wait()
            two=await run_reflection(ActionContext(self.connect,self.w,ai_caller=provider))
            release.set()
            first=await one
            return calls,first,two
        calls,first,second=asyncio.run(scenario())
        self.assertEqual(len(calls),1)
        self.assertEqual(first['status'],'applied',first)
        self.assertEqual(second['ai_calls'],0)

    def test_disabled_and_paused_do_not_use_legacy_ai_chain(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','0'); conn.commit()
        caller=Caller()
        self.assertEqual(self.reflect(caller,force=True)['status'],'disabled')
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','1')
            wanderer_runtime_setting_set(conn,'enabled','0'); conn.commit()
        self.assertEqual(self.reflect(caller,force=True)['status'],'disabled')
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,'enabled','1')
            conn.execute("UPDATE wanderer_agents SET status='paused' WHERE id=?",(self.w['id'],)); conn.commit()
        self.assertEqual(self.reflect(caller,force=True)['status'],'disabled')
        self.assertFalse(caller.calls)

    def test_501_friend_roster_is_bounded_and_finishes_in_two_calls(self):
        with self.connect() as conn:
            for index in range(500):
                tid=conn.execute('INSERT INTO teachers(name,email,hashed_password) VALUES(?,?,?) RETURNING id',
                    (f'容量合成好友{index}',f'capacity-{index}@example.test','synthetic-only')).fetchone()[0]
                a,b=sorted([self.me,f'teacher:{tid}'])
                conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) "
                    "VALUES(?,?,'accepted',?,?)",(a,b,self.me,local_iso()))
            conn.commit()
        rosters=[]
        def grade_roster(payload):
            roster=json.loads(payload['system_prompt'].split('【本次自省尚未评价的好友：有界实时名单】')[1].split('\n\n',1)[0])
            rosters.append(roster)
            ratings=[{'peer_ref':item['peer_identity'],'grade':'neutral','expected_revision':item['revision']}
                     for item in roster['items']]
            return decision([command('relationship.rate_many',{'ratings':ratings})],bool(roster['next_offset']))
        result=self.reflect(Caller(grade_roster,grade_roster))
        self.assertEqual(result['status'],'applied',result)
        self.assertEqual([len(item['items']) for item in rosters],[500,1])
        self.assertEqual(result['reflection']['rated_count'],501)
        self.assertEqual(result['reflection']['missing_count'],0)
        self.assertEqual(result['reflection']['ai_calls'],2)

    def test_resume_after_authority_rotation_keeps_used_allowance(self):
        self.reflect(Caller(decision()))
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_agents SET authority_version=authority_version+1 WHERE id=?',(self.w['id'],))
            conn.commit()
        result=self.reflect(Caller(decision([self.rating()])),force=True)
        self.assertEqual(result['status'],'applied',result)
        self.assertEqual(result['reflection']['ai_calls'],2)

    def test_failed_callback_keeps_whole_session_running_until_delayed_retry_succeeds(self):
        def fail(_):
            raise RuntimeError('synthetic provider unavailable')
        result=self.reflect(Caller(decision([self.rating()],True),fail))
        self.assertEqual(result['status'],'running',result)
        self.assertEqual(result['reflection']['missing_count'],0)
        premature=Caller()
        self.assertEqual(asyncio.run(runtime.drain_wake_events(self.connect,ai_caller=premature))['ai_calls'],0)
        self.assertFalse(premature.calls)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_wake_events SET run_at=? WHERE source='model_retry'",(local_iso(),))
            conn.commit()
        resumed=asyncio.run(runtime.drain_wake_events(self.connect,ai_caller=Caller(decision())))
        self.assertEqual(resumed['ai_calls'],1)
        with self.connect() as conn:
            state=daily.snapshot(conn,self.w)
            self.assertEqual(state['status'],'applied')
            self.assertEqual(state['ai_calls'],3)

    def test_explicit_manual_reflection_advances_existing_model_retry_without_extra_entitlement(self):
        def fail(_):
            raise RuntimeError('synthetic provider unavailable')
        result=self.reflect(Caller(decision([self.rating()],True),fail))
        self.assertEqual(result['status'],'running',result)
        resumed=self.reflect(Caller(decision()),force=True)
        self.assertEqual(resumed['status'],'applied',resumed)
        self.assertEqual(resumed['reflection']['ai_calls'],3)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='model_retry'").fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_sessions').fetchone()[0],1)

    def test_old_day_callback_cannot_overwrite_newer_day_rating(self):
        previous=local_now()-timedelta(days=1)
        old=self.reflect(Caller(decision([self.rating('good')])),date=previous)
        self.assertEqual(old['status'],'applied',old)
        new=self.reflect(Caller(decision([self.rating('very_good',1)])))
        self.assertEqual(new['status'],'applied',new)
        with self.connect() as conn:
            before=affinity_state(conn,self.w['id'],self.peer)
            event=dict(conn.execute("SELECT * FROM wanderer_wake_events WHERE source='reflection' AND event_key=?",
                (previous.date().isoformat(),)).fetchone())
            run={**event,'wanderer':self.w,'scope':{'kind':'private'}}
            with self.assertRaisesRegex(Exception,'旧日回调不能覆盖'):
                daily.rate_friend(conn,run,self.peer,'averse',expected_revision=2,operation_id='old-day-overwrite')
            self.assertEqual(affinity_state(conn,self.w['id'],self.peer),before)

    def test_abandoned_callback_cannot_leave_daily_panel_running_forever(self):
        with patch.object(runtime,'MAX_CHAIN_DEPTH',0):
            result=self.reflect(Caller(decision([self.rating()],True)))
        self.assertEqual(result['status'],'running')
        with self.connect() as conn:
            event=conn.execute("SELECT * FROM wanderer_wake_events WHERE state='pending'").fetchone()
            conn.execute('UPDATE wanderer_wake_events SET run_at=? WHERE event_id=?',(local_iso(),event['event_id']))
            run=runtime._claim(conn,event['event_id'])
            conn.execute("UPDATE wanderer_runtime_leases SET lease_until='2000-01-01T00:00:00'")
            conn.commit()
        drained=asyncio.run(runtime.drain_wake_events(self.connect,ai_caller=Caller()))
        self.assertEqual(drained['ai_calls'],0)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM wanderer_daily_reflections').fetchone()[0],'partial')
            self.assertEqual(conn.execute('SELECT ai_calls FROM wanderer_reflection_sessions').fetchone()[0],1)
