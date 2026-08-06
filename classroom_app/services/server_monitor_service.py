"""Server monitoring dashboard data for super admins (监控大屏).

Aggregates three data planes into one snapshot:

- Host/container resources via psutil (CPU, memory, disk, network, process
  tree).  Inside Docker the process tree is namespaced to the app container,
  which is exactly the actionable slice for the platform operator.
- Platform traffic from ``runtime_metrics_service`` (request totals, latency,
  status codes, websocket connect/disconnect churn).
- A short in-memory history ring (sampled by a lazy daemon thread) that feeds
  the dashboard's trend charts without any persistence.

Every entry point degrades gracefully: if psutil is unavailable the snapshot
still returns traffic data with ``resource_ok=False`` instead of raising.
"""

from __future__ import annotations

import gc
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard dep in prod images
    psutil = None  # type: ignore[assignment]

from .runtime_metrics_service import get_runtime_metrics_snapshot


HISTORY_SAMPLE_INTERVAL_SECONDS = 5.0
HISTORY_SAMPLE_LIMIT = 180  # ~15 minutes of 5s samples
PROCESS_LIMIT = 120
TOP_ROUTE_LIMIT = 12
_PROTECTED_PROCESS_NAMES = {"postgres", "nginx", "dockerd", "containerd", "systemd", "init"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def monitor_available() -> bool:
    return psutil is not None


# ---------------------------------------------------------------------------
# History sampling (in-memory ring, lazily started daemon thread)
# ---------------------------------------------------------------------------

_history_lock = threading.Lock()
_history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SAMPLE_LIMIT)
_sampler_started = False
_last_request_totals: dict[str, int] = {"requests": 0, "errors": 0}


def _collect_history_sample() -> dict[str, Any] | None:
    if psutil is None:
        return None
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        runtime = get_runtime_metrics_snapshot(top_routes=1)
        http = runtime.get("http", {})
        total_requests = int(http.get("total_requests", 0))
        total_errors = int(http.get("total_errors", 0))
    except Exception:
        return None

    with _history_lock:
        request_delta = max(0, total_requests - _last_request_totals["requests"])
        error_delta = max(0, total_errors - _last_request_totals["errors"])
        _last_request_totals["requests"] = total_requests
        _last_request_totals["errors"] = total_errors

    return {
        "at": _utcnow_iso(),
        "cpu_percent": round(float(cpu_percent), 1),
        "memory_percent": round(float(memory.percent), 1),
        "memory_used_mb": round(memory.used / (1024 * 1024), 1),
        "requests_delta": request_delta,
        "errors_delta": error_delta,
        "active_requests": int(http.get("active_requests", 0)),
    }


def _sampler_loop() -> None:  # pragma: no cover - timing loop, logic tested via sample fn
    while True:
        sample = _collect_history_sample()
        if sample is not None:
            with _history_lock:
                _history.append(sample)
        time.sleep(HISTORY_SAMPLE_INTERVAL_SECONDS)


def ensure_history_sampler_started() -> None:
    global _sampler_started
    if _sampler_started or psutil is None:
        return
    with _history_lock:
        if _sampler_started:
            return
        _sampler_started = True
    # Prime the cpu_percent baseline so the first real sample is meaningful.
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass
    thread = threading.Thread(target=_sampler_loop, name="server-monitor-sampler", daemon=True)
    thread.start()


def get_history_snapshot() -> list[dict[str, Any]]:
    with _history_lock:
        return list(_history)


# ---------------------------------------------------------------------------
# Resource snapshot
# ---------------------------------------------------------------------------

def _safe_disk_usage(path: str) -> dict[str, Any] | None:
    try:
        usage = psutil.disk_usage(path)
    except Exception:
        return None
    return {
        "path": path,
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
        "percent": round(float(usage.percent), 1),
    }


