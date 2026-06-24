"""Endpoints for group-based assignment completion + peer evaluation.

Teacher:
  * GET  /assignments/{id}/group-config  — current binding + selectable schemes
  * POST /assignments/{id}/group-config  — bind to an existing scheme, create a
                                           new scheme then bind, or unbind

Student:
  * GET  /assignments/{id}/peer-eval     — my group + teammates to rate
  * POST /assignments/{id}/peer-eval     — submit 20-point teammate ratings
"""

from .common import *  # noqa: F401,F403  (shared FastAPI + db imports)

from ...services import group_assignment_service as ga
from ...services.collaboration_service import create_group_scheme


router = APIRouter()


def _scheme_options(conn, class_offering_id: int) -> list[dict[str, Any]]:
    """Active group schemes for a class offering with assigned-member counts."""
    rows = conn.execute(
        """
        SELECT s.id, s.name, s.status, s.group_count, s.min_members, s.max_members,
               s.created_at,
               (
                   SELECT COUNT(*)
                   FROM study_group_members m
                   JOIN study_groups g ON g.id = m.group_id
                   WHERE g.scheme_id = s.id AND m.status = 'active'
               ) AS assigned_count
        FROM group_schemes s
        WHERE s.class_offering_id = ?
          AND s.status = 'active'
        ORDER BY s.created_at DESC, s.id DESC
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"] or "随机分组"),
            "status": str(row["status"] or ""),
            "group_count": int(row["group_count"] or 0),
            "min_members": int(row["min_members"] or 0),
            "max_members": int(row["max_members"] or 0),
            "assigned_count": int(row["assigned_count"] or 0),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


@router.get("/assignments/{assignment_id}/group-config", response_class=JSONResponse)
async def get_group_config(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        class_offering_id = assignment.get("class_offering_id")
        if not class_offering_id:
            return {
                "status": "success",
                "supported": False,
                "message": "该作业未关联教学班，无法按小组完成。",
                "binding": None,
                "schemes": [],
            }
        binding = ga.get_assignment_group_binding(conn, assignment_id)
        schemes = _scheme_options(conn, int(class_offering_id))
        conn.commit()
    return {
        "status": "success",
        "supported": True,
        "class_offering_id": int(class_offering_id),
        "binding": binding,
        "schemes": schemes,
    }


@router.post("/assignments/{assignment_id}/group-config", response_class=JSONResponse)
async def set_group_config(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    action = str(data.get("action") or "").strip().lower()
    with get_db_connection() as conn:
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        class_offering_id = assignment.get("class_offering_id")
        if not class_offering_id:
            raise HTTPException(400, "该作业未关联教学班，无法按小组完成。")
        class_offering_id = int(class_offering_id)

        if action == "unbind":
            ga.unbind_assignment(conn, assignment_id=assignment_id)
            conn.commit()
            return {"status": "success", "binding": None}

        scheme_id = data.get("scheme_id")
        new_scheme = data.get("new_scheme")
        if not scheme_id and isinstance(new_scheme, dict):
            # Reuse the collaboration "新建分组" flow, then bind.
            try:
                created = create_group_scheme(conn, class_offering_id, user, new_scheme)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"创建分组方案失败：{exc}") from exc
            scheme_id = created.get("id")
        if not scheme_id:
            raise HTTPException(400, "请选择一个分组方案，或创建新的分组方案。")

        try:
            binding = ga.bind_assignment_to_scheme(
                conn,
                assignment_id=assignment_id,
                class_offering_id=class_offering_id,
                scheme_id=int(scheme_id),
                teacher_id=int(user["id"]),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
    return {"status": "success", "binding": binding}


@router.get("/assignments/{assignment_id}/peer-eval", response_class=JSONResponse)
async def get_peer_eval(assignment_id: str, user: dict = Depends(get_current_student)):
    with get_db_connection() as conn:
        if not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "无权访问该作业")
        context = ga.get_student_group_context(conn, assignment_id, int(user["id"]))
        conn.commit()
    if context is None:
        return {"status": "success", "is_group": False}
    return {
        "status": "success",
        "is_group": True,
        "in_group": bool(context.get("in_group")),
        "group": context.get("group"),
        "peers": context.get("peers", []),
    }


@router.post("/assignments/{assignment_id}/peer-eval", response_class=JSONResponse)
async def submit_peer_eval(assignment_id: str, request: Request, user: dict = Depends(get_current_student)):
    data = await request.json()
    raw_ratings = data.get("ratings")
    ratings: dict = {}
    if isinstance(raw_ratings, list):
        for item in raw_ratings:
            if not isinstance(item, dict):
                continue
            reviewee = item.get("reviewee_student_id")
            points = item.get("points")
            if reviewee is None:
                continue
            ratings[reviewee] = points
    elif isinstance(raw_ratings, dict):
        ratings = raw_ratings

    with get_db_connection() as conn:
        if not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "无权访问该作业")
        try:
            result = ga.submit_peer_contributions(
                conn,
                assignment_id=assignment_id,
                reviewer_id=int(user["id"]),
                ratings=ratings,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
    return {"status": "success", **result}
