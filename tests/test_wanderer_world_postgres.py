"""Independent-connection races on an explicitly owned empty PostgreSQL cluster."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import uuid

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_wanderers import ensure_wanderer_schema
from classroom_app.db.schema_wanderer_world import ensure_wanderer_world_schema
from classroom_app.db.schema_wanderer_runtime import ensure_wanderer_runtime_schema
from classroom_app.db.schema_wanderer_social import ensure_wanderer_social_schema
from classroom_app.db.schema_wanderer_reflection import ensure_wanderer_reflection_schema
from classroom_app.db.schema_account_control import ensure_account_control_schema
from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.services import wanderer_resource_service as resources
from classroom_app.services import wanderer_bond_service as bonds
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.time_utils import local_iso
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires owned offline PostgreSQL")
class WandererWorldPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.name = "lanshare_assessment_rehearsal_world_" + uuid.uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.name}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.name}"')
        finally:
            cls.admin.close()

    def connect(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.name)
        raw.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(raw)

    def setUp(self):
        from classroom_app import config
        self.enterContext(patch.object(config, "DB_ENGINE", "postgres"))
        self.enterContext(patch("classroom_app.services.wanderer_bond_service._notify"))
        with self.connect() as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            conn.execute("""CREATE TABLE students(id BIGINT PRIMARY KEY,name TEXT,student_id_number TEXT,class_id BIGINT,
                description TEXT,avatar_file_hash TEXT,avatar_mime_type TEXT,gender TEXT,enrollment_status TEXT,
                hashed_password TEXT,password_reset_required INTEGER DEFAULT 0,school_code TEXT,school_name TEXT,college TEXT,department TEXT)""")
            conn.execute("CREATE TABLE user_sessions(session_user_key TEXT PRIMARY KEY,role TEXT,user_id TEXT,session_id TEXT,expires_at TEXT)")
            ensure_wanderer_schema(conn, engine="postgres", allow_schema_changes=True)
            ensure_wanderer_world_schema(conn, engine="postgres", allow_schema_changes=True)
            ensure_wanderer_runtime_schema(conn, engine="postgres", allow_schema_changes=True)
            ensure_wanderer_social_schema(conn, engine="postgres", allow_schema_changes=True)
            ensure_wanderer_reflection_schema(conn, engine="postgres", allow_schema_changes=True)
            ensure_account_control_schema(conn)
            ensure_agent_platform_requests_schema(conn)
            for sid in range(1, 5):
                conn.execute("INSERT INTO students(id,name,student_id_number,class_id,enrollment_status,hashed_password) VALUES(?,?,?,99,'active','synthetic-password')", (sid, f"Synthetic {sid}", f"SYN-{sid}"))
            for wid in (1, 2, 3):
                conn.execute("INSERT INTO wanderer_agents(id,student_id,slug,display_name,archetype,persona_prompt,created_at,updated_at,born_at,lifespan_weeks,daily_wake_limit) VALUES(?,?,?,?,?,?,?,?,?,52,1)",
                    (wid,wid,f"synthetic-{wid}",f"Synthetic {wid}","synthetic","synthetic",local_iso(),local_iso(),local_iso()))
            conn.commit()

    def race(self, count, fn):
        barrier = threading.Barrier(count)
        def run(i):
            barrier.wait(timeout=10)
            return fn(i)
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count), timeout=30))

    def consume(self, actor, key):
        with self.connect() as conn:
            result = resources.consume(conn, actor, "ai_wake", 1, key)
            conn.commit()
            return result

    def bond(self, first, second):
        with self.connect() as conn:
            key=f"{first}:{second}"
            conn.execute("INSERT INTO wanderer_bonds(id,pair_key,first_id,second_id,state,first_request_id,last_request_id,created_at,updated_at) VALUES(?,?,?,?,'active','synthetic','synthetic',?,?)", (key,key,first,second,local_iso(),local_iso()))
            conn.commit()

    def test_account_migration_preserves_human_and_revokes_only_bound_ai(self):
        with self.connect() as conn:
            for sid in (1,4):
                conn.execute("INSERT INTO user_sessions VALUES(?,'student',?,'synthetic','2099')", (f"student:{sid}",str(sid)))
            for _ in range(2):
                ensure_account_control_schema(conn)
                ensure_wanderer_world_schema(conn,allow_schema_changes=True)
                ensure_wanderer_runtime_schema(conn,allow_schema_changes=True)
            self.assertIsNone(conn.execute("SELECT hashed_password FROM students WHERE id=1").fetchone()[0])
            self.assertEqual(conn.execute("SELECT hashed_password FROM students WHERE id=4").fetchone()[0], "synthetic-password")
            self.assertEqual([r[0] for r in conn.execute("SELECT user_id FROM user_sessions")], ["4"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_control_migrations").fetchone()[0], 3)
            conn.commit()

    def test_legacy_human_receipt_survives_nullable_source_migration_twice(self):
        with self.connect() as conn:
            conn.execute("ALTER TABLE agent_platform_requests DROP CONSTRAINT agent_platform_request_authority_source")
            for name in ("principal_kind","wanderer_id","turn_id","life_epoch","authority_version"):
                conn.execute(f"ALTER TABLE agent_platform_requests DROP COLUMN {name}")
            for name in ("task_id","attempt_id","delegation_id","source_session_hash"):
                conn.execute(f"ALTER TABLE agent_platform_requests ALTER COLUMN {name} SET NOT NULL")
            conn.execute("""INSERT INTO agent_platform_requests(id,operation_id,task_id,attempt_id,fencing_token,delegation_id,actor_role,actor_id,source_session_hash,authority_fingerprint,capability_key,method,path,route_source_sha256,request_hash,intent_hash,request_json,settlement_hash,status,created_at,updated_at)
                VALUES('legacy','legacy',7,'attempt',1,'grant','student',4,'session','fingerprint','fixture','GET','/fixture','sha','request','intent','{}','settlement','observed_http_result',1,1)""")
            for _ in range(2): ensure_agent_platform_requests_schema(conn)
            row=conn.execute("SELECT * FROM agent_platform_requests WHERE id='legacy'").fetchone()
            self.assertEqual((row["principal_kind"],row["task_id"],row["source_session_hash"]),("human_delegation",7,"session"))
            conn.commit()

    def test_last_quota_has_one_winner_across_independent_connections(self):
        result=self.race(8,lambda i:self.consume(1,f"last:{i}"))
        self.assertEqual(sum(bool(row["ok"]) for row in result),1)
        with self.connect() as conn:
            self.assertEqual(resources.snapshot(conn,1,"ai_wake")["used"],1)

    def _daily_run(self):
        from classroom_app.services import wanderer_daily_reflection_service as daily
        with self.connect() as conn:
            w=dict(conn.execute('SELECT * FROM wanderer_agents WHERE id=1').fetchone())
            event=runtime.enqueue_wake_event(conn,1,'reflection',local_iso()[:10],{'scope':{'kind':'private'}})
            with patch.object(daily,'_friends',return_value=[{'peer_identity':'student:2','name':'Synthetic 2'}]):
                daily.register_session(conn,w,local_iso()[:10],event)
            conn.commit()
        return {**event,'wanderer':w,'scope':{'kind':'private'},'turn_id':'synthetic-rating-turn'}

    def test_three_free_reflection_slots_have_exactly_three_winners(self):
        from classroom_app.services import wanderer_daily_reflection_service as daily
        run=self._daily_run()
        def reserve(index):
            with self.connect() as conn:
                result=daily.reserve_free_call(conn,{**run,'turn_id':f'free-race-{index}'})
                conn.commit()
                return result
        results=self.race(8,reserve)
        self.assertEqual(sum(item['ok'] for item in results),3)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT ai_calls FROM wanderer_reflection_sessions').fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_reflection_dispatches').fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_resource_reservations').fetchone()[0],0)

    def test_daily_rating_conflicts_preserve_one_baseline_and_revision(self):
        from classroom_app.services import wanderer_daily_reflection_service as daily
        from classroom_app.services.social_common import SocialError
        run=self._daily_run()
        def rate(index):
            with self.connect() as conn:
                try:
                    value=daily.rate_friend(conn,run,'student:2','good' if index else 'very_good',
                        expected_revision=0,operation_id=f'grade-race-{index}')
                    conn.commit()
                    return value
                except SocialError as exc:
                    conn.rollback()
                    return {'conflict':exc.status_code}
        with patch.object(daily,'_friends',return_value=[{'peer_identity':'student:2','name':'Synthetic 2'}]):
            results=self.race(2,rate)
        self.assertEqual(sum('affinity' in item for item in results),1,results)
        with self.connect() as conn:
            row=conn.execute('SELECT baseline_affinity,revision FROM wanderer_affinity_ratings').fetchone()
            self.assertEqual((row[0],row[1]),(0,1))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_affinity_rating_operations').fetchone()[0],1)

    def test_same_rating_operation_replays_across_connections(self):
        from classroom_app.services import wanderer_daily_reflection_service as daily
        run=self._daily_run()
        def rate(_):
            with self.connect() as conn:
                result=daily.rate_friend(conn,run,'student:2','good',expected_revision=0,operation_id='same-grade')
                conn.commit()
                return result
        with patch.object(daily,'_friends',return_value=[{'peer_identity':'student:2','name':'Synthetic 2'}]):
            results=self.race(6,rate)
        self.assertEqual({item['affinity'] for item in results},{5})
        self.assertEqual(sum(not item['replayed'] for item in results),1)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_affinity_rating_operations').fetchone()[0],1)

    def test_legacy_social_and_daily_capacity_migrate_once_without_removing_objects(self):
        from classroom_app.db.schema_social import ensure_social_schema
        from classroom_app.db.schema_wanderer_social import migrate_relationship_affinities
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            ensure_social_schema(conn, engine="postgres")
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) "
                         "VALUES('student:1','student:2','accepted','student:1',?)", (stamp,))
            group = conn.execute("INSERT INTO social_groups(name,owner_identity,created_at,updated_at) "
                                 "VALUES('Synthetic','student:1',?,?) RETURNING id", (stamp, stamp)).fetchone()[0]
            for sid in (1, 2):
                conn.execute("INSERT INTO social_group_members(group_id,member_identity,joined_at) VALUES(?,?,?)",
                             (group, f"student:{sid}", stamp))
            conn.execute("INSERT INTO social_friend_request_log(requester_identity,target_identity,event,created_at) "
                         "VALUES('student:1','student:2','request',?)", (stamp,))
            for _ in range(2):
                ensure_wanderer_social_schema(conn, engine="postgres", allow_schema_changes=True)
                migrate_relationship_affinities(conn)
                resources.migrate_capacity(conn)
                resources.migrate_social_capacity(conn)
            self.assertEqual(resources.migrate_social_capacity(conn), 0)
            self.assertEqual(resources.capacity(conn, 1, "friend_capacity")["used"], 1)
            self.assertEqual(resources.capacity(conn, 2, "friend_capacity")["used"], 1)
            self.assertEqual(resources.capacity(conn, 1, "owned_groups")["used"], 1)
            self.assertEqual(resources.capacity(conn, 1, "group_memberships")["used"], 1)
            self.assertEqual(resources.capacity(conn, 2, "group_memberships")["used"], 1)
            self.assertEqual(resources.snapshot(conn, 1, "friend_requests")["used"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_group_members WHERE left_at IS NULL").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_friendships WHERE status='accepted'").fetchone()[0], 1)
            conn.commit()

    def test_same_operation_replay_charges_one_time(self):
        result=self.race(8,lambda i:self.consume(1,"same-operation"))
        self.assertTrue(all(row["ok"] for row in result))
        with self.connect() as conn:
            self.assertEqual(resources.snapshot(conn,1,"ai_wake")["used"],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_resource_reservations").fetchone()[0],1)

    def test_two_memory_revisions_preserve_one_successor_and_original(self):
        from classroom_app.services.wanderer_identity_service import load_wanderer
        from classroom_app.services.wanderer_autobiography_service import write_memory
        with self.connect() as conn:
            actor = load_wanderer(conn, 1)
            original = write_memory(conn, actor, {"content": "Original synthetic memory", "location": "platform"},
                                    turn_id="synthetic", operation_id="original", scope={"kind": "public"}, available_evidence=[])
            conn.commit()
        def revise(index):
            with self.connect() as conn:
                try:
                    result = write_memory(conn, actor, {"content": f"Revision {index}", "location": "platform",
                        "supersedes_id": original["memory_id"], "expected_revision": 1},
                        turn_id="synthetic", operation_id=f"revision:{index}", scope={"kind": "public"}, available_evidence=[])
                    conn.commit()
                    return result
                except ValueError:
                    conn.rollback()
                    return None
        results = self.race(2, revise)
        self.assertEqual(sum(item is not None for item in results), 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_memory_sources WHERE supersedes_id=?", (original["memory_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT content FROM wanderer_episodic_memories WHERE id=?", (original["memory_id"],)).fetchone()[0], "Original synthetic memory")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_episodic_memories WHERE status='active'").fetchone()[0], 1)

    def test_replayed_bond_approval_rewards_each_owner_exactly_once(self):
        with patch("classroom_app.services.wanderer_bond_service._notify_application"):
            with self.connect() as conn:
                from classroom_app.services.wanderer_relationship_service import set_affinity
                for owner, peer in ((1, 2), (2, 1)):
                    set_affinity(conn,dict(conn.execute('SELECT * FROM wanderer_agents WHERE id=?',(owner,)).fetchone()),
                                 f'student:{peer}',100,expected_revision=0,source_turn_id='synthetic-current-decision',
                                 operation_id=f'approval-affinity:{owner}:{peer}')
                request = bonds.propose(conn, 1, 2, operation_id="approval")
                bonds.consent(conn, 2, "approval", accept=True, expected_revision=request["version"])
                conn.execute("UPDATE wanderer_bond_requests SET state='reviewing',review_token='fixture' WHERE id='approval'")
                conn.execute("INSERT INTO wanderer_dao_history_blocks(request_id,block_id,content_hash,content_json,summary,state,created_at) "
                             "VALUES('approval','test','hash','{}','reviewed','reviewed',?)", (local_iso(),))
                conn.commit()
        def approve(_index):
            with self.connect() as conn:
                result = bonds.apply_review(conn, "approval", approved=True, reason="Synthetic reviewed", review_token="fixture")
                conn.commit()
                return result
        results = self.race(8, approve)
        self.assertTrue(all(item["state"] == "approved" for item in results))
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_bonds WHERE state='active'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_life_ledger WHERE kind='bond_reward'").fetchone()[0], 2)
            self.assertEqual([row[0] for row in conn.execute("SELECT life_bonus_seconds FROM wanderer_agents WHERE id IN (1,2) ORDER BY id")], [4*604800,4*604800])

    def test_replayed_penalty_conserves_direct_owner_liability(self):
        from classroom_app.services.wanderer_penalty_service import apply_penalty
        self.bond(1, 2); self.bond(2, 3)
        def punish(_index):
            with self.connect() as conn:
                result = apply_penalty(conn, 1, {"life_seconds": 1000}, reason="Synthetic", operation_id="same-penalty")
                conn.commit()
                return result
        with patch("classroom_app.services.heavenly_dao_service.notify_wanderer"):
            results = self.race(8, punish)
        self.assertTrue(all(item == results[0] for item in results))
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_penalty_roots").fetchone()[0], 1)
            self.assertEqual([(row[0],row[1]) for row in conn.execute("SELECT beneficiary_id,amount_units FROM wanderer_penalty_allocations ORDER BY beneficiary_id")], [(1,800),(2,200)])
            self.assertEqual(conn.execute("SELECT life_penalty_weeks FROM wanderer_agents WHERE id=3").fetchone()[0], 0)

    def test_two_shared_knowledge_writers_cannot_duplicate_owner_capacity(self):
        self.bond(1, 2)
        def allocate(index):
            with self.connect() as conn:
                result = resources.resize_capacity(conn, index+1, "knowledge_chars", f"knowledge:{index}", 180,
                                                   base_limits={1: 100, 2: 100})
                conn.commit()
                return result
        results = self.race(2, allocate)
        self.assertEqual(sum(item["ok"] for item in results), 1)
        with self.connect() as conn:
            used = conn.execute("SELECT SUM(used_units) FROM wanderer_resource_usage WHERE resource='knowledge_chars' AND budget_date='capacity'").fetchone()[0]
            self.assertEqual(used, 180*resources.SCALE)

    def test_penalty_partner_death_and_lower_owner_consumption_do_not_deadlock(self):
        from classroom_app.services.wanderer_penalty_service import apply_penalty
        self.bond(2, 3); self.bond(1, 2)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET life_penalty_weeks=? WHERE id=2", ((52*604800-100)/604800,))
            conn.commit()
        def compete(index):
            if index:
                return self.consume(1, "lower-owner-consumption")
            with self.connect() as conn:
                result = apply_penalty(conn, 3, {"life_seconds": 1000}, reason="Synthetic", operation_id="partner-death")
                conn.commit()
                return result
        with patch("classroom_app.services.heavenly_dao_service.notify_wanderer"):
            results = self.race(2, compete)
        self.assertTrue(results[1]["ok"])
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM wanderer_agents WHERE id=2").fetchone()[0], "dead")
            self.assertEqual(conn.execute("SELECT life_penalty_weeks FROM wanderer_agents WHERE id=1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_penalty_roots").fetchone()[0], 1)

    def test_native_knowledge_entries_and_capacity_commit_together_under_contention(self):
        from classroom_app.services.wanderer_identity_service import load_wanderer
        from classroom_app.services.wanderer_knowledge_service import add_knowledge
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        with self.connect() as conn:
            actor = load_wanderer(conn, 1)
            wanderer_runtime_setting_set(conn, "autonomous_v2_enabled", "1")
            limit = resources.capacity(conn, 1, "knowledge_chars")["limit"]
            resources.grandfather_capacity(conn, 1, "knowledge_chars", "knowledge:retained", limit-60)
            conn.commit()
        def remember(index):
            with self.connect() as conn:
                try:
                    result = add_knowledge(conn, actor, source_kind="reflection", source_ref=f"synthetic:{index}",
                                           title="Synthetic", digest=str(index)*40, rng=lambda: 0.5)
                    conn.commit()
                    return result
                except ValueError:
                    conn.rollback()
                    return None
        results = self.race(2, remember)
        self.assertEqual(sum(item is not None for item in results), 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_knowledge_entries WHERE status='active'").fetchone()[0], 1)
            self.assertEqual(resources.capacity(conn, 1, "knowledge_chars")["remaining"], 20)

    def test_knowledge_revisions_survive_concurrent_updates_and_repeated_migration(self):
        from classroom_app.services.wanderer_identity_service import load_wanderer
        from classroom_app.services.wanderer_autonomous_learning import remember
        from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set
        evidence = {'ref':'world:synthetic:page:0','kind':'world_chapter',
                    'id':'synthetic','hash':'stable-chapter-hash','title':'Synthetic'}
        with self.connect() as conn:
            actor = load_wanderer(conn, 1)
            wanderer_runtime_setting_set(conn, 'autonomous_v2_enabled', '1')
            conn.commit()
        def write(index):
            with self.connect() as conn:
                result = remember(conn, actor, {'evidence_ref':evidence['ref'],
                    'digest':f'Synthetic understanding {index}'}, turn_id=f'synthetic-turn-{index}',
                    scope={'kind':'public'}, available_evidence=[evidence])
                conn.commit()
                return result
        results = self.race(2, write)
        self.assertEqual(len({item['entry_id'] for item in results}), 1)
        self.assertEqual(sorted(item['revision'] for item in results), [1, 2])
        with self.connect() as conn:
            for _ in range(2):
                ensure_wanderer_runtime_schema(conn, engine='postgres', allow_schema_changes=True)
            records = conn.execute('SELECT record_json FROM wanderer_knowledge_revisions '
                                   'WHERE wanderer_id=1 ORDER BY revision').fetchall()
            self.assertEqual({json.loads(row[0])['digest'] for row in records},
                             {'Synthetic understanding 0', 'Synthetic understanding 1'})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_knowledge_entries').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_evidence_snapshots').fetchone()[0], 0)
            conn.commit()

    def test_runtime_memory_write_rechecks_epoch_after_waiting_for_owner_lock(self):
        with self.connect() as conn:
            event = runtime.enqueue_wake_event(conn, 1, "fixture", "epoch-race")
            run = runtime._claim(conn, event["event_id"])
            conn.commit()
        arrived = threading.Event()
        original_lock = resources.lock_command_resources
        def signal_lock(conn, actor_id, command):
            arrived.set()
            return original_lock(conn, actor_id, command)
        command = {"id": "old-life", "capability": "memory.write", "args": {"content": "Old epoch", "location": "platform"}}
        with self.connect() as owner:
            bonds.lock_agents(owner, [1])
            with patch.object(resources, "lock_command_resources", side_effect=signal_lock), ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(runtime.execute_command(self.connect, run, command)))
                self.assertTrue(arrived.wait(10))
                owner.execute("UPDATE wanderer_agents SET life_epoch=life_epoch+1,authority_version=authority_version+1 WHERE id=1")
                owner.commit()
                try:
                    result = future.result(timeout=15)
                    self.assertEqual(result["status"], "denied")
                except (ValueError, PermissionError):
                    pass  # Authority may reject before command admission.
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_memory_sources").fetchone()[0], 0)

    def test_native_http_business_transaction_fences_epoch_changed_after_authentication(self):
        from fastapi import Depends, FastAPI, HTTPException
        from classroom_app.dependencies import get_current_user
        from classroom_app.services.wanderer_platform_service import execute_platform_command
        from classroom_app.services.agent_platform_route_capability import route_key
        entered, proceed = threading.Event(), threading.Event()
        app = FastAPI()
        with self.connect() as conn:
            conn.execute("CREATE TABLE synthetic_guard_writes(id BIGSERIAL PRIMARY KEY,owner_id BIGINT)")
            event = runtime.enqueue_wake_event(conn, 1, "fixture", "native-write-epoch")
            run = runtime._claim(conn, event["event_id"])
            conn.commit()
        @app.post("/api/synthetic-native-writes")
        def native_write(user=Depends(get_current_user)):
            entered.set()
            if not proceed.wait(10):
                raise RuntimeError("Synthetic synchronization timed out")
            with self.connect() as conn:
                conn.execute("INSERT INTO synthetic_guard_writes(owner_id) VALUES(?)", (int(user["id"]),))
                conn.commit()
            return {"status": "ok"}
        operation_id = str(uuid.uuid4())
        authority = {key: run[key] for key in ("turn_id", "life_epoch", "fencing_token", "authority_version")}
        def execute():
            return asyncio.run(execute_platform_command(wanderer_id=1, **authority, app=app, conn_factory=self.connect,
                operation_id=operation_id, command={"id": "native", "capability": route_key("POST", "/api/synthetic-native-writes"), "args": {}}))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execute)
            try:
                self.assertTrue(entered.wait(10))
                with self.connect() as conn:
                    bonds.lock_agents(conn, [1])
                    conn.execute("UPDATE wanderer_agents SET life_epoch=life_epoch+1,authority_version=authority_version+1 WHERE id=1")
                    conn.commit()
            finally:
                proceed.set()
            with self.assertRaises(HTTPException) as denied:
                future.result(timeout=15)
            self.assertTrue(denied.exception.detail["requires_reconciliation"])
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_guard_writes").fetchone()[0], 0)
            self.assertIsNotNone(conn.execute("SELECT host_execution_finished_at FROM agent_platform_requests WHERE operation_id=?", (operation_id,)).fetchone()[0])
            self.assertEqual(conn.execute("SELECT state FROM wanderer_resource_reservations WHERE operation_id=?",
                (f"platform-action:1:{operation_id}:active_interactions",)).fetchone()[0], "released")

    def test_two_borrowers_cannot_copy_the_same_owner_balance(self):
        self.bond(1,2); self.bond(1,3)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=0 WHERE id IN (2,3)")
            conn.commit()
        result=self.race(2,lambda i:self.consume(i+2,f"borrower:{i}"))
        self.assertEqual(sum(bool(row["ok"]) for row in result),1)
        with self.connect() as conn:
            rows=conn.execute("SELECT owner_id,used_units,reserved_units FROM wanderer_resource_usage").fetchall()
            self.assertEqual([(r[0],r[1],r[2]) for r in rows],[(1,1_000_000,0)])

    def test_duplicate_wake_event_can_acquire_one_lease(self):
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,1,"synthetic","same-event")
            conn.commit()
        def claim(_):
            with self.connect() as conn:
                result=runtime._claim(conn,event["event_id"])
                conn.commit()
                return result
        result=self.race(8,claim)
        self.assertEqual(sum(row is not None for row in result),1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_turns").fetchone()[0],1)

    def test_bond_end_and_reservation_share_lock_order(self):
        self.bond(1,2)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=0 WHERE id=2")
            conn.commit()
        def operation(index):
            if index: return self.consume(2,"end-race")
            with self.connect() as conn:
                result=bonds.end_bond(conn,1,2,expected_revision=1,reason="synthetic race")
                conn.commit()
                return result
        result=self.race(2,operation)
        self.assertEqual(result[0]["state"],"ended")
        with self.connect() as conn:
            self.assertEqual(resources.snapshot(conn,2,"ai_wake")["remaining"],0)
            self.assertLessEqual(resources.snapshot(conn,1,"ai_wake")["used"],1)
