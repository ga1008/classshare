"""Offline, caller-controlled repairs using already-persisted official evidence."""
from __future__ import annotations

import json
import sqlite3

from .academic_schedule_prediction_service import (
    ScheduleSnapshotError, ScheduleSyncLeaseError, _json, claim_schedule_sync,
    reconcile_and_publish_snapshot, release_schedule_sync,
)


def repair_stored_schedule_snapshot(conn, teacher_id: int, semester_id: int, *, now=None) -> dict:
    """Republish existing evidence without remote login, network or a commit.

    Caller rolls the transaction back for dry-run, or commits an authorized
    repair. The source timestamp and previous warnings remain reviewable; this
    is not advertised as a fresh successful network synchronization.
    """
    if isinstance(conn, sqlite3.Connection) and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT offline_schedule_repair")
    try:
        previous_state = conn.execute("SELECT * FROM teacher_academic_schedule_sync_state WHERE teacher_id=?",
                                      (int(teacher_id),)).fetchone()
        lease = claim_schedule_sync(conn, teacher_id, now=now)
        if lease["status"] != "claimed":
            raise ScheduleSyncLeaseError("教务同步正在进行，离线修复未执行。")
        source = conn.execute("SELECT * FROM teacher_academic_schedule_snapshots WHERE teacher_id=? AND semester_id=?",
                              (int(teacher_id), int(semester_id))).fetchone()
        if not source:
            raise ScheduleSnapshotError("没有可复用的完整教务快照。")
        previous_warnings = json.loads(source["warnings_json"] or "[]")
        result = reconcile_and_publish_snapshot(conn, teacher_id, semester_id,
                                                json.loads(source["snapshot_json"]), lease["token"], now=now)
        current = conn.execute("SELECT warnings_json FROM teacher_academic_schedule_snapshots WHERE teacher_id=? AND semester_id=?",
                               (int(teacher_id), int(semester_id))).fetchone()
        warnings = json.loads(current["warnings_json"] or "[]")
        # Preserve earlier anomaly evidence without duplicating each replay.
        seen = {_json(item) for item in warnings}
        for item in previous_warnings:
            if _json(item) not in seen:
                warnings.append(item)
                seen.add(_json(item))
        conn.execute("UPDATE teacher_academic_schedule_snapshots SET warnings_json=? WHERE teacher_id=? AND semester_id=?",
                     (_json(warnings), int(teacher_id), int(semester_id)))
        release_schedule_sync(conn, teacher_id, lease["token"], now=now)
        if previous_state:
            conn.execute("UPDATE teacher_academic_schedule_sync_state SET status=?,last_attempt_at=?,last_success_at=?,error=? WHERE teacher_id=?",
                         (previous_state["status"], previous_state["last_attempt_at"], previous_state["last_success_at"],
                          previous_state["error"], int(teacher_id)))
        conn.execute("RELEASE SAVEPOINT offline_schedule_repair")
        return {"teacher_id": int(teacher_id), "semester_id": int(semester_id),
                "source_revision": int(source["revision"]), "source_published_at": source["published_at"],
                "revision": result.get("revision"), "updated_session_ids": result.get("updated_session_ids", []),
                "warnings": warnings}
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT offline_schedule_repair")
        conn.execute("RELEASE SAVEPOINT offline_schedule_repair")
        raise
