"""In-process progress board for Git teaching-repository operations.

``execute_material_repository_action`` is one blocking request that can take
up to several minutes (export → pull → compare → publish → lesson projection).
The library UI polls ``GET /api/materials/{id}/repository/progress`` while the
request is in flight and renders the current phase. The board is per-process
(like the per-repository asyncio lock that already serialises the operation);
a poll that lands on a process without a record simply reports ``idle``.

Called by: ``materials_git_service.execute_material_repository_action`` (writes)
and ``routers/materials_parts/library.py`` (reads).
"""
from __future__ import annotations

import threading
import time
from typing import Any

PHASES: list[tuple[str, str, int]] = [
    ("queued", "等待仓库空闲", 4),
    ("export", "导出服务器工作区", 12),
    ("identity", "准备 Git 身份", 18),
    ("pull", "拉取远程更新", 40),
    ("prepare", "比对本地与远程差异", 60),
    ("publish", "写回材料库", 78),
    ("bindings", "对应课堂课次", 92),
    ("done", "完成", 100),
]
_PHASE_INDEX = {key: index for index, (key, _, _) in enumerate(PHASES)}
_PHASE_META = {key: (label, percent) for key, label, percent in PHASES}
_RETENTION_SECONDS = 120.0

_board: dict[int, dict[str, Any]] = {}
_guard = threading.Lock()


def _now() -> float:
    return time.time()


def _prune_locked() -> None:
    stale = [key for key, value in _board.items() if value.get("finished_at") and _now() - value["finished_at"] > _RETENTION_SECONDS]
    for key in stale:
        _board.pop(key, None)


def start(material_id: int, action: str) -> None:
    with _guard:
        _prune_locked()
        _board[int(material_id)] = {
            "material_id": int(material_id),
            "action": str(action or ""),
            "phase": "queued",
            "label": _PHASE_META["queued"][0],
            "percent": _PHASE_META["queued"][1],
            "status": "running",
            "message": "",
            "started_at": _now(),
            "updated_at": _now(),
            "finished_at": None,
            "steps": [{"phase": "queued", "label": _PHASE_META["queued"][0], "at": _now()}],
        }


def advance(material_id: int, phase: str, message: str = "") -> None:
    with _guard:
        record = _board.get(int(material_id))
        if not record or record.get("status") != "running":
            return
        if phase not in _PHASE_META:
            return
        if _PHASE_INDEX[phase] < _PHASE_INDEX.get(record["phase"], 0):
            return
        label, percent = _PHASE_META[phase]
        record.update(phase=phase, label=label, percent=percent, message=str(message or ""), updated_at=_now())
        record["steps"].append({"phase": phase, "label": label, "at": _now()})


def finish(material_id: int, status: str, message: str = "") -> None:
    with _guard:
        record = _board.get(int(material_id))
        if not record:
            return
        ok = str(status or "") == "success"
        record.update(
            phase="done" if ok else record.get("phase", "queued"),
            label="完成" if ok else ("需要登录" if status == "auth_required" else "失败"),
            percent=100 if ok else record.get("percent", 0),
            status=str(status or "failed"),
            message=str(message or ""),
            updated_at=_now(),
            finished_at=_now(),
        )
        record["steps"].append({"phase": "done" if ok else "failed", "label": record["label"], "at": _now()})


def snapshot(material_id: int) -> dict[str, Any]:
    with _guard:
        _prune_locked()
        record = _board.get(int(material_id))
        if not record:
            return {"material_id": int(material_id), "status": "idle", "phase": "", "label": "", "percent": 0, "message": "", "elapsed_seconds": 0, "steps": []}
        elapsed = (record["finished_at"] or _now()) - record["started_at"]
        return {
            "material_id": record["material_id"],
            "action": record["action"],
            "status": record["status"],
            "phase": record["phase"],
            "label": record["label"],
            "percent": int(record["percent"]),
            "message": record["message"],
            "elapsed_seconds": round(elapsed, 1),
            "steps": [dict(step) for step in record["steps"]],
            "phases": [{"phase": key, "label": label, "percent": percent} for key, label, percent in PHASES],
        }


def is_running(material_id: int) -> bool:
    with _guard:
        record = _board.get(int(material_id))
        return bool(record and record.get("status") == "running")
