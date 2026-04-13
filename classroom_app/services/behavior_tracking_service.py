from __future__ import annotations

import asyncio
import json
import random
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from queue import Empty, Full, Queue
from typing import Any, Optional

import sqlite3
import time

from ..config import (
    BEHAVIOR_WRITE_BATCH_SIZE,
    BEHAVIOR_WRITE_ENQUEUE_TIMEOUT_MS,
    BEHAVIOR_WRITE_FLUSH_INTERVAL_MS,
    BEHAVIOR_WRITE_QUEUE_SIZE,
    BEHAVIOR_WRITE_SYNC_TIMEOUT_MS,
)
from ..core import ai_client
from ..database import get_db_connection
from .psych_profile_service import (
    format_classroom_summary,
    format_short_timestamp,
    load_ai_class_config,
    load_classroom_snapshot,
    load_latest_hidden_profile,
    normalize_psych_profile_payload,
)
from .prompt_utils import (
    build_time_context_text,
    polite_address,
)

PROFILE_INTERVAL_MIN_SECONDS = 30 * 60
PROFILE_INTERVAL_MAX_SECONDS = 70 * 60
PROFILE_RETRY_BACKOFF_SECONDS = 5 * 60
HEARTBEAT_MAX_DELTA_SECONDS = 90
ACTIVE_PRESENCE_WINDOW_SECONDS = 150
PROFILE_SCHEDULER_POLL_SECONDS = 45
PROFILE_SCHEDULER_MAX_CONCURRENT = 1
BEHAVIOR_HISTORY_LIMIT = 48
LOGIN_AUDIT_HISTORY_LIMIT = 8

_scheduler_task: Optional[asyncio.Task] = None
_scheduler_stop_event: Optional[asyncio.Event] = None
_profile_tasks: set[asyncio.Task] = set()
_behavior_write_pipeline: Optional["_BehaviorWritePipeline"] = None
_behavior_write_pipeline_lock = threading.Lock()

BEHAVIOR_WRITE_MAX_RETRIES = 6
BEHAVIOR_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05


@dataclass(slots=True)
class _BehaviorWriteRequest:
    class_offering_id: int = 0
    user_pk: int = 0
    user_role: str = ""
    display_name: str = ""
    page_key: Optional[str] = None
    events: list[dict[str, Any]] | None = None
    session_started_at: Optional[str] = None
    future: Optional[Future] = None
    is_barrier: bool = False


class _BehaviorWritePipeline:
    def __init__(self) -> None:
        self._queue: Queue[_BehaviorWriteRequest] = Queue(maxsize=max(1, BEHAVIOR_WRITE_QUEUE_SIZE))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="behavior-write-worker",
            daemon=True,
        )
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._thread.start()
            self._started = True

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        if self._started and self._thread.is_alive():
            self._thread.join(timeout=max(timeout, 0.1))

    def submit(
        self,
        request: _BehaviorWriteRequest,
        *,
        wait: bool = False,
        timeout_ms: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        request.future = Future() if wait else None
        enqueue_timeout_seconds = max(
            float(timeout_ms if timeout_ms is not None else BEHAVIOR_WRITE_ENQUEUE_TIMEOUT_MS) / 1000.0,
            0.01,
        )
        self._queue.put(request, timeout=enqueue_timeout_seconds)
        if not wait or request.future is None:
            return None
        return request.future.result(timeout=max(float(BEHAVIOR_WRITE_SYNC_TIMEOUT_MS) / 1000.0, 0.1))

    def flush(self, timeout: float = 10.0) -> None:
        barrier = _BehaviorWriteRequest(is_barrier=True, future=Future())
        self._queue.put(barrier, timeout=max(timeout, 0.1))
        barrier.future.result(timeout=max(timeout, 0.1))

    def stats(self) -> dict[str, Any]:
        return {
            "alive": bool(self._thread.is_alive()),
            "queue_depth": int(self._queue.qsize()),
            "queue_capacity": int(BEHAVIOR_WRITE_QUEUE_SIZE),
        }

    def _run(self) -> None:
        flush_window_seconds = max(float(BEHAVIOR_WRITE_FLUSH_INTERVAL_MS) / 1000.0, 0.01)
        max_batch_size = max(1, int(BEHAVIOR_WRITE_BATCH_SIZE))

        while True:
            if self._stop_event.is_set() and self._queue.empty():
                return

            try:
                first_item = self._queue.get(timeout=0.5)
            except Empty:
                continue

            batch = [first_item]
            deadline = time.monotonic() + flush_window_seconds
            while len(batch) < max_batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=remaining))
                except Empty:
                    break

            while len(batch) < max_batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except Empty:
                    break

            self._flush_batch(batch)

    def _flush_batch(self, batch: list[_BehaviorWriteRequest]) -> None:
        pending = list(batch)
        try:
            for attempt in range(BEHAVIOR_WRITE_MAX_RETRIES):
                try:
                    outcomes: list[tuple[_BehaviorWriteRequest, Optional[dict[str, Any]], Optional[Exception]]] = []
                    with get_db_connection() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        for index, request in enumerate(pending):
                            if request.is_barrier:
                                outcomes.append((request, {"flushed": True}, None))
                                continue

                            savepoint_name = f"behavior_req_{index}"
                            conn.execute(f"SAVEPOINT {savepoint_name}")
                            try:
                                result = _record_behavior_batch_in_connection(
                                    conn,
                                    class_offering_id=request.class_offering_id,
                                    user_pk=request.user_pk,
                                    user_role=request.user_role,
                                    display_name=request.display_name,
                                    page_key=request.page_key,
                                    events=request.events or [],
                                    session_started_at=request.session_started_at,
                                )
                            except Exception as exc:
                                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                                conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                                outcomes.append((request, None, exc))
                            else:
                                conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                                outcomes.append((request, result, None))
                        conn.commit()

                    for request, result, exc in outcomes:
                        if request.future is not None and not request.future.done():
                            if exc is not None:
                                request.future.set_exception(exc)
                            else:
                                request.future.set_result(result or {})
                        elif exc is not None and not request.is_barrier:
                            print(f"[BEHAVIOR] 异步写入失败: {exc}")
                    return
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc).lower() or attempt >= BEHAVIOR_WRITE_MAX_RETRIES - 1:
                        for request in pending:
                            if request.future is not None and not request.future.done():
                                request.future.set_exception(exc)
                            elif not request.is_barrier:
                                print(f"[BEHAVIOR] 批量写入失败: {exc}")
                        return
                    delay = (
                        BEHAVIOR_WRITE_RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                        + random.uniform(0.0, BEHAVIOR_WRITE_RETRY_BASE_DELAY_SECONDS)
                    )
                    time.sleep(delay)
        finally:
            for _ in pending:
                self._queue.task_done()

