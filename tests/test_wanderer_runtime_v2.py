import asyncio
import json
from unittest.mock import patch

from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.wanderer_settings_service import daily_ai_calls_key, wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso
from tests.wanderer_test_support import WandererDbTestCase


def decision(commands=(), callback=False, collect=()):
    return {"protocol": "wanderer.v1", "commands": list(commands),
            "callback": {"requested": callback, "collect": list(collect)}}


def command(key, args=None, ident="one", deps=()):
    return {"id": ident, "capability": key, "args": args or {}, "depends_on": list(deps)}


class Caller:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, endpoint, payload, **kwargs):
        self.calls.append(payload)
        value = self.responses.pop(0)
        if callable(value):
            value = value(payload)
        return {"status": "success", "response_json": value}


class RuntimeV2Tests(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            for table in ("wanderer_commands", "wanderer_turns", "wanderer_wake_events", "wanderer_runtime_leases",
                          "wanderer_memory_sources", "wanderer_episodic_memories", "wanderer_resource_reservations",
                          "wanderer_resource_usage"):
                conn.execute(f"DELETE FROM {table}")
            for table in ('wanderer_capacity_allocations','wanderer_bonds','wanderer_knowledge_sources','wanderer_cultivation_awards',
                          'wanderer_gain_debt_payments','wanderer_talent_awards','wanderer_self_state_sources','wanderer_todo_evidence',
                          'wanderer_knowledge_revisions','wanderer_evidence_snapshots'):
                conn.execute(f'DELETE FROM {table}')
            self.w = self.wanderer(conn, "lele")
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=100,status='active',born_at=?,lifespan_weeks=10,"
                         "life_penalty_weeks=0 WHERE id=?", (local_iso(), self.w['id']))
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), "0")
            wanderer_runtime_setting_set(conn, "autonomous_v2_enabled", "1")
            wanderer_runtime_setting_set(conn, "enabled", "1")
            conn.commit()

    def wake(self, caller, *, key="event", context=None):
        return asyncio.run(runtime.wake_wanderer(self.connect, self.w['id'], source="test", event_key=key,
                                                context=context or {}, ai_caller=caller, force=True))

    def test_real_callback_reads_then_self_writes_memory(self):
        def remember(payload):
            self.assertIn("我的记忆", payload['system_prompt'])
            self.assertIn('"remaining":98', payload['system_prompt'])
            context = payload['system_prompt'].split('<<<data>>>\n')[1].split('\n<<<end>>>')[0]
            data = json.loads(context)
            evidence = data['evidence'][0]
            self.assertEqual(evidence['kind'], 'world_chapter')
            return decision([command('memory.write', {"content": "我读到了世界的介绍，决定以后自己核对规则。",
                            "location": "世界手册·第一章", "visibility": "public", "evidence_refs": [evidence['ref']]})])
        caller = Caller(decision([command('world.read', {'chapter_key': 'world'})], True), remember)
        result = self.wake(caller)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['ai_calls'], 2)
        with self.connect() as conn:
            page = runtime.timeline(conn, self.w)
            self.assertEqual(len(page['items']), 1)
            self.assertEqual(page['items'][0]['author_kind'], 'self')
            self.assertEqual(page['items'][0]['location'], '世界手册·第一章')
        next_call = Caller(decision())
        self.wake(next_call, key='next')
        self.assertIn('我读到了世界的介绍', next_call.calls[0]['system_prompt'])

    def test_last_credit_executes_commands_but_retains_unpaid_callback(self):
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_agents SET daily_wake_limit=1 WHERE id=?', (self.w['id'],))
            conn.commit()
        caller = Caller(decision([command('memory.write', {'content':'我今天选择休息。','location':'个人空间'})], True))
        result = self.wake(caller)
        self.assertEqual(len(caller.calls), 1)
        self.assertEqual(result['status'], 'capped')
        self.assertEqual(result['receipts'][0]['status'], 'succeeded')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_memory_sources').fetchone()['n'], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM wanderer_wake_events WHERE state='deferred'").fetchone()['n'], 1)

    def test_duplicate_source_event_never_calls_again(self):
        caller = Caller(decision())
        self.wake(caller)
        repeated = self.wake(caller)
        self.assertEqual(len(caller.calls), 1)
        self.assertEqual(repeated['ai_calls'], 0)

    def test_private_memory_cannot_become_public(self):
        caller = Caller(decision([command('memory.write', {'content':'只属于自己的私密记录。',
                    'location':'个人空间','visibility':'public'})]))
        result = self.wake(caller, context={'scope': {'kind':'private'}})
        self.assertEqual(result['status'], 'ok', result)
        with self.connect() as conn:
            self.assertEqual(runtime.timeline(conn,self.w)['items'], [])
            self.assertEqual(len(runtime.timeline(conn,self.w,viewer='admin')['items']), 1)

    def test_invalid_batch_is_atomic_before_any_write(self):
        valid = command('memory.write', {'content':'不应保存','location':'测试'})
        caller = Caller(decision([valid, {**valid, 'capability':'memory.read'}]))
        result = self.wake(caller)
        self.assertEqual(result['status'], 'failed')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_memory_sources').fetchone()['n'], 0)

    def test_callback_false_does_not_collect_or_call_again(self):
        caller = Caller(decision([], False, [command('world.read', {'chapter_key':'world'})]))
        result = self.wake(caller)
        self.assertEqual(result['ai_calls'], 1)
        self.assertEqual(result['receipts'], [])

    def test_revocation_while_model_waits_blocks_writes(self):
        def revoke(payload):
            with self.connect() as conn:
                conn.execute("UPDATE wanderer_agents SET status='paused',authority_version=authority_version+1 WHERE id=?", (self.w['id'],))
                conn.commit()
            return decision([command('memory.write', {'content':'迟到的输出','location':'旧场景'})])
        result = self.wake(Caller(revoke))
        self.assertEqual(result['status'], 'failed')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_memory_sources').fetchone()['n'], 0)

    def test_concurrent_same_agent_and_event_is_single_thought(self):
        async def scenario():
            entered = asyncio.Event()
            unblock = asyncio.Event()
            calls = []
            async def caller(endpoint,payload,**kwargs):
                calls.append(payload)
                entered.set()
                await unblock.wait()
                return {'status':'success','response_json':decision()}
            one = asyncio.create_task(runtime.wake_wanderer(self.connect,self.w['id'],source='test',event_key='race',context={},ai_caller=caller,force=True))
            await entered.wait()
            two = await runtime.wake_wanderer(self.connect,self.w['id'],source='test',event_key='race',context={},ai_caller=caller,force=True)
            unblock.set()
            await one
            return len(calls), two
        calls, result = asyncio.run(scenario())
        self.assertEqual(calls, 1)
        self.assertEqual(result['ai_calls'], 0)

    def test_protocol_rejects_forward_cycles_and_non_boolean_callback(self):
        with self.assertRaises(ValueError):
            runtime.parse_decision(json.dumps(decision([command('self.status',deps=['later'])])))
        with self.assertRaises(ValueError):
            runtime.parse_decision('{"protocol":"wanderer.v1","commands":[],"callback":{"requested":"false"}}')

    def test_collect_cannot_mutate_and_false_never_collects(self):
        caller=Caller(decision([],True,[command('memory.write',{'content':'采集不能写','location':'测试'})]),decision())
        result=self.wake(caller)
        self.assertEqual(result['ai_calls'],2)
        self.assertIn('collect只允许只读能力',caller.calls[1]['system_prompt'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_memory_sources').fetchone()['n'],0)

    def test_yield_keeps_callback_in_durable_queue(self):
        with patch.object(runtime,'MAX_CHAIN_DEPTH',1):
            result=self.wake(Caller(decision([],True),decision([],True)))
        self.assertEqual(result['ai_calls'],2)
        self.assertTrue(result['callback_deferred'])
        with self.connect() as conn:
            row=conn.execute('SELECT * FROM wanderer_wake_events WHERE event_id=?',(result['callback_event_id'],)).fetchone()
            self.assertEqual(row['state'],'pending')
            self.assertGreater(row['run_at'],local_iso())
            self.assertEqual(row['depth'],0)

    def test_result_references_bind_only_confirmed_dependencies(self):
        caller=Caller(decision([command('self.status',ident='status'),
            command('memory.write',{'content':'我知道了自己的身份','location':{'$ref':'status.result.name'}},ident='memory',deps=['status'])]))
        result=self.wake(caller)
        self.assertEqual(result['receipts'][-1]['status'],'succeeded',result)
        self.assertEqual(result['receipts'][-1]['result']['location'],self.w['display_name'])
        with self.assertRaises(ValueError):
            runtime.parse_decision(json.dumps(decision([command('memory.write',{'content':{'$ref':'unknown.result'}})])))

    def test_async_submission_polls_without_repeating_mutation_or_thought(self):
        calls=[]
        async def execute(**kwargs):
            calls.append(kwargs['command']['id'])
            if len(calls)==1:
                return {'status':'submitted','job_identity':{'kind':'ai_job','id':7},
                        'follow_up':{'capability':'blog.read_post','args':{'path_params':{'post_id':7}}}}
            return {'status':'succeeded','result':{'data':{'job':{'id':7,'status':'succeeded'}}}}
        caller=Caller(decision([command('blog.create_post',{'body':{'title':'记录','content':'记录'}},ident='submit')],True),decision())
        with patch('classroom_app.services.wanderer_platform_service.execute_platform_command',side_effect=execute):
            first=self.wake(caller)
            self.assertEqual(first['status'],'submitted',first)
            with self.connect() as conn:
                conn.execute("UPDATE wanderer_wake_events SET run_at='2000-01-01' WHERE state='processing'")
                conn.commit()
            second=self.wake(caller)
        self.assertEqual(second['status'],'ok',second)
        self.assertEqual(calls,['submit','job_poll'])
        self.assertEqual(len(caller.calls),2)  # original thought plus requested callback
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM wanderer_turns WHERE dispatched_at IS NOT NULL").fetchone()['n'],2)

    def test_stale_executing_turn_resumes_receipts_without_model(self):
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,self.w['id'],'test','resume',{})
            run=runtime._claim(conn,event['event_id'])
            output=decision([command('memory.write',{'content':'断点之后仍只写一次','location':'测试'})])
            conn.execute("UPDATE wanderer_turns SET state='executing',output_json=?,dispatched_at=? WHERE turn_id=?",(json.dumps(output,ensure_ascii=False),local_iso(),run['turn_id']))
            conn.execute("UPDATE wanderer_runtime_leases SET lease_until='2000-01-01'")
            conn.commit()
        caller=Caller()
        result=self.wake(caller,key='resume')
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(len(caller.calls),0)
        self.assertEqual(result['receipts'][0]['status'],'succeeded')

    def test_reconsider_invalid_output_is_new_paid_decision(self):
        first=self.wake(Caller({'protocol':'wrong'}))
        self.assertEqual(first['status'],'failed')
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM wanderer_wake_events WHERE state='failed'").fetchone()
            runtime.retry_failed_event(conn,self.w['id'],row['event_id'])
            conn.commit()
        caller=Caller(decision())
        result=self.wake(caller)
        self.assertEqual(result['status'],'ok',result)
        self.assertIn('previous_turn',caller.calls[0]['system_prompt'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_turns').fetchone()['n'],2)

    def test_memory_revision_preserves_original_and_rejects_stale_revision(self):
        first=self.wake(Caller(decision([command('memory.write',{'content':'最初理解','location':'书房','visibility':'public'})])))
        memory_id=first['receipts'][0]['result']['memory_id']
        args={'content':'纠正后的理解','location':'书房','visibility':'public','supersedes_id':memory_id,'expected_revision':1}
        second=self.wake(Caller(decision([command('memory.write',args)])),key='correct')
        self.assertEqual(second['receipts'][0]['result']['revision'],2)
        stale=self.wake(Caller(decision([command('memory.write',args)])),key='stale')
        self.assertEqual(stale['receipts'][0]['status'],'denied')
        with self.connect() as conn:
            history=runtime.timeline(conn,self.w,viewer='admin')['items']
            self.assertEqual(len(history),2)
            self.assertEqual(sum(item['status']=='active' for item in history),1)

    def test_world_read_then_self_authored_learning_is_recalled(self):
        def remember(payload):
            data=json.loads(payload['system_prompt'].split('<<<data>>>\n')[1].split('\n<<<end>>>')[0])
            return decision([command('knowledge.remember',{'evidence_ref':data['evidence'][0]['ref'],
                                      'digest':'我亲自理解了世界中学习与记忆的区别','topic':'自己的笔记'})])
        result=self.wake(Caller(decision([command('world.read',{'chapter_key':'world'})],True),remember))
        self.assertEqual(result['receipts'][-1]['status'],'succeeded',result)
        caller=Caller(decision())
        self.wake(caller,key='later')
        self.assertIn('我亲自理解了世界中学习与记忆的区别',caller.calls[0]['system_prompt'])

    def test_direct_partner_memory_is_attributed_and_not_transitive(self):
        from classroom_app.services.wanderer_autobiography_service import write_memory,recall_block
        from tests.test_wanderer_world_economy import WorldEconomyTest
        with self.connect() as conn:
            b=self.wanderer(conn,'atai'); c=self.wanderer(conn,'xiaoman')
            conn.execute("UPDATE wanderer_agents SET status='active',born_at=?,lifespan_weeks=20,life_penalty_weeks=0",(local_iso(),))
            WorldEconomyTest.edge(self,conn,self.w['id'],b['id'])
            WorldEconomyTest.edge(self,conn,b['id'],c['id'])
            for w,text,scope in [(b,'直接道侣的公开经历',{'kind':'public'}),(b,'不可共享的私人思绪',{'kind':'private'}),(c,'道侣的道侣经历',{'kind':'public'})]:
                evidence=[{'kind':'private_receipt','id':'opaque','owner_id':b['id'],'ref':'opaque'}] if scope['kind']=='private' else []
                write_memory(conn,w,{'content':text,'location':'书房'},turn_id='test',operation_id=text,scope=scope,available_evidence=evidence)
            prompt=recall_block(conn,self.w,scope={'kind':'private'})
            self.assertIn('直接道侣的公开经历',prompt)
            self.assertIn(f"作者散修{b['id']}",prompt)
            self.assertNotIn('不可共享的私人思绪',prompt)
            self.assertNotIn('道侣的道侣经历',prompt)

    def test_large_source_snapshot_preserves_every_reference_and_owner(self):
        refs=[{'kind':'self_observation','id':i,'ref':f'observation:{i}','title':'来源说明'*30} for i in range(160)]
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,self.w['id'],'test','large',{'evidence':refs})
            stored=json.loads(event['context_json'])
            self.assertIn('evidence_snapshot_id',stored)
            run=runtime._claim(conn,event['event_id'])
            self.assertEqual(runtime._evidence(run),refs)
            with self.assertRaises(PermissionError):
                runtime.sanitize_context(conn,self.wanderer(conn,'atai'),stored,{'kind':'private'})

    def test_callback_does_not_drop_early_protected_provenance(self):
        refs=[{'kind':'private_receipt','id':str(i),'owner_id':self.w['id'],'ref':f'private:{i}'} for i in range(75)]
        caller=Caller(decision([],True),decision([command('memory.write',{'content':'带原来源的理解','location':'书房'})]))
        result=self.wake(caller,context={'scope':{'kind':'private'},'evidence':refs})
        self.assertEqual(result['status'],'ok',result)
        with self.connect() as conn:
            source=conn.execute('SELECT evidence_json FROM wanderer_memory_sources').fetchone()
            self.assertEqual(len(json.loads(source['evidence_json'])),75)

    def test_withdrawn_provenance_redacts_derived_context_not_just_citation(self):
        with self.connect() as conn:
            source={'scope':{'kind':'private'},'evidence':[{'kind':'social_message','id':987654,'ref':'deleted'}],
                    'receipts':[{'result':{'paraphrase':'原消息的私密复述'}}]}
            result=runtime.sanitize_context(conn,self.w,source,{'kind':'private'})
            self.assertNotIn('原消息的私密复述',runtime.packed(result))
            self.assertIn('停止注入',result['notice'])

    def test_expired_worker_cannot_finish_replacement_turn(self):
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,self.w['id'],'test','fence',{})
            run=runtime._claim(conn,event['event_id'])
            conn.execute("UPDATE wanderer_turns SET state='executing',fencing_token=fencing_token+1 WHERE turn_id=?",(run['turn_id'],))
            conn.execute('UPDATE wanderer_runtime_leases SET fencing_token=fencing_token+1')
            conn.commit()
        runtime._finish(self.connect,run,'failed',error='迟到的旧工作器')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT state FROM wanderer_turns').fetchone()['state'],'executing')
            self.assertEqual(conn.execute('SELECT state FROM wanderer_wake_events').fetchone()['state'],'processing')

    def test_resolved_receipt_never_replays_business(self):
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,self.w['id'],'test','resolved',{})
            run=runtime._claim(conn,event['event_id']); conn.commit()
        item=command('memory.write',{'content':'只保存一次','location':'书房'})
        receipt=asyncio.run(runtime.execute_command(self.connect,run,item))
        receipt['status']='resolved'; receipt['verified_business']=False
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_commands SET state='resolved',receipt_json=?",(runtime.packed(receipt),)); conn.commit()
        replay=asyncio.run(runtime.execute_command(self.connect,run,item))
        self.assertEqual(replay['status'],'resolved')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_memory_sources').fetchone()['n'],1)

    def test_personal_private_memory_shared_only_to_direct_partner(self):
        from classroom_app.services.wanderer_autobiography_service import write_memory,timeline
        from tests.test_wanderer_world_economy import WorldEconomyTest
        with self.connect() as conn:
            b=self.wanderer(conn,'atai'); c=self.wanderer(conn,'xiaoman')
            conn.execute("UPDATE wanderer_agents SET status='active',born_at=?,lifespan_weeks=20,life_penalty_weeks=0",(local_iso(),))
            WorldEconomyTest.edge(self,conn,self.w['id'],b['id']); WorldEconomyTest.edge(self,conn,b['id'],c['id'])
            write_memory(conn,self.w,{'content':'我独自思考了自己的目标','location':'书房'},turn_id='first',operation_id='first',
                         scope={'kind':'private'},available_evidence=[])
            shared=timeline(conn,self.w,viewer='self',reader=b,scope={'kind':'private'})['items']
            self.assertEqual(len(shared),1)
            self.assertEqual(timeline(conn,self.w,viewer='self',reader=c,scope={'kind':'private'})['items'],[])
            write_memory(conn,b,{'content':'我了解了道侣的目标','location':'书房'},turn_id='second',operation_id='second',
                         scope={'kind':'private'},available_evidence=shared[0]['evidence'])
            self.assertEqual(timeline(conn,b,viewer='self',reader=c,scope={'kind':'private'})['items'],[])
            self.assertEqual(timeline(conn,self.w,viewer='public')['items'],[])
            conn.execute("UPDATE wanderer_bonds SET state='ended' WHERE first_id=? AND second_id=?",
                         tuple(sorted((self.w['id'],b['id']))))
            retained=timeline(conn,b,viewer='self',scope={'kind':'private'})['items']
            self.assertEqual(len(retained),1)
            self.assertIn('我了解了道侣的目标',retained[0]['content'])
            self.assertEqual(timeline(conn,self.w,viewer='self',reader=b,scope={'kind':'private'})['items'],[])
            self.assertEqual(timeline(conn,b,viewer='self',reader=c,scope={'kind':'private'})['items'],[])

    def test_private_world_learning_shared_and_revision_keeps_same_source(self):
        from classroom_app.services.wanderer_autonomous_learning import remember,read_knowledge
        from classroom_app.services.wanderer_world_service import read_chapter
        from tests.test_wanderer_world_economy import WorldEconomyTest
        with self.connect() as conn:
            b=self.wanderer(conn,'atai')
            conn.execute("UPDATE wanderer_agents SET status='active',born_at=?,lifespan_weeks=20,life_penalty_weeks=0",(local_iso(),))
            WorldEconomyTest.edge(self,conn,self.w['id'],b['id'])
            evidence=read_chapter(conn,chapter_key='world',offset=0,limit=80)['evidence']
            original=remember(conn,self.w,{'evidence_ref':evidence['ref'],'digest':'自己的世界理解'},turn_id='read',
                              scope={'kind':'private'},available_evidence=[evidence])
            shared=read_knowledge(conn,b,scope={'kind':'private'})['items']
            self.assertTrue(any(item['id']==original['entry_id'] and item['shared'] for item in shared))
            old=next(item for item in read_knowledge(conn,self.w,scope={'kind':'private'})['items'] if item['id']==original['entry_id'])
            later=read_chapter(conn,chapter_key='world',offset=80,limit=80)['evidence']
            revised=remember(conn,self.w,{'evidence_ref':later['ref'],'digest':'自己补充后的世界理解'},turn_id='reread',
                             scope={'kind':'private'},available_evidence=[later]+old['evidence'])
            self.assertEqual(original['entry_id'],revised['entry_id'])
            self.assertEqual(revised['revision'],2)
            self.assertTrue(any(item['id']==original['entry_id'] for item in read_knowledge(conn,b,scope={'kind':'private'})['items']))
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM wanderer_knowledge_revisions WHERE entry_id=?',
                                         (original['entry_id'],)).fetchone()['n'],2)

    def test_same_batch_read_keeps_provenance_when_writing_new_memory(self):
        first=self.wake(Caller(decision([command('memory.write',{'content':'私密原文','location':'书房'})])),
                        context={'scope':{'kind':'private'},'evidence':[{'kind':'private_receipt','id':'opaque',
                                 'owner_id':self.w['id'],'ref':'opaque'}]})
        self.assertEqual(first['status'],'ok',first)
        caller=Caller(decision([command('memory.read',ident='read'),
            command('memory.write',{'content':{'$ref':'read.result.items.0.content'},'location':'书房'},ident='write',deps=['read'])]))
        result=self.wake(caller,key='copy',context={'scope':{'kind':'private'}})
        self.assertEqual(result['status'],'ok',result)
        with self.connect() as conn:
            source=conn.execute('SELECT scope_json,evidence_json FROM wanderer_memory_sources ORDER BY memory_id DESC LIMIT 1').fetchone()
            self.assertFalse(json.loads(source['scope_json'])['partner_shareable'])
            self.assertIn('opaque',source['evidence_json'])

    def test_material_learning_uses_stable_content_version_and_original_acl(self):
        from classroom_app.services.wanderer_autonomous_learning import read_material
        from classroom_app.services.material_search_schema import ensure_material_search_schema
        from fastapi import HTTPException
        with self.connect() as conn:
            teacher=conn.execute('SELECT id FROM teachers LIMIT 1').fetchone()['id']
            mid=conn.execute("INSERT INTO course_materials(teacher_id,name,material_path,node_type,file_hash,scope_level,student_visible,owner_role,owner_user_pk) "
                "VALUES(?,?,?,'file','content-v1','public',1,'teacher',?) RETURNING id",
                (teacher,'合成阅读材料','/synthetic-autonomous-reading.md',teacher)).fetchone()['id']
            conn.execute('UPDATE course_materials SET root_id=? WHERE id=?',(mid,mid))
            ensure_material_search_schema(conn)
            conn.execute("INSERT INTO material_search_documents(material_id,file_hash) VALUES(?,'content-v1')",(mid,))
            for ordinal in range(2):
                conn.execute('INSERT INTO material_search_chunks(material_id,ordinal,text,text_lower) VALUES(?,?,?,?)',
                             (mid,ordinal,f'段落{ordinal}',f'段落{ordinal}'))
            one=read_material(conn,self.w,material_id=mid,limit=1)
            two=read_material(conn,self.w,material_id=mid,limit=2)
            self.assertEqual(one['evidence']['source_ref'],two['evidence']['source_ref'])
            conn.execute("UPDATE course_materials SET file_hash='content-v2' WHERE id=?",(mid,))
            self.assertEqual(read_material(conn,self.w,material_id=mid)['chunks'],[])
            conn.execute("UPDATE material_search_documents SET file_hash='content-v2' WHERE material_id=?",(mid,))
            changed=read_material(conn,self.w,material_id=mid)
            self.assertNotEqual(one['evidence']['source_ref'],changed['evidence']['source_ref'])
            conn.execute("UPDATE course_materials SET scope_level='private' WHERE id=?",(mid,))
            with self.assertRaises(HTTPException):
                read_material(conn,self.w,material_id=mid)

    def test_scene_switch_cannot_smuggle_private_text_in_scope_metadata(self):
        secret='不应带到公开场景的私密原文'
        result=self.wake(Caller(decision([command('context.switch',{'scope':{'kind':'public',
            'key':secret,'note':secret,'partner_shareable':True}})])),context={'scope':{'kind':'private'},'content':secret})
        self.assertEqual(result['receipts'][0]['status'],'succeeded',result)
        with self.connect() as conn:
            target=conn.execute("SELECT context_json FROM wanderer_wake_events WHERE source='context'").fetchone()
            self.assertNotIn(secret,target['context_json'])
            self.assertEqual(json.loads(target['context_json'])['scope'],{'kind':'public'})
