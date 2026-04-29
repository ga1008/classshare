from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

ASSIGNMENT_STATUS_NEW = "new"
ASSIGNMENT_STATUS_PUBLISHED = "published"
ASSIGNMENT_STATUS_CLOSED = "closed"
ASSIGNMENT_STATUSES = {
    ASSIGNMENT_STATUS_NEW,
    ASSIGNMENT_STATUS_PUBLISHED,
    ASSIGNMENT_STATUS_CLOSED,
}

ASSIGNMENT_MODE_PERMANENT = "permanent"
ASSIGNMENT_MODE_DEADLINE = "deadline"
ASSIGNMENT_MODE_COUNTDOWN = "countdown"
ASSIGNMENT_MODES = {
    ASSIGNMENT_MODE_PERMANENT,
    ASSIGNMENT_MODE_DEADLINE,
    ASSIGNMENT_MODE_COUNTDOWN,
}


def _utc_like_now() -> datetime:
    # Keep naive local-ISO style to match existing DB datetime conventions.
    return datetime.now().replace(microsecond=0)


def _parse_iso_like_datetime(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    normalized = text.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("时间格式无效，请使用 YYYY-MM-DDTHH:MM") from exc

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat()


def _parse_positive_minutes(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("倒计时必须是整数分钟") from exc
    if value <= 0:
        raise ValueError("倒计时必须大于 0 分钟")
    return value


def _parse_resubmission_minutes(raw: Any, default_minutes: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default_minutes
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("延后分钟数必须是整数") from exc
    if value <= 0:
        raise ValueError("延后分钟数必须大于 0")
    return value


def _normalize_mode(raw: Any, default: str = ASSIGNMENT_MODE_PERMANENT) -> str:
    mode = str(raw or "").strip().lower()
    if mode in ASSIGNMENT_MODES:
        return mode
    return default


def _normalize_status(raw: Any, default: str = ASSIGNMENT_STATUS_NEW) -> str:
    status = str(raw or "").strip().lower()
    if status in ASSIGNMENT_STATUSES:
        return status
    return default


def _is_truthy(raw: Any, default: bool = True) -> bool:
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def build_assignment_schedule_fields(
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    default_status: str = ASSIGNMENT_STATUS_NEW,
) -> dict[str, Any]:
    existing = dict(existing or {})
    now_dt = _utc_like_now()

    current_mode = _normalize_mode(existing.get("availability_mode"), ASSIGNMENT_MODE_PERMANENT)
    mode = _normalize_mode(
        payload.get("availability_mode") or payload.get("schedule_mode"),
        current_mode,
    )

    current_status = _normalize_status(existing.get("status"), default_status)
    status = _normalize_status(payload.get("status"), current_status)

    due_dt_payload = _parse_iso_like_datetime(payload.get("due_at"))
    starts_at_payload = _parse_iso_like_datetime(payload.get("starts_at"))
    existing_due_dt = _parse_iso_like_datetime(existing.get("due_at"))
    existing_starts_dt = _parse_iso_like_datetime(existing.get("starts_at"))

    duration_payload = _parse_positive_minutes(payload.get("duration_minutes"))
    existing_duration = None
    if existing.get("duration_minutes") not in (None, "", 0, "0"):
        try:
            existing_duration = int(existing.get("duration_minutes"))
        except (TypeError, ValueError):
            existing_duration = None
        if existing_duration is not None and existing_duration <= 0:
            existing_duration = None

    due_dt: datetime | None
    starts_at: datetime | None
    duration_minutes: int | None
    auto_close = _is_truthy(payload.get("auto_close"), default=True)

    if mode == ASSIGNMENT_MODE_PERMANENT:
        due_dt = None
        starts_at = None
        duration_minutes = None
        auto_close = False
    elif mode == ASSIGNMENT_MODE_DEADLINE:
        due_dt = due_dt_payload or existing_due_dt
        starts_at = None
        duration_minutes = None
        auto_close = True
        if due_dt is None and status == ASSIGNMENT_STATUS_PUBLISHED:
            raise ValueError("截止时间模式必须设置截止时间")
    else:
        duration_minutes = duration_payload if duration_payload is not None else existing_duration
        starts_at = starts_at_payload or existing_starts_dt
        if status == ASSIGNMENT_STATUS_PUBLISHED and starts_at is None:
            starts_at = now_dt
        due_dt = due_dt_payload or existing_due_dt
        if starts_at is not None and duration_minutes is not None:
            due_dt = starts_at + timedelta(minutes=duration_minutes)
        auto_close = True
        if status == ASSIGNMENT_STATUS_PUBLISHED and duration_minutes is None:
            raise ValueError("倒计时模式必须设置时长")

    closed_at = _parse_iso_like_datetime(existing.get("closed_at"))
    if status == ASSIGNMENT_STATUS_PUBLISHED and auto_close and due_dt is not None and due_dt <= now_dt:
        status = ASSIGNMENT_STATUS_CLOSED
        if closed_at is None:
            closed_at = now_dt
    elif status == ASSIGNMENT_STATUS_CLOSED:
        if closed_at is None:
            closed_at = now_dt
    else:
        closed_at = None

    return {
        "status": status,
        "availability_mode": mode,
        "starts_at": _dt_to_iso(starts_at),
        "due_at": _dt_to_iso(due_dt),
        "duration_minutes": duration_minutes,
        "auto_close": 1 if auto_close else 0,
        "closed_at": _dt_to_iso(closed_at),
    }


def build_resubmission_due_at(
    payload: dict[str, Any],
    *,
    default_minutes: int = 120,
    now_dt: datetime | None = None,
) -> str:
    now_dt = now_dt or _utc_like_now()
    explicit_due_at = (
        payload.get("resubmission_due_at")
        or payload.get("reopen_until")
        or payload.get("due_at")
    )
    due_dt = _parse_iso_like_datetime(explicit_due_at)
    if due_dt is None:
        due_dt = now_dt + timedelta(
            minutes=_parse_resubmission_minutes(payload.get("extension_minutes"), default_minutes)
        )
    if due_dt <= now_dt:
        raise ValueError("重交截止时间必须晚于当前时间")
    return _dt_to_iso(due_dt)


def submission_resubmission_accepts(submission_row, now_dt: datetime | None = None) -> bool:
    submission = dict(submission_row or {})
    if not submission:
        return False
    if not _is_truthy(submission.get("resubmission_allowed"), default=False):
        return False
    due_dt = _parse_iso_like_datetime(submission.get("resubmission_due_at"))
    if due_dt is None:
        return False
    return due_dt > (now_dt or _utc_like_now())


def is_assignment_overdue(assignment: dict[str, Any], now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or _utc_like_now()
    status = _normalize_status(assignment.get("status"), ASSIGNMENT_STATUS_NEW)
    if status != ASSIGNMENT_STATUS_PUBLISHED:
        return False
    if not _is_truthy(assignment.get("auto_close"), default=True):
        return False
    due_dt = _parse_iso_like_datetime(assignment.get("due_at"))
    if due_dt is None:
        return False
    return due_dt <= now_dt


def refresh_assignment_runtime_status(conn, assignment_row, now_dt: datetime | None = None) -> dict[str, Any]:
    assignment = dict(assignment_row or {})
    if not assignment:
        return assignment
    now_dt = now_dt or _utc_like_now()

    if is_assignment_overdue(assignment, now_dt=now_dt):
        closed_at_iso = _dt_to_iso(now_dt)
        conn.execute(
            """
            UPDATE assignments
            SET status = ?, closed_at = COALESCE(closed_at, ?)
            WHERE id = ? AND status = ?
            """,
            (
                ASSIGNMENT_STATUS_CLOSED,
                closed_at_iso,
                assignment.get("id"),
                ASSIGNMENT_STATUS_PUBLISHED,
            ),
        )
        assignment["status"] = ASSIGNMENT_STATUS_CLOSED
        assignment["closed_at"] = assignment.get("closed_at") or closed_at_iso
    return assignment


def close_overdue_assignments(conn, now_dt: datetime | None = None) -> int:
    now_dt = now_dt or _utc_like_now()
    now_iso = _dt_to_iso(now_dt)
    cursor = conn.execute(
        """
        UPDATE assignments
        SET status = ?, closed_at = COALESCE(closed_at, ?)
        WHERE status = ?
          AND COALESCE(auto_close, 1) = 1
          AND due_at IS NOT NULL
          AND due_at <> ''
          AND due_at <= ?
        """,
        (
            ASSIGNMENT_STATUS_CLOSED,
            now_iso,
            ASSIGNMENT_STATUS_PUBLISHED,
            now_iso,
        ),
    )
    return int(cursor.rowcount or 0)


def enrich_assignment_runtime_view(assignment_row, now_dt: datetime | None = None) -> dict[str, Any]:
    assignment = dict(assignment_row or {})
    if not assignment:
        return assignment

    now_dt = now_dt or _utc_like_now()
    mode = _normalize_mode(assignment.get("availability_mode"), ASSIGNMENT_MODE_PERMANENT)
    due_dt = _parse_iso_like_datetime(assignment.get("due_at"))
    starts_dt = _parse_iso_like_datetime(assignment.get("starts_at"))
    status = _normalize_status(assignment.get("status"), ASSIGNMENT_STATUS_NEW)

    if status == ASSIGNMENT_STATUS_PUBLISHED and _is_truthy(assignment.get("auto_close"), default=True):
        if due_dt is not None and due_dt <= now_dt:
            status = ASSIGNMENT_STATUS_CLOSED

    remaining_seconds = None
    if status == ASSIGNMENT_STATUS_PUBLISHED and due_dt is not None:
        remaining_seconds = max(0, int((due_dt - now_dt).total_seconds()))

    mode_label = {
        ASSIGNMENT_MODE_PERMANENT: "长期有效",
        ASSIGNMENT_MODE_DEADLINE: "截止时间",
        ASSIGNMENT_MODE_COUNTDOWN: "倒计时",
    }[mode]

    assignment["availability_mode"] = mode
    assignment["availability_mode_label"] = mode_label
    assignment["starts_at"] = _dt_to_iso(starts_dt)
    assignment["due_at"] = _dt_to_iso(due_dt)
    assignment["status"] = status
    assignment["effective_status"] = status
    assignment["remaining_seconds"] = remaining_seconds
    assignment["has_time_limit"] = mode in {ASSIGNMENT_MODE_DEADLINE, ASSIGNMENT_MODE_COUNTDOWN}
    assignment["is_accepting_submissions"] = status == ASSIGNMENT_STATUS_PUBLISHED
    return assignment


def assignment_accepts_submissions(assignment_row, now_dt: datetime | None = None) -> bool:
    assignment = enrich_assignment_runtime_view(assignment_row, now_dt=now_dt)
    return bool(assignment and assignment.get("is_accepting_submissions"))
