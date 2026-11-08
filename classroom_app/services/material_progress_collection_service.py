"""Atomic, replay-safe material telemetry using the shared measured-interval contract."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re

from ..db.connection import get_configured_db_engine
from .behavior_event_validation import HEARTBEAT_ACTIONS, validate_behavior_payload
from .behavior_presence_service import read_presence_sample, measure_presence, _load_coverage, COVERAGE_WINDOW_MS
from .behavior_tracking_service import record_behavior_batch
from .learning_progress_service import (record_material_learning_progress, get_material_mastery_check_context, get_student_learning_state,
                                      MATERIAL_COMPLETE_ACTIVE_SECONDS, MATERIAL_COMPLETE_TOTAL_SECONDS)

_BATCH = re.compile(r"^b1_[0-9]{10,16}_[A-Za-z0-9_-]+$")
_COVERAGE_KEY = "_collection_coverage_v2"
_ELAPSED_REMAINDER = "_collection_elapsed_remainder_v2"


class MaterialCollectionError(ValueError):
    def __init__(self, message, code="batch_payload_invalid", *, status=422, retryable=False):
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable


def _observed_ms(coverage, floor):
    # Visible/hidden can overlap across tabs. Elapsed reading is their union,
    # not the sum of independent signal counters.
    end = total = 0
    for left, right in sorted([span for signal in ("visible", "hidden") for span in coverage["ranges"].get(signal, [])]):
        left = max(left, floor)
        total += max(0, right - max(left, end))
        end = max(end, right)
    return total


def _response(conn, row, class_offering_id, student_id, material_id):
    state = get_student_learning_state(conn, class_offering_id, student_id)
    return {"status": "success", "completed": bool(row and row["completed"]), "mastered": bool(row and row["mastered"]),
            "mastery_check": get_material_mastery_check_context(conn, class_offering_id=class_offering_id, student_id=student_id, material_id=material_id),
            "progress_pending": state.get("snapshot_status") != "ready", "material_progress_missing": row is None,
            "progress": {key: state.get(key) for key in ("score", "progress_percent", "eligible_stage")}}


def collect_material_progress(conn, *, class_offering_id, user, payload, received_at=None):
    """Caller authorizes/locks parents and commits progress plus inbox together.

    Per-material interval union prevents double credit across pending retries,
    tail beacons and tabs. Classroom presence uses the existing durable inbox
    and its wider per-class union. No worker/process is created per reader.
    """
    now = received_at or datetime.now()
    batch_id = payload.get("batch_id")
    if not isinstance(batch_id, str) or not _BATCH.fullmatch(batch_id):
        raise MaterialCollectionError("材料采集批次编号无效。")
    student_id, material_id = int(user["id"]), int(payload["material_id"])
    if user.get("role") != "student" or payload.get("user_key") != f"student:{student_id}":
        raise MaterialCollectionError("账号已更换，请重新打开课堂。", "identity_changed", status=409, retryable=True)
    events = payload.get("events") or []
    if (not isinstance(events, list) or not 1 <= len(events) <= 20
            or any(not isinstance(event, dict) or not isinstance(event.get("payload", {}), dict) for event in events)):
        raise MaterialCollectionError("材料采集批次没有观测事件。")
    page_key = payload.get("page_key")
    # The immutable inbox hash must cover the material fields too. Enrich an
    # existing event, without adding an artificial activity/reward event.
    wire_events = [dict(event) for event in events]
    context = {key: payload.get(key) for key in ("material_id", "session_id", "scroll_ratio", "completed")}
    wire_events[0]["payload"] = {**(wire_events[0].get("payload") or {}), "material_progress_context": context}
    validate_behavior_payload(events=wire_events, page_key=page_key, display_name="", session_started_at=user.get("login_time"))
    canonical = json.dumps({"events": wire_events, "page_key": page_key}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    batch_key = hashlib.sha256(json.dumps(["student", student_id, class_offering_id, batch_id]).encode()).hexdigest()
    postgres = get_configured_db_engine() == "postgres"
    if postgres:
        # Also serialize the first UPSERT before a row exists. Different
        # students/materials remain independent and route timeouts are bounded.
        key = int.from_bytes(hashlib.blake2s(f"{class_offering_id}:{student_id}:{material_id}".encode(), digest_size=4).digest(), "big", signed=True)
        conn.execute("SELECT pg_advisory_xact_lock(?, ?)", (0x4D505247, key))
    previous = conn.execute("SELECT payload_hash FROM behavior_write_inbox WHERE batch_key=?", (batch_key,)).fetchone()
    if previous and previous["payload_hash"] != payload_hash:
        raise MaterialCollectionError("材料采集批次编号已用于其他内容。", "batch_payload_conflict", status=409)
    row = conn.execute("SELECT * FROM learning_material_progress WHERE class_offering_id=? AND student_id=? AND material_id=?" +
                       (" FOR UPDATE" if postgres else ""), (class_offering_id, student_id, material_id)).fetchone()
    if previous:
        # Unknown-commit retries must not update view_count, timestamps, flags,
        # dirty versions or material counters a second time.
        result = _response(conn, row, class_offering_id, student_id, material_id)
    else:
        try:
            metadata = json.loads(row["metadata_json"] or "{}") if row else {}
            if not isinstance(metadata, dict):
                raise ValueError("invalid metadata shape")
            coverage = metadata.get(_COVERAGE_KEY, "{}")
            if not isinstance(coverage, str) or _load_coverage(coverage) is None:
                raise ValueError("invalid coverage shape")
            remainder = metadata.get(_ELAPSED_REMAINDER, 0)
            if type(remainder) is not int or not 0 <= remainder < 1000:
                raise ValueError("invalid elapsed remainder")
            duration_ms = activity = 0
            for event in events:
                if event.get("action_type") not in HEARTBEAT_ACTIONS:
                    continue
                sample = read_presence_sample(event.get("payload") or {}, now, user.get("login_time"))
                if sample is None:
                    continue  # The durable behavior worker records rejection reasons.
                old_coverage = _load_coverage(coverage)
                floor = max(old_coverage["reference"], sample.received_ms) - COVERAGE_WINDOW_MS
                before_ms = _observed_ms(old_coverage, floor) if sample.interval else 0
                reasons = []
                measured = measure_presence(coverage, sample, session_since=0, rolling_since=0, page_key=page_key, diagnostics=reasons)
                if measured is None or "presence_coverage_capacity" in reasons:
                    raise ValueError("invalid or full material coverage")
                coverage, deltas, _ = measured
                if sample.interval:
                    duration_ms += max(0, _observed_ms(_load_coverage(coverage), floor) - before_ms)
                activity += deltas["learning"]
            duration, remainder = divmod(duration_ms + remainder, 1000)
        except (ValueError, TypeError) as exc:
            raise MaterialCollectionError("材料计时恢复状态异常，原数据已保留。", "material_collection_state_invalid", status=409, retryable=True) from exc
        cumulative_active = int(row["active_seconds"] or 0) + activity if row else activity
        cumulative_elapsed = int(row["accumulated_seconds"] or 0) + duration if row else duration
        completed = bool(payload.get("completed") or cumulative_active >= MATERIAL_COMPLETE_ACTIVE_SECONDS
                         or cumulative_elapsed >= MATERIAL_COMPLETE_TOTAL_SECONDS)
        result = record_material_learning_progress(conn, class_offering_id=class_offering_id, student_id=student_id,
            material_id=material_id, session_id=payload.get("session_id"), duration_seconds=duration,
            active_seconds=activity, scroll_ratio=payload.get("scroll_ratio") or 0, completed=completed,
            metadata={**metadata, "page_key": page_key, _COVERAGE_KEY: coverage, _ELAPSED_REMAINDER: remainder}, defer_projection=True, measured_interval=True)
    # No further writes/IO after intake: its global capacity lock is released
    # immediately by the caller's commit. Both facts either persist or roll back.
    receipt = record_behavior_batch(class_offering_id=class_offering_id, user_pk=student_id, user_role="student",
        display_name=str(user.get("name") or f"student:{student_id}"), page_key=page_key, events=wire_events,
        session_started_at=user.get("login_time"), batch_id=batch_id, require_durable=True, wait=False, conn=conn)
    return {**result, "batch_id": batch_id, "current_user_key": f"student:{student_id}",
            "write_mode": "durable_queued" if receipt.get("write_mode") == "transaction_pending" else receipt["write_mode"],
            "degraded": bool(receipt.get("degraded")), "duplicate": previous is not None}
