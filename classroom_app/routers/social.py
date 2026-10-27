"""好友、群组与统一在线 — REST API (docs/social-friends-groups-2026-10-27.md §7).

Every route needs a logged-in user; errors are ``{detail}``. Database work runs
in a worker thread on its own connection (commit on success, rollback on
error); push events collected by the services are delivered after commit.
The websocket lives in ``social_ws.py``; the internal push endpoint for the
scheduler process is at the bottom of this file.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

import asyncio
import os
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import (
    social_ai_search_service as ai_search_service,
    social_card_service as card_service,
    social_conversation_service as conversations,
    social_friend_service as friends,
    social_group_service as groups,
    social_identity_service as identities,
    social_media_service as media,
    social_message_service as messages,
    social_notification_service as notifications,
    social_push_service,
    social_recommendation_service as recommendation_service,
    social_scope_service as scope_service,
)
from ..services.presence_service import presence_map
from ..services.rate_limit_service import RateLimitExceededError
from ..services.social_common import SocialError, ensure_schema, normalize_identity, run_db, user_identity
from ..services.submission_file_safety import OCTET_STREAM, SAFE_INLINE_IMAGE_MIME_TYPES, untrusted_file_headers

router = APIRouter(prefix="/api/social")
internal_router = APIRouter()

MAX_PRESENCE_QUERY = 200
DOWNLOAD_CHUNK = 64 * 1024
INTERNAL_PUSH_MAX_TARGETS = 2000
_MIB = 1024 * 1024


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RateLimitExceededError):
        return HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after_seconds)})
    headers = {"Retry-After": str(exc.retry_after)} if getattr(exc, "retry_after", 0) else None
    return HTTPException(status_code=getattr(exc, "status_code", 400), detail=getattr(exc, "detail", str(exc)),
                         headers=headers)


def _is_unique_violation(exc: Exception) -> bool:
    names = {cls.__name__ for cls in type(exc).__mro__}
    return bool(names & {"IntegrityError", "UniqueViolation"})


def _transaction(work: Callable[[Any, list], Any], events: list) -> Any:
    with get_db_connection() as conn:
        try:
            ensure_schema(conn)
            result = work(conn, events)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


async def _run(work: Callable[[Any, list], Any]) -> Any:
    """Run ``work(conn, events)`` in a thread, commit, then deliver its push events."""
    events: list = []
    try:
        result = await run_db(_transaction, work, events)
    except (SocialError, RateLimitExceededError) as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        if _is_unique_violation(exc):  # e.g. two concurrent sends with one client_nonce
            raise HTTPException(status_code=409, detail="请求重复提交，请刷新后重试。") from exc
        raise
    try:
        await social_push_service.push_events(events)
    except Exception as exc:  # noqa: BLE001 - the write is committed; a failed push only delays the UI
        print(f"[SOCIAL] push after commit failed: {exc!r}")
    return result


def _field(data: Any, name: str, default: Any = None) -> Any:
    return data.get(name, default) if isinstance(data, dict) else default


# ---------------------------------------------------------------------------
# Friends, users, blocks
# ---------------------------------------------------------------------------

@router.get("/friends")
async def social_list_friends(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.list_friends(conn, user))


@router.get("/friends/requests")
async def social_list_friend_requests(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.list_requests(conn, user))


@router.post("/friends/requests")
async def social_send_friend_request(data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.send_request(
        conn, user, _field(data, "target_identity"), _field(data, "message", ""), events))


@router.post("/friends/requests/{request_id}/accept")
async def social_accept_friend_request(request_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.accept_request(conn, user, request_id, events))


@router.post("/friends/requests/{request_id}/decline")
async def social_decline_friend_request(request_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.decline_request(conn, user, request_id, events))


@router.delete("/friends/requests/{request_id}")
async def social_withdraw_friend_request(request_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.withdraw_request(conn, user, request_id, events))


@router.delete("/friends/{identity}")
async def social_delete_friend(identity: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.delete_friend(conn, user, identity, events))


@router.patch("/friends/{identity}")
async def social_update_friend(identity: str, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.set_remark(conn, user, identity, _field(data, "remark", "")))


@router.get("/scope")
async def social_scope(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: scope_service.scope_payload(conn, user))


@router.get("/recommendations")
async def social_recommendations(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: recommendation_service.recommendations_payload(conn, user))


@router.get("/users/search")
async def social_search_users(q: str = Query(default=""), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: identities.search_payload(conn, user, q))


@router.get("/users/suggestions")
async def social_user_suggestions(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: {"items": identities.suggest_users(conn, user)})


@router.post("/users/ai-search")
async def social_ai_search_users(data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    try:
        return await ai_search_service.ai_search(user, _field(data, "prompt", ""))
    except SocialError as exc:
        raise _http_error(exc) from exc


@router.get("/users/{identity}/profile")
async def social_user_profile(identity: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: identities.profile_sidebar(conn, user, identity))


@router.get("/users/{identity}/card")
async def social_user_card(identity: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: card_service.user_card(conn, user, identity))


@router.get("/blocks")
async def social_list_blocks(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: {"items": friends.list_blocks(conn, user)})


@router.post("/blocks")
async def social_add_block(data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.block_user(conn, user, _field(data, "identity"), events))


@router.delete("/blocks/{identity}")
async def social_remove_block(identity: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: friends.unblock_user(conn, user, identity))


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

@router.get("/groups")
async def social_list_groups(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: {"items": groups.list_groups(conn, user)})


@router.post("/groups")
async def social_create_group(data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.create_group(
        conn, user, _field(data, "name"), _field(data, "member_identities", []), events))


@router.get("/groups/{group_id}")
async def social_group_detail(group_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.group_detail(conn, user, group_id))


@router.patch("/groups/{group_id}")
async def social_update_group(group_id: int, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.update_group(
        conn, user, group_id, name=_field(data, "name"), announcement=_field(data, "announcement"),
        expected_updated_at=_field(data, "expected_updated_at"), events=events))


@router.post("/groups/{group_id}/members")
async def social_add_group_members(group_id: int, data: dict = Body(default={}),
                                   user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.add_members(
        conn, user, group_id, _field(data, "identities", []), events))


@router.delete("/groups/{group_id}/members/{identity}")
async def social_remove_group_member(group_id: int, identity: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.remove_member(conn, user, group_id, identity, events))


@router.post("/groups/{group_id}/leave")
async def social_leave_group(group_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.leave_group(conn, user, group_id, events))


@router.post("/groups/{group_id}/dissolve")
async def social_dissolve_group(group_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.dissolve_group(conn, user, group_id, events))


@router.post("/groups/{group_id}/transfer")
async def social_transfer_group(group_id: int, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.transfer_owner(
        conn, user, group_id, _field(data, "identity"), events))


@router.post("/groups/{group_id}/admins")
async def social_set_group_admin(group_id: int, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.set_admin(
        conn, user, group_id, _field(data, "identity"), bool(_field(data, "admin", False)), events))


@router.patch("/groups/{group_id}/me")
async def social_update_my_group_settings(group_id: int, data: dict = Body(default={}),
                                          user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.update_me(
        conn, user, group_id, nickname=_field(data, "nickname"), muted=_field(data, "muted"),
        alias=_field(data, "alias"), events=events))


@router.get("/groups/{group_id}/files")
async def social_group_files(group_id: int, kind: Optional[str] = Query(default=None),
                             before_id: Optional[int] = Query(default=None), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: groups.list_group_files(
        conn, user, group_id, kind=kind, before_id=before_id))


# ---------------------------------------------------------------------------
# Conversations and messages
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def social_list_conversations(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: {"items": conversations.list_conversations(conn, user)})


@router.get("/mention-quota")
async def social_mention_quota(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: notifications.mention_quota(conn, user))


@router.get("/conversations/{key}/preferences")
async def social_conversation_preferences(key: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: notifications.preferences(conn, user, key))


@router.patch("/conversations/{key}/preferences")
async def social_set_conversation_preferences(key: str, data: dict = Body(default={}),
                                               user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: notifications.set_preferences(
        conn, user, key, _field(data, "muted"), events))


@router.get("/conversations/{key}/messages")
async def social_list_messages(key: str, before_id: Optional[int] = Query(default=None),
                               limit: int = Query(default=50), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: conversations.list_messages(
        conn, user, key, before_id=before_id, limit=limit))


@router.post("/conversations/{key}/messages")
async def social_send_message(key: str, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    payload = data if isinstance(data, dict) else {}
    try:
        return await _run(lambda conn, events: messages.send_message(conn, user, key, payload, events))
    except HTTPException as exc:
        # Two concurrent sends with one client_nonce: the loser (unique violation, or a
        # media token the winner already consumed) answers with the stored message.
        nonce = messages.clean_nonce(payload)
        if not nonce or exc.status_code not in (409, 410):
            raise
        existing = await _run(lambda conn, events: messages.message_for_nonce(conn, user, key, nonce))
        if existing is None:
            raise
        return existing


@router.post("/conversations/{key}/read")
async def social_mark_read(key: str, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: messages.mark_read(
        conn, user, key, _field(data, "last_message_id"), events))


@router.post("/conversations/{key}/nudge")
async def social_nudge(key: str, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: messages.nudge(conn, user, key, _field(data, "identity"), events))


@router.post("/messages/{message_id}/recall")
async def social_recall_message(message_id: int, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: messages.recall_message(conn, user, message_id, events))


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

def _media_spec(data: Any) -> dict[str, Any]:
    return {name: _field(data, name) for name in ("sha256", "size", "mime", "filename")}


@router.post("/media/precheck")
async def social_media_precheck(data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: media.precheck(conn, user, **_media_spec(data)))


@router.post("/media/upload/init")
async def social_media_upload_init(data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: media.upload_init(conn, user, **_media_spec(data)))


async def _read_chunk_body(request: Request) -> bytes:
    limit = media.CHUNK_SIZE
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="分块不能超过 1 MB。")
    buffer = bytearray()
    async for piece in request.stream():
        buffer.extend(piece)
        if len(buffer) > limit:
            raise HTTPException(status_code=413, detail="分块不能超过 1 MB。")
    return bytes(buffer)


def _declared_chunk_size(request: Request) -> int:
    declared = request.headers.get("content-length")
    return min(int(declared), media.CHUNK_SIZE) if declared and declared.isdigit() else media.CHUNK_SIZE


@router.put("/media/upload/{upload_id}/chunk")
async def social_media_upload_chunk(upload_id: str, request: Request, index: int = Query(...),
                                    user: dict = Depends(get_current_user)):
    """Order matters (abuse resistance): session/owner check, one in-flight chunk per person,
    token-bucket rate (429 + Retry-After, never a sleep), then a global slot with a short wait."""
    me = user_identity(user)
    await _run(lambda conn, events: media.check_upload_session(conn, user, upload_id))
    try:
        media.upload_limiter.begin(me)
    except SocialError as exc:
        raise _http_error(exc) from exc
    try:
        media.upload_limiter.take(me, _declared_chunk_size(request), media.upload_rate_bytes())
        async with media.transfer_slot("upload"):
            data = await _read_chunk_body(request)
            return await _run(lambda conn, events: media.write_chunk(conn, user, upload_id, index, data))
    except SocialError as exc:
        raise _http_error(exc) from exc
    finally:
        media.upload_limiter.end(me)


@router.post("/media/upload/{upload_id}/complete")
async def social_media_upload_complete(upload_id: str, user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: media.upload_complete(conn, user, upload_id))


def _range_error(size: int) -> HTTPException:
    return HTTPException(status_code=416, detail="请求的范围无效。", headers={"Content-Range": f"bytes */{size}"})


def _parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    """``(start, end_inclusive)`` for a single ``bytes=`` range; None means the whole file."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[len("bytes="):].strip().partition("-")
    try:
        if first == "":
            length = int(last)
            if length <= 0:
                raise ValueError("empty suffix range")
            return max(0, size - length), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError as exc:
        raise _range_error(size) from exc
    if start >= size or end < start:
        raise _range_error(size)
    return start, min(end, size - 1)


