"""Native independent-connection settlement and command lock-order acceptance."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

import tests.test_wanderer_world_postgres as support
from classroom_app.services import wanderer_bond_service as bonds
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.wanderer_cultivation_service import award_cultivation
from classroom_app.services.wanderer_identity_service import load_wanderer
from classroom_app.services.wanderer_life_service import apply_talent_change
from classroom_app.services.wanderer_penalty_service import apply_penalty


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'),'Requires owned offline PostgreSQL')
class WandererEconomyNativePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.WandererWorldPostgresTest.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        support.WandererWorldPostgresTest.tearDownClass.__func__(cls)

    setUp=support.WandererWorldPostgresTest.setUp
    connect=support.WandererWorldPostgresTest.connect
    race=support.WandererWorldPostgresTest.race

    def test_history_and_admin_pages_include_original_donors_without_unrelated_accounts(self):
        from classroom_app.services import wanderer_resource_service as resources
        from classroom_app.services.heavenly_dao_bond_service import _condition
        from classroom_app.services.wanderer_workspace_service import _records
        from classroom_app.time_utils import local_iso
        with self.connect() as conn:
            conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) VALUES('donor-edge','1:2',1,2,'active','fixture','fixture',?,?)",(local_iso(),local_iso()))
            for index in range(3):
                self.assertTrue(resources.consume(conn,2,'ai_wake',1,f'donor-loan:{index}',base_limits={1:100,2:0})['ok'])
                self.assertTrue(resources.resize_capacity(conn,2,'knowledge_chars',f'donor-object:{index}',10,base_limits={1:100,2:0})['ok'])
            for kind,table in (('resource_reservations','wanderer_resource_reservations'),('capacity','wanderer_capacity_allocations')):
                first=_records(conn,1,kind,0,2)
                second=_records(conn,1,kind,first['next_cursor'],2)
                self.assertEqual((first['total'],len(first['items']),len(second['items'])),(3,2,1))
                self.assertEqual({row['actor_id'] for row in first['items']}, {2})
                self.assertEqual(_records(conn,3,kind,0,20)['items'],[])
                condition,params=_condition(table,{'actor_id','lines_json'},(1,3),(1,3))
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {condition}',params).fetchone()[0],3)
            conn.commit()

    def test_concurrent_source_rewrites_cannot_repeat_archived_learning_reward(self):
        from classroom_app.services.wanderer_knowledge_service import add_knowledge
        with self.connect() as conn:
            actor=load_wanderer(conn,1)
            first=add_knowledge(conn,actor,source_kind='material_page',source_ref='growth:race',title='First',digest='a'*200,rng=lambda:1.0)
            conn.execute("UPDATE wanderer_knowledge_entries SET status='forgotten' WHERE id=?",(first['entry_id'],))
            before=load_wanderer(conn,1)['cultivation_score']
            conn.commit()
        def revise(index):
            with self.connect() as conn:
                result=add_knowledge(conn,load_wanderer(conn,1),source_kind='material_page',source_ref='growth:race',
                                     title='Rewritten',digest=str(index)*200,rng=lambda:1.0)
                conn.commit()
                return result['score_delta']
        self.assertEqual(self.race(8,revise),[0]*8)
        with self.connect() as conn:
            self.assertEqual(load_wanderer(conn,1)['cultivation_score'],before)
            self.assertEqual(conn.execute('SELECT rewarded_chars FROM wanderer_knowledge_growth WHERE entry_id=?',(first['entry_id'],)).fetchone()[0],200)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_cultivation_awards').fetchone()[0],1)

    def test_runtime_opposite_unbonded_proposals_use_one_lock_order(self):
        runs=[]
        with self.connect() as conn:
            for actor in (1,2):
                event=runtime.enqueue_wake_event(conn,actor,'fixture',f'opposite-{actor}')
                runs.append(runtime._claim(conn,event['event_id']))
            conn.commit()
        def propose(index):
            command={'id':'propose','capability':'bond.propose','args':{'peer_id':2-index,'reason':'本人自主申请'}}
            return asyncio.run(runtime.execute_command(self.connect,runs[index],command))
        with patch('classroom_app.services.wanderer_bond_service._notify_application'):
            results=self.race(2,propose)
        self.assertTrue(all(item['status']=='succeeded' for item in results),results)
        self.assertEqual(results[0]['result']['id'],results[1]['result']['id'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_bond_requests').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_commands WHERE state='succeeded'").fetchone()[0],2)

    def test_duplicate_positive_cultivation_pays_original_debt_only_once(self):
        with patch('classroom_app.services.heavenly_dao_service.notify_wanderer'):
            with self.connect() as conn:
                conn.execute('UPDATE wanderer_agents SET cultivation_score=0 WHERE id=1')
                apply_penalty(conn,1,{'cultivation':20},reason='原裁决',operation_id='cultivation-debt')
                conn.commit()
        def gain(_index):
            with self.connect() as conn:
                result=award_cultivation(conn,1,10,'knowledge','same-gain')
                conn.commit()
                return result
        self.assertEqual(self.race(8,gain),[0]*8)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=1 AND component='cultivation'").fetchone()[0],10_000_000)
            self.assertEqual(award_cultivation(conn,1,10,'knowledge','second-gain'),0)
            self.assertEqual(award_cultivation(conn,1,5,'knowledge','third-gain'),5)
            self.assertEqual(load_wanderer(conn,1)['cultivation_score'],5)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_cultivation_awards').fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_penalty_roots').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT applied_units FROM wanderer_penalty_allocations WHERE root_id='cultivation-debt'").fetchone()[0],20_000_000)
            conn.commit()

    def test_duplicate_talent_gain_pays_old_root_across_new_life_without_resplitting(self):
        with patch('classroom_app.services.heavenly_dao_service.notify_wanderer'):
            with self.connect() as conn:
                conn.execute('UPDATE wanderer_agents SET talents_json=? WHERE id=1',(json.dumps({'luck':0.6}),))
                apply_penalty(conn,1,{'talent:luck':0.8},reason='原裁决',operation_id='talent-debt')
                conn.commit()
        def gain(_index):
            with self.connect() as conn:
                result=apply_talent_change(conn,load_wanderer(conn,1),'luck',0.5,reason='新收获',event_id='same-gain')
                conn.commit()
                return result
        results=self.race(8,gain)
        self.assertTrue(all(row==results[0] for row in results))
        self.assertEqual((results[0]['debt_paid'],results[0]['net_gain']),(0.5,0))
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=1 AND component='talent:luck'").fetchone()[0],300_000)
            conn.execute('UPDATE wanderer_agents SET life_epoch=2 WHERE id=1')
            result=apply_talent_change(conn,load_wanderer(conn,1),'luck',0.5,reason='新生收获',event_id='second-gain')
            self.assertEqual((result['debt_paid'],result['net_gain']),(0.3,0.2))
            payment=conn.execute("SELECT * FROM wanderer_gain_debt_payments WHERE gain_key='talent:second-gain'").fetchone()
            self.assertEqual(payment['life_epoch'],2)
            origin=json.loads(payment['allocations_json'])[0]
            self.assertEqual((origin['root_id'],origin['life_epoch']),('talent-debt',1))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_talent_awards').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_penalty_roots').fetchone()[0],1)
            conn.commit()
