"""Teacher and student game repositories, ZIP ingestion and maintenance jobs."""

from __future__ import annotations

import asyncio
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.formparsers import MultiPartException, MultiPartParser

from ..dependencies import require_teacher_domain
from ..services import minigame_packages as packages
from ..services import minigame_platform as platform


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/api/games/v1/admin", dependencies=[Depends(private_response)])
library = require_teacher_domain("library")


async def teacher(request: Request):
    user = await platform.run_db(platform.require_player, request)
    packages.require_teacher(user)
    return library(user)


async def contributor(request: Request):
    user = await platform.run_db(platform.require_player, request)
    return library(user) if user["role"] == "teacher" else user


def query(operation, *args):
    with platform.game_db() as conn:
        return operation(conn, *args)


async def command(request, user, game, action, vid=""):
    platform.check_write(request, user)
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 32_768:
            raise HTTPException(413, "管理命令超出体积限制。")
    try:
        body = json.loads(data) if data else {}
    except (ValueError, UnicodeError):
        raise HTTPException(422, "需要有效的 JSON 对象。")
    result = await platform.run_db(
        packages.mutate, user, game, action, body, request.headers.get("Idempotency-Key", ""), vid
    )
    if action in {"delete","delete-version"}:
        await platform.run_db(confirm_register, user, result["jobId"])
    return result


def confirm_register(user, job_id):
    with platform.game_db() as conn:
        info = dict(packages.one(conn, "mg_admin_jobs", job_id))
        platform.require_manager(conn, user, info["game_id"])
    try:
        with packages.game_lock(info["game_id"]):
            packages.deletion_register(info)
    except (OSError, RuntimeError, HTTPException):
        raise HTTPException(
            503,
            detail={
                "code": "DELETE_REGISTER_PENDING",
                "message": "正在确认删除登记，请用原请求键重试。",
                "jobId": job_id,
            },
            headers={"Retry-After": "2"},
        )


@router.get("/games")
async def games(request: Request, user=Depends(contributor)):
    return await platform.run_db(query, packages.list_games, user, dict(request.query_params))


@router.get("/games/{game_id}")
async def detail(game_id: str, versionOffset: int = Query(0, ge=0), user=Depends(contributor)):
    return await platform.run_db(query, packages.detail, user, game_id, versionOffset)


@router.post("/games", status_code=201)
async def create(request: Request, user=Depends(contributor)):
    return await command(request, user, "", "create")


@router.patch("/games/{game_id}")
async def update(game_id: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "patch")


@router.post("/games/{game_id}/versions", status_code=202)
async def upload(game_id: str, request: Request, user=Depends(contributor)):
    return await upload_form(request,user,game_id)


@router.post("/imports", status_code=202)
async def import_game(request: Request, user=Depends(contributor)):
    return await upload_form(request,user)


