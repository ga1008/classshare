from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..config import (
    AGENT_TASK_RUNTIME_WORKSPACE_PREFIX,
    AGENT_TASK_WORKSPACE_ROOT,
)


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELED = "canceled"

ACTIVE_TASK_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
FINAL_TASK_STATUSES = {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELED}

TASK_STATUS_LABELS = {
    TASK_STATUS_QUEUED: "排队中",
    TASK_STATUS_RUNNING: "执行中",
    TASK_STATUS_COMPLETED: "已完成",
    TASK_STATUS_FAILED: "失败",
    TASK_STATUS_CANCELED: "已取消",
}

TASK_TYPE_DEFINITIONS: dict[str, dict[str, str]] = {
    "course_material_digest": {
        "label": "整理课程材料",
        "verb": "整理",
        "placeholder": "整理本课堂近期材料，输出下次课导学文档草稿。",
    },
    "lesson_document": {
        "label": "生成学习文档",
        "verb": "生成",
        "placeholder": "结合当前课程材料，生成下一次课的学习文档。",
    },
    "assignment_blueprint": {
        "label": "生成作业/考试草案",
        "verb": "出题",
        "placeholder": "结合材料和 JSON 模板，生成下次课课堂作业草案。",
    },
    "blog_draft": {
        "label": "撰写课堂博客",
        "verb": "撰写",
        "placeholder": "围绕本课堂主题写一篇可发布的博客草稿。",
    },
    "student_notification": {
        "label": "拟定学生通知",
        "verb": "通知",
        "placeholder": "给某考试低于指定分数的学生拟定通知内容和名单规则。",
    },
    "general_teaching_task": {
        "label": "教学事务",
        "verb": "处理",
        "placeholder": "描述一个需要排队执行的教学事务。",
    },
}

_CORE_CODE_DENY_PATTERNS = (
    r"\bgit\s+(commit|push|pull|reset|checkout|merge|rebase|clean|rm)\b",
    r"\bdocker\s+(compose|run|exec|build|rm|rmi|stop|restart)\b",
    r"\b(rm\s+-rf|del\s+/f|format\s+|drop\s+table|truncate\s+table)\b",
    r"\b(classroom_app|templates|static/js|static/css|Dockerfile|docker-compose|main\.py|ai_assistant\.py)\b",
    r"(修改|重构|删除|覆盖|提交|推送|部署).{0,16}(核心代码|源码|项目代码|代码库|路由|模板|数据库结构|数据表|迁移)",
    r"(核心代码|源码|项目代码|代码库|路由|模板|数据库结构|数据表|迁移).{0,16}(修改|重构|删除|覆盖|提交|推送|部署)",
)

MAX_INSTRUCTION_CHARS = 4000
MAX_CONTEXT_TEXT_CHARS = 16000
MAX_RESULT_DETAIL_CHARS = 40000


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, max_chars: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _load_json(raw_value: Any, fallback: Any) -> Any:
    if raw_value in (None, ""):
        return fallback
    try:
        return json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _summarize_text(text: str, *, limit: int = 96) -> str:
    normalized = re.sub(r"\s+", " ", _clean_text(text))
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def validate_business_task(instruction: str) -> None:
    normalized = instruction.strip()
    if len(normalized) < 6:
        raise HTTPException(status_code=400, detail="请补充更明确的任务内容。")
    if len(normalized) > MAX_INSTRUCTION_CHARS:
        raise HTTPException(status_code=400, detail="任务描述过长，请压缩到 4000 字以内。")

    lowered = normalized.lower()
    for pattern in _CORE_CODE_DENY_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail="任务中心只处理教学业务事务，不能执行核心代码、部署、数据库结构或项目源码改动。",
            )


def task_type_options() -> list[dict[str, str]]:
    return [
        {"value": key, **value}
        for key, value in TASK_TYPE_DEFINITIONS.items()
    ]


def _teacher_display_name(user: dict[str, Any]) -> str:
    return _clean_text(user.get("name") or user.get("nickname") or f"教师{user.get('id') or ''}", max_chars=80)


