import json
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from classroom_app.db import schema_agent_ext
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db import schema_scheduler
from classroom_app.services import agent_task_service
from fastapi import HTTPException

from classroom_app.services.agent_action_registry import (
    extract_proposed_actions,
    issue_action_confirmation_token,
    validate_action_params,
    verify_action_confirmation_token,
)
from classroom_app.services.agent_subscription_service import (
    DISPATCH_TASK_KIND,
    set_agent_subscription,
)
from classroom_app.services.agent_task_progress_service import (
    ERROR_CLASS_CONTENT,
    ERROR_CLASS_TRANSIENT,
    classify_runtime_error,
    diff_runtime_snapshot,
)


class AgentTaskImprovementTests(unittest.TestCase):
    def _open_agent_task_conn(self):
        schema_agent_ext._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_classroom_activity_schema(conn)
        with patch.object(schema_agent_ext, "get_configured_db_engine", return_value="sqlite"):
            schema_agent_ext.ensure_agent_task_extension_schema(conn, force=True)
        return conn

    def _insert_agent_task_row(self, conn, *, teacher_id=7, status=None, parent_task_id=None):
        now = agent_task_service.utcnow_iso()
        cursor = conn.execute(
            """
            INSERT INTO agent_tasks (
                task_uuid, teacher_id, teacher_name, task_type, title, public_summary,
                private_instruction, context_snapshot_json, status, priority, parent_task_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"workspace-clean-{uuid.uuid4().hex}",
                int(teacher_id),
                "Teacher",
                "general_teaching_task",
                "Agent task",
                "Agent task",
                "Prepare a short teaching plan",
                "{}",
                status or agent_task_service.TASK_STATUS_COMPLETED,
                0,
                parent_task_id,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def test_runtime_snapshot_diff_humanizes_new_steps_and_tools(self):
        events = diff_runtime_snapshot(
            {"status": "running", "timeline": ["已理解任务"], "tool_calls": []},
            {
                "status": "running",
                "timeline": ["已理解任务", {"message": "正在整理课堂材料"}],
                "tool_calls": [{"name": "query", "arguments": {"sql": "SELECT * FROM courses"}}],
            },
        )

        self.assertEqual(["runtime_step", "runtime_tool_call"], [event["event_type"] for event in events])
        self.assertIn("正在整理课堂材料", events[0]["message"])
        self.assertIn("正在查询平台数据库", events[1]["message"])

    def test_runtime_error_classifier_keeps_retry_budget_conservative(self):
        self.assertEqual(ERROR_CLASS_TRANSIENT, classify_runtime_error("503 service unavailable"))
        self.assertEqual(ERROR_CLASS_CONTENT, classify_runtime_error("JSON parse failed"))
        self.assertEqual("fatal", classify_runtime_error("invalid api key"))

    def test_extract_proposed_actions_drops_unknown_fields_and_invalid_actions(self):
        text = """
        已为你准备好草稿。
        ```json
        {
          "proposed_actions": [
            {
              "action": "create_blog_draft",
              "summary": "创建博客草稿",
              "params": {
                "title": "课堂复盘",
                "content_md": "正文",
                "tags": ["AI", "教学"],
                "extra": "should be dropped"
              }
            },
            {"action": "delete_everything", "params": {"title": "bad"}}
          ]
        }
        ```
        """

        proposals = extract_proposed_actions(text)

        self.assertEqual(1, len(proposals))
        self.assertEqual("create_blog_draft", proposals[0]["action"])
        self.assertNotIn("extra", proposals[0]["params"])
        self.assertEqual(["AI", "教学"], proposals[0]["params"]["tags"])

    def test_action_param_validation_requires_registered_schema(self):
        clean, errors = validate_action_params("create_assignment_draft", {"title": "Only title"})

        self.assertEqual({"title": "Only title"}, clean)
        self.assertTrue(any("class_offering_id" in error for error in errors))
        self.assertTrue(any("requirements_md" in error for error in errors))

    def test_action_confirmation_token_binds_scope_and_exact_params(self):
        params = {
            "title": "课堂复盘",
            "content_md": "正文",
            "tags": ["AI", "教学"],
        }
        issued = issue_action_confirmation_token(
            teacher_id=7,
            task_id=42,
            action_index=0,
            action="create_blog_draft",
            params=params,
        )

        confirmed = verify_action_confirmation_token(
            token=issued["confirmation_token"],
            teacher_id=7,
            task_id=42,
            action_index=0,
            action="create_blog_draft",
            params=params,
        )

        self.assertEqual(params, confirmed)
        with self.assertRaises(HTTPException):
            verify_action_confirmation_token(
                token=issued["confirmation_token"],
                teacher_id=7,
                task_id=42,
                action_index=0,
                action="create_blog_draft",
                params={**params, "title": "篡改标题"},
            )
        with self.assertRaises(HTTPException):
            verify_action_confirmation_token(
                token="",
                teacher_id=7,
                task_id=42,
                action_index=0,
                action="create_blog_draft",
                params=params,
            )

    def test_action_confirmation_rejects_unknown_schema_fields(self):
        with self.assertRaises(HTTPException):
            issue_action_confirmation_token(
                teacher_id=7,
                task_id=42,
                action_index=0,
                action="create_blog_draft",
                params={
                    "title": "课堂复盘",
                    "content_md": "正文",
                    "dangerous": "not allowed",
                },
            )

    def test_set_agent_subscription_persists_scheduler_row(self):
        schema_scheduler._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            with patch.object(schema_scheduler, "get_configured_db_engine", return_value="sqlite"):
                result = set_agent_subscription(
                    conn,
                    {"id": 7, "name": "Teacher"},
                    template_key="weekly_report",
                    enabled=True,
                    hour=8,
                )

            row = conn.execute("SELECT * FROM scheduled_tasks WHERE dedupe_key = ?", ("agent-sub:weekly_report:7",)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(DISPATCH_TASK_KIND, row["task_kind"])
            self.assertEqual("pending", row["status"])
            payload = json.loads(row["payload_json"])
            self.assertEqual({"teacher_id": 7, "template_key": "weekly_report", "hour": 8}, payload)
            self.assertTrue(result["subscriptions"][0]["enabled"])
        finally:
            conn.close()
            schema_scheduler._SCHEMA_READY = False

    def test_save_task_attachments_writes_isolated_workspace_files(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            agent_task_service,
            "AGENT_TASK_WORKSPACE_ROOT",
            Path(tmpdir),
        ):
            metadata = agent_task_service.save_task_attachments(
                42,
                [
                    {
                        "name": "roster.pdf",
                        "data": b"%PDF test bytes",
                        "text": "# Roster\nAlice",
                        "kind": "document",
                    }
                ],
            )

            stored = Path(tmpdir) / "tasks" / "42" / "attachments" / metadata[0]["stored_name"]
            extracted = stored.with_name(stored.name + ".extracted.txt")
            self.assertTrue(stored.exists())
            self.assertTrue(extracted.exists())
            self.assertIn("Alice", extracted.read_text(encoding="utf-8"))

    def test_delete_agent_task_removes_isolated_workspace(self):
        conn = self._open_agent_task_conn()
        try:
            with tempfile.TemporaryDirectory() as tmpdir, patch.object(
                agent_task_service,
                "AGENT_TASK_WORKSPACE_ROOT",
                Path(tmpdir),
            ):
                task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                workspace = Path(tmpdir) / "tasks" / str(task_id)
                attachments_dir = workspace / "attachments"
                attachments_dir.mkdir(parents=True)
                (attachments_dir / "roster.txt").write_text("Alice", encoding="utf-8")

                result = agent_task_service.delete_agent_task(conn, task_id, teacher_id=7)

                self.assertTrue(result["deleted"])
                self.assertTrue(result["workspace_deleted"])
                self.assertFalse(workspace.exists())
                self.assertIsNone(conn.execute("SELECT id FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_delete_agent_task_history_removes_finished_workspaces_only(self):
        conn = self._open_agent_task_conn()
        try:
            with tempfile.TemporaryDirectory() as tmpdir, patch.object(
                agent_task_service,
                "AGENT_TASK_WORKSPACE_ROOT",
                Path(tmpdir),
            ):
                completed_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                failed_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_FAILED)
                queued_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_QUEUED)
                other_teacher_id = self._insert_agent_task_row(
                    conn,
                    teacher_id=8,
                    status=agent_task_service.TASK_STATUS_COMPLETED,
                )
                workspaces = {}
                for task_id in (completed_id, failed_id, queued_id, other_teacher_id):
                    workspace = Path(tmpdir) / "tasks" / str(task_id)
                    workspace.mkdir(parents=True)
                    (workspace / "note.txt").write_text(str(task_id), encoding="utf-8")
                    workspaces[task_id] = workspace

                result = agent_task_service.delete_agent_task_history(conn, teacher_id=7)

                self.assertEqual(2, result["deleted_count"])
                self.assertCountEqual([completed_id, failed_id], result["task_ids"])
                self.assertCountEqual([completed_id, failed_id], result["deleted_workspace_ids"])
                self.assertFalse(workspaces[completed_id].exists())
                self.assertFalse(workspaces[failed_id].exists())
                self.assertTrue(workspaces[queued_id].exists())
                self.assertTrue(workspaces[other_teacher_id].exists())
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_delete_agent_task_removes_terminal_follow_up_chain(self):
        conn = self._open_agent_task_conn()
        try:
            with tempfile.TemporaryDirectory() as tmpdir, patch.object(
                agent_task_service,
                "AGENT_TASK_WORKSPACE_ROOT",
                Path(tmpdir),
            ):
                parent_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                child_id = self._insert_agent_task_row(
                    conn,
                    status=agent_task_service.TASK_STATUS_FAILED,
                    parent_task_id=parent_id,
                )
                grandchild_id = self._insert_agent_task_row(
                    conn,
                    status=agent_task_service.TASK_STATUS_COMPLETED,
                    parent_task_id=child_id,
                )
                sibling_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                for task_id in (parent_id, child_id, grandchild_id, sibling_id):
                    workspace = Path(tmpdir) / "tasks" / str(task_id)
                    workspace.mkdir(parents=True)
                    (workspace / "TASK.md").write_text("task", encoding="utf-8")

                result = agent_task_service.delete_agent_task(conn, parent_id, teacher_id=7)

                self.assertEqual(3, result["deleted_count"])
                self.assertCountEqual([parent_id, child_id, grandchild_id], result["task_ids"])
                for task_id in (parent_id, child_id, grandchild_id):
                    self.assertFalse((Path(tmpdir) / "tasks" / str(task_id)).exists())
                    self.assertIsNone(conn.execute("SELECT id FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())
                self.assertIsNotNone(conn.execute("SELECT id FROM agent_tasks WHERE id = ?", (sibling_id,)).fetchone())
                self.assertTrue((Path(tmpdir) / "tasks" / str(sibling_id)).exists())
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_delete_agent_task_rejects_chain_with_active_descendant(self):
        conn = self._open_agent_task_conn()
        try:
            parent_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
            child_id = self._insert_agent_task_row(
                conn,
                status=agent_task_service.TASK_STATUS_RUNNING,
                parent_task_id=parent_id,
            )

            with self.assertRaises(HTTPException) as raised:
                agent_task_service.delete_agent_task(conn, parent_id, teacher_id=7)

            self.assertEqual(400, raised.exception.status_code)
            self.assertIsNotNone(conn.execute("SELECT id FROM agent_tasks WHERE id = ?", (parent_id,)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT id FROM agent_tasks WHERE id = ?", (child_id,)).fetchone())
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_cleanup_stale_agent_task_attachments_keeps_current_workspaces(self):
        conn = self._open_agent_task_conn()
        try:
            with tempfile.TemporaryDirectory() as tmpdir, patch.object(
                agent_task_service,
                "AGENT_TASK_WORKSPACE_ROOT",
                Path(tmpdir),
            ):
                old_task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                recent_task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                queued_task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_QUEUED)
                now = datetime.now(timezone.utc)
                old_time = (now - timedelta(days=8)).isoformat()
                recent_time = (now - timedelta(days=2)).isoformat()
                conn.execute(
                    "UPDATE agent_tasks SET completed_at = ?, updated_at = ? WHERE id = ?",
                    (old_time, old_time, old_task_id),
                )
                conn.execute(
                    "UPDATE agent_tasks SET completed_at = ?, updated_at = ? WHERE id = ?",
                    (recent_time, recent_time, recent_task_id),
                )
                conn.execute(
                    "UPDATE agent_tasks SET created_at = ?, updated_at = ? WHERE id = ?",
                    (old_time, old_time, queued_task_id),
                )
                for task_id in (old_task_id, recent_task_id, queued_task_id):
                    workspace = Path(tmpdir) / "tasks" / str(task_id)
                    attachments = workspace / "attachments"
                    attachments.mkdir(parents=True)
                    (attachments / "source.txt").write_text("source", encoding="utf-8")
                    (workspace / "TASK.md").write_text("task readme", encoding="utf-8")

                result = agent_task_service.cleanup_stale_agent_task_attachments(conn, older_than_days=7)

                self.assertEqual(1, result["cleaned_count"])
                self.assertEqual([old_task_id], result["cleaned_task_ids"])
                self.assertFalse((Path(tmpdir) / "tasks" / str(old_task_id) / "attachments").exists())
                self.assertTrue((Path(tmpdir) / "tasks" / str(old_task_id) / "TASK.md").exists())
                self.assertTrue((Path(tmpdir) / "tasks" / str(recent_task_id) / "attachments").exists())
                self.assertTrue((Path(tmpdir) / "tasks" / str(queued_task_id) / "attachments").exists())
                detail = json.loads(
                    conn.execute(
                        "SELECT result_detail_json FROM agent_tasks WHERE id = ?",
                        (old_task_id,),
                    ).fetchone()["result_detail_json"]
                )
                self.assertTrue(detail["agent_attachments_removed"])
                self.assertIn("agent_attachments_cleaned_at", detail)

                second = agent_task_service.cleanup_stale_agent_task_attachments(conn, older_than_days=7)
                self.assertEqual(0, second["checked_count"])
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_active_task_supplement_is_evented_and_prompt_visible(self):
        schema_agent_ext._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            ensure_classroom_activity_schema(conn)
            with patch.object(schema_agent_ext, "get_configured_db_engine", return_value="sqlite"):
                schema_agent_ext.ensure_agent_task_extension_schema(conn)
            now = agent_task_service.utcnow_iso()
            cursor = conn.execute(
                """
                INSERT INTO agent_tasks (
                    task_uuid, teacher_id, teacher_name, task_type, title, public_summary,
                    private_instruction, context_snapshot_json, status, priority, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "supplement-test",
                    7,
                    "Teacher",
                    "general_teaching_task",
                    "教学任务",
                    "教学任务",
                    "整理本周教学情况",
                    json.dumps({"agent_options": {"deep_thinking": True}}, ensure_ascii=False),
                    agent_task_service.TASK_STATUS_QUEUED,
                    0,
                    now,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)

            result = agent_task_service.add_task_supplement(
                conn,
                {"id": 7, "name": "Teacher"},
                task_id,
                "请补充输出学生分层建议",
            )

            self.assertEqual(task_id, result["id"])
            context = json.loads(
                conn.execute(
                    "SELECT context_snapshot_json FROM agent_tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()["context_snapshot_json"]
            )
            supplements = context["agent_options"]["pending_supplements"]
            self.assertEqual("请补充输出学生分层建议", supplements[-1]["message"])
            event = conn.execute(
                "SELECT event_type, message, detail_json FROM agent_task_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            self.assertEqual("pending_supplement", event["event_type"])
            self.assertIn("一并读取", event["message"])

            row = dict(conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())
            prompt = agent_task_service.build_runtime_prompt(row, "/workspace/tasks/1")
            self.assertIn("请补充输出学生分层建议", prompt)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False


if __name__ == "__main__":
    unittest.main()