def retry_on_locked(max_retries=5, base_delay=0.1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
                        time.sleep(delay)
                    else:
                        raise
            return None
        return wrapper
    return decorator


def _get_behavior_write_pipeline() -> _BehaviorWritePipeline:
    global _behavior_write_pipeline
    with _behavior_write_pipeline_lock:
        if _behavior_write_pipeline is None:
            _behavior_write_pipeline = _BehaviorWritePipeline()
            _behavior_write_pipeline.start()
        return _behavior_write_pipeline


def start_behavior_write_pipeline() -> None:
    pipeline = _get_behavior_write_pipeline()
    stats = pipeline.stats()
    print(
        "[BEHAVIOR] 异步写入管线已启动 "
        f"(queue_capacity={stats['queue_capacity']}, batch_size={int(BEHAVIOR_WRITE_BATCH_SIZE)})"
    )


def stop_behavior_write_pipeline(timeout: float = 10.0) -> None:
    global _behavior_write_pipeline
    with _behavior_write_pipeline_lock:
        pipeline = _behavior_write_pipeline
        _behavior_write_pipeline = None

    if pipeline is None:
        return

    try:
        pipeline.flush(timeout=max(timeout, 0.1))
    except Exception as exc:
        print(f"[BEHAVIOR] 停止前刷新写入管线失败: {exc}")
    pipeline.stop(timeout=timeout)
    print("[BEHAVIOR] 异步写入管线已停止")


def get_behavior_write_pipeline_stats() -> dict[str, Any]:
    pipeline = _behavior_write_pipeline
    if pipeline is None:
        return {
            "alive": False,
            "queue_depth": 0,
            "queue_capacity": int(BEHAVIOR_WRITE_QUEUE_SIZE),
        }
    return pipeline.stats()


def flush_behavior_write_pipeline(timeout: float = 10.0) -> None:
    pipeline = _behavior_write_pipeline
    if pipeline is None:
        return
    pipeline.flush(timeout=max(timeout, 0.1))


def _estimate_logged_event_count(events: list[dict[str, Any]]) -> int:
    accepted = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        event_type = _normalize_action_type(str(item.get("action_type") or "page_action"))
        summary_text = _truncate_text(item.get("summary_text") or "", 300)
        if event_type in {"presence_heartbeat", "page_presence", "heartbeat"} and not summary_text:
            continue
        accepted += 1
    return accepted


def _build_queued_snapshot(events: list[dict[str, Any]]) -> dict[str, Any]:
    accepted_count = _estimate_logged_event_count(events)
    snapshot = {
        "accepted_event_count": accepted_count,
        "logged_event_ids": [],
        "queued_event_count": accepted_count,
        "seconds_until_next_profile": None,
        "write_mode": "queued",
        "degraded": False,
    }
    if accepted_count == 1:
        snapshot["event_id"] = None
    return snapshot


def _build_degraded_snapshot(events: list[dict[str, Any]], exc: Exception) -> dict[str, Any]:
    snapshot = _build_queued_snapshot(events)
    snapshot.update(
        {
            "accepted_event_count": 0,
            "logged_event_ids": [],
            "queued_event_count": 0,
            "write_mode": "degraded",
            "degraded": True,
            "degraded_reason": str(exc),
        }
    )
    if "event_id" in snapshot:
        snapshot["event_id"] = None
    return snapshot


def _submit_behavior_write(
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    page_key: Optional[str],
    events: list[dict[str, Any]],
    session_started_at: Optional[str],
    wait: bool = False,
) -> dict[str, Any]:
    normalized_events = [item for item in events if isinstance(item, dict)]
    if not normalized_events:
        empty_snapshot = {
            "accepted_event_count": 0,
            "logged_event_ids": [],
            "queued_event_count": 0,
            "seconds_until_next_profile": None,
            "write_mode": "noop",
            "degraded": False,
        }
        return empty_snapshot

    request = _BehaviorWriteRequest(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        display_name=display_name,
        page_key=page_key,
        events=normalized_events,
        session_started_at=session_started_at,
    )
    try:
        result = _get_behavior_write_pipeline().submit(request, wait=wait)
        if result is None:
            return _build_queued_snapshot(normalized_events)
        result.setdefault("write_mode", "synced")
        result.setdefault("degraded", False)
        return result
    except Full as exc:
        print(f"[BEHAVIOR] 写入队列已满，放弃本次行为记录: {exc}")
        if wait:
            raise
        return _build_degraded_snapshot(normalized_events, exc)
    except FutureTimeoutError as exc:
        print(f"[BEHAVIOR] 等待行为写入结果超时: {exc}")
        if wait:
            raise
        return _build_queued_snapshot(normalized_events)
    except Exception as exc:
        print(f"[BEHAVIOR] 提交行为写入失败: {exc}")
        if wait:
            raise
        return _build_degraded_snapshot(normalized_events, exc)


def build_random_profile_interval_seconds() -> int:
    return random.randint(PROFILE_INTERVAL_MIN_SECONDS, PROFILE_INTERVAL_MAX_SECONDS)


def _normalize_action_type(action_type: str) -> str:
    raw_value = str(action_type or "page_action").strip().lower().replace("-", "_").replace(" ", "_")
    return raw_value[:64] or "page_action"


def _truncate_text(text: Any, limit: int = 160) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _dump_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _safe_json_loads(raw_value: Any) -> Optional[dict[str, Any]]:
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_row_value(row: Any, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _latest_timestamp_text(*values: Any, fallback: str) -> str:
    latest_dt: Optional[datetime] = None
    for value in values:
        parsed = _parse_iso_datetime(value)
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
    return latest_dt.isoformat() if latest_dt else fallback


def _build_next_profile_due_at(anchor_text: Optional[str], interval_seconds: int, *, fallback: str) -> str:
    anchor_dt = _parse_iso_datetime(anchor_text) or _parse_iso_datetime(fallback) or datetime.now()
    return (anchor_dt + timedelta(seconds=max(0, int(interval_seconds or 0)))).isoformat()


def _seconds_until_due_timestamp(value: Any) -> int:
    due_dt = _parse_iso_datetime(value)
    if due_dt is None:
        return 0
    now_dt = datetime.now(due_dt.tzinfo) if due_dt.tzinfo else datetime.now()
    return max(0, int((due_dt - now_dt).total_seconds()))


def _should_start_new_behavior_session(current_anchor_text: Any, session_started_at_text: Optional[str]) -> bool:
    next_anchor = _parse_iso_datetime(session_started_at_text)
    if next_anchor is None:
        return False
    current_anchor = _parse_iso_datetime(current_anchor_text)
    if current_anchor is None:
        return True
    return next_anchor > current_anchor + timedelta(seconds=1)


@retry_on_locked()
def _ensure_behavior_state_row(
    conn,
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    now: str,
    session_started_at: Optional[str] = None,
    last_presence_at: Optional[str] = None,
    page_key: Optional[str] = None,
) -> None:
    state = conn.execute(
        """
        SELECT current_session_started_at, last_profiled_at,
               next_profile_interval_seconds, next_profile_due_at
        FROM classroom_behavior_states
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        LIMIT 1
        """,
        (class_offering_id, user_pk, user_role),
    ).fetchone()

    if not state:
        next_interval = build_random_profile_interval_seconds()
        anchor_text = str(session_started_at or now)
        next_due_at = _build_next_profile_due_at(anchor_text, next_interval, fallback=now)
        conn.execute(
            """
            INSERT INTO classroom_behavior_states (
                class_offering_id, user_pk, user_role, total_activity_count,
                last_profiled_activity_count, profile_generation_pending,
                last_event_at, next_profile_interval_seconds, next_profile_due_at,
                current_session_started_at, last_presence_at, last_page_key,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 0, 0, 0, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                class_offering_id,
                user_pk,
                user_role,
                next_interval,
                next_due_at,
                anchor_text,
                last_presence_at,
                page_key,
                now,
                now,
            ),
        )
        return

    if _should_start_new_behavior_session(
        _safe_row_value(state, "current_session_started_at"),
        session_started_at,
    ):
        next_interval = build_random_profile_interval_seconds()
        next_due_at = _build_next_profile_due_at(session_started_at, next_interval, fallback=now)
        conn.execute(
            """
            UPDATE classroom_behavior_states
            SET current_session_started_at = ?,
                last_presence_at = COALESCE(?, last_presence_at),
                last_page_key = COALESCE(?, last_page_key),
                profile_generation_pending = 0,
                next_profile_interval_seconds = ?,
                next_profile_due_at = ?,
                online_accumulated_seconds = 0,
                focus_total_seconds = 0,
                blur_total_seconds = 0,
                visible_total_seconds = 0,
                hidden_total_seconds = 0,
                discussion_lurk_total_seconds = 0,
                ai_panel_open_total_seconds = 0,
                last_visibility_state = NULL,
                last_focus_state = NULL,
                last_idle_seconds = 0,
                updated_at = ?
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            """,
            (
                session_started_at,
                last_presence_at,
                page_key,
                next_interval,
                next_due_at,
                now,
                class_offering_id,
                user_pk,
                user_role,
            ),
        )
        return

    current_interval = int(_safe_row_value(state, "next_profile_interval_seconds") or 0)
    if current_interval <= 0:
        current_interval = build_random_profile_interval_seconds()

    current_anchor = str(_safe_row_value(state, "current_session_started_at") or session_started_at or now)
    current_due_at = str(_safe_row_value(state, "next_profile_due_at") or "").strip()
    if not current_due_at:
        due_anchor_text = _latest_timestamp_text(
            _safe_row_value(state, "last_profiled_at"),
            current_anchor,
            fallback=current_anchor,
        )
        current_due_at = _build_next_profile_due_at(due_anchor_text, current_interval, fallback=now)

    conn.execute(
        """
        UPDATE classroom_behavior_states
        SET current_session_started_at = COALESCE(current_session_started_at, ?),
            last_presence_at = COALESCE(?, last_presence_at),
            last_page_key = COALESCE(?, last_page_key),
            next_profile_interval_seconds = ?,
            next_profile_due_at = ?,
            updated_at = ?
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        """,
        (
            current_anchor,
            last_presence_at,
            page_key,
            current_interval,
            current_due_at,
            now,
            class_offering_id,
            user_pk,
            user_role,
        ),
    )


def _load_behavior_state_snapshot(
    conn,
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM classroom_behavior_states
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        LIMIT 1
        """,
        (class_offering_id, user_pk, user_role),
    ).fetchone()
    snapshot = dict(row) if row else {}
    remaining = _seconds_until_due_timestamp(snapshot.get("next_profile_due_at"))
    if remaining <= 0 and not snapshot.get("next_profile_due_at"):
        remaining = int(snapshot.get("next_profile_interval_seconds") or 0) - int(snapshot.get("online_accumulated_seconds") or 0)
    snapshot["seconds_until_next_profile"] = max(0, remaining)
    return snapshot


def _record_behavior_batch_in_connection(
    conn,
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    page_key: Optional[str],
    events: list[dict[str, Any]],
    session_started_at: Optional[str] = None,
) -> dict[str, Any]:
    now = datetime.now()
    now_text = now.isoformat()
    logged_event_ids: list[int] = []

    _ensure_behavior_state_row(
        conn,
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        now=now_text,
        session_started_at=session_started_at,
        last_presence_at=now_text,
        page_key=page_key,
    )

    for item in events:
        if not isinstance(item, dict):
            continue

        event_type = _normalize_action_type(str(item.get("action_type") or "page_action"))
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        summary_text = _truncate_text(item.get("summary_text") or "", 300)
        item_page_key = str(item.get("page_key") or page_key or "").strip() or None

        if event_type in {"presence_heartbeat", "page_presence", "heartbeat"}:
            visibility_state = "visible" if str(payload.get("visibility_state") or "visible") == "visible" else "hidden"
            focus_state = "focused" if bool(payload.get("focused", True)) else "blurred"
            _apply_presence_heartbeat(
                conn,
                class_offering_id=class_offering_id,
                user_pk=user_pk,
                user_role=user_role,
                now=now,
                page_key=item_page_key,
                visibility_state=visibility_state,
                focus_state=focus_state,
                idle_seconds=int(payload.get("idle_seconds") or 0),
                ai_panel_open=bool(payload.get("ai_panel_open")),
            )
            if not summary_text:
                continue

        cursor = conn.execute(
            """
            INSERT INTO classroom_behavior_events (
                class_offering_id, user_pk, user_role, display_name,
                action_type, summary_text, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                class_offering_id,
                user_pk,
                user_role,
                display_name,
                event_type,
                summary_text or event_type,
                _dump_payload(payload),
                now_text,
            ),
        )
        logged_event_ids.append(int(cursor.lastrowid))
        conn.execute(
            """
            UPDATE classroom_behavior_states
            SET total_activity_count = total_activity_count + 1,
                last_event_at = ?,
                last_page_key = COALESCE(?, last_page_key),
                updated_at = ?
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            """,
            (
                now_text,
                item_page_key,
                now_text,
                class_offering_id,
                user_pk,
                user_role,
            ),
        )

    snapshot = _load_behavior_state_snapshot(
        conn,
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
    )
    snapshot["logged_event_ids"] = logged_event_ids
    snapshot["accepted_event_count"] = len(logged_event_ids)
    return snapshot


def record_behavior_event(
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    action_type: str,
    summary_text: str,
    payload: Optional[dict[str, Any]] = None,
    page_key: Optional[str] = None,
    session_started_at: Optional[str] = None,
    wait: bool = False,
) -> dict[str, Any]:
    snapshot = _submit_behavior_write(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        display_name=display_name,
        page_key=page_key,
        events=[
            {
                "action_type": action_type,
                "summary_text": summary_text,
                "payload": payload or {},
                "page_key": page_key,
            }
        ],
        session_started_at=session_started_at,
        wait=wait,
    )
    if snapshot.get("event_id") is None and snapshot.get("logged_event_ids"):
        snapshot["event_id"] = int(snapshot["logged_event_ids"][0])
    else:
        snapshot.setdefault("event_id", None)
    return snapshot


def _apply_presence_heartbeat(
    conn,
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    now: datetime,
    page_key: Optional[str],
    visibility_state: str,
    focus_state: str,
    idle_seconds: int,
    ai_panel_open: bool,
) -> None:
    state = conn.execute(
        """
        SELECT last_presence_at, current_session_started_at
        FROM classroom_behavior_states
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        LIMIT 1
        """,
        (class_offering_id, user_pk, user_role),
    ).fetchone()

    now_text = now.isoformat()
    previous_presence_at = str(state["last_presence_at"] or "") if state else ""
    previous_session_started_at = str(state["current_session_started_at"] or "") if state else ""

    raw_delta_seconds = 0
    delta_seconds = 0
    if previous_presence_at:
        try:
            previous_dt = datetime.fromisoformat(previous_presence_at)
        except ValueError:
            previous_dt = now
        raw_delta_seconds = int(max(0, (now - previous_dt).total_seconds()))
        delta_seconds = int(max(0, min(raw_delta_seconds, HEARTBEAT_MAX_DELTA_SECONDS)))

    is_new_session = (not previous_presence_at) or raw_delta_seconds > ACTIVE_PRESENCE_WINDOW_SECONDS
    session_started_at = previous_session_started_at or now_text
    effective_delta = 0 if is_new_session else delta_seconds
    capped_idle_seconds = max(0, min(int(idle_seconds or 0), HEARTBEAT_MAX_DELTA_SECONDS))
    is_active = effective_delta > 0 and capped_idle_seconds < ACTIVE_PRESENCE_WINDOW_SECONDS

    online_delta = effective_delta if is_active else 0
    focus_delta = effective_delta if focus_state == "focused" else 0
    blur_delta = effective_delta if focus_state != "focused" else 0
    visible_delta = effective_delta if visibility_state == "visible" else 0
    hidden_delta = effective_delta if visibility_state != "visible" else 0
    lurk_delta = 0
    if page_key == "classroom_discussion" and visibility_state == "visible" and focus_state != "focused":
        lurk_delta = effective_delta
    ai_panel_delta = effective_delta if ai_panel_open else 0

    conn.execute(
        """
        UPDATE classroom_behavior_states
        SET current_session_started_at = ?,
            last_presence_at = ?,
            last_page_key = COALESCE(?, last_page_key),
            last_visibility_state = ?,
            last_focus_state = ?,
            last_idle_seconds = ?,
            online_accumulated_seconds = online_accumulated_seconds + ?,
            focus_total_seconds = focus_total_seconds + ?,
            blur_total_seconds = blur_total_seconds + ?,
            visible_total_seconds = visible_total_seconds + ?,
            hidden_total_seconds = hidden_total_seconds + ?,
            discussion_lurk_total_seconds = discussion_lurk_total_seconds + ?,
            ai_panel_open_total_seconds = ai_panel_open_total_seconds + ?,
            updated_at = ?
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        """,
        (
            session_started_at,
            now_text,
            page_key,
            visibility_state,
            focus_state,
            capped_idle_seconds,
            online_delta,
            focus_delta,
            blur_delta,
            visible_delta,
            hidden_delta,
            lurk_delta,
            ai_panel_delta,
            now_text,
            class_offering_id,
            user_pk,
            user_role,
        ),
    )


def record_behavior_batch(
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    page_key: Optional[str],
    events: list[dict[str, Any]],
    session_started_at: Optional[str] = None,
    wait: bool = False,
) -> dict[str, Any]:
    return _submit_behavior_write(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        display_name=display_name,
        page_key=page_key,
        events=events,
        session_started_at=session_started_at,
        wait=wait,
    )


def _format_duration_minutes(total_seconds: Any) -> str:
    seconds = max(0, int(total_seconds or 0))
    minutes = seconds // 60
    if minutes <= 0:
        return "不足1分钟"
    return f"{minutes}分钟"


def _build_recent_activity_transcript(rows: list[Any]) -> str:
    lines: list[str] = []
    for row in reversed(rows):
        timestamp_text = format_short_timestamp(row["created_at"])
        summary_text = str(row["summary_text"] or "").strip()
        if not summary_text:
            continue
        lines.append(f"{timestamp_text} {summary_text}".strip())
    return "\n".join(lines)


def _build_login_audit_summary(rows: list[Any]) -> str:
    if not rows:
        return "暂无近期登录审计记录。"

    lines: list[str] = []
    for row in rows:
        logged_at = _safe_row_value(row, "logged_at")
        parts = [format_short_timestamp(logged_at) or str(logged_at or "")]
        ip_address = _safe_row_value(row, "ip_address")
        if ip_address:
            parts.append(f"IP:{ip_address}")
        device_parts = [
            str(_safe_row_value(row, key) or "").strip()
            for key in ("device_type", "os_name", "browser_name")
            if str(_safe_row_value(row, key) or "").strip()
        ]
        if device_parts:
            parts.append("/".join(device_parts))
        device_label = _safe_row_value(row, "device_label")
        if device_label:
            parts.append(str(device_label))
        lines.append(" | ".join(part for part in parts if part))
    return "\n".join(lines)


def _build_presence_summary(state: dict[str, Any]) -> str:
    if not state:
        return "暂无在线行为汇总。"

    parts = [
        "最近在线累计: " + _format_duration_minutes(state.get("online_accumulated_seconds")),
        "聚焦时长: " + _format_duration_minutes(state.get("focus_total_seconds")),
        "失焦时长: " + _format_duration_minutes(state.get("blur_total_seconds")),
        "页面可见时长: " + _format_duration_minutes(state.get("visible_total_seconds")),
        "页面隐藏时长: " + _format_duration_minutes(state.get("hidden_total_seconds")),
    ]
    if state.get("discussion_lurk_total_seconds"):
        parts.append("课堂潜水估计时长: " + _format_duration_minutes(state.get("discussion_lurk_total_seconds")))
    if state.get("ai_panel_open_total_seconds"):
        parts.append("AI面板停留时长: " + _format_duration_minutes(state.get("ai_panel_open_total_seconds")))
    if state.get("last_page_key"):
        parts.append("最近页面: " + str(state.get("last_page_key")))
    if state.get("last_visibility_state"):
        parts.append("最近可见状态: " + str(state.get("last_visibility_state")))
    if state.get("last_focus_state"):
        parts.append("最近焦点状态: " + str(state.get("last_focus_state")))
    return "\n".join(parts)


def _load_user_profile_seed(conn, user_pk: int, user_role: str) -> tuple[str, str]:
    if user_role == "teacher":
        row = conn.execute(
            "SELECT name, description FROM teachers WHERE id = ? LIMIT 1",
            (user_pk,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name, description FROM students WHERE id = ? LIMIT 1",
            (user_pk,),
        ).fetchone()

    if not row:
        return "", ""
    return str(row["name"] or ""), str(row["description"] or "")


def _refresh_cached_ai_session_contexts(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    format_system_prompt_func,
) -> None:
    try:
        refreshed_context_prompt = format_system_prompt_func(user_pk, user_role, class_offering_id)
    except Exception as exc:
        print(f"[BEHAVIOR_PROFILE] 刷新 AI 会话上下文失败: {exc}")
        return

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE ai_chat_sessions
            SET context_prompt = ?
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            """,
            (refreshed_context_prompt, class_offering_id, user_pk, user_role),
        )
        conn.commit()


def _next_profile_round_index(
    conn,
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(round_index), 0) AS max_round
        FROM classroom_behavior_profiles
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        """,
        (class_offering_id, user_pk, user_role),
    ).fetchone()
    return int(row["max_round"] or 0) + 1


def _load_recent_login_audits(conn, *, user_pk: int, user_role: str) -> list[Any]:
    if user_role != "student":
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT logged_at, ip_address, user_agent,
                   device_type, os_name, browser_name, device_label
            FROM student_login_audit_logs
            WHERE student_id = ?
            ORDER BY logged_at DESC, id DESC
            LIMIT ?
            """,
            (user_pk, LOGIN_AUDIT_HISTORY_LIMIT),
        ).fetchall()
    ]


