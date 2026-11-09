"""Uncertain native writes can be reviewed without replay or invented success."""
import asyncio
import json
import uuid

from fastapi import HTTPException

from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services import wanderer_reconciliation as reconciliation
from classroom_app.services.agent_platform_route_capability import route_key
from classroom_app.services.wanderer_runtime_service import enqueue_wake_event, _claim
from classroom_app.time_utils import local_iso
from tests.test_wanderer_platform_execution import fixture_app
from tests.wanderer_test_support import WandererDbTestCase


class WandererReconciliationTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.op = str(uuid.uuid4())
        with self.connect() as conn:
            self.wid = conn.execute("SELECT id FROM wanderer_agents ORDER BY id LIMIT 1").fetchone()[0]
            self.teacher = conn.execute("SELECT id FROM teachers ORDER BY id LIMIT 1").fetchone()[0]
            conn.execute("UPDATE teachers SET is_super_admin=1,is_active=1 WHERE id=?", (self.teacher,))
            conn.execute("DELETE FROM wanderer_runtime_leases WHERE wanderer_id=?", (self.wid,))
            conn.execute("CREATE TABLE IF NOT EXISTS synthetic_owned_items(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,name TEXT,version INTEGER)")
            conn.execute("DELETE FROM synthetic_owned_items")
            event = enqueue_wake_event(conn, self.wid, "reconciliation_fixture", self.op)
            self.run = _claim(conn, event["event_id"])
            conn.commit()
        authority = {key: self.run[key] for key in ("turn_id", "life_epoch", "fencing_token", "authority_version")}
        command = {"id": "fixture", "capability": route_key("POST", "/api/owned-items-uncertain"),
                   "args": {"body": {"name": self.op}}}
        with self.assertRaises(RuntimeError):
            asyncio.run(platform.execute_platform_command(wanderer_id=self.wid, **authority, app=fixture_app(),
                conn_factory=self.connect, operation_id=self.op, command=command))
        with self.connect() as conn:
            conn.execute("INSERT INTO wanderer_commands(operation_id,turn_id,command_id,capability,args_hash,command_json,"
                         "state,receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'uncertain',?,?,?)",
                         (self.op, self.run["turn_id"], "fixture", command["capability"], "synthetic", json.dumps(command),
                          json.dumps({"status": "uncertain", "operation_id": self.op}), local_iso(), local_iso()))
            conn.execute("UPDATE wanderer_turns SET state='completed' WHERE turn_id=?", (self.run["turn_id"],))
            conn.commit()
        self.user = {"role": "teacher", "id": self.teacher}

    def view(self, conn):
        return reconciliation.get_command_reconciliation(conn, user=self.user, wanderer_id=self.wid, operation_id=self.op)["request"]

    def save(self, conn, revision, outcome="occurred", **extra):
        return reconciliation.reconcile_command(conn, user=self.user, wanderer_id=self.wid, operation_id=self.op,
            expected_revision=revision, outcome=outcome, reason="管理员核对原生记录后的合成声明", **extra)

    def test_occurred_settles_resources_keeps_observation_and_never_replays(self):
        with self.connect() as conn:
            before = self.view(conn)
            self.assertTrue(before["can_reconcile"])
            result = self.save(conn, before["revision"])
            self.assertEqual(result["request"]["state"], "resolved")
            self.assertFalse(result["request"]["reconciliation"]["verified_business"])
            self.assertEqual(result["request"]["observation"], before["observation"])
            self.assertTrue(self.save(conn, before["revision"])["replayed"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT state FROM wanderer_resource_reservations WHERE operation_id=?",
                (f"platform-action:{self.wid}:{self.op}:active_interactions",)).fetchone()[0], "consumed")
            with self.assertRaises(HTTPException):
                self.save(conn, result["request"]["revision"], outcome="not_occurred")
            conn.commit()

    def test_not_occurred_releases_only_reservation(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM synthetic_owned_items")  # Synthetic native absence evidence.
            self.save(conn, self.view(conn)["revision"], outcome="not_occurred")
            self.assertEqual(conn.execute("SELECT state FROM wanderer_resource_reservations WHERE operation_id=?",
                (f"platform-action:{self.wid}:{self.op}:active_interactions",)).fetchone()[0], "released")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 0)
            conn.commit()

    def test_live_execution_and_changed_observation_block_declaration(self):
        with self.connect() as conn:
            revision = self.view(conn)["revision"]
            conn.execute("UPDATE agent_platform_requests SET updated_at=updated_at+1 WHERE operation_id=?", (self.op,))
            with self.assertRaises(HTTPException) as conflict:
                self.save(conn, revision)
            self.assertEqual(conflict.exception.status_code, 409)
            conn.execute("UPDATE wanderer_turns SET state='executing' WHERE turn_id=?", (self.run["turn_id"],))
            view = self.view(conn)
            self.assertFalse(view["can_reconcile"])
            with self.assertRaises(HTTPException):
                self.save(conn, view["revision"])
            conn.execute("UPDATE wanderer_turns SET state='completed' WHERE turn_id=?", (self.run["turn_id"],))
            conn.execute("UPDATE agent_platform_requests SET host_execution_finished_at=NULL WHERE operation_id=?", (self.op,))
            self.assertFalse(self.view(conn)["can_reconcile"])

    def test_fresh_admin_role_and_status_required(self):
        with self.connect() as conn:
            revision = self.view(conn)["revision"]
            conn.execute("UPDATE teachers SET is_super_admin=0 WHERE id=?", (self.teacher,))
            with self.assertRaises(HTTPException) as denied:
                self.save(conn, revision)
            self.assertEqual(denied.exception.status_code, 403)
            self.user = {"role": "student", "id": self.teacher}
            with self.assertRaises(HTTPException):
                self.view(conn)
