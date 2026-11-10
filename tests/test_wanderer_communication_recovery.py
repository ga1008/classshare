"""Natural adapter failure recovery and readable callback working context."""
import asyncio
import json
from unittest.mock import patch

from fastapi import FastAPI
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services import wanderer_inbox_service as inbox, wanderer_inbox_worker as worker
from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso
from tests import test_wanderer_social as social_support
from tests.wanderer_behavior_support import WandererWorldTestCase, ZeroSleep
from tests.test_wanderer_runtime_v2 import Caller, command, decision


class CommunicationRecoveryTest(WandererWorldTestCase):
    ident = staticmethod(social_support.WandererSocialTest.ident)
    user_of = staticmethod(social_support.WandererSocialTest.user_of)
    call = social_support.WandererSocialTest.call
    befriend = social_support.WandererSocialTest.befriend
    send = social_support.WandererSocialTest.send
    make_group = social_support.WandererSocialTest.make_group

    def setUp(self):
        super().setUp()
        self.real = f'student:{self.student_pk}'
        self.w = self.set_wanderer('atai', behaviors_json={}, schedule_json=social_support.ALWAYS_AWAKE)
        self.enterContext(patch.object(social_support.social_push_service,'push',social_support.PushRecorder()))
        self.enterContext(patch.object(social_support.social_push_service,'is_app_process',return_value=True))
        with self.connect() as conn:
            social_support.grant_tier(conn,self.offering_id,self.student_pk,3)
            for table in ('wanderer_message_deliveries','wanderer_commands','wanderer_turns',
                          'wanderer_wake_events','wanderer_runtime_leases','wanderer_group_observations'):
                conn.execute(f'DELETE FROM {table}')
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','1')
            conn.execute('UPDATE wanderer_agents SET born_at=?,lifespan_weeks=10,life_penalty_weeks=0,daily_wake_limit=100', (local_iso(),))
            conn.commit()
        from classroom_app.routers.social import router as social_router
        from classroom_app.routers.message_center import router as legacy_router
        self.app = FastAPI(); self.app.include_router(social_router); self.app.include_router(legacy_router)
        self.enterContext(patch.object(platform,'_app',return_value=self.app))

    def ready(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01' WHERE state IN ('pending','retry')")
            conn.execute("UPDATE wanderer_wake_events SET run_at='2000-01-01' WHERE source='model_retry'")
            conn.commit()

    def process(self, caller):
        self.ready()
        return asyncio.run(worker.process_once(factory=self.connect,ai_caller=caller,sleep=ZeroSleep()))

    @staticmethod
    async def outage(*args, **kwargs):
        raise TimeoutError('synthetic provider timeout')

    def test_native_and_legacy_dm_retry_once_then_continue_without_duplicate(self):
        from classroom_app.services.message_center_service import create_private_message
        for channel in ('social','legacy'):
            with self.subTest(channel=channel):
                if channel=='social':
                    key=self.befriend(self.real,self.w)
                    incoming=self.send(self.real,key,'合成来信，需要正常后续处理')
                    send=command('social.send_message',{'path_params':{'key':key},'body':{'body':'已恢复，这是唯一回复'}})
                else:
                    with self.connect() as conn:
                        incoming=create_private_message(conn,self.student,contact_identity=self.ident(self.w),content='旧入口合成来信')['message']
                        conn.commit()
                    key=incoming['conversation_key']
                    send=command('legacy.send_message',{'body':{'contact_identity':self.real,'content':'已恢复，这是唯一回复'}})
                self.assertEqual(self.process(self.outage),['retry'])
                with self.connect() as conn:
                    state=conn.execute('SELECT * FROM wanderer_social_inbox WHERE conversation_key=?',(key,)).fetchone()
                    self.assertEqual(state['handled_message_id'],0)
                caller=Caller(decision([send]))
                self.assertEqual(self.process(caller),['done'])
                self.assertEqual(len(caller.calls),1)
                self.assertEqual(self.process(caller),[])
                with self.connect() as conn:
                    state=conn.execute('SELECT * FROM wanderer_social_inbox WHERE conversation_key=?',(key,)).fetchone()
                    self.assertEqual(state['handled_message_id'],incoming['id'])
                    table='social_messages' if channel=='social' else 'private_messages'
                    self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE conversation_key=? AND sender_identity=?',
                        (key,self.ident(self.w))).fetchone()[0],1)

    def test_group_model_retry_advances_original_incoming_watermark(self):
        from classroom_app.services.wanderer_group_runtime_service import run_member_group
        gid,key=self.make_group(self.w)
        incoming=self.send(self.real,key,'群聊等待实际回复')
        first=asyncio.run(run_member_group(self.w,gid,conn_factory=self.connect,ai_caller=self.outage))
        self.assertEqual(first['status'],'failed',first)
        self.ready()
        caller=Caller(decision([command('social.send_message',{'path_params':{'key':key},'body':{'body':'群聊已恢复'}})]))
        second=asyncio.run(run_member_group(self.w,gid,conn_factory=self.connect,ai_caller=caller))
        self.assertEqual(second['status'],'ok',second)
        self.assertEqual(len(caller.calls),1)
        replay=asyncio.run(run_member_group(self.w,gid,conn_factory=self.connect,ai_caller=caller))
        self.assertEqual(replay['status'],'nothing_new',replay)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT handled_message_id FROM wanderer_group_observations WHERE wanderer_id=? AND group_id=?',
                (self.w['id'],gid)).fetchone()[0],incoming['id'])

    def test_retry_delay_and_chain_limit_do_not_burn_unbounded_credits(self):
        calls=[]
        async def outage(*args,**kwargs):
            calls.append(True)
            return await self.outage(*args,**kwargs)
        def wake():
            return asyncio.run(runtime.wake_wanderer(self.connect,self.w['id'],source='timer',event_key='retry-limit',ai_caller=outage))
        self.assertEqual(wake()['ai_calls'],1)
        self.assertEqual(wake()['ai_calls'],0) # delayed; no second immediate request
        self.ready()
        self.assertEqual(wake()['ai_calls'],1)
        self.assertEqual(wake()['ai_calls'],0)
        self.assertEqual(len(calls),2)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='model_retry'").fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_turns WHERE dispatched_at IS NOT NULL').fetchone()[0],2)
            from classroom_app.services.wanderer_resource_service import snapshot
            self.assertEqual(snapshot(conn,self.w['id'],'ai_wake')['used'],2)

    def test_group_model_retry_and_protocol_repair_compose_in_both_orders(self):
        from classroom_app.services.wanderer_group_runtime_service import run_member_group
        gid,key=self.make_group(self.w)
        for initial_timeout in (True,False):
            with self.subTest(initial_timeout=initial_timeout):
                incoming=self.send(self.real,key,'需经过两类恢复的合成来信')
                steps=([TimeoutError('synthetic'),{'old':'invalid'}] if initial_timeout else
                       [{'old':'invalid'},TimeoutError('synthetic')])
                calls=[]
                async def caller(*args,**kwargs):
                    calls.append(True)
                    value=steps.pop(0) if steps else decision([command('social.send_message',
                        {'path_params':{'key':key},'body':{'body':'恢复完成，仅发送一次'}})])
                    if isinstance(value,Exception):
                        raise value
                    return {'status':'success','response_json':value}
                first=asyncio.run(run_member_group(self.w,gid,conn_factory=self.connect,ai_caller=caller))
                self.assertEqual(first['status'],'failed',first)
                self.ready()
                final=asyncio.run(run_member_group(self.w,gid,conn_factory=self.connect,ai_caller=caller))
                self.assertEqual(final['status'],'ok',final)
                self.assertEqual(len(calls),3)
                self.assertEqual(asyncio.run(run_member_group(self.w,gid,conn_factory=self.connect,ai_caller=caller))['status'],'nothing_new')
                with self.connect() as conn:
                    self.assertEqual(conn.execute('SELECT handled_message_id FROM wanderer_group_observations WHERE wanderer_id=? AND group_id=?',
                        (self.w['id'],gid)).fetchone()[0],incoming['id'])
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages WHERE conversation_key=? AND sender_identity=? AND message_type='text' AND id>?",
                        (key,self.ident(self.w),incoming['id'])).fetchone()[0],1)

    def test_read_history_survives_intermediate_callback_and_can_ground_knowledge(self):
        def remember(payload):
            text=payload['system_prompt']
            history=json.loads(text.split('【本链此前已执行的操作与阅读回执】\n')[1].split('\n\n')[0])
            original=next(item for item in history['items'] if item['capability']=='world.read')
            self.assertEqual(original['status'],'succeeded')
            self.assertIn('模拟',original['receipt']['result']['content'])
            ref=original['receipt']['result']['evidence']['ref']
            self.assertIn(ref,history['evidence'][0]['ref'])
            return decision([command('knowledge.remember',{'evidence_ref':ref,'digest':'我已研读世界介绍，确认自己生活在有实际权限与有限资源的平台世界。','topic':'实际研读所得'})])
        caller=Caller(decision([command('world.read',{'chapter_key':'world'})],True),
                      decision([command('self.status')],True),remember)
        result=asyncio.run(runtime.wake_wanderer(self.connect,self.w['id'],source='timer',event_key='history',ai_caller=caller))
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(len(caller.calls),3)
        self.assertTrue(all(r['status']=='succeeded' for r in result['receipts']),result)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_commands WHERE capability='world.read'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_knowledge_entries WHERE title='实际研读所得'").fetchone()[0],1)

    def test_chain_history_respects_scope_and_life_epoch(self):
        secret='PRIVATE_CHAIN_SENTINEL'
        result=asyncio.run(runtime.wake_wanderer(self.connect,self.w['id'],source='test',event_key='private',
            context={'scope':{'kind':'private'}},ai_caller=Caller(decision([command('memory.write',{'content':secret,'location':'本人私有空间'})]))))
        self.assertEqual(result['status'],'ok',result)
        with self.connect() as conn:
            original=dict(conn.execute('SELECT * FROM wanderer_turns WHERE turn_id=?',(result['turn_id'],)).fetchone())
            from classroom_app.services.wanderer_identity_service import load_wanderer
            w=load_wanderer(conn,self.w['id'])
            run={**original,'turn_id':'next-not-current','wanderer':w,'scope':{'kind':'public'}}
            self.assertEqual(runtime.chain_receipts(conn,run)['items'],[])
            run['scope']={'kind':'private'}
            self.assertIn(secret,json.dumps(runtime.chain_receipts(conn,run),ensure_ascii=False))
            run['life_epoch']+=1
            self.assertEqual(runtime.chain_receipts(conn,run)['items'],[])

    def test_chain_history_rechecks_recall_and_membership_before_injecting_old_text(self):
        from classroom_app.services import social_message_service as messages
        gid,key=self.make_group(self.w)
        secret='WITHDRAWN_CHAIN_SENTINEL'
        incoming=self.send(self.real,key,secret)
        source={'kind':'social_message','id':incoming['id'],'ref':f"social_message:{incoming['id']}"}
        result=asyncio.run(runtime.wake_wanderer(self.connect,self.w['id'],source='test',event_key='source-revoke',
            context={'conversation_key':key,'messages':[{'id':incoming['id'],'sender_identity':self.real,'body':secret}],
                     'evidence':[source]},ai_caller=Caller(decision([command('runtime.context')]))))
        self.assertEqual(result['status'],'ok',result)
        with self.connect() as conn:
            from classroom_app.services.wanderer_identity_service import load_wanderer
            original=dict(conn.execute('SELECT * FROM wanderer_turns WHERE turn_id=?',(result['turn_id'],)).fetchone())
            run={**original,'turn_id':'next','wanderer':load_wanderer(conn,self.w['id']),'scope':{'kind':'conversation','key':key}}
            self.assertIn(secret,json.dumps(runtime.chain_receipts(conn,run),ensure_ascii=False))
            messages.recall_message(conn,self.user_of(self.real),incoming['id'])
            self.assertNotIn(secret,json.dumps(runtime.chain_receipts(conn,run),ensure_ascii=False))
            conn.execute('UPDATE social_group_members SET left_at=? WHERE group_id=? AND member_identity=?',
                         (local_iso(),gid,self.ident(self.w)))
            self.assertEqual(runtime.chain_receipts(conn,run)['items'],[])
            conn.commit()

    def test_summary_counts_actual_speaker_changes_and_requires_unchanged_peer_watermark(self):
        from tools.wanderer_communication_snapshot import summarize_rows
        rows=[(1,'conv','a',0,True,'same'),(2,'conv','a',10,True,'same'),
              (3,'conv','b',20,True,'reply'),(4,'conv','a',30,True,'same'),
              (5,'conv','b',40,True,'reply'),(6,'conv','a',50,True,'next'),
              (7,'conv','b',60,True,'reply')]
        report=summarize_rows(rows,since_epoch=5)
        self.assertEqual(report['plain_text_repeat_candidates'],1)
        self.assertEqual(report['conversations_with_6_alternating_speaker_turns'],1)
        self.assertEqual(report['ai_to_ai_conversations_with_6_alternating_speaker_turns'],1)
        self.assertNotIn('same',json.dumps(report))
