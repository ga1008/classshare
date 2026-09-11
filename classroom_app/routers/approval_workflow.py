"""HTTP surface of the generic approval workflow (``/api/approvals``).

Teachers and students share these endpoints; the service layer decides who may
create, view, decide or cancel a given request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import approval_workflow_service as workflow
from ..services.approval_workflow_service import ApprovalWorkflowError

router = APIRouter(prefix="/api/approvals")


async def _json_payload(request: Request, *, optional: bool = False) -> dict[str, Any]:
    if optional and int(request.headers.get("content-length") or 0) <= 0:
        return {}
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


def _run(conn, action):
    try:
        result = action()
        conn.commit()
        return result
    except ApprovalWorkflowError as exc:
        conn.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    except Exception:
        conn.rollback()
        raise


@router.get("/types", response_class=JSONResponse)
def list_types(user: dict = Depends(get_current_user)):
    return {"items": workflow.list_request_types()}


@router.get("", response_class=JSONResponse)
def list_requests(
    scope: str = "incoming",
    status: str = "",
    assignment_id: str = "",
    request_type: str = "",
    limit: int = 50,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        items = workflow.list_requests(
            conn, user, scope=scope, status=status, assignment_id=assignment_id or None,
            request_type=request_type, limit=limit,
        )
        pending = workflow.count_pending_requests(conn, user, assignment_id=assignment_id or None)
        conn.commit()
    return {"items": items, "pending_count": pending}


@router.post("", response_class=JSONResponse)
async def create_request(request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        item = _run(conn, lambda: workflow.create_request(
            conn, user,
            request_type=str(payload.get("request_type") or ""),
            subject_id=payload.get("subject_id"),
            reason=payload.get("reason"),
            payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else None,
        ))
    return {"status": "success", "request": item}


@router.get("/{request_id}", response_class=JSONResponse)
def get_request(request_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        item = _run(conn, lambda: workflow.get_request(conn, user, request_id))
    return {"request": item}


@router.post("/{request_id}/approve", response_class=JSONResponse)
async def approve_request(request_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request, optional=True)
    decision_payload = payload.get("decision_payload") if isinstance(payload.get("decision_payload"), dict) else {}
    with get_db_connection() as conn:
        item = _run(conn, lambda: workflow.decide_request(
            conn, user, request_id, decision="approve", note=payload.get("note"), decision_payload=decision_payload,
        ))
    return {"status": "success", "request": item}


@router.post("/{request_id}/reject", response_class=JSONResponse)
async def reject_request(request_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        item = _run(conn, lambda: workflow.decide_request(
            conn, user, request_id, decision="reject", note=payload.get("note"),
        ))
    return {"status": "success", "request": item}


@router.post("/{request_id}/cancel", response_class=JSONResponse)
async def cancel_request(request_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request, optional=True)
    with get_db_connection() as conn:
        item = _run(conn, lambda: workflow.cancel_request(conn, user, request_id, note=payload.get("note")))
    return {"status": "success", "request": item}