def build_resource_snapshot() -> dict[str, Any]:
    """CPU/memory/disk/network state; ``resource_ok=False`` when psutil is absent."""
    if psutil is None:
        return {"resource_ok": False, "reason": "psutil 未安装"}

    cpu_percent = psutil.cpu_percent(interval=None)
    per_cpu = [round(float(value), 1) for value in psutil.cpu_percent(interval=None, percpu=True)]
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    load_avg: list[float] = []
    if hasattr(psutil, "getloadavg"):
        try:
            load_avg = [round(float(value), 2) for value in psutil.getloadavg()]
        except OSError:
            load_avg = []

    disk = _safe_disk_usage("/") or _safe_disk_usage(os.path.abspath(os.sep)) or {}

    net_io: dict[str, Any] = {}
    try:
        counters = psutil.net_io_counters()
        net_io = {
            "sent_mb": round(counters.bytes_sent / (1024 * 1024), 1),
            "recv_mb": round(counters.bytes_recv / (1024 * 1024), 1),
            "packets_sent": int(counters.packets_sent),
            "packets_recv": int(counters.packets_recv),
            "err_in": int(counters.errin),
            "err_out": int(counters.errout),
            "drop_in": int(counters.dropin),
            "drop_out": int(counters.dropout),
        }
    except Exception:
        net_io = {}

    boot_time_iso = ""
    try:
        boot_time_iso = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        boot_time_iso = ""

    return {
        "resource_ok": True,
        "sampled_at": _utcnow_iso(),
        "cpu": {
            "percent": round(float(cpu_percent), 1),
            "per_cpu": per_cpu,
            "core_count": psutil.cpu_count(logical=True) or len(per_cpu) or 1,
            "load_avg": load_avg,
        },
        "memory": {
            "total_mb": round(memory.total / (1024 * 1024), 1),
            "used_mb": round(memory.used / (1024 * 1024), 1),
            "available_mb": round(memory.available / (1024 * 1024), 1),
            "cached_mb": round(getattr(memory, "cached", 0) / (1024 * 1024), 1),
            "percent": round(float(memory.percent), 1),
            "swap_total_mb": round(swap.total / (1024 * 1024), 1),
            "swap_used_mb": round(swap.used / (1024 * 1024), 1),
            "swap_percent": round(float(swap.percent), 1),
        },
        "disk": disk,
        "network": net_io,
        "boot_time": boot_time_iso,
        "process_count": len(psutil.pids()),
    }


# ---------------------------------------------------------------------------
# Process tree
# ---------------------------------------------------------------------------

