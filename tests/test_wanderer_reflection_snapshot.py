import json
import sqlite3
from tests.wanderer_test_support import WandererDbTestCase
from tools.wanderer_reflection_snapshot import collect, valid_rating


class ReflectionSnapshotTest(WandererDbTestCase):
    def test_independent_arithmetic_detects_neutral_change_and_rounding(self):
        self.assertTrue(valid_rating('very_good', 50, 59))
        self.assertTrue(valid_rating('bad', 50, 46.3))
        self.assertTrue(valid_rating('neutral', 23.45, 23.45))
        self.assertFalse(valid_rating('neutral', 23.45, 23.5))
        self.assertFalse(valid_rating('very_good', 100, 106))
        self.assertFalse(valid_rating('other', 50, 50))

    def test_read_only_aggregates_detect_mischarged_or_inconsistent_sessions(self):
        from classroom_app.db.schema_wanderer_reflection import ensure_wanderer_reflection_schema
        with self.connect() as conn:
            ensure_wanderer_reflection_schema(conn)
            conn.execute("INSERT INTO wanderer_reflection_sessions(wanderer_id,reflection_date,root_event_id,chain_id,life_epoch,authority_version,ai_calls,created_at,updated_at) VALUES(999,'2099-01-01','private-root','private-chain',1,1,2,'now','now')")
            conn.execute("INSERT INTO wanderer_affinity_ratings(wanderer_id,reflection_date,peer_identity,grade,baseline_affinity,new_affinity,revision,rule_version,source_turn_id,reason,created_at,updated_at) VALUES(999,'2099-01-01','secret-peer','neutral',50,55,1,'v1','secret-turn','PRIVATE_REASON','now','now')")
            conn.commit()
            class Adapter:
                def execute(self, sql, params=()):
                    if not sql.startswith('SELECT '):
                        raise AssertionError('Non-read observation')
                    return conn.execute(sql.replace('%s','?'), params)
            before = conn.total_changes
            conn.set_authorizer(lambda action,*_: sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY)
            try:
                result = collect(Adapter(),'2099-01-01')
            finally:
                conn.set_authorizer(None)
            self.assertEqual(before,conn.total_changes)
            self.assertFalse(result['ok'])
            self.assertEqual(result['invariants']['dispatch_count_mismatch'],1)
            self.assertEqual(result['invariants']['invalid_rating_arithmetic'],1)
            serialized = json.dumps(result)
            for secret in ('secret-peer','PRIVATE_REASON','secret-turn','private-root','private-chain'):
                self.assertNotIn(secret,serialized)
