"""Safe product-funnel events shared by career path and resume workbench."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ..db import schema_career_engagement as career_engagement_schema

ALLOWED_SURFACES = {"career", "resume", "job"}
ALLOWED_EVENTS = {
    "career_viewed",
    "career_quiz_started",
    "career_quiz_completed",
    "career_result_viewed",
    "career_direction_opened",
    "career_job_search_opened",
    "career_resume_started",
    "job_description_analyzed",
    "job_target_resume_started",
    "application_created",
    "application_status_changed",
    "resume_home_viewed",
    "resume_import_started",
    "resume_import_completed",
    "resume_created",
    "resume_previewed",
    "resume_optimization_started",
    "resume_optimized",
    "resume_exported",
}

# Analytics context is intentionally narrow.  Never add free-form resume/JD
# fields here; business data belongs in the owned domain tables instead.
SAFE_CONTEXT_KEYS = {
    "phase",
    "session_status",
    "career_tag",
    "target_position",
    "source",
    "resume_id",
    "job_id",
    "application_id",
    "status",
    "format",
    "mode",
    "result_count",
    "has_resume",
    "has_profile",
    "location_pref",
}

_CLIENT_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,80}$")
_SAVEPOINT_NAME = "career_event_tracking"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def sanitize_event_context(context: Any) -> dict[str, Any]:
    """Return the privacy-safe subset of an event context."""
    if not isinstance(context, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key in SAFE_CONTEXT_KEYS:
        if key not in context:
            continue
        value = context.get(key)
        if isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, int):
            cleaned[key] = value
        elif isinstance(value, float) and value == value:
            cleaned[key] = round(value, 3)
        elif isinstance(value, str):
            cleaned[key] = value.strip()[:120]
    return cleaned


def record_student_career_event(
    conn: Any,
    student_id: int,
    *,
    surface: str,
    event_name: str,
    context: Any = None,
    client_event_id: str = "",
) -> bool:
    """Append one validated event; return False when a client retry was deduped."""
    surface = str(surface or "").strip().lower()
    event_name = str(event_name or "").strip().lower()
    if surface not in ALLOWED_SURFACES:
        raise ValueError("未知的求职功能页面")
    if event_name not in ALLOWED_EVENTS:
        raise ValueError("未知的求职行为事件")

    event_id = str(client_event_id or "").strip()
    if event_id and not _CLIENT_EVENT_ID_RE.fullmatch(event_id):
        raise ValueError("行为事件标识格式不正确")

    career_engagement_schema.ensure_career_engagement_schema(conn)
    cursor = conn.execute(
        """
        INSERT INTO student_career_events
            (student_id, surface, event_name, context_json, client_event_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (student_id, client_event_id) DO NOTHING
        """,
        (
            int(student_id),
            surface,
            event_name,
            json.dumps(sanitize_event_context(context), ensure_ascii=False),
            event_id or None,
            _now(),
        ),
    )
    return cursor.rowcount != 0


def record_student_career_event_safely(conn: Any, student_id: int, **kwargs: Any) -> bool:
    """Record an event without leaving the surrounding business transaction aborted."""
    try:
        conn.execute(f"SAVEPOINT {_SAVEPOINT_NAME}")
    except Exception:
        return False
    try:
        recorded = record_student_career_event(conn, student_id, **kwargs)
        conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
        return recorded
    except Exception:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT_NAME}")
            conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
        except Exception:
            pass
        # The runtime-schema flag may have been set by DDL that was rolled back
        # with this savepoint. Rechecking CREATE IF NOT EXISTS is harmless and
        # prevents a transient analytics failure from poisoning later attempts.
        career_engagement_schema._SCHEMA_READY = False
        return False
