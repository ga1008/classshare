"""Authenticated super-admin review of an immutable student game version."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..services import minigame_moderation as moderation
from ..services import minigame_packages as packages
from ..services import minigame_platform as platform
from .minigame_admin import private_response, query
from .minigame_runtime import input_body

router = APIRouter(prefix="/api/games/v1/moderation/reviews", dependencies=[Depends(private_response)])


async def reviewer(request: Request):
    user = await platform.run_db(platform.require_player, request)
    if user["role"] != "teacher":
        raise HTTPException(403, "仅超管可以复核游戏。")
    return user


@router.get("")
async def reviews(status: str = "manual", offset: int = Query(0, ge=0), user=Depends(reviewer)):
    return await platform.run_db(query, moderation.list_reviews, user, status, offset)


@router.get("/{vid}")
async def review(vid: str, user=Depends(reviewer)):
    return await platform.run_db(query, moderation.detail, user, vid)


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
    return game, path


@router.get("/{vid}/package")
async def package(vid: str, user=Depends(reviewer)):
    from ..minigame_content import GameFileResponse

    game, path = await platform.run_db(query, review_file, user, vid)
    return GameFileResponse(game, path, media_type="application/zip", filename=f"review-{vid}.zip",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
