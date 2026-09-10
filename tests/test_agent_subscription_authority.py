import contextlib
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db import schema_scheduler
from classroom_app.services import agent_subscription_service as subscriptions, agent_task_service
from tests import test_agent_task_improvements as fixture_module


class SubscriptionAuthorityTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.AgentTaskImprovementTests()
        self.conn = fixture._open_agent_task_conn()
        self.user = fixture._subscription_user(self.conn)
        schema_scheduler._SCHEMA_READY = False
        self.patches = [patch.object(schema_scheduler, "get_configured_db_engine", return_value="sqlite"),
                        patch.object(agent_task_service, "build_teacher_page_context", return_value={}),
                        patch("classroom_app.database.get_db_connection", self.connection)]
        for item in self.patches:
            item.start()

    @contextlib.contextmanager
    def connection(self):
        with self.conn:
            yield self.conn

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.conn.close()
        schema_scheduler._SCHEMA_READY = False

    def enable(self):
        return subscriptions.set_agent_subscription(self.conn, self.user, template_key="weekly_report", enabled=True)

    def dispatch(self):
        self.conn.execute("UPDATE scheduled_tasks SET status='running'")
        self.conn.commit()
        row = dict(self.conn.execute("SELECT * FROM scheduled_tasks").fetchone())
        return subscriptions.handle_agent_task_dispatch(row)

    def test_dispatch_uses_persistent_read_only_authority_after_login_session_ends(self):
        self.enable()
        self.conn.execute("DELETE FROM user_sessions")
        self.conn.commit()
        self.assertTrue(self.dispatch().startswith("queued agent task"))
        task = dict(self.conn.execute("SELECT * FROM agent_tasks").fetchone())
        authority = dict(self.conn.execute("SELECT * FROM agent_persistent_authorizations").fetchone())
        self.assertEqual(authority["id"], task["persistent_authorization_id"])
        self.assertIsNone(task["source_session_hash"])
        self.assertNotIn("platform:write", json.loads(authority["scopes_json"]))
        public = agent_task_service.get_agent_task(self.conn, task["id"], teacher_id=7)
        self.assertNotIn(authority["id"], json.dumps(public))

    def test_disable_cancels_queued_and_requests_running_stop_atomically(self):
        self.enable()
        self.dispatch()
        first = self.conn.execute("SELECT id FROM agent_tasks").fetchone()[0]
        self.conn.execute("UPDATE agent_tasks SET status='running' WHERE id=?", (first,))
        self.conn.commit()
        self.dispatch()
        subscriptions.set_agent_subscription(self.conn, self.user, template_key="weekly_report", enabled=False)
        rows = self.conn.execute("SELECT status,cancel_requested_at FROM agent_tasks ORDER BY id").fetchall()
        self.assertEqual(["running", "canceled"], [row[0] for row in rows])
        self.assertTrue(all(row[1] for row in rows))
        self.assertEqual("revoked", self.conn.execute("SELECT status FROM agent_persistent_authorizations").fetchone()[0])
        self.assertEqual("cancelled", self.conn.execute("SELECT status FROM scheduled_tasks").fetchone()[0])

    def test_legacy_or_changed_authority_cannot_dispatch_and_is_explained(self):
        self.enable()
        self.conn.execute("UPDATE scheduled_tasks SET payload_json=?", (json.dumps({"teacher_id": 7, "template_key": "weekly_report", "hour": 8}),))
        self.conn.commit()
        item = subscriptions.list_agent_subscriptions(self.conn, teacher_id=7)["subscriptions"][0]
        self.assertFalse(item["enabled"])
        self.assertTrue(item["authorization_required"])
        self.assertEqual("skipped: authorization required", self.dispatch())
        after = subscriptions.list_agent_subscriptions(self.conn, teacher_id=7)["subscriptions"][0]
        self.assertTrue(after["authorization_required"])
        self.assertFalse(after["enabled"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0])

    def test_failed_reauthorization_rolls_back_prior_revoke_and_schedule_change(self):
        self.enable()
        self.dispatch()
        with self.assertRaises(HTTPException):
            with self.conn:
                subscriptions.set_agent_subscription(self.conn, {**self.user, "session_id": ""}, template_key="weekly_report", enabled=True)
        self.assertEqual("active", self.conn.execute("SELECT status FROM agent_persistent_authorizations").fetchone()[0])
        self.assertEqual("queued", self.conn.execute("SELECT status FROM agent_tasks").fetchone()[0])

    def test_stale_dispatch_payload_cannot_run_replaced_subscription(self):
        self.enable()
        self.conn.execute("UPDATE scheduled_tasks SET status='running'")
        old = dict(self.conn.execute("SELECT * FROM scheduled_tasks").fetchone())
        self.conn.commit()
        self.enable()
        self.assertEqual("skipped: subscription changed", subscriptions.handle_agent_task_dispatch(old))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0])
