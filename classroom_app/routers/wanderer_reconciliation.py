"""Fresh administrator authorization for uncertain service command review."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.wanderer_reconciliation import get_command_reconciliation, reconcile_command

router = APIRouter()
PATH = "/api/manage/system/wanderers/{wanderer_id}/commands/{operation_id}/reconcile"


class ReconciliationBody(BaseModel):
    expected_revision: str
    outcome: str
    reason: str


@router.get(PATH)
def get_reconciliation(wanderer_id: int, operation_id: str, user=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = get_command_reconciliation(conn, user=user, wanderer_id=wanderer_id, operation_id=operation_id)
        conn.commit()
    return {"status": "success", **result}


@router.post(PATH)
def save_reconciliation(wanderer_id: int, operation_id: str, body: ReconciliationBody, user=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = reconcile_command(conn, user=user, wanderer_id=wanderer_id, operation_id=operation_id,
                                   **body.model_dump())
        conn.commit()
    return {"status": "success", **result}
