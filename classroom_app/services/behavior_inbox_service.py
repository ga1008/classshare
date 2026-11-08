"""Commit intake before acknowledging behavior, then apply each receipt atomically."""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from ..config import (
    BEHAVIOR_WRITE_QUEUE_SIZE, BEHAVIOR_WRITE_SYNC_TIMEOUT_MS, BEHAVIOR_WRITE_BATCH_SIZE,
    BEHAVIOR_WRITE_FLUSH_INTERVAL_MS, BEHAVIOR_WRITE_ENQUEUE_TIMEOUT_MS,
    BEHAVIOR_WRITE_APPLY_BUDGET_MS, BEHAVIOR_WRITE_LOCK_TIMEOUT_MS, BEHAVIOR_WRITE_STATEMENT_TIMEOUT_MS,
)
from ..database import get_db_connection
from ..db.connection import begin_immediate_transaction, get_configured_db_engine
from ..db.errors import DatabaseBusyError, DatabaseConnectionError, is_retryable_conflict
from .behavior_event_validation import BehaviorValidationError, MAX_PAYLOAD_BYTES, validate_behavior_payload

CAPACITY_LOCK = (0x42485652, 1)
APPLY_LOCK = (0x42485652, 2)
CLEANUP_LOCK = (0x42485652, 3)
CLIENT_RETRY_DAYS = 7
RECEIPT_RETENTION_DAYS = 10
PERMANENT_DATA_FAILURE_LIMIT = 3
_UNREADABLE_RESULT_FIELDS = ("unreadable_result_json", "unreadable_result_sha256", "unreadable_result_bytes")


def new_batch_id() -> str:
    return f"b1_{int(time.time() * 1000)}_{uuid.uuid4().hex}"


class BehaviorBatchConflict(ValueError):
    """A previously used batch ID has a different immutable payload."""

    def __init__(self, message: str, error_code: str = "batch_payload_conflict"):
        super().__init__(message)
        self.error_code = error_code


class BehaviorDeletionPending(ValueError):
    """Accepted behavior must finish before its actor or classroom can be removed."""


def _actor_table(role: str) -> str:
    if role not in {"student", "teacher"}:
        raise BehaviorBatchConflict("行为记录的账号类型无效。", "actor_unavailable")
    return "students" if role == "student" else "teachers"


def prepare_behavior_deletion(conn, *, user_role: str | None = None, user_pk: int | None = None,
                              class_offering_id: int | None = None) -> None:
    """Caller owns the deletion transaction. Parent locks exclude new intake."""
    if get_configured_db_engine() == "sqlite" and not conn.in_transaction:
        begin_immediate_transaction(conn)
    if class_offering_id is not None:
        parent = "class_offerings"
        parent_id = int(class_offering_id)
        predicate, values = "class_offering_id = ?", (parent_id,)
    else:
        parent = _actor_table(str(user_role))
        parent_id = int(user_pk or 0)
        predicate, values = "user_role = ? AND user_pk = ?", (user_role, parent_id)
    if get_configured_db_engine() == "postgres":
        conn.execute(f"SELECT id FROM {parent} WHERE id = ? FOR UPDATE", (parent_id,)).fetchone()
    if conn.execute(f"SELECT 1 FROM behavior_write_inbox WHERE {predicate} AND state IN ('pending','quarantined') LIMIT 1", values).fetchone():
        raise BehaviorDeletionPending("仍有已接收的行为记录正在入账或已隔离待修复，请先处理后再删除。")
    audited = conn.execute(f"""SELECT batch_key,result_json FROM behavior_write_inbox
        WHERE {predicate} AND state IN ('applied','discarded') AND result_json LIKE ?""",
        (*values, '%"recovery_actions"%')).fetchall()
    retired = {"retired": True, "accepted_event_count": 0, "logged_event_ids": []}
    audit_receipts = []
    for row in audited:
        actions = json.loads(row["result_json"] or "{}").get("recovery_actions") or []
        # Preserve who resolved the receipt and its hash/time, while retiring
        # free-form reasons with the deleted person's other personal content.
        actions = [{**{key: action[key] for key in ("action", "operator", "at", "payload_hash") if key in action},
                    "reason": "retired"} for action in actions]
        audit_receipts.append((row["batch_key"], json.dumps({**retired, "recovery_actions": actions})))
    # Keep immutable scope/hash tombstones for replay safety, retire personal
    # display/session details alongside the explicit actor/classroom deletion.
    conn.execute(f"""UPDATE behavior_write_inbox SET display_name = '', session_started_at = NULL,
        events_json = '[]', result_json = ? WHERE {predicate} AND state IN ('applied','discarded')""",
        (json.dumps(retired), *values))
    for offset in range(0, len(audit_receipts), 200):
        batch = audit_receipts[offset:offset+200]
        cases = " ".join("WHEN ? THEN ?" for _ in batch)
        marks = ",".join("?" for _ in batch)
        conn.execute(f"UPDATE behavior_write_inbox SET result_json=CASE batch_key {cases} END WHERE batch_key IN ({marks})",
                     tuple(value for pair in batch for value in pair) + tuple(key for key, _value in batch))
    # Collection metadata follows the same actor/classroom lifecycle as its
    # retired business records; it must not leave personal IP evidence behind.
    conn.execute(f"DELETE FROM behavior_collection_observations WHERE {predicate}", values)


