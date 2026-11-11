"""AI communication delivery, membership wake-ups and exact credit prices."""
import asyncio
import sqlite3
import unittest
from unittest.mock import patch
from classroom_app.services import social_message_service as messages
from classroom_app.services import social_group_service as groups
from classroom_app.services import wanderer_inbox_worker as worker
from classroom_app.services.social_common import SocialError
from classroom_app.services.wanderer_relationship_service import set_affinity
from classroom_app.services.wanderer_social_budget_service import message_cost, quote_message, DISPLAY_UNIT
from classroom_app.services.wanderer_actions_social import _gate, load_window, watch_group
from classroom_app.services.wanderer_social_outreach import outreach_candidates
from classroom_app.services.wanderer_actions_social import social_context
from tests import test_wanderer_social as support
from tests.wanderer_behavior_support import WandererWorldTestCase, StubCaller, ZeroSleep, turn


class OrdinaryMessageBudgetTest(unittest.TestCase):
    def test_human_delivery_never_initializes_ai_world_tables(self):
        from classroom_app.services.wanderer_social_budget_service import charge_message
        with sqlite3.connect(':memory:') as conn:
            conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE else sqlite3.SQLITE_OK)
            result = charge_message(conn, 'student:1', {'type':'dm','key':'dm:student:1:student:2','peer':'student:2'}, message_id=1)
            self.assertFalse(result['charged'])
            self.assertEqual(result['cultivation_gain'], 0)
            self.assertEqual(conn.execute('SELECT name FROM sqlite_master').fetchall(), [])


