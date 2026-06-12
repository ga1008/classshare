from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .message_center_service import (
    MESSAGE_CATEGORY_AI_FEEDBACK,
    _build_notification_payload,
    _insert_notification_if_allowed,
    list_super_admin_teachers,
)


STAGE_EXAM_DAILY_LIMIT = 3
DEFAULT_AI_WEEKLY_BUDGETS: dict[str, int] = {
    "stage_exam_generation": 50,
    "ai_grading": 600,
    "behavior_profile": 600,
    "material_mastery_check_generation": 300,
    "weekly_report": 300,
    "total": 2000,
}


class AIUsageBudgetError(ValueError):
    """Raised when an AI usage budget or rate-limit policy blocks an action."""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _week_start(value: datetime | date) -> date:
    current = value.date() if isinstance(value, datetime) else value
    return current - timedelta(days=current.weekday())


def normalize_ai_weekly_budget(payload: Any) -> dict[str, int]:
    data = _json_loads(payload, {}) if not isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        raise AIUsageBudgetError("AI 预算格式不正确")
    budgets = data.get("weekly_budget") if isinstance(data.get("weekly_budget"), dict) else data
    normalized = dict(DEFAULT_AI_WEEKLY_BUDGETS)
    for key in DEFAULT_AI_WEEKLY_BUDGETS:
        if key not in budgets:
            continue
        value = _safe_int(budgets.get(key), -1)
        if value < 0:
            raise AIUsageBudgetError("AI 周预算不能小于 0")
        normalized[key] = min(value, 100000)
    return normalized


def serialize_ai_weekly_budget(budget: dict[str, int]) -> str:
    normalized = normalize_ai_weekly_budget(budget)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def _default_budget_config() -> dict[str, Any]:
    return {
        "weekly_budget": dict(DEFAULT_AI_WEEKLY_BUDGETS),
        "source": "default",
        "updated_at": None,
    }


def load_offering_ai_budget_config(conn, class_offering_id: int) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT ai_weekly_budget_json,
                   ai_weekly_budget_updated_at
            FROM class_offerings
            WHERE id = ?
            LIMIT 1
            """,
            (int(class_offering_id),),
        ).fetchone()
    except Exception as exc:
        message = str(exc).lower()
        if "ai_weekly_budget" not in message and "no such column" not in message and "undefinedcolumn" not in message:
            raise
        return _default_budget_config()
    if not row:
        return _default_budget_config()
    item = dict(row)
    raw = str(item.get("ai_weekly_budget_json") or "").strip()
    if not raw:
        return _default_budget_config()
    try:
        budget = normalize_ai_weekly_budget(raw)
    except AIUsageBudgetError:
        return _default_budget_config()
    return {
        "weekly_budget": budget,
        "source": "custom",
        "updated_at": item.get("ai_weekly_budget_updated_at"),
    }


def save_offering_ai_budget_config(conn, class_offering_id: int, payload: Any) -> dict[str, Any]:
    budget = normalize_ai_weekly_budget(payload)
    timestamp = _now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE class_offerings
        SET ai_weekly_budget_json = ?,
            ai_weekly_budget_updated_at = ?
        WHERE id = ?
        """,
        (serialize_ai_weekly_budget(budget), timestamp, int(class_offering_id)),
    )
    return {"weekly_budget": budget, "source": "custom", "updated_at": timestamp}


def _load_recent_ai_usage_rows(conn, *, days: int = 56, limit: int = 5000) -> list[dict[str, Any]]:
    cutoff = (_now() - timedelta(days=max(1, int(days or 56)))).isoformat(timespec="seconds")
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM ai_usage_log
            WHERE created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (cutoff, max(1, min(int(limit or 5000), 20000))),
        ).fetchall()
    except Exception as exc:
        message = str(exc).lower()
        if "ai_usage_log" not in message and "no such table" not in message and "undefinedtable" not in message:
            raise
        return []
    return [dict(row) for row in rows]


