from fastapi import APIRouter, Depends, Query

from ...db.connection import get_db_connection
from ...dependencies import get_current_teacher
from ...services.organization_suggestion_service import get_organization_suggestions

router = APIRouter()


@router.get("/organization-suggestions")
def api_organization_suggestions(
    school_code: str = Query(default="", max_length=160),
    school_name: str = Query(default="", max_length=160),
    college: str = Query(default="", max_length=160),
    user: dict = Depends(get_current_teacher),
):
    """Name-only recommendations for authenticated teachers, with editable inputs."""
    with get_db_connection() as conn:
        result = get_organization_suggestions(
            conn, school_code=school_code, school_name=school_name, college=college,
        )
    return {"status": "success", **result}
