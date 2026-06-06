from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from ..time_utils import local_iso, local_now
from .message_center_service import create_smart_attendance_alert_notification
from .smart_classroom_checkin_sync_service import (
    load_student_smart_attendance_absences,
    sync_teacher_smart_classroom_checkins,
)


TEACHER_DAILY_SYNC_TASK_TYPE = "teacher_daily_checkin_sync"


def _today_text() -> str:
    return local_now().date().isoformat()


def maybe_send_student_attendance_alert(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
) -> int:
    absences = load_student_smart_attendance_absences(
        conn,
        class_offering_id=int(class_offering_id),
        student_id=int(student_id),
    )
    if not absences:
        return 0
    course_name = str(absences[0].get("course_name") or "本课程")
    return create_smart_attendance_alert_notification(
        conn,
        student_id=int(student_id),
        class_offering_id=int(class_offering_id),
        course_name=course_name,
        absences=absences,
        reminder_date=_today_text(),
    )


def maybe_enqueue_teacher_daily_checkin_sync(
    conn,
    *,
    class_offering_id: int,
    teacher_id: int,
) -> int | None:
    now = local_iso()
    params = (
        int(class_offering_id),
        int(teacher_id),
        TEACHER_DAILY_SYNC_TASK_TYPE,
        _today_text(),
        now,
        now,
    )
    if get_configured_db_engine() == "postgres":
        row = conn.execute(
            """
            INSERT INTO smart_attendance_daily_tasks (
                class_offering_id, teacher_id, task_type, task_date,
                status, message, raw_payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', '', '{}', ?, ?)
            ON CONFLICT (class_offering_id, teacher_id, task_type, task_date)
            DO NOTHING
            RETURNING id
            """,
            params,
        ).fetchone()
        return int(row["id"]) if row else None

    try:
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO smart_attendance_daily_tasks (
                class_offering_id, teacher_id, task_type, task_date,
                status, message, raw_payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', '', '{}', ?, ?)
            """,
            params,
            engine="sqlite",
        )
    except sqlite3.IntegrityError:
        return None


def _mark_daily_task(
    conn,
    task_id: int,
    *,
    status: str,
    message: str = "",
    payload: dict[str, Any] | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    now = local_iso()
    assignments = ["status = ?", "message = ?", "updated_at = ?"]
    values: list[Any] = [status, str(message or "")[:500], now]
    if payload is not None:
        assignments.append("raw_payload_json = ?")
        values.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if started:
        assignments.append("started_at = COALESCE(started_at, ?)")
        values.append(now)
    if finished:
        assignments.append("finished_at = ?")
        values.append(now)
    values.append(int(task_id))
    conn.execute(
        f"UPDATE smart_attendance_daily_tasks SET {', '.join(assignments)} WHERE id = ?",
        values,
    )
    conn.commit()


async def run_teacher_daily_checkin_sync_task(
    task_id: int,
    *,
    teacher_id: int,
    class_offering_id: int,
) -> None:
    try:
        with get_db_connection() as conn:
            _mark_daily_task(conn, int(task_id), status="running", started=True)
        result = await sync_teacher_smart_classroom_checkins(
            int(teacher_id),
            class_offering_id=int(class_offering_id),
        )
        result_status = str(result.get("status") or "unknown").strip().lower()
        final_status = "success" if result_status in {"success", "partial_success", "empty"} else "failed"
        with get_db_connection() as conn:
            _mark_daily_task(
                conn,
                int(task_id),
                status=final_status,
                message=str(result.get("message") or ""),
                payload=result,
                finished=True,
            )
    except Exception as exc:
        try:
            with get_db_connection() as conn:
                _mark_daily_task(
                    conn,
                    int(task_id),
                    status="failed",
                    message=f"后台同步失败：{str(exc)[:180]}",
                    payload={"error": str(exc)[:500]},
                    finished=True,
                )
        except Exception:
            pass
        print(f"[SMART_ATTENDANCE] teacher daily sync failed: {exc}")
