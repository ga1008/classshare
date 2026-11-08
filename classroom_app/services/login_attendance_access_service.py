"""Small durable authenticated observations, separate from measured study time."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import logging
import os
import re
import threading
import time

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from .trusted_network_service import client_network

_lock = threading.Lock()
_recent = OrderedDict()
_inflight = set()
_failures = 0
_last_failure_ms = None
_last_success_ms = None
_logger = logging.getLogger(__name__)


def enabled():
    return os.getenv("LOGIN_ATTENDANCE_ENABLED", "1").strip().lower() in {"1", "true", "yes"}


def record_access(conn, *, student_id, ip_address, source, observed_at_ms=None,
                  network_source="verified_login", auth_channel="web", session_hash="", class_offering_hint=None):
    if not enabled() or not ip_address:
        return
    now = int(time.time() * 1000) if observed_at_ms is None else int(observed_at_ms)
    # This is platform presence, not a per-page counter. Arbitrarily many
    # paths/tabs must not multiply writes or manufacture additional votes.
    identity = f"{student_id}|{ip_address}|{source}|{session_hash}|{now // 60000}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    conn.execute(
        "INSERT INTO login_attendance_access(event_key,student_id,observed_at_ms,ip_address,network_source,"
        "auth_channel,session_hash,source,class_offering_hint,received_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(event_key) DO NOTHING",
        (key, int(student_id), now, ip_address, network_source, auth_channel, session_hash, source, class_offering_hint, now),
    )


def observe_authenticated_request(request, user):
    global _failures, _last_failure_ms, _last_success_ms
    if not enabled() or not user or user.get("role") != "student":
        return
    if getattr(request.state, "attendance_observed", False):
        return
    request.state.attendance_observed = True
    network = client_network(request)
    if not network["ip"]:
        return
    sid = int(user["id"])
    raw_session = str(user.get("session_id") or "")
    session_hash = hashlib.sha256(raw_session.encode()).hexdigest()[:24] if raw_session else ""
    match = re.match(r"^/(?:api/classrooms|classroom)/(\d+)(?:/|$)", request.url.path)
    offering_hint = int(match[1]) if match else None
    now = int(time.time() * 1000)
    key = (sid, network["ip"], session_hash, now // 60_000)
    with _lock:
        if key in _recent or key in _inflight:
            return
        _inflight.add(key)
    try:
        if get_configured_db_engine()=="postgres":
            from ..db.postgres import connect_postgres
            connection=connect_postgres(checkout_timeout=0.25)
        else:
            connection=get_db_connection()
        with connection as conn:
            if get_configured_db_engine()=="postgres":
                from .behavior_inbox_service import _bounded_timeouts
                _bounded_timeouts(conn,lock_ms=250,statement_ms=1000)
            else:
                conn.execute("PRAGMA busy_timeout=250")
            record_access(conn, student_id=sid, ip_address=network["ip"], source="authenticated_request",
                          network_source=network["source"], auth_channel=str(user.get("auth_channel") or "web"),
                          session_hash=session_hash, class_offering_hint=offering_hint, observed_at_ms=now)
            conn.commit()
        with _lock:
            _recent[key] = now
            while len(_recent) > 16_384:
                _recent.popitem(last=False)
            _last_success_ms = now
    except Exception:
        # A collection failure must not break an otherwise valid login. It
        # remains unknown evidence; never synthesize a successful observation.
        with _lock:
            _failures += 1
            should_log = _last_failure_ms is None or now - _last_failure_ms > 60_000
            _last_failure_ms = now
        request.state.attendance_collection_unavailable = True
        if should_log:
            _logger.warning("Attendance observation unavailable; authentication remains valid")
    finally:
        with _lock:
            _inflight.discard(key)


def collection_health():
    with _lock:
        return {"failed_observations": _failures, "last_failure_ms": _last_failure_ms,
                "last_success_ms": _last_success_ms}


def record_login_observation(conn, *, student_id, client_ip, login_method):
    """Keep the login transaction usable even if the evidence sink fails."""
    global _failures, _last_failure_ms
    if not enabled():
        return
    conn.execute("SAVEPOINT attendance_login_observation")
    try:
        original = None
        if get_configured_db_engine()=="postgres":
            from .behavior_inbox_service import _bounded_timeouts, _restore_timeouts
            original = _bounded_timeouts(conn,lock_ms=250,statement_ms=1000)
        record_access(conn, student_id=student_id, ip_address=client_ip, source="login",
                      auth_channel=str(login_method or "web")[:40])
        if original is not None:
            _restore_timeouts(conn,original)
        conn.execute("RELEASE SAVEPOINT attendance_login_observation")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT attendance_login_observation")
        conn.execute("RELEASE SAVEPOINT attendance_login_observation")
        with _lock:
            _failures += 1
            _last_failure_ms = int(time.time() * 1000)