def _build_behavior_profile_prompt(
    *,
    class_summary: str,
    class_ai_config: dict[str, str],
    user_name: str,
    user_role: str,
    current_description: str,
    previous_hidden_profile: Optional[dict[str, Any]],
    behavior_transcript: str,
    presence_summary: str,
    login_audit_summary: str,
) -> str:
    previous_profile_lines = ["这是首次全局行为侧写。"]
    if previous_hidden_profile:
        previous_profile_lines = [
            "上一轮侧写摘要: " + str(previous_hidden_profile.get("profile_summary") or "无"),
            "上一轮情绪状态: " + str(previous_hidden_profile.get("mental_state_summary") or "无"),
            "上一轮支持策略: " + str(previous_hidden_profile.get("support_strategy") or "无"),
            "上一轮性格推测: " + str(previous_hidden_profile.get("personality_traits") or "无"),
            "上一轮偏好推测: " + str(previous_hidden_profile.get("preference_summary") or "无"),
            "上一轮语言习惯: " + str(previous_hidden_profile.get("language_habit_summary") or "无"),
            "上一轮偏好AI风格: " + str(previous_hidden_profile.get("preferred_ai_style") or "无"),
        ]

    json_schema_lines = [
        "{",
        '  "user_profile_summary": "100字以内，描述稳定的学习与互动画像",',
        '  "mental_state_summary": "80字以内，描述当前情绪、压力、动力迹象，证据不足时保持中性",',
        '  "support_strategy": "120字以内，给主AI和教师的支持策略",',
        '  "hidden_premise_prompt": "供主AI内部使用的隐藏前提，不得暴露侧写来源",',
        '  "personality_traits": "60字以内，谨慎描述性格倾向",',
        '  "preference_summary": "80字以内，推测内容偏好、任务偏好或互动偏好",',
        '  "language_habit_summary": "80字以内，推测表达风格和用语习惯",',
        '  "preferred_ai_style": "60字以内，推测更适合该用户的AI回应方式",',
        '  "interest_hypothesis": "80字以内，推测兴趣点或关注主题",',
        '  "evidence_summary": "120字以内，总结支撑以上判断的关键行为证据",',
        '  "confidence": "low|medium|high"',
        "}",
    ]

    sections = [
        "你是一名课堂场景下的隐藏心理侧写分析师。",
        "目标是基于多维行为日志，给课堂AI助手生成内部可用但绝不外显的用户画像。",
        "要求：",
        "1. 只能做谨慎、非诊断式推测，禁止医疗化和夸张结论。",
        "2. 必须综合近期行为、登录习惯、页面停留、课堂聊天、AI提问、作业操作，不要只看单一事件。",
        "3. hidden_premise_prompt 必须强调：不暴露侧写，不说后台分析，先共情再引导，优先帮助学习。",
        "4. 需要同时覆盖情绪、性格、喜好、语言习惯、偏好的AI风格等维度。",
        "5. 只返回合法 JSON，不要返回 Markdown，不要补充解释。",
        '6. 在 hidden_premise_prompt 中，教师称呼用"X老师"（X为姓氏），学生用"X同学"，不要直呼全名。',
        "",
        "输出 JSON 结构：",
        "\n".join(json_schema_lines),
        "",
        "【课堂信息】",
        class_summary,
        "",
        "【教师AI配置】",
        "System Prompt:",
        class_ai_config.get("system_prompt") or "（无）",
        "",
        "Syllabus / RAG:",
        class_ai_config.get("syllabus") or "（无）",
        "",
        "【当前环境】",
        build_time_context_text(),
        "",
        "【当前用户】",
        "姓名: " + (user_name or "未知"),
        "礼貌称呼: " + polite_address(user_name or "未知", user_role),
        "角色: " + ("教师" if user_role == "teacher" else "学生"),
        "现有长期描述: " + (current_description or "暂无，请结合行为谨慎生成"),
        "",
        "【上一轮隐藏侧写】",
        "\n".join(previous_profile_lines),
        "",
        "【在线与页面行为汇总】",
        presence_summary or "暂无在线状态数据。",
        "",
        "【登录审计】",
        login_audit_summary or "暂无登录审计记录。",
        "",
        "【近期多维行为日志】",
        behavior_transcript or "暂无近期行为日志。",
    ]
    return "\n".join(part for part in sections if part is not None).strip()


