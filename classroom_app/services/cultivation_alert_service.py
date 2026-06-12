from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from ..db.connection import execute_insert_returning_id
from .student_support_service import (
    MAX_SHARED_NOTE_LENGTH,
    load_shared_student_teacher_note,
    normalize_shared_teacher_note,
    save_shared_student_teacher_note,
    teacher_can_access_student,
)


CULTIVATION_ALERT_TASK_KIND = "cultivation_alert_scan"
CULTIVATION_ALERT_INTERVAL_SECONDS = 24 * 60 * 60
CULTIVATION_ALERT_COOLDOWN_DAYS = 7
CULTIVATION_ALERT_SEVERITY_ORDER = {"L1": 1, "L2": 2, "L3": 3}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: Any | None = None) -> datetime:
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return datetime.now()
    normalized = text[:-1] if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.combine(datetime.fromisoformat(normalized[:10]).date(), datetime.min.time())
        except ValueError:
            return datetime.now()


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        if value in (None, ""):
            return default
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _severity_rank(value: Any) -> int:
    return CULTIVATION_ALERT_SEVERITY_ORDER.get(str(value or "").strip().upper(), 0)


def _severity_label(value: Any) -> str:
    severity = str(value or "").strip().upper()
    return {"L1": "提示", "L2": "关注", "L3": "干预"}.get(severity, "提示")


def _alert_payload(
    *,
    student_id: int,
    rule_key: str,
    severity: str,
    title: str,
    body: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "student_id": int(student_id),
        "rule_key": str(rule_key),
        "severity": str(severity).upper(),
        "title": str(title or ""),
        "body": str(body or ""),
        "evidence": evidence or {},
    }


def _load_offering_ids(conn, class_offering_id: int | None = None) -> list[int]:
    if class_offering_id:
        return [int(class_offering_id)]
    return [
        int(row["id"])
        for row in conn.execute("SELECT id FROM class_offerings ORDER BY id").fetchall()
    ]


def _load_active_students(conn, class_offering_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, s.name, s.student_id_number
            FROM students s
            JOIN class_offerings o ON o.class_id = s.class_id
            WHERE o.id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.student_id_number, s.id
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]