def _thumbnail_work(media_id: int, source: Path) -> Optional[str]:
    from ..services.social_media_store import build_thumbnail_bytes

    try:
        data = build_thumbnail_bytes(source)
    except Exception as exc:  # noqa: BLE001 - a broken image falls back to the original bytes
        print(f"[SOCIAL_MEDIA] thumbnail failed for media {media_id}: {exc!r}")
        return None
    with get_db_connection() as conn:
        path = media.store_thumbnail(conn, media_id, data)
        conn.commit()
    return str(path)


async def _resolve_media(user: dict, media_id: int, variant: Optional[str]) -> dict[str, Any]:
    info = await _run(lambda conn, events: media.resolve_download(conn, user, media_id, variant))
    if info["thumb_missing"]:
        from ..services.chat_image_derivatives import run_chat_image_processing

        thumb = await run_chat_image_processing(_thumbnail_work, media_id, info["path"])
        if thumb:
            info.update(path=Path(thumb), mime="image/jpeg")
    return info


def _presentation(info: dict[str, Any], download: bool) -> tuple[str, str]:
    """``(content_type, disposition)``: only raster images, video and audio render inline.

    Everything else (SVG, HTML, unknown) is an ``application/octet-stream`` attachment so a
    direct navigation can never run an uploaded document in our origin.
    """
    mime = str(info["mime"] or mimetypes.guess_type(str(info["filename"]))[0] or "").lower()
    kind = str(info["kind"] or "file")
    inline_ok = ((kind == "image" and mime in SAFE_INLINE_IMAGE_MIME_TYPES)
                 or (kind in {"video", "audio"} and mime.startswith(f"{kind}/")))
    if not inline_ok:
        return OCTET_STREAM, "attachment"
    return mime, "attachment" if download else "inline"