def _load_offering_labels(conn, offering_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    sql = f"""
        SELECT o.id,
               o.ai_weekly_budget_json,
               o.ai_weekly_budget_updated_at,
               c.name AS course_name,
               cl.name AS class_name,
               t.name AS teacher_name,
               t.id AS teacher_id
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id IN ({placeholders})
        """
    params = tuple(sorted(offering_ids))
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        message = str(exc).lower()
        if "ai_weekly_budget" not in message and "no such column" not in message and "undefinedcolumn" not in message:
            if "no such table" in message or "undefinedtable" in message:
                return {}
            raise
        rows = conn.execute(
            f"""
            SELECT o.id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   t.name AS teacher_name,
                   t.id AS teacher_id
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN teachers t ON t.id = o.teacher_id
            WHERE o.id IN ({placeholders})
            """,
            params,
        ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _load_recent_offering_rows(conn, *, limit: int = 100) -> dict[int, dict[str, Any]]:
    sql = """
        SELECT o.id,
               o.ai_weekly_budget_json,
               o.ai_weekly_budget_updated_at,
               c.name AS course_name,
               cl.name AS class_name,
               t.name AS teacher_name,
               t.id AS teacher_id
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        ORDER BY o.id DESC
        LIMIT ?
        """
    try:
        rows = conn.execute(sql, (max(1, min(int(limit or 100), 300)),)).fetchall()
    except Exception as exc:
        message = str(exc).lower()
        if "ai_weekly_budget" not in message and "no such column" not in message and "undefinedcolumn" not in message:
            if "no such table" in message or "undefinedtable" in message:
                return {}
            raise
        rows = conn.execute(
            """
            SELECT o.id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   t.name AS teacher_name,
                   t.id AS teacher_id
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN teachers t ON t.id = o.teacher_id
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 100), 300)),),
        ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _task_bucket() -> dict[str, Any]:
    return {
        "count": 0,
        "success_count": 0,
        "failed_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "duration_ms": 0,
    }


def build_ai_usage_dashboard(conn, *, weeks: int = 8) -> dict[str, Any]:
    rows = _load_recent_ai_usage_rows(conn, days=max(7, int(weeks or 8) * 7), limit=10000)
    today = _now().date()
    current_week = _week_start(today)
    by_task: dict[str, dict[str, Any]] = defaultdict(_task_bucket)
    by_week: dict[str, dict[str, Any]] = defaultdict(_task_bucket)
    by_offering: dict[int, dict[str, Any]] = defaultdict(_task_bucket)
    by_task_week: dict[tuple[str, str], int] = defaultdict(int)
    total = _task_bucket()

    for row in rows:
        task_type = str(row.get("task_type") or "unknown")
        status = str(row.get("status") or "unknown")
        created_at = _parse_datetime(row.get("created_at")) or _now()
        week_key = _week_start(created_at).isoformat()
        offering_id = _safe_int(row.get("class_offering_id"))
        prompt_tokens = _safe_int(row.get("prompt_tokens_estimate"))
        completion_tokens = _safe_int(row.get("completion_tokens_estimate"))
        duration_ms = _safe_int(row.get("duration_ms"))
        for bucket in (total, by_task[task_type], by_week[week_key]):
            bucket["count"] += 1
            bucket["success_count"] += 1 if status == "success" else 0
            bucket["failed_count"] += 0 if status == "success" else 1
            bucket["prompt_tokens"] += prompt_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["duration_ms"] += duration_ms
        if offering_id:
            bucket = by_offering[offering_id]
            bucket["count"] += 1
            bucket["success_count"] += 1 if status == "success" else 0
            bucket["failed_count"] += 0 if status == "success" else 1
            bucket["prompt_tokens"] += prompt_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["duration_ms"] += duration_ms
            if task_type == "stage_exam_generation" and _week_start(created_at) == current_week:
                bucket["stage_exam_generation_this_week"] = _safe_int(bucket.get("stage_exam_generation_this_week")) + 1
        by_task_week[(task_type, week_key)] += 1

    def finalize_bucket(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
        count = _safe_int(bucket.get("count"))
        total_tokens = _safe_int(bucket.get("prompt_tokens")) + _safe_int(bucket.get("completion_tokens"))
        return {
            "key": key,
            **bucket,
            "total_tokens": total_tokens,
            "success_rate": round(_safe_int(bucket.get("success_count")) / count * 100, 1) if count else 0,
            "avg_duration_ms": round(_safe_int(bucket.get("duration_ms")) / count) if count else 0,
        }

    task_items = sorted(
        (finalize_bucket(task_type, bucket) for task_type, bucket in by_task.items()),
        key=lambda item: (-_safe_int(item.get("count")), item["key"]),
    )
    week_items = [
        finalize_bucket((current_week - timedelta(days=7 * index)).isoformat(), by_week[(current_week - timedelta(days=7 * index)).isoformat()])
        for index in range(max(1, int(weeks or 8)) - 1, -1, -1)
    ]
    max_week_count = max([_safe_int(item.get("count")) for item in week_items] or [1])

    offering_labels = _load_recent_offering_rows(conn)
    offering_labels.update(_load_offering_labels(conn, {key for key in by_offering if key}))
    offering_items: list[dict[str, Any]] = []
    for offering_id in sorted(set(offering_labels.keys()) | {key for key in by_offering if key}):
        bucket = by_offering.get(offering_id) or _task_bucket()
        label = offering_labels.get(offering_id, {})
        budget_config = load_offering_ai_budget_config(conn, offering_id)
        budget = budget_config["weekly_budget"]
        stage_count = _safe_int(bucket.get("stage_exam_generation_this_week"))
        item = finalize_bucket(str(offering_id), bucket)
        item.update({
            "class_offering_id": offering_id,
            "course_name": label.get("course_name") or f"课堂 {offering_id}",
            "class_name": label.get("class_name") or "",
            "teacher_name": label.get("teacher_name") or "",
            "budget_source": budget_config.get("source"),
            "weekly_budget": dict(budget),
            "stage_exam_generation_this_week": stage_count,
            "stage_exam_generation_budget": _safe_int(budget.get("stage_exam_generation")),
            "budget_percent": round(stage_count / max(_safe_int(budget.get("stage_exam_generation"), 1), 1) * 100, 1),
            "over_stage_exam_budget": stage_count > _safe_int(budget.get("stage_exam_generation")),
        })
        offering_items.append(item)
    offering_items.sort(key=lambda item: (-_safe_int(item.get("count")), str(item.get("course_name") or "")))

    anomalies = []
    previous_week = (current_week - timedelta(days=7)).isoformat()
    current_week_text = current_week.isoformat()
    for task_type in by_task:
        current_count = by_task_week.get((task_type, current_week_text), 0)
        previous_count = by_task_week.get((task_type, previous_week), 0)
        if current_count >= 3 and (previous_count == 0 or current_count >= previous_count * 3):
            anomalies.append({
                "task_type": task_type,
                "current_week_count": current_count,
                "previous_week_count": previous_count,
                "ratio": None if previous_count == 0 else round(current_count / previous_count, 1),
            })
    anomalies.sort(key=lambda item: (-_safe_int(item.get("current_week_count")), item["task_type"]))

    return {
        "summary": finalize_bucket("total", total),
        "task_items": task_items,
        "week_items": week_items,
        "max_week_count": max(1, max_week_count),
        "offering_items": offering_items[:50],
        "anomalies": anomalies[:12],
        "default_weekly_budget": dict(DEFAULT_AI_WEEKLY_BUDGETS),
        "stage_exam_daily_limit": STAGE_EXAM_DAILY_LIMIT,
    }


def _count_offering_task_current_week(conn, *, class_offering_id: int, task_type: str | None = None) -> int:
    cutoff = _week_start(_now()).isoformat()
    params: list[Any] = [int(class_offering_id), cutoff]
    task_filter = ""
    if task_type:
        task_filter = "AND task_type = ?"
        params.append(str(task_type))
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM ai_usage_log
            WHERE class_offering_id = ?
              AND created_at >= ?
              AND status <> 'deferred'
              {task_filter}
            """,
            tuple(params),
        ).fetchone()
    except Exception as exc:
        message = str(exc).lower()
        if "ai_usage_log" in message or "no such table" in message or "undefinedtable" in message:
            return 0
        raise
    return _safe_int(row["count"] if row else 0)


def _merge_usage_log_metadata(conn, *, usage_log_id: int, metadata: dict[str, Any]) -> None:
    if not usage_log_id:
        return
    row = conn.execute(
        "SELECT metadata_json FROM ai_usage_log WHERE id = ? LIMIT 1",
        (int(usage_log_id),),
    ).fetchone()
    if not row:
        return
    existing = _json_loads(row["metadata_json"], {})
    if not isinstance(existing, dict):
        existing = {}
    existing.update(metadata)
    conn.execute(
        "UPDATE ai_usage_log SET metadata_json = ? WHERE id = ?",
        (json.dumps(existing, ensure_ascii=False, sort_keys=True), int(usage_log_id)),
    )


def _task_label(task_type: str) -> str:
    return {
        "stage_exam_generation": "破境试炼出卷",
        "stage_exam_grading": "破境试炼批改",
        "assignment_grading": "作业 AI 批改",
        "behavior_profile": "行为画像",
        "material_mastery_check_generation": "心法检验生成",
        "weekly_report": "修行周报",
    }.get(str(task_type or ""), str(task_type or "AI 任务"))


def _notify_ai_budget_overage(
    conn,
    *,
    class_offering_id: int,
    task_type: str,
    weekly_count: int,
    weekly_budget: int,
    total_count: int,
    total_budget: int,
) -> int:
    week_key = _week_start(_now()).isoformat()
    offering = _load_offering_labels(conn, {int(class_offering_id)}).get(int(class_offering_id), {})
    recipients: dict[int, dict[str, Any]] = {}
    try:
        for teacher in list_super_admin_teachers(conn):
            recipients[int(teacher["id"])] = teacher
    except Exception:
        return 0
    teacher_id = _safe_int(offering.get("teacher_id"))
    if teacher_id:
        recipients.setdefault(teacher_id, {"id": teacher_id, "name": str(offering.get("teacher_name") or ""), "email": ""})
    if not recipients:
        return 0

    course_name = str(offering.get("course_name") or f"课堂 {class_offering_id}")
    class_name = str(offering.get("class_name") or "")
    task_name = _task_label(task_type)
    body = (
        f"{course_name}{(' · ' + class_name) if class_name else ''} 本周{task_name}已使用 "
        f"{weekly_count}/{weekly_budget} 次，总 AI 调用 {total_count}/{total_budget} 次。P0 任务已放行，请检查是否为正常高峰。"
    )
    inserted_count = 0
    for recipient in recipients.values():
        payload = _build_notification_payload(
            recipient_role="teacher",
            recipient_user_pk=int(recipient["id"]),
            category=MESSAGE_CATEGORY_AI_FEEDBACK,
            severity="warning",
            title="课程 AI 周预算已超额",
            body_preview=body,
            link_url="/manage/system/ai-usage",
            class_offering_id=int(class_offering_id),
            ref_type="ai_budget_overage",
            ref_id=f"{class_offering_id}:{task_type}:{week_key}",
            metadata={
                "class_offering_id": int(class_offering_id),
                "task_type": str(task_type),
                "weekly_count": int(weekly_count),
                "weekly_budget": int(weekly_budget),
                "total_count": int(total_count),
                "total_budget": int(total_budget),
                "week_start": week_key,
            },
        )
        inserted_count += 1 if _insert_notification_if_allowed(conn, payload) else 0
    return inserted_count


def mark_ai_usage_budget_overage_if_needed(
    conn,
    *,
    usage_log_id: int,
    class_offering_id: int | None,
    task_type: str,
    priority: str,
) -> dict[str, Any]:
    if not class_offering_id:
        return {"over_budget": False}
    budget = load_offering_ai_budget_config(conn, int(class_offering_id))["weekly_budget"]
    task_budget = _safe_int(budget.get(task_type), _safe_int(budget.get("total")))
    total_budget = _safe_int(budget.get("total"))
    weekly_count = _count_offering_task_current_week(conn, class_offering_id=int(class_offering_id), task_type=task_type)
    total_count = _count_offering_task_current_week(conn, class_offering_id=int(class_offering_id))
    over_task = task_budget >= 0 and weekly_count > task_budget
    over_total = total_budget >= 0 and total_count > total_budget
    if not over_task and not over_total:
        return {
            "over_budget": False,
            "weekly_count": weekly_count,
            "weekly_budget": task_budget,
            "total_count": total_count,
            "total_budget": total_budget,
        }

    payload = {
        "budget_overage": True,
        "budget_overage_task": str(task_type),
        "weekly_count": weekly_count,
        "weekly_budget": task_budget,
        "total_count": total_count,
        "total_budget": total_budget,
        "budget_checked_at": _now().isoformat(timespec="seconds"),
    }
    try:
        _merge_usage_log_metadata(conn, usage_log_id=int(usage_log_id), metadata=payload)
    except Exception:
        pass
    notification_count = 0
    if str(priority or "").upper() == "P0":
        try:
            notification_count = _notify_ai_budget_overage(
                conn,
                class_offering_id=int(class_offering_id),
                task_type=str(task_type),
                weekly_count=weekly_count,
                weekly_budget=task_budget,
                total_count=total_count,
                total_budget=total_budget,
            )
        except Exception:
            notification_count = 0
    return {**payload, "over_budget": True, "notification_count": notification_count}


def count_stage_exam_generations_last_24h(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
) -> int:
    cutoff = (_now() - timedelta(hours=24)).isoformat(timespec="seconds")
    count = 0
    attempt_ids: set[int] = set()
    try:
        rows = conn.execute(
            """
            SELECT id
            FROM learning_stage_exam_attempts
            WHERE class_offering_id = ?
              AND student_id = ?
              AND stage_key = ?
              AND generated_at >= ?
            """,
            (int(class_offering_id), int(student_id), str(stage_key), cutoff),
        ).fetchall()
        attempt_ids = {_safe_int(row["id"]) for row in rows if _safe_int(row["id"])}
        count += len(attempt_ids)
    except Exception:
        pass
    try:
        rows = conn.execute(
            """
            SELECT source_ref, metadata_json
            FROM ai_usage_log
            WHERE task_type = 'stage_exam_generation'
              AND class_offering_id = ?
              AND student_id = ?
              AND created_at >= ?
            """,
            (int(class_offering_id), int(student_id), cutoff),
        ).fetchall()
        for row in rows:
            source_ref = str(row["source_ref"] or "")
            if source_ref.startswith("stage-exam:"):
                source_attempt_id = _safe_int(source_ref.split(":", 1)[1])
                if source_attempt_id and source_attempt_id in attempt_ids:
                    continue
            metadata = _json_loads(row["metadata_json"], {})
            if isinstance(metadata, dict) and str(metadata.get("stage_key") or "") == str(stage_key):
                count += 1
    except Exception:
        pass
    return count


def ensure_stage_exam_generation_quota(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
) -> None:
    count = count_stage_exam_generations_last_24h(
        conn,
        class_offering_id=class_offering_id,
        student_id=student_id,
        stage_key=stage_key,
    )
    if count >= STAGE_EXAM_DAILY_LIMIT:
        raise AIUsageBudgetError("今日试炼机会已用完，灵力需要恢复，明日再来。")


def should_defer_low_priority_ai_task(conn, *, class_offering_id: int, task_type: str) -> bool:
    if not class_offering_id:
        return False
    budget = load_offering_ai_budget_config(conn, int(class_offering_id))["weekly_budget"]
    task_budget = _safe_int(budget.get(task_type), -1)
    if task_budget >= 0:
        return _count_offering_task_current_week(conn, class_offering_id=int(class_offering_id), task_type=task_type) >= task_budget
    total_budget = _safe_int(budget.get("total"), -1)
    if total_budget >= 0:
        return _count_offering_task_current_week(conn, class_offering_id=int(class_offering_id)) >= total_budget
    return False