def _load_assignment_signal_by_student(conn, class_offering_id: int, now_dt: datetime) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id AS student_id,
               COUNT(a.id) AS assignment_count,
               SUM(CASE WHEN sub.id IS NOT NULL THEN 1 ELSE 0 END) AS submitted_count,
               SUM(CASE WHEN sub.id IS NULL THEN 1 ELSE 0 END) AS pending_count,
               SUM(CASE
                     WHEN sub.id IS NULL
                      AND a.due_at IS NOT NULL
                      AND a.due_at != ''
                      AND a.due_at <= ?
                     THEN 1 ELSE 0 END) AS due_soon_count,
               MIN(CASE
                     WHEN sub.id IS NULL
                      AND a.due_at IS NOT NULL
                      AND a.due_at != ''
                     THEN a.due_at ELSE NULL END) AS nearest_due_at
        FROM students s
        JOIN class_offerings o ON o.class_id = s.class_id
        LEFT JOIN assignments a
          ON a.class_offering_id = o.id
         AND COALESCE(a.status, 'new') != 'new'
        LEFT JOIN submissions sub
          ON CAST(sub.assignment_id AS TEXT) = CAST(a.id AS TEXT)
         AND sub.student_pk_id = s.id
        WHERE o.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        GROUP BY s.id
        """,
        ((now_dt + timedelta(days=2)).isoformat(timespec="seconds"), int(class_offering_id)),
    ).fetchall()
    return {int(row["student_id"]): dict(row) for row in rows}


def _load_last_activity_by_student(conn, class_offering_id: int) -> dict[int, str]:
    last_by_student: dict[int, str] = {}
    for row in conn.execute(
        """
        SELECT user_pk AS student_id, MAX(last_event_at) AS last_at
        FROM classroom_behavior_states
        WHERE class_offering_id = ?
          AND user_role = 'student'
        GROUP BY user_pk
        """,
        (int(class_offering_id),),
    ).fetchall():
        if row["last_at"]:
            last_by_student[int(row["student_id"])] = str(row["last_at"])
    for row in conn.execute(
        """
        SELECT user_pk AS student_id, MAX(created_at) AS last_at
        FROM classroom_behavior_events
        WHERE class_offering_id = ?
          AND user_role = 'student'
        GROUP BY user_pk
        """,
        (int(class_offering_id),),
    ).fetchall():
        student_id = int(row["student_id"])
        last_at = str(row["last_at"] or "")
        if last_at and last_at > last_by_student.get(student_id, ""):
            last_by_student[student_id] = last_at
    try:
        chat_rows = conn.execute(
            """
            SELECT user_id AS student_id, MAX(created_at) AS last_at
            FROM chat_logs
            WHERE class_offering_id = ?
              AND user_role = 'student'
            GROUP BY user_id
            """,
            (int(class_offering_id),),
        ).fetchall()
    except Exception:
        chat_rows = []
    for row in chat_rows:
        student_id = _safe_int(row["student_id"])
        if not student_id:
            continue
        last_at = str(row["last_at"] or "")
        if last_at and last_at > last_by_student.get(student_id, ""):
            last_by_student[student_id] = last_at
    return last_by_student


def _load_weekly_scores_by_student(conn, class_offering_id: int) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT student_id, week_start, score
        FROM cultivation_weekly_snapshots
        WHERE class_offering_id = ?
        ORDER BY student_id, week_start DESC
        """,
        (int(class_offering_id),),
    ).fetchall()
    by_student: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        bucket = by_student.setdefault(int(row["student_id"]), [])
        if len(bucket) < 3:
            bucket.append(dict(row))
    return by_student


