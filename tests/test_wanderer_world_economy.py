import asyncio
import json
from unittest.mock import patch

from tests.wanderer_test_support import WandererDbTestCase
from classroom_app import database
from classroom_app.time_utils import local_iso
from classroom_app.services import wanderer_bond_service as bonds
from classroom_app.services import wanderer_resource_service as resources
from classroom_app.services import wanderer_penalty_service as penalties
from classroom_app.services.wanderer_cultivation_service import award_cultivation
from classroom_app.services.wanderer_identity_service import load_wanderer, current_life, mark_dead
from classroom_app.services.wanderer_life_service import promote_life, finish_revival
from classroom_app.services.heavenly_dao_bond_service import review_request, sync_manifest


class WorldEconomyTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            for table in ('wanderer_gain_debt_payments','wanderer_talent_awards','wanderer_knowledge_revisions',
                          'wanderer_relationships','wanderer_relationship_versions','wanderer_relationship_changes'):
                conn.execute(f'DELETE FROM {table}')
            for table in ('social_group_members','social_groups','social_friendships','social_friend_request_log','social_blocks'):
                conn.execute(f'DELETE FROM {table}')
            for table in ("wanderer_bonds", "wanderer_bond_requests", "wanderer_resource_usage", "wanderer_resource_reservations", "wanderer_capacity_allocations", "wanderer_penalty_roots", "wanderer_penalty_allocations", "wanderer_penalty_debts", "wanderer_penalty_reversals", "wanderer_life_ledger", "wanderer_cultivation_awards", "wanderer_dao_history_blocks"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("UPDATE wanderer_agents SET status='active',tier=6,cultivation_score=0,born_at=?,lifespan_weeks=25,life_penalty_weeks=0,life_bonus_seconds=0,life_epoch=1,authority_version=1,daily_wake_limit=100", (local_iso(),))
            self.ids = [int(r["id"]) for r in conn.execute("SELECT id FROM wanderer_agents ORDER BY id LIMIT 3").fetchall()]
            conn.commit()

    def edge(self, conn, a, b):
        a, b = sorted((a, b))
        conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) VALUES(?,?,?,?,'active','fixture','fixture',?,?)", (f"bond:{a}:{b}", f"{a}:{b}", a, b, local_iso(), local_iso()))

    def affection(self, conn, a, b):
        from classroom_app.services.wanderer_relationship_service import affinity_state, set_affinity
        for first, second in ((a,b),(b,a)):
            peer = load_wanderer(conn, second)
            identity=f"student:{peer['student_id']}"
            revision=affinity_state(conn,first,identity)['revision']
            set_affinity(conn,load_wanderer(conn,first),identity,100,expected_revision=revision,
                         source_turn_id='synthetic-current-decision',operation_id=f'{self.id()}:{first}:{second}:{revision}')

    def test_legacy_full_affinity_cannot_replace_current_self_authored_choice(self):
        from classroom_app.db.schema_wanderer_social import migrate_relationship_affinities
        a,b,_=self.ids
        with self.connect() as conn:
            for owner,peer_id in ((a,b),(b,a)):
                peer=load_wanderer(conn,peer_id)
                conn.execute('INSERT INTO wanderer_relationships(wanderer_id,peer_identity,affinity,updated_at) VALUES(?,?,100,?)',
                             (owner,f"student:{peer['student_id']}",local_iso()))
            migrate_relationship_affinities(conn)
            request=bonds.propose(conn,a,b,operation_id='historical-full-affinity')
            request=bonds.consent(conn,b,request['id'],accept=True,expected_revision=request['version'])
            self.assertEqual(request['state'],'awaiting_consent')
            self.affection(conn,a,b)
            request=bonds.consent(conn,b,request['id'],accept=True,expected_revision=request['version'])
            self.assertEqual(request['state'],'ready')

    def approve(self, conn, a, b, key):
        self.affection(conn, a, b)
        row = bonds.propose(conn, a, b, operation_id=key)
        row = bonds.consent(conn, b, key, accept=True, expected_revision=row["version"])
        self.assertEqual(row["state"], "ready")
        conn.execute("UPDATE wanderer_bond_requests SET state='reviewing',review_token='fixture' WHERE id=?", (key,))
        conn.execute("INSERT INTO wanderer_dao_history_blocks(request_id,block_id,content_hash,content_json,summary,state,created_at) VALUES(?,'test','hash','{}','reviewed','reviewed',?)", (key,local_iso()))
        return bonds.apply_review(conn, key, approved=True, reason="完整审阅通过。", review_token="fixture")

    def test_direct_only_owner_debits_survive_dissolution_and_next_day(self):
        a,b,c = self.ids
        with self.connect() as conn:
            self.edge(conn,a,b); self.edge(conn,b,c)
            conn.execute("UPDATE wanderer_agents SET tier=0")
            state = resources.snapshot(conn,a,"ai_wake",budget_date="2026-11-09")
            self.assertEqual(state["limit"],260)
            result = resources.consume(conn,a,"ai_wake",150,"calls",budget_date="2026-11-09")
            self.assertEqual([line["owner_id"] for line in result["owners"]],[a,b])
            self.assertEqual([line["amount"] for line in result["owners"]],[120,30])
            self.assertEqual(resources.consume(conn,a,"ai_wake",150,"calls",budget_date="2026-11-09")["state"],"consumed")
            bonds.end_bond(conn,a,b,expected_revision=1)
            self.assertEqual(resources.snapshot(conn,a,"ai_wake",budget_date="2026-11-09")["remaining"],0)
            self.assertEqual(resources.snapshot(conn,a,"ai_wake",budget_date="2026-11-10")["remaining"],100)
            self.assertFalse(resources.settle(conn,"calls",release=True)["ok"])

    def test_daily_wake_quota_grows_with_tier(self):
        a = self.ids[0]
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET tier=0,daily_wake_limit=20 WHERE id=?", (a,))
            self.assertEqual(resources.snapshot(conn,a,"ai_wake",budget_date="2026-11-09")["limit"],20)
            conn.execute("UPDATE wanderer_agents SET tier=4 WHERE id=?", (a,))
            self.assertEqual(resources.snapshot(conn,a,"ai_wake",budget_date="2026-11-09")["limit"],92)
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=0 WHERE id=?", (a,))
            self.assertEqual(resources.snapshot(conn,a,"ai_wake",budget_date="2026-11-09")["limit"],0)

    def test_capacity_uses_original_owners_and_grandfathers_overage(self):
        a,b,_ = self.ids
        with self.connect() as conn:
            self.edge(conn,a,b)
            bases={a:10,b:10}
            self.assertTrue(resources.resize_capacity(conn,a,"knowledge_chars","knowledge:1",20,base_limits=bases)["ok"])
            bonds.end_bond(conn,a,b,expected_revision=1)
            self.assertEqual(resources.capacity(conn,a,"knowledge_chars",base_limits=bases)["used"],12)
            self.assertFalse(resources.resize_capacity(conn,a,"knowledge_chars","knowledge:1",21,base_limits=bases)["ok"])
            resources.release_capacity(conn,a,"knowledge_chars","knowledge:1")
            self.assertEqual(resources.capacity(conn,b,"knowledge_chars",base_limits=bases)["used"],0)
            resources.grandfather_capacity(conn,a,"knowledge_chars","knowledge:legacy",30)
            self.assertEqual(resources.capacity(conn,a,"knowledge_chars",base_limits=bases)["remaining"],0)

    def test_pair_reward_once_and_tier_reward_remains_independent(self):
        a,b,_ = self.ids
        with self.connect() as conn:
            self.approve(conn,a,b,"first")
            self.assertEqual(load_wanderer(conn,a)["life_bonus_seconds"],4*604800)
            promote_life(conn,load_wanderer(conn,a),7)
            self.assertEqual(load_wanderer(conn,a)["life_bonus_seconds"],4*604800)
            bonds.end_bond(conn,a,b,expected_revision=1)
            self.approve(conn,a,b,"again")
            self.assertEqual(load_wanderer(conn,a)["life_bonus_seconds"],4*604800)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM wanderer_life_ledger WHERE kind='bond_reward'").fetchone()["n"],2)

    def test_penalty_conserves_total_and_never_propagates_to_partner_partner(self):
        a,b,c = self.ids
        with self.connect() as conn:
            self.edge(conn,a,b); self.edge(conn,b,c)
            result=penalties.apply_penalty(conn,a,{"life_seconds":1000},reason="违反约定。",operation_id="sanction")
            self.assertEqual([(x["owner_id"],x["units"]) for x in result["allocations"]],[(a,800),(b,200)])
            self.assertEqual(load_wanderer(conn,c)["life_penalty_weeks"],0)
            penalties.apply_penalty(conn,a,{"life_seconds":1000},reason="违反约定。",operation_id="sanction")
            self.assertAlmostEqual(load_wanderer(conn,a)["life_penalty_weeks"]*604800,800)
            for i in range(5):
                penalties.apply_penalty(conn,a,{"tier":1},reason="降阶。",operation_id=f"tier:{i}")
            self.assertEqual([load_wanderer(conn,i)["tier"] for i in (a,b,c)],[2,5,6])

    def test_gain_multiplies_once_before_shared_cap_and_is_idempotent(self):
        a,b,c = self.ids
        with self.connect() as conn:
            self.edge(conn,a,b);self.edge(conn,a,c)
            self.assertEqual(award_cultivation(conn,a,10,"interaction","event",daily_cap=60),9)
            self.assertEqual(award_cultivation(conn,a,10,"interaction","event",daily_cap=60),9)
            self.assertEqual(load_wanderer(conn,a)["cultivation_score"],9)
            self.assertEqual(load_wanderer(conn,b)["cultivation_score"],0)
            self.assertEqual(award_cultivation(conn,a,1000,"interaction","event2",daily_cap=60),219)

    def test_death_is_idempotent_and_revival_does_not_regrant_old_reward(self):
        a,b,_=self.ids
        with self.connect() as conn:
            self.approve(conn,a,b,"alive")
            review=mark_dead(conn,a,"合成寿终")
            self.assertEqual(mark_dead(conn,a,"重复"),review)
            self.assertEqual(load_wanderer(conn,a)["life_epoch"],2)
            self.assertFalse(bonds.is_direct_partner(conn,a,b))
            conn.execute("UPDATE wanderer_agents SET status='active',born_at=?,life_penalty_weeks=0 WHERE id=?",(local_iso(),a))
            finish_revival(conn,a)
            self.assertEqual(load_wanderer(conn,a)["life_epoch"],3)
            self.assertEqual(load_wanderer(conn,a)["life_bonus_seconds"],0)
            self.approve(conn,a,b,"reborn")
            self.assertEqual(load_wanderer(conn,a)["life_bonus_seconds"],0)

    def test_all_history_has_no_recent_window_and_final_approval_requires_every_block(self):
        a,b,_=self.ids
        with self.connect() as conn:
            self.affection(conn,a,b)
            req=bonds.propose(conn,a,b,operation_id="history")
            bonds.consent(conn,b,"history",accept=True,expected_revision=req["version"])
            for i in range(150):
                conn.execute("INSERT INTO wanderer_action_log(wanderer_id,action_kind,target_type,target_id,outcome,content_preview,created_at) VALUES(?,'skip','','','ok',?,?)",(a,f"old-evidence-{i}","2020-01-01T00:00:00"))
            for revision, digest in ((1, 'original-understanding'), (2, 'corrected-understanding')):
                conn.execute("INSERT INTO wanderer_knowledge_revisions(entry_id,revision,wanderer_id,turn_id,record_json,created_at) VALUES(987654,?,?,'history-fixture',?,?)",
                             (revision,a,json.dumps({'digest':digest}),local_iso()))
            sync_manifest(conn,"history")
            all_text="".join(r["content_json"] for r in conn.execute("SELECT content_json FROM wanderer_dao_history_blocks WHERE request_id='history'").fetchall())
            self.assertIn("old-evidence-0",all_text);self.assertIn("old-evidence-149",all_text)
            self.assertIn('original-understanding',all_text)
            self.assertIn('corrected-understanding',all_text)
            conn.commit()
        calls=[]
        async def judge(_factory,**kwargs):
            calls.append(kwargs["kind"])
            payload=json.loads(kwargs["user_message"])
            if kwargs["kind"]=="bond_history":
                return {"result":{"reviewed":True,"content_hash":payload["content_hash"],"summary":"全部本块记录已审阅。"}}
            return {"result":{"approved":True,"reason":"全历史审阅通过。"}}
        with patch("classroom_app.services.wanderer_ai_service.dao_judge",judge):
            outcome=asyncio.run(review_request(database.get_db_connection,"history"))
        self.assertEqual(outcome["status"],"approved")
        self.assertIn("bond_history",calls)
        self.assertEqual(calls[-1],"bond")

    def test_revocation_uses_original_owners_after_end_and_preserves_new_life(self):
        a,b,c=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b)
            penalties.apply_penalty(conn,a,{"life_seconds":1000},reason="误判",operation_id="revoke-life")
            self.assertEqual([r["applied_units"] for r in conn.execute("SELECT applied_units FROM wanderer_penalty_allocations WHERE root_id='revoke-life' ORDER BY beneficiary_id")],[800,200])
            bonds.end_bond(conn,a,b,expected_revision=1)
            self.edge(conn,a,c)
            penalties.reverse_penalty(conn,"revoke-life",reason="证据纠正",operation_id="reverse")
            self.assertEqual([load_wanderer(conn,i)["life_penalty_weeks"] for i in (a,b,c)],[0,0,0])
            penalties.reverse_penalty(conn,"revoke-life",reason="重复",operation_id="reverse-again")
            self.assertEqual(load_wanderer(conn,a)["life_penalty_weeks"],0)

    def test_quantized_warning_reversal_restores_derived_tier_without_resplitting(self):
        a,_,_=self.ids
        with self.connect() as conn:
            for i in range(3):
                penalties.apply_penalty(conn,a,{"warning":1},reason="警告",operation_id=f"warn:{i}")
            self.assertEqual(load_wanderer(conn,a)["tier"],5)
            penalties.reverse_penalty(conn,"warn:1",reason="警告撤销",operation_id="undo-warning")
            self.assertEqual(load_wanderer(conn,a)["tier"],6)
            self.assertEqual(conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component='warning'",(a,)).fetchone()["balance_units"],2_000_000)

    def test_shared_learning_excludes_unproven_private_seed_and_deduplicates_world_source(self):
        a,b,c=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b);self.edge(conn,b,c)
            baseline=bonds.shared_knowledge_qualification(conn,a)["knowledge_chars"]
            for owner,kind,ref,chars in ((b,"private","secret",9000),(b,"world","rules:v1",500),(a,"world","rules:v1",300),(c,"world","other:v1",5000)):
                conn.execute("INSERT INTO wanderer_knowledge_entries(wanderer_id,source_kind,source_ref,title,digest,chars,status,created_at,updated_at) VALUES(?,?,?,'learned','body',?,'active',?,?)",(owner,kind,ref,chars,local_iso(),local_iso()))
            learning=bonds.shared_knowledge_qualification(conn,a)
            self.assertEqual(learning["knowledge_chars"],baseline+500)
            self.assertNotIn("secret",json.dumps(learning))
            self.assertNotIn(c,learning["owners"])

    def test_equalization_has_both_consent_and_conserves_seconds(self):
        a,b,_=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b)
            conn.execute("UPDATE wanderer_agents SET lifespan_weeks=10 WHERE id=?",(a,))
            conn.execute("UPDATE wanderer_agents SET lifespan_weeks=6 WHERE id=?",(b,))
            request=bonds.propose(conn,a,b,operation_id="equal-life",kind="equalize")
            self.assertEqual(request["state"],"awaiting_consent")
            bonds.consent(conn,b,"equal-life",accept=True,expected_revision=request["version"])
            conn.execute("UPDATE wanderer_bond_requests SET state='reviewing',review_token='fixture' WHERE id='equal-life'")
            conn.execute("INSERT INTO wanderer_dao_history_blocks(request_id,block_id,content_hash,content_json,summary,state,created_at) VALUES('equal-life','ok','hash','{}','reviewed','reviewed',?)",(local_iso(),))
            result=bonds.apply_review(conn,"equal-life",approved=True,reason="双方同意",review_token="fixture")["result"]
            self.assertEqual(sum(result["before_seconds"]),sum(result["after_seconds"]))
            self.assertLessEqual(abs(result["after_seconds"][0]-result["after_seconds"][1]),1)
            self.assertEqual(sum(r["delta_seconds"] for r in conn.execute("SELECT delta_seconds FROM wanderer_life_ledger WHERE kind='equalize'")),0)

    def test_all_resource_projection_has_bounded_queries(self):
        a,b,c=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b);self.edge(conn,b,c)
            conn.execute("UPDATE wanderer_agents SET tier=0")
            sql=[]
            conn.set_trace_callback(sql.append)
            all_limits=resources.limits(conn,a)
            conn.set_trace_callback(None)
            self.assertEqual(set(all_limits),set(resources.RESOURCE_CATALOG))
            self.assertLessEqual(len([s for s in sql if s.lstrip().upper().startswith('SELECT')]),10)
            self.assertEqual(all_limits['ai_wake']['limit'],260)

    def test_ordinary_friend_and_group_mutations_account_and_release_capacity(self):
        from classroom_app.services import social_friend_service as friends, social_group_service as groups
        from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict
        a,b,_=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b)
            wa,wb=load_wanderer(conn,a),load_wanderer(conn,b)
            ua,ub=build_wanderer_user_dict(wa),build_wanderer_user_dict(wb)
            ia,ib=f"student:{wa['student_id']}",f"student:{wb['student_id']}"
            with patch('classroom_app.services.social_scope_service.can_add',return_value=(True,'')):
                request=friends.send_request(conn,ua,ib,'一起来交流')
                friends.send_request(conn,ua,ib,'重复申请')
            self.assertEqual(resources.snapshot(conn,a,'friend_requests')['used'],1)
            friends.accept_request(conn,ub,request['request_id'])
            self.assertEqual(resources.capacity(conn,a,'friend_capacity')['used'],2)
            group=groups.create_group(conn,ua,name='道友小聚',member_identities=[ib])
            gid=group['id']
            self.assertEqual(resources.capacity(conn,a,'group_memberships')['used'],2)
            groups.transfer_owner(conn,ua,gid,ib)
            self.assertEqual(resources.capacity(conn,b,'owned_groups')['used'],1)
            groups.leave_group(conn,ua,gid)
            self.assertEqual(resources.capacity(conn,a,'group_memberships')['used'],1)
            groups.dissolve_group(conn,ub,gid)
            self.assertEqual(resources.capacity(conn,a,'group_memberships')['used'],0)
            self.assertEqual(resources.capacity(conn,a,'owned_groups')['used'],0)
            friends.delete_friend(conn,ua,ib)
            self.assertEqual(resources.capacity(conn,a,'friend_capacity')['used'],0)

    def test_existing_social_objects_migrate_once_without_losing_overage(self):
        from classroom_app.services.social_common import now_iso
        a,b,_=self.ids
        with self.connect() as conn:
            wa,wb=load_wanderer(conn,a),load_wanderer(conn,b)
            ia,ib=sorted((f"student:{wa['student_id']}",f"student:{wb['student_id']}"))
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) VALUES(?,?,'accepted',?,?)",(ia,ib,ia,now_iso()))
            conn.execute("DELETE FROM wanderer_world_schema WHERE key='social_capacity_migration_v1'")
            self.assertEqual(resources.migrate_social_capacity(conn),2)
            self.assertEqual(resources.migrate_social_capacity(conn),0)
            self.assertEqual(resources.capacity(conn,a,'friend_capacity')['used'],1)

    def test_warning_pardon_restores_original_beneficiaries_after_dissolution(self):
        a,b,c=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b)
            for n in range(5):
                penalties.apply_penalty(conn,a,{'warning':1},reason='违规警告',operation_id=f'warning-pardon:{n}')
            self.assertEqual(load_wanderer(conn,a)['tier'],5)
            bonds.end_bond(conn,a,b,expected_revision=1)
            self.edge(conn,a,c)
            result=penalties.pardon_warnings(conn,a,reason='管理员完整撤销')
            self.assertEqual(len(result['revoked_roots']),5)
            self.assertEqual(load_wanderer(conn,a)['tier'],6)
            self.assertEqual(conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component='warning'",(b,)).fetchone()['balance_units'],0)
            self.assertEqual(penalties.pardon_warnings(conn,a,reason='重复撤销')['revoked_roots'],[])

    def test_packaged_handbook_and_examples_match_runtime_protocol(self):
        import re
        from pathlib import Path
        from classroom_app.services.wanderer_runtime_service import parse_decision
        from classroom_app.services.wanderer_world_service import HANDBOOK,documents
        text=HANDBOOK.read_text(encoding='utf-8')
        self.assertEqual(text,(Path(__file__).resolve().parents[1]/'docs/wanderer-world-handbook-2026-11-10.md').read_text(encoding='utf-8'))
        self.assertEqual(len(documents()),16)
        self.assertTrue(documents()[0]['version'].startswith('v3.0-'))
        examples=re.findall(r'```json\s*(.*?)\s*```',text,re.S)
        self.assertGreaterEqual(len(examples),2)
        self.assertEqual(parse_decision(examples[0])['callback']['requested'],True)
        memory=json.loads(examples[1])
        self.assertEqual(memory['capability'],'memory.write')
        self.assertTrue({'content','location','visibility'}.issubset(memory['args']))

    def test_positive_gains_pay_original_debt_once_without_resplitting(self):
        from classroom_app.services.wanderer_life_service import apply_talent_change
        a,b,c=self.ids
        with self.connect() as conn:
            self.edge(conn,a,b)
            penalties.apply_penalty(conn,a,{'cultivation':20,'talent:luck':1},reason='原裁决',operation_id='debt-root')
            bonds.end_bond(conn,a,b,expected_revision=1)
            self.edge(conn,a,c)
            self.assertEqual(award_cultivation(conn,a,10,'knowledge','gain-1'),0)
            self.assertEqual(award_cultivation(conn,a,10,'knowledge','gain-1'),0)
            self.assertEqual(award_cultivation(conn,a,10,'knowledge','gain-2'),2)
            self.assertEqual(load_wanderer(conn,a)['cultivation_score'],2)
            awards=conn.execute('SELECT awarded_units,debt_paid_units FROM wanderer_cultivation_awards WHERE wanderer_id=? ORDER BY event_id',(a,)).fetchall()
            self.assertEqual([(r['awarded_units'],r['debt_paid_units']) for r in awards],[(9_000_000,9_000_000),(9_000_000,7_000_000)])
            before=load_wanderer(conn,a)
            old_debt=conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component='talent:luck'",(a,)).fetchone()['balance_units']
            result=apply_talent_change(conn,before,'luck',0.2,reason='新收获',event_id='talent-gain')
            self.assertEqual(result['debt_paid'],min(old_debt,200000)/1_000_000)
            self.assertEqual(apply_talent_change(conn,before,'luck',0.2,reason='重放',event_id='talent-gain'),result)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_penalty_roots').fetchone()[0],1)
            paid=conn.execute("SELECT * FROM wanderer_gain_debt_payments WHERE owner_id=? AND component='cultivation' ORDER BY gain_key",(a,)).fetchall()
            self.assertTrue(all(json.loads(r['allocations_json'])[0]['root_id']=='debt-root' for r in paid))
            self.assertFalse(conn.execute('SELECT 1 FROM wanderer_penalty_allocations WHERE beneficiary_id=?',(c,)).fetchone())

    def test_command_prelock_contains_unbonded_peer_and_old_capacity_owners(self):
        a,b,c=self.ids
        with self.connect() as conn:
            self.edge(conn,a,c)
            resources.resize_capacity(conn,a,'knowledge_chars','borrowed',15,base_limits={a:5,c:10})
            bonds.end_bond(conn,a,c,expected_revision=1)
            self.assertEqual(resources.lock_command_resources(conn,a,{'capability':'bond.propose','args':{'peer_id':b}}),sorted((a,b,c)))
            self.assertEqual(resources.lock_command_resources(conn,b,{'capability':'life.resurrect','args':{'peer_id':a}}),sorted((a,b,c)))
