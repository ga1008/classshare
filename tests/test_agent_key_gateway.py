"""Synthetic key/gateway lifecycle tests; no real keys, DB or provider calls."""
import asyncio
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.db.schema_agent_model import ensure_agent_model_schema
from classroom_app.services import agent_key_service as keys
from classroom_app.services.agent_model_gateway_service import configuration_generation


VALID = {"status": "valid", "message": "Synthetic connectivity OK", "usage": {}, "response_ms": 1}
FAILED = {"status": "failed", "message": "Synthetic rejection", "usage": {}, "response_ms": 1}


def install_keys(conn):
    conn.executescript("""
        CREATE TABLE agent_runtime_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL DEFAULT 'deepseek',
            key_label TEXT NOT NULL, key_fingerprint TEXT NOT NULL UNIQUE, key_encrypted TEXT NOT NULL,
            key_suffix TEXT NOT NULL, base_url TEXT NOT NULL, model TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, is_active INTEGER NOT NULL DEFAULT 0,
            created_by_teacher_id INTEGER, last_test_status TEXT DEFAULT 'unchecked',
            last_test_message TEXT DEFAULT '', last_test_usage_json TEXT DEFAULT '{}',
            last_test_at TEXT, last_used_at TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE agent_runtime_key_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key_id INTEGER NOT NULL,
            status TEXT, message TEXT, response_ms INTEGER, usage_json TEXT,
            checked_by_teacher_id INTEGER, created_at TEXT,
            FOREIGN KEY(key_id) REFERENCES agent_runtime_api_keys(id) ON DELETE CASCADE
        );
        CREATE TABLE agent_runtime_usage_snapshots (id INTEGER PRIMARY KEY, source TEXT, usage_json TEXT);
        INSERT INTO agent_runtime_usage_snapshots VALUES (1, 'legacy', '{"turns":999}');
    """)
    ensure_agent_model_schema(conn)
    conn.commit()