@dataclass(slots=True)
class BehaviorWriteRequest:
    class_offering_id: int
    user_pk: int
    user_role: str
    display_name: str
    page_key: str | None
    events: list[dict[str, Any]]
    session_started_at: str | None = None
    batch_id: str = field(default_factory=new_batch_id)


def _error_code(exc: BaseException) -> str:
    # Never store event contents, DSNs or a driver's parameter rendering.
    return f"{type(exc).__name__}:{getattr(exc, 'sqlstate', '') or ''}"[:120]


def _timeout_ms(value: str) -> int:
    """Parse PostgreSQL's normalized timeout setting; zero means disabled."""
    match = re.fullmatch(r"(\d+)\s*(ms|s|min|h|d)?", str(value).strip())
    if match is None:
        raise ValueError("Unrecognized database timeout setting")
    return int(match[1]) * {None: 1, "ms": 1, "s": 1000, "min": 60000, "h": 3600000, "d": 86400000}[match[2]]


def _bounded_timeouts(conn, *, lock_ms: int, statement_ms: int) -> tuple[str, str]:
    """Use transaction-local bounds without relaxing a stricter caller setting."""
    row = conn.execute("SELECT current_setting('lock_timeout') AS lock_timeout, "
                       "current_setting('statement_timeout') AS statement_timeout").fetchone()
    original = str(row["lock_timeout"]), str(row["statement_timeout"])
    requested = lock_ms, statement_ms
    bounded = [f"{min(current, desired) if current else desired}ms"
               for current, desired in zip(map(_timeout_ms, original), requested)]
    conn.execute("SELECT set_config('lock_timeout', ?, true), set_config('statement_timeout', ?, true)", tuple(bounded))
    return original


def _restore_timeouts(conn, original: tuple[str, str]) -> None:
    conn.execute("SELECT set_config('lock_timeout', ?, true), set_config('statement_timeout', ?, true)", original)


def _event_count(row) -> int:
    try:
        events = json.loads(row["events_json"])
        return len(events) if isinstance(events, list) else 0
    except (ValueError, TypeError):
        return 0


def _read_receipt_metadata(row) -> dict[str, Any]:
    try:
        metadata = json.loads(row["result_json"] or "{}")
        if not isinstance(metadata, dict):
            raise ValueError("Recovery metadata must be an object")
        recovery = metadata.get("recovery_actions")
        if recovery is not None and (not isinstance(recovery, list) or any(not isinstance(item, dict) for item in recovery)):
            raise ValueError("Recovery history must contain action objects")
        streak = metadata.get("permanent_error_streak", 0)
        if type(streak) is not int or streak < 0:
            raise ValueError("Recovery error count is invalid")
    except (ValueError, TypeError, RecursionError) as exc:
        raise BehaviorValidationError("已接收回执的恢复信息格式无效，原内容已保留待审核。") from exc
    return metadata


def _preserve_unreadable_metadata(row) -> dict[str, Any]:
    # This is the existing TEXT value, not an event copy or a truncated
    # substitute. Encode/hash once and quarantine once, so even an oversized
    # legacy result cannot make every drain repeatedly parse/wrap the group.
    raw = row["result_json"]
    encoded = raw.encode("utf-8")
    return {"unreadable_result_json": raw, "unreadable_result_sha256": hashlib.sha256(encoded).hexdigest(),
            "unreadable_result_bytes": len(encoded)}


