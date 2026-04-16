from __future__ import annotations

import math
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


ROUTE_LATENCY_SAMPLE_LIMIT = 512
RECENT_HTTP_ERROR_LIMIT = 64
RECENT_WS_ERROR_LIMIT = 64


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _normalize_route(route_path: str | None, fallback_path: str | None = None) -> str:
    normalized = str(route_path or "").strip()
    if normalized:
        return normalized
    fallback = str(fallback_path or "").strip()
    return fallback or "/"


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return float(samples[0])

    ordered = sorted(samples)
    rank = max(0.0, min(1.0, percentile / 100.0)) * (len(ordered) - 1)
    lower_index = math.floor(rank)
    upper_index = math.ceil(rank)
    if lower_index == upper_index:
        return float(ordered[lower_index])

    lower_value = float(ordered[lower_index])
    upper_value = float(ordered[upper_index])
    weight = rank - lower_index
    return lower_value + (upper_value - lower_value) * weight


@dataclass(slots=True)
class _RouteMetric:
    count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    last_duration_ms: float = 0.0
    last_status_code: int = 0
    last_seen_at: str = ""
    last_error_at: str = ""
    last_error_message: str = ""
    status_counts: Counter[str] = field(default_factory=Counter)
    latency_samples_ms: deque[float] = field(
        default_factory=lambda: deque(maxlen=ROUTE_LATENCY_SAMPLE_LIMIT)
    )

    def to_snapshot(self, method: str, route_path: str) -> dict[str, Any]:
        sample_values = list(self.latency_samples_ms)
        return {
            "method": method,
            "route_path": route_path,
            "count": int(self.count),
            "error_count": int(self.error_count),
            "avg_duration_ms": round(self.total_duration_ms / self.count, 2) if self.count else 0.0,
            "p95_duration_ms": round(_percentile(sample_values, 95.0), 2),
            "max_duration_ms": round(self.max_duration_ms, 2),
            "last_duration_ms": round(self.last_duration_ms, 2),
            "last_status_code": int(self.last_status_code),
            "last_seen_at": self.last_seen_at,
            "last_error_at": self.last_error_at,
            "last_error_message": self.last_error_message,
            "status_counts": dict(self.status_counts),
        }


@dataclass(slots=True)
class _WebSocketRoomMetric:
    active_connections: int = 0
    total_connections: int = 0
    disconnects: int = 0
    received_messages: int = 0
    sent_messages: int = 0
    error_count: int = 0
    last_seen_at: str = ""
    last_error_at: str = ""
    last_error_message: str = ""

    def to_snapshot(self, room_id: int) -> dict[str, Any]:
        return {
            "room_id": int(room_id),
            "active_connections": int(self.active_connections),
            "total_connections": int(self.total_connections),
            "disconnects": int(self.disconnects),
            "received_messages": int(self.received_messages),
            "sent_messages": int(self.sent_messages),
            "error_count": int(self.error_count),
            "last_seen_at": self.last_seen_at,
            "last_error_at": self.last_error_at,
            "last_error_message": self.last_error_message,
        }


_lock = threading.Lock()
_started_at = time.perf_counter()
_started_at_iso = _utcnow_iso()
_active_http_requests = 0
_total_http_requests = 0
_total_http_errors = 0
_http_status_counts: Counter[str] = Counter()
_http_route_metrics: dict[tuple[str, str], _RouteMetric] = defaultdict(_RouteMetric)
_recent_http_errors: deque[dict[str, Any]] = deque(maxlen=RECENT_HTTP_ERROR_LIMIT)

_active_ws_connections = 0
_total_ws_connections = 0
_total_ws_disconnects = 0
_total_ws_received_messages = 0
_total_ws_sent_messages = 0
_total_ws_errors = 0
_ws_room_metrics: dict[int, _WebSocketRoomMetric] = defaultdict(_WebSocketRoomMetric)
_recent_ws_errors: deque[dict[str, Any]] = deque(maxlen=RECENT_WS_ERROR_LIMIT)


def begin_http_request() -> float:
    global _active_http_requests
    with _lock:
        _active_http_requests += 1
    return time.perf_counter()


def finish_http_request(
    *,
    started_at: float,
    method: str,
    route_path: str | None,
    fallback_path: str | None,
    status_code: int,
    error_message: str = "",
) -> None:
    global _active_http_requests, _total_http_requests, _total_http_errors

    ended_at = _utcnow_iso()
    normalized_route = _normalize_route(route_path, fallback_path)
    normalized_method = str(method or "GET").upper()
    normalized_status = int(status_code or 0)
    latency_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    error_text = str(error_message or "").strip()

    with _lock:
        _active_http_requests = max(0, _active_http_requests - 1)
        _total_http_requests += 1
        _http_status_counts[str(normalized_status)] += 1

        route_metric = _http_route_metrics[(normalized_method, normalized_route)]
        route_metric.count += 1
        route_metric.total_duration_ms += latency_ms
        route_metric.max_duration_ms = max(route_metric.max_duration_ms, latency_ms)
        route_metric.last_duration_ms = latency_ms
        route_metric.last_status_code = normalized_status
        route_metric.last_seen_at = ended_at
        route_metric.status_counts[str(normalized_status)] += 1
        route_metric.latency_samples_ms.append(latency_ms)

        if error_text or normalized_status >= 500:
            _total_http_errors += 1
            route_metric.error_count += 1
            route_metric.last_error_at = ended_at
            route_metric.last_error_message = error_text or f"http {normalized_status}"
            _recent_http_errors.append(
                {
                    "at": ended_at,
                    "method": normalized_method,
                    "route_path": normalized_route,
                    "status_code": normalized_status,
                    "message": route_metric.last_error_message,
                    "duration_ms": round(latency_ms, 2),
                }
            )


