"""散修 to-do list: capacity, scheduling, wakes and the daily reflection hook."""
import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services import wanderer_todo_service as todos
from classroom_app.services.wanderer_identity_service import load_wanderer
from classroom_app.services.wanderer_life_core import todo_capacity
from tests.wanderer_test_support import WandererDbTestCase


class StubCaller:
    def __init__(self, text='{"action":"skip","mood":"平静"}'):
        self.text = text
        self.calls = []

    async def __call__(self, endpoint, payload, **kwargs):
        self.calls.append((endpoint, payload, kwargs))
        return {"status": "success", "response_text": self.text}


class WandererTodoTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute('DELETE FROM wanderer_todos')
            conn.execute("DELETE FROM scheduled_tasks WHERE task_kind='wanderer_todo_wake'")
            conn.execute("DELETE FROM wanderer_capacity_allocations WHERE resource='todo_capacity'")
            conn.execute("DELETE FROM wanderer_resource_usage WHERE resource IN ('todo_capacity','cultivation_todo')")
            conn.execute("DELETE FROM wanderer_resource_reservations WHERE resource='cultivation_todo'")
            conn.execute("DELETE FROM wanderer_cultivation_awards WHERE kind='todo'")
            conn.commit()
        patcher = patch.dict(os.environ, {"WANDERER_ENABLED": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def wanderer_id(self, slug: str) -> int:
        with self.connect() as conn:
            return int(self.wanderer(conn, slug)["id"])

    def test_capacity_grows_three_per_tier(self):
        self.assertEqual(todo_capacity(0), 3)
        self.assertEqual(todo_capacity(1), 6)
        self.assertEqual(todo_capacity(5), 18)
        self.assertEqual(todo_capacity(10), 33)

    def test_create_with_countdown_arms_a_wake(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            result = todos.apply_todo_ops(conn, wanderer, [
                {"op": "create", "title": "回访阿泰", "detail": "问问他实验做完没", "due_in_minutes": 30},
            ])
            conn.commit()
            todo_id = result["applied"][0]["id"]
            row = conn.execute("SELECT * FROM wanderer_todos WHERE id = ?", (todo_id,)).fetchone()
            task = conn.execute("SELECT * FROM scheduled_tasks WHERE dedupe_key = ?",
                                (todos.todo_dedupe_key(todo_id),)).fetchone()
        self.assertEqual(result["errors"], [])
        self.assertEqual(row["schedule_kind"], "countdown")
        self.assertEqual(row["status"], "pending")
        self.assertIsNotNone(task)
        self.assertEqual(task["task_kind"], todos.TASK_KIND_WANDERER_TODO_WAKE)

    def test_capacity_limit_and_complete_flow(self):
        with self.connect() as conn:
            wanderer = {**self.wanderer(conn, "atai"), "tier": 0}
            conn.execute('UPDATE wanderer_agents SET tier=0 WHERE id=?',(wanderer['id'],))
            for index in range(3):
                todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": f"事{index}"}])
            fourth = todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": "第四件"}])
            first = conn.execute("SELECT id FROM wanderer_todos WHERE wanderer_id = ? ORDER BY id LIMIT 1",
                                 (wanderer["id"],)).fetchone()["id"]
            before = load_wanderer(conn, wanderer["id"])["cultivation_score"]
            done = todos.apply_todo_ops(conn, wanderer, [{"op": "complete", "id": int(first)}])
            again = todos.apply_todo_ops(conn, wanderer, [{"op": "complete", "id": int(first)}])
            after = load_wanderer(conn, wanderer["id"])["cultivation_score"]
            status = conn.execute("SELECT status FROM wanderer_todos WHERE id = ?", (int(first),)).fetchone()["status"]
        self.assertEqual(fourth["errors"][0]["error"], "capacity")
        self.assertEqual(done["completed"], 1)
        self.assertEqual(again["completed"], 0)  # completion is idempotent
        self.assertEqual(status, "done")
        self.assertAlmostEqual(after - before, todos.TODO_DONE_CULTIVATION, places=2)

    def test_update_reschedules_and_delete_cancels(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            created = todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": "写周记"}])
            todo_id = created["applied"][0]["id"]
            todos.apply_todo_ops(conn, wanderer, [{"op": "update", "id": todo_id, "due_in_minutes": 120}])
            updated = conn.execute("SELECT * FROM wanderer_todos WHERE id = ?", (todo_id,)).fetchone()
            task = conn.execute("SELECT * FROM scheduled_tasks WHERE dedupe_key = ?",
                                (todos.todo_dedupe_key(todo_id),)).fetchone()
            todos.apply_todo_ops(conn, wanderer, [{"op": "delete", "id": todo_id}])
            deleted = conn.execute("SELECT status FROM wanderer_todos WHERE id = ?", (todo_id,)).fetchone()
            cancelled = conn.execute("SELECT status FROM scheduled_tasks WHERE dedupe_key = ?",
                                     (todos.todo_dedupe_key(todo_id),)).fetchone()
        self.assertEqual(updated["schedule_kind"], "countdown")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(deleted["status"], "cancelled")
        self.assertEqual(cancelled["status"], "cancelled")

    def test_voice_block_explains_the_wake_reason(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            created = todos.apply_todo_ops(conn, wanderer, [
                {"op": "create", "title": "给阿泰回消息", "due_in_minutes": 10},
            ])
            todo_id = created["applied"][0]["id"]
            block = todos.todo_block(conn, wanderer, wake_todo_id=todo_id)
        self.assertIn("#{}".format(todo_id), block)
        self.assertIn("本次唤醒", block)
        self.assertIn("给阿泰回消息", block)

    def test_wake_runs_a_turn_and_clears_the_reminder(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            created = todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": "喝水"}])
            todo_id = created["applied"][0]["id"]
            past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE wanderer_todos SET remind_at = ? WHERE id = ?", (past, todo_id))
            conn.commit()
        caller = StubCaller()
        summary = asyncio.run(todos.handle_wanderer_todo_wake(
            {"payload": {"todo_id": todo_id, "wanderer_id": self.wanderer_id("atai")}},
            ai_caller=caller,
        ))
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM wanderer_todos WHERE id = ?", (todo_id,)).fetchone()
            actions = conn.execute("SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? "
                                   "AND action_kind = 'todo_wake'", (row["wanderer_id"],)).fetchone()["n"]
        self.assertIn("status=ok", summary)
        self.assertEqual(len(caller.calls), 1)
        self.assertIsNotNone(row["last_woke_at"])
        self.assertEqual(int(row["wake_count"]), 1)
        self.assertIsNone(row["remind_at"])
        self.assertEqual(actions, 1)

    def test_wake_can_postpone_without_losing_the_new_reminder(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lele")
            created = todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": "改作业",
                                                              "due_in_minutes": 30}])
            todo_id = created["applied"][0]["id"]
            conn.commit()
        postponed = json.dumps({
            "action": "reply", "parts": ["现在还没写完，晚一点再说。"], "mood": "平静",
            "todos": [{"op": "update", "id": todo_id, "due_in_minutes": 90}],
        }, ensure_ascii=False)
        asyncio.run(todos.handle_wanderer_todo_wake(
            {"payload": {"todo_id": todo_id, "wanderer_id": self.wanderer_id("lele")}},
            ai_caller=StubCaller(postponed),
        ))
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM wanderer_todos WHERE id = ?", (todo_id,)).fetchone()
            task = conn.execute("SELECT status, run_at FROM scheduled_tasks WHERE dedupe_key = ?",
                                (todos.todo_dedupe_key(todo_id),)).fetchone()
        self.assertIsNotNone(row["remind_at"])
        self.assertEqual(row["status"], "pending")
        self.assertEqual(task["status"], "pending")
        self.assertGreater(datetime.fromisoformat(task["run_at"]), datetime.now())

    def test_malformed_due_time_is_rejected_not_silently_indefinite(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lele")
            created = todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": "整理笔记"}])
            todo_id = created["applied"][0]["id"]
            bad_create = todos.apply_todo_ops(conn, wanderer, [
                {"op": "create", "title": "明天下午", "due_at": "明天下午"},
            ])
            bad_update = todos.apply_todo_ops(conn, wanderer, [
                {"op": "update", "id": todo_id, "due_at": "明天下午"},
            ])
            row = conn.execute("SELECT schedule_kind, remind_at FROM wanderer_todos WHERE id = ?",
                               (todo_id,)).fetchone()
        self.assertEqual(bad_create["errors"][0]["error"], "bad_due_at")
        self.assertEqual(bad_update["errors"][0]["error"], "bad_due_at")
        self.assertEqual(row["schedule_kind"], "none")
        self.assertIsNone(row["remind_at"])

    def test_wake_is_a_no_op_while_the_runtime_is_disabled(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lele")
            created = todos.apply_todo_ops(conn, wanderer, [{"op": "create", "title": "喝水"}])
            todo_id = created["applied"][0]["id"]
            conn.commit()
        caller = StubCaller()
        with patch.dict(os.environ, {"WANDERER_ENABLED": "false"}):
            summary = asyncio.run(todos.handle_wanderer_todo_wake(
                {"payload": {"todo_id": todo_id, "wanderer_id": self.wanderer_id("lele")}},
                ai_caller=caller,
            ))
        self.assertIn("status=skipped", summary)
        self.assertEqual(caller.calls, [])

    def test_restart_rearms_pending_wakes(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            created = todos.apply_todo_ops(conn, wanderer, [
                {"op": "create", "title": "改作业", "due_in_minutes": 60},
            ])
            todo_id = created["applied"][0]["id"]
            conn.execute("UPDATE scheduled_tasks SET status = 'cancelled' WHERE dedupe_key = ?",
                         (todos.todo_dedupe_key(todo_id),))
            conn.commit()
            rearmed = todos.ensure_todo_wakes(conn)
            task = conn.execute("SELECT status FROM scheduled_tasks WHERE dedupe_key = ?",
                                (todos.todo_dedupe_key(todo_id),)).fetchone()
        self.assertGreaterEqual(rearmed, 1)
        self.assertEqual(task["status"], "pending")


if __name__ == "__main__":
    unittest.main()