def _finalize_profile_generation(
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    success: bool,
) -> None:
    now_text = datetime.now().isoformat()
    with get_db_connection() as conn:
        state = conn.execute(
            """
            SELECT total_activity_count
            FROM classroom_behavior_states
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            LIMIT 1
            """,
            (class_offering_id, user_pk, user_role),
        ).fetchone()
        total_activity_count = int(state["total_activity_count"] or 0) if state else 0

        if success:
            next_interval = build_random_profile_interval_seconds()
            next_due_at = _build_next_profile_due_at(now_text, next_interval, fallback=now_text)
            conn.execute(
                """
                UPDATE classroom_behavior_states
                SET last_profiled_activity_count = ?,
                    last_profiled_at = ?,
                    profile_generation_pending = 0,
                    online_accumulated_seconds = 0,
                    focus_total_seconds = 0,
                    blur_total_seconds = 0,
                    visible_total_seconds = 0,
                    hidden_total_seconds = 0,
                    discussion_lurk_total_seconds = 0,
                    ai_panel_open_total_seconds = 0,
                    last_idle_seconds = 0,
                    next_profile_interval_seconds = ?,
                    next_profile_due_at = ?,
                    updated_at = ?
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                """,
                (
                    total_activity_count,
                    now_text,
                    next_interval,
                    next_due_at,
                    now_text,
                    class_offering_id,
                    user_pk,
                    user_role,
                ),
            )
        else:
            retry_due_at = _build_next_profile_due_at(now_text, PROFILE_RETRY_BACKOFF_SECONDS, fallback=now_text)
            conn.execute(
                """
                UPDATE classroom_behavior_states
                SET profile_generation_pending = 0,
                    next_profile_interval_seconds = ?,
                    next_profile_due_at = ?,
                    updated_at = ?
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                """,
                (
                    PROFILE_RETRY_BACKOFF_SECONDS,
                    retry_due_at,
                    now_text,
                    class_offering_id,
                    user_pk,
                    user_role,
                ),
            )
        conn.commit()


