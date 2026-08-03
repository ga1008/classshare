"""Material-instance signature points, multi-person flows and ordered binding.

The existing signature request table remains the review/audit source of truth
for one signature.  This module adds the user-facing aggregate: one flow per
signature point and material revision, containing an ordered set of signature
requests.  Approved grants are reusable for that exact revision and disappear
automatically when the material is rebuilt into a different revision.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from . import signature_service, signature_workflow_service


MAX_SIGNATURES_PER_POINT = 12
ACTIVE_FLOW_STATUSES = {"pending", "partially_approved"}


def _clean(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _scope(
    conn: Any,
    user: dict[str, Any],
    *,
    function_point_key: str,
    material_type: str,
    material_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    actor = signature_service.build_signature_actor(conn, user)
    scope = signature_workflow_service.resolve_material_scope(
        conn,
        actor,
        function_point_key=function_point_key,
        material_type=material_type,
        material_id=material_id,
    )
    return actor, scope


def _active_flow_row(conn: Any, actor: dict[str, Any], scope: dict[str, str]) -> Any:
    return conn.execute(
        """
        SELECT * FROM signature_point_flows
        WHERE function_point_key = ? AND material_type = ? AND material_id = ?
          AND material_revision = ? AND requester_role = ? AND requester_id = ?
          AND status IN ('pending', 'partially_approved')
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (
            scope["function_point_key"], scope["material_type"], scope["material_id"],
            scope["material_revision"], actor["role"], int(actor["id"]),
        ),
    ).fetchone()


def _serialize_flow(conn: Any, flow_row: Any | None) -> dict[str, Any] | None:
    if not flow_row:
        return None
    flow = dict(flow_row)
    rows = conn.execute(
        """
        SELECT item.*, signature.name AS signature_name,
               signature.subject_name AS signature_subject_name
        FROM signature_point_flow_items item
        JOIN electronic_signatures signature ON signature.id = item.signature_id
        WHERE item.flow_id = ?
        ORDER BY item.display_order, item.id
        """,
        (int(flow["id"]),),
    ).fetchall()
    request_ids = [int(row["request_id"]) for row in rows if int(row["request_id"] or 0) > 0]
    requests_by_id = signature_workflow_service.get_requests(conn, request_ids)
    items: list[dict[str, Any]] = []
    for row in rows:
        request_id = int(row["request_id"] or 0)
        items.append(
            {
                "id": int(row["id"]),
                "signature_id": int(row["signature_id"]),
                "signature_name": row["signature_subject_name"] or row["signature_name"] or "",
                "display_order": int(row["display_order"] or 0),
                "status": row["status"] or "",
                "granted_at": row["granted_at"] or "",
                "request_id": request_id,
                "request": requests_by_id.get(request_id),
            }
        )
    return {
        "id": int(flow["id"]),
        "function_point_key": flow["function_point_key"],
        "material_type": flow["material_type"],
        "material_id": flow["material_id"],
        "material_revision": flow["material_revision"],
        "material_label": flow["material_label"] or "",
        "status": flow["status"] or "",
        "request_note": flow["request_note"] or "",
        "created_at": flow["created_at"] or "",
        "updated_at": flow["updated_at"] or "",
        "ended_at": flow["ended_at"] or "",
        "items": items,
    }


def _binding_ids(conn: Any, scope: dict[str, str]) -> list[int]:
    rows = conn.execute(
        """
        SELECT signature_id FROM signature_point_bindings
        WHERE function_point_key = ? AND material_type = ? AND material_id = ?
          AND material_revision = ?
        ORDER BY display_order, id
        """,
        (
            scope["function_point_key"], scope["material_type"],
            scope["material_id"], scope["material_revision"],
        ),
    ).fetchall()
    return [int(row["signature_id"]) for row in rows]


def _lock_binding_scope(conn: Any, scope: dict[str, str]) -> None:
    """Serialize replacement of one material revision's ordered bindings."""
    if get_configured_db_engine() != "postgres":
        return
    lock_key = ":".join(
        [
            "signature-point-binding",
            scope["function_point_key"],
            scope["material_type"],
            scope["material_id"],
            scope["material_revision"],
        ]
    )
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (lock_key,))