def _load_failed_stage_attempts_by_student(conn, class_offering_id: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT student_id,
               stage_key,
               COUNT(*) AS failed_count,
               MAX(COALESCE(generated_at, graded_at, submitted_at, '')) AS last_failed_at
        FROM learning_stage_exam_attempts
        WHERE class_offering_id = ?
          AND status = 'failed'
        GROUP BY student_id, stage_key
        HAVING COUNT(*) >= 2
        """,
        (int(class_offering_id),),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        student_id = int(row["student_id"])
        item = dict(row)
        if student_id not in result or _safe_int(item.get("failed_count")) > _safe_int(result[student_id].get("failed_count")):
            result[student_id] = item
    return result


def _build_alert_candidates_for_offering(conn, class_offering_id: int, now_dt: datetime) -> list[dict[str, Any]]:
    students = _load_active_students(conn, class_offering_id)
    assignments = _load_assignment_signal_by_student(conn, class_offering_id, now_dt)
    last_activity = _load_last_activity_by_student(conn, class_offering_id)
    weekly_scores = _load_weekly_scores_by_student(conn, class_offering_id)
    failed_attempts = _load_failed_stage_attempts_by_student(conn, class_offering_id)
    no_activity_cutoff = now_dt - timedelta(days=7)
    alerts: list[dict[str, Any]] = []

    for student in students:
        student_id = int(student["id"])
        student_name = str(student.get("name") or f"学生 {student_id}")
        assignment = assignments.get(student_id, {})
        assignment_count = _safe_int(assignment.get("assignment_count"))
        submitted_count = _safe_int(assignment.get("submitted_count"))
        pending_count = _safe_int(assignment.get("pending_count"))
        due_soon_count = _safe_int(assignment.get("due_soon_count"))
        completion_ratio = submitted_count / assignment_count if assignment_count else 1.0

        if due_soon_count > 0:
            alerts.append(_alert_payload(
                student_id=student_id,
                rule_key="pending_assignment_due_soon",
                severity="L1",
                title=f"{student_name} 有临近截止任务",
                body=f"{student_name} 还有 {due_soon_count} 个临近截止任务未提交。",
                evidence={
                    "assignment_count": assignment_count,
                    "pending_count": pending_count,
                    "due_soon_count": due_soon_count,
                    "nearest_due_at": assignment.get("nearest_due_at") or "",
                },
            ))

        if assignment_count > 0 and completion_ratio < 0.5:
            alerts.append(_alert_payload(
                student_id=student_id,
                rule_key="low_task_completion",
                severity="L2",
                title=f"{student_name} 任务完成率偏低",
                body=f"已提交 {submitted_count}/{assignment_count}，完成率低于 50%。",
                evidence={
                    "assignment_count": assignment_count,
                    "submitted_count": submitted_count,
                    "pending_count": pending_count,
                    "completion_ratio": round(completion_ratio, 3),
                },
            ))

        scores = weekly_scores.get(student_id, [])
        if len(scores) >= 3:
            current_delta = _safe_float(scores[0].get("score")) - _safe_float(scores[1].get("score"))
            previous_delta = _safe_float(scores[1].get("score")) - _safe_float(scores[2].get("score"))
            if current_delta <= 0.05 and previous_delta <= 0.05:
                alerts.append(_alert_payload(
                    student_id=student_id,
                    rule_key="zero_growth_two_weeks",
                    severity="L2",
                    title=f"{student_name} 修为连续两周停滞",
                    body="近两段周快照没有可见增长，建议查看材料、任务和互动缺口。",
                    evidence={
                        "weeks": [item.get("week_start") for item in scores[:3]],
                        "scores": [round(_safe_float(item.get("score")), 1) for item in scores[:3]],
                        "current_delta": round(current_delta, 2),
                        "previous_delta": round(previous_delta, 2),
                    },
                ))

        last_at = last_activity.get(student_id, "")
        if not last_at or _coerce_datetime(last_at) < no_activity_cutoff:
            alerts.append(_alert_payload(
                student_id=student_id,
                rule_key="no_activity_7d",
                severity="L3",
                title=f"{student_name} 7 天无课堂活动",
                body="最近 7 天没有课堂行为、聊天或学习活动记录。",
                evidence={
                    "last_activity_at": last_at,
                    "cutoff": no_activity_cutoff.isoformat(timespec="seconds"),
                },
            ))

        failed = failed_attempts.get(student_id)
        if failed:
            alerts.append(_alert_payload(
                student_id=student_id,
                rule_key="stage_exam_failed_twice",
                severity="L3",
                title=f"{student_name} 破境试炼连续受阻",
                body=f"{failed.get('stage_key') or '当前境界'} 试炼已有 {_safe_int(failed.get('failed_count'))} 次生成或完成失败。",
                evidence={
                    "stage_key": failed.get("stage_key") or "",
                    "failed_count": _safe_int(failed.get("failed_count")),
                    "last_failed_at": failed.get("last_failed_at") or "",
                },
            ))
    return alerts


def _find_existing_alert(conn, class_offering_id: int, student_id: int, rule_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM cultivation_alerts
        WHERE class_offering_id = ?
          AND student_id = ?
          AND rule_key = ?
          AND status IN ('active', 'snoozed')
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id), str(rule_key)),
    ).fetchone()
    return dict(row) if row else None


def _handled_recently(conn, class_offering_id: int, student_id: int, rule_key: str, now_dt: datetime) -> bool:
    cutoff = (now_dt - timedelta(days=CULTIVATION_ALERT_COOLDOWN_DAYS)).isoformat(timespec="seconds")
    row = conn.execute(
        """
        SELECT id
        FROM cultivation_alerts
        WHERE class_offering_id = ?
          AND student_id = ?
          AND rule_key = ?
          AND status = 'handled'
          AND handled_at >= ?
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id), str(rule_key), cutoff),
    ).fetchone()
    return row is not None