def _receipt(row: Any) -> dict[str, Any]:
    if row["state"] in {"quarantined", "discarded"}:
        return {"batch_id": row["batch_id"], "write_mode": row["state"], "degraded": True,
                "accepted_event_count": 0, "logged_event_ids": [], "queued_event_count": 0,
                "durable_event_count": _event_count(row),
                "error_code": "operator_discarded" if row["state"] == "discarded" else row["last_error"],
                "message": "记录已隔离并保留，等待管理员修复。" if row["state"] == "quarantined" else "管理员已明确放弃入账；本批次未计入行为。"}
    if row["state"] == "applied":
        snapshot = json.loads(row["result_json"] or "{}")
        snapshot.pop("recovery_actions", None)
        for field in _UNREADABLE_RESULT_FIELDS:
            snapshot.pop(field, None)  # Administrator evidence is not a browser receipt field.
        return {**snapshot, "batch_id": row["batch_id"], "write_mode": "synced", "degraded": False}
    return {
        "batch_id": row["batch_id"], "accepted_event_count": 0, "logged_event_ids": [],
        "queued_event_count": len(json.loads(row["events_json"])),
        "durable_event_count": len(json.loads(row["events_json"])),
        "seconds_until_next_profile": None, "write_mode": "durable_queued", "degraded": False,
    }


