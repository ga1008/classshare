"""Persist bounded collection evidence without creating activity or learning time.

The caller owns the transaction. Business observations share the inbox apply
transaction, and an idempotent retry never replaces the first recorded fact.
Client diagnostics use their own batch ID and may reference a business batch
in diagnostics.original_batch_id; they are never evidence of classroom access.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import ipaddress
import json
import math
import re


COLLECTION_SOURCES = frozenset({"server", "browser", "client_diagnostic"})
COLLECTION_ROLES = frozenset({"student", "teacher"})
MAX_DIAGNOSTICS_BYTES = 8192
MAX_REASON_CODES = 32
MAX_SAMPLE_COUNT = 10000
MAX_OBSERVATION_AGE = timedelta(days=7, seconds=90)
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_BATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PRIVATE_KEYS = frozenset({
    "ip", "ip_address", "client_ip", "remote_ip", "x_forwarded_for", "ua", "user_agent",
    "password", "hashed_password", "token", "access_token", "refresh_token", "authorization",
    "cookie", "cookies", "session", "session_id", "session_key", "session_user_key",
})


def _stamp(value, name: str) -> datetime:
    if isinstance(value, str):
        if len(value) > 64 or "T" not in value:
            raise ValueError(f"{name} must be an ISO datetime")
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO datetime") from exc
    if not isinstance(value, datetime) or not 2000 <= value.year <= 2100:
        raise ValueError(f"{name} must be an ISO datetime")
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


def _count(value, name: str, *, maximum=MAX_SAMPLE_COUNT) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be a bounded non-negative integer")
    return value


def _reason_codes(values) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > MAX_REASON_CODES:
        raise ValueError("reason_codes must be a bounded sequence")
    if any(not isinstance(value, str) or not _CODE.fullmatch(value) for value in values):
        raise ValueError("reason_codes must contain machine codes")
    return sorted(set(values))


def _diagnostics(value) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise ValueError("diagnostics must be an object")

    def validate(item, depth=0):
        if depth > 4:
            raise ValueError("diagnostics nesting is too deep")
        if isinstance(item, dict):
            if len(item) > 32:
                raise ValueError("diagnostics has too many fields")
            for key, child in item.items():
                if (not isinstance(key, str) or not 1 <= len(key) <= 64
                        or key.lower().replace("-", "_") in _PRIVATE_KEYS):
                    raise ValueError("diagnostics contains an invalid or private field")
                validate(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 32:
                raise ValueError("diagnostics has too many items")
            for child in item:
                validate(child, depth + 1)
        elif isinstance(item, str):
            if len(item) > 512:
                raise ValueError("diagnostics text is too long")
        elif item is not None and type(item) not in (bool, int, float):
            raise ValueError("diagnostics must contain JSON values")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("diagnostics numbers must be finite")
    validate(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        encoded = raw.encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("diagnostics must be valid JSON and Unicode") from exc
    if len(encoded) > MAX_DIAGNOSTICS_BYTES:
        raise ValueError("diagnostics is too large")
    return raw


def _observation_values(*, class_offering_id, user_pk, user_role, batch_id,
                        received_at, source, valid_sample_count, rejected_sample_count,
                        learning_delta_seconds, observed_start=None, observed_end=None,
                        reason_codes=None, diagnostics=None, ip_address=None):
    if (type(class_offering_id) is not int or not 0 < class_offering_id <= 2147483647
            or type(user_pk) is not int or not 0 < user_pk <= 2147483647):
        raise ValueError("A positive classroom and actor are required")
    if (not isinstance(user_role, str) or user_role not in COLLECTION_ROLES
            or not isinstance(source, str) or source not in COLLECTION_SOURCES):
        raise ValueError("Invalid collection actor or source")
    if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
        raise ValueError("Invalid collection batch ID")
    valid = _count(valid_sample_count, "valid_sample_count")
    rejected = _count(rejected_sample_count, "rejected_sample_count")
    learning = _count(learning_delta_seconds, "learning_delta_seconds", maximum=MAX_SAMPLE_COUNT * 90)
    if valid + rejected > MAX_SAMPLE_COUNT or learning > valid * 90:
        raise ValueError("Collection counts are inconsistent")
    received = _stamp(received_at, "received_at")
    start = end = None
    if (observed_start is None) != (observed_end is None):
        raise ValueError("Observed range requires both endpoints")
    if observed_start is not None:
        start, end = _stamp(observed_start, "observed_start"), _stamp(observed_end, "observed_end")
        if not received - MAX_OBSERVATION_AGE <= start <= end <= received + timedelta(seconds=5):
            raise ValueError("Observed range is outside the accepted collection window")
    if source == "client_diagnostic" and (valid or rejected or learning or start is not None):
        raise ValueError("Client diagnostics cannot claim measured presence")
    codes_json = json.dumps(_reason_codes(reason_codes), separators=(",", ":"))
    diagnostics_json = _diagnostics(diagnostics)
    if ip_address is not None:
        if not isinstance(ip_address, str) or len(ip_address) > 45:
            raise ValueError("IP address must be one server-resolved address")
        try:
            ip_address = str(ipaddress.ip_address(ip_address))
        except ValueError as exc:
            raise ValueError("IP address must be one server-resolved address") from exc
    key = hashlib.sha256(json.dumps([user_role, user_pk, class_offering_id, batch_id],
                                   separators=(",", ":")).encode("utf-8")).hexdigest()
    return (key, batch_id, class_offering_id, user_pk, user_role, source, received.isoformat(),
            start.isoformat() if start else None, end.isoformat() if end else None, valid, rejected, learning,
            codes_json, int(source == "client_diagnostic"), diagnostics_json, ip_address)


_INSERT_COLUMNS = """INSERT INTO behavior_collection_observations (
        observation_key, batch_id, class_offering_id, user_pk, user_role, source, received_at,
        observed_start, observed_end, valid_sample_count, rejected_sample_count, learning_delta_seconds,
        reason_codes_json, client_diagnostic_count, diagnostics_json, ip_address
        ) VALUES """
_ON_CONFLICT = " ON CONFLICT(user_role, user_pk, class_offering_id, batch_id) DO NOTHING"


def record_collection_observation(conn, **observation) -> bool:
    """Insert one immutable fact in the caller's transaction, without crediting activity."""
    cursor = conn.execute(_INSERT_COLUMNS + "(" + ",".join("?" for _ in range(16)) + ")" + _ON_CONFLICT,
                          _observation_values(**observation))
    return cursor.rowcount == 1


def record_collection_observations(conn, observations: list[dict]) -> int:
    """Validate a bounded batch before any write, then insert in one round trip.

    Duplicate IDs retain their first fact. Commit and access/rate checks remain
    the caller's responsibility. IP is a server-resolved field, never wire JSON.
    """
    if not isinstance(observations, list) or not 1 <= len(observations) <= 20:
        raise ValueError("Collection observation batch must contain 1 to 20 facts")
    values = [_observation_values(**item) for item in observations]
    row_marks = "(" + ",".join("?" for _ in range(16)) + ")"
    cursor = conn.execute(_INSERT_COLUMNS + ",".join(row_marks for _ in values) + _ON_CONFLICT,
                          tuple(value for row in values for value in row))
    return int(cursor.rowcount)
