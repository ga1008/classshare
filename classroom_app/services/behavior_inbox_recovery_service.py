"""Explicit administrator recovery; never edits an accepted event payload."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hmac
import json
import re

from ..db.connection import begin_immediate_transaction, get_configured_db_engine
from .behavior_event_validation import BehaviorValidationError, validate_behavior_payload
from . import behavior_inbox_service as inbox
from .behavior_inbox_service import BehaviorBatchConflict, _actor_table


class BehaviorRecoveryOrderError(BehaviorValidationError):
    """Historical replay would regress already committed behavior or streaks."""


def validate_recovery_order(conn, row) -> None:
    """Called under APPLY_LOCK, both at admin retry and at actual replay."""
    def local_time(value):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else stamp
        except (ValueError, TypeError) as exc:
            raise BehaviorRecoveryOrderError("无法确认旧批次的时间顺序，需专门补偿修复或明确放弃。") from exc
    received = local_time(row["received_at"])
    age = datetime.now() - received
    if age < timedelta(0) or age > timedelta(days=inbox.CLIENT_RETRY_DAYS):
        raise BehaviorRecoveryOrderError("隔离记录已超出安全恢复时间窗口，需专门补偿修复或明确放弃。")
    later = conn.execute("""SELECT 1 FROM behavior_write_inbox WHERE user_role=? AND user_pk=? AND state='applied'
        AND (received_at>? OR (received_at=? AND batch_key>?)) LIMIT 1""",
        (row["user_role"], row["user_pk"], row["received_at"], row["received_at"], row["batch_key"])).fetchone()
    if later:
        raise BehaviorRecoveryOrderError("该账号已有更晚的行为入账，不能倒序回放；需专门补偿修复或明确放弃。")
    suffix = " FOR UPDATE" if get_configured_db_engine() == "postgres" else ""
    states = conn.execute("""SELECT last_event_at,last_presence_at,current_session_started_at
        FROM classroom_behavior_states WHERE user_role=? AND user_pk=?""" + suffix,
        (row["user_role"], row["user_pk"])).fetchall()
    if any(local_time(value) > received for state in states for value in
           (state["last_event_at"], state["last_presence_at"], state["current_session_started_at"]) if value):
        raise BehaviorRecoveryOrderError("账号已有更晚的行为状态，不能倒序回放；需专门补偿修复或明确放弃。")
    if row["user_role"] == "student":
        streak = conn.execute("SELECT last_active_date FROM student_activity_streaks WHERE student_id=?" + suffix,
                              (row["user_pk"],)).fetchone()
        if streak and streak["last_active_date"]:
            try:
                last_day = date.fromisoformat(str(streak["last_active_date"]))
            except ValueError as exc:
                raise BehaviorRecoveryOrderError("无法确认连续学习日期，需先修复该状态。") from exc
            if last_day > received.date():
                raise BehaviorRecoveryOrderError("账号已有更晚的活跃或补签日期，不能倒退连续学习状态。")


def recover_behavior_receipt(conn, *, action: str, batch_key: str, payload_hash: str,
                             class_offering_id: int, user_role: str, user_pk: int,
                             operator: str, reason: str) -> dict:
    if action not in {"retry", "discard"} or not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (batch_key, payload_hash)):
        raise ValueError("明确选择 retry/discard，并提供完整批次键及原始 payload hash。")
    if type(class_offering_id) is not int or type(user_pk) is not int or min(class_offering_id, user_pk) <= 0:
        raise ValueError("恢复操作必须明确指定课堂、账号类型和账号 ID。")
    _actor_table(user_role)
    if (not isinstance(operator, str) or not operator.strip() or len(operator) > 80
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 500
            or "\x00" in operator or "\x00" in reason):
        raise ValueError("恢复操作必须提供操作者和不超过 500 字的处理原因。")
    operator.encode("utf-8")
    reason.encode("utf-8")
    postgres = get_configured_db_engine() == "postgres"
    if not postgres and not conn.in_transaction:
        begin_immediate_transaction(conn)
    if action == "retry":
        for table, identifier in (("class_offerings", class_offering_id), (_actor_table(user_role), user_pk)):
            if conn.execute(f"SELECT id FROM {table} WHERE id = ?" + (" FOR KEY SHARE" if postgres else ""),
                            (identifier,)).fetchone() is None:
                raise BehaviorBatchConflict("课堂或账号已不存在，不能重新入账。")
        if postgres:
            conn.execute("SELECT pg_advisory_xact_lock(?, ?)", inbox.APPLY_LOCK)
    row = conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_key = ?" +
                       (" FOR UPDATE" if postgres else ""), (batch_key,)).fetchone()
    if (row is None or not hmac.compare_digest(row["payload_hash"], payload_hash)
            or int(row["class_offering_id"]) != class_offering_id or row["user_role"] != user_role
            or int(row["user_pk"]) != user_pk or row["state"] != "quarantined"):
        raise BehaviorBatchConflict("回执状态、原始 hash 或目标身份已变化；请重新检查后操作。")
    if action == "retry":
        validate_recovery_order(conn, row)
        validate_behavior_payload(events=json.loads(row["events_json"]), page_key=row["page_key"],
                                  display_name=row["display_name"], session_started_at=row["session_started_at"])
    metadata = json.loads(row["result_json"] or "{}")
    audit = list(metadata.get("recovery_actions") or [])
    audit.append({"action": action, "operator": operator.strip(), "reason": reason.strip(),
                  "at": datetime.now().isoformat(), "payload_hash": payload_hash})
    metadata["recovery_actions"] = audit
    metadata["permanent_error_streak"] = 0
    state = "pending" if action == "retry" else "discarded"
    conn.execute("""UPDATE behavior_write_inbox SET state=?, retry_at=NULL, resolved_at=?,
        result_json=? WHERE batch_key=? AND state='quarantined'""",
        (state, None if action == "retry" else datetime.now().isoformat(), json.dumps(metadata, ensure_ascii=False), batch_key))
    return {"batch_key": batch_key, "state": state, "applied": False,
            "payload_hash": payload_hash, "original_payload_retained": True}
