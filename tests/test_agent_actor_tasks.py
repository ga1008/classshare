import asyncio
import hashlib
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException
from jinja2 import Environment, FileSystemLoader

from classroom_app.db import schema_agent_ext
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.services import agent_task_service as service
from classroom_app.routers import agent_tasks as router


class AgentActorTaskTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        ensure_classroom_activity_schema(self.conn)
        with patch.object(schema_agent_ext, "get_configured_db_engine", return_value="sqlite"):
            schema_agent_ext.ensure_agent_task_extension_schema(self.conn, force=True)
        self.teacher = {"role": "teacher", "id": 7, "name": "Teacher"}
        self.student = {"role": "student", "id": 7, "name": "Student"}
        self.context_patch = patch.object(service, "build_teacher_page_context", return_value={})
        self.context_patch.start()
        self.cleanup_patch = patch.object(service, "_remove_task_workspace", return_value=False)
        self.cleanup_patch.start()

    def tearDown(self):
        self.cleanup_patch.stop()
        self.context_patch.stop()
        self.conn.close()

    def create(self, user, *, session="session-one", **payload):
        return service.create_agent_task(self.conn, user, {"instruction": "请整理我的课程材料并形成学习计划", **payload}, source_session_id=session)

    def finish(self, task, status="completed"):
        self.conn.execute("UPDATE agent_tasks SET status=?, result_summary=?, result_detail_json=? WHERE id=?",
                          (status, "private result " + str(task["id"]), json.dumps({"artifacts": [{"path": "RESULT.md"}]}), task["id"]))
        self.conn.commit()

    def test_student_creation_is_distinct_preserves_type_and_has_private_session_source(self):
        with patch.object(service, "build_teacher_page_context", side_effect=AssertionError("student must not load teacher context")):
            task = self.create(self.student, task_type="blog_draft", page_context={"page": {"title": "我的课堂"}, "actor": {"role": "teacher"}, "follow_up": {"parent_thread_id": "stolen"}}, source_session_hash="forged")
        row = dict(self.conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task["id"],)).fetchone())
        self.assertIsNone(row["teacher_id"])
        self.assertEqual(("student", 7, "blog_draft", "deepseek-dsh"), (row["actor_role"], row["actor_id"], row["task_type"], row["runtime_provider"]))
        self.assertEqual(hashlib.sha256(b"session-one").hexdigest(), row["source_session_hash"])
        self.assertEqual("student:7", row["source_session_key"])
        context = json.loads(row["context_snapshot_json"])
        self.assertEqual({"role": "student", "id": 7}, context["actor"])
        self.assertNotIn("follow_up", context)
        serialized = json.dumps(task)
        self.assertNotIn("source_session", serialized)
        self.assertNotIn("session-one", serialized)
        self.assertNotIn(row["source_session_hash"], serialized)

    def test_same_numbered_teacher_cannot_read_student_details_events_or_mutate(self):
        task = self.create(self.student)
        public = service.get_agent_task(self.conn, task["id"], teacher_id=7)
        self.assertFalse(public["is_owner"])
        for key in ("private_instruction", "context_snapshot", "events", "attachments", "result_detail"):
            self.assertNotIn(key, public)
        operations = [
            lambda: service.list_task_events_after(self.conn, task["id"], teacher_id=7),
            lambda: service.cancel_agent_task(self.conn, task["id"], teacher_id=7),
            lambda: service.delete_agent_task(self.conn, task["id"], teacher_id=7),
            lambda: service.add_task_supplement(self.conn, self.teacher, task["id"], "补充内容"),
            lambda: service.create_follow_up_task(self.conn, self.teacher, task["id"], "补充内容"),
            lambda: service.create_retry_task(self.conn, self.teacher, task["id"]),
        ]
        for operation in operations:
            with self.assertRaises(HTTPException) as error:
                operation()
            self.assertEqual(403, error.exception.status_code)
        self.assertTrue(service.get_agent_task(self.conn, task["id"], teacher_id=7, actor_role="student")["is_owner"])

    def test_composer_and_queue_fairness_distinguish_same_numbered_users(self):
        service.set_agent_task_composer(self.conn, self.teacher, active=True)
        state = service.set_agent_task_composer(self.conn, self.student, active=True)
        self.assertEqual("teacher", state["composer"]["actor_role"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM agent_task_composers").fetchone()[0])
        service.set_agent_task_composer(self.conn, self.student, active=False)
        self.assertEqual("teacher", self.conn.execute("SELECT actor_role FROM agent_task_composers").fetchone()[0])
        first = self.create(self.teacher)
        second = self.create(self.teacher)
        student = self.create(self.student)
        self.conn.commit()
        self.assertEqual([first["id"], student["id"], second["id"]], service._ordered_queued_task_ids(self.conn))
        with patch("classroom_app.config.AGENT_TASK_GLOBAL_CONCURRENCY", 2):
            claim1 = service._claim_next_agent_task_sqlite(self.conn, worker_id="w1", now=service.utcnow_iso())
            claim2 = service._claim_next_agent_task_sqlite(self.conn, worker_id="w2", now=service.utcnow_iso())
        self.assertEqual(("teacher", "student"), (claim1["actor_role"], claim2["actor_role"]))

    def test_followup_retry_memory_and_delete_stay_with_actor_and_refresh_session(self):
        teacher = self.create(self.teacher)
        student = self.create(self.student)
        self.finish(teacher)
        self.finish(student)
        memory = service.build_task_memory_block(self.conn, teacher_id=7, actor_role="student", task_type="general_teaching_task")
        self.assertIn("private result " + str(student["id"]), memory)
        self.assertNotIn("private result " + str(teacher["id"]), memory)
        followup = service.create_follow_up_task(self.conn, self.student, student["id"], "继续补充一份清单", source_session_id="session-two")
        self.finish(followup, "failed")
        retry = service.create_retry_task(self.conn, self.student, followup["id"], source_session_id="session-three")
        self.assertEqual("student", retry["actor_role"])
        row = self.conn.execute("SELECT teacher_id, source_session_hash FROM agent_tasks WHERE id=?", (retry["id"],)).fetchone()
        self.assertIsNone(row["teacher_id"])
        self.assertEqual(hashlib.sha256(b"session-three").hexdigest(), row["source_session_hash"])
        with self.assertRaises(HTTPException):
            service.delete_agent_task(self.conn, student["id"], teacher_id=7, actor_role="student")
        service.cancel_agent_task(self.conn, retry["id"], teacher_id=7, actor_role="student")
        removed = service.delete_agent_task(self.conn, student["id"], teacher_id=7, actor_role="student")
        self.assertEqual({student["id"], followup["id"], retry["id"]}, set(removed["task_ids"]))
        self.assertEqual(teacher["id"], self.conn.execute("SELECT id FROM agent_tasks").fetchone()[0])

    def test_history_deletion_notifications_and_download_use_actor(self):
        teacher = self.create(self.teacher)
        student = self.create(self.student)
        self.finish(teacher)
        self.finish(student)
        with patch("classroom_app.services.message_center_service.create_agent_task_notification") as notification:
            service._notify_task_finished(self.conn, student["id"], "completed", "done", "")
        self.assertEqual("student", notification.call_args.kwargs["recipient_role"])
        self.assertEqual(7, notification.call_args.kwargs["recipient_user_pk"])
        with patch.object(router, "get_db_connection", return_value=self.conn), patch.object(router, "resolve_task_workspace_artifact") as resolve:
            with self.assertRaises(HTTPException) as error:
                router.api_download_agent_task_artifact(student["id"], "RESULT.md", self.teacher)
            self.assertEqual(403, error.exception.status_code)
            resolve.assert_not_called()
        deleted = service.delete_agent_task_history(self.conn, teacher_id=7)
        self.assertEqual([teacher["id"]], deleted["task_ids"])
        self.assertEqual(student["id"], self.conn.execute("SELECT id FROM agent_tasks").fetchone()[0])

    def test_student_cannot_use_same_numbered_teacher_subscription_or_task_action(self):
        with patch.object(router, "get_db_connection", side_effect=AssertionError("must not resolve teacher")):
            self.assertFalse(router.api_list_agent_subscriptions(self.student)["supported"])
            with self.assertRaises(HTTPException) as error:
                asyncio.run(router.api_set_agent_subscription(None, self.student))
            self.assertEqual(403, error.exception.status_code)
        teacher_task = self.create(self.teacher)
        self.finish(teacher_task)
        with patch.object(router, "get_db_connection", return_value=self.conn):
            for function in (router.api_preview_agent_task_action, router.api_execute_agent_task_action):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(function(teacher_task["id"], 0, None, self.student))
                self.assertEqual(403, error.exception.status_code)

    def test_public_creation_uses_authenticated_identity_and_private_session(self):
        payload = {"instruction": "请整理我的学习材料", "actor_role": "teacher", "actor_id": 999,
                   "runtime_provider": "legacy", "source_session_id": "forged-session",
                   "source_session_hash": "forged-hash", "extra_context": {"actor": {"id": 999}}}
        with patch.object(router, "get_db_connection", return_value=self.conn), \
             patch.object(router, "_parse_create_request", new=AsyncMock(return_value=(payload, []))), \
             patch.object(router, "AGENT_TASKS_ENABLED", True):
            result = asyncio.run(router.api_create_agent_task(None, BackgroundTasks(), {**self.student, "session_id": "trusted-session"}))
        row = dict(self.conn.execute("SELECT * FROM agent_tasks WHERE id=?", (result["task"]["id"],)).fetchone())
        self.assertEqual(("student", 7, None), (row["actor_role"], row["actor_id"], row["teacher_id"]))
        self.assertEqual(hashlib.sha256(b"trusted-session").hexdigest(), row["source_session_hash"])
        self.assertEqual("deepseek-dsh", row["runtime_provider"])
        self.assertNotIn("forged", json.dumps(result))

    def test_student_history_excludes_teacher_terminal_tasks_but_shares_public_live_queue(self):
        old_teacher = self.create(self.teacher)
        live_teacher = self.create(self.teacher)
        student = self.create(self.student)
        self.finish(old_teacher)
        self.finish(student)
        result = service.list_agent_tasks(self.conn, viewer_teacher_id=7, viewer_role="student")
        self.assertEqual({live_teacher["id"], student["id"]}, {task["id"] for task in result["tasks"]})
        self.assertEqual(1, result["counts"]["completed"])
        self.assertEqual(1, result["counts"]["queued"])

    def test_widget_enables_agent_for_students_without_teacher_subscriptions(self):
        env = Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "templates")))
        template = env.get_template("partials/ai_workspace_widget.html")
        for role in ("student", "teacher"):
            rendered = template.render(user_info={"role": role})
            self.assertIn("taskCenterEnabled: true", rendered)
            self.assertIn('id="ai-agent-history-drawer"', rendered)
            self.assertIn('data-ai-mode-select="agent"', rendered)
            self.assertEqual(role == "teacher", 'id="ai-agent-subscriptions-panel"' in rendered)


