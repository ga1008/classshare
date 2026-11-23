"""Game directory, authenticated host pages and launch controls."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from ..services import minigame_author_kit as author_kit
from ..services import minigame_platform as p
from ..services import minigame_catalog as directory
from ..services.minigame_launch import change_grant, launch, version_for
from .minigame_admin import teacher
from .minigame_runtime import input_body

router = APIRouter()
pages = APIRouter()
API = "/api/games/v1"
Player = Annotated[dict, Depends(p.require_player)]
Manager = Annotated[dict, Depends(teacher)]
NO_STORE = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


def sidebar_data(conn, user, audience=None):
    from ..services.minigame_runtime import runtime_sidebar

    query = directory.CatalogQuery(
        user, audience if audience is not None else p._audience(conn, user)
    )
    result = runtime_sidebar(conn, user, query, limit=5)
    result["recommended"] = directory.recommended(
        conn, query, result["recent"], result["popular"], limit=5
    )
    return result


def context(user, **extra):
    safe = {k: user[k] for k in ("role", "id", "name", "display_name")}
    return {"user": safe, "user_info": safe, "csrf_token": p.csrf_token(user), **extra}


@pages.get("/games")
def hall(
    request: Request,
    user: Player,
    q: str = "",
    mode: str = "",
    tag: str = "",
    cursor: str = "",
):
    from ..core import templates
    from ..services.user_ui_preferences_service import resolve_user_ui_preferences

    with p.game_db() as conn:
        audience = p._audience(conn, user)
        page = directory.page(conn, user, q, mode, tag, cursor, 24, audience)
        resolve_user_ui_preferences(request, user, conn=conn)
        content = context(
            user,
            games=page["items"],
            next_cursor=page["nextCursor"],
            has_prev=bool(cursor and not page.get("cursorReset")),
            sidebar=sidebar_data(conn, user, audience),
            sidebar_hydrated=True,
        )
    return templates.TemplateResponse(
        request, "minigames/hall.html", content, headers=NO_STORE
    )


@pages.get("/games/{slug}")
def host(request: Request, slug: str, user: Player, previewVersion: str = ""):
    from ..core import templates
    from ..services.user_ui_preferences_service import resolve_user_ui_preferences

    with p.game_db() as conn:
        row = conn.execute(
            "SELECT id FROM mg_games WHERE slug=? OR id=? ORDER BY id LIMIT 1",
            (slug, slug),
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到小游戏")
        game = version_for(
            conn,
            user,
            row["id"],
            previewVersion or None,
            "preview" if previewVersion else "play",
        )
        card = p.public_game(game)
        card["version_id"] = game["version_id"]
        resolve_user_ui_preferences(request, user, conn=conn)
    data = context(
        user,
        game=card,
        preview_version_id=previewVersion,
        embedded_mode=True,
        suppress_page_backdrop=True,
        suppress_back_to_top=True,
    )
    return templates.TemplateResponse(
        request, "minigames/host.html", data, headers=NO_STORE
    )


def management_context(request, user, conn, page_title, **fields):
    from ..services.user_ui_preferences_service import resolve_user_ui_preferences
    from .ui_parts.common import _build_manage_template_context

    resolve_user_ui_preferences(request, user, conn=conn)
    extra = context(user, **fields)
    return _build_manage_template_context(
        request,
        extra["user"],
        page_title=page_title,
        active_page="games",
        teacher_is_super_admin=p.is_super_admin_teacher(conn, user["id"]),
        extra=extra,
    )


@pages.get("/manage/library/games/author-guide")
def author_guide(request: Request, user: Manager):
    from ..core import templates

    html, contents = author_kit.guide_page()
    with p.game_db() as conn:
        ctx = management_context(request, user, conn, "小游戏开发规范", guide_html=html, guide_toc=contents)
    return templates.TemplateResponse(request, "minigames/author_guide.html", ctx, headers=NO_STORE)


@pages.get("/manage/library/games/author-guide/markdown")
def download_author_spec(user: Manager):
    return Response(author_kit.guide_bytes(), media_type="text/markdown; charset=utf-8",
                    headers={**NO_STORE, "Content-Disposition": 'attachment; filename="AI-GAME-SPEC.md"',
                             "X-Content-Type-Options": "nosniff"})


@pages.get("/manage/library/games/author-guide/download")
def download_author_kit(user: Manager):
    return Response(author_kit.kit_bytes(), media_type="application/zip",
                    headers={**NO_STORE, "Content-Disposition": 'attachment; filename="LanShare-game-author-kit-v1.zip"',
                             "X-Content-Type-Options": "nosniff"})


@pages.get("/manage/library/games")
@pages.get("/manage/library/games/{game_id}")
def manage(request: Request, user: Manager, game_id: str = ""):
    from ..core import templates

    with p.game_db() as conn:
        row = p.require_manager(conn, user, game_id) if game_id else None
        ctx = management_context(request, user, conn, "小游戏管理", game=p.public_game(row) if row else None,
                                 games=[], scope_options=p.scope_options(conn, user))
    return templates.TemplateResponse(
        request,
        "minigames/detail.html" if row else "minigames/manage.html",
        ctx,
        headers=NO_STORE,
    )


@router.get(API + "/catalog")
def list_games(
    user: Player,
    q: str = "",
    mode: str = "",
    tag: str = "",
    cursor: str = "",
    limit: int = Query(24, ge=1, le=48),
):
    with p.game_db() as conn:
        return directory.page(conn, user, q, mode, tag, cursor, limit)


@router.get(API + "/sidebar")
def sidebar(user: Player):
    with p.game_db() as conn:
        return sidebar_data(conn, user)


@router.get(API + "/games/{game_id}")
def game_detail(game_id: str, user: Player):
    with p.game_db() as conn:
        return p.public_game(p.require_play(conn, user, game_id))


@router.get(API + "/games/{game_id}/icon")
def game_icon(game_id: str, user: Player):
    from ..minigame_content import GameFileResponse

    with p.game_db() as conn:
        game = p.require_play(conn, user, game_id, definition=False)
        icon = (
            p.game_root() / "versions" / game_id / game["version_id"] / "thumbnail.png"
        )
        if not icon.is_file():
            raise HTTPException(404)
        return GameFileResponse(game_id, icon, media_type="image/png", headers=NO_STORE)


@router.post(API + "/games/{game_id}/launches")
async def new_launch(request: Request, game_id: str, user: Player):
    p.check_write(request, user)
    body = await input_body(request)
    return await p.run_db(
        launch, user, game_id, body, request.headers.get("Idempotency-Key")
    )


@router.post(API + "/launches/{lease_id}/renew")
@router.post(API + "/launches/{lease_id}/revoke")
async def update_launch(request: Request, lease_id: str, user: Player):
    p.check_write(request, user)
    body = await input_body(request)
    return await p.run_db(
        change_grant,
        user,
        lease_id,
        body.get("hostInstance"),
        request.url.path.endswith("/revoke"),
    )