def _upsert_alert(conn, class_offering_id: int, payload: dict[str, Any], now_dt: datetime) -> int:
    now_text = now_dt.isoformat(timespec="seconds")
    student_id = int(payload["student_id"])
    rule_key = str(payload["rule_key"])
    existing = _find_existing_alert(conn, class_offering_id, student_id, rule_key)
    if existing:
        if str(existing.get("status") or "") == "snoozed":
            snoozed_until = str(existing.get("snoozed_until") or "")
            if snoozed_until and snoozed_until > now_text:
                return 0
        conn.execute(
            """
            UPDATE cultivation_alerts
            SET severity = ?,
                status = 'active',
                title = ?,
                body = ?,
                evidence_json = ?,
                last_seen_at = ?,
                snoozed_until = NULL,
                metadata_json = ?
            WHERE id = ?
            """,
            (
                payload["severity"],
                payload["title"],
                payload["body"],
                _json_dumps(payload.get("evidence")),
                now_text,
                _json_dumps({"rule_version": 1}),
                existing["id"],
            ),
        )
        return int(existing["id"])
    if _handled_recently(conn, class_offering_id, student_id, rule_key, now_dt):
        return 0
    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO cultivation_alerts (
            class_offering_id, student_id, rule_key, severity, status,
            title, body, evidence_json, first_seen_at, last_seen_at, metadata_json
        )
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            student_id,
            rule_key,
            payload["severity"],
            payload["title"],
            payload["body"],
            _json_dumps(payload.get("evidence")),
            now_text,
            now_text,
            _json_dumps({"rule_version": 1}),
        ),
    )


def generate_cultivation_alerts(
    conn,
    *,
    class_offering_id: int | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    now_dt = _coerce_datetime(now)
    result = {
        "offerings": 0,
        "candidates": 0,
        "created_or_updated": 0,
        "suppressed": 0,
        "resolved": 0,
    }
    for offering_id in _load_offering_ids(conn, class_offering_id):
        result["offerings"] += 1
        candidates = _build_alert_candidates_for_offering(conn, int(offering_id), now_dt)
        result["candidates"] += len(candidates)
        fired_keys = {(int(item["student_id"]), str(item["rule_key"])) for item in candidates}
        for candidate in candidates:
            alert_id = _upsert_alert(conn, int(offering_id), candidate, now_dt)
            if alert_id:
                result["created_or_updated"] += 1
            else:
                result["suppressed"] += 1
        active_rows = conn.execute(
            """
            SELECT id, student_id, rule_key
            FROM cultivation_alerts
            WHERE class_offering_id = ?
              AND status = 'active'
            """,
            (int(offering_id),),
        ).fetchall()
        for row in active_rows:
            key = (int(row["student_id"]), str(row["rule_key"]))
            if key in fired_keys:
                continue
            conn.execute(
                """
                UPDATE cultivation_alerts
                SET status = 'resolved',
                    action_note = CASE WHEN action_note = '' THEN 'auto-resolved' ELSE action_note END,
                    last_seen_at = ?
                WHERE id = ?
                """,
                (now_dt.isoformat(timespec="seconds"), row["id"]),
            )
            result["resolved"] += 1
    return result


def ensure_cultivation_alert_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=CULTIVATION_ALERT_TASK_KIND,
        run_at=datetime.now() + timedelta(seconds=300),
        payload={},
        dedupe_key="cultivation:alert-scan",
        recurrence_seconds=CULTIVATION_ALERT_INTERVAL_SECONDS,
        title="Cultivation alert scan",
        priority=76,
        max_attempts=3,
        replace=False,
    )