async def generate_behavior_profile_for_user(
    *,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    trigger_mode: str = "scheduled",
    trigger_event_id: Optional[int] = None,
) -> bool:
    from ..routers.ai import format_system_prompt

    success = False
    try:
        with get_db_connection() as conn:
            class_snapshot = load_classroom_snapshot(conn, class_offering_id)
            class_ai_config = load_ai_class_config(conn, class_offering_id)
            latest_hidden_profile = load_latest_hidden_profile(conn, class_offering_id, user_pk, user_role)
            user_name, current_description = _load_user_profile_seed(conn, user_pk, user_role)
            state_snapshot = _load_behavior_state_snapshot(
                conn,
                class_offering_id=class_offering_id,
                user_pk=user_pk,
                user_role=user_role,
            )
            recent_events = conn.execute(
                """
                SELECT id, action_type, summary_text, payload_json, created_at
                FROM classroom_behavior_events
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (class_offering_id, user_pk, user_role, BEHAVIOR_HISTORY_LIMIT),
            ).fetchall()
            recent_logins = _load_recent_login_audits(conn, user_pk=user_pk, user_role=user_role)
            round_index = _next_profile_round_index(
                conn,
                class_offering_id=class_offering_id,
                user_pk=user_pk,
                user_role=user_role,
            )

        behavior_transcript = _build_recent_activity_transcript(list(recent_events))
        if not behavior_transcript.strip():
            return False

        profile_prompt = _build_behavior_profile_prompt(
            class_summary=format_classroom_summary(class_snapshot),
            class_ai_config=class_ai_config,
            user_name=user_name,
            user_role=user_role,
            current_description=current_description,
            previous_hidden_profile=latest_hidden_profile,
            behavior_transcript=behavior_transcript,
            presence_summary=_build_presence_summary(state_snapshot),
            login_audit_summary=_build_login_audit_summary(list(recent_logins)),
        )

        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": "你是一名资深心理侧写分析师，只允许输出合法 JSON。",
                "messages": [],
                "new_message": profile_prompt,
                "model_capability": "thinking",
                "response_format": "json",
                "task_priority": "background",
                "task_label": "behavior_profile",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        response_data = response.json()
        if response_data.get("status") != "success":
            raise RuntimeError(f"AI 返回失败: {response_data}")

        payload = response_data.get("response_json")
        if not isinstance(payload, dict):
            raise RuntimeError(f"AI 未返回有效 JSON: {payload}")

        normalized = normalize_psych_profile_payload(payload)
        if not any(
            normalized[key]
            for key in (
                "profile_summary",
                "mental_state_summary",
                "support_strategy",
                "hidden_premise_prompt",
                "personality_traits",
                "preferred_ai_style",
            )
        ):
            raise RuntimeError("侧写结果为空")

        created_at = datetime.now().isoformat()
        with get_db_connection() as conn:
            state_row = conn.execute(
                """
                SELECT total_activity_count
                FROM classroom_behavior_states
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                LIMIT 1
                """,
                (class_offering_id, user_pk, user_role),
            ).fetchone()
            activity_count_snapshot = int(state_row["total_activity_count"] or 0) if state_row else 0

            conn.execute(
                """
                INSERT INTO classroom_behavior_profiles (
                    class_offering_id, user_pk, user_role, trigger_event_id, round_index,
                    activity_count_snapshot, profile_summary, mental_state_summary,
                    support_strategy, hidden_premise_prompt, personality_traits,
                    preference_summary, language_habit_summary, preferred_ai_style,
                    interest_hypothesis, evidence_summary, trigger_mode, confidence,
                    raw_payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    class_offering_id,
                    user_pk,
                    user_role,
                    trigger_event_id,
                    round_index,
                    activity_count_snapshot,
                    normalized["profile_summary"],
                    normalized["mental_state_summary"],
                    normalized["support_strategy"],
                    normalized["hidden_premise_prompt"],
                    normalized["personality_traits"],
                    normalized["preference_summary"],
                    normalized["language_habit_summary"],
                    normalized["preferred_ai_style"],
                    normalized["interest_hypothesis"],
                    normalized["evidence_summary"],
                    trigger_mode,
                    normalized["confidence"],
                    _dump_payload(payload),
                    created_at,
                ),
            )

            if normalized["profile_summary"]:
                table_name = "teachers" if user_role == "teacher" else "students"
                conn.execute(
                    f"UPDATE {table_name} SET description = ? WHERE id = ?",
                    (normalized["profile_summary"], user_pk),
                )

            conn.commit()

        _refresh_cached_ai_session_contexts(
            class_offering_id,
            user_pk,
            user_role,
            format_system_prompt,
        )
        success = True
        print(
            f"[BEHAVIOR_PROFILE] 更新完成: class={class_offering_id}, "
            f"user={user_role}:{user_pk}, trigger={trigger_mode}"
        )
        return True
    except Exception as exc:
        print(f"[BEHAVIOR_PROFILE] 更新失败: {exc}")
        return False
    finally:
        _finalize_profile_generation(
            class_offering_id=class_offering_id,
            user_pk=user_pk,
            user_role=user_role,
            success=success,
        )