def connect(path=":memory:"):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class AgentKeyGatewayTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect()
        install_keys(self.conn)
        self.addCleanup(self.conn.close)
        # Synthetic cipher, deliberately reversible only inside the test fixture.
        self.encryption = patch.object(keys, "encrypt_secret", side_effect=lambda v: "fixture:" + v)
        self.decryption = patch.object(keys, "decrypt_secret", side_effect=lambda v: v.removeprefix("fixture:") if v else "")
        self.encryption.start()
        self.decryption.start()
        self.addCleanup(self.encryption.stop)
        self.addCleanup(self.decryption.stop)

    def create(self, label="one", *, active=False, conn=None):
        return keys.create_agent_api_key(conn or self.conn, {
            "api_key": "synthetic-key-" + label, "key_label": label,
            "test_on_save": True, "make_active": active,
        }, teacher_id=7, test_result=VALID)["key"]["id"]

    def receipt(self, key_id, generation, *, input_tokens=None, output_tokens=None, status="completed"):
        count = self.conn.execute("SELECT COUNT(*) FROM agent_model_requests").fetchone()[0]
        self.conn.execute("""
            INSERT INTO agent_model_requests (
                id, task_id, attempt_id, fencing_token, actor_role, actor_id,
                key_id, config_generation, endpoint, model, status, output_token_limit,
                input_tokens, output_tokens, expires_at, created_at, completed_at
            ) VALUES (?, 1, 'attempt', 1, 'teacher', 7, ?, ?, 'chat/completions', 'deepseek-v4-pro', ?, 20, ?, ?, 9999999999, ?, ?)
        """, (str(count), key_id, generation, status, input_tokens, output_tokens,
              f"2026-09-10T10:00:{count:02}", f"2026-09-10T10:00:{count:02}" if status == "completed" else None))

    def test_configuration_reads_are_side_effect_free_and_need_real_observation(self):
        key_id = self.create(active=True)
        before = self.conn.total_changes
        dashboard = keys.build_agent_key_dashboard(self.conn)
        self.assertEqual(before, self.conn.total_changes)
        config = dashboard["runtime_config"]
        self.assertEqual("pending_observation", config["status"])
        self.assertIsNone(config["last_request_generation"])
        self.assertNotIn("config_path", config)
        self.assertNotIn("synthetic-key", json.dumps(dashboard))
        self.receipt(key_id, config["desired_generation"], status="failed")
        self.assertEqual("pending_observation", keys.get_agent_model_configuration(self.conn)["status"])
        self.receipt(key_id, config["desired_generation"], input_tokens=0, output_tokens=0)
        self.assertEqual("observed", keys.get_agent_model_configuration(self.conn)["status"])

    def test_switch_distinguishes_desired_from_inflight_old_generation(self):
        old = self.create("old", active=True)
        generation = keys.get_agent_model_configuration(self.conn)["desired_generation"]
        new = self.create("new", active=True)
        self.receipt(old, generation, input_tokens=5, output_tokens=1)
        config = keys.get_agent_model_configuration(self.conn)
        self.assertEqual(new, config["key_id"])
        self.assertNotEqual(generation, config["desired_generation"])
        self.assertEqual(generation, config["last_request_generation"])
        self.assertEqual("pending_observation", config["status"])

    def test_probe_metadata_and_noop_activation_do_not_change_generation(self):
        key_id = self.create(active=True)
        before = keys.get_agent_model_configuration(self.conn)["desired_generation"]
        result = keys.record_saved_agent_key_test(self.conn, key_id, teacher_id=7,
            expected_configuration=before, result=VALID, activate=True)
        self.assertEqual(before, result["runtime_config"]["desired_generation"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_key_checks").fetchone()[0])

    def test_missing_tokens_are_null_and_reported_zero_is_zero(self):
        empty = keys.get_agent_model_usage(self.conn)["groups"]["day"]["totals"]
        self.assertEqual(0, empty["turns"])
        self.assertIsNone(empty["input_tokens"])
        self.assertIsNone(empty["cost_usd"])
        key_id = self.create(active=True)
        self.receipt(key_id, "a", input_tokens=0, output_tokens=0)
        zero = keys.get_agent_model_usage(self.conn)["groups"]["day"]["totals"]
        self.assertEqual(0, zero["input_tokens"])
        self.receipt(key_id, "a", input_tokens=None, output_tokens=2)
        partial = keys.get_agent_model_usage(self.conn)["groups"]["day"]["totals"]
        self.assertIsNone(partial["input_tokens"])
        self.assertEqual(0, partial["reported_input_tokens"])
        self.assertEqual(1, partial["input_reported_requests"])
        self.assertEqual(2, partial["output_tokens"])
        self.assertEqual(2, partial["turns"])

    def test_usage_refresh_never_contacts_runtime_or_mutates_legacy_history(self):
        before = self.conn.total_changes
        with patch.object(keys.httpx, "AsyncClient", side_effect=AssertionError("Unexpected network")):
            value = asyncio.run(keys.fetch_agent_runtime_usage(self.conn, teacher_id=7))
        self.assertEqual("agent_model_requests", value["source"])
        self.assertEqual(before, self.conn.total_changes)
        self.assertEqual('{"turns":999}', self.conn.execute("SELECT usage_json FROM agent_runtime_usage_snapshots").fetchone()[0])

    def test_delete_erases_credential_preserves_checks_requests_and_allows_readd(self):
        key_id = self.create(active=True)
        self.receipt(key_id, "old", input_tokens=1, output_tokens=1)
        keys.delete_agent_api_key(self.conn, key_id)
        row = self.conn.execute("SELECT * FROM agent_runtime_api_keys WHERE id = ?", (key_id,)).fetchone()
        self.assertEqual("", row["key_encrypted"])
        self.assertIsNotNone(row["deleted_at"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_key_checks").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_model_requests").fetchone()[0])
        self.assertEqual([], keys.list_agent_api_keys(self.conn))
        self.assertIsNone(keys.get_active_agent_api_key(self.conn))
        self.assertNotEqual(key_id, self.create())

    def test_activation_requires_probe_and_failed_probe_leaves_active_key(self):
        old = self.create("old", active=True)
        untested = keys.create_agent_api_key(self.conn, {
            "api_key": "synthetic-unchecked", "test_on_save": False, "make_active": False,
        }, teacher_id=7)["key"]["id"]
        with self.assertRaises(ValueError):
            keys.set_active_agent_api_key(self.conn, untested)
        item, _ = keys.load_agent_api_key_secret(self.conn, untested)
        failed = keys.record_saved_agent_key_test(self.conn, untested, teacher_id=7,
            expected_configuration=configuration_generation(item), result=FAILED, activate=True)
        self.assertFalse(failed["activated"])
        self.assertEqual(old, keys.get_active_agent_api_key(self.conn)[0]["id"])
        denied = keys.create_agent_api_key(self.conn, {
            "api_key": "synthetic-new", "test_on_save": False, "make_active": True,
        }, teacher_id=7)
        self.assertFalse(denied["saved"])

    def test_delayed_probe_cannot_apply_to_changed_or_deleted_configuration(self):
        key_id = self.create()
        item, _ = keys.load_agent_api_key_secret(self.conn, key_id)
        expected = configuration_generation(item)
        self.conn.execute("UPDATE agent_runtime_api_keys SET model = 'changed' WHERE id = ?", (key_id,))
        with self.assertRaises(HTTPException) as caught:
            keys.record_saved_agent_key_test(self.conn, key_id, teacher_id=7,
                expected_configuration=expected, result=VALID, activate=True)
        self.assertEqual(409, caught.exception.status_code)
        keys.delete_agent_api_key(self.conn, key_id)
        with self.assertRaises(ValueError):
            keys.record_saved_agent_key_test(self.conn, key_id, teacher_id=7,
                expected_configuration=expected, result=VALID, activate=True)

    def test_service_rolls_back_as_one_transaction(self):
        key_id = self.create(active=True)
        self.assertGreater(key_id, 0)
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_api_keys").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_key_checks").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT revision FROM agent_model_configuration_lock").fetchone()[0])

    def test_main_base_url_uses_deployment_allowlist_and_separate_search_base(self):
        for url in ("http://localhost", "https://evil.invalid", "https://api.deepseek.com/anthropic/v1", "https://api.deepseek.com#x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                keys.prepare_agent_api_key({"api_key": "synthetic", "base_url": url})
        self.assertEqual("https://api.deepseek.com/v1", keys.prepare_agent_api_key({
            "api_key": "synthetic", "base_url": "https://api.deepseek.com/v1/"})["base_url"])

    def test_concurrent_activation_has_one_selected_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "keys.sqlite")
            with closing(connect(path)) as conn:
                install_keys(conn)
                ids = [self.create("first", conn=conn), self.create("second", conn=conn)]
                conn.commit()
            barrier = threading.Barrier(2)
            outcomes = []

            def activate(key_id):
                connection = connect(path)
                try:
                    barrier.wait(timeout=5)
                    keys.set_active_agent_api_key(connection, key_id)
                    connection.commit()
                    outcomes.append("ok")
                except Exception as exc:
                    outcomes.append(type(exc).__name__)
                finally:
                    connection.close()

            threads = [threading.Thread(target=activate, args=(key_id,)) for key_id in ids]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(["ok", "ok"], sorted(outcomes))
            with closing(connect(path)) as conn:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM agent_runtime_api_keys WHERE is_active = 1").fetchone()[0])
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE agent_runtime_api_keys SET is_active = 1")

    def test_schema_repairs_legacy_duplicate_selection_without_deleting_rows(self):
        self.create("one")
        self.create("two")
        self.conn.execute("DROP INDEX idx_agent_keys_one_selected_provider")
        self.conn.execute("UPDATE agent_runtime_api_keys SET is_active = 1")
        ensure_agent_model_schema(self.conn)
        ensure_agent_model_schema(self.conn)
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_api_keys").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_api_keys WHERE is_active = 1").fetchone()[0])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_key_checks").fetchone()[0])


