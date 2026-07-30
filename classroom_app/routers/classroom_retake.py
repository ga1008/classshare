"""重修/插班生管理 HTTP API（课堂班级名单处）。

四个端点，都只对本课堂授课教师（或超管）开放：

* ``GET  /api/classroom/{id}/retake-students``          —— 名单 + 建议 + 确认状态
* ``POST /api/classroom/{id}/retake-students/detect``   —— AI 按学号前缀识别候选（仅建议）
* ``POST /api/classroom/{id}/retake-students/confirm``  —— 教师敲定（默认平时分默认 70），
  自动回填历史缺交默认分，并触发本课堂已生成的平时/考核材料一键更新
* ``POST /api/classroom/{id}/retake-students/revoke``   —— 撤销，按普通学生处理
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.classroom_retake_service import (
    confirm_retake_student,
    detect_retake_candidates,
    list_retake_students,
    revoke_retake_student,
)
from ..services.resource_access_service import is_super_admin_teacher

router = APIRouter(prefix="/api/classroom")


class RetakeConfirmRequest(BaseModel):
    student_id: int
    default_score: float | None = Field(default=None, ge=0, le=100)


class RetakeRevokeRequest(BaseModel):
    student_id: int


def _ensure_offering_access(conn, class_offering_id: int, user: dict) -> None:
    owns = conn.execute(
        "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
        (int(class_offering_id), int(user["id"])),
    ).fetchone()
    if not owns and not is_super_admin_teacher(conn, int(user["id"])):
        raise HTTPException(403, "无权访问该课堂")


@router.get("/{class_offering_id}/retake-students", response_class=JSONResponse)
async def list_classroom_retake_students(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, class_offering_id, user)
        items = list_retake_students(conn, class_offering_id=int(class_offering_id))
    return {"status": "success", "items": items}


@router.post("/{class_offering_id}/retake-students/detect", response_class=JSONResponse)
async def detect_classroom_retake_students(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, class_offering_id, user)
        detection = detect_retake_candidates(conn, class_offering_id=int(class_offering_id))
        items = list_retake_students(conn, class_offering_id=int(class_offering_id))
    message = (
        f"AI 已按学号前缀核对全班 {detection['roster_count']} 人，"
        f"识别出 {len(detection['suggestions'])} 名疑似重修/插班学生，请逐个确认。"
        if detection["detectable"]
        else "班级人数过少或学号前缀过于分散，AI 暂无法可靠识别，请手动指定。"
    )
    return {"status": "success", "detection": detection, "items": items, "message": message}


@router.post("/{class_offering_id}/retake-students/confirm", response_class=JSONResponse)
async def confirm_classroom_retake_student(
    class_offering_id: int,
    payload: RetakeConfirmRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, class_offering_id, user)
        confirmation = confirm_retake_student(
            conn,
            class_offering_id=int(class_offering_id),
            student_id=int(payload.student_id),
            teacher_id=int(user["id"]),
            default_score=payload.default_score,
        )

    # 已生成的平时成绩表/考核登分表自动更新一次；单份失败不阻塞确认。
    from .materials_parts.final_materials import refresh_offering_grade_record_materials

    material_refresh: list[dict[str, Any]] = []
    try:
        material_refresh = await refresh_offering_grade_record_materials(
            int(class_offering_id),
            user,
        )
    except Exception:
        material_refresh = [{"status": "failed", "message": "材料自动更新暂时失败，可稍后在材料页手动一键更新。"}]

    refreshed = sum(1 for item in material_refresh if item.get("status") == "success")
    failed = sum(1 for item in material_refresh if item.get("status") == "failed")
    backfill = confirmation.get("backfill") or {}
    message_parts = [
        f"已确认 {confirmation['student_name']}（{confirmation['student_number']}）为重修/插班学生，"
        f"默认平时分 {confirmation['default_ordinary_score']:g} 分。",
        f"历史已截止任务补记默认分 {backfill.get('created_count', 0)} 条。"
        if backfill.get("created_count")
        else "历史任务无需补分。",
    ]
    if material_refresh:
        message_parts.append(
            f"已自动更新 {refreshed} 份成绩材料" + (f"，{failed} 份失败（可稍后手动一键更新）" if failed else "") + "。"
        )
    with get_db_connection() as conn:
        items = list_retake_students(conn, class_offering_id=int(class_offering_id))
    return {
        "status": "success",
        "message": "".join(message_parts),
        "confirmation": confirmation,
        "material_refresh": material_refresh,
        "items": items,
    }


@router.post("/{class_offering_id}/retake-students/revoke", response_class=JSONResponse)
async def revoke_classroom_retake_student(
    class_offering_id: int,
    payload: RetakeRevokeRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, class_offering_id, user)
        result = revoke_retake_student(
            conn,
            class_offering_id=int(class_offering_id),
            student_id=int(payload.student_id),
            teacher_id=int(user["id"]),
        )
        items = list_retake_students(conn, class_offering_id=int(class_offering_id))
    return {
        "status": "success",
        "message": "已撤销重修/插班标记，该学生此后按普通学生处理；已写入的默认分占位保留，可在作业页逐条调整。",
        "result": result,
        "items": items,
    }