def _claim_due_profile_candidates(limit: int = PROFILE_SCHEDULER_MAX_CONCURRENT) -> list[dict[str, Any]]:
    now = datetime.now()
    now_text = now.isoformat()
    active_cutoff = (now - timedelta(seconds=ACTIVE_PRESENCE_WINDOW_SECONDS)).isoformat()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT class_offering_id, user_pk, user_role, total_activity_count,
                   next_profile_due_at, last_event_at
            FROM classroom_behavior_states
            WHERE profile_generation_pending = 0
              AND total_activity_count > 0
              AND last_presence_at IS NOT NULL
              AND last_presence_at >= ?
              AND next_profile_due_at IS NOT NULL
              AND next_profile_due_at != ''
              AND next_profile_due_at <= ?
            ORDER BY next_profile_due_at ASC, last_event_at DESC, updated_at DESC
            LIMIT ?
            """,
            (active_cutoff, now_text, limit),
        ).fetchall()

        claimed: list[dict[str, Any]] = []
        for row in rows:
            updated = conn.execute(
                """
                UPDATE classroom_behavior_states
                SET profile_generation_pending = 1,
                    updated_at = ?
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                  AND profile_generation_pending = 0
                """,
                (
                    now_text,
                    row["class_offering_id"],
                    row["user_pk"],
                    row["user_role"],
                ),
            )
            if updated.rowcount:
                claimed.append(dict(row))

        conn.commit()
        return claimed


async def _run_profile_candidate(candidate: dict[str, Any]) -> None:
    try:
        await generate_behavior_profile_for_user(
            class_offering_id=int(candidate["class_offering_id"]),
            user_pk=int(candidate["user_pk"]),
            user_role=str(candidate["user_role"]),
            trigger_mode="scheduled",
        )
    except Exception as exc:
        print(f"[BEHAVIOR_PROFILE] 后台任务异常: {exc}")


def _track_background_profile_task(task: asyncio.Task) -> None:
    _profile_tasks.add(task)
    task.add_done_callback(lambda completed: _profile_tasks.discard(completed))


async def _profile_scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            available_slots = max(0, PROFILE_SCHEDULER_MAX_CONCURRENT - len(_profile_tasks))
            if available_slots > 0:
                for candidate in _claim_due_profile_candidates(available_slots):
                    task = asyncio.create_task(_run_profile_candidate(candidate))
                    _track_background_profile_task(task)
        except Exception as exc:
            print(f"[BEHAVIOR_PROFILE] 调度循环异常: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=PROFILE_SCHEDULER_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue


def start_behavior_profile_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event
    if _scheduler_task and not _scheduler_task.done():
        return

    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_profile_scheduler_loop(_scheduler_stop_event))
    print("[BEHAVIOR_PROFILE] 定时侧写调度器已启动")


async def stop_behavior_profile_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event

    if _scheduler_stop_event:
        _scheduler_stop_event.set()
    if _scheduler_task:
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    if _profile_tasks:
        await asyncio.gather(*list(_profile_tasks), return_exceptions=True)

    _scheduler_task = None
    _scheduler_stop_event = None
    print("[BEHAVIOR_PROFILE] 定时侧写调度器已停止")
