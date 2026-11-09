"""Exact migration witnesses on an explicitly owned native PostgreSQL clone."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from tools.assessment_postgres_rehearsal import connect_offline, _runtime_schema_adapter


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class WandererMigrationRehearsalPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_wproof_" + uuid.uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port,
                                    database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def setUp(self):
        from classroom_app import config
        self.enterContext(patch.object(config, "DB_ENGINE", "postgres"))
        self.conn = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        self.addCleanup(self.conn.close)
        with self.conn.transaction():
            self.conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            self.conn.execute("""
                CREATE TABLE students(id BIGINT PRIMARY KEY,enrollment_status TEXT,
                    hashed_password TEXT,password_reset_required INTEGER,name TEXT);
                INSERT INTO students VALUES
                    (1,'active','DO_NOT_REPORT_HUMAN_PASSWORD',1,'DO_NOT_REPORT_HUMAN_NAME'),
                    (2,'wanderer','DO_NOT_REPORT_OLD_AI_PASSWORD',1,'DO_NOT_REPORT_AI_NAME'),
                    (3,'active','DO_NOT_REPORT_BOUND_PASSWORD',1,'DO_NOT_REPORT_BOUND_NAME');
                CREATE TABLE wanderer_agents(id BIGINT PRIMARY KEY,student_id BIGINT,persona_prompt TEXT);
                INSERT INTO wanderer_agents VALUES (8,3,'DO_NOT_REPORT_PERSONA');
                CREATE TABLE user_sessions(session_user_key TEXT PRIMARY KEY,role TEXT,user_id TEXT,
                    session_id TEXT,expires_at TEXT);
                INSERT INTO user_sessions VALUES
                    ('human','student','1','DO_NOT_REPORT_HUMAN_SESSION','2099'),
                    ('ai','student','2','DO_NOT_REPORT_AI_SESSION','2099'),
                    ('bound','student','3','DO_NOT_REPORT_BOUND_SESSION','2099'),
                    ('teacher','teacher','2','DO_NOT_REPORT_TEACHER_SESSION','2099');
                CREATE TABLE remember_login_tokens(id TEXT PRIMARY KEY,role TEXT,user_id TEXT,
                    revoked_at TEXT NOT NULL DEFAULT '',revoke_reason TEXT NOT NULL DEFAULT '',token_hash TEXT);
                INSERT INTO remember_login_tokens VALUES
                    ('r1','student','1','','','DO_NOT_REPORT_HUMAN_TOKEN'),
                    ('r2','student','2','','','DO_NOT_REPORT_AI_TOKEN'),
                    ('r3','student','3','12','already_revoked','DO_NOT_REPORT_OLD_TOKEN'),
                    ('rt','teacher','2','','','DO_NOT_REPORT_TEACHER_TOKEN');
                CREATE TABLE mp_sessions(id TEXT PRIMARY KEY,user_role TEXT,user_pk BIGINT,revoked INTEGER,token TEXT);
                INSERT INTO mp_sessions VALUES
                    ('m1','student',1,0,'DO_NOT_REPORT_HUMAN_MP'),
                    ('m2','student',2,0,'DO_NOT_REPORT_AI_MP'),
                    ('m3','student',3,1,'DO_NOT_REPORT_OLD_MP'),
                    ('mt','teacher',2,0,'DO_NOT_REPORT_TEACHER_MP');
                CREATE TABLE wechat_bindings(id BIGINT PRIMARY KEY,user_role TEXT,user_pk BIGINT,
                    status TEXT,updated_at TEXT,openid TEXT);
                INSERT INTO wechat_bindings VALUES
                    (1,'student',1,'active','old','DO_NOT_REPORT_HUMAN_OPENID'),
                    (2,'student',2,'active','old','DO_NOT_REPORT_AI_OPENID'),
                    (3,'student',3,'revoked','old','DO_NOT_REPORT_OLD_OPENID'),
                    (4,'teacher',2,'active','old','DO_NOT_REPORT_TEACHER_OPENID');
                CREATE TABLE agent_task_delegations(id TEXT PRIMARY KEY,actor_role TEXT,actor_id BIGINT,
                    status TEXT,revoked_at BIGINT,revoke_reason TEXT,scope_json TEXT);
                CREATE TABLE agent_persistent_authorizations(LIKE agent_task_delegations INCLUDING ALL);
                INSERT INTO agent_task_delegations VALUES
                    ('d1','student',1,'active',NULL,'','DO_NOT_REPORT_HUMAN_SCOPE'),
                    ('d2','student',2,'active',NULL,'','DO_NOT_REPORT_AI_SCOPE'),
                    ('d3','student',3,'revoked',12,'old','DO_NOT_REPORT_OLD_SCOPE'),
                    ('dt','teacher',2,'active',NULL,'','DO_NOT_REPORT_TEACHER_SCOPE');
                INSERT INTO agent_persistent_authorizations SELECT * FROM agent_task_delegations;
                CREATE TABLE wanderer_relationships(wanderer_id BIGINT,peer_identity TEXT,
                    affinity REAL,summary TEXT,updated_at TEXT,PRIMARY KEY(wanderer_id,peer_identity));
                INSERT INTO wanderer_relationships VALUES
                    (8,'student:1',-100,'DO_NOT_REPORT_RELATIONSHIP_A','old'),
                    (8,'student:2',0,'DO_NOT_REPORT_RELATIONSHIP_B','old'),
                    (8,'teacher:1',100,'DO_NOT_REPORT_RELATIONSHIP_C','old'),
                    (8,'teacher:2',40,'DO_NOT_REPORT_ALREADY_MIGRATED','old');
                CREATE TABLE wanderer_relationship_versions(wanderer_id INTEGER NOT NULL,peer_identity TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,origin TEXT NOT NULL DEFAULT 'self',legacy_affinity REAL,
                    source_turn_id TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,PRIMARY KEY(wanderer_id,peer_identity));
                INSERT INTO wanderer_relationship_versions VALUES(8,'teacher:2',3,'self',-20,'prior','old');
                CREATE TABLE agent_platform_requests(
                    id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,task_id BIGINT NOT NULL,
                    attempt_id TEXT NOT NULL,fencing_token BIGINT NOT NULL,delegation_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,actor_id BIGINT NOT NULL,source_session_hash TEXT NOT NULL,
                    authority_fingerprint TEXT NOT NULL,capability_key TEXT NOT NULL,method TEXT NOT NULL,
                    path TEXT NOT NULL,route_source_sha256 TEXT NOT NULL,request_hash TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,request_json TEXT NOT NULL,settlement_hash TEXT NOT NULL,
                    mutates INTEGER NOT NULL DEFAULT 1 CHECK(mutates IN(0,1)),
                    execution_host_id TEXT NOT NULL DEFAULT '',host_execution_finished_at BIGINT,
                    status TEXT NOT NULL CHECK(status IN('admitted','executing','observed_http_result','submitted','uncertain')),
                    result_json TEXT NOT NULL DEFAULT '{}',created_at BIGINT NOT NULL,updated_at BIGINT NOT NULL,
                    reconciliation_status TEXT NOT NULL DEFAULT 'pending' CHECK(reconciliation_status IN('pending','cleared')),
                    reconciliation_resolution TEXT CHECK(reconciliation_resolution IN('occurred','not_occurred')),
                    reconciled_at BIGINT,reconciled_by_role TEXT,reconciled_by_id BIGINT,
                    reconciled_session_hash TEXT,reconciliation_note TEXT,settled_at BIGINT,
                    UNIQUE(actor_role,actor_id,operation_id));
                INSERT INTO agent_platform_requests(id,operation_id,task_id,attempt_id,fencing_token,delegation_id,
                    actor_role,actor_id,source_session_hash,authority_fingerprint,capability_key,method,path,
                    route_source_sha256,request_hash,intent_hash,request_json,settlement_hash,status,created_at,updated_at)
                VALUES('old-receipt','old-operation',1,'old-attempt',1,'d1','student',1,'DO_NOT_REPORT_SOURCE_SESSION',
                    'old-authority','old-capability','POST','/old','old-route','old-request','old-intent',
                    '{"secret":"DO_NOT_REPORT_REQUEST"}','old-settlement','observed_http_result',1,1);
            """)
        self.before = self.capture()

    def capture(self):
        from tools.wanderer_migration_rehearsal import capture
        with self.conn.transaction():
            tables = [row[0] for row in self.conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
            return capture(self.conn, tables)

    def migrate(self):
        from classroom_app.db.schema_account_control import ensure_account_control_schema
        from classroom_app.db.schema_wanderer_social import ensure_wanderer_social_schema, migrate_relationship_affinities
        from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
        with self.conn.transaction(), _runtime_schema_adapter(self.conn) as conn:
            ensure_account_control_schema(conn)
            ensure_wanderer_social_schema(conn, engine="postgres", allow_schema_changes=True)
            migrate_relationship_affinities(conn)
            ensure_agent_platform_requests_schema(conn)

    def proof(self):
        from tools.wanderer_migration_rehearsal import prove
        return prove(self.before, self.capture())

    def assert_failed_after(self, sql):
        self.migrate()
        with self.conn.transaction():
            self.conn.execute(sql)
        proof = self.proof()
        self.assertEqual("failed", proof["status"], proof.get("blockers"))
        self.assertEqual([], proof["allowed_differences"])

    def test_actual_migrations_prove_exact_changes_without_reporting_private_content(self):
        from tools.wanderer_migration_rehearsal import valid_report_proof
        self.migrate()
        proof = self.proof()
        self.assertEqual("ok", proof["status"], proof.get("blockers"))
        self.assertTrue(valid_report_proof(proof))
        self.assertNotIn("DO_NOT_REPORT_", json.dumps(proof))
        self.assertIn("table:students", proof["allowed_differences"])
        self.assertIn("table:wanderer_relationships", proof["allowed_differences"])
        self.assertIn("table:user_sessions", proof["allowed_differences"])
        self.assertNotIn("table:agent_platform_requests", proof["allowed_differences"])
        self.assertEqual([(1,"human"),(2,"ai"),(3,"ai")], self.conn.execute(
            "SELECT id,control_mode FROM students ORDER BY id").fetchall())
        self.assertEqual([("student:1",0.0),("student:2",50.0),("teacher:1",100.0),("teacher:2",40.0)],
            self.conn.execute("SELECT peer_identity,affinity FROM wanderer_relationships ORDER BY peer_identity").fetchall())

    def test_second_real_migration_has_identical_proof_and_preserves_previous_version(self):
        self.migrate()
        first = self.proof()
        self.assertEqual("ok", first["status"], first.get("blockers"))
        self.migrate()
        self.assertEqual(first, self.proof())

    def test_human_password_and_ai_unrelated_field_changes_are_rejected(self):
        for sql in (
            "UPDATE students SET hashed_password='wrong' WHERE id=1",
            "UPDATE students SET name='changed' WHERE id=2",
            "UPDATE students SET control_mode='ai' WHERE id=1",
        ):
            with self.subTest(sql=sql), self.conn.transaction(force_rollback=True):
                self.assert_failed_after(sql)

    def test_unrelated_session_deletion_and_revoked_token_rewrite_are_rejected(self):
        for sql in (
            "DELETE FROM user_sessions WHERE role='teacher'",
            "UPDATE remember_login_tokens SET revoke_reason='rewrite' WHERE id='r3'",
            "UPDATE remember_login_tokens SET token_hash='changed' WHERE id='r2'",
            "UPDATE mp_sessions SET revoked=1 WHERE user_pk=1",
        ):
            with self.subTest(sql=sql), self.conn.transaction(force_rollback=True):
                self.assert_failed_after(sql)

    def test_authority_row_reassignment_and_scope_change_are_rejected(self):
        for sql in (
            "UPDATE agent_task_delegations SET actor_id=2 WHERE id='d1'",
            "UPDATE agent_persistent_authorizations SET scope_json='changed' WHERE id='d2'",
            "UPDATE wechat_bindings SET openid='changed' WHERE id=2",
        ):
            with self.subTest(sql=sql), self.conn.transaction(force_rollback=True):
                self.assert_failed_after(sql)

    def test_affinity_mapping_history_and_original_text_are_exact(self):
        for sql in (
            "UPDATE wanderer_relationships SET affinity=51 WHERE peer_identity='student:2'",
            "UPDATE wanderer_relationships SET summary='changed' WHERE peer_identity='student:1'",
            "UPDATE wanderer_relationship_versions SET legacy_affinity=-99 WHERE peer_identity='student:1'",
            "UPDATE wanderer_relationship_versions SET revision=0 WHERE peer_identity='teacher:2'",
        ):
            with self.subTest(sql=sql), self.conn.transaction(force_rollback=True):
                self.assert_failed_after(sql)

    def test_existing_http_request_payload_and_unrelated_schema_changes_are_rejected(self):
        for sql in (
            "UPDATE agent_platform_requests SET request_json='{}'",
            "ALTER TABLE agent_platform_requests ALTER COLUMN path DROP NOT NULL",
            "ALTER TABLE agent_platform_requests DROP CONSTRAINT agent_platform_request_authority_source",
        ):
            with self.subTest(sql=sql), self.conn.transaction(force_rollback=True):
                self.assert_failed_after(sql)

    def test_fractional_real_affinity_is_verified_at_the_original_storage_precision(self):
        with self.conn.transaction():
            self.conn.execute("INSERT INTO wanderer_relationships VALUES (8,'teacher:3',-80.2,'DO_NOT_REPORT_REAL_FRACTION','old')")
            original_numeric = self.conn.execute("SELECT affinity,affinity::text,affinity::double precision::text "
                "FROM wanderer_relationships WHERE peer_identity='teacher:3'").fetchone()
        self.before = self.capture()
        self.migrate()
        proof = self.proof()
        actual_numeric = self.conn.execute("SELECT affinity,affinity::text,affinity::double precision::text "
            "FROM wanderer_relationships WHERE peer_identity='teacher:3'").fetchone()
        self.assertEqual("ok", proof["status"], {"blockers": proof.get("blockers"),
            "original_numeric": original_numeric, "actual_numeric": actual_numeric})
        with self.conn.transaction():
            self.conn.execute("UPDATE wanderer_relationships SET affinity=9.900001 WHERE peer_identity='teacher:3'")
        self.assertEqual("failed", self.proof()["status"], "A neighboring float32 value must not pass by tolerance")

    def test_agent_bridge_accepts_only_the_matching_exact_wanderer_witness(self):
        from tools.agent_authority_migration_rehearsal import capture as capture_agent, prove as prove_agent
        with self.conn.transaction():
            names = [r[0] for r in self.conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
            original_agent = capture_agent(self.conn, names)
        self.migrate()
        exact = self.proof()
        with self.conn.transaction():
            names = [r[0] for r in self.conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
            migrated_agent = capture_agent(self.conn, names)
        proof = prove_agent(original_agent, migrated_agent, wanderer_proof=exact)
        self.assertEqual("ok", proof["status"], proof.get("blockers"))
        with self.conn.transaction():
            self.conn.execute("UPDATE agent_task_delegations SET scope_json='tampered-human-permission' WHERE id='d1'")
            changed_agent = capture_agent(self.conn, names)
        changed_exact = self.proof()
        self.assertEqual("failed", changed_exact["status"])
        for witness in (exact, changed_exact):
            rejected = prove_agent(original_agent, changed_agent, wanderer_proof=witness)
            self.assertEqual("failed", rejected["status"])

    def test_startup_contract_rejects_a_version_marker_without_required_tables_and_columns(self):
        from tools.wanderer_migration_rehearsal import startup_contract
        from classroom_app.db.schema_wanderer_world import VERSION
        self.migrate()
        with self.conn.transaction():
            self.conn.execute("CREATE TABLE wanderer_world_schema(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            self.conn.execute("INSERT INTO wanderer_world_schema VALUES('version',%s)", (VERSION,))
            report = startup_contract(self.conn)
        self.assertEqual("failed", report["status"])
        self.assertIn("wanderer_wake_events", report["missing_tables"])
        self.assertIn("wanderer_agents.daily_wake_limit", report["missing_columns"])
        self.assertLess(report["present_required_table_count"], report["required_table_count"])

    def test_recomputed_proof_rejects_forged_allowlist_or_status(self):
        from tools.wanderer_migration_rehearsal import valid_report_proof
        self.migrate()
        good = self.proof()
        self.assertTrue(valid_report_proof(good), good.get("blockers"))
        changed = copy.deepcopy(good)
        changed["allowed_differences"].append("table:submissions")
        self.assertFalse(valid_report_proof(changed))
        with self.conn.transaction():
            self.conn.execute("UPDATE students SET hashed_password='wrong' WHERE id=1")
        changed = self.proof()
        changed["status"] = "ok"
        changed["blockers"] = []
        self.assertFalse(valid_report_proof(changed))


if __name__ == "__main__":
    unittest.main()