def _normalize_context_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        safe_key = _clean_text(key, max_chars=64)
        if not safe_key:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[safe_key] = _clean_text(value, max_chars=MAX_CONTEXT_TEXT_CHARS) if isinstance(value, str) else value
        elif isinstance(value, list):
            normalized[safe_key] = value[:20]
        elif isinstance(value, dict):
            normalized[safe_key] = {
                _clean_text(child_key, max_chars=64): (
                    _clean_text(child_value, max_chars=2000)
                    if isinstance(child_value, str)
                    else child_value
                )
                for child_key, child_value in list(value.items())[:40]
            }
    return normalized


def _resolve_optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def build_teacher_page_context(conn, teacher_id: int, page_context: dict[str, Any]) -> dict[str, Any]:
    """Enrich client page hints with server-verified teaching context."""
    context = _normalize_context_payload(page_context)
    context.setdefault("server_context", {})
    server_context: dict[str, Any] = {}

    class_offering_id = _resolve_optional_int(
        context.get("classOfferingId")
        or context.get("class_offering_id")
        or (context.get("materialContext") or {}).get("classOfferingId")
    )
    assignment_id = _resolve_optional_int(context.get("assignmentId") or context.get("assignment_id"))
    material_id = _resolve_optional_int(
        context.get("materialId")
        or context.get("material_id")
        or (context.get("materialContext") or {}).get("materialId")
    )

    if assignment_id:
        row = conn.execute(
            """
            SELECT a.id, a.title, a.status, a.class_offering_id, a.requirements_md, a.rubric_md,
                   c.name AS course_name, cl.name AS class_name,
                   COUNT(s.id) AS submission_count
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings co ON co.id = a.class_offering_id
            LEFT JOIN classes cl ON cl.id = co.class_id
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE a.id = ?
              AND (co.teacher_id = ? OR c.created_by_teacher_id = ?)
            GROUP BY a.id
            LIMIT 1
            """,
            (assignment_id, teacher_id, teacher_id),
        ).fetchone()
        if row:
            server_context["assignment"] = {
                "id": int(row["id"]),
                "title": row["title"],
                "status": row["status"],
                "class_offering_id": row["class_offering_id"],
                "course_name": row["course_name"],
                "class_name": row["class_name"],
                "submission_count": int(row["submission_count"] or 0),
                "requirements_excerpt": _summarize_text(row["requirements_md"] or "", limit=600),
                "rubric_excerpt": _summarize_text(row["rubric_md"] or "", limit=600),
            }
            class_offering_id = class_offering_id or _resolve_optional_int(row["class_offering_id"])

    if material_id:
        row = conn.execute(
            """
            SELECT id, name, material_path, node_type, preview_type, ai_parse_status,
                   ai_parse_result_json
            FROM course_materials
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (material_id, teacher_id),
        ).fetchone()
        if row:
            parsed = _load_json(row["ai_parse_result_json"], {})
            server_context["material"] = {
                "id": int(row["id"]),
                "name": row["name"],
                "path": row["material_path"],
                "node_type": row["node_type"],
                "preview_type": row["preview_type"],
                "ai_parse_status": row["ai_parse_status"],
                "ai_summary": _summarize_text(parsed.get("summary") or "", limit=800)
                if isinstance(parsed, dict)
                else "",
            }

    if class_offering_id:
        row = conn.execute(
            """
            SELECT co.id, co.semester, co.schedule_info, co.first_class_date,
                   c.name AS course_name, cl.name AS class_name
            FROM class_offerings co
            JOIN courses c ON c.id = co.course_id
            JOIN classes cl ON cl.id = co.class_id
            WHERE co.id = ? AND co.teacher_id = ?
            LIMIT 1
            """,
            (class_offering_id, teacher_id),
        ).fetchone()
        if row:
            assignments = conn.execute(
                """
                SELECT id, title, status, due_at, created_at
                FROM assignments
                WHERE class_offering_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 8
                """,
                (class_offering_id,),
            ).fetchall()
            materials = conn.execute(
                """
                SELECT m.id, m.name, m.material_path, m.preview_type
                FROM course_material_assignments a
                JOIN course_materials m ON m.id = a.material_id
                WHERE a.class_offering_id = ?
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT 8
                """,
                (class_offering_id,),
            ).fetchall()
            server_context["classroom"] = {
                "id": int(row["id"]),
                "course_name": row["course_name"],
                "class_name": row["class_name"],
                "semester": row["semester"],
                "first_class_date": row["first_class_date"],
                "schedule_info": _summarize_text(row["schedule_info"] or "", limit=500),
                "recent_assignments": [dict(item) for item in assignments],
                "recent_materials": [dict(item) for item in materials],
            }

    context["server_context"] = server_context
    return context


def create_agent_task(conn, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    teacher_id = int(user["id"])
    task_type = _clean_text(payload.get("task_type"), max_chars=64) or "general_teaching_task"
    if task_type not in TASK_TYPE_DEFINITIONS:
        task_type = "general_teaching_task"

    instruction = _clean_text(payload.get("instruction"), max_chars=MAX_INSTRUCTION_CHARS)
    validate_business_task(instruction)

    context_snapshot = build_teacher_page_context(conn, teacher_id, payload.get("page_context") or {})
    title = _clean_text(payload.get("title"), max_chars=120)
    if not title:
        title = _summarize_text(instruction, limit=36) or TASK_TYPE_DEFINITIONS[task_type]["label"]
    public_summary = f"{TASK_TYPE_DEFINITIONS[task_type]['label']}：{_summarize_text(title, limit=64)}"

    now = utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO agent_tasks (
            task_uuid, teacher_id, teacher_name, task_type, title, public_summary,
            private_instruction, context_snapshot_json, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            teacher_id,
            _teacher_display_name(user),
            task_type,
            title,
            public_summary,
            instruction,
            _json_dumps(context_snapshot),
            TASK_STATUS_QUEUED,
            now,
            now,
        ),
    )
    task_id = int(cursor.lastrowid)
    append_task_event(
        conn,
        task_id,
        "queued",
        "任务已进入全平台队列。",
        {"task_type": task_type},
        commit=False,
    )
    return get_agent_task(conn, task_id, teacher_id=teacher_id)


def append_task_event(
    conn,
    task_id: int,
    event_type: str,
    message: str,
    detail: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_task_events (task_id, event_type, message, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(task_id),
            _clean_text(event_type, max_chars=40) or "status",
            _clean_text(message, max_chars=1000),
            _json_dumps(detail or {}),
            utcnow_iso(),
        ),
    )
    if commit:
        conn.commit()


def _queue_positions(rows: list[dict[str, Any]]) -> dict[int, int]:
    queued = [
        item
        for item in sorted(rows, key=lambda value: (value.get("created_at") or "", int(value.get("id") or 0)))
        if item.get("status") == TASK_STATUS_QUEUED
    ]
    return {int(item["id"]): index + 1 for index, item in enumerate(queued)}


def _elapsed_seconds(item: dict[str, Any]) -> int:
    started_at = item.get("started_at")
    completed_at = item.get("completed_at")
    if not started_at:
        return 0
    try:
        start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00")) if completed_at else datetime.now(timezone.utc)
    except ValueError:
        return 0
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return max(0, int((end_dt - start_dt).total_seconds()))


def _serialize_event(row) -> dict[str, Any]:
    item = dict(row)
    return {
        "id": int(item.get("id") or 0),
        "event_type": item.get("event_type") or "status",
        "message": item.get("message") or "",
        "detail": _load_json(item.get("detail_json"), {}),
        "created_at": item.get("created_at") or "",
    }


def serialize_agent_task(row, *, viewer_teacher_id: int, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item = dict(row)
    task_id = int(item.get("id") or 0)
    owner_teacher_id = int(item.get("teacher_id") or 0)
    is_owner = owner_teacher_id == int(viewer_teacher_id)
    status = item.get("status") or TASK_STATUS_QUEUED
    payload = {
        "id": task_id,
        "task_uuid": item.get("task_uuid") or "",
        "task_type": item.get("task_type") or "",
        "task_type_label": TASK_TYPE_DEFINITIONS.get(item.get("task_type") or "", {}).get("label", "教学事务"),
        "title": item.get("title") if is_owner else item.get("public_summary"),
        "public_summary": item.get("public_summary") or "",
        "teacher_name": item.get("teacher_name") or "某位老师",
        "status": status,
        "status_label": TASK_STATUS_LABELS.get(status, "处理中"),
        "is_owner": is_owner,
        "is_active": status in ACTIVE_TASK_STATUSES,
        "is_terminal": status in FINAL_TASK_STATUSES,
        "queue_position": int(item.get("queue_position") or 0),
        "elapsed_seconds": _elapsed_seconds(item),
        "runtime_provider": item.get("runtime_provider") or "deepseek-tui",
        "runtime_status": item.get("runtime_status") or "",
        "created_at": item.get("created_at") or "",
        "started_at": item.get("started_at") or "",
        "completed_at": item.get("completed_at") or "",
        "updated_at": item.get("updated_at") or "",
    }
    if is_owner:
        payload.update(
            {
                "private_instruction": item.get("private_instruction") or "",
                "context_snapshot": _load_json(item.get("context_snapshot_json"), {}),
                "runtime_task_id": item.get("runtime_task_id") or "",
                "runtime_thread_id": item.get("runtime_thread_id") or "",
                "runtime_turn_id": item.get("runtime_turn_id") or "",
                "result_summary": item.get("result_summary") or "",
                "result_detail": _load_json(item.get("result_detail_json"), {}),
                "error_message": item.get("error_message") or "",
                "events": events or [],
            }
        )
    return payload


def list_agent_tasks(conn, *, viewer_teacher_id: int, limit: int = 30) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM agent_tasks
            ORDER BY
              CASE status
                WHEN 'running' THEN 0
                WHEN 'queued' THEN 1
                ELSE 2
              END,
              created_at ASC,
              id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 80)),),
        ).fetchall()
    ]
    queue_positions = _queue_positions(rows)
    for row in rows:
        row["queue_position"] = queue_positions.get(int(row["id"]), 0)
    counts = {
        status: int(
            conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE status = ?", (status,)).fetchone()[0]
        )
        for status in (
            TASK_STATUS_QUEUED,
            TASK_STATUS_RUNNING,
            TASK_STATUS_COMPLETED,
            TASK_STATUS_FAILED,
            TASK_STATUS_CANCELED,
        )
    }
    return {
        "tasks": [
            serialize_agent_task(row, viewer_teacher_id=viewer_teacher_id)
            for row in rows
        ],
        "counts": counts,
    }


def get_agent_task(conn, task_id: int, *, teacher_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")
    events = [
        _serialize_event(event)
        for event in conn.execute(
            "SELECT * FROM agent_task_events WHERE task_id = ? ORDER BY id ASC",
            (int(task_id),),
        ).fetchall()
    ]
    item = dict(row)
    queue_positions = _queue_positions([item])
    item["queue_position"] = queue_positions.get(int(item["id"]), 0)
    serialized = serialize_agent_task(item, viewer_teacher_id=int(teacher_id), events=events)
    if not serialized["is_owner"]:
        return serialized
    return serialized


def cancel_agent_task(conn, task_id: int, *, teacher_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if int(row["teacher_id"] or 0) != int(teacher_id):
        raise HTTPException(status_code=403, detail="只能取消自己的任务。")
    status = str(row["status"] or "")
    if status in FINAL_TASK_STATUSES:
        return get_agent_task(conn, task_id, teacher_id=teacher_id)

    now = utcnow_iso()
    if status == TASK_STATUS_RUNNING:
        conn.execute(
            """
            UPDATE agent_tasks
            SET cancel_requested_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(task_id)),
        )
        append_task_event(conn, task_id, "cancel_requested", "已请求取消，正在等待执行器响应。", commit=False)
    else:
        conn.execute(
            """
            UPDATE agent_tasks
            SET status = ?, cancel_requested_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_CANCELED, now, now, now, int(task_id)),
        )
        append_task_event(conn, task_id, "canceled", "任务已取消。", commit=False)
    conn.commit()
    return get_agent_task(conn, task_id, teacher_id=teacher_id)


def claim_next_agent_task(conn, *, worker_id: str) -> dict[str, Any] | None:
    now = utcnow_iso()
    conn.execute("BEGIN IMMEDIATE")
    running = conn.execute(
        "SELECT id FROM agent_tasks WHERE status = ? LIMIT 1",
        (TASK_STATUS_RUNNING,),
    ).fetchone()
    if running:
        conn.commit()
        return None
    row = conn.execute(
        """
        SELECT *
        FROM agent_tasks
        WHERE status = ?
        ORDER BY priority DESC, created_at ASC, id ASC
        LIMIT 1
        """,
        (TASK_STATUS_QUEUED,),
    ).fetchone()
    if not row:
        conn.commit()
        return None
    task_id = int(row["id"])
    conn.execute(
        """
        UPDATE agent_tasks
        SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?, worker_id = ?
        WHERE id = ? AND status = ?
        """,
        (TASK_STATUS_RUNNING, now, now, worker_id, task_id, TASK_STATUS_QUEUED),
    )
    append_task_event(conn, task_id, "started", "Agent 执行器已领取任务。", {"worker_id": worker_id}, commit=False)
    conn.commit()
    return dict(conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())


def mark_task_runtime_started(
    conn,
    task_id: int,
    *,
    runtime_task_id: str,
    runtime_thread_id: str = "",
    runtime_turn_id: str = "",
) -> None:
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE agent_tasks
        SET runtime_task_id = ?, runtime_thread_id = ?, runtime_turn_id = ?,
            runtime_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (runtime_task_id, runtime_thread_id, runtime_turn_id, TASK_STATUS_RUNNING, now, int(task_id)),
    )
    append_task_event(
        conn,
        task_id,
        "runtime_started",
        "已接入 DeepSeek-TUI 独立运行时。",
        {"runtime_task_id": runtime_task_id},
        commit=False,
    )
    conn.commit()


def update_task_runtime_snapshot(conn, task_id: int, runtime_task: dict[str, Any]) -> None:
    now = utcnow_iso()
    status = str(runtime_task.get("status") or "")
    conn.execute(
        """
        UPDATE agent_tasks
        SET runtime_status = ?, runtime_thread_id = COALESCE(?, runtime_thread_id),
            runtime_turn_id = COALESCE(?, runtime_turn_id), updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            runtime_task.get("thread_id"),
            runtime_task.get("turn_id"),
            now,
            int(task_id),
        ),
    )
    conn.commit()


