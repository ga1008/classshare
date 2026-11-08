"""Read-only, windowed classroom evidence; these facts are not attendance decisions.

Login audits prove a platform login, accepted classroom receipts prove access,
and validated browser presence proves measured learning. Keeping those channels
separate is essential: an absent heartbeat is not evidence of student absence.
The caller must authorize teacher ownership before enabling login IP disclosure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterator

from .behavior_presence_service import COVERAGE_WINDOW_MS, MAX_FUTURE_SKEW_MS, MAX_SAMPLE_AGE_MS, _load_coverage
from .offering_membership_service import offering_class_ids


CLASSROOM_TIMEZONE = timezone(timedelta(hours=8))
_CHUNK_SIZE = 250  # Three UNION branches remain below SQLite's historical 999 bind limit.
_NON_OPERATION_ACTIONS = {
    "page_view", "page_enter", "page_leave", "presence_heartbeat", "page_presence",
    "heartbeat", "visibility_change", "focus_change", "page_visibility", "page_focus", "blur", "focus",
}
COLLECTION_STATUSES = ("no_observation", "observed_zero", "measured", "error", "gap", "pending")
_ERROR_REASONS = {
    "coverage_corrupt", "observation_metadata_corrupt", "presence_payload_invalid",
    "presence_clock_out_of_range", "presence_session_missing", "presence_interval_invalid",
    "presence_coverage_invalid", "presence_state_missing", "presence_coverage_capacity",
}


def _local(value: datetime | str) -> datetime:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("统计时间必须是有效的 ISO 时间") from exc
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=CLASSROOM_TIMEZONE)
    return stamp.astimezone(CLASSROOM_TIMEZONE)


def _local_text(stamp: datetime, *, separator: str = "T") -> str:
    return stamp.replace(tzinfo=None).isoformat(sep=separator)


def _window_clause(column: str, start: datetime, end: datetime) -> tuple[str, list[str]]:
    # Both ISO separators occur in legacy TEXT timestamps. Keep the raw column
    # in comparisons so the existing actor/time indexes remain usable.
    return (
        f"(({column} >= ? AND {column} < ? AND SUBSTR({column}, 11, 1) = 'T') "
        f"OR ({column} >= ? AND {column} < ? AND SUBSTR({column}, 11, 1) = ' '))",
        [_local_text(start), _local_text(end), _local_text(start, separator=" "), _local_text(end, separator=" ")],
    )


def _chunks(values: list[int]) -> Iterator[list[int]]:
    for offset in range(0, len(values), _CHUNK_SIZE):
        yield values[offset:offset + _CHUNK_SIZE]


def _in(values: list[int]) -> str:
    return ",".join("?" for _ in values)


def _touch_times(target: dict, first, last) -> None:
    for key, candidate, pick in (("first_at", first, min), ("last_at", last, max)):
        if candidate:
            normalized = _local(candidate).isoformat()
            target[key] = pick(target[key], normalized) if target[key] else normalized


def _union_ms(entries: list, start: int, end: int) -> int:
    # Persisted ranges have already been validated as ordered and disjoint by
    # the same validator used for crediting. Clip half-open ranges precisely.
    return sum(max(0, min(right, end) - max(left, start)) for left, right in entries)


def _diagnostic_window(row, start: datetime, end: datetime, reasons: set[str]) -> tuple[bool, str]:
    """Attribute bounded client claims, falling back to the server receipt clock.

    These untrusted claims only mark uncertainty. They never create access,
    presence, measured zero, or learning credit, even when their clocks fit.
    """
    received = _local(row["received_at"])
    received_here = start <= received < end
    try:
        data = json.loads(row["diagnostics_json"] or "{}")
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        reasons.add("observation_metadata_corrupt")
        return received_here, "received"
    received_ms = int(received.timestamp() * 1000)
    observed = data.get("observed_ms")
    if "client_clock_out_of_range" in reasons or not (
        type(observed) is int and received_ms - MAX_SAMPLE_AGE_MS <= observed <= received_ms + MAX_FUTURE_SKEW_MS
    ):
        if observed is not None:
            reasons.add("client_clock_out_of_range")
        return received_here, "received"
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    interval_start, interval_end = data.get("interval_start_ms"), data.get("interval_end_ms")
    interval_matches = (
        type(interval_start) is int and type(interval_end) is int
        and received_ms - MAX_SAMPLE_AGE_MS <= interval_start < interval_end <= received_ms + MAX_FUTURE_SKEW_MS
        and interval_start < end_ms and interval_end > start_ms
    )
    return start_ms <= observed < end_ms or interval_matches, "client_claim"


def _roster(conn, offering_id: int) -> list[dict]:
    offering = conn.execute("SELECT class_id FROM class_offerings WHERE id = ?", (offering_id,)).fetchone()
    if not offering:
        raise ValueError("课堂不存在")
    class_ids = list(dict.fromkeys([
        int(offering["class_id"]), *offering_class_ids(conn, offering_id, ensure_schema=False),
    ]))
    return [dict(row) for row in conn.execute(
        f"""SELECT s.id, s.name, s.student_id_number, c.name AS class_name,
                   CASE WHEN r.status = 'confirmed' THEN 1 ELSE 0 END AS confirmed_retake
            FROM students s JOIN classes c ON c.id = s.class_id
            LEFT JOIN classroom_retake_students r
              ON r.class_offering_id = ? AND r.student_id = s.id
            WHERE COALESCE(s.enrollment_status, 'active') = 'active'
              AND (s.class_id IN ({_in(class_ids)}) OR s.id IN (
                  SELECT student_id FROM classroom_retake_students
                  WHERE class_offering_id = ? AND status = 'confirmed'))
            ORDER BY c.name, s.student_id_number, s.id""",
        (offering_id, *class_ids, offering_id),
    ).fetchall()]


def _login_evidence(conn, students: dict[int, dict], start: datetime, end: datetime, include_ips: bool) -> None:
    clause, times = _window_clause("logged_at", start, end)
    for ids in _chunks(list(students)):
        rows = conn.execute(
            f"""SELECT student_id, COUNT(*) AS successful_count,
                       MIN(REPLACE(logged_at, ' ', 'T')) AS first_at,
                       MAX(REPLACE(logged_at, ' ', 'T')) AS last_at,
                       COUNT(DISTINCT NULLIF(TRIM(ip_address), '')) AS distinct_ip_count
                FROM student_login_audit_logs
                WHERE student_id IN ({_in(ids)}) AND {clause}
                GROUP BY student_id""", (*ids, *times),
        ).fetchall()
        for row in rows:
            login = students[int(row["student_id"])]["login"]
            login["successful_count"] = int(row["successful_count"])
            login["distinct_ip_count"] = int(row["distinct_ip_count"])
            _touch_times(login, row["first_at"], row["last_at"])
        if include_ips:
            rows = conn.execute(
                f"""SELECT DISTINCT student_id, ip_address FROM student_login_audit_logs
                    WHERE student_id IN ({_in(ids)}) AND {clause}
                      AND NULLIF(TRIM(ip_address), '') IS NOT NULL
                    ORDER BY student_id, ip_address""", (*ids, *times),
            ).fetchall()
            for row in rows:
                students[int(row["student_id"])]["login"]["ip_addresses"].append(row["ip_address"])


def _classroom_evidence(conn, offering_id: int, students: dict[int, dict], start: datetime, end: datetime) -> None:
    receipt_clause, receipt_times = _window_clause("received_at", start, end)
    event_clause, event_times = _window_clause("created_at", start, end)
    for ids in _chunks(list(students)):
        receipts = conn.execute(
            f"""SELECT user_pk, state, COUNT(*) AS batch_count,
                       MIN(REPLACE(received_at, ' ', 'T')) AS first_at,
                       MAX(REPLACE(received_at, ' ', 'T')) AS last_at
                FROM behavior_write_inbox WHERE user_role = 'student'
                  AND user_pk IN ({_in(ids)}) AND class_offering_id = ?
                  AND state IN ('pending', 'applied', 'quarantined', 'discarded')
                  AND {receipt_clause} GROUP BY user_pk, state""",
            (*ids, offering_id, *receipt_times),
        ).fetchall()
        for row in receipts:
            item = students[int(row["user_pk"])]
            access = item["classroom_access"]
            count = int(row["batch_count"])
            access["authenticated_batch_count"] += count
            item["collection"][f"{row['state']}_batch_count"] = count
            _touch_times(access, row["first_at"], row["last_at"])
        events = conn.execute(
            f"""SELECT user_pk, action_type, COUNT(*) AS record_count,
                       MIN(REPLACE(created_at, ' ', 'T')) AS first_at,
                       MAX(REPLACE(created_at, ' ', 'T')) AS last_at
                FROM classroom_behavior_events WHERE class_offering_id = ?
                  AND user_role = 'student' AND user_pk IN ({_in(ids)})
                  AND {event_clause} GROUP BY user_pk, action_type""",
            (offering_id, *ids, *event_times),
        ).fetchall()
        for row in events:
            access = students[int(row["user_pk"])]["classroom_access"]
            action, count = str(row["action_type"]), int(row["record_count"])
            access["action_counts"][action] = count
            access["operation_record_count"] += count if action not in _NON_OPERATION_ACTIONS else 0
            access["page_view_count"] += count if action in {"page_view", "page_enter"} else 0
            _touch_times(access, row["first_at"], row["last_at"])


def _presence_ranges(conn, offering_id: int, students: dict[int, dict], start_ms: int, end_ms: int) -> None:
    for ids in _chunks(list(students)):
        rows = conn.execute(
            f"""SELECT user_pk, presence_coverage_json FROM classroom_behavior_states
                WHERE class_offering_id = ? AND user_role = 'student' AND user_pk IN ({_in(ids)})""",
            (offering_id, *ids),
        ).fetchall()
        for row in rows:
            item = students[int(row["user_pk"])]
            coverage = _load_coverage(row["presence_coverage_json"])
            if coverage is None:
                item["learning"]["coverage_status"] = "invalid"
                item["collection"]["reason_codes"].add("coverage_corrupt")
                continue
            reference = coverage["reference"]
            learning = item["learning"]
            learning["coverage_status"] = "valid"
            learning["recorded_milliseconds"] = _union_ms(coverage["ranges"].get("learning", []), start_ms, end_ms)
            learning["recorded_seconds"] = learning["recorded_milliseconds"] // 1000
            if reference:
                retained_from = reference - COVERAGE_WINDOW_MS
                learning["coverage_retained_from"] = datetime.fromtimestamp(
                    retained_from / 1000, CLASSROOM_TIMEZONE,
                ).isoformat()
                if start_ms < retained_from:
                    item["collection"]["reason_codes"].add("coverage_retention_gap")


def _collection_observations(conn, offering_id: int, students: dict[int, dict], start: datetime, end: datetime) -> None:
    # Received-time lookup also catches diagnostics without an observation
    # interval; observed-time lookup includes delayed replay received later.
    received_clause, received_times = _window_clause("received_at", start, end)
    diagnostic_clause, diagnostic_times = _window_clause(
        "received_at", start - timedelta(milliseconds=MAX_FUTURE_SKEW_MS), end + timedelta(milliseconds=MAX_SAMPLE_AGE_MS),
    )
    overlap = "(observed_end > ? AND observed_end <= ? AND observed_start < ?)"
    overlap_times = [_local_text(start), _local_text(end + timedelta(milliseconds=COVERAGE_WINDOW_MS)), _local_text(end)]
    for ids in _chunks(list(students)):
        projection = """observation_key, user_pk, source, received_at, observed_start, observed_end,
                        valid_sample_count, rejected_sample_count, reason_codes_json, client_diagnostic_count, diagnostics_json"""
        actor_filter = f"class_offering_id = ? AND user_role = 'student' AND user_pk IN ({_in(ids)})"
        rows = conn.execute(
            f"""SELECT {projection} FROM behavior_collection_observations
                WHERE {actor_filter} AND {received_clause}
                UNION
                SELECT {projection} FROM behavior_collection_observations
                WHERE {actor_filter} AND {overlap}
                UNION
                SELECT {projection} FROM behavior_collection_observations
                WHERE {actor_filter} AND source = 'client_diagnostic' AND {diagnostic_clause}""",
            (offering_id, *ids, *received_times, offering_id, *ids, *overlap_times,
             offering_id, *ids, *diagnostic_times),
        ).fetchall()
        for row in rows:
            item = students[int(row["user_pk"])]
            collection = item["collection"]
            observed_start = _local(row["observed_start"]) if row["observed_start"] else None
            observed_end = _local(row["observed_end"]) if row["observed_end"] else None
            observed_here = bool(observed_start and observed_end and observed_start < end and observed_end > start)
            received_here = start <= _local(row["received_at"]) < end
            try:
                codes = json.loads(row["reason_codes_json"] or "[]")
            except (ValueError, TypeError):
                codes = ["observation_metadata_corrupt"]
            if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
                codes = ["observation_metadata_corrupt"]
            codes = set(codes)
            relevant = received_here or observed_here
            if row["source"] == "client_diagnostic":
                relevant, basis = _diagnostic_window(row, start, end, codes)
                if relevant:
                    collection[f"diagnostic_{basis}_time_count"] += int(row["client_diagnostic_count"])
            # A delayed old sample must not create a spurious zero in today's
            # received-time window. Counts explicitly describe whole batches.
            if row["source"] == "browser" and observed_here:
                collection["valid_sample_count_in_intersecting_batches"] += int(row["valid_sample_count"])
                if start <= observed_start and observed_end <= end:
                    collection["contained_valid_sample_count"] += int(row["valid_sample_count"])
                if int(row["valid_sample_count"]):
                    _touch_times(collection, max(start, observed_start), min(end, observed_end))
            if relevant:
                collection["rejected_sample_count_in_intersecting_batches"] += int(row["rejected_sample_count"])
                collection["diagnostic_count"] += int(row["client_diagnostic_count"])
                collection["reason_codes"].update(codes)


def _finish_student(item: dict) -> None:
    collection, learning = item["collection"], item["learning"]
    reasons = collection["reason_codes"]
    observed = collection["contained_valid_sample_count"] > 0
    intersects = collection["valid_sample_count_in_intersecting_batches"] > 0
    if intersects and learning["coverage_status"] == "missing":
        reasons.add("presence_state_missing")
    if intersects and not observed and not learning["recorded_milliseconds"]:
        reasons.add("observation_window_partial")
    learning["measured_seconds"] = (
        learning["recorded_seconds"]
        if learning["coverage_status"] == "valid" and (observed or learning["recorded_milliseconds"])
        else None
    )
    if (reasons & _ERROR_REASONS
            or collection["rejected_sample_count_in_intersecting_batches"]
            or collection["quarantined_batch_count"] or collection["discarded_batch_count"]):
        status = "error"
    elif collection["pending_batch_count"]:
        status = "pending"
    elif reasons or collection["diagnostic_count"]:
        status = "gap"
    elif learning["recorded_milliseconds"]:
        status = "measured"
    elif observed:
        status = "observed_zero"
    else:
        status = "no_observation"
        reasons.add("no_valid_browser_observation")
    collection["status"] = status
    collection["reason_codes"] = sorted(reasons)
    collection["is_observed_zero"] = status == "observed_zero"


def build_classroom_activity_evidence(
    conn, class_offering_id: int, window_start: datetime | str, window_end: datetime | str,
    *, include_login_ips: bool = False,
) -> dict[str, Any]:
    """Batch read one authorized classroom and one explicit <=24h local window.

    No schema initialization, session cleanup, commits, learning snapshots or
    profile generation occur here. Recorded time is a measured lower bound;
    unknown and failed collections are never counted as observed zero.
    """
    start, end = _local(window_start), _local(window_end)
    if end <= start or end - start > timedelta(hours=24):
        raise ValueError("统计时间窗须大于0且不超过24小时")
    offering_id = int(class_offering_id)
    roster = _roster(conn, offering_id)
    students: dict[int, dict] = {}
    for row in roster:
        login = {"successful_count": 0, "first_at": None, "last_at": None, "distinct_ip_count": 0}
        if include_login_ips:
            login["ip_addresses"] = []
        students[int(row["id"])] = {
            "id": int(row["id"]), "name": row["name"], "student_id_number": row["student_id_number"],
            "class_name": row["class_name"], "confirmed_retake": bool(row["confirmed_retake"]),
            "login": login,
            "classroom_access": {"authenticated_batch_count": 0, "first_at": None, "last_at": None,
                                 "page_view_count": 0, "operation_record_count": 0, "action_counts": {}},
            "learning": {"recorded_seconds": 0, "recorded_milliseconds": 0, "measured_seconds": None,
                         "coverage_retained_from": None, "coverage_status": "missing"},
            "collection": {"status": "no_observation", "reason_codes": set(),
                           "pending_batch_count": 0, "applied_batch_count": 0, "quarantined_batch_count": 0,
                           "discarded_batch_count": 0, "valid_sample_count_in_intersecting_batches": 0,
                           "contained_valid_sample_count": 0,
                           "rejected_sample_count_in_intersecting_batches": 0,
                           "diagnostic_client_claim_time_count": 0, "diagnostic_received_time_count": 0,
                           "diagnostic_count": 0, "first_at": None, "last_at": None},
        }
    _login_evidence(conn, students, start, end, include_login_ips)
    _classroom_evidence(conn, offering_id, students, start, end)
    _presence_ranges(conn, offering_id, students, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    _collection_observations(conn, offering_id, students, start, end)
    for item in students.values():
        _finish_student(item)
    items = list(students.values())
    counts = {status: sum(item["collection"]["status"] == status for item in items) for status in COLLECTION_STATUSES}
    recorded = [item["learning"]["recorded_seconds"] for item in items]
    positives = [seconds for seconds in recorded if seconds > 0]
    return {
        "class_offering_id": offering_id,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Shanghai"},
        "semantics": {
            "login": "successful_platform_login_audit_not_classroom_entry",
            "classroom_access": "authenticated_classroom_receipts_and_operation_records",
            "learning": "union_of_retained_validated_learning_intervals",
            "recorded_seconds": "recorded_lower_bound_unknown_is_not_observed_zero",
            "observation_counts": "whole_batches_intersecting_the_window",
            "diagnostic_time": "bounded_untrusted_client_claim_or_server_received_time_fallback_never_activity_or_credit",
            "observed_zero": "valid_browser_batch_wholly_contained_in_window_without_recorded_learning_or_collection_issue",
            "roster": "current_active_combined_classes_and_confirmed_retake_students",
            "attendance_decision": False,
        },
        "summary": {
            "student_count": len(items), "successful_login_students": sum(item["login"]["successful_count"] > 0 for item in items),
            "classroom_access_students": sum(bool(item["classroom_access"]["first_at"]) for item in items),
            "collection_status_counts": counts, "observed_zero_students": counts["observed_zero"],
            "unknown_students": sum(item["learning"]["measured_seconds"] is None for item in items),
            "incomplete_collection_students": sum(item["collection"]["status"] in {"no_observation", "error", "gap", "pending"} for item in items),
            "recorded_seconds_total": sum(recorded),
            "longest_recorded_seconds": max(recorded) if recorded else None,
            "shortest_recorded_seconds": min(recorded) if recorded else None,
            "average_recorded_seconds_all": sum(recorded) / len(items) if items else None,
            "lower_bound_average_seconds_all": sum(recorded) / len(items) if items else None,
            "average_recorded_seconds_positive": sum(positives) / len(positives) if positives else None,
        },
        "students": items,
    }
