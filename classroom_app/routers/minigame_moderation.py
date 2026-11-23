"""Authenticated super-admin review of an immutable student game version."""

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..services import minigame_moderation as moderation
from ..services import minigame_packages as packages
from ..services import minigame_platform as platform
from .minigame_admin import private_response, query
from .minigame_runtime import input_body

router = APIRouter(prefix="/api/games/v1/moderation/reviews", dependencies=[Depends(private_response)])

SAFE_INLINE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "video/mp4",
    "video/webm",
}


async def reviewer(request: Request):
    user = await platform.run_db(platform.require_player, request)
    if user["role"] != "teacher":
        raise HTTPException(403, "仅超管可以复核游戏。")
    return user


@router.get("/settings")
async def review_settings(user=Depends(reviewer)):
    return await platform.run_db(query, moderation.read_settings, user)


@router.patch("/settings")
async def update_review_settings(request: Request, user=Depends(reviewer)):
    platform.check_write(request, user)
    body = await input_body(request)
    return await platform.run_db(query, moderation.set_settings, user, body)


@router.get("")
async def reviews(
    status: str = "manual",
    offset: int = Query(0, ge=0),
    q: str = "",
    user=Depends(reviewer),
):
    return await platform.run_db(query, moderation.list_reviews, user, status, offset, q)


@router.get("/{vid}")
async def review(vid: str, user=Depends(reviewer)):
    return await platform.run_db(query, moderation.detail, user, vid)


@router.get("/{vid}/tree")
async def review_tree(vid: str, user=Depends(reviewer)):
    return await platform.run_db(query, moderation.package_tree, user, vid)


@router.get("/{vid}/file-content")
async def review_file_content(vid: str, path: str = Query(..., min_length=1, max_length=240), user=Depends(reviewer)):
    return await platform.run_db(query, moderation.read_review_file_content, user, vid, path)


@router.get("/{vid}/file")
async def review_member_file(
    vid: str,
    path: str = Query(..., min_length=1, max_length=240),
    download: bool = False,
    user=Depends(reviewer),
):
    from ..minigame_content import GameFileResponse

    game, target, meta = await platform.run_db(query, moderation.resolve_review_member, user, vid, path)
    mime = str(meta["mime"] or "application/octet-stream").lower()
    force_download = download or mime not in SAFE_INLINE_MIMES
    filename = PurePosixPath(meta["path"]).name or "file"
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Referrer-Policy": "no-referrer",
    }
    return GameFileResponse(
        game,
        target,
        media_type="application/octet-stream" if force_download else mime,
        filename=filename if force_download else None,
        content_disposition_type="attachment" if force_download else "inline",
        headers=headers,
    )


@router.post("/{vid}/ai-review")
async def submit_ai_review(vid: str, request: Request, user=Depends(reviewer)):
    platform.check_write(request, user)
    body = await input_body(request)
    return await platform.run_db(query, moderation.submit_ai_review, user, vid, body)


@router.post("/{vid}/preview", status_code=201)
async def preview_review(vid: str, request: Request, user=Depends(reviewer)):
    platform.check_write(request, user)
    return await platform.run_db(query, moderation.preview_launch_url, user, vid)


@router.post("/{vid}/decision")
async def decision(vid: str, request: Request, user=Depends(reviewer)):
    platform.check_write(request, user)
    body = await input_body(request)
    return await platform.run_db(query, moderation.decide, user, vid, body)


def review_file(conn, user, vid):
    moderation.require_admin(conn, user)
    row = moderation.review_row(conn, vid)
    game = row["game_id"]
    folder = packages.asset_path("version", game, vid)
    path = packages.checked_dir(folder, folder / "upload.zip")
    if row["game_status"] in ("deleted", "deleting") or not path.is_file():
        raise HTTPException(404, "该版本的游戏包已移除。")
    packages.marker(folder, game, vid)
    slug = (row["slug"] or f"review-{vid}").strip()
    ver = (row["release_version"] or vid[:8]).strip()
    return game, path, f"{slug}-{ver}.zip"


def review_icon_file(conn, user, vid):
    moderation.require_admin(conn, user)
    row = moderation.review_row(conn, vid)
    game = row["game_id"]
    folder = packages.asset_path("version", game, vid)
    path = packages.checked_dir(folder, folder / "thumbnail.png")
    if row["game_status"] in ("deleted", "deleting") or not path.is_file():
        raise HTTPException(404, "该版本的图标不可用。")
    packages.marker(folder, game, vid)
    return game, path


@router.get("/{vid}/icon")
async def review_icon(vid: str, user=Depends(reviewer)):
    from ..minigame_content import GameFileResponse

    game, path = await platform.run_db(query, review_icon_file, user, vid)
    return GameFileResponse(
        game,
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/{vid}/package")
async def package(vid: str, user=Depends(reviewer)):
    from ..minigame_content import GameFileResponse

    game, path, filename = await platform.run_db(query, review_file, user, vid)
    return GameFileResponse(
        game,
        path,
        media_type="application/zip",
        filename=filename,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