class AgentKeyRouteTransactionTests(unittest.TestCase):
    """The DB connection must be released for every provider probe."""
    setUp = AgentKeyGatewayTests.setUp
    create = AgentKeyGatewayTests.create

    def route_context(self):
        active = [0]

        @contextmanager
        def connection():
            active[0] += 1
            try:
                yield self.conn
            except BaseException:
                self.conn.rollback()
                raise
            finally:
                active[0] -= 1

        return active, connection

    def test_create_probes_selected_model_without_holding_connection(self):
        from classroom_app.routers.manage_parts import system_config as routes
        active, connection = self.route_context()

        async def probe(**kwargs):
            self.assertEqual(0, active[0])
            self.assertEqual("selected-model", kwargs["model"])
            return VALID

        with patch.object(routes, "get_db_connection", connection), patch.object(routes, "_require_current_super_admin") as auth, \
             patch.object(routes, "_parse_json_request", AsyncMock(return_value={"api_key": "synthetic-route-key", "model": "selected-model", "test_on_save": False, "make_active": True})), \
             patch.object(keys, "test_agent_api_key_value", side_effect=probe):
            result = asyncio.run(routes.api_create_agent_key(object(), {"id": 7}))
        self.assertTrue(result["saved"])
        self.assertEqual(2, auth.call_count)

    def test_saved_probe_without_connection_and_admin_revocation_blocks_writeback(self):
        from classroom_app.routers.manage_parts import system_config as routes
        key_id = self.create(active=True)
        self.conn.commit()
        active, connection = self.route_context()

        async def probe(**kwargs):
            self.assertEqual(0, active[0])
            return VALID

        with patch.object(routes, "get_db_connection", connection), \
             patch.object(routes, "_require_current_super_admin", side_effect=[None, HTTPException(403, "revoked")]), \
             patch.object(keys, "test_agent_api_key_value", side_effect=probe):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(routes.api_activate_agent_key(key_id, {"id": 7}))
        self.assertEqual(403, caught.exception.status_code)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_runtime_key_checks").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
