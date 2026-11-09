"""散修个人待办：新增/改期/完成/取消，以及到点自动唤醒。

The list is deliberately small and personal (3 slots at tier 0, +3 per tier).
Every AI turn may return a ``todos`` array; the backend validates and applies it
so the model never writes SQL. A to-do with a time is armed as a one-shot
scheduled task (persisted in ``scheduled_tasks``, so a server restart resumes
it); the wake handler runs one normal wanderer turn with the to-do highlighted,
and the model decides whether to finish, postpone or cancel it.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from ..db.connection import execute_insert_returning_id
from ..time_utils import local_iso, local_now, parse_datetime
from .scheduled_task_service import cancel_tasks_by_dedupe, schedule_task
from .wanderer_actions_common import ActionContext, as_int, record_action, run_turn
from .wanderer_identity_service import STATUS_ACTIVE, load_wanderer, log_wanderer_action
from .wanderer_life_core import todo_capacity

TASK_KIND_WANDERER_TODO_WAKE = "wanderer_todo_wake"
TODO_DEDUPE_PREFIX = "wanderer:todo:"
TODO_OPS_MAX = 5
TODO_TITLE_MAX = 80
TODO_DETAIL_MAX = 400
TODO_DONE_CULTIVATION = 0.8
TODO_DONE_DAILY_CAP = 3.0
TODO_WAKE_MIN_LEAD_SECONDS = 30


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _now(value: Any = None) -> datetime:
    return value if isinstance(value, datetime) else local_now()


def _local_naive(value: Any) -> datetime | None:
    """Parse a stored/ISO value as platform-local wall time when it carries a timezone."""
    moment = parse_datetime(value)
    if moment is None:
        return None
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo is not None else moment


def todo_dedupe_key(todo_id: Any) -> str:
    return f"{TODO_DEDUPE_PREFIX}{int(todo_id)}"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_todos(conn: Any, wanderer_id: int, *, status: str = "pending",
               limit: int | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT * FROM wanderer_todos WHERE wanderer_id = ? AND status = ? "
           "ORDER BY CASE WHEN remind_at IS NULL THEN 1 ELSE 0 END, remind_at, id")
    params: tuple[Any, ...] = (int(wanderer_id), str(status))
    if limit:
        sql += " LIMIT ?"
        params += (int(limit),)
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def pending_todo_count(conn: Any, wanderer_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_todos WHERE wanderer_id = ? AND status = 'pending'",
        (int(wanderer_id),),
    ).fetchone()
    return int(_row_value(row, "n", 0) or 0)


def _due_label(value: Any, now: datetime) -> str:
    moment = _local_naive(value)
    if moment is None:
        return "无定期"
    delta = moment - now
    if delta.total_seconds() < 0:
        return f"已到期（{moment.strftime('%m-%d %H:%M')}）"
    if delta <= timedelta(hours=1):
        return f"{max(1, int(delta.total_seconds() // 60))} 分钟后"
    if moment.date() == now.date():
        return f"今天 {moment.strftime('%H:%M')}"
    if moment.date() == (now + timedelta(days=1)).date():
        return f"明天 {moment.strftime('%H:%M')}"
    return moment.strftime("%m-%d %H:%M")


def todo_block(conn: Any, wanderer: dict[str, Any], *, wake_todo_id: int | None = None,
               now: Any = None) -> str:
    """The prompt block: the open list plus the reason for this wake."""
    moment = _now(now)
    capacity = todo_capacity(wanderer.get("tier"))
    todos = list_todos(conn, int(wanderer["id"]), limit=capacity)
    lines = [f"【你的待办】{len(todos)}/{capacity} 条。可以新增、改期、完成或取消，最多每轮 5 个操作。"]
    for item in todos:
        marker = " ← 本次唤醒" if wake_todo_id and int(item["id"]) == int(wake_todo_id) else ""
        detail = f"：{_clean(item.get('detail'), 120)}" if str(item.get("detail") or "").strip() else ""
        lines.append(f"- #{int(item['id'])} [{_due_label(item.get('remind_at') or item.get('due_at'), moment)}] "
                     f"{_clean(item.get('title'), 60)}{detail}{marker}")
    if not todos:
        lines.append("（清单是空的。想给自己留件事，就用 todos 的 create。）")
    if wake_todo_id:
        wake = next((item for item in todos if int(item["id"]) == int(wake_todo_id)), None)
        if wake:
            lines.append(
                f"【本次唤醒】你之前给自己定的「{_clean(wake.get('title'), 60)}」到点了。"
                "先想想要不要现在做；做不了就改期或取消，不要假装已经完成。"
            )
    lines.append("规则：做完用 complete，改期用 update（due_at 或 due_in_minutes），取消用 delete，新增用 create；"
                 "无定期的待办只在自省或自己想起来时处理。")
    return "\n".join(lines)


def todo_ops_contract(tier: Any = 0) -> str:
    return (
        "【可选：待办操作】如果这轮确实需要管理自己的待办，在 JSON 里加一个 todos 数组（最多 5 条）；没有就省略。\n"
        '{"todos": [{"op": "create|update|delete|complete", "id": 12, "title": "不超过 80 字",'
        ' "detail": "不超过 400 字", "due_at": "YYYY-MM-DD HH:MM", "due_in_minutes": 120}]}\n'
        f"每人最多 {todo_capacity(tier)} 条未完成待办；due_at 用平台本地时间，due_in_minutes 表示倒计时；"
        "都不给就是无定期。"
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _parse_due(entry: dict[str, Any], now: datetime) -> tuple[str, str | None, str | None]:
    """(schedule_kind, due_at, remind_at) from one op payload."""
    raw = entry.get("due_at")
    if raw not in (None, ""):
        moment = _local_naive(str(raw))
        if moment is None:
            return "invalid", None, None
        return "at", moment.isoformat(timespec="minutes"), moment.isoformat(timespec="seconds")
    saw_countdown = False
    for key, unit in (("due_in_minutes", 60), ("due_in_hours", 3600), ("due_in_days", 86400)):
        value = entry.get(key)
        if value in (None, ""):
            continue
        saw_countdown = True
        try:
            seconds = int(float(value) * unit)
        except (TypeError, ValueError):
            continue
        seconds = max(TODO_WAKE_MIN_LEAD_SECONDS, min(seconds, 366 * 86400))
        moment = now + timedelta(seconds=seconds)
        return "countdown", moment.isoformat(timespec="minutes"), moment.isoformat(timespec="seconds")
    return ("invalid", None, None) if saw_countdown else ("none", None, None)


def _arm_wake(conn: Any, todo: dict[str, Any], now: datetime) -> None:
    remind = _local_naive(todo.get("remind_at"))
    if todo.get("status") != "pending" or remind is None:
        cancel_tasks_by_dedupe(conn, todo_dedupe_key(todo["id"]))
        return
    run_at = max(now + timedelta(seconds=TODO_WAKE_MIN_LEAD_SECONDS), remind)
    try:
        schedule_task(
            conn,
            task_kind=TASK_KIND_WANDERER_TODO_WAKE,
            run_at=run_at,
            payload={"todo_id": int(todo["id"]), "wanderer_id": int(todo["wanderer_id"])},
            dedupe_key=todo_dedupe_key(todo["id"]),
            title=f"散修待办：{_clean(todo.get('title'), 60)}"[:120],
            replace=True,
        )
    except Exception as exc:  # noqa: BLE001 - the list must survive a scheduler hiccup
        print(f"[WANDERER TODO] 定时唤醒失败: {type(exc).__name__}: {exc}")


def _log_todo(conn: Any, wanderer_id: int, kind: str, todo: dict[str, Any], **detail: Any) -> None:
    try:
        log_wanderer_action(conn, wanderer_id, kind, target_type="wanderer_todo",
                            target_id=str(todo.get("id") or ""),
                            content_preview=_clean(todo.get("title"), 80),
                            detail={"schedule": todo.get("schedule_kind"), **detail})
    except Exception:  # noqa: BLE001 - the to-do itself is already written
        pass


def _award_completion(conn: Any, wanderer: dict[str, Any], *, now: datetime, todo_id: int) -> float:
    """Small cultivation for finishing a self-set to-do, capped per local day."""
    # Serialize the read/modify/write against concurrent tick/inbox lanes.
    from .wanderer_resource_service import lock_actor_resources
    lock_actor_resources(conn, int(wanderer['id']))
    if conn.execute('SELECT 1 FROM wanderer_cultivation_awards WHERE wanderer_id=? AND event_id=?',
                    (int(wanderer['id']),f'todo:{todo_id}:complete')).fetchone():
        return 0.0
    row = conn.execute("SELECT stats_json FROM wanderer_agents WHERE id = ?", (int(wanderer["id"]),)).fetchone()
    try:
        stats = json.loads(str(_row_value(row, "stats_json", 0) or "{}"))
    except (TypeError, ValueError):
        stats = {}
    today = now.date().isoformat()
    already = float(stats.get("todo_cult") or 0) if stats.get("todo_cult_day") == today else 0.0
    from .wanderer_cultivation_service import award_cultivation
    grant = award_cultivation(conn, int(wanderer['id']), TODO_DONE_CULTIVATION, 'todo',
                              f'todo:{todo_id}:complete', daily_cap=TODO_DONE_DAILY_CAP)
    if grant <= 0:
        return 0.0
    stats["todo_cult_day"] = today
    stats["todo_cult"] = round(already + grant, 2)
    conn.execute(
        "UPDATE wanderer_agents SET stats_json = ?, updated_at = ? "
        "WHERE id = ?",
        (json.dumps(stats, ensure_ascii=False, sort_keys=True), local_iso(), int(wanderer["id"])),
    )
    return grant


def apply_todo_ops(conn: Any, wanderer: dict[str, Any], ops: Any, *, now: Any = None,
                   source: str = "self") -> dict[str, Any]:
    """Validate and apply up to ``TODO_OPS_MAX`` operations. Never raises for bad input."""
    moment = _now(now)
    # Serialize capacity checks and writes for one wanderer (tick + inbox lanes).
    from .wanderer_resource_service import lock_actor_resources, capacity as resource_capacity, resize_capacity, release_capacity, ability_limit
    lock_actor_resources(conn, int(wanderer['id']))
    capacity = resource_capacity(conn, int(wanderer['id']), 'todo_capacity')['limit']
    title_max=int(ability_limit(conn,int(wanderer['id']),'todo_title_chars'))
    detail_max=int(ability_limit(conn,int(wanderer['id']),'todo_detail_chars'))
    items = [item for item in (ops if isinstance(ops, list) else []) if isinstance(item, dict)][:TODO_OPS_MAX]
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    completed = 0
    for entry in items:
        op = str(entry.get("op") or entry.get("action") or "").strip().lower()
        todo_id = as_int(entry.get("id"))
        if op == "create":
            title = _clean(entry.get("title"), title_max)
            if not title:
                errors.append({"op": op, "error": "missing_title"})
                continue
            if pending_todo_count(conn, int(wanderer["id"])) >= capacity:
                errors.append({"op": op, "error": "capacity"})
                continue
            kind, due_at, remind_at = _parse_due(entry, moment)
            if kind == "invalid":
                errors.append({"op": op, "error": "bad_due_at"})
                continue
            timestamp = local_iso()
            new_id = int(execute_insert_returning_id(
                conn,
                "INSERT INTO wanderer_todos (wanderer_id, title, detail, schedule_kind, due_at, remind_at, "
                "status, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (int(wanderer["id"]), title, _clean(entry.get("detail"), detail_max), kind, due_at,
                 remind_at, _clean(source, 20) or "self", timestamp, timestamp),
            ))
            todo = {"id": new_id, "wanderer_id": int(wanderer["id"]), "title": title,
                    "status": "pending", "remind_at": remind_at}
            allocated = resize_capacity(conn, int(wanderer['id']), 'todo_capacity', f'todo:{new_id}', 1)
            if not allocated['ok']:
                conn.execute('DELETE FROM wanderer_todos WHERE id=? AND wanderer_id=?', (new_id, int(wanderer['id'])))
                errors.append({'op':op,'error':'shared_capacity_exhausted'})
                continue
            _arm_wake(conn, todo, moment)
            _log_todo(conn, int(wanderer["id"]), "todo_create", todo)
            applied.append({"op": op, "id": new_id, "title": title, "schedule": kind})
            continue
        row = conn.execute(
            "SELECT * FROM wanderer_todos WHERE id = ? AND wanderer_id = ?",
            (todo_id, int(wanderer["id"])),
        ).fetchone() if todo_id else None
        if row is None:
            errors.append({"op": op or "?", "id": todo_id, "error": "not_found"})
            continue
        todo = dict(row)
        if op in {"delete", "cancel"}:
            release_capacity(conn, int(wanderer['id']), 'todo_capacity', f"todo:{todo['id']}")
            conn.execute(
                "UPDATE wanderer_todos SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (local_iso(), int(todo["id"])),
            )
            cancel_tasks_by_dedupe(conn, todo_dedupe_key(todo["id"]))
            _log_todo(conn, int(wanderer["id"]), "todo_delete", todo)
            applied.append({"op": "delete", "id": int(todo["id"])})
            continue
        if op == "complete":
            if todo.get("status") == "pending":
                release_capacity(conn, int(wanderer['id']), 'todo_capacity', f"todo:{todo['id']}")
                conn.execute(
                    "UPDATE wanderer_todos SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
                    (local_iso(), local_iso(), int(todo["id"])),
                )
                cancel_tasks_by_dedupe(conn, todo_dedupe_key(todo["id"]))
                _award_completion(conn, wanderer, now=moment, todo_id=int(todo['id']))
                _log_todo(conn, int(wanderer["id"]), "todo_complete", todo)
                completed += 1
                applied.append({"op": "complete", "id": int(todo["id"]), "title": todo.get("title")})
            continue
        if op == "update":
            if str(todo.get("status") or "") != "pending" and pending_todo_count(conn, int(wanderer["id"])) >= capacity:
                errors.append({"op": op, "id": int(todo["id"]), "error": "capacity"})
                continue
            title = _clean(entry.get("title"), title_max) if "title" in entry else str(todo.get("title") or "")
            detail = (_clean(entry.get("detail"), detail_max) if "detail" in entry
                      else str(todo.get("detail") or ""))
            if "due_at" in entry or any(key in entry for key in ("due_in_minutes", "due_in_hours", "due_in_days")):
                kind, due_at, remind_at = _parse_due(entry, moment)
                if kind == "invalid":
                    errors.append({"op": op, "id": int(todo["id"]), "error": "bad_due_at"})
                    continue
            else:
                kind, due_at, remind_at = (str(todo.get("schedule_kind") or "none"),
                                           todo.get("due_at"), todo.get("remind_at"))
            allocated = resize_capacity(conn, int(wanderer['id']), 'todo_capacity', f"todo:{todo['id']}", 1)
            if not allocated['ok']:
                errors.append({'op':op,'id':todo['id'],'error':'shared_capacity_exhausted'})
                continue
            conn.execute(
                "UPDATE wanderer_todos SET title = ?, detail = ?, schedule_kind = ?, due_at = ?, remind_at = ?, "
                "status = 'pending', updated_at = ? WHERE id = ?",
                (title or str(todo.get("title") or ""), detail, kind, due_at, remind_at,
                 local_iso(), int(todo["id"])),
            )
            todo.update({"title": title, "detail": detail, "schedule_kind": kind,
                         "due_at": due_at, "remind_at": remind_at, "status": "pending"})
            _arm_wake(conn, todo, moment)
            _log_todo(conn, int(wanderer["id"]), "todo_update", todo)
            applied.append({"op": "update", "id": int(todo["id"]), "title": title, "schedule": kind})
            continue
        errors.append({"op": op or "?", "id": todo_id, "error": "unknown_op"})
    return {"applied": applied, "errors": errors, "completed": completed}


# ---------------------------------------------------------------------------
# Wake
# ---------------------------------------------------------------------------

def _wake_scene(todo: dict[str, Any]) -> dict[str, Any]:
    title = _clean(todo.get("title"), 60)
    detail = _clean(todo.get("detail"), 240)
    return {
        "kind": "待办到点", "title": title, "meta": "这是你之前给自己定的，不是别人催你。",
        "body": detail, "tags": ["待办"],
        "task": "到点了。先判断还要不要做：要做就现在做、用 todos 的 complete 收尾；"
                "暂时做不了就改期或取消，也可以对自己说一句再 skip。不要假装已经完成。",
        "_wake_todo_id": int(todo["id"]),
    }


async def run_todo_wake(wanderer_id: int, todo_id: int, *, ai_caller: Any = None,
                        sleep: Any = None, conn_factory: Any = None) -> dict[str, Any]:
    from ..database import get_db_connection

    factory = conn_factory or get_db_connection
    from .wanderer_settings_service import is_wanderer_runtime_enabled

    with factory() as conn:
        if not is_wanderer_runtime_enabled(conn):
            return {"status": "skipped", "reason": "disabled"}
        wanderer = load_wanderer(conn, int(wanderer_id))
        row = conn.execute("SELECT * FROM wanderer_todos WHERE id = ? AND wanderer_id = ?",
                           (int(todo_id), int(wanderer_id))).fetchone()
    if wanderer is None or str(wanderer.get("status")) != STATUS_ACTIVE or row is None:
        return {"status": "skipped", "reason": "not_active"}
    todo = dict(row)
    if str(todo.get("status")) != "pending":
        return {"status": "skipped", "reason": "not_pending"}
    ctx = ActionContext(conn_factory=factory, wanderer=wanderer, ai_caller=ai_caller,
                        sleep=sleep or asyncio.sleep, special=True)
    from .wanderer_runtime_service import runtime_enabled, wake_wanderer
    with factory() as conn:
        autonomous = runtime_enabled(conn)
    if autonomous:
        result = await wake_wanderer(factory, wanderer_id, source='todo',
            event_key=f"todo:{todo_id}:{todo.get('remind_at') or todo.get('updated_at')}",
            context={'scope':{'kind':'private'},'todo':todo,'reason':'你给自己安排的提醒已到。'}, ai_caller=ai_caller)
    else:
        result = await run_turn(ctx, scene=_wake_scene(todo), thread_key=f"todo:{int(todo['id'])}",
                                suffix="todo_wake")
    calls = int(result.get("ai_calls") or 0)
    old_remind = todo.get("remind_at")
    with factory() as conn:
        if result.get("status") == "ok":
            # Keep a new reminder the model set during this wake (postpone), clear only the one that fired.
            conn.execute(
                "UPDATE wanderer_todos SET last_woke_at = ?, wake_count = wake_count + 1, "
                "remind_at = CASE WHEN remind_at = ? THEN NULL ELSE remind_at END, updated_at = ? WHERE id = ?",
                (local_iso(), old_remind, local_iso(), int(todo["id"])),
            )
        else:
            # A failed/capped turn must not silently drop the reminder: retry in half an hour.
            retry_at = (local_now() + timedelta(minutes=30)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE wanderer_todos SET last_woke_at = ?, remind_at = ?, updated_at = ? WHERE id = ?",
                (local_iso(), retry_at, local_iso(), int(todo["id"])),
            )
            _arm_wake(conn, {**todo, "remind_at": retry_at}, local_now())
        conn.commit()
    record_action(ctx, "todo_wake", target_type="wanderer_todo", target_id=int(todo["id"]),
                  content_preview=_clean(todo.get("title"), 80), ai_calls=calls,
                  outcome="ok" if result.get("status") == "ok" else "skipped",
                  detail={"status": result.get("status"), "action": (result.get("turn") or {}).get("action")})
    return {"status": result.get("status") or "ok", "todo_id": int(todo["id"]), "ai_calls": calls}


async def handle_wanderer_todo_wake(task: dict[str, Any], *, ai_caller: Any = None, sleep: Any = None,
                                    conn_factory: Any = None) -> str:
    payload = (task or {}).get("payload") or {}
    todo_id = as_int(payload.get("todo_id"))
    wanderer_id = as_int(payload.get("wanderer_id"))
    if todo_id <= 0 or wanderer_id <= 0:
        return "todo wake: skipped (missing ids)"
    result = await run_todo_wake(wanderer_id, todo_id, ai_caller=ai_caller, sleep=sleep,
                                 conn_factory=conn_factory)
    return f"todo wake: status={result.get('status')} todo={todo_id} ai_calls={result.get('ai_calls', 0)}"


def ensure_todo_wakes(conn: Any, *, now: Any = None) -> int:
    """Re-arm every pending timed to-do (idempotent; survives a server restart)."""
    from .wanderer_settings_service import is_wanderer_runtime_enabled

    if not is_wanderer_runtime_enabled(conn):
        return 0
    moment = _now(now)
    rows = conn.execute(
        "SELECT * FROM wanderer_todos WHERE status = 'pending' AND remind_at IS NOT NULL "
        "ORDER BY id LIMIT 2000"
    ).fetchall()
    for row in rows:
        _arm_wake(conn, dict(row), moment)
    return len(rows)


def todo_summary(conn: Any, wanderer: dict[str, Any], *, now: Any = None) -> dict[str, Any]:
    todos = list_todos(conn, int(wanderer["id"]))
    return {"capacity": todo_capacity(wanderer.get("tier")), "pending": len(todos),
            "next": [_due_label(item.get("remind_at") or item.get("due_at"), _now(now)) for item in todos[:3]]}
