from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..config import MAX_UPLOAD_SIZE_BYTES
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.collaboration_service import (
    add_group_file,
    add_group_member,
    create_group,
    create_group_scheme,
    create_group_submission_blog_draft,
    join_group,
    leave_group,
    list_group_chat,
    load_collaboration_snapshot,
    nominate_group_leader,
    post_group_chat,
    random_join_scheme,
    remove_group_member,
    resolve_group_file_download,
    set_group_goal_progress,
    submit_peer_review,
    teacher_assign_to_scheme_group,
    update_group,
    upsert_group_submission,
)
from ..services.file_service import save_file_globally


router = APIRouter(prefix="/api/collaboration")
GROUP_FILE_MAX_BYTES = min(MAX_UPLOAD_SIZE_BYTES, 100 * 1024 * 1024)


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


def _measure_upload(file: UploadFile) -> int:
    file.file.seek(0, 2)
    size = int(file.file.tell())
    file.file.seek(0)
    return size


def _group_class_offering_id(conn, group_id: int) -> int:
    row = conn.execute(
        "SELECT class_offering_id FROM study_groups WHERE id = ? LIMIT 1",
        (int(group_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "小组不存在")
    return int(row["class_offering_id"])


@router.get("/classrooms/{class_offering_id}/snapshot", response_class=JSONResponse)
async def collaboration_snapshot(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        snapshot = load_collaboration_snapshot(conn, class_offering_id, user)
    return {"status": "ok", "snapshot": snapshot}


def _scheme_class_offering_id(conn, scheme_id: int) -> int:
    row = conn.execute(
        "SELECT class_offering_id FROM group_schemes WHERE id = ? LIMIT 1",
        (int(scheme_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "分组方案不存在")
    return int(row["class_offering_id"])


@router.post("/classrooms/{class_offering_id}/schemes", response_class=JSONResponse)
async def create_random_group_scheme(class_offering_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        scheme = create_group_scheme(conn, class_offering_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, class_offering_id, user)
        conn.commit()
    return {"status": "ok", "message": "分组方案已创建", "scheme": scheme, "snapshot": snapshot}


@router.post("/schemes/{scheme_id}/random-join", response_class=JSONResponse)
async def random_join_group_scheme(scheme_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        result = random_join_scheme(conn, scheme_id, user)
        snapshot = load_collaboration_snapshot(conn, _scheme_class_offering_id(conn, scheme_id), user)
        conn.commit()
    return {
        "status": "ok",
        "message": f"已加入「{result['group_name']}」",
        "result": result,
        "snapshot": snapshot,
    }


@router.get("/groups/{group_id}/chat", response_class=JSONResponse)
async def get_group_chat(group_id: int, after_id: int = 0, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        payload = list_group_chat(conn, group_id, user, after_id)
    return {"status": "ok", **payload}


@router.post("/groups/{group_id}/chat", response_class=JSONResponse)
async def send_group_chat(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        message = post_group_chat(conn, group_id, user, payload.get("content"))
        conn.commit()
    return {"status": "ok", "message": message}


@router.put("/groups/{group_id}/goal", response_class=JSONResponse)
async def set_study_group_goal(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        set_group_goal_progress(conn, group_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {"status": "ok", "message": "小组目标与进度已更新", "snapshot": snapshot}


@router.post("/groups/{group_id}/scheme-assign", response_class=JSONResponse)
async def assign_scheme_group_member(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    student = payload.get("student_id")
    try:
        student_id = int(student)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要分配的学生") from exc
    with get_db_connection() as conn:
        teacher_assign_to_scheme_group(conn, group_id, user, student_id)
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {"status": "ok", "message": "已分配到小组", "snapshot": snapshot}


@router.post("/groups/{group_id}/nominate-leader", response_class=JSONResponse)
async def nominate_study_group_leader(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    candidate = payload.get("candidate_student_id")
    try:
        candidate_id = int(candidate)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要举荐的组员") from exc
    with get_db_connection() as conn:
        nominate_group_leader(conn, group_id, user, candidate_id)
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {"status": "ok", "message": "已举荐组长", "snapshot": snapshot}


@router.post("/classrooms/{class_offering_id}/groups", response_class=JSONResponse)
async def create_study_group(class_offering_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        group = create_group(conn, class_offering_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, class_offering_id, user)
        conn.commit()
    return {
        "status": "ok",
        "message": "小组已创建",
        "group": group,
        "snapshot": snapshot,
    }


@router.put("/groups/{group_id}", response_class=JSONResponse)
async def update_study_group(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        group = update_group(conn, group_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "小组信息已更新",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/join", response_class=JSONResponse)
async def join_study_group(group_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = join_group(conn, group_id, user)
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "已加入小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/leave", response_class=JSONResponse)
async def leave_study_group(group_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = leave_group(conn, group_id, user)
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "已退出小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/members", response_class=JSONResponse)
async def add_study_group_member(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    student_id = payload.get("student_id")
    try:
        normalized_student_id = int(student_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要加入小组的学生") from exc
    with get_db_connection() as conn:
        group = add_group_member(conn, group_id, user, normalized_student_id)
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "成员已加入小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.delete("/groups/{group_id}/members/{student_id}", response_class=JSONResponse)
async def remove_study_group_member(group_id: int, student_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = remove_group_member(conn, group_id, user, student_id)
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "成员已移出小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/files", response_class=JSONResponse)
async def upload_study_group_file(
    group_id: int,
    file: UploadFile = File(...),
    description: str = Form(""),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(400, "请选择要上传的文件")
    size = _measure_upload(file)
    if size <= 0:
        raise HTTPException(400, "不能上传空文件")
    if size > GROUP_FILE_MAX_BYTES:
        raise HTTPException(400, "组内文件单个不能超过 100MB")

    storage = await save_file_globally(file)
    if not storage:
        raise HTTPException(500, "文件保存失败")

    with get_db_connection() as conn:
        group_file = add_group_file(
            conn,
            group_id,
            user,
            file_hash=str(storage["hash"]),
            original_filename=str(file.filename),
            mime_type=str(file.content_type or ""),
            file_size=int(storage.get("size") or size),
            description=description,
        )
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "组内文件已上传",
        "file": group_file,
        "snapshot": snapshot,
    }


@router.get("/files/{file_id}/download")
async def download_study_group_file(file_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        payload = resolve_group_file_download(conn, file_id, user)
    return FileResponse(
        payload["path"],
        media_type=payload["mime_type"],
        filename=payload["filename"],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(payload['filename'])}"
        },
    )


@router.put("/groups/{group_id}/submission", response_class=JSONResponse)
async def save_group_submission(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        submission = upsert_group_submission(conn, group_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "小组成果已提交",
        "submission": submission,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/blog-draft", response_class=JSONResponse)
async def create_submission_blog_draft(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        blog_post = create_group_submission_blog_draft(conn, group_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "小组成果博客草稿已生成",
        "blog_post": blog_post,
        "blog_url": blog_post["url"],
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/peer-reviews", response_class=JSONResponse)
async def save_peer_review(group_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        review = submit_peer_review(conn, group_id, user, payload)
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        conn.commit()
    return {
        "status": "ok",
        "message": "同伴互评已保存",
        "review": review,
        "snapshot": snapshot,
    }
