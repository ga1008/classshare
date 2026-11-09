"""Auditable administrator declarations for ended, uncertain service writes.

Never executes the original command or presents a human declaration as a
verified business fact. Native HTTP observations remain immutable evidence.
"""
import hashlib
import json
import time
import uuid

from fastapi import HTTPException

from ..time_utils import local_iso
from .resource_access_service import is_super_admin_teacher


TERMINAL_TURNS = {"completed", "failed", "interrupted", "cancelled", "canceled"}


def _admin(conn, user, *, lock=False):
    if user.get("role") != "teacher":
        raise HTTPException(403, "只有超管教师可以核对散修指令。")
    if lock:
        conn.execute("UPDATE teachers SET is_active=is_active WHERE id=?", (user.get("id"),))
    if not is_super_admin_teacher(conn, user.get("id")):
        raise HTTPException(403, "只有在职超管教师可以核对散修指令。")


def _load(conn, wanderer_id, operation_id, *, lock=False):
    try:
        if str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "指令编号无效。") from None
    row = conn.execute("SELECT * FROM agent_platform_requests WHERE principal_kind='wanderer_service' "
                       "AND wanderer_id=? AND operation_id=?", (int(wanderer_id), operation_id)).fetchone()
    if row is None:
        raise HTTPException(404, "没有该散修的平台请求。")
    if lock:
        # Admission and resource settlement both lock resource owners first.
        from .wanderer_resource_service import lock_agents
        owners = {int(wanderer_id)}
        for resource in _resources(conn, wanderer_id, operation_id):
            owners.update(int(line["owner_id"]) for line in json.loads(resource["lines_json"]))
        lock_agents(conn, sorted(owners))
        conn.execute("UPDATE wanderer_turns SET state=state WHERE turn_id=?", (row["turn_id"],))
        conn.execute("UPDATE agent_platform_requests SET id=id WHERE id=?", (row["id"],))
        conn.execute("UPDATE wanderer_commands SET state=state WHERE operation_id=?", (operation_id,))
        row = conn.execute("SELECT * FROM agent_platform_requests WHERE id=?", (row["id"],)).fetchone()
    command = conn.execute("SELECT * FROM wanderer_commands WHERE operation_id=? AND turn_id=?",
                           (operation_id, row["turn_id"])).fetchone()
    turn = conn.execute("SELECT * FROM wanderer_turns WHERE turn_id=? AND wanderer_id=?",
                        (row["turn_id"], int(wanderer_id))).fetchone()
    if command is None or turn is None:
        raise HTTPException(409, "原指令或轮次账本不完整，无法核对。")
    return dict(row), dict(command), dict(turn)


def _resources(conn, wanderer_id, operation_id):
    return conn.execute("SELECT * FROM wanderer_resource_reservations WHERE actor_id=? AND operation_id LIKE ?",
                        (int(wanderer_id), f"platform-action:{int(wanderer_id)}:{operation_id}:%")).fetchall()


def _revision(row, command, turn):
    return hashlib.sha256(json.dumps([row, command, turn["state"]], sort_keys=True,
                                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _block_reason(row, command, turn):
    if row["reconciliation_status"] == "cleared":
        return "核对声明已保存，不能改写。"
    if not row["mutates"]:
        return "读取操作无需人工核对。"
    if command["state"] != "uncertain":
        return "仅可核对结果不确定的指令。"
    if turn["state"] not in TERMINAL_TURNS:
        return "原轮次尚未结束。"
    if row["host_execution_finished_at"] is None:
        return "尚无执行进程已结束的证明，请先进行执行恢复核查。"
    return ""


def _view(row, command, turn):
    reason = _block_reason(row, command, turn)
    return {"operation_id": row["operation_id"], "wanderer_id": row["wanderer_id"],
            "turn_id": row["turn_id"], "state": command["state"],
            "revision": _revision(row, command, turn), "can_reconcile": not reason,
            "block_reason": reason, "summary": f"{row['method']} {row['path']}",
            "observation": {"status": row["status"], "result": json.loads(row["result_json"]),
                            "host_execution_finished": row["host_execution_finished_at"] is not None},
            "reconciliation": {"status": row["reconciliation_status"],
                               "outcome": row["reconciliation_resolution"], "reason": row["reconciliation_note"],
                               "at": row["reconciled_at"], "actor_id": row["reconciled_by_id"],
                               "verified_business": False}, "automatic_retry_allowed": False}


def _audit(conn, user, wanderer_id, operation_id, section):
    conn.execute("INSERT INTO wanderer_admin_access(id,wanderer_id,actor_id,section,selector,created_at) "
                 "VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), int(wanderer_id), int(user["id"]),
                                        section, operation_id, local_iso()))


def get_command_reconciliation(conn, *, user, wanderer_id, operation_id):
    _admin(conn, user)
    row, command, turn = _load(conn, wanderer_id, operation_id)
    _audit(conn, user, wanderer_id, operation_id, "command_reconciliation_read")
    return {"request": _view(row, command, turn)}


def reconcile_command(conn, *, user, wanderer_id, operation_id, expected_revision, outcome, reason):
    if outcome not in {"occurred", "not_occurred"}:
        raise HTTPException(400, "请选择已发生或未发生。")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000 or any(
            0xD800 <= ord(c) <= 0xDFFF or (ord(c) < 32 and c not in "\n\r\t") for c in reason):
        raise HTTPException(400, "请填写不超过 1000 字的核对依据。")
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise HTTPException(400, "请先重新读取指令状态。")
    reason = reason.strip()
    _admin(conn, user, lock=True)
    row, command, turn = _load(conn, wanderer_id, operation_id, lock=True)
    if row["reconciliation_status"] == "cleared":
        if row["reconciliation_resolution"] != outcome or row["reconciliation_note"] != reason:
            raise HTTPException(409, "已保存的核对声明不能改写。")
        return {"request": _view(row, command, turn), "replayed": True}
    if _revision(row, command, turn) != expected_revision:
        raise HTTPException(409, "执行状态或观察已变化，请重新读取后核对。")
    block = _block_reason(row, command, turn)
    if block:
        raise HTTPException(409, block)
    now = int(time.time())
    conn.execute("UPDATE agent_platform_requests SET reconciliation_status='cleared',"
                 "reconciliation_resolution=?,reconciled_at=?,reconciled_by_role='teacher',"
                 "reconciled_by_id=?,reconciliation_note=? WHERE id=?",
                 (outcome, now, int(user["id"]), reason, row["id"]))
    from .wanderer_resource_service import settle
    for resource in _resources(conn, wanderer_id, operation_id):
        if resource["state"] == "reserved":
            settle(conn, resource["operation_id"], release=outcome == "not_occurred")
    receipt = json.loads(command["receipt_json"])
    receipt.update(status="resolved", verified_business=False, automatic_retry_allowed=False,
                   reconciliation={"outcome": outcome, "reason": reason, "actor_id": int(user["id"]),
                                   "at": now, "verified_business": False})
    conn.execute("UPDATE wanderer_commands SET state='resolved',receipt_json=?,updated_at=? WHERE operation_id=?",
                 (json.dumps(receipt, ensure_ascii=False), local_iso(), operation_id))
    _audit(conn, user, wanderer_id, operation_id, "command_reconciliation_write")
    return {"request": _view(*_load(conn, wanderer_id, operation_id)), "replayed": False}
