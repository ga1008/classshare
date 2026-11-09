"""Legacy messaging uses ordinary routes and the same paid AI communication lane."""
import asyncio
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from classroom_app.services import message_center_service as service
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict
from classroom_app.services.wanderer_resource_service import snapshot,consume
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
from classroom_app.services.rate_limit_service import RateLimitExceededError
from classroom_app.services.agent_platform_request_context import _request_identity
from tests.wanderer_behavior_support import WandererWorldTestCase,ZeroSleep


class WandererLegacyDeliveryTest(WandererWorldTestCase):
    def setUp(self):
        super().setUp()
        self.a=self.set_wanderer('atai',daily_wake_limit=100)
        self.b=self.set_wanderer('lele',daily_wake_limit=100)
        self.user=build_wanderer_user_dict(self.a)
        self.peer=f"student:{self.b['student_id']}"
        from threading import Lock
        self.native_lock=Lock();self.native_source=None;self.native_fingerprint=None
        with self.connect() as conn:
            for table in ('wanderer_private_message_operations','wanderer_resource_usage','wanderer_resource_reservations',
                          'wanderer_capacity_allocations','wanderer_cultivation_awards','wanderer_wake_events','wanderer_turns',
                          'wanderer_commands','wanderer_runtime_leases','wanderer_relationship_versions','wanderer_relationship_changes'):
                conn.execute(f'DELETE FROM {table}')
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','1')
            conn.commit()

    @contextmanager
    def native_operation(self,operation_id):
        from classroom_app.services.platform_execution_authority import WandererServiceAuthority,verify_platform_authority
        from classroom_app.services.wanderer_runtime_service import enqueue_wake_event,_claim
        with self.native_lock:
            if self.native_source is None:
                with self.connect() as conn:
                    event=enqueue_wake_event(conn,self.a['id'],'native_test',str(uuid.uuid4()))
                    run=_claim(conn,event['event_id'])
                    self.native_source=WandererServiceAuthority(self.a['id'],run['turn_id'],run['life_epoch'],
                        run['fencing_token'],run['authority_version'],self.connect)
                    self.native_fingerprint=verify_platform_authority(conn,self.native_source,required_scope='platform:write').actor.authority_fingerprint
                    conn.commit()
        context=SimpleNamespace(active=True,service_identity_verified=True,
            service_authority=self.native_source,actor_role='student',required_scope='platform:write',
            authority_fingerprint=self.native_fingerprint,actor_id=self.a['student_id'],operation_id=operation_id)
        marker=_request_identity.set(context)
        try: yield
        finally: _request_identity.reset(marker)

    def send(self,conn,text='今天又有了新的见闻，想继续与你交流。'):
        return service.create_private_message(conn,self.user,contact_identity=self.peer,content=text)

    def test_native_replay_returns_same_message_and_conflicts_on_changed_body(self):
        op=str(uuid.uuid4())
        with self.native_operation(op),self.connect() as conn:
            first=self.send(conn);conn.commit()
            before=snapshot(conn,self.a['id'],'social_message')['used']
            again=self.send(conn);conn.commit()
            self.assertEqual(first['message']['id'],again['message']['id'])
            self.assertTrue(again['replayed'])
            self.assertEqual(snapshot(conn,self.a['id'],'social_message')['used'],before)
            with self.assertRaises(ValueError): self.send(conn,'更换内容')
            conn.commit()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_cultivation_awards WHERE event_id LIKE ?',('private-message:%',)).fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_social_inbox WHERE source='legacy'").fetchone()[0],1)

    def test_quota_denial_rolls_back_message_even_when_caller_commits_error(self):
        with self.connect() as conn:
            budget=snapshot(conn,self.a['id'],'social_message')
            consume(conn,self.a['id'],'social_message',budget['remaining'],'fixture-exhaust')
            conn.commit()
            with self.assertRaises(RateLimitExceededError): self.send(conn)
            conn.commit()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_social_inbox WHERE source='legacy'").fetchone()[0],0)
            self.assertEqual(snapshot(conn,self.a['id'],'social_message')['remaining'],0)

    def test_legacy_and_social_share_the_same_bucket(self):
        from classroom_app.services.social_common import dm_key
        from classroom_app.services.social_message_service import send_message
        from classroom_app.services.social_common import SocialError
        from classroom_app.time_utils import local_iso
        sender=f"student:{self.a['student_id']}"
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,'social_message_base','1')
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) VALUES(?,?,?,?,?)",
                         (*sorted([sender,self.peer]),'accepted',sender,local_iso()))
            self.send(conn);conn.commit()
            with self.assertRaises(SocialError):send_message(conn,self.user,dm_key(sender,self.peer),{'body':'另一入口','client_nonce':'different-entry'})
            conn.commit()
            self.assertEqual(snapshot(conn,self.a['id'],'social_message')['used'],3000000)
            self.assertEqual(snapshot(conn,self.a['id'],'active_interactions')['used'],0)

    def test_independent_connections_cannot_overspend_last_credit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn,'social_message_base','1');conn.commit()
        ready=Barrier(2)
        def attempt(index):
            with self.connect() as conn:
                ready.wait(timeout=10)
                try:
                    self.send(conn,f'并发消息{index}');conn.commit();return 'sent'
                except RateLimitExceededError:
                    conn.commit();return 'capped'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[0,1]))
        self.assertCountEqual(results,['sent','capped'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],1)
            self.assertEqual(snapshot(conn,self.a['id'],'social_message')['remaining'],0)

    def test_same_operation_from_independent_connections_debits_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        op=str(uuid.uuid4());ready=Barrier(2)
        def attempt(_):
            with self.native_operation(op),self.connect() as conn:
                ready.wait(timeout=10)
                result=self.send(conn);conn.commit();return result['message']['id']
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(attempt,[0,1]))
        self.assertEqual(ids[0],ids[1])
        with self.connect() as conn:
            self.assertEqual(snapshot(conn,self.a['id'],'social_message')['used'],3000000)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],1)

    def test_self_affinity_and_direct_partner_price_are_exactly_threefold(self):
        from classroom_app.services.wanderer_relationship_service import set_affinity
        from classroom_app.time_utils import local_iso
        with self.connect() as conn:
            set_affinity(conn,self.a,self.peer,100,expected_revision=0,source_turn_id='fixture',operation_id=str(uuid.uuid4()))
            ordinary=self.send(conn)['message']['id']
            first,second=sorted([self.a['id'],self.b['id']]);stamp=local_iso()
            conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         ('legacy-price',f'{first}:{second}',first,second,'active','fixture','fixture',stamp,stamp))
            bonded=self.send(conn)['message']['id']
            amounts=[conn.execute('SELECT amount_units FROM wanderer_resource_reservations WHERE operation_id=?',
                                  (f'private-message:{message_id}',)).fetchone()[0] for message_id in (ordinary,bonded)]
            self.assertEqual(amounts,[1500000,500000])
            conn.commit()

    def test_real_asgi_legacy_route_verifies_native_message_and_replays_operation(self):
        from classroom_app.routers.message_center import router
        from classroom_app.services import wanderer_platform_service as platform
        from classroom_app.services.wanderer_runtime_service import enqueue_wake_event,_claim
        app=FastAPI();app.include_router(router)
        with self.connect() as conn:
            event=enqueue_wake_event(conn,self.a['id'],'fixture',str(uuid.uuid4()))
            run=_claim(conn,event['event_id']);conn.commit()
        authority={key:run[key] for key in ('turn_id','life_epoch','fencing_token','authority_version')}
        op=str(uuid.uuid4())
        async def execute():
            return await platform.execute_platform_command(wanderer_id=self.a['id'],**authority,app=app,
                conn_factory=self.connect,operation_id=op,command={'id':'legacy','capability':'legacy.send_message',
                'args':{'body':{'contact_identity':self.peer,'content':'从原有消息中心发出的真实合成私信'}}})
        first=asyncio.run(execute());again=asyncio.run(execute())
        self.assertEqual(first['status'],'succeeded',first)
        self.assertTrue(first['verified_business'],first)
        self.assertEqual(first['result']['data']['sent_message']['id'],again['result']['data']['sent_message']['id'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_private_message_operations').fetchone()[0],1)
            self.assertEqual(snapshot(conn,self.a['id'],'active_interactions')['used'],0)

    def test_six_actual_runtime_legacy_rounds_continue_without_duplicate_delivery(self):
        from classroom_app.routers.message_center import router
        from classroom_app.services import wanderer_platform_service as platform,wanderer_inbox_worker as worker
        app=FastAPI();app.include_router(router);calls=[]
        with self.connect() as conn:self.send(conn);conn.commit()
        async def caller(endpoint,payload,**kwargs):
            context=json.loads(payload['system_prompt'].split('<<<data>>>\n')[1].split('\n<<<end>>>')[0])
            calls.append(context)
            return {'status':'success','response_json':{'protocol':'wanderer.v1','commands':[{'id':'reply',
                'capability':'legacy.send_message','args':{'body':{'contact_identity':context['peer_identity'],
                'content':f'第{len(calls)}轮，我们继续谈谈各自的见闻。'}}}],'callback':{'requested':False}}}
        with patch.object(platform,'_app',return_value=app):
            for _ in range(6):
                with self.connect() as conn:
                    conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01' WHERE state IN ('pending','retry')");conn.commit()
                result=asyncio.run(worker.process_once(factory=self.connect,ai_caller=caller,sleep=ZeroSleep(),limit=1))
                self.assertIn('done',result)
        self.assertEqual(len(calls),6)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],7)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_private_message_operations').fetchone()[0],6)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_commands WHERE state='succeeded'").fetchone()[0],6)
            self.assertEqual(snapshot(conn,self.a['id'],'ai_wake')['used']+snapshot(conn,self.b['id'],'ai_wake')['used'],6)

    def test_legacy_protocol_repair_delivers_once_and_replay_has_no_extra_charge(self):
        from classroom_app.routers.message_center import router
        from classroom_app.services import wanderer_platform_service as platform,wanderer_inbox_worker as worker,wanderer_runtime_service as runtime
        from tests.test_wanderer_runtime_v2 import Caller,decision,command
        app=FastAPI();app.include_router(router)
        with self.connect() as conn:
            self.send(conn);conn.commit()
        def repaired(payload):
            context=json.loads(payload['system_prompt'].split('<<<data>>>\n')[1].split('\n<<<end>>>')[0])
            return decision([command('legacy.send_message',{'body':{'contact_identity':context['peer_identity'],'content':'经过格式纠正后的合成私信。'}})])
        caller=Caller({'action':'reply','parts':['这条旧协议消息不能发送']},repaired)
        with patch.object(platform,'_app',return_value=app):
            with self.connect() as conn:
                conn.execute("UPDATE wanderer_social_inbox SET run_at='2000-01-01' WHERE state IN ('pending','retry')");conn.commit()
            result=asyncio.run(worker.process_once(factory=self.connect,ai_caller=caller,sleep=ZeroSleep(),limit=1))
            self.assertIn('done',result)
            with self.connect() as conn:
                event=dict(conn.execute("SELECT * FROM wanderer_wake_events WHERE source='inbox' ORDER BY created_at LIMIT 1").fetchone())
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_messages').fetchone()[0],2)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_private_message_operations').fetchone()[0],1)
                self.assertEqual(snapshot(conn,self.a['id'],'ai_wake')['used']+snapshot(conn,self.b['id'],'ai_wake')['used'],2)
                self.assertGreater(conn.execute('SELECT handled_message_id FROM wanderer_social_inbox WHERE wanderer_id=?',(event['wanderer_id'],)).fetchone()[0],0)
            replay=asyncio.run(runtime.wake_wanderer(self.connect,event['wanderer_id'],source='inbox',event_key=event['event_key'],ai_caller=caller))
        self.assertEqual(replay['status'],'ok',replay)
        self.assertEqual(replay['ai_calls'],0)
        self.assertEqual(len(caller.calls),2)

    def test_background_assistant_job_restores_trusted_billing_origin(self):
        from classroom_app.services import wanderer_ai_attribution as attribution
        source=attribution.AIAttribution(self.a['id'],self.a['student_id'],self.a['life_epoch'],self.a['authority_version'],str(uuid.uuid4()),self.connect)
        with self.connect() as conn:
            msg=self.send(conn)
            marker=attribution._attribution.set(source)
            try: job=service.create_private_ai_reply_job(conn,self.user,conversation_key=msg['conversation_key'],
                    class_offering_id=self.offering_id,request_message_id=msg['message']['id'])
            finally: attribution._attribution.reset(marker)
            conn.commit()
        observed=[]
        async def generate(*args,**kwargs):
            observed.append(attribution.current_attribution())
            return {'id':msg['message']['id']}
        with patch.object(service,'generate_ai_private_reply',side_effect=generate):
            asyncio.run(service.process_private_ai_reply_job(job['id']))
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0].wanderer_id,self.a['id'])
        self.assertEqual(observed[0].job_kind,'private_message_ai_jobs')
        self.assertEqual(observed[0].operation_id,source.operation_id)
        self.assertIsNone(attribution.current_attribution())
