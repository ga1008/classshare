"""Authenticated detail and student appeal entry for imported attendance."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import approval_workflow_service as workflow
from ..services import smart_attendance_appeal_service as attendance
from ..services.trusted_network_service import require_same_origin


async def _private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"


router = APIRouter(prefix="/api/classrooms", dependencies=[Depends(_private_response)])


class AppealPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    source_fingerprint: str = Field(min_length=1, max_length=128)


@router.get("/{offering_id}/smart-attendance/records/{source_key}")
def get_record(offering_id: int, source_key: str, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            return attendance.record_detail(conn, offering_id, source_key, user)
        except workflow.ApprovalWorkflowError as exc:
            raise HTTPException(exc.status_code, exc.message) from None


@router.post("/{offering_id}/smart-attendance/records/{source_key}/appeals")
def create_appeal(offering_id: int, source_key: str, payload: AppealPayload, request: Request, user: dict = Depends(get_current_user)):
    require_same_origin(request)
    with get_db_connection() as conn:
        try:
            attendance.current_fact(conn, source_key, user, expected_offering=offering_id)
            result = workflow.create_request(conn, user, request_type=attendance.KEY, subject_id=source_key,
                reason=payload.reason, payload={"source_fingerprint": payload.source_fingerprint})
            conn.commit()
            return {"status": "success", "request": result}
        except workflow.ApprovalWorkflowError as exc:
            conn.rollback()
            raise HTTPException(exc.status_code, exc.message) from None
