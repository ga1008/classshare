"""Business receipts for a finished Agent task (moved from the retired DSH service).

Write evidence comes from durable platform receipts (``agent_action_executions``
and ``agent_platform_requests``), never from model text.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from .agent_actor_service import task_actor_identity


def platform_request_observations(conn, task):
    """Summarize durable HTTP observations without promoting them to domain proof."""
    owner = task_actor_identity(task)
    rows = conn.execute("SELECT id,operation_id,actor_role,actor_id,capability_key,status,result_json "
                        "FROM agent_platform_requests WHERE task_id=? ORDER BY created_at,id", (task["id"],)).fetchall()
    observations, blockers = [], []
    for row in rows:
        item = {"request_id": row["id"], "operation_id": row["operation_id"], "capability_key": row["capability_key"],
                "status": row["status"], "verified_business": False, "automatic_retry_allowed": False}
        if (row["actor_role"], int(row["actor_id"])) != owner:
            blockers.append({"code": "request_identity_mismatch", "request_id": row["id"]})
        else:
            result = json.loads(row["result_json"] or "{}")
            item["observation"] = {key: result[key] for key in ("http_status", "body_sha256", "follow_up", "reason") if key in result}
            if row["status"] != "observed_http_result":
                blockers.append({"code": "platform_request_" + row["status"], "request_id": row["id"]})
        observations.append(item)
    return observations, blockers


def verified_platform_operations(conn, task, rows):
    """Use committed platform receipts, never ACP text, as write evidence.

    A scheduler receipt proves submission. Its asynchronous business outcome
    is separately reconciled with the exact job and current material binding.
    This only reads job state; pending jobs continue under their own lifecycle.
    """
    operations, blockers = [], []
    owner = task_actor_identity(task)
    for row in rows:
        item = {"operation_id": row["operation_id"], "action": row["action"], "status": row["status"]}
        if (row["actor_role"], int(row["actor_id"])) != owner:
            item["completion_status"] = "unverified"
            blockers.append({"code": "operation_identity_mismatch", "operation_id": row["operation_id"]})
            operations.append(item)
            continue
        result = json.loads(row["result_json"])
        item["result"] = result
        item["completion_status"] = "committed" if row["status"] == "completed" else "unverified"
        if row["status"] != "completed":
            blockers.append({"code": "operation_" + row["status"], "operation_id": row["operation_id"]})
        elif row["action"] == "generate_session_document":
            evidence, blocker = _verify_generated_document(conn, owner, result)
            item["domain_result"] = evidence
            item["completion_status"] = evidence["status"]
            if blocker:
                blockers.append({"code": blocker, "operation_id": row["operation_id"]})
        elif result.get("completion_status") not in (None, "completed", "committed"):
            # New deferred domain adapters must add a real reconciler before
            # their submission receipt can count as completed business.
            item["completion_status"] = "unverified"
            blockers.append({"code": "domain_result_unverified", "operation_id": row["operation_id"]})
        operations.append(item)
    return operations, blockers


def _verify_generated_document(conn, owner, result):
    snapshot = result.get("generation_task") or {}
    identifier = result.get("ref_id")
    row = conn.execute("""SELECT g.*, s.learning_material_id AS bound_material_id,
        s.class_offering_id AS current_offering_id, o.teacher_id AS current_teacher_id
        FROM session_material_generation_tasks g
        LEFT JOIN class_offering_sessions s ON s.id=g.session_id
        LEFT JOIN class_offerings o ON o.id=g.class_offering_id WHERE g.id=?""", (identifier,)).fetchone()
    evidence = {"generation_task_id": identifier, "status": "unverified"}
    if (not row or owner[0] != "teacher" or snapshot.get("id") != identifier
            or any(int(row[key] or 0) != int(snapshot.get(key) or 0) for key in ("teacher_id", "class_offering_id", "session_id"))
            or int(row["teacher_id"] or 0) != owner[1]
            or row["current_teacher_id"] != owner[1] or row["current_offering_id"] != row["class_offering_id"]):
        return evidence, "domain_job_identity_mismatch"
    evidence.update(status=row["status"], class_offering_id=row["class_offering_id"], session_id=row["session_id"])
    if row["status"] in {"queued", "running"}:
        return evidence, "domain_job_pending"
    if row["status"] != "completed":
        return evidence, "domain_job_failed"
    material_id = row["generated_material_id"]
    material = conn.execute("SELECT id,teacher_id,material_path,file_hash,file_size FROM course_materials WHERE id=?", (material_id,)).fetchone()
    binding = conn.execute("SELECT material_id FROM class_offering_learning_materials WHERE class_offering_id=? AND session_id=? AND material_id=?",
                           (row["class_offering_id"], row["session_id"], material_id)).fetchone()
    if (not material or material["teacher_id"] != owner[1] or row["bound_material_id"] != material_id
            or not binding or material["material_path"] != row["generated_material_path"]):
        evidence["status"] = "unverified"
        return evidence, "domain_material_binding_missing"
    # The ordinary material receipt validator checks the immutable file too.
    from .agent_platform_write_service import validate_action_receipt
    try:
        validate_action_receipt(conn, actor_role=owner[0], actor_id=owner[1], action="save_material_draft",
            result={"ref_id": material_id, "file_hash": material["file_hash"], "file_size": int(material["file_size"] or 0)})
    except (HTTPException, OSError, ValueError, TypeError):
        evidence["status"] = "unverified"
        return evidence, "domain_material_integrity_failed"
    evidence.update(generated_material_id=material_id, generated_material_path=row["generated_material_path"], binding_verified=True)
    return evidence, None
