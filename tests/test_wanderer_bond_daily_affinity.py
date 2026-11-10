"""Daily ratings must preserve the consent and exhaustive bond-review workflow."""
import asyncio
import json
from unittest.mock import patch

from classroom_app.services import wanderer_bond_service as bonds
from classroom_app.services.heavenly_dao_bond_service import review_request
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_identity_service import load_wanderer
from classroom_app.services.wanderer_reflection_service import run_reflection
from classroom_app.services.wanderer_relationship_service import affinity_state, set_affinity
from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
from classroom_app.time_utils import local_iso
from tests.test_wanderer_runtime_v2 import Caller, command, decision
from tests.wanderer_test_support import WandererDbTestCase


class DailyAffinityBondTests(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict('os.environ',{'WANDERER_ENABLED':'true'}))
        with self.connect() as conn:
            for table in ('wanderer_dao_history_blocks','wanderer_bonds','wanderer_bond_requests','wanderer_life_ledger',
                          'wanderer_affinity_rating_operations','wanderer_affinity_ratings','wanderer_reflection_dispatches',
                          'wanderer_reflection_sessions','wanderer_commands','wanderer_turns','wanderer_wake_events',
                          'wanderer_runtime_leases','wanderer_daily_reflections','wanderer_relationship_changes',
                          'wanderer_relationship_versions','wanderer_relationships','social_friendships',
                          'wanderer_resource_reservations','wanderer_resource_usage','wanderer_capacity_allocations'):
                conn.execute(f'DELETE FROM {table}')
            self.first=self.wanderer(conn,'lele')
            self.second=self.wanderer(conn,'momo')
            conn.execute("UPDATE wanderer_agents SET status='active',born_at=?,lifespan_weeks=20,"
                         'life_penalty_weeks=0,life_bonus_seconds=0,daily_wake_limit=0',(local_iso(),))
            for owner,peer in ((self.first,self.second),(self.second,self.first)):
                set_affinity(conn,owner,f"student:{peer['student_id']}",95,expected_revision=0,
                    source_turn_id='synthetic-history',operation_id=f"history:{owner['id']}")
            a,b=sorted([f"student:{self.first['student_id']}",f"student:{self.second['student_id']}"])
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) "
                         "VALUES(?,?,'accepted',?,?)",(a,b,a,local_iso()))
            wanderer_runtime_setting_set(conn,'autonomous_v2_enabled','1')
            wanderer_runtime_setting_set(conn,'enabled','1')
            wanderer_runtime_setting_set(conn,'daily_ai_call_cap','0')
            conn.commit()

    def reflect(self,owner,commands,*,force=False):
        return asyncio.run(run_reflection(ActionContext(self.connect,owner,ai_caller=Caller(decision(commands))),force=force))

    def grade(self,peer,*,revision=1,grade='very_good'):
        return command('relationship.rate',{'peer_ref':f"student:{peer['student_id']}",
            'grade':grade,'expected_revision':revision},'rating')

    def ready_request(self):
        first=self.reflect(self.first,[self.grade(self.second),command('bond.propose',
            {'peer_id':self.second['id'],'reason':'每日自省后决定提出申请。'},'propose',deps=['rating'])])
        self.assertEqual(first['status'],'applied',first)
        request=first['runtime']['receipts'][1]['result']
        second=self.reflect(self.second,[self.grade(self.first),command('bond.consent',
            {'request_id':request['id'],'accept':True,'expected_revision':request['version']},'consent',deps=['rating'])])
        self.assertEqual(second['status'],'applied',second)
        ready=second['runtime']['receipts'][1]['result']
        self.assertEqual(ready['state'],'ready',ready)
        return ready

    @staticmethod
    async def judge(_factory,**kwargs):
        payload=json.loads(kwargs['user_message'])
        if kwargs['kind']=='bond_history':
            return {'result':{'reviewed':True,'content_hash':payload['content_hash'],'summary':'合成审阅：已核对完整证据块。'}}
        return {'result':{'approved':True,'reason':'双方自主同意且行为审阅通过。'}}

    def test_daily_ratings_reach_100_then_apply_consent_review_and_reward(self):
        ready=self.ready_request()
        with self.connect() as conn:
            for owner,peer in ((self.first,self.second),(self.second,self.first)):
                state=affinity_state(conn,owner['id'],f"student:{peer['student_id']}")
                self.assertEqual((state['origin'],state['affinity'],state['revision']),('daily_reflection',100,2))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_affinity_ratings WHERE baseline_affinity=95 AND new_affinity=100').fetchone()[0],2)
        with patch('classroom_app.services.wanderer_ai_service.dao_judge',self.judge):
            result=asyncio.run(review_request(self.connect,ready['id']))
        self.assertEqual(result['status'],'approved',result)
        with self.connect() as conn:
            self.assertTrue(bonds.is_direct_partner(conn,self.first['id'],self.second['id']))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_dao_history_blocks WHERE request_id=? AND state<>'reviewed'",(ready['id'],)).fetchone()[0],0)
            for owner in (self.first,self.second):
                self.assertEqual(load_wanderer(conn,owner['id'])['life_bonus_seconds'],4*604800)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_life_ledger WHERE kind='bond_reward'").fetchone()[0],2)

    def test_legacy_and_unrated_full_values_still_cannot_grant_readiness(self):
        with self.connect() as conn:
            conn.execute('UPDATE wanderer_relationships SET affinity=100')
            request=bonds.propose(conn,self.first['id'],self.second['id'],operation_id='synthetic-ineligible')
            for origin in ('legacy','unrated','unknown'):
                with self.subTest(origin=origin):
                    conn.execute('UPDATE wanderer_relationship_versions SET origin=?',(origin,))
                    request=bonds.consent(conn,self.second['id'],request['id'],accept=True,expected_revision=request['version'])
                    self.assertEqual(request['state'],'awaiting_consent')

    def test_approval_rechecks_current_daily_rating_after_consent(self):
        ready=self.ready_request()
        changed=self.reflect(self.first,[self.grade(self.second,revision=2,grade='bad')],force=True)
        self.assertEqual(changed['status'],'applied',changed)
        with patch('classroom_app.services.wanderer_ai_service.dao_judge',self.judge):
            with self.assertRaisesRegex(ValueError,'100%'):
                asyncio.run(review_request(self.connect,ready['id']))
        with self.connect() as conn:
            self.assertFalse(bonds.is_direct_partner(conn,self.first['id'],self.second['id']))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_life_ledger WHERE kind='bond_reward'").fetchone()[0],0)
