"""Commit intake before acknowledging behavior, then apply each receipt atomically."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from ..config import BEHAVIOR_WRITE_QUEUE_SIZE, BEHAVIOR_WRITE_SYNC_TIMEOUT_MS
from ..database import get_db_connection
from ..db.connection import begin_immediate_transaction, get_configured_db_engine
from ..db.errors import DatabaseBusyError, DatabaseConnectionError, is_retryable_conflict
from .behavior_event_validation import BehaviorValidationError, MAX_PAYLOAD_BYTES, validate_behavior_payload

CAPACITY_LOCK = (0x42485652, 1)
APPLY_LOCK = (0x42485652, 2)
CLIENT_RETRY_DAYS = 7
RECEIPT_RETENTION_DAYS = 10
PERMANENT_DATA_FAILURE_LIMIT = 3


def new_batch_id() -> str:
    return f"b1_{int(time.time() * 1000)}_{uuid.uuid4().hex}"


class BehaviorBatchConflict(ValueError):
    """A previously used batch ID has a different immutable payload."""


class BehaviorDeletionPending(ValueError):
    """Accepted behavior must finish before its actor or classroom can be removed."""


def _actor_table(role: str) -> str:
    if role not in {"student", "teacher"}:
        raise BehaviorBatchConflict("行为记录的账号类型无效。")
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


def _event_count(row) -> int:
    try:
        events = json.loads(row["events_json"])
        return len(events) if isinstance(events, list) else 0
    except (ValueError, TypeError):
        return 0


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
                snapshot = self._persist_in_connection(conn, request, batch_key, payload_hash, received_at)
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
            raise BehaviorBatchConflict("课堂已变更，请刷新页面后查看行为记录状态。")
        actor = conn.execute(f"SELECT id FROM {_actor_table(request.user_role)} WHERE id = ?" +
                             (" FOR KEY SHARE" if postgres else ""), (request.user_pk,)).fetchone()
        if actor is None:
            raise BehaviorBatchConflict("账号已变更，请刷新页面后查看行为记录状态。")
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
                raise BehaviorBatchConflict("行为批次编号无效。") from exc
            age = datetime.now() - created
            if age > timedelta(days=CLIENT_RETRY_DAYS) or age < -timedelta(days=1):
                raise BehaviorBatchConflict("行为批次已超过重试期限，请刷新页面查看记录状态。")
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
        for attempt in range(3):
            committing = False
            try:
                with get_db_connection() as conn:
                    begin_immediate_transaction(conn)
                    if get_configured_db_engine() == "postgres":
                        conn.execute("SET LOCAL lock_timeout = '500ms'")
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
                if not retryable or attempt == 2:
                    raise DatabaseBusyError("行为记录暂时无法确认，请使用原批次编号重试。") from exc
                self._stop_event.wait(0.05 * (2 ** attempt) + random.uniform(0.0, 0.05))
        raise DatabaseBusyError("行为记录暂时无法确认。")

    def _drain_once(self) -> bool:
        """One short transaction owns events, their effects and the applied receipt."""
        with get_db_connection() as conn:
            begin_immediate_transaction(conn)
            postgres = get_configured_db_engine() == "postgres"
            if postgres and not conn.execute("SELECT pg_try_advisory_xact_lock(?, ?) AS acquired", APPLY_LOCK).fetchone()["acquired"]:
                return False
            # Preserve actor order across classrooms too. Daily streaks/rewards
            # share the actor, whereas heartbeat state is scoped to the classroom.
            row = conn.execute("""SELECT q.* FROM behavior_write_inbox q
                WHERE q.state = 'pending' AND (q.retry_at IS NULL OR q.retry_at <= ?)
                  AND NOT EXISTS (SELECT 1 FROM behavior_write_inbox previous
                      WHERE previous.state = 'pending' AND previous.user_role = q.user_role
                        AND previous.user_pk = q.user_pk
                        AND (previous.received_at < q.received_at OR
                             (previous.received_at = q.received_at AND previous.batch_key < q.batch_key)))
                ORDER BY q.received_at, q.batch_key LIMIT 1""" + (" FOR UPDATE SKIP LOCKED" if postgres else ""),
                (datetime.now().isoformat(),)).fetchone()
            if row is None:
                return False
            conn.execute("SAVEPOINT behavior_apply")
            try:
                recovery = json.loads(row["result_json"] or "{}").get("recovery_actions") or []
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
                    session_started_at=row["session_started_at"], recorded_at=row["received_at"])
                snapshot.update(queued_event_count=0, durable_event_count=len(json.loads(row["events_json"])))
                audit = json.loads(row["result_json"] or "{}").get("recovery_actions")
                if audit:
                    snapshot["recovery_actions"] = audit
                completed_at = datetime.now().isoformat()
                conn.execute("""UPDATE behavior_write_inbox SET state = 'applied', applied_at = ?, resolved_at = ?,
                    retry_at = NULL, last_error = '', result_json = ?, events_json = '[]'
                    WHERE batch_key = ? AND state = 'pending'""",
                    (completed_at, completed_at, json.dumps(snapshot, ensure_ascii=False, default=str), row["batch_key"]))
                conn.execute("RELEASE SAVEPOINT behavior_apply")
            except Exception as exc:
                # PostgreSQL must leave its failed transaction state before any further SQL.
                conn.execute("ROLLBACK TO SAVEPOINT behavior_apply")
                conn.execute("RELEASE SAVEPOINT behavior_apply")
                attempts = int(row["attempts"]) + 1
                retry_at = (datetime.now() + timedelta(seconds=min(300, 2 ** min(attempts, 8)) + random.random())).isoformat()
                metadata = json.loads(row["result_json"] or "{}")
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
                self._commit(conn)
                self._increment("retry_count")
                self._update_stats(last_error=_error_code(exc))
                return True
            self._commit(conn)
        self._increment("committed_batch_count")
        self._increment("committed_event_count", int(snapshot.get("accepted_event_count") or 0))
        self._update_stats(last_success_at=datetime.now().isoformat(), last_error="")
        return True

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
            conn.execute("""DELETE FROM behavior_write_inbox WHERE batch_key IN (
                SELECT batch_key FROM behavior_write_inbox WHERE state IN ('applied','discarded')
                    AND SUBSTR(batch_id, 1, 3) = 'b1_'
                    AND COALESCE(resolved_at, applied_at, received_at) < ? LIMIT 2000)""",
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
                if self._drain_once():
                    continue
            except Exception as exc:
                # A bad connection, savepoint failure or unknown commit cannot kill supervision.
                # The transaction either committed its receipt or remains pending for replay.
                self._update_stats(last_error=_error_code(exc))
                self._increment("retry_count")
            self._wake.wait(0.5)
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