class AgentActorSchemaMigrationTests(unittest.TestCase):
    def legacy_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE teachers(id INTEGER PRIMARY KEY);
            INSERT INTO teachers VALUES(7);
            CREATE TABLE agent_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, teacher_id INTEGER NOT NULL,
                created_at TEXT, custom_extension TEXT, FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE);
            CREATE INDEX task_custom ON agent_tasks(custom_extension);
            CREATE TABLE agent_task_events(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES agent_tasks(id) ON DELETE CASCADE);
            CREATE TABLE task_authority(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES agent_tasks(id) ON DELETE CASCADE);
            CREATE TABLE agent_task_composers(teacher_id INTEGER PRIMARY KEY, teacher_name TEXT, page_label TEXT, updated_at TEXT,
                FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE);
            INSERT INTO agent_tasks(id,teacher_id,custom_extension) VALUES(42,7,'retain');
            INSERT INTO agent_task_events VALUES(1,42);
            INSERT INTO task_authority VALUES(1,42);
            INSERT INTO agent_task_composers VALUES(7,'teacher','page','now');
        """)
        return conn

    def test_legacy_sqlite_rebuild_keeps_children_indexes_data_and_is_repeatable(self):
        with closing(self.legacy_connection()) as conn:
            schema_agent_ext.prepare_sqlite_agent_actor_schema(conn)
            with patch.object(schema_agent_ext, "get_configured_db_engine", return_value="sqlite"):
                schema_agent_ext.ensure_agent_task_extension_schema(conn, force=True)
            conn.commit()
            schema_agent_ext.prepare_sqlite_agent_actor_schema(conn)
            self.assertEqual(1, conn.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM task_authority").fetchone()[0])
            task = dict(conn.execute("SELECT * FROM agent_tasks").fetchone())
            self.assertEqual((42, 7, "teacher", 7, "retain"), (task["id"], task["teacher_id"], task["actor_role"], task["actor_id"], task["custom_extension"]))
            self.assertTrue(conn.execute("SELECT name FROM sqlite_master WHERE name='task_custom'").fetchone())
            conn.execute("INSERT INTO agent_tasks(teacher_id,actor_role,actor_id) VALUES(NULL,'student',7)")
            conn.execute("INSERT INTO agent_task_composers(teacher_id,actor_role,actor_id,updated_at) VALUES(NULL,'student',7,'now')")
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM agent_task_composers").fetchone()[0])

    def test_legacy_migration_does_not_commit_callers_transaction(self):
        with closing(self.legacy_connection()) as conn:
            conn.execute("UPDATE agent_tasks SET custom_extension='uncommitted'")
            with self.assertRaisesRegex(RuntimeError, "outside a transaction"):
                schema_agent_ext.prepare_sqlite_agent_actor_schema(conn)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
            self.assertEqual("retain", conn.execute("SELECT custom_extension FROM agent_tasks").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