def get_point_state(
    conn: Any,
    user: dict[str, Any],
    *,
    function_point_key: str,
    material_type: str,
    material_id: str,
) -> dict[str, Any]:
    actor, scope = _scope(
        conn,
        user,
        function_point_key=function_point_key,
        material_type=material_type,
        material_id=material_id,
    )
    listed = signature_service.list_signatures(conn, user, limit=500)
    grant_rows = conn.execute(
        """
        SELECT request.signature_id, item.id AS grant_item_id
        FROM signature_access_request_items item
        JOIN signature_access_requests request ON request.id = item.request_id
        WHERE request.requester_role = ? AND request.requester_id = ?
          AND request.status IN ('approved', 'partially_used')
          AND item.function_point_key = ? AND item.status = 'available'
          AND item.material_type = ? AND item.material_id = ? AND item.material_revision = ?
        ORDER BY request.requested_at, request.id, item.id
        """,
        (
            actor["role"], int(actor["id"]), scope["function_point_key"],
            scope["material_type"], scope["material_id"], scope["material_revision"],
        ),
    ).fetchall()
    grants = {int(row["signature_id"]): int(row["grant_item_id"]) for row in grant_rows}
    signatures: list[dict[str, Any]] = []
    for item in listed.get("items") or []:
        direct_mode = signature_workflow_service.direct_authorization_mode(actor, item)
        grant_item_id = grants.get(int(item["id"]))
        can_use = bool(direct_mode or grant_item_id)
        try:
            owner_id = int(item.get("owner_id") or 0)
        except (TypeError, ValueError):
            owner_id = 0
        owner_bound = str(item.get("owner_role") or "").strip().lower() in {"teacher", "student"} and owner_id > 0
        signer_bound = bool(item.get("subject_bound"))
        # Unbound signatures stay requestable — platform admins review them —
        # so a colleague without an account never blocks an official document.
        needs_admin_review = not owner_bound and not signer_bound
        signatures.append(
            {
                **item,
                "can_use": can_use,
                "can_request": not can_use,
                "needs_admin_review": needs_admin_review,
                "signer_bound": signer_bound,
                "authorization_mode": direct_mode or ("approval" if grant_item_id else ""),
                "grant_item_id": grant_item_id,
            }
        )
    selected_ids = _binding_ids(conn, scope)
    active_flow = _serialize_flow(conn, _active_flow_row(conn, actor, scope))
    return {
        "status": "success",
        "point": {
            "key": scope["function_point_key"],
            "label": scope["function_point_label"],
        },
        "material": {
            "type": scope["material_type"],
            "id": scope["material_id"],
            "revision": scope["material_revision"],
            "label": scope["material_label"],
        },
        "signatures": signatures,
        "usable_signatures": [item for item in signatures if item["can_use"]],
        "requestable_signatures": [item for item in signatures if item["can_request"]],
        "selected_signature_ids": selected_ids,
        "active_flow": active_flow,
    }


def create_point_flow(
    conn: Any,
    user: dict[str, Any],
    *,
    function_point_key: str,
    material_type: str,
    material_id: str,
    signature_ids: list[int],
    note: str = "",
) -> dict[str, Any]:
    actor, scope = _scope(
        conn,
        user,
        function_point_key=function_point_key,
        material_type=material_type,
        material_id=material_id,
    )
    ordered: list[int] = []
    for value in signature_ids:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0 and normalized not in ordered:
            ordered.append(normalized)
    if not ordered:
        raise signature_service.SignatureServiceError(400, "请至少选择一个需要申请的签名。")
    if len(ordered) > MAX_SIGNATURES_PER_POINT:
        raise signature_service.SignatureServiceError(400, f"同一签名点一次最多申请 {MAX_SIGNATURES_PER_POINT} 个签名。")
    engine = get_configured_db_engine()
    if engine == "postgres":
        lock_key = ":".join(
            ["signature-point-flow", scope["function_point_key"], scope["material_type"], scope["material_id"], scope["material_revision"], actor["role"], str(actor["id"])]
        )
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (lock_key,))
    if _active_flow_row(conn, actor, scope):
        raise signature_service.SignatureServiceError(409, "该签名点已有未结束的申请流程。")
    for signature_id in ordered:
        signature = signature_workflow_service._signature_row(conn, signature_id)
        if not signature_service.can_view_signature(actor, signature):
            raise signature_service.SignatureServiceError(403, "申请中包含当前账号不可查看的签名。")
        access = signature_workflow_service.access_state(
            conn,
            actor,
            signature,
            scope["function_point_key"],
            material_type=scope["material_type"],
            material_id=scope["material_id"],
            material_revision=scope["material_revision"],
        )
        if access.get("can_use"):
            raise signature_service.SignatureServiceError(400, "申请中包含已可直接使用或已获授权的签名。")
        if not signature_workflow_service._reviewer_identities(conn, signature) and not signature_workflow_service._admin_identities(conn):
            raise signature_service.SignatureServiceError(
                422, "申请中有签名未绑定任何账号，且平台暂无管理员可代为审批。"
            )
    try:
        flow_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO signature_point_flows (
                function_point_key, material_type, material_id, material_revision,
                material_label, requester_role, requester_id, request_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope["function_point_key"], scope["material_type"], scope["material_id"],
                scope["material_revision"], scope["material_label"], actor["role"],
                int(actor["id"]), _clean(note, 300),
            ),
            engine=engine,
        )
    except Exception as exc:
        detail = str(exc).lower()
        if isinstance(exc, sqlite3.IntegrityError) or (engine == "postgres" and ("unique" in detail or "duplicate" in detail)):
            raise signature_service.SignatureServiceError(409, "该签名点已有未结束的申请流程。") from exc
        raise
    for order, signature_id in enumerate(ordered):
        result = signature_workflow_service.create_access_request(
            conn,
            user,
            signature_id,
            function_point_keys=[scope["function_point_key"]],
            note=note,
            flow_id=flow_id,
            material_type=scope["material_type"],
            material_id=scope["material_id"],
            material_revision=scope["material_revision"],
            material_label=scope["material_label"],
            display_order=order,
        )
        request_id = int(result["request"]["id"])
        conn.execute(
            """
            INSERT INTO signature_point_flow_items (
                flow_id, signature_id, display_order, request_id
            ) VALUES (?, ?, ?, ?)
            """,
            (flow_id, signature_id, order, request_id),
        )
    flow_row = conn.execute("SELECT * FROM signature_point_flows WHERE id = ?", (flow_id,)).fetchone()
    return {"status": "success", "flow": _serialize_flow(conn, flow_row)}