def finish_agent_task(
    conn,
    task_id: int,
    *,
    status: str,
    result_summary: str = "",
    result_detail: dict[str, Any] | None = None,
    error_message: str = "",
) -> None:
    now = utcnow_iso()
    safe_status = status if status in FINAL_TASK_STATUSES else TASK_STATUS_FAILED
    conn.execute(
        """
        UPDATE agent_tasks
        SET status = ?, result_summary = ?, result_detail_json = ?, error_message = ?,
            completed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            safe_status,
            _clean_text(result_summary, max_chars=2000),
            _json_dumps(result_detail or {}),
            _clean_text(error_message, max_chars=2000),
            now,
            now,
            int(task_id),
        ),
    )
    append_task_event(
        conn,
        task_id,
        safe_status,
        TASK_STATUS_LABELS.get(safe_status, "任务结束"),
        {"error": error_message} if error_message else {},
        commit=False,
    )
    conn.commit()


def task_workspace_paths(task: dict[str, Any]) -> tuple[Path, str]:
    task_id = str(task.get("id") or task.get("task_uuid") or uuid.uuid4())
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id)
    host_path = AGENT_TASK_WORKSPACE_ROOT / "tasks" / safe_name
    runtime_path = f"{AGENT_TASK_RUNTIME_WORKSPACE_PREFIX}/{safe_name}"
    return host_path, runtime_path


def write_task_workspace(task: dict[str, Any]) -> str:
    host_path, runtime_path = task_workspace_paths(task)
    host_path.mkdir(parents=True, exist_ok=True)
    context = _load_json(task.get("context_snapshot_json"), {})
    context_text = json.dumps(context, ensure_ascii=False, indent=2)
    instructions = _clean_text(task.get("private_instruction"), max_chars=MAX_INSTRUCTION_CHARS)
    readme = f"""# LanShare Agent Task {task.get('id')}

## Task

{instructions}

## Verified Page Context

```json
{context_text[:MAX_CONTEXT_TEXT_CHARS]}
```

## Safety Boundary

- Do not modify LanShare core source code, deployment files, database schema, or runtime configuration.
- Work only with the task context and produce business-facing drafts, checklists, or validated action proposals.
- If platform state changes are needed, describe the exact whitelisted action and wait for LanShare to execute it.
"""
    (host_path / "TASK.md").write_text(readme, encoding="utf-8")
    (host_path / "context.json").write_text(context_text, encoding="utf-8")
    return runtime_path


def build_runtime_prompt(task: dict[str, Any], runtime_workspace: str) -> str:
    context = _load_json(task.get("context_snapshot_json"), {})
    task_type = str(task.get("task_type") or "general_teaching_task")
    definition = TASK_TYPE_DEFINITIONS.get(task_type, TASK_TYPE_DEFINITIONS["general_teaching_task"])
    instruction = _clean_text(task.get("private_instruction"), max_chars=MAX_INSTRUCTION_CHARS)
    return f"""
你是 LanShare 内置的教师任务中心 Agent，当前任务类型是：{definition["label"]}。

必须遵守：
1. 只处理教学业务相关任务：课程材料整理、学习文档草案、作业/考试草案、博客草稿、学生通知草稿、教学事务分析。
2. 严禁修改、生成补丁、删除、重构或部署 LanShare 核心代码；严禁修改数据库结构、Docker 配置、运行脚本、源码目录。
3. 你所在 workspace 是隔离任务目录：{runtime_workspace}。只能在此目录内阅读上下文、整理产物。
4. 涉及发布博客、发送通知、创建作业/考试等平台状态变更时，先输出结构化草案和执行建议，不要假装已经修改平台数据。
5. 输出必须面向教师，清楚列出：任务理解、已使用的上下文、执行结果/草案、需要教师确认的动作、风险提醒。

教师任务：
{instruction}

平台已验证的页面和课堂上下文如下，若上下文不足请明确说明，不要编造不存在的数据：
```json
{json.dumps(context, ensure_ascii=False, indent=2)[:MAX_CONTEXT_TEXT_CHARS]}
```
""".strip()


def compact_runtime_detail(runtime_task: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "runtime_task_id": runtime_task.get("id"),
        "runtime_status": runtime_task.get("status"),
        "thread_id": runtime_task.get("thread_id"),
        "turn_id": runtime_task.get("turn_id"),
        "result_summary": runtime_task.get("result_summary"),
        "error": runtime_task.get("error"),
        "duration_ms": runtime_task.get("duration_ms"),
        "timeline": runtime_task.get("timeline") or [],
        "tool_calls": runtime_task.get("tool_calls") or [],
        "artifacts": runtime_task.get("artifacts") or [],
    }
    encoded = json.dumps(detail, ensure_ascii=False)
    if len(encoded) > MAX_RESULT_DETAIL_CHARS:
        detail["timeline"] = detail["timeline"][-20:]
        detail["tool_calls"] = detail["tool_calls"][-20:]
        detail["truncated"] = True
    return detail
