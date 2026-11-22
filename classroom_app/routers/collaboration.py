from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from functools import partial, wraps
from typing import Any
from urllib.parse import quote
from weakref import WeakValueDictionary

import anyio
from anyio.lowlevel import RunVar
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute

from ..config import MAX_UPLOAD_SIZE_BYTES, POSTGRES_POOL_MAX
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.collaboration_service import (
    GROUP_CHAT_ATTACHMENT_MAX_BYTES,
    MAX_GROUP_CHAT_ATTACHMENTS,
    add_group_chat_attachment,
    add_group_file,
    add_group_member,
    assign_scheme_leader,
    auto_assign_scheme_leaders,
    close_group_scheme,
    redistribute_scheme_groups,
    create_group,
    create_group_scheme_record,
    create_group_submission_blog_draft,
    create_student_group,
    ensure_group_chat_writable,
    fill_scheme_groups,
    invite_to_group,
    join_group,
    leave_group,
    list_group_chat,
    load_collaboration_snapshot,
    load_invite_candidates,
    load_member_public_card,
    nominate_group_leader,
    update_invitation_response,
    post_group_chat,
    join_scheme_member,
    scheme_from_snapshot,
    recall_group_chat_message,
    remove_group_member,
    resolve_group_chat_attachment_download,
    resolve_group_file_download,
    set_group_goal_progress,
    set_scheme_admission,
    submit_peer_review,
    teacher_assign_to_scheme_group,
    update_group,
    upsert_group_submission,
)
from ..services.file_service import save_file_globally
from ..services.submission_file_safety import (
    SAFE_INLINE_IMAGE_MIME_TYPES,
    trusted_submission_mime_type,
    untrusted_file_headers,
)


GROUP_FILE_MAX_BYTES = min(MAX_UPLOAD_SIZE_BYTES, 100 * 1024 * 1024)
_work_state = RunVar("collaboration_work_state")


class _CollaborationRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def prepare_response(request: Request):
            # Release admissions once the Response exists, before a slow client downloads it.
            async with AsyncExitStack() as slots:
                request.state.collaboration_slots = slots
                return await handle(request)

        return prepare_response


