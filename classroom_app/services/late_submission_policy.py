from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any


LATE_PENALTY_FIXED = "fixed"
LATE_PENALTY_GRADIENT = "gradient"
LATE_PENALTY_STRATEGIES = {LATE_PENALTY_FIXED, LATE_PENALTY_GRADIENT}


def utc_like_now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_iso_like_datetime(raw: Any) -> datetime | None:
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


def dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat()


def _is_truthy(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _has_key(payload: dict[str, Any], *keys: str) -> bool:
    return any(key in payload for key in keys)


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _normalize_strategy(raw: Any, default: str = LATE_PENALTY_FIXED) -> str:
    value = str(raw or "").strip().lower()
    if value in {"step", "stepped", "梯度", "gradient"}:
        return LATE_PENALTY_GRADIENT
    if value in {"fixed", "flat", "定量"}:
        return LATE_PENALTY_FIXED
    return default if default in LATE_PENALTY_STRATEGIES else LATE_PENALTY_FIXED


def _parse_float(raw: Any, default: float | None = None) -> float | None:
    if raw in (None, ""):
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("补交扣分配置必须是数字") from exc


def _bounded_score(raw: Any, default: float | None = None) -> float | None:
    value = _parse_float(raw, default)
    if value is None:
        return None
    if value < 0 or value > 100:
        raise ValueError("补交分数边界必须在 0-100 之间")
    return round(value, 2)


def _positive_float(raw: Any, default: float) -> float:
    value = _parse_float(raw, default)
    if value is None or value <= 0:
        raise ValueError("梯度扣分间隔必须大于 0")
    return round(value, 4)


def _nonnegative_float(raw: Any, default: float = 0) -> float:
    value = _parse_float(raw, default)
    if value is None or value < 0:
        raise ValueError("补交扣分值不能小于 0")
    return round(value, 2)


def _normalize_score_value(value: float) -> int | float:
    bounded = max(0.0, min(100.0, float(value)))
    rounded = round(bounded, 2)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def build_late_submission_policy_fields(
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    due_at: Any = None,
    require_due: bool = True,
) -> dict[str, Any]:
    existing = dict(existing or {})
    enabled_raw = _first_present(payload, "late_submission_enabled", "allow_late_submission")
    enabled = _is_truthy(enabled_raw, default=_is_truthy(existing.get("late_submission_enabled"), False))

    due_dt = parse_iso_like_datetime(due_at)
    until_source = (
        _first_present(payload, "late_submission_until", "late_due_at", "second_due_at")
        if _has_key(payload, "late_submission_until", "late_due_at", "second_due_at")
        else existing.get("late_submission_until")
    )
    until_dt = parse_iso_like_datetime(until_source)

    existing_strategy = _normalize_strategy(existing.get("late_penalty_strategy"), LATE_PENALTY_FIXED)
    strategy = _normalize_strategy(
        _first_present(payload, "late_penalty_strategy", "late_penalty_type")
        if _has_key(payload, "late_penalty_strategy", "late_penalty_type")
        else existing_strategy,
        existing_strategy,
    )

    points = _nonnegative_float(
        _first_present(payload, "late_penalty_points", "late_penalty_value")
        if _has_key(payload, "late_penalty_points", "late_penalty_value")
        else existing.get("late_penalty_points"),
        0,
    )
    interval_hours = _positive_float(
        _first_present(payload, "late_penalty_interval_hours", "late_interval_hours")
        if _has_key(payload, "late_penalty_interval_hours", "late_interval_hours")
        else existing.get("late_penalty_interval_hours"),
        1,
    )
    min_score = _bounded_score(
        _first_present(payload, "late_penalty_min_score", "late_min_score", "late_penalty_floor_score")
        if _has_key(payload, "late_penalty_min_score", "late_min_score", "late_penalty_floor_score")
        else existing.get("late_penalty_min_score"),
        0,
    )
    score_cap = _bounded_score(
        _first_present(payload, "late_score_cap", "late_max_score", "late_score_limit")
        if _has_key(payload, "late_score_cap", "late_max_score", "late_score_limit")
        else existing.get("late_score_cap"),
        None,
    )

    if not enabled:
        return {
            "late_submission_enabled": 0,
            "late_submission_until": None,
            "late_penalty_strategy": strategy,
            "late_penalty_interval_hours": interval_hours,
            "late_penalty_points": points,
            "late_penalty_min_score": min_score,
            "late_score_cap": score_cap,
        }

    if due_dt is None and require_due:
        raise ValueError("启用补交扣分前需要先设置首次截止时间")
    if until_dt is not None and until_dt <= due_dt:
        raise ValueError("补交二次截止时间必须晚于首次截止时间")
    if score_cap is not None and min_score is not None and score_cap < min_score:
        raise ValueError("补交最高分不能低于扣分最低保留分")

    return {
        "late_submission_enabled": 1,
        "late_submission_until": dt_to_iso(until_dt),
        "late_penalty_strategy": strategy,
        "late_penalty_interval_hours": interval_hours,
        "late_penalty_points": points,
        "late_penalty_min_score": min_score,
        "late_score_cap": score_cap,
    }


def assignment_late_window_accepts(assignment: dict[str, Any], now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or utc_like_now()
    if not _is_truthy(assignment.get("late_submission_enabled"), False):
        return False
    due_dt = parse_iso_like_datetime(assignment.get("due_at"))
    if due_dt is None or now_dt <= due_dt:
        return False
    until_dt = parse_iso_like_datetime(assignment.get("late_submission_until"))
    if until_dt is not None and now_dt > until_dt:
        return False
    return True


def assignment_is_accepting_by_time(assignment: dict[str, Any], now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or utc_like_now()
    due_dt = parse_iso_like_datetime(assignment.get("due_at"))
    if due_dt is None:
        return True
    if due_dt > now_dt:
        return True
    return assignment_late_window_accepts(assignment, now_dt=now_dt)


def assignment_should_auto_close(assignment: dict[str, Any], now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or utc_like_now()
    due_dt = parse_iso_like_datetime(assignment.get("due_at"))
    if due_dt is None or due_dt > now_dt:
        return False
    if not _is_truthy(assignment.get("late_submission_enabled"), False):
        return True
    until_dt = parse_iso_like_datetime(assignment.get("late_submission_until"))
    return until_dt is not None and until_dt <= now_dt


def build_late_submission_snapshot(
    assignment: dict[str, Any],
    submitted_at: Any,
) -> dict[str, Any]:
    submitted_dt = parse_iso_like_datetime(submitted_at) or utc_like_now()
    due_dt = parse_iso_like_datetime(assignment.get("due_at"))
    is_late = bool(
        due_dt is not None
        and submitted_dt > due_dt
        and _is_truthy(assignment.get("late_submission_enabled"), False)
    )
    late_by_seconds = max(0, int((submitted_dt - due_dt).total_seconds())) if due_dt and submitted_dt > due_dt else 0
    return {
        "enabled": _is_truthy(assignment.get("late_submission_enabled"), False),
        "is_late_submission": is_late,
        "submitted_at": dt_to_iso(submitted_dt),
        "due_at": dt_to_iso(due_dt),
        "late_by_seconds": late_by_seconds,
        "late_submission_until": dt_to_iso(parse_iso_like_datetime(assignment.get("late_submission_until"))),
        "late_penalty_strategy": _normalize_strategy(assignment.get("late_penalty_strategy")),
        "late_penalty_interval_hours": _positive_float(assignment.get("late_penalty_interval_hours"), 1),
        "late_penalty_points": _nonnegative_float(assignment.get("late_penalty_points"), 0),
        "late_penalty_min_score": _bounded_score(assignment.get("late_penalty_min_score"), 0),
        "late_score_cap": _bounded_score(assignment.get("late_score_cap"), None),
    }


def load_late_policy_snapshot(raw_snapshot: Any) -> dict[str, Any]:
    if isinstance(raw_snapshot, dict):
        return dict(raw_snapshot)
    if not raw_snapshot:
        return {}
    try:
        data = json.loads(str(raw_snapshot))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def describe_late_policy(policy: dict[str, Any]) -> str:
    if not _is_truthy(policy.get("enabled", policy.get("late_submission_enabled")), False):
        return ""
    strategy = _normalize_strategy(policy.get("late_penalty_strategy"))
    points = _nonnegative_float(policy.get("late_penalty_points"), 0)
    min_score = _bounded_score(policy.get("late_penalty_min_score"), 0)
    cap = _bounded_score(policy.get("late_score_cap"), None)
    if strategy == LATE_PENALTY_GRADIENT:
        interval = _positive_float(policy.get("late_penalty_interval_hours"), 1)
        base = f"每 {interval:g} 小时扣 {points:g} 分，最低扣到 {min_score:g} 分"
    else:
        base = f"统一扣 {points:g} 分，最低扣到 {min_score:g} 分"
    if cap is not None:
        base = f"{base}，补交最高 {cap:g} 分"
    return base


def apply_late_policy_to_score(
    raw_score: Any,
    *,
    submission: dict[str, Any] | None = None,
    assignment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        original_score = float(raw_score)
    except (TypeError, ValueError):
        return {
            "applied": False,
            "original_score": None,
            "final_score": raw_score,
            "penalty_points": 0,
            "late_by_seconds": 0,
            "score_cap_applied": False,
            "message": "",
        }

    submission = dict(submission or {})
    assignment = dict(assignment or {})
    snapshot = load_late_policy_snapshot(submission.get("late_policy_snapshot_json"))
    if not snapshot:
        submitted_at = submission.get("submitted_at")
        snapshot = build_late_submission_snapshot(assignment, submitted_at) if assignment else {}

    is_late = _is_truthy(snapshot.get("is_late_submission"), False) or _is_truthy(submission.get("is_late_submission"), False)
    if not is_late:
        return {
            "applied": False,
            "original_score": _normalize_score_value(original_score),
            "final_score": _normalize_score_value(original_score),
            "penalty_points": 0,
            "late_by_seconds": int(submission.get("late_by_seconds") or snapshot.get("late_by_seconds") or 0),
            "score_cap_applied": False,
            "message": "",
        }

    strategy = _normalize_strategy(snapshot.get("late_penalty_strategy"))
    points = _nonnegative_float(snapshot.get("late_penalty_points"), 0)
    interval_hours = _positive_float(snapshot.get("late_penalty_interval_hours"), 1)
    min_score = _bounded_score(snapshot.get("late_penalty_min_score"), 0) or 0
    cap = _bounded_score(snapshot.get("late_score_cap"), None)
    late_by_seconds = int(submission.get("late_by_seconds") or snapshot.get("late_by_seconds") or 0)

    if strategy == LATE_PENALTY_GRADIENT:
        interval_seconds = max(1, int(interval_hours * 3600))
        steps = max(1, math.ceil(max(late_by_seconds, 1) / interval_seconds))
        penalty = points * steps
    else:
        steps = 1
        penalty = points

    floor_for_this_score = min(original_score, min_score)
    after_penalty = max(original_score - penalty, floor_for_this_score)
    cap_applied = False
    if cap is not None and after_penalty > cap:
        after_penalty = cap
        cap_applied = True

    final_score = _normalize_score_value(after_penalty)
    penalty_applied = max(0.0, original_score - float(final_score))
    message_parts = [
        f"补交扣分：原始分 {original_score:g}",
        f"迟交 {format_late_duration(late_by_seconds)}",
    ]
    if strategy == LATE_PENALTY_GRADIENT:
        message_parts.append(f"按 {steps} 个梯度扣 {penalty_applied:g} 分")
    else:
        message_parts.append(f"定量扣 {penalty_applied:g} 分")
    if cap_applied:
        message_parts.append(f"补交最高分 {cap:g}")
    message_parts.append(f"最终分 {final_score:g}")
    return {
        "applied": True,
        "original_score": _normalize_score_value(original_score),
        "final_score": final_score,
        "penalty_points": _normalize_score_value(penalty_applied),
        "late_by_seconds": late_by_seconds,
        "score_cap_applied": cap_applied,
        "message": "，".join(message_parts) + "。",
    }


def append_late_policy_feedback(feedback_md: Any, adjustment: dict[str, Any]) -> str:
    text = str(feedback_md or "").strip()
    if not adjustment.get("applied"):
        return text
    message = str(adjustment.get("message") or "").strip()
    if not message:
        return text
    block = f"## 补交扣分\n{message}"
    return f"{text}\n\n{block}".strip() if text else block


def format_late_duration(total_seconds: Any) -> str:
    try:
        seconds = max(0, int(total_seconds or 0))
    except (TypeError, ValueError):
        seconds = 0
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟"
    return "不足 1 分钟"


def serialize_assignment_time_state(
    assignment: dict[str, Any],
    *,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    now_dt = now_dt or utc_like_now()
    due_dt = parse_iso_like_datetime(assignment.get("due_at"))
    late_until_dt = parse_iso_like_datetime(assignment.get("late_submission_until"))
    status = str(assignment.get("effective_status") or assignment.get("status") or "").strip().lower()
    late_open = status == "published" and assignment_late_window_accepts(assignment, now_dt=now_dt)
    accepting = status == "published" and assignment_is_accepting_by_time(assignment, now_dt=now_dt)
    phase = "none"
    countdown_at = None
    remaining_seconds = None
    if due_dt is not None and status == "published" and due_dt > now_dt:
        phase = "regular"
        countdown_at = due_dt
        remaining_seconds = max(0, int((due_dt - now_dt).total_seconds()))
    elif late_open:
        phase = "late"
        countdown_at = late_until_dt
        if late_until_dt is not None:
            remaining_seconds = max(0, int((late_until_dt - now_dt).total_seconds()))
    elif due_dt is not None:
        phase = "closed"

    return {
        "id": assignment.get("id"),
        "assignment_id": assignment.get("id"),
        "server_now": dt_to_iso(now_dt),
        "status": status,
        "is_accepting_submissions": accepting,
        "deadline_phase": phase,
        "due_at": dt_to_iso(due_dt),
        "late_submission_until": dt_to_iso(late_until_dt),
        "countdown_at": dt_to_iso(countdown_at),
        "remaining_seconds": remaining_seconds,
        "is_late_submission_open": late_open,
        "late_policy_label": describe_late_policy(
            {
                "enabled": assignment.get("late_submission_enabled"),
                "late_penalty_strategy": assignment.get("late_penalty_strategy"),
                "late_penalty_interval_hours": assignment.get("late_penalty_interval_hours"),
                "late_penalty_points": assignment.get("late_penalty_points"),
                "late_penalty_min_score": assignment.get("late_penalty_min_score"),
                "late_score_cap": assignment.get("late_score_cap"),
            }
        ),
    }