async def upload_form(request, user, game_id=None):
    platform.check_write(request, user)
    # Reject unauthorized callers and declared oversized bodies before multipart spooling.
    if game_id:
        await platform.run_db(query, platform.require_manager, user, game_id)
    raw_len = request.headers.get("content-length", "")
    if len(raw_len) > 12 or not raw_len.isascii() or not raw_len.isdigit():
        raise HTTPException(411, "上传游戏包需要 Content-Length。")
    limit = packages.validation.MAX_ZIP + 512 * 1024
    if int(raw_len) > limit:
        raise HTTPException(413, "游戏包超过体积配额。")

    async def limited_stream():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > min(int(raw_len), limit):
                raise MultiPartException("上传数据超过声明长度。")
            yield chunk

    if not request.headers.get("Content-Type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(415, "需要 multipart/form-data 上传。")
    parser = MultiPartParser(request.headers, limited_stream(), max_files=1, max_fields=5, max_part_size=8192)
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        raise HTTPException(422, "上传数据超出限制或 multipart 格式不完整。") from exc
    try:
        file, revision = form.get("file"), form.get("expectedRevision")
        if not hasattr(file, "file") or not str(file.filename).lower().endswith(".zip"):
            raise HTTPException(422, "请选择一个 ZIP 游戏包。")
        if (game_id or revision is not None) and (
            not isinstance(revision, str)
            or not revision.isascii()
            or not revision.isdigit()
            or len(revision) > 12
        ):
            raise HTTPException(422, "缺少有效的游戏修订号。")
        # run_db shields the underlying job on disconnect. Wait before the form closes its spooled file.
        key = request.headers.get("Idempotency-Key","")
        if game_id:
            task = asyncio.create_task(platform.run_db(packages.receive_upload,user,game_id,int(revision),key,file.file))
        else:
            if not set(form.keys()) <= {"file","tags","targetGameId","sourceVersionId","expectedRevision","allowRemix"}:
                raise HTTPException(422,"上传表单包含未知字段。")
            allow_remix = form.get("allowRemix","true")
            if allow_remix not in ("true","false"):
                raise HTTPException(422,"请明确选择是否允许二次开发。")
            options = {"allowRemix":allow_remix=="true"}
            if "tags" in form:
                try:
                    options["tags"] = packages.repository.check_tags(json.loads(form["tags"]))
                except (ValueError, TypeError) as exc:
                    raise HTTPException(422,"标签必须为 JSON 字符串数组。") from exc
            for key_name in ("targetGameId","sourceVersionId"):
                if value := form.get(key_name):
                    if not isinstance(value,str) or not re.fullmatch("[a-f0-9]{32}",value):
                        raise HTTPException(422,"游戏或源版本编号无效。")
                    options[key_name]=value
            if revision is not None:
                options["expectedRevision"]=int(revision)
            task = asyncio.create_task(platform.run_db(packages.import_package,user,key,file.file,options))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    finally:
        await form.close()


def version_reader(conn, user, game_id, version_id):
    from ..services import minigame_moderation as moderation
    platform.require_manager(conn, user, game_id)
    row = conn.execute(packages.VERSION_WITH_AUTHOR + " WHERE v.game_id=? AND v.id=? AND v.status<>'deleted'",
                       (game_id,version_id)).fetchone()
    if not row:
        raise HTTPException(404, "版本不存在。")
    info = packages.version_info(row)
    info["review"] = moderation.get_summary(conn,version_id)
    return {"version": info}


@router.get("/games/{game_id}/versions/{vid}")
async def version(game_id: str, vid: str, user=Depends(contributor)):
    return await platform.run_db(query, version_reader, user, game_id, vid)


def job_reader(conn, user, job_id):
    job = packages.one(conn, "mg_admin_jobs", job_id)
    platform.require_manager(conn, user, job["game_id"])
    return packages.job_info(job)


@router.get("/jobs/{job_id}")
async def job(job_id: str, user=Depends(contributor)):
    return await platform.run_db(query, job_reader, user, job_id)


@router.get("/games/{game_id}/versions/{vid}/package")
async def managed_package(game_id: str, vid: str, user=Depends(contributor)):
    return await platform.run_db(packages.download_managed_version, user, game_id, vid)


@router.post("/games/{game_id}/versions/{vid}/preview", status_code=201)
async def preview(game_id: str, vid: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "preview", vid)


@router.post("/games/{game_id}/versions/{vid}/playtests", status_code=201)
async def playtest(game_id: str, vid: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "playtest", vid)


@router.post("/games/{game_id}/versions/{vid}/retire")
async def retire(game_id: str, vid: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "retire", vid)


@router.post("/games/{game_id}/versions/{vid}/submit-review")
async def submit_review(game_id: str, vid: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "submit-review", vid)


@router.post("/games/{game_id}/publish")
async def publish(game_id: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "publish")


@router.post("/games/{game_id}/unlist")
async def unlist(game_id: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "unlist")


@router.post("/games/{game_id}/deletion-plans")
async def plan(game_id: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "plan")


@router.post("/games/{game_id}/suggestions", status_code=202)
async def suggestions(game_id: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "suggest")


@router.delete("/games/{game_id}", status_code=202)
async def delete(game_id: str, request: Request, user=Depends(contributor)):
    return await command(request, user, game_id, "delete")


@router.patch("/games/{game_id}/versions/{vid}/license")
async def source_license(game_id: str,vid: str,request: Request,user=Depends(contributor)):
    return await command(request,user,game_id,"license",vid)


@router.delete("/games/{game_id}/versions/{vid}", status_code=202)
async def delete_version(game_id: str,vid: str,request: Request,user=Depends(contributor)):
    return await command(request,user,game_id,"delete-version",vid)
