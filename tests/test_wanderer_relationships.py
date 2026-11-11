"""Isolated internal CAS, retained migration and disabled AI absolute writes."""
from classroom_app.db.schema_wanderer_social import ensure_wanderer_social_schema, migrate_relationship_affinities
from classroom_app.services.social_common import SocialError
from classroom_app.services.wanderer_relationship_service import (
    affinity_state, set_affinity, note_interaction, record_feeling, grieve_death,
    apply_turn_feeling, list_relationships, relationship_block, mutual_affinity,
)
from tests.wanderer_test_support import WandererDbTestCase


class RelationshipTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            ensure_wanderer_social_schema(conn)
            for table in ("wanderer_relationship_changes","wanderer_relationship_versions","wanderer_relationships"):
                conn.execute(f"DELETE FROM {table}")
            self.a = self.wanderer(conn,"linwanzhou")
            self.b = self.wanderer(conn,"suqinghe")
            self.peer = f"student:{self.b['student_id']}"
            conn.commit()

    def decide(self,conn,value,revision=0,operation="decide-1",actor=None,peer=None):
        return set_affinity(conn,actor or self.a,peer or self.peer,value,expected_revision=revision,
                            source_turn_id="turn-1",operation_id=operation,reason="我自己的感受")

    def test_can_jump_to_100_then_zero_without_charm(self):
        with self.connect() as conn:
            self.a["talents"]={"charm":0.2}
            self.assertEqual(self.decide(conn,100)["affinity"],100)
            self.assertEqual(self.decide(conn,0,1,"decide-2")["affinity"],0)
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["revision"],2)

    def test_affinity_display_value_is_rounded_to_one_decimal(self):
        from classroom_app.services.wanderer_relationship_service import relationship_rankings
        with self.connect() as conn:
            self.decide(conn, 60.400001525878906)
            state = affinity_state(conn, self.a["id"], self.peer)
            ranked = relationship_rankings(conn, self.a["id"])["items"]
        self.assertEqual(state["affinity"], 60.4)
        self.assertEqual(ranked[0]["affinity"], 60.4)

    def test_operation_replay_and_revision_conflict(self):
        with self.connect() as conn:
            self.decide(conn,100)
            self.assertTrue(self.decide(conn,100)["replayed"])
            with self.assertRaises(SocialError) as stale:
                self.decide(conn,80,0,"stale")
            self.assertEqual(stale.exception.status_code,409)
            with self.assertRaises(SocialError):
                self.decide(conn,80,1,"decide-1")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_relationship_changes").fetchone()[0],1)

    def test_reject_nonfinite_out_of_range_and_self(self):
        with self.connect() as conn:
            for value in (-1,101,float("nan"),float("inf"),True):
                with self.assertRaises(SocialError):
                    self.decide(conn,value)
            with self.assertRaises(SocialError):
                self.decide(conn,100,peer=f"student:{self.a['student_id']}")

    def test_encounter_and_grief_never_choose_affinity(self):
        with self.connect() as conn:
            self.decide(conn,100)
            record_feeling(conn,self.a,self.peer,delta=-5,reason="收到消息")
            self.assertEqual(grieve_death(conn,self.b),1)
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["affinity"],100)
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["revision"],1)

    def test_all_legacy_feeling_outputs_only_record_encounters(self):
        with self.connect() as conn:
            apply_turn_feeling(conn,self.a,self.peer,{"feeling":{"affinity":3}})
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["affinity"],0)
            apply_turn_feeling(conn,self.a,self.peer,{"turn_id":"turn-7","feeling":{"value":100,"expected_revision":0}})
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["affinity"],0)
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["revision"],0)
            self.assertEqual(list_relationships(conn,self.a['id'])[0]['label'],'尚未评分')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_relationship_changes').fetchone()[0],0)

    def test_migration_keeps_original_and_runs_once(self):
        with self.connect() as conn:
            note_interaction(conn,self.a,self.peer)
            conn.execute("DELETE FROM wanderer_relationship_versions")
            conn.execute("UPDATE wanderer_relationships SET affinity=-60")
            self.assertEqual(migrate_relationship_affinities(conn),1)
            self.assertEqual(migrate_relationship_affinities(conn),0)
            state=affinity_state(conn,self.a["id"],self.peer)
            self.assertEqual(state["affinity"],20)
            self.assertEqual(state["legacy_affinity"],-60)
            self.assertEqual(state["origin"],"legacy")
            self.decide(conn,100)
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["legacy_affinity"],-60)

    def test_panel_prompt_and_mutual_are_directed(self):
        with self.connect() as conn:
            self.decide(conn,100)
            self.assertFalse(mutual_affinity(conn,self.a,self.peer,minimum=100))
            self.decide(conn,100,operation="back",actor=self.b,peer=f"student:{self.a['student_id']}")
            self.assertTrue(mutual_affinity(conn,self.a,self.peer,minimum=100))
            row=list_relationships(conn,self.a["id"])[0]
            self.assertEqual(row["revision"],1)
            self.assertEqual(row["origin"],"self")
            self.assertIn("100%",relationship_block(conn,self.a))
            self.assertIn("版本 1",relationship_block(conn,self.a))

    def test_competing_connections_cannot_overwrite_same_revision(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def change(value):
            with self.connect() as conn:
                barrier.wait(timeout=10)
                try:
                    self.decide(conn,value,operation=f"concurrent-{value}")
                    conn.commit()
                    return "updated"
                except SocialError as exc:
                    conn.rollback()
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(change,(20,100)))
        self.assertEqual(result.count("updated"),1)
        self.assertEqual(result.count(409),1)
        with self.connect() as conn:
            self.assertEqual(affinity_state(conn,self.a["id"],self.peer)["revision"],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_relationship_changes").fetchone()[0],1)
