from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.learning_progress_service import (
    CultivationWeightValidationError,
    build_student_global_cultivation_profile,
    build_class_learning_overview,
    create_personal_stage_exam,
    delete_personal_stage_exam,
    get_class_cultivation_weight_settings,
    get_material_mastery_check_context,
    list_cultivation_score_events,
    mark_learning_certificate_revealed,
    preview_class_cultivation_weights,
    record_material_learning_progress,
    serialize_student_learning_progress,
    submit_material_mastery_check,
    update_class_cultivation_weights,
)
from ..services.cultivation_alert_service import (
    append_cultivation_alert_support_note,
    build_class_cultivation_alert_context,
    build_cultivation_alert_private_message,
    get_cultivation_alert_for_action,
    handle_cultivation_alert,
    list_cultivation_alerts,
)
from ..services.materials_service import ensure_user_material_access, get_nearest_assignment_anchor
from ..services.message_center_service import create_private_message
from ..services.todo_service import (
    TodoValidationError,
    build_classroom_todo_overview,
    create_manual_todo,
    delete_manual_todo,
    update_manual_todo,
)
from ..services.resource_access_service import ensure_classroom_access as ensure_scoped_classroom_access

router = APIRouter(prefix="/api")


class MaterialProgressPayload(BaseModel):
    material_id: int
    session_id: Optional[int] = None
    duration_seconds: int = 0
    active_seconds: int = 0
    scroll_ratio: float = 0.0
    completed: bool = False
    page_key: str = "material_viewer"


class MaterialMasteryCheckPayload(BaseModel):
    answers: dict[str, str] = {}


class CultivationWeightPayload(BaseModel):
    weights: dict[str, int | float] = {}