class BehaviorWritePipeline:
    def __init__(self, apply_batch: Callable[..., dict[str, Any]]) -> None:
        self._apply_batch = apply_batch
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stats: dict[str, Any] = {
            "queue_depth": 0, "queue_capacity": int(BEHAVIOR_WRITE_QUEUE_SIZE),
            "queue_depth_known": False, "retrying_count": 0, "committed_batch_count": 0,
            "committed_event_count": 0, "retry_count": 0, "unknown_commit_count": 0,
            "last_heartbeat_at": None, "last_success_at": None, "oldest_queued_at": None,
            "quarantined_count": 0, "oldest_quarantined_at": None,
            "snapshot_at": None, "last_error": "", "worker_restarts": 0,
            "committed_counts_scope": "worker_confirmed_since_start",
            "drain_transactions": 0, "last_drain_batch_count": 0, "max_drain_batch_count": 0,
            "last_drain_duration_ms": 0.0,
        }

    def start(self) -> None:
        with self._lock:
            if self._stop_event.is_set():
                raise DatabaseBusyError("行为写入服务正在停止，请稍后重试。")
            if self._thread is not None and self._thread.is_alive():
                return
            if self._thread is not None:
                self._stats["worker_restarts"] += 1
            self._thread = threading.Thread(target=self._run, name="behavior-write-worker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            alive = bool(self._thread is not None and self._thread.is_alive())
            full = self._stats["queue_depth_known"] and (
                self._stats["queue_depth"] + self._stats["quarantined_count"] >= self._stats["queue_capacity"])
            return {**self._stats, "alive": alive, "accepting": alive and not self._stop_event.is_set() and not full,
                    "storage": "database"}

    def _update_stats(self, **values: Any) -> None:
        with self._lock:
            self._stats.update(values)

    def _increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._stats[name] += amount

    def submit(self, request: BehaviorWriteRequest, *, wait: bool = False, conn=None) -> dict[str, Any]:
        self.start()  # Restarts a dead thread, retains the database backlog.
        if self._stop_event.is_set():
            raise DatabaseBusyError("行为写入服务正在停止，请稍后重试。")
        try:
            canonical = json.dumps({"events": request.events, "page_key": request.page_key},
                                   ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            canonical.encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise BehaviorValidationError("行为数据必须是有效的 JSON 与 Unicode 文本。") from exc
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        batch_key = hashlib.sha256(json.dumps([request.user_role, request.user_pk,
            request.class_offering_id, request.batch_id]).encode("utf-8")).hexdigest()
        received_at = datetime.now().isoformat()
        if conn is not None:
            if wait:
                raise ValueError("Cannot wait for behavior inside its uncommitted business transaction")
            conn.execute("SAVEPOINT behavior_intake")
            try:
                original_timeouts = (_bounded_timeouts(conn, lock_ms=BEHAVIOR_WRITE_LOCK_TIMEOUT_MS,
                                     statement_ms=BEHAVIOR_WRITE_STATEMENT_TIMEOUT_MS)
                                     if get_configured_db_engine() == "postgres" else None)
                snapshot = self._persist_in_connection(conn, request, batch_key, payload_hash, received_at)
                if original_timeouts is not None:
                    _restore_timeouts(conn, original_timeouts)
                conn.execute("RELEASE SAVEPOINT behavior_intake")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT behavior_intake")
                conn.execute("RELEASE SAVEPOINT behavior_intake")
                raise
            if snapshot["write_mode"] != "synced":
                snapshot.update(write_mode="transaction_pending", durable_event_count=0)
        else:
            snapshot = self._persist_request(request, batch_key, payload_hash, received_at)
        self._wake.set()
        if not wait or snapshot["write_mode"] == "synced":
            return snapshot
        deadline = time.monotonic() + max(BEHAVIOR_WRITE_SYNC_TIMEOUT_MS / 1000.0, 0.1)
        while time.monotonic() < deadline:
            with get_db_connection() as conn:
                row = conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_key = ?", (batch_key,)).fetchone()
            if row and row["state"] == "applied":
                return _receipt(row)
            if self._stop_event.wait(0.05):
                break
        # Already durable. A caller timeout never retracts acceptance or generates a new ID.
        return snapshot

    def _persist_in_connection(self, conn, request, batch_key, payload_hash, received_at) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_key = ?", (batch_key,)).fetchone()
        if row:
            if row["payload_hash"] != payload_hash:
                raise BehaviorBatchConflict("行为批次编号已用于其他内容。")
            return _receipt(row)
        # Existing receipts are reconciled first, including failed legacy
        # payloads. New malformed data never becomes an accepted receipt.
        validate_behavior_payload(events=request.events, page_key=request.page_key,
                                  display_name=request.display_name, session_started_at=request.session_started_at)
        postgres = get_configured_db_engine() == "postgres"
        # Merge/deletion owns FOR UPDATE on the same parent before its final
        # pending-inbox check. Accepted work therefore cannot appear behind it.
        offering = conn.execute("SELECT id FROM class_offerings WHERE id = ?" +
                                (" FOR KEY SHARE" if postgres else ""), (request.class_offering_id,)).fetchone()
        if offering is None:
            raise BehaviorBatchConflict("课堂已变更，请刷新页面后查看行为记录状态。", "classroom_unavailable")
        actor = conn.execute(f"SELECT id FROM {_actor_table(request.user_role)} WHERE id = ?" +
                             (" FOR KEY SHARE" if postgres else ""), (request.user_pk,)).fetchone()
        if actor is None:
            raise BehaviorBatchConflict("账号已变更，请刷新页面后查看行为记录状态。", "actor_unavailable")
        if postgres:
            conn.execute("SELECT pg_advisory_xact_lock(?, ?)", CAPACITY_LOCK)
        # Another intake can have committed while this one waited for capacity.
        row = conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_key = ?", (batch_key,)).fetchone()
        if row:
            if row["payload_hash"] != payload_hash:
                raise BehaviorBatchConflict("行为批次编号已用于其他内容。")
            return _receipt(row)
        if request.batch_id.startswith("b1_"):
            try:
                created = datetime.fromtimestamp(int(request.batch_id.split("_", 2)[1]) / 1000.0)
            except (ValueError, OverflowError, OSError) as exc:
                raise BehaviorBatchConflict("行为批次编号无效。", "batch_timestamp_invalid") from exc
            age = datetime.now() - created
            if age > timedelta(days=CLIENT_RETRY_DAYS) or age < -timedelta(days=1):
                raise BehaviorBatchConflict("行为批次已超过重试期限，请刷新页面查看记录状态。", "batch_expired")
        count = conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox WHERE state IN ('pending','quarantined')").fetchone()["n"]
        if int(count) >= int(BEHAVIOR_WRITE_QUEUE_SIZE):
            raise DatabaseBusyError("行为记录暂时积压，请稍后使用原批次编号重试。")
        conn.execute("""INSERT INTO behavior_write_inbox (
            batch_key, batch_id, payload_hash, class_offering_id, user_pk, user_role,
            display_name, page_key, events_json, session_started_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch_key, request.batch_id, payload_hash, request.class_offering_id, request.user_pk,
             request.user_role, request.display_name, request.page_key,
             json.dumps(request.events, ensure_ascii=False, default=str),
             request.session_started_at, received_at))
        return _receipt({"state": "pending", "batch_id": request.batch_id,
                         "events_json": json.dumps(request.events, default=str)})

    def _persist_request(self, request, batch_key, payload_hash, received_at) -> dict[str, Any]:
        deadline = time.monotonic() + BEHAVIOR_WRITE_ENQUEUE_TIMEOUT_MS / 1000.0
        for attempt in range(3):
            committing = False
            try:
                with get_db_connection() as conn:
                    begin_immediate_transaction(conn)
                    if get_configured_db_engine() == "postgres":
                        _bounded_timeouts(conn, lock_ms=BEHAVIOR_WRITE_LOCK_TIMEOUT_MS,
                                          statement_ms=BEHAVIOR_WRITE_STATEMENT_TIMEOUT_MS)
                    snapshot = self._persist_in_connection(conn, request, batch_key, payload_hash, received_at)
                    committing = True
                    conn.commit()
                    return snapshot
            except (BehaviorBatchConflict, ValueError):
                raise
            except Exception as exc:
                if committing:
                    self._increment("unknown_commit_count")
                retryable = committing or isinstance(exc, (DatabaseBusyError, DatabaseConnectionError)) or is_retryable_conflict(exc)
                if not retryable or attempt == 2 or time.monotonic() >= deadline:
                    raise DatabaseBusyError("行为记录暂时无法确认，请使用原批次编号重试。") from exc
                self._stop_event.wait(min(max(0.0, deadline - time.monotonic()),
                                          0.05 * (2 ** attempt) + random.uniform(0.0, 0.05)))
        raise DatabaseBusyError("行为记录暂时无法确认。")

    def _select_next_receipt(self, conn, *, postgres: bool):
        # Preserve actor order across classrooms too. Daily streaks/rewards
        # share the actor, whereas heartbeat state is scoped to the classroom.
        # Materialize heads once: after a quiet period PostgreSQL can estimate
        # just one pending row, turning a correlated predecessor lookup into
        # repeated scans of the whole backlog. Carry state through the fence
        # so the locked row is rechecked without a second pending-only scan.
        # Rank before retry filtering: a future retry blocks only its actor.
        return conn.execute("""WITH actor_heads AS MATERIALIZED (
                SELECT batch_key, state FROM (
                    SELECT batch_key, state, ROW_NUMBER() OVER (
                        PARTITION BY user_role, user_pk ORDER BY received_at, batch_key
                    ) AS actor_rank
                    FROM behavior_write_inbox WHERE state = 'pending'
                ) ranked WHERE actor_rank = 1
            )
            SELECT q.* FROM actor_heads h JOIN behavior_write_inbox q
                ON q.batch_key = h.batch_key AND q.state = h.state
            WHERE q.retry_at IS NULL OR q.retry_at <= ?
            ORDER BY q.received_at, q.batch_key LIMIT 1""" + (" FOR UPDATE OF q SKIP LOCKED" if postgres else ""),
            (datetime.now().isoformat(),)).fetchone()

    def _apply_receipt(self, conn, row) -> tuple[bool, int, str]:
        """One receipt's savepoint owns its effects and result, never a commit."""
        conn.execute("SAVEPOINT behavior_apply")
        metadata = None
        try:
            metadata = _read_receipt_metadata(row)
            recovery = metadata.get("recovery_actions") or []
            if recovery and recovery[-1].get("action") == "retry":
                from .behavior_inbox_recovery_service import validate_recovery_order
                validate_recovery_order(conn, row)
            try:
                events = json.loads(row["events_json"])
            except (ValueError, TypeError) as exc:
                raise BehaviorValidationError("已接收记录不是有效的 JSON。") from exc
            validate_behavior_payload(events=events, page_key=row["page_key"], display_name=row["display_name"],
                                      session_started_at=row["session_started_at"])
            snapshot = self._apply_batch(conn, class_offering_id=int(row["class_offering_id"]),
                user_pk=int(row["user_pk"]), user_role=row["user_role"], display_name=row["display_name"],
                page_key=row["page_key"], events=events,
                session_started_at=row["session_started_at"], recorded_at=row["received_at"], batch_id=row["batch_id"])
            snapshot.update(queued_event_count=0, durable_event_count=len(events))
            audit = metadata.get("recovery_actions")
            if audit:
                snapshot["recovery_actions"] = audit
            snapshot.update({field: metadata[field] for field in _UNREADABLE_RESULT_FIELDS if field in metadata})
            completed_at = datetime.now().isoformat()
            conn.execute("""UPDATE behavior_write_inbox SET state = 'applied', applied_at = ?, resolved_at = ?,
                retry_at = NULL, last_error = '', result_json = ?, events_json = '[]'
                WHERE batch_key = ? AND state = 'pending'""",
                (completed_at, completed_at, json.dumps(snapshot, ensure_ascii=False, default=str), row["batch_key"]))
            conn.execute("RELEASE SAVEPOINT behavior_apply")
            return True, int(snapshot.get("accepted_event_count") or 0), ""
        except Exception as exc:
            # Leave PostgreSQL's failed-transaction state before any further
            # SQL. Other actors can continue without losing this raw payload.
            conn.execute("ROLLBACK TO SAVEPOINT behavior_apply")
            conn.execute("RELEASE SAVEPOINT behavior_apply")
            attempts = int(row["attempts"]) + 1
            retry_at = (datetime.now() + timedelta(seconds=min(300, 2 ** min(attempts, 8)) + random.random())).isoformat()
            if metadata is None:
                metadata = _preserve_unreadable_metadata(row)
            code = _error_code(exc)
            permanent = str(getattr(exc, "sqlstate", "") or "").startswith("22")
            streak = int(metadata.get("permanent_error_streak") or 0) + 1 if permanent and row["last_error"] == code else int(permanent)
            metadata["permanent_error_streak"] = streak
            quarantine = isinstance(exc, BehaviorValidationError) or (permanent and streak >= PERMANENT_DATA_FAILURE_LIMIT)
            if quarantine:
                metadata["quarantined_at"] = datetime.now().isoformat()
            conn.execute("""UPDATE behavior_write_inbox SET attempts = ?, retry_at = ?, last_error = ?,
                state = ?, result_json = ? WHERE batch_key = ? AND state = 'pending'""",
                (attempts, None if quarantine else retry_at, code, "quarantined" if quarantine else "pending",
                 json.dumps(metadata, ensure_ascii=False), row["batch_key"]))
            return False, 0, code

    def _drain_batch(self, *, max_batches: int | None = None) -> int:
        """Amortize checkout/commit over a bounded, independently isolated group.

        A lost commit acknowledgement is reconciled from each immutable
        receipt, exactly as for the single-receipt path. The budget is checked
        between receipts; database waits also have their own local bounds.
        """
        limit = max(1, min(16, int(max_batches if max_batches is not None else BEHAVIOR_WRITE_BATCH_SIZE)))
        started = time.monotonic()
        deadline = started + BEHAVIOR_WRITE_APPLY_BUDGET_MS / 1000.0
        outcomes: list[tuple[bool, int, str]] = []
        with get_db_connection() as conn:
            begin_immediate_transaction(conn)
            postgres = get_configured_db_engine() == "postgres"
            if postgres:
                _bounded_timeouts(conn, lock_ms=BEHAVIOR_WRITE_LOCK_TIMEOUT_MS,
                                  statement_ms=BEHAVIOR_WRITE_STATEMENT_TIMEOUT_MS)
                if not conn.execute("SELECT pg_try_advisory_xact_lock(?, ?) AS acquired", APPLY_LOCK).fetchone()["acquired"]:
                    return 0
            while len(outcomes) < limit and (not outcomes or time.monotonic() < deadline):
                row = self._select_next_receipt(conn, postgres=postgres)
                if row is None:
                    break
                outcomes.append(self._apply_receipt(conn, row))
                if self._stop_event.is_set():
                    break
            if not outcomes:
                return 0
            self._commit(conn)
        # Counters describe transactions whose commit was confirmed locally.
        # Unknown commits intentionally leave them unchanged until DB sampling.
        duration_ms = round((time.monotonic() - started) * 1000.0, 3)
        successes = sum(success for success, _events, _error in outcomes)
        errors = [error for _success, _events, error in outcomes if error]
        with self._lock:
            self._stats["committed_batch_count"] += successes
            self._stats["committed_event_count"] += sum(events for _success, events, _error in outcomes)
            self._stats["retry_count"] += len(errors)
            self._stats["drain_transactions"] += 1
            self._stats["last_drain_batch_count"] = len(outcomes)
            self._stats["max_drain_batch_count"] = max(self._stats["max_drain_batch_count"], len(outcomes))
            self._stats["last_drain_duration_ms"] = duration_ms
            self._stats["last_error"] = errors[-1] if errors else ""
            if successes:
                self._stats["last_success_at"] = datetime.now().isoformat()
        return len(outcomes)

    def _drain_once(self) -> bool:
        """Compatibility/test entry point: apply at most one immutable receipt."""
        return bool(self._drain_batch(max_batches=1))

    def _commit(self, conn) -> None:
        try:
            conn.commit()
        except Exception:
            self._increment("unknown_commit_count")
            raise

    def _refresh_depth(self) -> None:
        with get_db_connection() as conn:
            row = conn.execute("""SELECT
                COALESCE(SUM(CASE WHEN state='pending' THEN 1 ELSE 0 END),0) AS depth,
                MIN(CASE WHEN state='pending' THEN received_at END) AS oldest,
                COALESCE(SUM(CASE WHEN state='pending' AND attempts>0 THEN 1 ELSE 0 END),0) AS retrying,
                COALESCE(SUM(CASE WHEN state='quarantined' THEN 1 ELSE 0 END),0) AS quarantined,
                MIN(CASE WHEN state='quarantined' THEN received_at END) AS oldest_quarantined
                FROM behavior_write_inbox WHERE state IN ('pending','quarantined')""").fetchone()
        self._update_stats(queue_depth=int(row["depth"]), oldest_queued_at=row["oldest"],
                           quarantined_count=int(row["quarantined"]), oldest_quarantined_at=row["oldest_quarantined"],
                           retrying_count=int(row["retrying"]), queue_depth_known=True,
                           snapshot_at=datetime.now().isoformat())

    def _cleanup_receipts(self) -> None:
        # IDs without a creation time remain as compact compatibility tombstones.
        # Timestamped IDs are rejected after seven days, before their ten-day tombstone expires.
        with get_db_connection() as conn:
            if get_configured_db_engine() == "postgres":
                _bounded_timeouts(conn, lock_ms=BEHAVIOR_WRITE_LOCK_TIMEOUT_MS,
                                  statement_ms=BEHAVIOR_WRITE_STATEMENT_TIMEOUT_MS)
                if not conn.execute("SELECT pg_try_advisory_xact_lock(?, ?) AS acquired", CLEANUP_LOCK).fetchone()["acquired"]:
                    return
            conn.execute("""DELETE FROM behavior_write_inbox WHERE batch_key IN (
                SELECT batch_key FROM behavior_write_inbox WHERE state IN ('applied','discarded')
                    AND SUBSTR(batch_id, 1, 3) = 'b1_'
                    AND COALESCE(resolved_at, applied_at, received_at) < ?
                ORDER BY COALESCE(resolved_at, applied_at, received_at), batch_key LIMIT 2000)""",
                ((datetime.now() - timedelta(days=RECEIPT_RETENTION_DAYS)).isoformat(),))
            conn.commit()

    def _run(self) -> None:
        next_depth_sample = 0.0
        next_cleanup = 0.0
        while not self._stop_event.is_set():
            self._update_stats(last_heartbeat_at=datetime.now().isoformat())
            try:
                if time.monotonic() >= next_cleanup:
                    self._cleanup_receipts()
                    next_cleanup = time.monotonic() + 60.0
                if time.monotonic() >= next_depth_sample:
                    self._refresh_depth()
                    next_depth_sample = time.monotonic() + 2.0
                if self._drain_batch():
                    continue
            except Exception as exc:
                # A bad connection, savepoint failure or unknown commit cannot kill supervision.
                # The transaction either committed its receipt or remains pending for replay.
                self._update_stats(last_error=_error_code(exc))
                self._increment("retry_count")
            self._wake.wait(BEHAVIOR_WRITE_FLUSH_INTERVAL_MS / 1000.0)
            self._wake.clear()

    def flush(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        self._wake.set()
        while time.monotonic() < deadline:
            self._refresh_depth()
            if self.stats()["queue_depth"] == 0:
                if self.stats()["quarantined_count"]:
                    raise DatabaseBusyError("仍有已隔离的行为记录待管理员处理，原数据保留。")
                return
            if not self.stats()["alive"]:
                raise DatabaseBusyError("行为写入线程已停止，记录保留在持久队列。")
            time.sleep(0.05)
        raise TimeoutError("行为记录尚未排空，记录保留在持久队列。")