def record_websocket_connect(room_id: int) -> None:
    global _active_ws_connections, _total_ws_connections
    timestamp = _utcnow_iso()
    normalized_room_id = int(room_id)
    with _lock:
        _active_ws_connections += 1
        _total_ws_connections += 1
        room_metric = _ws_room_metrics[normalized_room_id]
        room_metric.active_connections += 1
        room_metric.total_connections += 1
        room_metric.last_seen_at = timestamp


def record_websocket_disconnect(room_id: int) -> None:
    global _active_ws_connections, _total_ws_disconnects
    timestamp = _utcnow_iso()
    normalized_room_id = int(room_id)
    with _lock:
        _active_ws_connections = max(0, _active_ws_connections - 1)
        _total_ws_disconnects += 1
        room_metric = _ws_room_metrics[normalized_room_id]
        room_metric.active_connections = max(0, room_metric.active_connections - 1)
        room_metric.disconnects += 1
        room_metric.last_seen_at = timestamp


def record_websocket_received(room_id: int, count: int = 1) -> None:
    global _total_ws_received_messages
    timestamp = _utcnow_iso()
    normalized_room_id = int(room_id)
    increment = max(0, int(count or 0))
    with _lock:
        _total_ws_received_messages += increment
        room_metric = _ws_room_metrics[normalized_room_id]
        room_metric.received_messages += increment
        room_metric.last_seen_at = timestamp


def record_websocket_sent(room_id: int, count: int = 1) -> None:
    global _total_ws_sent_messages
    timestamp = _utcnow_iso()
    normalized_room_id = int(room_id)
    increment = max(0, int(count or 0))
    with _lock:
        _total_ws_sent_messages += increment
        room_metric = _ws_room_metrics[normalized_room_id]
        room_metric.sent_messages += increment
        room_metric.last_seen_at = timestamp


def record_websocket_error(room_id: int, error_message: str) -> None:
    global _total_ws_errors
    timestamp = _utcnow_iso()
    normalized_room_id = int(room_id)
    normalized_error = str(error_message or "").strip() or "unknown websocket error"
    with _lock:
        _total_ws_errors += 1
        room_metric = _ws_room_metrics[normalized_room_id]
        room_metric.error_count += 1
        room_metric.last_error_at = timestamp
        room_metric.last_error_message = normalized_error
        room_metric.last_seen_at = timestamp
        _recent_ws_errors.append(
            {
                "at": timestamp,
                "room_id": normalized_room_id,
                "message": normalized_error,
            }
        )


def get_runtime_metrics_snapshot(*, top_routes: int = 20) -> dict[str, Any]:
    with _lock:
        route_entries = [
            metric.to_snapshot(method, route_path)
            for (method, route_path), metric in _http_route_metrics.items()
        ]
        route_entries.sort(
            key=lambda item: (
                -int(item["count"]),
                -int(item["error_count"]),
                item["method"],
                item["route_path"],
            )
        )

        ws_room_entries = [
            metric.to_snapshot(room_id)
            for room_id, metric in _ws_room_metrics.items()
        ]
        ws_room_entries.sort(
            key=lambda item: (
                -int(item["active_connections"]),
                -int(item["received_messages"]),
                int(item["room_id"]),
            )
        )

        return {
            "started_at": _started_at_iso,
            "uptime_seconds": round(max(0.0, time.perf_counter() - _started_at), 2),
            "http": {
                "active_requests": int(_active_http_requests),
                "total_requests": int(_total_http_requests),
                "total_errors": int(_total_http_errors),
                "status_counts": dict(_http_status_counts),
                "top_routes": route_entries[: max(1, int(top_routes or 1))],
                "recent_errors": list(_recent_http_errors),
            },
            "websocket": {
                "active_connections": int(_active_ws_connections),
                "total_connections": int(_total_ws_connections),
                "total_disconnects": int(_total_ws_disconnects),
                "received_messages": int(_total_ws_received_messages),
                "sent_messages": int(_total_ws_sent_messages),
                "total_errors": int(_total_ws_errors),
                "rooms": ws_room_entries,
                "recent_errors": list(_recent_ws_errors),
            },
        }
