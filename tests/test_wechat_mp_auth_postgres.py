"""Opt-in rehearsal against the disposable localhost phase-1 PostgreSQL database.

Set LANSHARE_MP_AUTH_TEST_DATABASE_URL explicitly. The configured application
database is never opened. The suite owns only the initially absent schema
mp_phase1_identity, and removes that schema after all connections are released.
"""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from classroom_app.db import schema_wechat_mp as schema
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.services import wechat_mp_service as service


DATABASE_URL = os.environ.get("LANSHARE_MP_AUTH_TEST_DATABASE_URL", "")
SCHEMA_NAME = "mp_phase1_identity"


@unittest.skipUnless(DATABASE_URL, "explicit disposable PostgreSQL test URL required")
class WechatMpRealPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.driver = psycopg
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            database, address = conn.execute("SELECT current_database(), host(inet_server_addr())").fetchone()
            if database != "lanshare_miniapp_phase1" or address not in {"127.0.0.1", "::1"}:
                raise RuntimeError("refusing a database outside the disposable local phase-1 rehearsal")
            exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (SCHEMA_NAME,)).fetchone()
            if exists:
                raise RuntimeError("test schema already exists; refusing to overwrite its contents")
            conn.execute(f"CREATE SCHEMA {SCHEMA_NAME}")
        cls.addClassCleanup(cls.drop_owned_schema)
        with patch.object(schema, "_SCHEMA_READY", False), patch.object(schema, "get_configured_db_engine", return_value="postgres"):
            with cls.connection() as conn:
                service.ensure_wechat_mp_runtime(conn)

    @classmethod
    def drop_owned_schema(cls):
        with cls.driver.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {SCHEMA_NAME} CASCADE")

    @classmethod
    def connection(cls):
        raw = cls.driver.connect(
            DATABASE_URL,
            row_factory=sqlite_compatible_dict_row,
            options=f"-c search_path={SCHEMA_NAME} -c statement_timeout=10000 -c lock_timeout=8000",
        )
        return LanSharePostgresConnection(raw)

    def setUp(self):
        self.enterContext(patch.object(schema, "_SCHEMA_READY", True))
        self.enterContext(patch.object(schema, "get_configured_db_engine", return_value="postgres"))
        with self.connection() as conn:
            conn.execute("TRUNCATE wechat_bindings, mp_sessions, mp_consumed_bind_tickets, mp_bind_rate_limits")

    def test_runtime_schema_is_idempotent_and_preserves_bindings(self):
        with self.connection() as conn:
            service.create_binding(conn, user_role="student", user_pk=11, openid="existing")
            for _ in range(2):
                schema._SCHEMA_READY = False
                service.ensure_wechat_mp_runtime(conn)
            self.assertEqual(service.find_active_binding(conn, "existing")["user_pk"], 11)
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", (SCHEMA_NAME,)
            ).fetchall()
            self.assertEqual(len(tables), 4)

    def test_single_use_ticket_conflict_does_not_abort_postgres_transaction(self):
        ticket = service.build_bind_ticket("phone-a")
        with self.connection() as conn:
            self.assertIsNotNone(service.consume_bind_ticket(conn, ticket))
        with self.connection() as conn:
            self.assertIsNone(service.consume_bind_ticket(conn, ticket))
            self.assertEqual(conn.execute("SELECT 1 AS result").fetchone()["result"], 1)

    def test_rolled_back_ticket_can_be_retried(self):
        ticket = service.build_bind_ticket("phone-a")
        with self.connection() as conn:
            self.assertIsNotNone(service.consume_bind_ticket(conn, ticket))
            conn.rollback()
        with self.connection() as conn:
            self.assertIsNotNone(service.consume_bind_ticket(conn, ticket))

    def test_parallel_ticket_consumption_has_one_winner(self):
        ticket = service.build_bind_ticket("phone-a")
        barrier = Barrier(6)

        def consume(_):
            with self.connection() as conn:
                barrier.wait(timeout=5)
                return service.consume_bind_ticket(conn, ticket) is not None

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(consume, range(6)))
        self.assertEqual(sum(results), 1)

    def test_parallel_binding_limits_are_shared_between_connections(self):
        barrier = Barrier(10)

        def attempt(index):
            try:
                with self.connection() as conn:
                    barrier.wait(timeout=5)
                    # Separate identities share an IP: both key order and the
                    # cross-worker IP budget must hold under contention.
                    service.check_bind_rate_limit(conn, f"openid:{index}", "ip:shared")
                return True
            except service.WechatMpError:
                return False

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(attempt, range(10)))
        self.assertEqual(sum(results), service.BIND_RATE_MAX_ATTEMPTS)

    def test_concurrent_rebinding_keeps_only_the_final_identity_session(self):
        barrier = Barrier(2)

        def rebind(role):
            user_pk = 11 if role == "student" else 9
            with self.connection() as conn:
                barrier.wait(timeout=5)
                service.create_binding(conn, user_role=role, user_pk=user_pk, openid="phone-a")
                token = service.issue_mp_session(conn, user_role=role, user_pk=user_pk, openid="phone-a")
            return role, token

        with ThreadPoolExecutor(max_workers=2) as executor:
            sessions = list(executor.map(rebind, ("student", "teacher")))
        with self.connection() as conn:
            binding = service.find_active_binding(conn, "phone-a")
            for role, token in sessions:
                session = service.resolve_mp_session(conn, token)
                self.assertEqual(session is not None, role == binding["user_role"])

    def test_single_wechat_logout_and_account_revocation_have_distinct_scopes(self):
        with self.connection() as conn:
            tokens = {}
            for openid in ("phone-a", "phone-b"):
                service.create_binding(conn, user_role="student", user_pk=11, openid=openid)
                tokens[openid] = service.issue_mp_session(conn, user_role="student", user_pk=11, openid=openid)
            self.assertEqual(service.revoke_openid_binding(conn, openid="phone-a", user_role="student", user_pk=11), 1)
            self.assertIsNone(service.resolve_mp_session(conn, tokens["phone-a"]))
            self.assertIsNotNone(service.resolve_mp_session(conn, tokens["phone-b"]))
            self.assertEqual(service.revoke_binding(conn, user_role="student", user_pk=11), 1)
            self.assertIsNone(service.resolve_mp_session(conn, tokens["phone-b"]))


if __name__ == "__main__":
    unittest.main()
