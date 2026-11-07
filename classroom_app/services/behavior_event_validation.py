"""Reject deterministic behavior-data failures before durable acceptance."""
from __future__ import annotations

import json


MAX_PAYLOAD_BYTES = 64 * 1024
HEARTBEAT_ACTIONS = {"presence_heartbeat", "page_presence", "heartbeat"}


def is_measured_presence(payload: dict) -> bool:
    return type(payload.get("presence_version")) is int and payload["presence_version"] == 2


class BehaviorValidationError(ValueError):
    """A batch cannot be applied with the existing behavior parsing rules."""


class BehaviorPayloadTooLarge(BehaviorValidationError):
    """A new batch exceeds the bounded intake contract."""


def _text(value, field: str) -> None:
    text = str(value or "")
    if "\x00" in text:
        raise BehaviorValidationError(f"{field}不能包含空字符。")
    try:
        text.encode("utf-8")
    except UnicodeError as exc:
        raise BehaviorValidationError(f"{field}必须是有效的 Unicode 文本。") from exc


def validate_behavior_payload(*, events, page_key, display_name="", session_started_at=None) -> str:
    """Validate without changing payloads, heartbeat coercion or scoring.

    Server telemetry retains its historical ``default=str`` serialization for
    datetime/tuple-like metadata. HTTP JSON still has to contain finite values.
    """
    _text(page_key, "页面标识")
    _text(display_name, "用户名称")
    _text(session_started_at, "会话时间")
    try:
        canonical = json.dumps({"events": events, "page_key": page_key}, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
        size = len(canonical.encode("utf-8"))
        serialized_events = json.loads(canonical)["events"]
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise BehaviorValidationError("行为数据必须是有限、有效的 JSON 文本。") from exc
    if size > MAX_PAYLOAD_BYTES:
        raise BehaviorPayloadTooLarge("行为批次过大，请拆分后重试。")
    if not isinstance(serialized_events, list):
        raise BehaviorValidationError("行为批次必须包含事件列表。")
    for event in serialized_events:
        if not isinstance(event, dict):
            raise BehaviorValidationError("行为事件必须是对象。")
        for field in ("action_type", "summary_text", "page_key"):
            _text(event.get(field), field)
        action = str(event.get("action_type") or "page_action").strip().lower().replace("-", "_").replace(" ", "_")[:64]
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if action in HEARTBEAT_ACTIONS and not is_measured_presence(payload):
            try:
                # Match the application exactly, including legacy numeric
                # strings, finite floats and empty-value fallback to zero.
                int(payload.get("idle_seconds") or 0)
            except (ValueError, TypeError, OverflowError) as exc:
                raise BehaviorValidationError("心跳 idle_seconds 必须可以转换为有限整数。") from exc
    return canonical