def end_point_flow(conn: Any, user: dict[str, Any], flow_id: int) -> dict[str, Any]:
    actor = signature_service.build_signature_actor(conn, user)
    flow = conn.execute("SELECT * FROM signature_point_flows WHERE id = ? LIMIT 1", (int(flow_id),)).fetchone()
    if not flow:
        raise signature_service.SignatureServiceError(404, "签名申请流程不存在。")
    if str(flow["requester_role"] or "") != actor["role"] or int(flow["requester_id"] or 0) != int(actor["id"]):
        raise signature_service.SignatureServiceError(403, "只有申请人可以结束该流程。")
    if str(flow["status"] or "") not in ACTIVE_FLOW_STATUSES:
        raise signature_service.SignatureServiceError(409, "该申请流程已经结束。")
    conn.execute(
        "UPDATE signature_point_flows SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
        (int(flow_id),),
    )
    pending = conn.execute(
        "SELECT request_id FROM signature_point_flow_items WHERE flow_id = ? AND status = 'pending'",
        (int(flow_id),),
    ).fetchall()
    for row in pending:
        request_id = int(row["request_id"] or 0)
        conn.execute(
            "UPDATE signature_access_requests SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (request_id,),
        )
        conn.execute(
            "UPDATE signature_access_request_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE request_id = ? AND status = 'pending'",
            (request_id,),
        )
        conn.execute(
            "UPDATE signature_access_request_reviewers SET status = 'cancelled' WHERE request_id = ? AND status = 'pending'",
            (request_id,),
        )
    conn.execute(
        "UPDATE signature_point_flow_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE flow_id = ? AND status = 'pending'",
        (int(flow_id),),
    )
    return {"status": "success", "flow_id": int(flow_id)}


def bind_point_signatures(
    conn: Any,
    user: dict[str, Any],
    *,
    function_point_key: str,
    material_type: str,
    material_id: str,
    signature_ids: list[int],
    context_label: str = "",
    metadata: dict[str, Any] | None = None,
    ip: str = "",
    user_agent: str = "",
) -> list[int]:
    actor, scope = _scope(
        conn,
        user,
        function_point_key=function_point_key,
        material_type=material_type,
        material_id=material_id,
    )
    ordered: list[int] = []
    for value in signature_ids:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0 and normalized not in ordered:
            ordered.append(normalized)
    if len(ordered) > MAX_SIGNATURES_PER_POINT:
        raise signature_service.SignatureServiceError(400, f"同一签名点最多绑定 {MAX_SIGNATURES_PER_POINT} 个签名。")
    # Binding is a replace-all operation. Without a scope lock, two concurrent
    # saves can both delete the old rows and then interleave their inserts,
    # producing a mixed order that neither user submitted.
    _lock_binding_scope(conn, scope)
    for signature_id in ordered:
        signature = signature_workflow_service._signature_row(conn, signature_id)
        if not signature_service.resolve_signature_file_path(signature):
            raise signature_service.SignatureServiceError(422, "所选签名图片文件不存在。")
        signature_workflow_service.authorize_and_consume_signature_use(
            conn,
            user,
            signature_id,
            function_point_key=scope["function_point_key"],
            context_type=scope["material_type"],
            context_id=scope["material_id"],
            context_label=context_label or scope["material_label"],
            metadata=metadata or {},
            ip=ip,
            user_agent=user_agent,
        )
    conn.execute(
        """
        DELETE FROM signature_point_bindings
        WHERE function_point_key = ? AND material_type = ? AND material_id = ?
          AND material_revision = ?
        """,
        (
            scope["function_point_key"], scope["material_type"],
            scope["material_id"], scope["material_revision"],
        ),
    )
    for order, signature_id in enumerate(ordered):
        conn.execute(
            """
            INSERT INTO signature_point_bindings (
                function_point_key, material_type, material_id, material_revision,
                signature_id, display_order, bound_by_role, bound_by_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope["function_point_key"], scope["material_type"], scope["material_id"],
                scope["material_revision"], signature_id, order, actor["role"], int(actor["id"]),
            ),
        )
    return ordered


def binding_signature_ids(
    conn: Any,
    *,
    function_point_key: str,
    material_type: str,
    material_id: str,
    material_revision: str,
) -> list[int]:
    return _binding_ids(
        conn,
        {
            "function_point_key": function_point_key,
            "material_type": material_type,
            "material_id": material_id,
            "material_revision": material_revision,
        },
    )
