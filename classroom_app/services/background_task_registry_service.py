from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BackgroundTaskDefinition:
    task_type: str
    display_name: str
    source: str
    recoverable: bool
    recovery_action: str
    owner: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "display_name": self.display_name,
            "source": self.source,
            "recoverable": bool(self.recoverable),
            "recovery_action": self.recovery_action,
            "owner": self.owner,
        }


BACKGROUND_TASK_DEFINITIONS: tuple[BackgroundTaskDefinition, ...] = (
    BackgroundTaskDefinition(
        task_type="ai_grading",
        display_name="AI 批改",
        source="submissions.status / ai_assistant callback",
        recoverable=True,
        recovery_action="startup reclaim stale grading tasks; teacher can requeue failed submissions",
        owner="homework",
    ),
    BackgroundTaskDefinition(
        task_type="material_ai_import",
        display_name="材料 AI 导入",
        source="material_ai_import_records + in-memory import workers",
        recoverable=True,
        recovery_action="re-enqueue queued records; mark stale running records for retry or failure",
        owner="materials",
    ),
    BackgroundTaskDefinition(
        task_type="session_material_generation",
        display_name="课堂材料生成",
        source="session_material_generation_tasks",
        recoverable=True,
        recovery_action="expire stale running generation tasks; retry from persisted request payload",
        owner="materials",
    ),
    BackgroundTaskDefinition(
        task_type="private_message_ai_reply",
        display_name="消息中心 AI 回复",
        source="private_message_ai_jobs + scheduled asyncio tasks",
        recoverable=True,
        recovery_action="startup requeues pending/running jobs before scheduling workers",
        owner="message_center",
    ),
    BackgroundTaskDefinition(
        task_type="email_outbox",
        display_name="邮件发送",
        source="email_outbox + email_worker_heartbeats",
        recoverable=True,
        recovery_action="worker claims queued due jobs; failed jobs require retry policy or manual review",
        owner="message_center",
    ),
    BackgroundTaskDefinition(
        task_type="blog_news_crawler",
        display_name="博客爬虫",
        source="blog_news_crawler_runs + blog_news_crawler_config heartbeat",
        recoverable=True,
        recovery_action="stale running runs can be expired; pending runs are picked by crawler worker",
        owner="learning_blog",
    ),
    BackgroundTaskDefinition(
        task_type="agent_task",
        display_name="Agent worker",
        source="agent_tasks + agent_task_events",
        recoverable=True,
        recovery_action="queued tasks persist; running tasks can be reclaimed after runtime timeout",
        owner="agent",
    ),
    BackgroundTaskDefinition(
        task_type="scheduled_task",
        display_name="定时任务调度",
        source="scheduled_tasks + scheduled_task_worker_heartbeats",
        recoverable=True,
        recovery_action="due tasks persist with run_at; stale running tasks are reclaimed after lock timeout and retried with backoff",
        owner="platform",
    ),
    BackgroundTaskDefinition(
        task_type="behavior_write_pipeline",
        display_name="行为写入管线",
        source="behavior write pipeline runtime queue",
        recoverable=False,
        recovery_action="restart worker and monitor queue depth; source events are transient",
        owner="classroom_activity",
    ),
)


def list_background_task_definitions() -> list[dict[str, Any]]:
    return [definition.to_dict() for definition in BACKGROUND_TASK_DEFINITIONS]


def get_background_task_definition(task_type: str) -> BackgroundTaskDefinition:
    normalized = str(task_type or "").strip()
    for definition in BACKGROUND_TASK_DEFINITIONS:
        if definition.task_type == normalized:
            return definition
    raise KeyError(f"Unknown background task type: {task_type}")