def _serialize_alert(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "class_offering_id": int(row["class_offering_id"]),
        "student_id": int(row["student_id"]),
        "student_name": row.get("student_name") or "",
        "student_id_number": row.get("student_id_number") or "",
        "rule_key": row.get("rule_key") or "",
        "severity": row.get("severity") or "L1",
        "severity_label": _severity_label(row.get("severity")),
        "status": row.get("status") or "active",
        "title": row.get("title") or "",
        "body": row.get("body") or "",
        "evidence": _json_loads(row.get("evidence_json"), {}),
        "first_seen_at": row.get("first_seen_at") or "",
        "last_seen_at": row.get("last_seen_at") or "",
        "snoozed_until": row.get("snoozed_until") or "",
    }


def get_cultivation_alert_for_action(
    conn,
    alert_id: int,
    class_offering_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT ca.*, s.name AS student_name, s.student_id_number
        FROM cultivation_alerts ca
        LEFT JOIN students s ON s.id = ca.student_id
        WHERE ca.id = ?
          AND ca.class_offering_id = ?
        LIMIT 1
        """,
        (int(alert_id), int(class_offering_id)),
    ).fetchone()
    if not row:
        return None
    return _serialize_alert(dict(row))


def build_cultivation_alert_private_message(alert: dict[str, Any], content: str | None = None) -> str:
    custom_content = str(content or "").strip()
    if custom_content:
        return custom_content[:4000].rstrip()
    student_name = str(alert.get("student_name") or "同学").strip() or "同学"
    title = str(alert.get("title") or "学习节奏提醒").strip() or "学习节奏提醒"
    body = str(alert.get("body") or "").strip()
    lines = [
        f"{student_name}同学，我看到系统提示“{title}”。",
        "这不是批评，只是想确认你最近的学习节奏是否需要帮助。",
    ]
    if body:
        lines.append(f"当前线索：{body}")
    lines.append("你可以直接回复我：现在卡在哪里、是否需要我帮你拆一个更小的下一步。")
    return "\n".join(lines)[:4000].rstrip()


def build_cultivation_alert_support_note(
    alert: dict[str, Any],
    note: str | None = None,
    *,
    now_text: str | None = None,
) -> str:
    custom_note = str(note or "").strip()
    if custom_note:
        return normalize_shared_teacher_note(custom_note)
    date_text = str(now_text or _now_iso())[:10]
    student_name = str(alert.get("student_name") or "学生").strip() or "学生"
    severity_label = str(alert.get("severity_label") or _severity_label(alert.get("severity"))).strip()
    title = str(alert.get("title") or "修为预警").strip() or "修为预警"
    body = str(alert.get("body") or "").strip()
    text = f"[{date_text} 修为预警-{severity_label}] {student_name}：{title}"
    if body:
        text = f"{text}。{body}"
    return normalize_shared_teacher_note(text)


def _append_shared_support_note(existing_text: str, addition_text: str) -> str:
    existing = normalize_shared_teacher_note(existing_text)
    addition = normalize_shared_teacher_note(addition_text)
    if not addition:
        return existing
    if not existing:
        return addition
    separator = "\n\n"
    combined = f"{existing}{separator}{addition}"
    if len(combined) <= MAX_SHARED_NOTE_LENGTH:
        return combined
    prefix = "...\n"
    existing_budget = MAX_SHARED_NOTE_LENGTH - len(addition) - len(separator) - len(prefix)
    if existing_budget <= 0:
        return addition[-MAX_SHARED_NOTE_LENGTH:].lstrip()
    existing_tail = existing[-existing_budget:].lstrip()
    return normalize_shared_teacher_note(f"{prefix}{existing_tail}{separator}{addition}")


def append_cultivation_alert_support_note(
    conn,
    *,
    alert: dict[str, Any],
    teacher_id: int,
    note: str | None = None,
    now_text: str | None = None,
) -> dict[str, Any]:
    student_id = int(alert["student_id"])
    if not teacher_can_access_student(conn, teacher_id=int(teacher_id), student_id=student_id):
        raise PermissionError("teacher cannot access student")
    existing = load_shared_student_teacher_note(conn, student_id)
    addition = build_cultivation_alert_support_note(alert, note, now_text=now_text)
    merged = _append_shared_support_note(existing.get("note_text") or "", addition)
    return save_shared_student_teacher_note(
        conn,
        student_id=student_id,
        teacher_id=int(teacher_id),
        note_text=merged,
        now_text=now_text or _now_iso(),
    )


def list_cultivation_alerts(
    conn,
    class_offering_id: int,
    *,
    statuses: tuple[str, ...] = ("active",),
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_statuses = tuple(str(status or "").strip().lower() for status in statuses if str(status or "").strip())
    if not normalized_statuses:
        normalized_statuses = ("active",)
    placeholders = ",".join("?" for _ in normalized_statuses)
    rows = conn.execute(
        f"""
        SELECT ca.*, s.name AS student_name, s.student_id_number
        FROM cultivation_alerts ca
        LEFT JOIN students s ON s.id = ca.student_id
        WHERE ca.class_offering_id = ?
          AND ca.status IN ({placeholders})
        ORDER BY
          CASE ca.severity WHEN 'L3' THEN 3 WHEN 'L2' THEN 2 ELSE 1 END DESC,
          ca.last_seen_at DESC,
          ca.id DESC
        LIMIT ?
        """,
        (int(class_offering_id), *normalized_statuses, max(1, min(int(limit or 50), 200))),
    ).fetchall()
    return [_serialize_alert(dict(row)) for row in rows]


def build_class_cultivation_alert_context(conn, class_offering_id: int) -> dict[str, Any]:
    alerts = list_cultivation_alerts(conn, int(class_offering_id), statuses=("active",), limit=80)
    counts = {"L1": 0, "L2": 0, "L3": 0}
    alerts_by_student: dict[int, list[dict[str, Any]]] = {}
    for alert in alerts:
        severity = str(alert.get("severity") or "L1").upper()
        counts[severity] = counts.get(severity, 0) + 1
        alerts_by_student.setdefault(int(alert["student_id"]), []).append(alert)
    student_count = len(alerts_by_student)
    return {
        "counts": counts,
        "total_count": len(alerts),
        "student_count": student_count,
        "highest_severity": max((item for item, count in counts.items() if count), key=_severity_rank) if alerts else "",
        "items": alerts[:8],
        "alerts_by_student": alerts_by_student,
    }


def handle_cultivation_alert(
    conn,
    *,
    alert_id: int,
    teacher_id: int,
    action: str,
    note: str = "",
    snooze_days: int = CULTIVATION_ALERT_COOLDOWN_DAYS,
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"handled", "snoozed"}:
        raise ValueError("Unsupported alert action")
    row = conn.execute(
        "SELECT * FROM cultivation_alerts WHERE id = ? LIMIT 1",
        (int(alert_id),),
    ).fetchone()
    if not row:
        raise ValueError("Alert not found")
    now_text = _now_iso()
    if normalized_action == "handled":
        conn.execute(
            """
            UPDATE cultivation_alerts
            SET status = 'handled',
                handled_at = ?,
                handled_by_teacher_id = ?,
                action_note = ?,
                snoozed_until = NULL
            WHERE id = ?
            """,
            (now_text, int(teacher_id), str(note or "")[:1000], int(alert_id)),
        )
    else:
        snoozed_until = (datetime.now() + timedelta(days=max(1, min(int(snooze_days or 7), 30)))).isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE cultivation_alerts
            SET status = 'snoozed',
                handled_by_teacher_id = ?,
                action_note = ?,
                snoozed_until = ?
            WHERE id = ?
            """,
            (int(teacher_id), str(note or "")[:1000], snoozed_until, int(alert_id)),
        )
    updated = conn.execute(
        """
        SELECT ca.*, s.name AS student_name, s.student_id_number
        FROM cultivation_alerts ca
        LEFT JOIN students s ON s.id = ca.student_id
        WHERE ca.id = ?
        LIMIT 1
        """,
        (int(alert_id),),
    ).fetchone()
    return _serialize_alert(dict(updated))