async def _admit_collaboration_request(request: Request):
    # Dependencies run after multipart upload parsing, before student authentication and SQL.
    state = _work_state.get(None)
    if state is None:
        state = (anyio.CapacityLimiter(max(1, min(4, POSTGRES_POOL_MAX // 2))), WeakValueDictionary())
        _work_state.set(state)
    capacity, locks = state
    slots = request.state.collaboration_slots
    if request.method not in {"GET", "HEAD"} and request.path_params:
        # Serialise common URLs locally; the DB owns correctness across workers and aliases.
        key = next(iter(request.path_params.items()))
        lock = locks.get(key)
        if lock is None:
            locks[key] = lock = asyncio.Lock()
        await slots.enter_async_context(lock)
    await slots.enter_async_context(capacity)


router = APIRouter(prefix="/api/collaboration", route_class=_CollaborationRoute,
                   dependencies=[Depends(_admit_collaboration_request)])


def _collaboration_work(endpoint):
    """Run the synchronous DB transaction and JSON encoding together off the event loop."""
    @wraps(endpoint)
    def dispatch(*args, **kwargs):
        result = endpoint(*args, **kwargs)
        return JSONResponse(jsonable_encoder(result)) if isinstance(result, dict) else result

    return dispatch


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


def _safe_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
@_collaboration_work
def collaboration_snapshot(class_offering_id: int, user: dict = Depends(get_current_user)):
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
@_collaboration_work
def create_random_group_scheme(class_offering_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        scheme = create_group_scheme_record(conn, class_offering_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, class_offering_id, user)
        scheme = scheme_from_snapshot(conn, snapshot, scheme, user)
    return {"status": "ok", "message": "分组方案已创建", "scheme": scheme, "snapshot": snapshot}


@router.post("/schemes/{scheme_id}/fill", response_class=JSONResponse)
@_collaboration_work
def fill_random_group_scheme(scheme_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        count = fill_scheme_groups(conn, scheme_id, user)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _scheme_class_offering_id(conn, scheme_id), user)
    return {"status": "ok", "message": f"已随机分配 {count} 名学生" if count else "参与学生均已分组", "snapshot": snapshot}


@router.post("/schemes/{scheme_id}/random-join", response_class=JSONResponse)
@_collaboration_work
def random_join_group_scheme(scheme_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        result = join_scheme_member(conn, scheme_id, user)
        conn.commit()
        scheme = result["scheme"]
        snapshot = load_collaboration_snapshot(conn, int(scheme["class_offering_id"]), user)
        result["scheme"] = scheme_from_snapshot(conn, snapshot, scheme, user)
    return {
        "status": "ok",
        "message": f"已加入「{result['group_name']}」",
        "result": result,
        "snapshot": snapshot,
    }


@router.get("/groups/{group_id}/chat", response_class=JSONResponse)
@_collaboration_work
def get_group_chat(group_id: int, after_id: int = 0, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        payload = list_group_chat(conn, group_id, user, after_id)
    return {"status": "ok", **payload}


@router.post("/groups/{group_id}/chat", response_class=JSONResponse)
@_collaboration_work
def send_group_chat(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    raw_ids = payload.get("attachment_ids") or []
    attachment_ids: list[int] = []
    if isinstance(raw_ids, list):
        for value in raw_ids[:MAX_GROUP_CHAT_ATTACHMENTS]:
            parsed = _safe_int_or_none(value)
            if parsed is not None:
                attachment_ids.append(parsed)
    with get_db_connection() as conn:
        message = post_group_chat(
            conn,
            group_id,
            user,
            payload.get("content"),
            attachment_ids=attachment_ids,
            message_type=str(payload.get("message_type") or "text"),
            sticker_emoji_id=_safe_int_or_none(payload.get("sticker_emoji_id")),
        )
        conn.commit()
    return {"status": "ok", "message": message}


@router.post("/groups/{group_id}/chat/attachments", response_class=JSONResponse)
async def upload_group_chat_attachments(
    group_id: int,
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    if not files:
        raise HTTPException(400, "请选择要上传的文件")
    if len(files) > MAX_GROUP_CHAT_ATTACHMENTS:
        raise HTTPException(400, f"单条消息最多上传 {MAX_GROUP_CHAT_ATTACHMENTS} 个附件")
    def check_writable():
        with get_db_connection() as conn:
            ensure_group_chat_writable(conn, group_id, user)

    def record_attachment(file, storage, measured_size):
        with get_db_connection() as conn:
            payload = add_group_chat_attachment(
                conn, group_id, user, file_hash=str(storage["hash"]),
                original_filename=str(file.filename),
                mime_type=trusted_submission_mime_type(str(file.filename or "")),
                file_size=int(storage.get("size") or measured_size),
            )
            conn.commit()
        return payload

    await anyio.to_thread.run_sync(check_writable)
    payloads = []
    for file in files:
        if not file.filename:
            continue
        size = _measure_upload(file)
        if size <= 0:
            raise HTTPException(400, "不能上传空文件")
        if size > GROUP_CHAT_ATTACHMENT_MAX_BYTES:
            raise HTTPException(400, "单个附件不能超过 50MB")
        storage = await save_file_globally(file)
        if not storage:
            raise HTTPException(500, "文件保存失败")
        payload = await anyio.to_thread.run_sync(partial(record_attachment, file, storage, size))
        payloads.append(payload)
    return {"status": "ok", "attachments": payloads}


@router.post("/groups/{group_id}/chat/{message_id}/recall", response_class=JSONResponse)
@_collaboration_work
def recall_group_chat(group_id: int, message_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        result = recall_group_chat_message(conn, group_id, message_id, user)
        conn.commit()
    return {"status": "ok", "result": result}


@router.get("/classrooms/{class_offering_id}/member-card/{student_id}", response_class=JSONResponse)
@_collaboration_work
def get_member_public_card(class_offering_id: int, student_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        card = load_member_public_card(conn, class_offering_id, student_id, user)
    return {"status": "ok", "card": card}


@router.get("/groups/{group_id}/chat/attachments/{attachment_id}")
@_collaboration_work
def download_group_chat_attachment(
    group_id: int,
    attachment_id: int,
    download: bool = False,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        payload = resolve_group_chat_attachment_download(conn, attachment_id, user)
    # Type comes from the file name (stored types may be client-declared on
    # older rows); only raster images may render inline on our origin.
    media_type = trusted_submission_mime_type(str(payload["filename"] or ""))
    inline_ok = not download and media_type in SAFE_INLINE_IMAGE_MIME_TYPES
    disposition = "inline" if inline_ok else "attachment"
    return FileResponse(
        payload["path"],
        media_type=media_type,
        filename=payload["filename"],
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(payload['filename'])}",
            **untrusted_file_headers(),
        },
    )


@router.put("/schemes/{scheme_id}/admission")
@_collaboration_work
def change_scheme_admission(scheme_id: int, payload: dict, user: dict = Depends(get_current_user)):
    is_open = payload.get("open")
    if not isinstance(is_open, bool):
        raise HTTPException(400, "请选择开启或暂停学生抽组")
    with get_db_connection() as conn:
        set_scheme_admission(conn, scheme_id, user, is_open)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _scheme_class_offering_id(conn, scheme_id), user)
    return {"status": "ok", "message": "学生抽组已开启" if is_open else "学生抽组已暂停", "snapshot": snapshot}


@router.post("/schemes/{scheme_id}/close", response_class=JSONResponse)
@_collaboration_work
def close_random_group_scheme(scheme_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        close_group_scheme(conn, scheme_id, user)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _scheme_class_offering_id(conn, scheme_id), user)
    return {"status": "ok", "message": "分组方案已结束并归档", "snapshot": snapshot}


@router.post("/schemes/{scheme_id}/auto-leaders", response_class=JSONResponse)
@_collaboration_work
def auto_assign_random_scheme_leaders(scheme_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        result = auto_assign_scheme_leaders(conn, scheme_id, user)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _scheme_class_offering_id(conn, scheme_id), user)
    return {
        "status": "ok",
        "message": f"已为 {result['assigned']} 个小组自动配置组长",
        "snapshot": snapshot,
    }


@router.post("/schemes/{scheme_id}/redistribute", response_class=JSONResponse)
@_collaboration_work
def redistribute_random_scheme_groups(scheme_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        redistribute_scheme_groups(conn, scheme_id, user)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _scheme_class_offering_id(conn, scheme_id), user)
    return {"status": "ok", "message": "各组人数已达标，组数和原有组长已保留", "snapshot": snapshot}


@router.post("/groups/{group_id}/assign-leader", response_class=JSONResponse)
@_collaboration_work
def teacher_assign_group_leader(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    candidate = payload.get("candidate_student_id")
    try:
        candidate_id = int(candidate)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要指定的组员") from exc
    with get_db_connection() as conn:
        assign_scheme_leader(conn, group_id, user, candidate_id)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {"status": "ok", "message": "已指定组长", "snapshot": snapshot}


@router.put("/groups/{group_id}/goal", response_class=JSONResponse)
@_collaboration_work
def set_study_group_goal(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        set_group_goal_progress(conn, group_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {"status": "ok", "message": "小组目标与进度已更新", "snapshot": snapshot}


@router.post("/groups/{group_id}/scheme-assign", response_class=JSONResponse)
@_collaboration_work
def assign_scheme_group_member(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    student = payload.get("student_id")
    try:
        student_id = int(student)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要分配的学生") from exc
    with get_db_connection() as conn:
        teacher_assign_to_scheme_group(conn, group_id, user, student_id)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {"status": "ok", "message": "已分配到小组", "snapshot": snapshot}


@router.post("/groups/{group_id}/nominate-leader", response_class=JSONResponse)
@_collaboration_work
def nominate_study_group_leader(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    candidate = payload.get("candidate_student_id")
    try:
        candidate_id = int(candidate)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要举荐的组员") from exc
    with get_db_connection() as conn:
        nominate_group_leader(conn, group_id, user, candidate_id)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {"status": "ok", "message": "已举荐组长", "snapshot": snapshot}


@router.post("/classrooms/{class_offering_id}/student-groups", response_class=JSONResponse)
@_collaboration_work
def create_student_invite_group(class_offering_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = create_student_group(conn, class_offering_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, class_offering_id, user)
    return {"status": "ok", "message": "分组已发起，邀请已发送", "group": group, "snapshot": snapshot}


@router.get("/classrooms/{class_offering_id}/invite-candidates", response_class=JSONResponse)
@_collaboration_work
def get_invite_candidates(class_offering_id: int, group_id: int | None = None, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        candidates = load_invite_candidates(conn, class_offering_id, user, group_id)
    return {"status": "ok", "candidates": candidates}


@router.post("/groups/{group_id}/invite", response_class=JSONResponse)
@_collaboration_work
def invite_group_members(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = invite_to_group(conn, group_id, user, payload.get("invitee_student_ids") or [])
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {"status": "ok", "message": "邀请已发送", "group": group, "snapshot": snapshot}


@router.post("/invitations/{invitation_id}/accept", response_class=JSONResponse)
@_collaboration_work
def accept_group_invitation(invitation_id: int, user: dict = Depends(get_current_user)):
    return _invitation_response(invitation_id, user, accept=True)


@router.post("/invitations/{invitation_id}/decline", response_class=JSONResponse)
@_collaboration_work
def decline_group_invitation(invitation_id: int, user: dict = Depends(get_current_user)):
    return _invitation_response(invitation_id, user, accept=False)


def _invitation_response(invitation_id, user, *, accept):
    with get_db_connection() as conn:
        classroom_id, message = update_invitation_response(conn, invitation_id, user, accept=accept)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, classroom_id, user)
    return {"status": "ok", "message": message, "snapshot": snapshot}


@router.post("/classrooms/{class_offering_id}/groups", response_class=JSONResponse)
@_collaboration_work
def create_study_group(class_offering_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = create_group(conn, class_offering_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, class_offering_id, user)
    return {
        "status": "ok",
        "message": "小组已创建",
        "group": group,
        "snapshot": snapshot,
    }


@router.put("/groups/{group_id}", response_class=JSONResponse)
@_collaboration_work
def update_study_group(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = update_group(conn, group_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
    return {
        "status": "ok",
        "message": "小组信息已更新",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/join", response_class=JSONResponse)
@_collaboration_work
def join_study_group(group_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = join_group(conn, group_id, user)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
    return {
        "status": "ok",
        "message": "已加入小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/leave", response_class=JSONResponse)
@_collaboration_work
def leave_study_group(group_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = leave_group(conn, group_id, user)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
    return {
        "status": "ok",
        "message": "已退出小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/members", response_class=JSONResponse)
@_collaboration_work
def add_study_group_member(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    student_id = payload.get("student_id")
    try:
        normalized_student_id = int(student_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择要加入小组的学生") from exc
    with get_db_connection() as conn:
        group = add_group_member(conn, group_id, user, normalized_student_id)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
    return {
        "status": "ok",
        "message": "成员已加入小组",
        "group": group,
        "snapshot": snapshot,
    }


@router.delete("/groups/{group_id}/members/{student_id}", response_class=JSONResponse)
@_collaboration_work
def remove_study_group_member(group_id: int, student_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        group = remove_group_member(conn, group_id, user, student_id)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, int(group["class_offering_id"]), user)
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
    from ..services.collaboration_service import _ensure_group_access, _can_access_group_work
    def check_access():
        with get_db_connection() as conn:
            group = _ensure_group_access(conn, group_id, user)
            if not _can_access_group_work(conn, group, user):
                raise HTTPException(403, '只有小组成员或教师可以上传组内文件')

    await anyio.to_thread.run_sync(check_access)
    size = _measure_upload(file)
    if size <= 0:
        raise HTTPException(400, "不能上传空文件")
    if size > GROUP_FILE_MAX_BYTES:
        raise HTTPException(400, "组内文件单个不能超过 100MB")

    storage = await save_file_globally(file)
    if not storage:
        raise HTTPException(500, "文件保存失败")

    def save_record():
        with get_db_connection() as conn:
            group_file = add_group_file(
                conn, group_id, user, file_hash=str(storage["hash"]),
                original_filename=str(file.filename), mime_type=str(file.content_type or ""),
                file_size=int(storage.get("size") or size), description=description,
            )
            conn.commit()
            snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
        return JSONResponse(jsonable_encoder({
            "status": "ok", "message": "组内文件已上传", "file": group_file, "snapshot": snapshot,
        }))

    return await anyio.to_thread.run_sync(save_record)


@router.get("/files/{file_id}/download")
@_collaboration_work
def download_study_group_file(file_id: int, user: dict = Depends(get_current_user)):
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
@_collaboration_work
def save_group_submission(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        submission = upsert_group_submission(conn, group_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {
        "status": "ok",
        "message": "小组成果已提交",
        "submission": submission,
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/blog-draft", response_class=JSONResponse)
@_collaboration_work
def create_submission_blog_draft(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        blog_post = create_group_submission_blog_draft(conn, group_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {
        "status": "ok",
        "message": "小组成果博客草稿已生成",
        "blog_post": blog_post,
        "blog_url": blog_post["url"],
        "snapshot": snapshot,
    }


@router.post("/groups/{group_id}/peer-reviews", response_class=JSONResponse)
@_collaboration_work
def save_peer_review(group_id: int, payload: dict = Depends(_json_payload), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        review = submit_peer_review(conn, group_id, user, payload)
        conn.commit()
        snapshot = load_collaboration_snapshot(conn, _group_class_offering_id(conn, group_id), user)
    return {
        "status": "ok",
        "message": "同伴互评已保存",
        "review": review,
        "snapshot": snapshot,
    }
