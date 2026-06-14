import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db import schema_agent_ext
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_learning_blog import ensure_learning_blog_signature_schema
from classroom_app.db.schema_materials_integrations import ensure_materials_integrations_schema
from classroom_app.db import schema_scheduler
from classroom_app.services import agent_task_service
from fastapi import HTTPException

from classroom_app.services.agent_action_registry import (
    execute_proposed_action,
    extract_proposed_actions,
    issue_action_confirmation_token,
    validate_action_params,
    verify_action_confirmation_token,
)
from classroom_app.services.agent_subscription_service import (
    DISPATCH_TASK_KIND,
    handle_agent_task_dispatch,
    list_agent_subscriptions,
    set_agent_subscription,
)
from classroom_app.services.agent_task_progress_service import (
    ERROR_CLASS_CONTENT,
    ERROR_CLASS_TRANSIENT,
    MAX_NEW_EVENTS_PER_DIFF,
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

    def _open_agent_action_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_foundation_schema(conn)
        ensure_classroom_activity_schema(conn)
        ensure_assignment_schema(conn)
        ensure_materials_integrations_schema(conn)
        ensure_learning_blog_signature_schema(conn)
        conn.execute(
            """
            INSERT INTO teachers (id, name, nickname, email, hashed_password, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (7, "Teacher", "T", "teacher@example.test", "hashed", 1),
        )
        conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (1, "三班", 7))
        conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (1, "综合英语", 7))
        conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
            (3, 1, 1, 7),
        )
        conn.commit()
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

    def test_runtime_snapshot_diff_suppresses_raw_protocol_noise(self):
        events = diff_runtime_snapshot(
            {"status": "running", "timeline": []},
            {
                "status": "running",
                "timeline": [
                    '#13261 item.delta: {"delta":"什么是","kind":"agent_reasoning"}',
                    '#13357 item.started: {"item":{"kind":"file_change","summary":"write_file {\\\"path\\\":\\\"/workspace/tasks/9/blog_draft.md\\\"}"}}',
                    '#13358 item.completed: {"item":{"kind":"agent_message","summary":"I have material combined with domain knowledge."}}',
                ],
            },
        )

        messages = [event["message"] for event in events]
        self.assertEqual(["正在写入任务产物：blog_draft.md"], messages)
        self.assertFalse(any("item.delta" in message or "agent_reasoning" in message for message in messages))

    def test_runtime_snapshot_diff_surfaces_output_deltas(self):
        new_output_events = diff_runtime_snapshot(
            {"status": "running", "text_outputs": []},
            {
                "status": "running",
                "text_outputs": [{"path": "outputs/plan.md", "text": "已生成第一版课堂讨论安排。"}],
            },
        )

        self.assertEqual(["runtime_output_delta"], [event["event_type"] for event in new_output_events])
        self.assertIn("已生成一段结果草稿", new_output_events[0]["message"])
        self.assertEqual("outputs/plan.md", new_output_events[0]["detail"]["source"])

        growing_output_events = diff_runtime_snapshot(
            {"status": "running", "output": "第一段：课堂导入。"},
            {"status": "running", "output": "第一段：课堂导入。\n第二段：分组讨论任务。"},
        )

        self.assertEqual(["runtime_output_delta"], [event["event_type"] for event in growing_output_events])
        self.assertIn("已继续生成结果草稿", growing_output_events[0]["message"])
        self.assertIn("分组讨论任务", growing_output_events[0]["message"])

    def test_runtime_snapshot_diff_event_cap_includes_summary_within_limit(self):
        events = diff_runtime_snapshot(
            {"status": "running", "timeline": [], "tool_calls": []},
            {
                "status": "running",
                "timeline": [f"步骤 {index}" for index in range(20)],
                "tool_calls": [{"name": "query"} for _ in range(4)],
            },
        )

        self.assertLessEqual(len(events), MAX_NEW_EVENTS_PER_DIFF)
        self.assertIn("另外", events[-1]["message"])

    def test_runtime_error_classifier_keeps_retry_budget_conservative(self):
        self.assertEqual(ERROR_CLASS_TRANSIENT, classify_runtime_error("503 service unavailable"))
        self.assertEqual(ERROR_CLASS_CONTENT, classify_runtime_error("JSON parse failed"))
        self.assertEqual("fatal", classify_runtime_error("invalid api key"))

    def test_runtime_result_summary_ignores_protocol_noise(self):
        summary = agent_task_service.runtime_result_summary(
            {
                "status": "completed",
                "message": '#13261 item.delta: {"delta":"什么是","kind":"agent_reasoning"}',
                "timeline": ['#13262 item.delta: {"delta":" Agent","kind":"agent_reasoning"}'],
            }
        )

        self.assertIn("没有返回明确的业务结论", summary)
        self.assertNotIn("item.delta", summary)

    def test_finished_agent_task_notification_links_back_to_task_card(self):
        conn = self._open_agent_task_conn()
        try:
            from classroom_app.db.schema_foundation import ensure_foundation_schema
            from classroom_app.services.email_notification_service import create_teacher_email_config

            ensure_foundation_schema(conn)
            conn.execute(
                """
                INSERT INTO teachers (id, name, email, hashed_password, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (7, "Teacher", "teacher@example.test", "hashed", 1),
            )
            create_teacher_email_config(
                conn,
                7,
                {
                    "provider": "custom",
                    "label": "Agent Mail",
                    "smtp_host": "smtp.example.test",
                    "smtp_port": 465,
                    "smtp_security": "ssl",
                    "smtp_username": "teacher@example.test",
                    "smtp_password": "secret",
                    "from_email": "teacher@example.test",
                    "from_name": "Teacher",
                    "enabled": True,
                    "is_default": True,
                    "per_minute_limit": 20,
                    "daily_limit": 200,
                },
            )
            task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_RUNNING)

            agent_task_service.finish_agent_task(
                conn,
                task_id,
                status=agent_task_service.TASK_STATUS_COMPLETED,
                result_summary="已生成教学周报，请查看提交率和低分预警。",
            )
            agent_task_service.finish_agent_task(
                conn,
                task_id,
                status=agent_task_service.TASK_STATUS_COMPLETED,
                result_summary="重复完成事件不应重复打扰教师。",
            )

            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, category, severity, title, body_preview, link_url, ref_type, ref_id,
                           metadata_json, email_status, email_job_id
                    FROM message_center_notifications
                    WHERE recipient_role = 'teacher' AND recipient_user_pk = ?
                    ORDER BY id
                    """,
                    (7,),
                ).fetchall()
            ]

            self.assertEqual(1, len(rows))
            self.assertEqual("agent_task", rows[0]["category"])
            self.assertEqual("important", rows[0]["severity"])
            self.assertIn("Agent 任务完成", rows[0]["title"])
            self.assertEqual(f"/?agent_task={task_id}", rows[0]["link_url"])
            self.assertEqual("agent_task", rows[0]["ref_type"])
            self.assertEqual(f"agent-task:{task_id}:completed", rows[0]["ref_id"])
            self.assertEqual("queued", rows[0]["email_status"])
            self.assertIsNotNone(rows[0]["email_job_id"])
            self.assertEqual(
                {"agent_task_id": task_id, "status": agent_task_service.TASK_STATUS_COMPLETED},
                json.loads(rows[0]["metadata_json"]),
            )
            email_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT notification_id, category, severity, recipient_email, status
                    FROM email_outbox
                    ORDER BY id
                    """
                ).fetchall()
            ]
            self.assertEqual(
                [
                    {
                        "notification_id": rows[0]["id"],
                        "category": "agent_task",
                        "severity": "important",
                        "recipient_email": "teacher@example.test",
                        "status": "queued",
                    }
                ],
                email_rows,
            )
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_terminal_agent_events_refresh_message_center_bell(self):
        workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
        bell_js = Path("static/js/message_center_bell.js").read_text(encoding="utf-8")
        terminal_handler = workspace_js[
            workspace_js.index("function handleTaskEventPayload"):
            workspace_js.index("function startTaskEventStream")
        ]

        self.assertIn("function refreshAgentTaskFinishNotification", workspace_js)
        self.assertIn("window.refreshMessageCenterBell", workspace_js)
        self.assertIn("message-center:refresh-requested", workspace_js)
        self.assertIn("refreshAgentTaskFinishNotification(id)", terminal_handler)
        self.assertIn("message-center:refresh-requested", bell_js)
        self.assertIn("refreshBell({ allowPopup", bell_js)

    def test_agent_queue_list_surfaces_wait_estimate(self):
        workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
        render_list_block = workspace_js[
            workspace_js.index("function renderTaskList"):
            workspace_js.index("function formatAgentSubscriptionHour")
        ]

        self.assertIn("queuePieces", render_list_block)
        self.assertIn("estimated_wait_label", render_list_block)
        self.assertIn("队列第", render_list_block)
        self.assertIn("queuePieces.join(' · ')", render_list_block)

    def test_agent_queue_deploy_shape_defaults_to_two_parallel_workers(self):
        compose_yml = Path("docker-compose.yml").read_text(encoding="utf-8")
        docker_env_example = Path("docker.env.example").read_text(encoding="utf-8")
        deploy_script = Path("deployment/deploy_remote.ps1").read_text(encoding="utf-8")

        self.assertIn('"${DEEPSEEK_TUI_WORKERS:-2}"', compose_yml)
        self.assertIn("AGENT_TASK_GLOBAL_CONCURRENCY=2", docker_env_example)
        self.assertIn("AGENT_TASK_WORKER_CONCURRENCY=2", docker_env_example)
        self.assertIn("DEEPSEEK_TUI_WORKERS=2", docker_env_example)
        self.assertIn("ensure_env_value AGENT_TASK_GLOBAL_CONCURRENCY 2", deploy_script)
        self.assertIn("ensure_env_value AGENT_TASK_WORKER_CONCURRENCY 2", deploy_script)
        self.assertIn("ensure_env_value DEEPSEEK_TUI_WORKERS 2", deploy_script)

    def test_agent_attachment_validator_rejects_unsupported_types_before_submit(self):
        workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
        validator_block = workspace_js[
            workspace_js.index("const AGENT_ATTACHMENT_ALLOWED_EXTENSIONS"):
            workspace_js.index("function agentAttachmentPreviews")
        ]

        self.assertIn("AGENT_ATTACHMENT_ALLOWED_EXTENSIONS", validator_block)
        self.assertIn("agentAttachmentExtension(file.name)", validator_block)
        self.assertIn("类型暂不支持", validator_block)
        self.assertIn(".docx", validator_block)
        self.assertIn(".xlsx", validator_block)
        self.assertIn(".png", validator_block)

    def test_record_agent_auto_retry_enforces_hourly_budget(self):
        conn = self._open_agent_task_conn()
        try:
            first_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_RUNNING)
            second_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_RUNNING)
            conn.commit()
            now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

            with patch.object(agent_task_service, "get_configured_db_engine", return_value="sqlite"):
                first = agent_task_service.record_agent_auto_retry(
                    conn,
                    first_id,
                    error_text="503 service unavailable",
                    error_class=ERROR_CLASS_TRANSIENT,
                    hourly_limit=1,
                    now=now,
                )
                second = agent_task_service.record_agent_auto_retry(
                    conn,
                    second_id,
                    error_text="503 service unavailable",
                    error_class=ERROR_CLASS_TRANSIENT,
                    hourly_limit=1,
                    now=now + timedelta(minutes=5),
                )

            self.assertTrue(first["allowed"])
            self.assertFalse(second["allowed"])
            first_retry_count = conn.execute(
                "SELECT retry_count FROM agent_tasks WHERE id = ?",
                (first_id,),
            ).fetchone()["retry_count"]
            second_retry_count = conn.execute(
                "SELECT retry_count FROM agent_tasks WHERE id = ?",
                (second_id,),
            ).fetchone()["retry_count"]
            self.assertEqual(1, first_retry_count)
            self.assertEqual(0, second_retry_count)
            events = conn.execute(
                "SELECT event_type, message, detail_json FROM agent_task_events ORDER BY id"
            ).fetchall()
            self.assertEqual(["auto_retry", "auto_retry_budget_exhausted"], [row["event_type"] for row in events])
            self.assertIn("自动重试", events[0]["message"])
            self.assertIn("无需教师操作", events[0]["message"])
            self.assertIn("自动重试次数已达上限", events[1]["message"])
            self.assertIn("重试按钮", events[1]["message"])
            exhausted_detail = json.loads(events[1]["detail_json"])
            self.assertEqual(1, exhausted_detail["retry_count_last_hour"])
            self.assertEqual(1, exhausted_detail["hourly_limit"])

            from agent_task_worker import _retry_budget_error_message

            failed_message = _retry_budget_error_message("503 service unavailable", 1)
            self.assertIn("自动重试次数已达上限", failed_message)
            self.assertIn("任务卡片上的重试按钮", failed_message)
            self.assertIn("503 service unavailable", failed_message)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_task_memory_orders_completed_tasks_by_fallback_timestamp(self):
        conn = self._open_agent_task_conn()
        try:
            older_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
            newer_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
            conn.execute(
                """
                UPDATE agent_tasks
                SET title = ?, result_summary = ?, completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("Older plan", "older summary", "2026-01-01T09:00:00+00:00", older_id),
            )
            conn.execute(
                """
                UPDATE agent_tasks
                SET title = ?, result_summary = ?, completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("Newer plan", "newer summary", "2026-01-02T09:00:00+00:00", newer_id),
            )

            block = agent_task_service.build_task_memory_block(
                conn,
                teacher_id=7,
                task_type="general_teaching_task",
            )

            self.assertIn("Newer plan", block)
            self.assertIn("Older plan", block)
            self.assertLess(block.index("Newer plan"), block.index("Older plan"))
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_task_memory_omits_no_history_tasks(self):
        conn = self._open_agent_task_conn()
        try:
            sensitive_ids = [
                self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
                for _ in range(12)
            ]
            normal_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
            for index, sensitive_id in enumerate(sensitive_ids):
                conn.execute(
                    """
                    UPDATE agent_tasks
                    SET title = ?, result_summary = ?, context_snapshot_json = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        f"Sensitive plan {index}",
                        f"sensitive summary {index}",
                        json.dumps({"agent_options": {"no_history": True}}, ensure_ascii=False),
                        f"2026-01-03T{index:02d}:00:00+00:00",
                        sensitive_id,
                    ),
                )
            conn.execute(
                """
                UPDATE agent_tasks
                SET title = ?, result_summary = ?, context_snapshot_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    "Reusable plan",
                    "reusable summary",
                    json.dumps({"agent_options": {"no_history": False}}, ensure_ascii=False),
                    "2026-01-02T09:00:00+00:00",
                    normal_id,
                ),
            )

            block = agent_task_service.build_task_memory_block(
                conn,
                teacher_id=7,
                task_type="general_teaching_task",
            )

            self.assertIn("Reusable plan", block)
            self.assertNotIn("Sensitive plan 0", block)
            self.assertNotIn("Sensitive plan 11", block)
            self.assertNotIn("sensitive summary", block)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_create_agent_task_persists_no_history_option(self):
        conn = self._open_agent_task_conn()
        try:
            task = agent_task_service.create_agent_task(
                conn,
                {"id": 7, "name": "Teacher"},
                {
                    "task_type": "general_teaching_task",
                    "instruction": "整理一份本周教学任务复盘，列出后续行动。",
                    "page_context": {},
                    "no_history": True,
                },
            )

            context = json.loads(
                conn.execute(
                    "SELECT context_snapshot_json FROM agent_tasks WHERE id = ?",
                    (int(task["id"]),),
                ).fetchone()["context_snapshot_json"]
            )
            self.assertTrue(context["agent_options"]["no_history"])
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_agent_workflow_starters_enter_verified_context_and_prompt(self):
        catalog = agent_task_service.agent_workflow_catalog()
        lesson = next(item for item in catalog if item["key"] == "lesson_document_generation")
        self.assertEqual("lesson_document", lesson["task_type"])
        self.assertIn("学习文档生成与绑定", lesson["starter_prompt"])
        self.assertIn("需要我确认的动作", lesson["starter_prompt"])

        conn = self._open_agent_task_conn()
        try:
            context = agent_task_service.build_teacher_page_context(
                conn,
                7,
                {
                    "agentWorkflowKey": "lesson_document_generation",
                    "agentWorkflow": {"key": "unknown", "name": "前端不可直接指定"},
                },
                task_type="lesson_document",
                instruction="按当前课时生成下一份学习文档。",
            )
            selected = context["server_context"]["selected_agent_workflow"]
            self.assertEqual("lesson_document_generation", selected["key"])
            self.assertEqual("学习文档生成与绑定", selected["name"])

            ignored = agent_task_service.build_teacher_page_context(
                conn,
                7,
                {"agentWorkflowKey": "not_registered"},
                task_type="lesson_document",
                instruction="按当前课时生成下一份学习文档。",
            )
            self.assertNotIn("selected_agent_workflow", ignored["server_context"])

            prompt = agent_task_service.build_runtime_prompt(
                {
                    "id": 99,
                    "teacher_id": 7,
                    "task_type": "lesson_document",
                    "private_instruction": "按当前课时生成下一份学习文档。",
                    "context_snapshot_json": json.dumps(context, ensure_ascii=False),
                },
                "/workspace/tasks/99",
            )
            self.assertIn("教师提交任务前选择了以下 Agent 工作流", prompt)
            self.assertIn("学习文档生成与绑定", prompt)
            self.assertIn("只写当前教师材料库", prompt)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_agent_starter_panel_is_wired_in_frontend(self):
        workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
        self.assertIn("function renderAgentStarters", workspace_js)
        self.assertIn("data-agent-starter", workspace_js)
        self.assertIn("selectedAgentWorkflowKey", workspace_js)
        self.assertIn("agentWorkflowKey", workspace_js)

        template = Path("templates/partials/ai_workspace_widget.html").read_text(encoding="utf-8")
        self.assertIn('id="ai-agent-starters"', template)

        ui_css = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
        self.assertIn(".ai-agent-starters", ui_css)
        self.assertIn(".ai-agent-starter", ui_css)

    def test_follow_up_task_inherits_no_history_option(self):
        conn = self._open_agent_task_conn()
        try:
            parent_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
            conn.execute(
                """
                UPDATE agent_tasks
                SET result_summary = ?, context_snapshot_json = ?
                WHERE id = ?
                """,
                (
                    "已有任务结论",
                    json.dumps(
                        {
                            "agent_options": {
                                "deep_thinking": True,
                                "no_history": True,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    parent_id,
                ),
            )

            child = agent_task_service.create_follow_up_task(
                conn,
                {"id": 7, "name": "Teacher"},
                parent_id,
                "继续完善这份教学任务，补充可执行步骤。",
            )

            context = json.loads(
                conn.execute(
                    "SELECT context_snapshot_json FROM agent_tasks WHERE id = ?",
                    (int(child["id"]),),
                ).fetchone()["context_snapshot_json"]
            )
            self.assertTrue(context["agent_options"]["no_history"])
            self.assertTrue(context["agent_options"]["deep_thinking"])
            self.assertEqual(parent_id, context["follow_up"]["parent_task_id"])
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_follow_up_runtime_payload_uses_parent_thread_id(self):
        from agent_task_worker import _build_runtime_task_payload

        task = {
            "id": 42,
            "context_snapshot_json": json.dumps(
                {
                    "agent_options": {"deep_thinking": False},
                    "follow_up": {"parent_thread_id": "thread-parent-123"},
                },
                ensure_ascii=False,
            ),
        }

        payload = _build_runtime_task_payload(task, "/workspace/tasks/42", "prompt")

        self.assertEqual("thread-parent-123", payload["thread_id"])
        self.assertEqual("prompt", payload["prompt"])
        self.assertEqual("/workspace/tasks/42", payload["workspace"])
        self.assertEqual("agent", payload["mode"])

    def test_runtime_payload_omits_blank_follow_up_thread_id(self):
        from agent_task_worker import _build_runtime_task_payload

        task = {
            "id": 43,
            "context_snapshot_json": json.dumps(
                {"follow_up": {"parent_thread_id": "   "}},
                ensure_ascii=False,
            ),
        }

        payload = _build_runtime_task_payload(task, "/workspace/tasks/43", "prompt")

        self.assertNotIn("thread_id", payload)

    def test_retry_task_inherits_no_history_option(self):
        conn = self._open_agent_task_conn()
        try:
            parent_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_FAILED)
            conn.execute(
                """
                UPDATE agent_tasks
                SET context_snapshot_json = ?
                WHERE id = ?
                """,
                (
                    json.dumps({"agent_options": {"no_history": True}}, ensure_ascii=False),
                    parent_id,
                ),
            )

            child = agent_task_service.create_retry_task(
                conn,
                {"id": 7, "name": "Teacher"},
                parent_id,
            )

            context = json.loads(
                conn.execute(
                    "SELECT context_snapshot_json FROM agent_tasks WHERE id = ?",
                    (int(child["id"]),),
                ).fetchone()["context_snapshot_json"]
            )
            self.assertTrue(context["agent_options"]["no_history"])
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

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
            {
              "action": "publish_blog_post",
              "summary": "发布博客",
              "params": {
                "title": "课堂观察",
                "content_md": "结合最新资料后的正文",
                "tags": ["教学"],
                "visibility": "public"
              }
            },
            {
              "action": "create_blog_comment",
              "summary": "发表评论",
              "params": {
                "post_id": 9,
                "content_md": "这篇总结很有启发。"
              }
            },
            {"action": "delete_everything", "params": {"title": "bad"}}
          ]
        }
        ```
        """

        proposals = extract_proposed_actions(text)

        self.assertEqual(3, len(proposals))
        self.assertEqual("create_blog_draft", proposals[0]["action"])
        self.assertNotIn("extra", proposals[0]["params"])
        self.assertEqual(["AI", "教学"], proposals[0]["params"]["tags"])
        self.assertEqual("publish_blog_post", proposals[1]["action"])
        self.assertEqual("public", proposals[1]["params"]["visibility"])
        self.assertIn("发布博客", proposals[1]["confirmation_note"])
        self.assertEqual("create_blog_comment", proposals[2]["action"])
        self.assertEqual(9, proposals[2]["params"]["post_id"])

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

    def test_registered_agent_actions_execute_safe_drafts_and_manual_notification_link(self):
        conn = self._open_agent_action_conn()
        try:
            assignment = execute_proposed_action(
                conn,
                teacher_id=7,
                action="create_assignment_draft",
                params={
                    "class_offering_id": 3,
                    "title": "Unit 1 reflection",
                    "requirements_md": "Write a short reflection.",
                    "rubric_md": "| 维度 | 分值 |",
                },
            )
            self.assertEqual("/assignment/1", assignment["url"])
            assignment_row = conn.execute(
                "SELECT status, title FROM assignments WHERE id = ?",
                (assignment["ref_id"],),
            ).fetchone()
            self.assertEqual("new", assignment_row["status"])
            self.assertEqual("Unit 1 reflection", assignment_row["title"])

            material = execute_proposed_action(
                conn,
                teacher_id=7,
                action="save_material_draft",
                params={
                    "title": "课堂导学",
                    "content_md": "# 导学\n请完成课前预习。",
                },
            )
            self.assertEqual("/materials/view/2", material["url"])
            material_row = conn.execute(
                "SELECT name, material_path, file_hash FROM course_materials WHERE id = ?",
                (material["ref_id"],),
            ).fetchone()
            self.assertEqual("课堂导学.md", material_row["name"])
            self.assertIn("Agent 草稿", material_row["material_path"])
            self.assertTrue(material_row["file_hash"])

            blog = execute_proposed_action(
                conn,
                teacher_id=7,
                action="create_blog_draft",
                params={
                    "title": "课堂复盘",
                    "content_md": "今天的课堂讨论很充分。",
                    "tags": ["Agent", "教学"],
                },
            )
            self.assertEqual("/blog?tab=mine", blog["url"])
            blog_row = conn.execute(
                "SELECT status, title, tags_json FROM blog_posts WHERE id = ?",
                (blog["ref_id"],),
            ).fetchone()
            self.assertEqual("draft", blog_row["status"])
            self.assertEqual("课堂复盘", blog_row["title"])
            self.assertEqual(["Agent", "教学"], json.loads(blog_row["tags_json"]))

            published = execute_proposed_action(
                conn,
                teacher_id=7,
                action="publish_blog_post",
                params={
                    "title": "课堂观察",
                    "content_md": "今天结合最新资料更新了课堂观察。",
                    "tags": ["Agent", "观察"],
                    "visibility": "public",
                },
            )
            self.assertEqual(f"/blog?post={published['ref_id']}", published["url"])
            published_row = conn.execute(
                "SELECT status, title, tags_json FROM blog_posts WHERE id = ?",
                (published["ref_id"],),
            ).fetchone()
            self.assertEqual("published", published_row["status"])
            self.assertEqual("课堂观察", published_row["title"])
            self.assertEqual(["Agent", "观察"], json.loads(published_row["tags_json"]))

            comment = execute_proposed_action(
                conn,
                teacher_id=7,
                action="create_blog_comment",
                params={
                    "post_id": published["ref_id"],
                    "content_md": "这篇课堂观察可以继续沉淀为复盘素材。",
                },
            )
            self.assertEqual(f"/blog?post={published['ref_id']}", comment["url"])
            comment_row = conn.execute(
                "SELECT post_id, author_identity, content_md FROM blog_comments WHERE id = ?",
                (comment["ref_id"],),
            ).fetchone()
            self.assertEqual(published["ref_id"], comment_row["post_id"])
            self.assertEqual("teacher:7", comment_row["author_identity"])
            self.assertIn("课堂观察", comment_row["content_md"])
            comment_count = conn.execute(
                "SELECT comment_count FROM blog_posts WHERE id = ?",
                (published["ref_id"],),
            ).fetchone()["comment_count"]
            self.assertEqual(1, comment_count)

            manual = execute_proposed_action(
                conn,
                teacher_id=7,
                action="send_student_notification",
                params={
                    "title": "提醒",
                    "content_md": "请及时查看本周任务。",
                    "student_names": ["Alice"],
                },
            )
            self.assertTrue(manual["manual"])
            self.assertEqual("/messages", manual["url"])
            self.assertIn("本周任务", manual["copy_text"])
        finally:
            conn.close()

    def test_assignment_action_rejects_other_teacher_classroom(self):
        conn = self._open_agent_action_conn()
        try:
            with self.assertRaises(HTTPException) as ctx:
                execute_proposed_action(
                    conn,
                    teacher_id=8,
                    action="create_assignment_draft",
                    params={
                        "class_offering_id": 3,
                        "title": "Not mine",
                        "requirements_md": "Should be rejected.",
                    },
                )
            self.assertEqual(403, ctx.exception.status_code)
        finally:
            conn.close()

    def test_manual_agent_action_uses_server_confirmation_and_audit_path(self):
        workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
        manual_block_start = workspace_js.index("async function openManualAgentAction")
        manual_block_end = workspace_js.index("async function executeAgentAction")
        manual_block = workspace_js[manual_block_start:manual_block_end]

        self.assertIn("/preview", manual_block)
        self.assertIn("/execute", manual_block)
        self.assertIn("confirmation_token", manual_block)
        self.assertIn("renderTaskDetail", manual_block)

    def test_agent_task_context_uses_bottom_composer_and_hides_recommendations(self):
        workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
        starters_block = workspace_js[
            workspace_js.index("function renderAgentStarters"):
            workspace_js.index("function applyAgentStarter")
        ]
        followup_block = workspace_js[
            workspace_js.index("function renderFollowUpBox"):
            workspace_js.index("function renderTaskList")
        ]
        submit_block = workspace_js[
            workspace_js.index("async function submitActiveAgentSupplementFromComposer"):
            workspace_js.index("function bindTaskCenter")
        ]

        self.assertIn("function currentActiveOwnAgentTask", workspace_js)
        self.assertIn("function currentAgentComposerTargetTask", workspace_js)
        self.assertIn("const hasTaskContext = hasCurrentAgentTaskContext()", starters_block)
        self.assertIn("if (!agentMode || hasTaskContext || hasInput || !starters.length)", starters_block)
        self.assertIn("!task.is_terminal", followup_block)
        self.assertNotIn("task.is_active", followup_block)
        self.assertNotIn("data-agent-followup-input", workspace_js)
        self.assertNotIn("data-agent-followup", workspace_js)
        self.assertIn("/follow-up", submit_block)
        self.assertIn("补充到当前 Agent 任务", workspace_js)
        self.assertIn("追问当前 Agent 结果", workspace_js)
        self.assertIn("补充说明暂不支持附件", workspace_js)
        self.assertIn("function renderTaskEventsPanel", workspace_js)
        self.assertIn("function userFacingTaskEvent", workspace_js)

    def test_manual_notification_action_execute_route_audits_without_sending(self):
        conn = self._open_agent_task_conn()
        try:
            from classroom_app.routers.agent_tasks import (
                api_execute_agent_task_action,
                api_preview_agent_task_action,
            )

            class _Request:
                def __init__(self, payload):
                    self.payload = payload

                async def json(self):
                    return self.payload

            task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_COMPLETED)
            detail = {
                "proposed_actions": [
                    {
                        "action": "send_student_notification",
                        "label": "去消息中心发送通知",
                        "summary": "提醒学生查看任务",
                        "execution_mode": "manual_link",
                        "risk": "medium",
                        "params": {
                            "title": "提醒",
                            "content_md": "请查看本周任务。",
                            "student_names": ["Alice"],
                        },
                        "executed": None,
                    }
                ]
            }
            conn.execute(
                "UPDATE agent_tasks SET result_detail_json = ? WHERE id = ?",
                (json.dumps(detail, ensure_ascii=False), task_id),
            )
            conn.commit()

            with patch("classroom_app.routers.agent_tasks.get_db_connection", return_value=conn):
                preview = asyncio.run(api_preview_agent_task_action(task_id, 0, _Request({}), {"id": 7}))
                executed = asyncio.run(
                    api_execute_agent_task_action(
                        task_id,
                        0,
                        _Request({"confirmation_token": preview["confirmation_token"]}),
                        {"id": 7},
                    )
                )

            self.assertTrue(executed["result"]["manual"])
            self.assertEqual("/messages", executed["result"]["url"])
            self.assertIn("本周任务", executed["result"]["copy_text"])
            event = conn.execute(
                "SELECT event_type, message, detail_json FROM agent_task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            self.assertEqual("action_executed", event["event_type"])
            self.assertIn("去消息中心发送通知", event["message"])
            event_detail = json.loads(event["detail_json"])
            self.assertEqual("send_student_notification", event_detail["action"])
            updated_detail = json.loads(
                conn.execute("SELECT result_detail_json FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()[
                    "result_detail_json"
                ]
            )
            self.assertEqual("/messages", updated_detail["proposed_actions"][0]["executed"]["url"])
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

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

    def test_agent_subscription_list_explains_last_scheduler_run(self):
        schema_scheduler._SCHEMA_READY = False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            with patch.object(schema_scheduler, "get_configured_db_engine", return_value="sqlite"):
                set_agent_subscription(
                    conn,
                    {"id": 7, "name": "Teacher"},
                    template_key="exam_briefing",
                    enabled=True,
                    hour=7,
                )
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET last_result = ?, finished_at = ?
                WHERE dedupe_key = ?
                """,
                ("skipped: no upcoming exams", "2026-06-13T07:00:00", "agent-sub:exam_briefing:7"),
            )
            conn.commit()

            result = list_agent_subscriptions(conn, teacher_id=7)
            exam = next(item for item in result["subscriptions"] if item["key"] == "exam_briefing")

            self.assertTrue(exam["enabled"])
            self.assertIn("未来 3 天暂无考试/监考安排", exam["last_run_message"])
            self.assertEqual("2026-06-13T07:00:00", exam["last_finished_at"])
            self.assertIn("last_run_message", Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8"))
        finally:
            conn.close()
            schema_scheduler._SCHEMA_READY = False

    def test_agent_subscription_repeated_skip_notifies_message_center_once(self):
        conn = self._open_agent_task_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS teachers (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    email TEXT DEFAULT '',
                    nickname TEXT,
                    is_active INTEGER DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS teacher_calendar_events (
                    id INTEGER PRIMARY KEY,
                    teacher_id INTEGER,
                    title TEXT,
                    subtitle TEXT DEFAULT '',
                    starts_at TEXT,
                    location TEXT DEFAULT '',
                    source_type TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    deleted_at TEXT
                )
                """
            )
            conn.execute("INSERT INTO teachers (id, name, email, nickname, is_active) VALUES (7, 'Teacher', '', '', 1)")
            conn.commit()
            payload = {"teacher_id": 7, "template_key": "exam_briefing", "hour": 7}
            task_row = {
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "last_result": "skipped: no upcoming exams",
            }

            with patch("classroom_app.database.get_db_connection", return_value=conn):
                self.assertEqual("skipped: no upcoming exams", handle_agent_task_dispatch(task_row))
                self.assertEqual("skipped: no upcoming exams", handle_agent_task_dispatch(task_row))

            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT category, title, body_preview, link_url, ref_type, ref_id, metadata_json
                    FROM message_center_notifications
                    WHERE recipient_role = 'teacher' AND recipient_user_pk = 7
                    ORDER BY id
                    """
                ).fetchall()
            ]

            self.assertEqual(1, len(rows))
            self.assertEqual("agent_task", rows[0]["category"])
            self.assertIn("Agent 订阅提醒：考前提醒包", rows[0]["title"])
            self.assertIn("连续两次没有新产出", rows[0]["body_preview"])
            self.assertEqual("/?agent_subscriptions=1", rows[0]["link_url"])
            self.assertEqual("agent_task", rows[0]["ref_type"])
            self.assertEqual(
                "agent-subscription-skip:7:exam_briefing:skipped: no upcoming exams",
                rows[0]["ref_id"],
            )
            self.assertEqual(
                {
                    "agent_subscription": "exam_briefing",
                    "result": "skipped: no upcoming exams",
                    "consecutive": 2,
                },
                json.loads(rows[0]["metadata_json"]),
            )
            with patch.object(schema_scheduler, "get_configured_db_engine", return_value="sqlite"):
                set_agent_subscription(
                    conn,
                    {"id": 7, "name": "Teacher"},
                    template_key="exam_briefing",
                    enabled=True,
                    hour=7,
                )
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET last_result = ?, finished_at = ?
                WHERE dedupe_key = ?
                """,
                ("skipped: no upcoming exams", "2026-06-13T07:00:00", "agent-sub:exam_briefing:7"),
            )
            conn.commit()
            subscription_result = list_agent_subscriptions(conn, teacher_id=7)
            exam = next(item for item in subscription_result["subscriptions"] if item["key"] == "exam_briefing")
            self.assertEqual("suggest_pause", exam["attention_level"])
            self.assertIn("暂停", exam["attention_message"])
            self.assertEqual(rows[0]["ref_id"], exam["attention_ref_id"])

            workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
            self.assertIn("function handleAgentSubscriptionDeepLink", workspace_js)
            self.assertIn("agent_subscriptions", workspace_js)
            self.assertIn("ai-agent-subscriptions-panel", workspace_js)
            self.assertIn("attention_message", workspace_js)
            ui_css = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
            self.assertIn("ai-agent-subscription-row__attention", ui_css)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

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

    def test_image_agent_attachment_gets_text_fallback_and_workspace_note(self):
        from PIL import Image

        from classroom_app.routers.agent_tasks import _process_agent_attachment

        buffer = io.BytesIO()
        Image.new("RGB", (3, 2), color="white").save(buffer, format="PNG")

        class _Upload:
            filename = "classroom-screenshot.png"

            async def read(self):
                return buffer.getvalue()

        item = asyncio.run(_process_agent_attachment(_Upload()))

        self.assertEqual("image", item["kind"])
        self.assertIn("3x2", item["text"])
        self.assertIn("原图位于任务 workspace", item["text"])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            agent_task_service,
            "AGENT_TASK_WORKSPACE_ROOT",
            Path(tmpdir),
        ):
            metadata = agent_task_service.save_task_attachments(88, [item])
            stored = Path(tmpdir) / "tasks" / "88" / "attachments" / metadata[0]["stored_name"]
            extracted = stored.with_name(stored.name + ".extracted.txt")

            self.assertTrue(stored.exists())
            self.assertTrue(extracted.exists())
            self.assertIn("3x2", extracted.read_text(encoding="utf-8"))
            self.assertIn("3x2", metadata[0]["summary"])

    def test_broken_image_agent_attachment_is_rejected(self):
        from classroom_app.routers.agent_tasks import _process_agent_attachment

        class _Upload:
            filename = "broken.png"

            async def read(self):
                return b"not a real image"

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(_process_agent_attachment(_Upload()))
        self.assertEqual(400, ctx.exception.status_code)
        self.assertIn("无法读取", ctx.exception.detail)

    def test_failed_runtime_detail_recovers_safe_workspace_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            agent_task_service,
            "AGENT_TASK_WORKSPACE_ROOT",
            Path(tmpdir),
        ):
            workspace = Path(tmpdir) / "tasks" / "42"
            outputs = workspace / "outputs"
            attachments = workspace / "attachments"
            outputs.mkdir(parents=True)
            attachments.mkdir(parents=True)
            (workspace / "TASK.md").write_text("private task", encoding="utf-8")
            (workspace / "BRIDGE.md").write_text("token", encoding="utf-8")
            (workspace / "context.json").write_text("{}", encoding="utf-8")
            (workspace / "PARTIAL_RESULT.md").write_text("部分完成内容", encoding="utf-8")
            (outputs / "table.csv").write_text("name,score\nAlice,95", encoding="utf-8")
            (attachments / "source.txt").write_text("teacher upload", encoding="utf-8")

            detail, summary = agent_task_service.build_failed_runtime_detail(
                42,
                runtime_task={"status": "running", "summary": "已完成课堂数据整理"},
                error_class="timeout",
                error_message="timeout",
            )

            recovered_paths = [item["path"] for item in detail["recovered_artifacts"]]
            self.assertIn("PARTIAL_RESULT.md", recovered_paths)
            self.assertIn("outputs/table.csv", recovered_paths)
            self.assertNotIn("TASK.md", recovered_paths)
            self.assertNotIn("BRIDGE.md", recovered_paths)
            self.assertNotIn("context.json", recovered_paths)
            self.assertNotIn("attachments/source.txt", recovered_paths)
            self.assertTrue(detail["partial_result_available"])
            self.assertIn("next_actions", detail)
            self.assertTrue(any("底部输入框" in item for item in detail["next_actions"]))
            self.assertIn("部分完成总结", summary)
            self.assertIn("已完成课堂数据整理", summary)

            resolved = agent_task_service.resolve_task_workspace_artifact(42, "outputs/table.csv")
            self.assertEqual(outputs / "table.csv", resolved["path"])
            with self.assertRaises(ValueError):
                agent_task_service.resolve_task_workspace_artifact(42, "BRIDGE.md")
            with self.assertRaises(ValueError):
                agent_task_service.resolve_task_workspace_artifact(42, "attachments/source.txt")
            with self.assertRaises(ValueError):
                agent_task_service.resolve_task_workspace_artifact(42, "../context.json")

    def test_failed_task_detail_recovers_existing_workspace_artifacts_on_read(self):
        conn = self._open_agent_task_conn()
        try:
            with tempfile.TemporaryDirectory() as tmpdir, patch.object(
                agent_task_service,
                "AGENT_TASK_WORKSPACE_ROOT",
                Path(tmpdir),
            ):
                task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_FAILED)
                conn.execute(
                    """
                    UPDATE agent_tasks
                    SET result_summary = ?, error_message = ?
                    WHERE id = ?
                    """,
                    ("Agent task exceeded 1800 seconds.", "Agent task exceeded 1800 seconds.", task_id),
                )
                workspace = Path(tmpdir) / "tasks" / str(task_id)
                workspace.mkdir(parents=True)
                (workspace / "blog_draft.md").write_text("# 可用草稿\n\n已经完成的部分。", encoding="utf-8")

                task = agent_task_service.get_agent_task(conn, task_id, teacher_id=7)

                self.assertIn("部分完成总结", task["result_summary"])
                self.assertIn("blog_draft.md", task["result_summary"])
                recovered_paths = [item["path"] for item in task["result_detail"]["recovered_artifacts"]]
                self.assertEqual(["blog_draft.md"], recovered_paths)
                self.assertTrue(any("底部输入框" in item for item in task["result_detail"]["next_actions"]))
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

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
            event_detail = json.loads(event["detail_json"])
            self.assertEqual("prompt", event_detail["runtime_injection"])
            self.assertFalse(event_detail["follow_up_available"])

            row = dict(conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())
            prompt = agent_task_service.build_runtime_prompt(row, "/workspace/tasks/1")
            self.assertIn("请补充输出学生分层建议", prompt)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False

    def test_running_task_supplement_surfaces_follow_up_fallback(self):
        conn = self._open_agent_task_conn()
        try:
            task_id = self._insert_agent_task_row(conn, status=agent_task_service.TASK_STATUS_RUNNING)

            result = agent_task_service.add_task_supplement(
                conn,
                {"id": 7, "name": "Teacher"},
                task_id,
                "请把课堂活动再压缩成 15 分钟版本",
            )

            self.assertEqual(task_id, result["id"])
            event = conn.execute(
                "SELECT event_type, message, detail_json FROM agent_task_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            self.assertEqual("pending_supplement", event["event_type"])
            self.assertIn("一键作为追问", event["message"])
            detail = json.loads(event["detail_json"])
            self.assertEqual("visible_event", detail["runtime_injection"])
            self.assertTrue(detail["follow_up_available"])
            self.assertEqual("请把课堂活动再压缩成 15 分钟版本", detail["supplement"])

            workspace_js = Path("static/js/ai_workspace_widget.js").read_text(encoding="utf-8")
            self.assertIn("data-agent-supplement-followup", workspace_js)
            self.assertIn("function prefillSupplementFollowUp", workspace_js)
            self.assertIn("补充说明：", workspace_js)
            ui_css = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")
            self.assertIn("ai-task-event__supplement", ui_css)
            self.assertIn("ai-task-event__followup", ui_css)
        finally:
            conn.close()
            schema_agent_ext._SCHEMA_READY = False


if __name__ == "__main__":
    unittest.main()