async def _stream_open_file(handle: Any, start: int, length: int, rate_bytes: float):
    """Stream from an already-open handle (its size was taken with fstat on the same handle)."""
    try:
        await asyncio.to_thread(handle.seek, int(start))
        remaining = max(0, int(length))
        while remaining > 0:
            chunk = await asyncio.to_thread(handle.read, min(DOWNLOAD_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
            if rate_bytes > 0:
                await asyncio.sleep(len(chunk) / rate_bytes)
    finally:
        handle.close()


def _open_for_download(path: Path) -> tuple[Any, int]:
    handle = open(path, "rb")
    try:
        return handle, os.fstat(handle.fileno()).st_size
    except OSError:
        handle.close()
        raise


@router.get("/media/{media_id}")
async def social_media_download(media_id: int, request: Request, variant: Optional[str] = Query(default=None),
                                download: bool = Query(default=False), user: dict = Depends(get_current_user)):
    info = await _resolve_media(user, media_id, variant)
    try:
        handle, size = await asyncio.to_thread(_open_for_download, info["path"])
    except OSError as exc:
        raise HTTPException(status_code=410, detail="文件已过期。") from exc
    try:
        byte_range = _parse_range(request.headers.get("range"), size)
    except HTTPException:
        handle.close()
        raise
    byte_range = _parse_range(request.headers.get("range"), size)
    start, end = byte_range if byte_range else (0, size - 1)
    mime, disposition = _presentation(info, download)
    headers = {
        **untrusted_file_headers(),
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(0, end - start + 1)),
        "Content-Disposition": f"{disposition}; filename*=utf-8''{quote(str(info['filename'] or 'file'))}",
        "Cache-Control": "private, max-age=604800",
    }
    if byte_range:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    async def body():
        try:
            async with media.transfer_slot("download"):
                async for chunk in _stream_open_file(handle, start, end - start + 1, float(media.download_rate_bytes())):
                    yield chunk
        finally:
            handle.close()

    return StreamingResponse(body(), status_code=206 if byte_range else 200, media_type=mime, headers=headers)


@router.post("/media/{media_id}/backfill")
async def social_media_backfill(media_id: int, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: media.backfill(conn, user, media_id, _field(data, "media_token"), events))


@router.post("/lan/{transfer_id}/status")
async def social_lan_status(transfer_id: int, data: dict = Body(default={}), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: media.lan_status(conn, user, transfer_id, _field(data, "status")))


@router.get("/quota")
async def social_quota(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: media.quota(conn, user))


# ---------------------------------------------------------------------------
# Presence and admin
# ---------------------------------------------------------------------------

def _presence_items(conn: Any, user: dict, raw: str) -> dict[str, Any]:
    """Online state only for self, friends and co-group members (never across a block); others get null."""
    from ..services.social_relations import presence_visible

    me = user_identity(user)
    wanted = []
    for item in str(raw or "").split(",")[:MAX_PRESENCE_QUERY]:
        try:
            wanted.append(normalize_identity(item))
        except SocialError:
            continue
    visible = presence_visible(conn, me, wanted)
    states = presence_map(conn, [item for item in wanted if item in visible])
    return {"items": {item: states.get(item) or {"online": None, "last_seen_at": None} for item in wanted}}


@router.get("/presence")
async def social_presence(identities: str = Query(default=""), user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: _presence_items(conn, user, identities))


def _admin_storage(conn: Any, user: dict) -> dict[str, Any]:
    from ..services.message_center_service import is_super_admin_teacher
    from ..services.social_media_retention_service import storage_summary

    if str(user.get("role") or "") != "teacher" or not is_super_admin_teacher(conn, user.get("id")):
        raise SocialError("仅超级管理员可以查看。", 403)
    return storage_summary(conn)


@router.get("/admin/storage")
async def social_admin_storage(user: dict = Depends(get_current_user)):
    return await _run(lambda conn, events: _admin_storage(conn, user))


def _require_super_admin(conn: Any, user: dict) -> None:
    from ..services.message_center_service import is_super_admin_teacher

    if str(user.get("role") or "") != "teacher" or not is_super_admin_teacher(conn, user.get("id")):
        raise SocialError("仅超级管理员可以操作。", 403)


@router.post("/admin/recommendations/run")
async def social_admin_run_recommendations(user: dict = Depends(get_current_user)):
    """QA / super admin: run the daily recommendation pipeline now (unchanged buckets are skipped)."""
    await _run(lambda conn, events: _require_super_admin(conn, user))
    summary = await recommendation_service.run_recommendations_now()
    return {key: summary[key] for key in ("buckets", "ai", "backend", "fallback", "skipped", "kept", "pairs",
                                          "ai_calls_today", "ai_cap")}


# ---------------------------------------------------------------------------
# Internal: scheduler -> app push (token shared with the 散修 internal endpoints)
# ---------------------------------------------------------------------------

INTERNAL_BATCH_MAX_ITEMS = 500


async def _deliver_batch(batch: Any) -> int:
    if not isinstance(batch, list) or len(batch) > INTERNAL_BATCH_MAX_ITEMS:
        raise HTTPException(status_code=400, detail="batch must be a list of at most 500 items")
    delivered, budget = 0, INTERNAL_PUSH_MAX_TARGETS * 10
    for item in batch:
        targets = item.get("identities") if isinstance(item, dict) else None
        event = item.get("event") if isinstance(item, dict) else None
        if not isinstance(targets, list) or not isinstance(event, dict) or not event.get("type"):
            continue
        wanted = [str(target) for target in targets[:min(INTERNAL_PUSH_MAX_TARGETS, budget)]]
        budget -= len(wanted)
        if wanted:
            delivered += await social_push_service.deliver_local(wanted, event)
        if budget <= 0:
            break
    return delivered


@internal_router.post("/api/internal/social/push")
async def social_internal_push(payload: dict = Body(...), x_wanderer_internal_token: str = Header(default="")):
    """Deliver an event to /ws/social sockets; requires ``X-Wanderer-Internal-Token``. No DB access."""
    from ..services.wanderer_settings_service import internal_token_matches

    if not internal_token_matches(x_wanderer_internal_token):
        raise HTTPException(status_code=401, detail="invalid internal token")
    batch = payload.get("batch") if isinstance(payload, dict) else None
    if batch is not None:  # additive: [{identities, event}, ...] from social_push_service.push_events
        return JSONResponse({"status": "ok", "delivered": await _deliver_batch(batch)})
    targets = payload.get("identities") if isinstance(payload, dict) else None
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(targets, list) or not isinstance(event, dict) or not event.get("type"):
        raise HTTPException(status_code=400, detail="identities and event are required")
    delivered = await social_push_service.deliver_local([str(item) for item in targets[:INTERNAL_PUSH_MAX_TARGETS]],
                                                        event)
    return JSONResponse({"status": "ok", "delivered": delivered})
