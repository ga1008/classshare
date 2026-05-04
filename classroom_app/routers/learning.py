from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.learning_progress_service import (
    build_student_global_cultivation_profile,
    build_class_learning_overview,
    create_personal_stage_exam,
    delete_personal_stage_exam,
    record_material_learning_progress,
    serialize_student_learning_progress,
)
from ..services.materials_service import ensure_user_material_access, get_nearest_assignment_anchor
from ..services.todo_service import (
    TodoValidationError,
    build_classroom_todo_overview,
    create_manual_todo,
    delete_manual_todo,
    update_manual_todo,
)

router = APIRouter(prefix="/api")


class MaterialProgressPayload(BaseModel):
    material_id: int
    session_id: Optional[int] = None
    duration_seconds: int = 0
    active_seconds: int = 0
    scroll_ratio: float = 0.0
    completed: bool = False
    page_key: str = "material_viewer"


class ManualTodoPayload(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    start_at: Optional[str] = None
    due_at: Optional[str] = None
    completed: Optional[bool] = None


def _payload_to_dict(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _ensure_classroom_access(conn, class_offering_id: int, user: dict) -> dict:
    row = conn.execute(
        """
        SELECT o.*, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (class_offering_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "课堂不存在")
    offering = dict(row)
    role = str(user.get("role") or "").lower()
    if role == "teacher":
        if int(offering["teacher_id"]) != int(user["id"]):
            raise HTTPException(403, "无权访问该课堂")
        return offering
    if role == "student":
        student = conn.execute("SELECT class_id FROM students WHERE id = ?", (user["id"],)).fetchone()
        if not student or int(student["class_id"]) != int(offering["class_id"]):
            raise HTTPException(403, "您未加入此课堂")
        return offering
    raise HTTPException(403, "无权访问该课堂")


def _ensure_material_in_classroom(conn, class_offering_id: int, material_id: int, user: dict) -> None:
    material = ensure_user_material_access(conn, material_id, user)
    row = conn.execute(
        """
        SELECT 1
        FROM (
            SELECT learning_material_id AS material_id
            FROM class_offering_sessions
            WHERE class_offering_id = ? AND learning_material_id IS NOT NULL
            UNION
            SELECT home_learning_material_id AS material_id
            FROM class_offerings
            WHERE id = ? AND home_learning_material_id IS NOT NULL
            UNION
            SELECT material_id
            FROM course_material_assignments
            WHERE class_offering_id = ?
        ) material_scope
        WHERE material_id = ?
        LIMIT 1
        """,
        (class_offering_id, class_offering_id, class_offering_id, material_id),
    ).fetchone()
    if row:
        return
    if get_nearest_assignment_anchor(conn, class_offering_id, material):
        return
    raise HTTPException(403, "该材料不属于当前课堂学习范围")


@router.get("/learning/cultivation-profile", response_class=JSONResponse)
async def get_cultivation_profile(user: dict = Depends(get_current_user)):
    if user["role"] != "student":
        return {"status": "success", "profile": None}
    with get_db_connection() as conn:
        profile = build_student_global_cultivation_profile(conn, int(user["id"]))
        conn.commit()
        return {"status": "success", "profile": profile}


@router.get("/classrooms/{class_offering_id}/learning/progress", response_class=JSONResponse)
async def get_learning_progress(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        if user["role"] == "teacher":
            overview = build_class_learning_overview(conn, class_offering_id)
            conn.commit()
            return {"status": "success", "overview": overview}
        progress = serialize_student_learning_progress(conn, class_offering_id, int(user["id"]))
        conn.commit()
        return {"status": "success", "progress": progress}


@router.get("/classrooms/{class_offering_id}/todos", response_class=JSONResponse)
async def get_classroom_todos(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        overview = build_classroom_todo_overview(
            conn,
            class_offering_id=class_offering_id,
            user=user,
        )
        return {"status": "success", "todo_overview": overview}


@router.post("/classrooms/{class_offering_id}/todos", response_class=JSONResponse)
async def create_classroom_todo(
    class_offering_id: int,
    payload: ManualTodoPayload,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        try:
            result = create_manual_todo(
                conn,
                class_offering_id=class_offering_id,
                user=user,
                payload=_payload_to_dict(payload),
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except TodoValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        overview = build_classroom_todo_overview(
            conn,
            class_offering_id=class_offering_id,
            user=user,
        )
        return {"status": "success", **result, "todo_overview": overview}


@router.patch("/classrooms/{class_offering_id}/todos/{todo_id}", response_class=JSONResponse)
async def update_classroom_todo(
    class_offering_id: int,
    todo_id: int,
    payload: ManualTodoPayload,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        try:
            result = update_manual_todo(
                conn,
                class_offering_id=class_offering_id,
                todo_id=todo_id,
                user=user,
                payload=_payload_to_dict(payload),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except TodoValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        overview = build_classroom_todo_overview(
            conn,
            class_offering_id=class_offering_id,
            user=user,
        )
        return {"status": "success", **result, "todo_overview": overview}


@router.delete("/classrooms/{class_offering_id}/todos/{todo_id}", response_class=JSONResponse)
async def delete_classroom_todo(
    class_offering_id: int,
    todo_id: int,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        try:
            result = delete_manual_todo(
                conn,
                class_offering_id=class_offering_id,
                todo_id=todo_id,
                user=user,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        conn.commit()
        overview = build_classroom_todo_overview(
            conn,
            class_offering_id=class_offering_id,
            user=user,
        )
        return {"status": "success", **result, "todo_overview": overview}


@router.post("/classrooms/{class_offering_id}/learning/material-progress", response_class=JSONResponse)
async def post_material_progress(
    class_offering_id: int,
    payload: MaterialProgressPayload,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生需要记录学习进度")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        _ensure_material_in_classroom(conn, class_offering_id, int(payload.material_id), user)
        result = record_material_learning_progress(
            conn,
            class_offering_id=class_offering_id,
            student_id=int(user["id"]),
            material_id=int(payload.material_id),
            session_id=payload.session_id,
            duration_seconds=payload.duration_seconds,
            active_seconds=payload.active_seconds,
            scroll_ratio=payload.scroll_ratio,
            completed=payload.completed,
            metadata={"page_key": payload.page_key},
        )
        conn.commit()
        return result


@router.post("/classrooms/{class_offering_id}/learning/stages/{stage_key}/exam", response_class=JSONResponse)
async def create_learning_stage_exam(
    class_offering_id: int,
    stage_key: str,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生可以生成自己的破境试炼")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
    try:
        return await create_personal_stage_exam(class_offering_id, int(user["id"]), stage_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/classrooms/{class_offering_id}/learning/stages/{stage_key}/exam", response_class=JSONResponse)
async def delete_learning_stage_exam(
    class_offering_id: int,
    stage_key: str,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生可以删除自己的个人破境试炼")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
    try:
        return delete_personal_stage_exam(class_offering_id, int(user["id"]), stage_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
