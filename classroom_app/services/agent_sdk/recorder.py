"""Structured, user-facing Agent events.

The Agent window renders each kind differently, so every event states *what
kind of step* it is (requirement: 思考是思考，决定是决定，使用工具是使用工具，
操作是操作，疑问是疑问):

  thinking          model reasoning (collapsed by default)       detail.text
  decision          an explicit decision via record_decision     detail.decision/rationale/next_steps
  tool_call         read-only tool use (query, read, search)     detail.call_id/tool/label/target
  tool_result       its outcome                                  detail.call_id/ok/summary
  operation         a state-changing platform action             detail.call_id/label/method/path/safety_check
  operation_result  its outcome (HTTP/business receipt)          detail.call_id/ok/status/http_status/summary
  guard             a safety self-check was refused / hard block detail.code/message
  assistant_text    an interim explanation from the model        detail.text
  artifact          a file saved to the task                     detail.path/name/size
  question_requested / question_answered                         detail.question
  usage             model usage for the segment                  detail.requests/input_tokens/output_tokens

Messages are short Chinese sentences; long text lives in ``detail``.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from ...database import get_db_connection

MAX_DETAIL_TEXT = 6000
_CREDENTIAL = re.compile(r"lsagt_[A-Za-z0-9_-]{16,128}")
_SECRET_KEYS = re.compile(r"token|password|credential|secret|authorization|cookie|api[_-]?key", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _CREDENTIAL.sub("[task credential]", value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items() if not _SECRET_KEYS.search(str(key))}
    return value


def clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[: max(0, limit - 1)].rstrip() + "…"


class EventRecorder:
    def __init__(self, task_id: int):
        self.task_id = int(task_id)

    def emit_sync(self, event_type: str, message: str, detail: dict[str, Any] | None = None) -> None:
        from ..agent_task_service import append_task_event

        safe = redact(dict(detail or {}))
        for key in ("text", "rationale", "summary"):
            if isinstance(safe.get(key), str):
                safe[key] = clip(safe[key], MAX_DETAIL_TEXT)
        with get_db_connection() as conn:
            append_task_event(conn, self.task_id, event_type, clip(redact(message), 480), safe, commit=True)

    async def emit(self, event_type: str, message: str, detail: dict[str, Any] | None = None) -> None:
        try:
            await asyncio.to_thread(self.emit_sync, event_type, message, detail)
        except Exception as exc:  # events are advisory; never break the run
            print(f"[AGENT_SDK] event write failed for task {self.task_id}: {exc}")
