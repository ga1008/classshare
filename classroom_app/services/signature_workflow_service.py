"""Feature- and material-bound signature request and review workflow."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from . import message_center_service, signature_service


REQUEST_STATUS_VALUES = {
    "pending",
    "approved",
    "partially_used",
    "consumed",
    "rejected",
    "cancelled",
}


def _clean(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _signature_row(conn: Any, signature_id: int) -> Any:
    row = conn.execute(
        """
        SELECT * FROM electronic_signatures
        WHERE id = ? AND status = 'active' AND deleted_at IS NULL
        LIMIT 1
        """,
        (int(signature_id),),
    ).fetchone()
    if not row:
        raise signature_service.SignatureServiceError(404, "签名不存在或已停用。")
    return row


def _same_identity(role: Any, user_id: Any, actor: dict[str, Any]) -> bool:
    try:
        normalized_id = int(user_id or 0)
    except (TypeError, ValueError):
        normalized_id = 0
    return str(role or "").strip().lower() == actor.get("role") and normalized_id == int(actor.get("id") or 0)


def direct_authorization_mode(actor: dict[str, Any], signature: Any) -> str:
    """Return the direct-use mode dictated by signer identity or ownership."""
    if _same_identity(signature["subject_role"], signature["subject_id"], actor):
        return "self"
    if _same_identity(signature["owner_role"], signature["owner_id"], actor):
        return "owner"
    return ""


def list_function_points(conn: Any, *, enabled_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE is_enabled = 1" if enabled_only else ""
    rows = conn.execute(
        f"""
        SELECT point_key, label, module_key, description, is_enabled
        FROM signature_function_points
        {where}
        ORDER BY module_key, point_key
        """
    ).fetchall()
    return [
        {
            "key": row["point_key"],
            "label": row["label"],
            "module_key": row["module_key"],
            "description": row["description"],
            "is_enabled": bool(row["is_enabled"]),
        }
        for row in rows
    ]


def _function_points(conn: Any, keys: list[str]) -> list[Any]:
    normalized = list(dict.fromkeys(_clean(key, 160) for key in keys if _clean(key, 160)))
    if not normalized:
        raise signature_service.SignatureServiceError(400, "请至少选择一个签名功能点。")
    if len(normalized) > 20:
        raise signature_service.SignatureServiceError(400, "一次申请最多选择 20 个功能点。")
    placeholders = ",".join("?" for _ in normalized)
    rows = conn.execute(
        f"""
        SELECT point_key, label, module_key, description
        FROM signature_function_points
        WHERE is_enabled = 1 AND point_key IN ({placeholders})
        """,
        tuple(normalized),
    ).fetchall()
    by_key = {str(row["point_key"]): row for row in rows}
    missing = [key for key in normalized if key not in by_key]
    if missing:
        raise signature_service.SignatureServiceError(400, f"签名功能点未注册或已停用：{missing[0]}")
    return [by_key[key] for key in normalized]


def _available_item(
    conn: Any,
    actor: dict[str, Any],
    signature_id: int,
    function_point_key: str,
    *,
    material_type: str = "",
    material_id: str = "",
    material_revision: str = "",
) -> Any:
    scoped = bool(material_type and material_id and material_revision)
    scope_sql = (
        "AND item.material_type = ? AND item.material_id = ? AND item.material_revision = ?"
        if scoped else
        "AND item.material_type = '' AND item.material_id = '' AND item.material_revision = ''"
    )
    params: list[Any] = [int(signature_id), actor["role"], int(actor["id"]), function_point_key]
    if scoped:
        params.extend([material_type, material_id, material_revision])
    return conn.execute(
        """
        SELECT item.*, request.status AS request_status
        FROM signature_access_request_items item
        JOIN signature_access_requests request ON request.id = item.request_id
        WHERE request.signature_id = ?
          AND request.requester_role = ?
          AND request.requester_id = ?
          AND request.status IN ('approved', 'partially_used')
          AND item.function_point_key = ?
          AND item.status = 'available'
          {scope_sql}
        ORDER BY request.requested_at, request.id, item.id
        LIMIT 1
        """.format(scope_sql=scope_sql),
        tuple(params),
    ).fetchone()


def access_state(
    conn: Any,
    actor: dict[str, Any],
    signature: Any,
    function_point_key: str,
    *,
    material_type: str = "",
    material_id: str = "",
    material_revision: str = "",
) -> dict[str, Any]:
    point = _function_points(conn, [function_point_key])[0]
    mode = direct_authorization_mode(actor, signature)
    if mode:
        return {
            "can_use": True,
            "authorization_mode": mode,
            "function_point_key": point["point_key"],
            "function_point_label": point["label"],
            "grant_item_id": None,
        }
    if not signature_service.can_view_signature(actor, signature):
        return {
            "can_use": False,
            "can_request": False,
            "authorization_mode": "",
            "function_point_key": point["point_key"],
            "function_point_label": point["label"],
            "grant_item_id": None,
        }
    item = _available_item(
        conn,
        actor,
        int(signature["id"]),
        str(point["point_key"]),
        material_type=material_type,
        material_id=material_id,
        material_revision=material_revision,
    )
    return {
        "can_use": bool(item),
        "can_request": not bool(item),
        "authorization_mode": "approval" if item else "",
        "function_point_key": point["point_key"],
        "function_point_label": point["label"],
        "grant_item_id": int(item["id"]) if item else None,
    }


def _identity_name(conn: Any, role: str, user_id: int) -> str:
    table = "teachers" if role == "teacher" else "students" if role == "student" else ""
    if not table or int(user_id or 0) <= 0:
        return ""
    row = conn.execute(f"SELECT name FROM {table} WHERE id = ? LIMIT 1", (int(user_id),)).fetchone()
    return _clean(row["name"] if row else "", 80)


def _reviewer_identities(conn: Any, signature: Any) -> list[dict[str, Any]]:
    candidates = [
        (signature["owner_role"], signature["owner_id"], "owner", signature["owner_name_snapshot"]),
        (signature["subject_role"], signature["subject_id"], "signer", signature["subject_name"]),
    ]
    reviewers: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for role, raw_id, kind, snapshot in candidates:
        normalized_role = str(role or "").strip().lower()
        try:
            reviewer_id = int(raw_id or 0)
        except (TypeError, ValueError):
            reviewer_id = 0
        key = (normalized_role, reviewer_id)
        if normalized_role not in {"teacher", "student"} or reviewer_id <= 0 or key in seen:
            continue
        seen.add(key)
        reviewers.append(
            {
                "role": normalized_role,
                "id": reviewer_id,
                "kind": kind,
                "name": _clean(snapshot, 80) or _identity_name(conn, normalized_role, reviewer_id),
            }
        )
    return reviewers


def _notify(
    conn: Any,
    *,
    recipients: list[dict[str, Any]],
    actor: dict[str, Any],
    title: str,
    body: str,
    ref_type: str,
    ref_id: str,
    metadata: dict[str, Any],
) -> int:
    count = 0
    seen: set[tuple[str, int]] = set()
    for recipient in recipients:
        role = str(recipient.get("role") or "")
        user_id = int(recipient.get("id") or 0)
        identity = (role, user_id)
        if role not in {"teacher", "student"} or user_id <= 0 or identity in seen:
            continue
        seen.add(identity)
        payload = message_center_service._build_notification_payload(
            recipient_role=role,
            recipient_user_pk=user_id,
            category=message_center_service.MESSAGE_CATEGORY_SIGNATURE,
            title=title,
            severity="important",
            body_preview=body,
            actor_role=actor.get("role") or "",
            actor_user_pk=int(actor.get("id") or 0),
            actor_display_name=actor.get("name") or "",
            link_url="/manage/me/signatures#signature-requests",
            ref_type=ref_type,
            ref_id=ref_id,
            metadata=metadata,
        )
        if message_center_service._insert_notification_if_allowed(conn, payload):
            count += 1
    return count


def create_access_request(
    conn: Any,
    user: dict[str, Any],
    signature_id: int,
    *,
    function_point_keys: list[str],
    note: str = "",
    flow_id: int | None = None,
    material_type: str = "",
    material_id: str = "",
    material_revision: str = "",
    material_label: str = "",
    display_order: int = 0,
) -> dict[str, Any]:
    actor = signature_service.build_signature_actor(conn, user)
    if actor.get("role") != "teacher":
        raise signature_service.SignatureServiceError(403, "当前仅教师可以提出签名使用申请。")
    signature = _signature_row(conn, signature_id)
    if not signature_service.can_view_signature(actor, signature):
        raise signature_service.SignatureServiceError(403, "当前账号无权查看此签名。")
    if direct_authorization_mode(actor, signature):
        raise signature_service.SignatureServiceError(400, "本人签名或归属权在本人名下的签名无需申请。")
    points = _function_points(conn, function_point_keys)
    normalized_material_type = _clean(material_type, 80)
    normalized_material_id = _clean(material_id, 160)
    normalized_material_revision = _clean(material_revision, 160)
    normalized_point_key = str(points[0]["point_key"]) if len(points) == 1 else ""
    if bool(flow_id) and (len(points) != 1 or not all((normalized_material_type, normalized_material_id, normalized_material_revision))):
        raise signature_service.SignatureServiceError(400, "签名点流程必须绑定一个功能点和明确的材料版本。")
    if get_configured_db_engine() == "postgres":
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(?))",
            (f"signature-request:{int(signature_id)}:{actor['role']}:{int(actor['id'])}:{normalized_point_key}:{normalized_material_type}:{normalized_material_id}:{normalized_material_revision}",),
        )
    existing = conn.execute(
        """
        SELECT id FROM signature_access_requests
        WHERE signature_id = ? AND requester_role = ? AND requester_id = ?
          AND function_point_key = ? AND material_type = ?
          AND material_id = ? AND material_revision = ?
          AND status = 'pending'
        ORDER BY requested_at DESC, id DESC LIMIT 1
        """,
        (
            int(signature_id), actor["role"], int(actor["id"]), normalized_point_key,
            normalized_material_type, normalized_material_id, normalized_material_revision,
        ),
    ).fetchone()
    if existing:
        raise signature_service.SignatureServiceError(409, "该签名已有待审批申请，请先等待审批或撤销。")
    try:
        subject_id = int(signature["subject_id"] or 0)
    except (TypeError, ValueError):
        subject_id = 0
    if str(signature["subject_role"] or "").strip().lower() not in {"teacher", "student"} or subject_id <= 0:
        raise signature_service.SignatureServiceError(
            422,
            "该签名尚未绑定可核验的签名者账号，请先由归属人补充签名者账号后再申请。",
        )
    reviewers = _reviewer_identities(conn, signature)
    if not reviewers:
        raise signature_service.SignatureServiceError(422, "该签名尚未绑定可核验的归属人或签名者账号，暂不能申请。")

    try:
        request_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO signature_access_requests (
                signature_id, requester_teacher_id, requester_role, requester_id,
                owner_role, owner_id, status, request_note,
                context_type, context_id, context_label,
                flow_id, function_point_key, material_type, material_id,
                material_revision, display_order
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(signature_id),
                int(actor["id"]),
                actor["role"],
                int(actor["id"]),
                signature["owner_role"] or "",
                signature["owner_id"],
                _clean(note, 300),
                normalized_material_type or "signature_workflow",
                normalized_material_id,
                _clean(material_label, 120) or "、".join(str(point["label"]) for point in points)[:120],
                int(flow_id) if flow_id else None,
                normalized_point_key,
                normalized_material_type,
                normalized_material_id,
                normalized_material_revision,
                max(0, int(display_order or 0)),
            ),
            engine=get_configured_db_engine(),
        )
    except Exception as exc:
        detail = str(exc).lower()
        if isinstance(exc, sqlite3.IntegrityError) or (
            get_configured_db_engine() == "postgres" and ("unique" in detail or "duplicate" in detail)
        ):
            raise signature_service.SignatureServiceError(409, "该签名已有待审批申请，请先等待审批或撤销。") from exc
        raise
    for point in points:
        conn.execute(
            """
            INSERT INTO signature_access_request_items (
                request_id, function_point_key, function_point_label_snapshot,
                material_type, material_id, material_revision
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request_id, point["point_key"], point["label"],
                normalized_material_type, normalized_material_id, normalized_material_revision,
            ),
        )
    for reviewer in reviewers:
        conn.execute(
            """
            INSERT INTO signature_access_request_reviewers (
                request_id, reviewer_role, reviewer_id, reviewer_kind, reviewer_name_snapshot
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (request_id, reviewer["role"], reviewer["id"], reviewer["kind"], reviewer["name"]),
        )
    labels = "、".join(str(point["label"]) for point in points)
    _notify(
        conn,
        recipients=reviewers,
        actor=actor,
        title="收到签名使用申请",
        body=f"{actor['name']} 申请在“{labels}”使用“{signature['subject_name'] or signature['name']}”签名，材料：{_clean(material_label, 120) or '当前材料'}。任一审批人同意后，仅可在当前材料版本不限次数使用。",
        ref_type="signature_request",
        ref_id=str(request_id),
        metadata={"request_id": request_id, "signature_id": int(signature_id), "function_point_keys": [p["point_key"] for p in points]},
    )
    return {"status": "success", "request": get_request(conn, request_id)}


def _serialize_request_item(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "function_point_key": row["function_point_key"],
        "function_point_label": row["function_point_label_snapshot"],
        "status": row["status"],
        "consumed_at": row["consumed_at"] or "",
        "context_type": row["consumed_context_type"] or "",
        "context_id": row["consumed_context_id"] or "",
        "usage_log_id": row["usage_log_id"],
        "material_type": row["material_type"] or "",
        "material_id": row["material_id"] or "",
        "material_revision": row["material_revision"] or "",
    }


def _serialize_request_reviewer(row: Any) -> dict[str, Any]:
    return {
        "role": row["reviewer_role"],
        "id": int(row["reviewer_id"]),
        "kind": row["reviewer_kind"],
        "name": row["reviewer_name_snapshot"],
        "status": row["status"],
        "review_note": row["review_note"],
        "reviewed_at": row["reviewed_at"] or "",
    }


def _serialize_request_row(
    row: Any,
    *,
    items: list[dict[str, Any]],
    reviewers: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "signature_id": int(row["signature_id"]),
        "signature_name": row["signature_name"] or "",
        "signature_subject_name": row["signature_subject_name"] or row["signature_name"] or "",
        "requester_role": row["requester_role"] or "teacher",
        "requester_id": int(row["requester_id"] or row["requester_teacher_id"] or 0),
        "requester_teacher_id": int(row["requester_teacher_id"] or 0),
        "requester_name": row["requester_name"] or "",
        "status": row["status"] or "",
        "request_note": row["request_note"] or "",
        "context_label": row["context_label"] or "",
        "requested_at": row["requested_at"] or "",
        "decided_at": row["decided_at"] or row["reviewed_at"] or "",
        "cancelled_at": row["cancelled_at"] or "",
        "flow_id": int(row["flow_id"] or 0),
        "function_point_key": row["function_point_key"] or "",
        "material_type": row["material_type"] or "",
        "material_id": row["material_id"] or "",
        "material_revision": row["material_revision"] or "",
        "display_order": int(row["display_order"] or 0),
        "items": items,
        "reviewers": reviewers,
    }


def get_requests(conn: Any, request_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Load several request aggregates with three bounded queries.

    Signature-point dialogs and request inboxes may display hundreds of
    requests.  Loading every request, item and reviewer independently creates
    a 1 + 2N query pattern, so collect each relation once and assemble the
    existing response contract in memory.
    """
    normalized_ids: list[int] = []
    seen_ids: set[int] = set()
    for value in request_ids:
        try:
            request_id = int(value)
        except (TypeError, ValueError):
            continue
        if request_id > 0 and request_id not in seen_ids:
            normalized_ids.append(request_id)
            seen_ids.add(request_id)
    if not normalized_ids:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    request_rows = conn.execute(
        f"""
        SELECT request.*, signature.name AS signature_name,
               signature.subject_name AS signature_subject_name,
               teacher.name AS requester_name
        FROM signature_access_requests request
        JOIN electronic_signatures signature ON signature.id = request.signature_id
        LEFT JOIN teachers teacher
          ON request.requester_role = 'teacher' AND teacher.id = request.requester_id
        WHERE request.id IN ({placeholders})
        """,
        tuple(normalized_ids),
    ).fetchall()
    item_rows = conn.execute(
        f"""
        SELECT id, request_id, function_point_key, function_point_label_snapshot, status,
               consumed_at, consumed_context_type, consumed_context_id, usage_log_id,
               material_type, material_id, material_revision
        FROM signature_access_request_items
        WHERE request_id IN ({placeholders})
        ORDER BY request_id, id
        """,
        tuple(normalized_ids),
    ).fetchall()
    reviewer_rows = conn.execute(
        f"""
        SELECT request_id, reviewer_role, reviewer_id, reviewer_kind,
               reviewer_name_snapshot, status, review_note, reviewed_at
        FROM signature_access_request_reviewers
        WHERE request_id IN ({placeholders})
        ORDER BY request_id, id
        """,
        tuple(normalized_ids),
    ).fetchall()
    items_by_request = {request_id: [] for request_id in normalized_ids}
    reviewers_by_request = {request_id: [] for request_id in normalized_ids}
    for row in item_rows:
        items_by_request.setdefault(int(row["request_id"]), []).append(_serialize_request_item(row))
    for row in reviewer_rows:
        reviewers_by_request.setdefault(int(row["request_id"]), []).append(_serialize_request_reviewer(row))
    return {
        int(row["id"]): _serialize_request_row(
            row,
            items=items_by_request.get(int(row["id"]), []),
            reviewers=reviewers_by_request.get(int(row["id"]), []),
        )
        for row in request_rows
    }


def get_request(conn: Any, request_id: int) -> dict[str, Any]:
    request = get_requests(conn, [int(request_id)]).get(int(request_id))
    if not request:
        raise signature_service.SignatureServiceError(404, "签名使用申请不存在。")
    return request


def list_access_requests(
    conn: Any,
    user: dict[str, Any],
    *,
    direction: str = "incoming",
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    actor = signature_service.build_signature_actor(conn, user)
    normalized_direction = "outgoing" if str(direction).lower() == "outgoing" else "incoming"
    normalized_status = str(status or "").strip().lower()
    if normalized_status and normalized_status not in REQUEST_STATUS_VALUES:
        raise signature_service.SignatureServiceError(400, "不支持的申请状态筛选。")
    where: list[str] = []
    params: list[Any] = []
    if normalized_direction == "outgoing":
        where.extend(["request.requester_role = ?", "request.requester_id = ?"])
        params.extend([actor["role"], int(actor["id"])])
    else:
        where.append(
            "EXISTS (SELECT 1 FROM signature_access_request_reviewers reviewer "
            "WHERE reviewer.request_id = request.id AND reviewer.reviewer_role = ? AND reviewer.reviewer_id = ?)"
        )
        params.extend([actor["role"], int(actor["id"])])
    if normalized_status:
        where.append("request.status = ?")
        params.append(normalized_status)
    rows = conn.execute(
        f"""
        SELECT request.id
        FROM signature_access_requests request
        WHERE {' AND '.join(where)}
        ORDER BY CASE request.status
            WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 WHEN 'partially_used' THEN 2 ELSE 3 END,
            request.requested_at DESC, request.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(int(limit or 100), 500))),
    ).fetchall()
    request_ids = [int(row["id"]) for row in rows]
    requests_by_id = get_requests(conn, request_ids)
    return {
        "items": [requests_by_id[request_id] for request_id in request_ids if request_id in requests_by_id],
        "direction": normalized_direction,
        "status": normalized_status,
        "actor": signature_service.serialize_signature_actor(actor),
    }


def review_access_request(
    conn: Any,
    user: dict[str, Any],
    request_id: int,
    *,
    action: str,
    note: str = "",
) -> dict[str, Any]:
    actor = signature_service.build_signature_actor(conn, user)
    # A no-op conditional update takes the request row lock on both supported
    # engines.  It serializes owner/signer decisions without introducing a
    # transient workflow status that could leak to readers.
    conn.execute(
        "UPDATE signature_access_requests SET status = status WHERE id = ? AND status = 'pending'",
        (int(request_id),),
    )
    request = get_request(conn, request_id)
    if request["status"] != "pending":
        raise signature_service.SignatureServiceError(409, "该申请已结束，不能重复审批。")
    reviewer = conn.execute(
        """
        SELECT * FROM signature_access_request_reviewers
        WHERE request_id = ? AND reviewer_role = ? AND reviewer_id = ?
        LIMIT 1
        """,
        (int(request_id), actor["role"], int(actor["id"])),
    ).fetchone()
    if not reviewer:
        raise signature_service.SignatureServiceError(403, "只有签名归属人或签名者本人可以审批。")
    if reviewer["status"] != "pending":
        raise signature_service.SignatureServiceError(409, "你已经处理过该申请。")
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise signature_service.SignatureServiceError(400, "审批动作必须为 approve 或 reject。")
    reviewer_status = "approved" if normalized_action == "approve" else "rejected"
    reviewer_update = conn.execute(
        """
        UPDATE signature_access_request_reviewers
        SET status = ?, review_note = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (reviewer_status, _clean(note, 300), int(reviewer["id"])),
    )
    if int(reviewer_update.rowcount or 0) != 1:
        raise signature_service.SignatureServiceError(409, "你已经处理过该申请。")
    if reviewer_status == "approved":
        conn.execute(
            """
            UPDATE signature_access_request_reviewers
            SET status = 'superseded'
            WHERE request_id = ? AND id <> ? AND status = 'pending'
            """,
            (int(request_id), int(reviewer["id"])),
        )
        conn.execute(
            """
            UPDATE signature_access_request_items
            SET status = 'available', updated_at = CURRENT_TIMESTAMP
            WHERE request_id = ? AND status = 'pending'
            """,
            (int(request_id),),
        )
        conn.execute(
            """
            UPDATE signature_access_requests
            SET status = 'approved', review_note = ?, reviewed_at = CURRENT_TIMESTAMP,
                reviewed_by_teacher_id = ?, decided_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (_clean(note, 300), int(actor["id"]) if actor["role"] == "teacher" else None, int(request_id)),
        )
        title = "签名使用申请已批准"
        body = f"{actor['name']} 已批准申请；该签名仅在当前材料版本对应签名点内不限次数使用。"
    else:
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM signature_access_request_reviewers
            WHERE request_id = ? AND status = 'pending'
            """,
            (int(request_id),),
        ).fetchone()[0]
        if int(remaining or 0) == 0:
            conn.execute(
                "UPDATE signature_access_request_items SET status = 'rejected', updated_at = CURRENT_TIMESTAMP WHERE request_id = ? AND status = 'pending'",
                (int(request_id),),
            )
            conn.execute(
                """
                UPDATE signature_access_requests
                SET status = 'rejected', review_note = ?, reviewed_at = CURRENT_TIMESTAMP,
                    reviewed_by_teacher_id = ?, decided_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (_clean(note, 300), int(actor["id"]) if actor["role"] == "teacher" else None, int(request_id)),
            )
            title = "签名使用申请已拒绝"
            body = "所有审批人均已拒绝，本次申请已结束。"
        else:
            title = "一位审批人已拒绝签名申请"
            body = "另一位审批人仍可处理；任一人同意后申请即可生效。"
    _sync_point_flow_for_request(conn, request_id)
    refreshed = get_request(conn, request_id)
    _notify(
        conn,
        recipients=[{"role": refreshed["requester_role"], "id": refreshed["requester_id"]}],
        actor=actor,
        title=title,
        body=body,
        ref_type="signature_request_review",
        ref_id=f"{request_id}:{actor['role']}:{actor['id']}",
        metadata={"request_id": int(request_id), "action": normalized_action, "status": refreshed["status"]},
    )
    return {"status": "success", "request": refreshed}


def cancel_access_request(conn: Any, user: dict[str, Any], request_id: int) -> dict[str, Any]:
    actor = signature_service.build_signature_actor(conn, user)
    request = get_request(conn, request_id)
    if request["requester_role"] != actor["role"] or int(request["requester_id"]) != int(actor["id"]):
        raise signature_service.SignatureServiceError(403, "只有申请人可以撤销申请。")
    if request["status"] != "pending":
        raise signature_service.SignatureServiceError(409, "只有待审批申请可以撤销。")
    conn.execute(
        "UPDATE signature_access_requests SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
        (int(request_id),),
    )
    conn.execute(
        "UPDATE signature_access_request_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE request_id = ? AND status = 'pending'",
        (int(request_id),),
    )
    conn.execute(
        "UPDATE signature_access_request_reviewers SET status = 'cancelled' WHERE request_id = ? AND status = 'pending'",
        (int(request_id),),
    )
    _sync_point_flow_for_request(conn, request_id)
    return {"status": "success", "request": get_request(conn, request_id)}


def _sync_point_flow_for_request(conn: Any, request_id: int) -> None:
    """Project a legacy per-signature decision onto its point-level flow."""
    request = conn.execute(
        "SELECT flow_id, status FROM signature_access_requests WHERE id = ? LIMIT 1",
        (int(request_id),),
    ).fetchone()
    if not request or not int(request["flow_id"] or 0):
        return
    flow_id = int(request["flow_id"])
    request_status = str(request["status"] or "")
    item_status = (
        "approved" if request_status in {"approved", "partially_used", "consumed"}
        else "rejected" if request_status == "rejected"
        else "cancelled" if request_status == "cancelled"
        else "pending"
    )
    conn.execute(
        """
        UPDATE signature_point_flow_items
        SET status = ?,
            granted_at = CASE WHEN ? = 'approved' THEN COALESCE(granted_at, CURRENT_TIMESTAMP) ELSE granted_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE flow_id = ? AND request_id = ?
        """,
        (item_status, item_status, flow_id, int(request_id)),
    )
    counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
        FROM signature_point_flow_items WHERE flow_id = ?
        """,
        (flow_id,),
    ).fetchone()
    pending_count = int(counts["pending_count"] or 0)
    approved_count = int(counts["approved_count"] or 0)
    rejected_count = int(counts["rejected_count"] or 0)
    if pending_count:
        flow_status = "partially_approved" if approved_count or rejected_count else "pending"
        ended_at_sql = "ended_at"
    else:
        flow_status = "approved" if approved_count else "rejected"
        ended_at_sql = "CURRENT_TIMESTAMP"
    conn.execute(
        f"UPDATE signature_point_flows SET status = ?, updated_at = CURRENT_TIMESTAMP, ended_at = {ended_at_sql} WHERE id = ? AND status <> 'cancelled'",
        (flow_status, flow_id),
    )


def _idempotency_key(signature_id: int, function_point_key: str, context_type: str, context_id: str) -> str:
    raw = f"signature-use-v1|{int(signature_id)}|{function_point_key}|{context_type}|{context_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_material_scope(
    conn: Any,
    actor: dict[str, Any],
    *,
    function_point_key: str,
    material_type: str,
    material_id: str,
    strict: bool = True,
) -> dict[str, str]:
    """Resolve and authorize the immutable revision behind a signature point."""
    point = _function_points(conn, [function_point_key])[0]
    normalized_type = _clean(material_type, 80)
    normalized_id = _clean(material_id, 160)
    if not normalized_type or not normalized_id:
        raise signature_service.SignatureServiceError(400, "签名点必须绑定明确的材料。")
    if str(point["module_key"] or "") != normalized_type:
        raise signature_service.SignatureServiceError(400, "签名点与材料类型不匹配。")
    if normalized_type == "academic_final_material":
        try:
            row = conn.execute(
                """
                SELECT id, teacher_id, source_file_hash, signature_revision, document_type_label, export_payload_json
                FROM material_ai_import_records WHERE id = ? AND parse_status = 'completed' LIMIT 1
                """,
                (int(normalized_id),),
            ).fetchone()
        except (sqlite3.OperationalError, TypeError, ValueError):
            if strict:
                raise signature_service.SignatureServiceError(404, "期末材料不存在或尚未生成完成。")
            return {"material_revision": ""}
        if not row:
            if not strict:
                return {"material_revision": ""}
            raise signature_service.SignatureServiceError(404, "期末材料不存在或尚未生成完成。")
        revision = _clean(row["signature_revision"], 160)
        if not revision:
            revision = f"source:{_clean(row['source_file_hash'], 120)}" if _clean(row["source_file_hash"], 120) else f"record:{normalized_id}"
        label = str(row["document_type_label"] or "期末材料")
        try:
            payload = json.loads(str(row["export_payload_json"] or "{}"))
            fields = payload.get("fields") if isinstance(payload, dict) else {}
            if isinstance(fields, dict):
                details = " · ".join(_clean(fields.get(key), 60) for key in ("course_name", "class_name") if _clean(fields.get(key), 60))
                if details:
                    label = f"{label} · {details}"
        except (TypeError, ValueError):
            pass
        owner_id = int(row["teacher_id"] or 0)
    elif normalized_type == "assessment_plan":
        try:
            row = conn.execute(
                "SELECT id, teacher_id, title, signature_revision FROM assessment_plans WHERE id = ? LIMIT 1",
                (normalized_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            if strict:
                raise signature_service.SignatureServiceError(404, "课程考核计划表不存在。")
            return {"material_revision": ""}
        if not row:
            if not strict:
                return {"material_revision": ""}
            raise signature_service.SignatureServiceError(404, "课程考核计划表不存在。")
        revision = _clean(row["signature_revision"], 160) or f"plan:{normalized_id}"
        label = _clean(row["title"], 120) or "课程考核计划表"
        owner_id = int(row["teacher_id"] or 0)
    else:
        raise signature_service.SignatureServiceError(400, "该材料类型尚未接入签名点工作流。")
    if actor.get("role") != "teacher" or (
        owner_id != int(actor.get("id") or 0) and not bool(actor.get("is_super_admin"))
    ):
        raise signature_service.SignatureServiceError(403, "只有材料归属教师可以管理该材料的签名点。")
    return {
        "function_point_key": str(point["point_key"]),
        "function_point_label": str(point["label"]),
        "material_type": normalized_type,
        "material_id": normalized_id,
        "material_revision": revision,
        "material_label": label,
    }


def _existing_usage(conn: Any, key: str) -> Any:
    return conn.execute(
        """
        SELECT id, signature_id, authorization_mode, request_id, request_item_id
        FROM signature_usage_logs WHERE idempotency_key = ? LIMIT 1
        """,
        (key,),
    ).fetchone()


def _insert_usage(
    conn: Any,
    *,
    signature: Any,
    actor: dict[str, Any],
    function_point_key: str,
    context_type: str,
    context_id: str,
    context_label: str,
    authorization_mode: str,
    idempotency_key: str,
    material_revision: str,
    request_id: int | None,
    request_item_id: int | None,
    metadata: dict[str, Any],
    ip: str,
    user_agent: str,
) -> int:
    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO signature_usage_logs (
            signature_id, signature_name_snapshot,
            actor_role, actor_id, actor_name_snapshot, action,
            context_type, context_id, context_label, function_point_key,
            request_id, request_item_id, authorization_mode, idempotency_key, material_revision,
            metadata_json, ip, user_agent
        ) VALUES (?, ?, ?, ?, ?, 'use', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(signature["id"]),
            signature["name"] or "",
            actor["role"],
            int(actor["id"]),
            actor.get("name") or "",
            context_type,
            context_id,
            context_label,
            function_point_key,
            request_id,
            request_item_id,
            authorization_mode,
            idempotency_key,
            material_revision,
            json.dumps(metadata or {}, ensure_ascii=False),
            _clean(ip, 80),
            _clean(user_agent, 240),
        ),
        engine=get_configured_db_engine(),
    )


def _refresh_request_status(conn: Any, request_id: int) -> None:
    counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available_count,
            SUM(CASE WHEN status = 'consumed' THEN 1 ELSE 0 END) AS consumed_count
        FROM signature_access_request_items WHERE request_id = ?
        """,
        (int(request_id),),
    ).fetchone()
    available = int(counts["available_count"] or 0)
    consumed = int(counts["consumed_count"] or 0)
    status = "consumed" if available == 0 and consumed > 0 else "partially_used" if consumed > 0 else "approved"
    conn.execute("UPDATE signature_access_requests SET status = ? WHERE id = ?", (status, int(request_id)))


def authorize_and_consume_signature_use(
    conn: Any,
    user: dict[str, Any],
    signature_id: int,
    *,
    function_point_key: str,
    context_type: str,
    context_id: str,
    context_label: str = "",
    metadata: dict[str, Any] | None = None,
    ip: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    actor = signature_service.build_signature_actor(conn, user)
    signature = _signature_row(conn, signature_id)
    point = _function_points(conn, [function_point_key])[0]
    normalized_context_type = _clean(context_type, 80)
    normalized_context_id = _clean(context_id, 160)
    if not normalized_context_type or not normalized_context_id:
        raise signature_service.SignatureServiceError(400, "签名使用必须绑定明确的业务对象。")
    if not signature_service.can_view_signature(actor, signature):
        raise signature_service.SignatureServiceError(403, "当前账号无权查看此签名。")
    material_revision = ""
    if normalized_context_type in {"academic_final_material", "assessment_plan"}:
        scope = resolve_material_scope(
            conn,
            actor,
            function_point_key=str(point["point_key"]),
            material_type=normalized_context_type,
            material_id=normalized_context_id,
            strict=False,
        )
        material_revision = scope["material_revision"]
    raw_key_context = f"{normalized_context_id}|{material_revision}" if material_revision else normalized_context_id
    key = _idempotency_key(int(signature_id), point["point_key"], normalized_context_type, raw_key_context)
    engine = get_configured_db_engine()
    if engine == "postgres":
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"signature-use:{key}",))
    existing = _existing_usage(conn, key)
    if existing:
        return {
            "status": "success",
            "signature_id": int(signature_id),
            "usage_log_id": int(existing["id"]),
            "authorization_mode": existing["authorization_mode"],
            "request_id": existing["request_id"],
            "request_item_id": existing["request_item_id"],
            "already_consumed": True,
        }

    mode = direct_authorization_mode(actor, signature)
    request_id: int | None = None
    request_item_id: int | None = None
    if not mode:
        item = _available_item(
            conn,
            actor,
            int(signature_id),
            str(point["point_key"]),
            material_type=normalized_context_type if material_revision else "",
            material_id=normalized_context_id if material_revision else "",
            material_revision=material_revision,
        )
        if not item:
            raise signature_service.SignatureServiceError(403, "当前材料版本的该签名点没有可用授权，请先申请并获批。")
        request_id = int(item["request_id"])
        request_item_id = int(item["id"])
        if not material_revision:
            cursor = conn.execute(
                """
                UPDATE signature_access_request_items
                SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP,
                    consumed_context_type = ?, consumed_context_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'available' AND consumed_at IS NULL
                """,
                (normalized_context_type, normalized_context_id, request_item_id),
            )
            if int(cursor.rowcount or 0) != 1:
                raise signature_service.SignatureServiceError(409, "该旧版一次性授权已被其他操作使用。")
        mode = "approval"

    usage_log_id = _insert_usage(
        conn,
        signature=signature,
        actor=actor,
        function_point_key=str(point["point_key"]),
        context_type=normalized_context_type,
        context_id=normalized_context_id,
        context_label=_clean(context_label, 160) or str(point["label"]),
        authorization_mode=mode,
        idempotency_key=key,
        material_revision=material_revision,
        request_id=request_id,
        request_item_id=request_item_id,
        metadata=metadata or {},
        ip=ip,
        user_agent=user_agent,
    )
    if request_item_id is not None and request_id is not None and not material_revision:
        conn.execute(
            "UPDATE signature_access_request_items SET usage_log_id = ? WHERE id = ?",
            (usage_log_id, request_item_id),
        )
        _refresh_request_status(conn, request_id)

    recipients = _reviewer_identities(conn, signature)
    _notify(
        conn,
        recipients=recipients,
        actor=actor,
        title="签名已被使用",
        body=f"{actor['name']} 已在“{point['label']}”使用“{signature['subject_name'] or signature['name']}”签名，业务对象：{_clean(context_label, 120) or normalized_context_id}。",
        ref_type="signature_use",
        ref_id=str(usage_log_id),
        metadata={
            "usage_log_id": usage_log_id,
            "signature_id": int(signature_id),
            "function_point_key": point["point_key"],
            "context_type": normalized_context_type,
            "context_id": normalized_context_id,
            "authorization_mode": mode,
        },
    )
    return {
        "status": "success",
        "signature_id": int(signature_id),
        "usage_log_id": usage_log_id,
        "authorization_mode": mode,
        "request_id": request_id,
        "request_item_id": request_item_id,
        "already_consumed": False,
    }