def build_process_tree(*, limit: int = PROCESS_LIMIT) -> dict[str, Any]:
    """Flat process list plus parent links; the frontend folds it into a tree."""
    if psutil is None:
        return {"resource_ok": False, "reason": "psutil 未安装", "processes": []}

    current_pid = os.getpid()
    entries: list[dict[str, Any]] = []
    # Keep the attr set minimal: on Windows every extra per-process attribute
    # (status, num_threads, cmdline) costs seconds across a full host.  The
    # cmdline is backfilled below only for the pruned display subset.
    for proc in psutil.process_iter(attrs=["pid", "ppid", "name", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            memory_info = info.get("memory_info")
            entries.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "ppid": int(info.get("ppid") or 0),
                    "name": str(info.get("name") or "")[:60],
                    "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                    "memory_mb": round((memory_info.rss if memory_info else 0) / (1024 * 1024), 1),
                    "cmdline": "",
                    "is_self": int(info.get("pid") or 0) == current_pid,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    entries.sort(key=lambda item: (-item["memory_mb"], -item["cpu_percent"], item["pid"]))
    total = len(entries)
    kept = entries[: max(1, int(limit or 1))]
    kept_pids = {entry["pid"] for entry in kept}
    # Keep ancestors of kept entries so the tree renders without orphans.
    by_pid = {entry["pid"]: entry for entry in entries}
    for entry in list(kept):
        cursor = entry
        while cursor["ppid"] in by_pid and cursor["ppid"] not in kept_pids:
            parent = by_pid[cursor["ppid"]]
            kept.append(parent)
            kept_pids.add(parent["pid"])
            cursor = parent

    for entry in kept:
        try:
            entry["cmdline"] = " ".join(psutil.Process(entry["pid"]).cmdline() or [])[:180]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            entry["cmdline"] = ""

    return {
        "resource_ok": True,
        "sampled_at": _utcnow_iso(),
        "total_count": total,
        "shown_count": len(kept),
        "self_pid": current_pid,
        "processes": kept,
    }


class ProcessActionError(ValueError):
    """Raised when a process action is rejected or fails."""


def terminate_process(pid: int, *, force: bool = False) -> dict[str, Any]:
    """Terminate (SIGTERM) or kill (SIGKILL) a process, with safety guards."""
    if psutil is None:
        raise ProcessActionError("psutil 未安装，无法管理进程。")

    normalized_pid = int(pid)
    if normalized_pid <= 1:
        raise ProcessActionError("禁止操作 PID 0/1（系统初始化进程）。")
    if normalized_pid == os.getpid():
        raise ProcessActionError("禁止终止 Web 服务自身进程，否则平台会立即离线。")

    try:
        proc = psutil.Process(normalized_pid)
        name = proc.name()
    except psutil.NoSuchProcess as exc:
        raise ProcessActionError(f"进程 {normalized_pid} 不存在或已退出。") from exc
    except psutil.AccessDenied as exc:
        raise ProcessActionError(f"没有权限读取进程 {normalized_pid}。") from exc

    if name.lower() in _PROTECTED_PROCESS_NAMES:
        raise ProcessActionError(f"进程 {name} 属于受保护的基础服务，禁止在大屏终止。")

    try:
        if force:
            proc.kill()
        else:
            proc.terminate()
        proc.wait(timeout=3)
        alive = False
    except psutil.TimeoutExpired:
        alive = True
    except psutil.NoSuchProcess:
        alive = False
    except psutil.AccessDenied as exc:
        raise ProcessActionError(f"没有权限终止进程 {normalized_pid}（{name}）。") from exc

    return {
        "pid": normalized_pid,
        "name": name,
        "forced": bool(force),
        "alive_after": alive,
    }


# ---------------------------------------------------------------------------
# Memory optimization
# ---------------------------------------------------------------------------

def optimize_memory() -> dict[str, Any]:
    """Best-effort in-process memory release: gc + glibc malloc_trim on Linux."""
    rss_before_mb = 0.0
    if psutil is not None:
        try:
            rss_before_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        except Exception:
            rss_before_mb = 0.0

    collected = gc.collect()

    trimmed = False
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
        trimmed = True
    except Exception:
        trimmed = False

    rss_after_mb = rss_before_mb
    if psutil is not None:
        try:
            rss_after_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        except Exception:
            rss_after_mb = rss_before_mb

    return {
        "collected_objects": int(collected),
        "malloc_trimmed": trimmed,
        "rss_before_mb": round(rss_before_mb, 1),
        "rss_after_mb": round(rss_after_mb, 1),
        "freed_mb": round(max(0.0, rss_before_mb - rss_after_mb), 1),
    }


# ---------------------------------------------------------------------------
# Combined snapshot + AI insight payload
# ---------------------------------------------------------------------------

def build_monitor_snapshot() -> dict[str, Any]:
    ensure_history_sampler_started()
    runtime = get_runtime_metrics_snapshot(top_routes=TOP_ROUTE_LIMIT)
    http = runtime.get("http", {})
    websocket = runtime.get("websocket", {})
    total_connections = int(websocket.get("total_connections", 0))
    total_disconnects = int(websocket.get("total_disconnects", 0))
    return {
        "generated_at": _utcnow_iso(),
        "resources": build_resource_snapshot(),
        "history": get_history_snapshot(),
        "traffic": {
            "uptime_seconds": runtime.get("uptime_seconds", 0),
            "started_at": runtime.get("started_at", ""),
            "active_requests": int(http.get("active_requests", 0)),
            "total_requests": int(http.get("total_requests", 0)),
            "total_errors": int(http.get("total_errors", 0)),
            "status_counts": dict(http.get("status_counts", {})),
            "top_routes": list(http.get("top_routes", [])),
            "recent_errors": list(http.get("recent_errors", []))[-10:],
        },
        "connections": {
            "ws_active": int(websocket.get("active_connections", 0)),
            "ws_total": total_connections,
            "ws_disconnects": total_disconnects,
            "ws_errors": int(websocket.get("total_errors", 0)),
            "ws_loss_rate": round(total_disconnects / total_connections * 100, 1) if total_connections else 0.0,
            "recent_ws_errors": list(websocket.get("recent_errors", []))[-10:],
        },
    }


AI_INSIGHT_SYSTEM_PROMPT = (
    "你是服务器运维监控分析助手。用户提供一段 JSON 格式的服务器与平台运行指标"
    "（CPU/内存/磁盘/进程/请求量/延迟/错误/WebSocket 连接），请给出简明的中文分析，"
    "严格返回 JSON：\n"
    '{"summary": "一句话总体评价", "health_score": 0-100 的整数, '
    '"highlights": ["值得注意的正面指标"], "risks": ["风险点，含具体数字"], '
    '"suggestions": ["可执行的优化建议"]}\n'
    "highlights/risks/suggestions 每项不超过 40 字，各最多 4 条；没有内容时返回空数组。"
    "不要编造指标中不存在的数据，不要输出 JSON 以外的文字。"
)


def build_ai_insight_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compact metrics digest for the fast text model (keeps the prompt small)."""
    resources = snapshot.get("resources", {})
    traffic = snapshot.get("traffic", {})
    connections = snapshot.get("connections", {})
    history = snapshot.get("history", [])
    cpu = resources.get("cpu", {}) if resources.get("resource_ok") else {}
    memory = resources.get("memory", {}) if resources.get("resource_ok") else {}
    disk = resources.get("disk", {}) if resources.get("resource_ok") else {}

    top_routes = [
        {
            "route": f"{route.get('method', '')} {route.get('route_path', '')}".strip(),
            "count": route.get("count", 0),
            "p95_ms": route.get("p95_duration_ms", 0),
            "errors": route.get("error_count", 0),
        }
        for route in list(traffic.get("top_routes", []))[:6]
    ]
    recent_cpu = [sample.get("cpu_percent", 0) for sample in history[-24:]]
    recent_memory = [sample.get("memory_percent", 0) for sample in history[-24:]]

    return {
        "cpu_percent": cpu.get("percent"),
        "cpu_load_avg": cpu.get("load_avg"),
        "cpu_core_count": cpu.get("core_count"),
        "memory_percent": memory.get("percent"),
        "memory_used_mb": memory.get("used_mb"),
        "memory_total_mb": memory.get("total_mb"),
        "swap_percent": memory.get("swap_percent"),
        "disk_percent": disk.get("percent"),
        "disk_free_gb": disk.get("free_gb"),
        "uptime_seconds": traffic.get("uptime_seconds"),
        "active_requests": traffic.get("active_requests"),
        "total_requests": traffic.get("total_requests"),
        "total_errors": traffic.get("total_errors"),
        "status_counts": traffic.get("status_counts"),
        "top_routes": top_routes,
        "ws_active": connections.get("ws_active"),
        "ws_disconnects": connections.get("ws_disconnects"),
        "ws_loss_rate_percent": connections.get("ws_loss_rate"),
        "recent_cpu_percent_series": recent_cpu,
        "recent_memory_percent_series": recent_memory,
    }