class SocialV2Test(WandererWorldTestCase):
    ident=staticmethod(support.WandererSocialTest.ident)
    user_of=staticmethod(support.WandererSocialTest.user_of)
    call=support.WandererSocialTest.call
    befriend=support.WandererSocialTest.befriend
    send=support.WandererSocialTest.send
    make_group=support.WandererSocialTest.make_group

    def setUp(self):
        super().setUp()
        self.real=f"student:{self.student_pk}"
        self.atai=self.set_wanderer("atai",schedule_json=support.ALWAYS_AWAKE,behaviors_json={})
        self.lele=self.set_wanderer("lele",schedule_json=support.ALWAYS_AWAKE,behaviors_json={})
        self.xiaoman=self.set_wanderer("xiaoman",schedule_json=support.ALWAYS_AWAKE,behaviors_json={})
        self.enterContext(patch.object(support.social_push_service,"push",support.PushRecorder()))
        self.enterContext(patch.object(support.social_push_service,"is_app_process",return_value=True))
        with self.connect() as conn:
            support.grant_tier(conn,self.offering_id,self.student_pk,3)
            from classroom_app.db.schema_wanderer_world import ensure_wanderer_world_schema
            ensure_wanderer_world_schema(conn)
            for table in ("wanderer_relationship_changes","wanderer_relationship_versions"):
                conn.execute(f"DELETE FROM {table}")
            # Each case owns fresh resources and model decisions.
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'wanderer_resource_%'").fetchall():
                conn.execute(f"DELETE FROM {row['name']}")
            conn.execute("DELETE FROM wanderer_bonds")
            conn.execute("DELETE FROM wanderer_wake_events")
            for table in ('wanderer_commands','wanderer_turns','wanderer_runtime_leases'):
                conn.execute(f'DELETE FROM {table}')
            conn.commit()

    def affinity(self,conn,actor,peer,value,operation):
        return set_affinity(conn,actor,peer,value,expected_revision=0,source_turn_id="social-test",operation_id=operation)

    def test_exact_threefold_cost_and_affinity(self):
        for value in (0,1,33,50,99,100):
            self.assertEqual(message_cost(value),3*message_cost(value,partner_dm=True))
        self.assertEqual(message_cost(0),DISPLAY_UNIT)
        self.assertEqual(message_cost(100),DISPLAY_UNIT//2)

    def test_ai_dm_multi_round_and_later_outreach(self):
        key=self.befriend(self.ident(self.atai),self.lele)
        self.send(self.ident(self.atai),key,"从今天的见闻聊起")
        caller=StubCaller(turn("reply",["我想继续讨论这个问题"]))
        for _ in range(3):
            with self.connect() as conn:
                conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01' WHERE state IN ('pending','retry')")
                conn.commit()
            result=asyncio.run(worker.process_once(factory=self.connect,ai_caller=caller,sleep=ZeroSleep()))
            with self.connect() as conn:
                errors=[dict(r) for r in conn.execute('SELECT state,last_error FROM wanderer_social_inbox').fetchall()]
            self.assertIn("done",result,errors)
        self.assertEqual(len(caller.calls),3)
        with self.connect() as conn:
            rows=conn.execute("SELECT * FROM social_messages WHERE conversation_key=? AND message_type='text'",(key,)).fetchall()
            self.assertEqual(len(rows),4)
            window=load_window(conn,self.ident(self.atai),key)
            self.assertNotEqual(_gate(conn,self.ident(self.atai),window,require_new=False)[0],"wanderer_loop")
            ctx,_=social_context(self.atai["id"],conn_factory=self.connect)
            self.assertIn(self.ident(self.lele),outreach_candidates(conn,ctx))

    def test_group_created_and_joined_events_have_no_fake_messages(self):
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,"autonomous_v2_enabled","1")
            conn.commit()
        gid,key=self.make_group(self.atai,self.lele)
        with self.connect() as conn:
            rows=conn.execute("SELECT message_type FROM social_messages WHERE conversation_key=?",(key,)).fetchall()
            self.assertEqual([r[0] for r in rows],["system"])
            events=conn.execute("SELECT * FROM wanderer_wake_events WHERE source='group_created'").fetchall()
            self.assertEqual(len(events),2)
            self.assertTrue(all(key in e["context_json"] for e in events))
        self.befriend(self.real,self.xiaoman)
        self.call(groups.add_members,self.user_of(self.real),gid,[self.ident(self.xiaoman)])
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='group_joined'").fetchone()[0],1)

    def test_mixed_group_price_uses_actual_ai_recipients(self):
        gid,key=self.make_group(self.atai,self.lele,self.xiaoman)
        from classroom_app.services.social_conversation_service import require_access
        with self.connect() as conn:
            self.affinity(conn,self.atai,self.ident(self.lele),100,"a-b")
            self.affinity(conn,self.atai,self.ident(self.xiaoman),0,"a-c")
            parsed=require_access(conn,self.ident(self.atai),key)
            quote=quote_message(conn,self.ident(self.atai),parsed)
            self.assertEqual(quote["affinity"],50)
            self.assertEqual(quote["amount"],message_cost(50))
            self.assertFalse(quote["partner_dm"])
            self.assertFalse(quote_message(conn,self.real,parsed)["charged"])

    def test_send_debit_nonce_and_failure_are_one_transaction(self):
        key=self.befriend(self.ident(self.atai),self.lele)
        from classroom_app.services.wanderer_resource_service import snapshot
        with self.connect() as conn:
            before=snapshot(conn,self.atai["id"],"social_message")
            message=messages.send_message(conn,self.user_of(self.ident(self.atai)),key,{"body":"hello","client_nonce":"one-send"})
            again=messages.send_message(conn,self.user_of(self.ident(self.atai)),key,{"body":"hello","client_nonce":"one-send"})
            self.assertEqual(message["id"],again["id"])
            after=snapshot(conn,self.atai["id"],"social_message")
            self.assertNotEqual(before,after)
            count=conn.execute("SELECT COUNT(*) FROM social_messages").fetchone()[0]
            with patch("classroom_app.services.wanderer_resource_service.consume",return_value={"ok":False}):
                with self.assertRaises(SocialError):
                    messages.send_message(conn,self.user_of(self.ident(self.atai)),key,{"body":"unpaid","client_nonce":"insufficient"})
            conn.commit() # Catching rejection still cannot commit the unpaid row.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages").fetchone()[0],count)
            self.assertEqual(snapshot(conn,self.atai["id"],"social_message"),after)

    def bond_fixture(self,conn,a,b):
        first,second=sorted((a["id"],b["id"]))
        conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) "
                     "VALUES(?,?,?,?,'active','synthetic','synthetic','2026-11-09','2026-11-09')",
                     (f"bond-{first}-{second}",f"{first}:{second}",first,second))

    def test_partner_rate_and_borrowed_resource_use_senders_affinity(self):
        key=self.befriend(self.ident(self.atai),self.xiaoman)
        bondkey=self.befriend(self.ident(self.atai),self.lele)
        from classroom_app.services.social_conversation_service import require_access
        from classroom_app.services.wanderer_resource_service import snapshot, consume
        with self.connect() as conn:
            self.bond_fixture(conn,self.atai,self.lele)
            self.affinity(conn,self.atai,self.ident(self.lele),33,"partner-affinity")
            self.affinity(conn,self.atai,self.ident(self.xiaoman),100,"target-affinity")
            quoted=quote_message(conn,self.ident(self.atai),require_access(conn,self.ident(self.atai),bondkey))
            self.assertTrue(quoted["partner_dm"])
            self.assertEqual(quoted["amount"]*3,message_cost(33))
            own=snapshot(conn,self.atai["id"],"social_message",allow_share=False)
            self.assertTrue(consume(conn,self.atai["id"],"social_message",own["remaining"],"use-own",allow_share=False)["ok"])
            delivered=messages.send_message(conn,self.user_of(self.ident(self.atai)),key,{"body":"borrowed delivery","client_nonce":"borrowed"})
            receipt=conn.execute("SELECT lines_json FROM wanderer_resource_reservations WHERE operation_id=?",
                                 (f"social-message:{delivered['id']}",)).fetchone()
            import json
            lines=json.loads(receipt["lines_json"])
            self.assertEqual(lines[0]["owner_id"],self.lele["id"])
            self.assertEqual(lines[0]["units"],message_cost(100)) # donor likes the target zero; sender likes target 100

    def test_two_independent_sends_cannot_spend_the_last_credit_twice(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        from classroom_app.services.wanderer_resource_service import snapshot
        key=self.befriend(self.ident(self.atai),self.lele)
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,"social_message_base","1")
            conn.commit()
        ready=Barrier(2)
        def attempt(index):
            with self.connect() as conn:
                ready.wait(timeout=10)
                try:
                    messages.send_message(conn,self.user_of(self.ident(self.atai)),key,
                                          {"body":f"race-{index}","client_nonce":f"race-{index}"})
                    conn.commit()
                    return "sent"
                except SocialError as exc:
                    conn.commit() # savepoint must already have undone the rejected delivery
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(attempt,(1,2)))
        self.assertEqual(results.count("sent"),1)
        self.assertEqual(results.count(429),1)
        with self.connect() as conn:
            self.assertEqual(snapshot(conn,self.atai["id"],"social_message")["remaining"],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages WHERE conversation_key=? AND message_type='text'",(key,)).fetchone()[0],1)

    def test_real_group_protocol_sends_six_rounds_and_preserves_scope(self):
        import json
        from fastapi import FastAPI
        from classroom_app.routers.social import router
        from classroom_app.services import wanderer_platform_service as platform
        from classroom_app.services.wanderer_group_runtime_service import run_group_runtime
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        from classroom_app.time_utils import local_iso
        private=self.befriend(self.ident(self.atai),self.lele)
        self.send(self.ident(self.atai),private,"PRIVATE_SENTINEL_ONLY_IN_DM")
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,"autonomous_v2_enabled","1")
            conn.commit()
        gid,key=self.make_group(self.atai,self.lele)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET born_at=?,lifespan_weeks=10,life_penalty_weeks=0,daily_wake_limit=100",(local_iso(),))
            conn.commit()
        app=FastAPI()
        app.include_router(router)
        calls=[]
        async def caller(endpoint,payload,**kwargs):
            calls.append(payload)
            prompt=payload["system_prompt"]
            self.assertNotIn("PRIVATE_SENTINEL_ONLY_IN_DM",prompt)
            context=json.loads(prompt.split("<<<data>>>\n")[1].split("\n<<<end>>>")[0])
            self.assertEqual(context["conversation_key"],key)
            commands=[]
            if context["event_kind"]=="group_message":
                commands=[{"id":"say","capability":"social.send_message",
                           "args":{"path_params":{"key":key},"body":{"body":f"第{len(calls)}轮的新想法", "client_nonce":f"group-real-{len(calls)}"}}}]
            return {"status":"success","response_json":{"protocol":"wanderer.v1","commands":commands,"callback":{"requested":False}}}
        with patch.object(platform,"_app",return_value=app):
            initial=asyncio.run(run_group_runtime(gid,conn_factory=self.connect,ai_caller=caller))
            self.assertEqual(initial["ai_calls"],2,initial)
            self.send(self.real,key,"开始我们的讨论")
            results=[asyncio.run(run_group_runtime(gid,conn_factory=self.connect,ai_caller=caller)) for _ in range(3)]
        self.assertEqual(sum(r["ai_calls"] for r in results),6,results)
        receipts=[receipt for result in results for out in result["scheduled"] for receipt in out.get("receipts",[])]
        self.assertEqual(len(receipts),6,receipts)
        self.assertTrue(all(r["status"]=="succeeded" and r.get("result",{}).get("verified_business") is True for r in receipts),receipts)
        with self.connect() as conn:
            texts=conn.execute("SELECT * FROM social_messages WHERE conversation_key=? AND message_type='text' AND sender_identity<>?",(key,self.real)).fetchall()
            self.assertEqual(len(texts),6)
            self.assertEqual(len({r["sender_identity"] for r in texts}),2)
            states=conn.execute("SELECT handled_message_id FROM wanderer_group_observations WHERE group_id=?",(gid,)).fetchall()
            self.assertTrue(all(r["handled_message_id"]>0 for r in states))

    def test_group_protocol_repair_after_next_day_budget_advances_original_watermark(self):
        from datetime import datetime,timedelta
        from fastapi import FastAPI
        from classroom_app.routers.social import router
        from classroom_app.services import wanderer_platform_service as platform,wanderer_runtime_service as runtime
        from classroom_app.services.wanderer_group_runtime_service import run_member_group
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        from classroom_app.time_utils import local_iso
        from tests.test_wanderer_runtime_v2 import Caller,decision,command
        gid,key=self.make_group(self.atai,self.lele)
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','1')
            conn.execute("UPDATE wanderer_agents SET born_at=?,lifespan_weeks=10,life_penalty_weeks=0,daily_wake_limit=1,tier=0",(local_iso(),))
            conn.execute("UPDATE wanderer_wake_events SET run_at='2100-01-01' WHERE source IN ('group_created','group_joined')")
            conn.commit()
        self.send(self.real,key,'合成群聊中的新问题')
        app=FastAPI();app.include_router(router)
        bad=Caller(decision([],True,['self.status']))
        with patch.object(platform,'_app',return_value=app):
            result=asyncio.run(run_member_group(self.atai,gid,conn_factory=self.connect,ai_caller=bad))
        self.assertEqual(result['status'],'capped',result)
        self.assertEqual(len(bad.calls),1)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT handled_message_id FROM wanderer_group_observations WHERE group_id=? AND wanderer_id=?',(gid,self.atai['id'])).fetchone()[0],0)
        later=Caller(decision([command('social.send_message',{'path_params':{'key':key},'body':{'body':'修复后由我自主回复。','client_nonce':'repair-group'}})]))
        tomorrow=datetime.fromisoformat(local_iso())+timedelta(days=1)
        with patch.object(platform,'_app',return_value=app), \
             patch('classroom_app.time_utils.local_now',return_value=tomorrow), \
             patch('classroom_app.services.wanderer_settings_service.local_now',return_value=tomorrow), \
             patch.object(runtime,'local_now',return_value=tomorrow), \
             patch('classroom_app.services.wanderer_resource_service.local_now',return_value=tomorrow):
            drained=asyncio.run(runtime.drain_wake_events(self.connect,ai_caller=later,limit=1))
            self.assertEqual(drained['ai_calls'],1,drained)
            replay=asyncio.run(run_member_group(self.atai,gid,conn_factory=self.connect,ai_caller=later))
            self.assertEqual(replay['status'],'ok',replay)
            self.assertEqual(replay['ai_calls'],0)
        with self.connect() as conn:
            self.assertGreater(conn.execute('SELECT handled_message_id FROM wanderer_group_observations WHERE group_id=? AND wanderer_id=?',(gid,self.atai['id'])).fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages WHERE conversation_key=? AND sender_identity=? AND message_type='text'",(key,self.ident(self.atai))).fetchone()[0],1)

    def test_recovery_restores_missing_group_trigger_without_fake_text(self):
        from classroom_app.services.wanderer_group_runtime_service import recover_group_events
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        gid,key=self.make_group(self.atai,self.lele)
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,"autonomous_v2_enabled","1")
            conn.execute("DELETE FROM scheduled_tasks WHERE task_kind='wanderer_group_watch'")
            conn.execute("DELETE FROM wanderer_wake_events")
            conn.execute("DELETE FROM wanderer_group_observations")
            before=conn.execute("SELECT COUNT(*) FROM social_messages").fetchone()[0]
            self.assertEqual(recover_group_events(conn),1)
            self.assertEqual(recover_group_events(conn),0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='group_joined'").fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages").fetchone()[0],before)
            conn.commit()

    def test_group_budget_and_parse_failure_preserve_unseen_watermark(self):
        from classroom_app.services.wanderer_group_runtime_service import run_member_group
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        from classroom_app.time_utils import local_iso
        gid,key=self.make_group(self.atai)
        incoming=self.send(self.real,key,"我有一个新问题")
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,"autonomous_v2_enabled","1")
            conn.execute("UPDATE wanderer_agents SET born_at=?,lifespan_weeks=10,daily_wake_limit=0 WHERE id=?",(local_iso(),self.atai["id"]))
            conn.commit()
        caller=StubCaller("not json")
        capped=asyncio.run(run_member_group(self.atai,gid,conn_factory=self.connect,ai_caller=caller))
        self.assertEqual(capped["status"],"capped",capped)
        self.assertEqual(caller.calls,[])
        with self.connect() as conn:
            state=conn.execute("SELECT handled_message_id FROM wanderer_group_observations WHERE group_id=?",(gid,)).fetchone()
            self.assertEqual(state["handled_message_id"],0)
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=100 WHERE id=?",(self.atai["id"],))
            conn.commit()
        # A new real input is a new decision event; failed parsing cannot acknowledge either message.
        self.send(self.real,key,"补充第二句话")
        failed=asyncio.run(run_member_group(self.atai,gid,conn_factory=self.connect,ai_caller=caller))
        self.assertEqual(failed["status"],"failed",failed)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT handled_message_id FROM wanderer_group_observations WHERE group_id=?",(gid,)).fetchone()[0],0)
            cursor=conn.execute("SELECT last_read_message_id FROM social_group_members WHERE group_id=? AND member_identity=?",(gid,self.ident(self.atai))).fetchone()[0]
            self.assertLess(cursor,incoming["id"])

    def test_each_segment_and_nudge_charges_and_awards_once(self):
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        from classroom_app.services.wanderer_resource_service import snapshot
        key=self.befriend(self.ident(self.atai),self.lele)
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,"autonomous_v2_enabled","1")
            user=self.user_of(self.ident(self.atai))
            first=messages.send_message(conn,user,key,{"body":"第一段", "client_nonce":"segment-1"})
            second=messages.send_message(conn,user,key,{"body":"第二段", "client_nonce":"segment-2"})
            nudged=messages.nudge(conn,user,key)
            messages.send_message(conn,user,key,{"body":"第一段", "client_nonce":"segment-1"})
            self.assertEqual(snapshot(conn,self.atai["id"],"social_message")["used"],3*DISPLAY_UNIT)
            for item in (first,second,nudged):
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_cultivation_awards WHERE event_id=?",
                                              (f"social-message:{item['id']}",)).fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_action_log WHERE wanderer_id=? AND action_kind='social_message'",
                                          (self.atai["id"],)).fetchone()[0],3)

    def test_long_wanderer_thread_passes_without_a_model_call(self):
        from classroom_app.services.wanderer_actions_social import GROUP_CONVERSATION_LIMIT
        from classroom_app.services.wanderer_group_runtime_service import run_member_group
        gid, key = self.make_group(self.atai, self.lele)
        for index in range(GROUP_CONVERSATION_LIMIT):
            self.send(self.ident(self.atai), key, f"散修轮次{index}")
        caller = StubCaller()
        result = asyncio.run(run_member_group(self.lele, gid, conn_factory=self.connect, ai_caller=caller))
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["ai_calls"], 0)
        self.assertEqual(caller.calls, [])
        with self.connect() as conn:
            row = conn.execute("SELECT handled_message_id FROM wanderer_group_observations "
                               "WHERE wanderer_id=? AND group_id=?", (self.lele["id"], gid)).fetchone()
            self.assertGreater(int(row["handled_message_id"]), 0)


class GroupConversationBudgetTest(unittest.TestCase):
    def test_mention_always_wakes_and_long_wanderer_threads_pass(self):
        from classroom_app.services.wanderer_actions_social import (
            GROUP_CONVERSATION_LIMIT, group_reply_probability)
        wanderer = {"activity_level": 3, "reply_policy": {}, "archetype": ""}
        self.assertEqual(group_reply_probability(wanderer, interest=0, mentioned=True, streak=9,
                                                 last_is_wanderer=True, sent_today=99,
                                                 rounds_since_human=99), 1.0)
        self.assertEqual(group_reply_probability(wanderer, interest=0, mentioned=False, streak=1,
                                                 last_is_wanderer=True, sent_today=0,
                                                 rounds_since_human=GROUP_CONVERSATION_LIMIT - 1), 1.0)
        self.assertEqual(group_reply_probability(wanderer, interest=0, mentioned=False, streak=1,
                                                 last_is_wanderer=True, sent_today=0,
                                                 rounds_since_human=GROUP_CONVERSATION_LIMIT), 0.0)
        self.assertEqual(group_reply_probability(wanderer, interest=0, mentioned=False, streak=0,
                                                 last_is_wanderer=False, sent_today=0,
                                                 rounds_since_human=99), 1.0)
