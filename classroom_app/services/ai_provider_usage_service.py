from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


AI_PROVIDER_USAGE_TAIL_BYTES = _bounded_env_int(
    "AI_PROVIDER_USAGE_TAIL_BYTES",
    8 * 1024 * 1024,
    minimum=256 * 1024,
    maximum=64 * 1024 * 1024,
)
AI_PROVIDER_USAGE_MAX_EVENTS = _bounded_env_int(
    "AI_PROVIDER_USAGE_MAX_EVENTS",
    10000,
    minimum=100,
    maximum=50000,
)


def provider_usage_log_path() -> Path:
    configured = Path(os.getenv("AI_USAGE_LOG_PATH", "logs/ai_usage.jsonl"))
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def _read_log_tail(path: Path, *, max_bytes: int = AI_PROVIDER_USAGE_TAIL_BYTES) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        start = max(0, end - max(1, int(max_bytes)))
        handle.seek(start)
        payload = handle.read()
    if start > 0:
        newline = payload.find(b"\n")
        payload = payload[newline + 1 :] if newline >= 0 else b""
    return payload.decode("utf-8", errors="ignore").splitlines()


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _usage_tokens(event: dict[str, Any]) -> tuple[int, int]:
    usage = event.get("provider_usage") if isinstance(event.get("provider_usage"), dict) else {}
    cost = event.get("cost_estimate") if isinstance(event.get("cost_estimate"), dict) else {}
    prompt_tokens = _safe_int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or cost.get("prompt_tokens")
    )
    completion_tokens = _safe_int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or cost.get("completion_tokens")
    )
    return prompt_tokens, completion_tokens


def _bucket() -> dict[str, Any]:
    return {
        "calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "duration_ms": 0.0,
        "estimated_cost_cny": 0.0,
        "cost_known_calls": 0,
    }


def build_provider_usage_snapshot(
    *,
    path: Path | None = None,
    days: int = 56,
    now: datetime | None = None,
) -> dict[str, Any]:
    log_path = path or provider_usage_log_path()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.astimezone(timezone.utc) - timedelta(days=max(1, int(days or 56)))
    events: list[dict[str, Any]] = []
    malformed_lines = 0
    for line in _read_log_tail(log_path):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        if not isinstance(event, dict) or event.get("event") != "ai_usage":
            continue
        created_at = _parse_datetime(event.get("finished_at") or event.get("started_at"))
        if created_at is None or created_at < cutoff:
            continue
        events.append(event)
    if len(events) > AI_PROVIDER_USAGE_MAX_EVENTS:
        events = events[-AI_PROVIDER_USAGE_MAX_EVENTS:]

    total = _bucket()
    by_model: dict[tuple[str, str], dict[str, Any]] = defaultdict(_bucket)
    by_task: dict[str, dict[str, Any]] = defaultdict(_bucket)

    def add(bucket: dict[str, Any], event: dict[str, Any]) -> None:
        status = str(event.get("status") or "unknown").strip().lower()
        prompt_tokens, completion_tokens = _usage_tokens(event)
        cost = event.get("cost_estimate") if isinstance(event.get("cost_estimate"), dict) else {}
        bucket["calls"] += 1
        bucket["successful_calls"] += 1 if status == "success" else 0
        bucket["failed_calls"] += 0 if status == "success" else 1
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["duration_ms"] += _safe_float(event.get("duration_ms"))
        if "estimated_cost" in cost:
            bucket["estimated_cost_cny"] += _safe_float(cost.get("estimated_cost"))
            bucket["cost_known_calls"] += 1

    for event in events:
        provider = str(event.get("platform") or "unknown").strip() or "unknown"
        model = str(event.get("model") or "unknown").strip() or "unknown"
        extra = event.get("extra") if isinstance(event.get("extra"), dict) else {}
        task_type = str(extra.get("task_type") or event.get("task_label") or "unknown").strip() or "unknown"
        for bucket in (total, by_model[(provider, model)], by_task[task_type]):
            add(bucket, event)

    def finalize(bucket: dict[str, Any]) -> dict[str, Any]:
        calls = _safe_int(bucket.get("calls"))
        return {
            **bucket,
            "total_tokens": _safe_int(bucket.get("prompt_tokens")) + _safe_int(bucket.get("completion_tokens")),
            "success_rate": round(_safe_int(bucket.get("successful_calls")) / calls * 100, 1) if calls else 0.0,
            "avg_duration_ms": round(_safe_float(bucket.get("duration_ms")) / calls) if calls else 0,
            "estimated_cost_cny": round(_safe_float(bucket.get("estimated_cost_cny")), 6),
        }

    model_items = []
    for (provider, model), bucket in by_model.items():
        model_items.append({"provider": provider, "model": model, **finalize(bucket)})
    model_items.sort(
        key=lambda item: (
            -_safe_float(item.get("estimated_cost_cny")),
            -_safe_int(item.get("calls")),
            item["provider"],
            item["model"],
        )
    )

    task_items = [
        {"task_type": task_type, **finalize(bucket)}
        for task_type, bucket in by_task.items()
    ]
    task_items.sort(key=lambda item: (-_safe_float(item.get("estimated_cost_cny")), item["task_type"]))

    return {
        "available": bool(events),
        "path_exists": log_path.exists(),
        "window_days": max(1, int(days or 56)),
        "events_read": len(events),
        "malformed_lines_skipped": malformed_lines,
        "tail_bytes_limit": AI_PROVIDER_USAGE_TAIL_BYTES,
        "summary": finalize(total),
        "model_items": model_items,
        "task_items": task_items,
    }