class ManualTodoPayload(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    start_at: Optional[str] = None
    due_at: Optional[str] = None
    completed: Optional[bool] = None


class CultivationAlertActionPayload(BaseModel):
    action: str
    note: Optional[str] = ""
    content: Optional[str] = ""
    snooze_days: Optional[int] = 7


def _payload_to_dict(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _ensure_classroom_access(conn, class_offering_id: int, user: dict) -> dict:
    return dict(ensure_scoped_classroom_access(conn, class_offering_id, user))


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


@router.get("/classrooms/{class_offering_id}/learning/weights", response_class=JSONResponse)
async def get_learning_weights(class_offering_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "teacher":
        raise HTTPException(403, "仅教师可查看课堂修为权重")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        return {
            "status": "success",
            "weight_settings": get_class_cultivation_weight_settings(conn, class_offering_id),
        }


@router.post("/classrooms/{class_offering_id}/learning/weights/preview", response_class=JSONResponse)
async def preview_learning_weights(
    class_offering_id: int,
    payload: CultivationWeightPayload,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "teacher":
        raise HTTPException(403, "仅教师可预览课堂修为权重")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        try:
            return preview_class_cultivation_weights(conn, class_offering_id, payload.weights)
        except CultivationWeightValidationError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/classrooms/{class_offering_id}/learning/weights", response_class=JSONResponse)
async def update_learning_weights(
    class_offering_id: int,
    payload: CultivationWeightPayload,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "teacher":
        raise HTTPException(403, "仅教师可调整课堂修为权重")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        try:
            result = update_class_cultivation_weights(
                conn,
                class_offering_id,
                teacher_id=int(user["id"]),
                weights_payload=payload.weights,
            )
        except CultivationWeightValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        return result


@router.get("/classrooms/{class_offering_id}/learning/score-events", response_class=JSONResponse)
async def get_learning_score_events(class_offering_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生可以查看自己的修为流水")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        events = list_cultivation_score_events(conn, class_offering_id, int(user["id"]))
        return {"status": "success", "events": events}


@router.post("/learning/certificates/{certificate_id}/revealed", response_class=JSONResponse)
async def mark_certificate_revealed(certificate_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生可以确认自己的道印揭幕状态")
    with get_db_connection() as conn:
        certificate = mark_learning_certificate_revealed(conn, int(certificate_id), int(user["id"]))
        if not certificate:
            raise HTTPException(404, "道印不存在或不属于当前学生")
        conn.commit()
        return {
            "status": "success",
            "certificate_id": int(certificate_id),
            "revealed_at": certificate.get("revealed_at") or "",
        }


@router.get("/classrooms/{class_offering_id}/learning/alerts", response_class=JSONResponse)
async def get_learning_alerts(class_offering_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "teacher":
        raise HTTPException(403, "仅教师可查看班级修为预警")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        summary = build_class_cultivation_alert_context(conn, class_offering_id)
        alerts = list_cultivation_alerts(conn, class_offering_id, statuses=("active", "snoozed"), limit=80)
        return {"status": "success", "summary": summary, "alerts": alerts}


@router.post("/classrooms/{class_offering_id}/learning/alerts/{alert_id}/actions", response_class=JSONResponse)
async def update_learning_alert(
    class_offering_id: int,
    alert_id: int,
    payload: CultivationAlertActionPayload,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "teacher":
        raise HTTPException(403, "仅教师可处理班级修为预警")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        alert = get_cultivation_alert_for_action(conn, int(alert_id), int(class_offering_id))
        if not alert:
            raise HTTPException(404, "预警不存在或不属于当前课堂")
        normalized_action = str(payload.action or "").strip().lower()
        normalized_action = {
            "message": "private_message",
            "private": "private_message",
            "private_message": "private_message",
            "support_note": "support_note",
            "note": "support_note",
            "handle": "handled",
            "handled": "handled",
            "snooze": "snoozed",
            "snoozed": "snoozed",
        }.get(normalized_action, normalized_action)
        try:
            if normalized_action == "private_message":
                content = build_cultivation_alert_private_message(alert, payload.content or payload.note or "")
                message_result = create_private_message(
                    conn,
                    user,
                    contact_identity=f"student:{int(alert['student_id'])}",
                    class_offering_id=int(class_offering_id),
                    content=content,
                )
                response_message = "已发送关怀私信"
                side_effect = {
                    "type": "private_message",
                    "conversation_key": message_result["conversation_key"],
                    "message": message_result["message_serialized"],
                }
            elif normalized_action == "support_note":
                note = append_cultivation_alert_support_note(
                    conn,
                    alert=alert,
                    teacher_id=int(user["id"]),
                    note=payload.note or "",
                )
                response_message = "已记入教师共享备注"
                side_effect = {"type": "support_note", "note": note}
            else:
                alert = handle_cultivation_alert(
                    conn,
                    alert_id=int(alert_id),
                    teacher_id=int(user["id"]),
                    action=normalized_action,
                    note=payload.note or "",
                    snooze_days=int(payload.snooze_days or 7),
                )
                response_message = "预警状态已更新"
                side_effect = {"type": normalized_action}
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        summary = build_class_cultivation_alert_context(conn, class_offering_id)
        return {
            "status": "success",
            "message": response_message,
            "alert": alert,
            "summary": summary,
            "side_effect": side_effect,
        }


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


@router.get("/classrooms/{class_offering_id}/learning/materials/{material_id}/mastery-check", response_class=JSONResponse)
async def get_material_mastery_check(
    class_offering_id: int,
    material_id: int,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生需要查看心法检验")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        _ensure_material_in_classroom(conn, class_offering_id, int(material_id), user)
        return {
            "status": "success",
            "mastery_check": get_material_mastery_check_context(
                conn,
                class_offering_id=class_offering_id,
                student_id=int(user["id"]),
                material_id=int(material_id),
            ),
        }


@router.post("/classrooms/{class_offering_id}/learning/materials/{material_id}/mastery-check", response_class=JSONResponse)
async def post_material_mastery_check(
    class_offering_id: int,
    material_id: int,
    payload: MaterialMasteryCheckPayload,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "student":
        raise HTTPException(403, "仅学生可以提交心法检验")
    with get_db_connection() as conn:
        _ensure_classroom_access(conn, class_offering_id, user)
        _ensure_material_in_classroom(conn, class_offering_id, int(material_id), user)
        try:
            result = submit_material_mastery_check(
                conn,
                class_offering_id=class_offering_id,
                student_id=int(user["id"]),
                material_id=int(material_id),
                answers=dict(payload.answers or {}),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
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
