"""Standalone ASGI application for the cookie-free game content hostname."""

import asyncio
from contextlib import suppress
import logging
import random
import re
import unicodedata
from urllib.parse import unquote_to_bytes, urlsplit

from fastapi import FastAPI, Request, HTTPException
from starlette.responses import FileResponse, Response

from .services.minigame_launch import mark_entry_loaded, resolve_asset
from .services import minigame_platform
from .services.minigame_platform import asset_usage, content_origins, platform_origins, run_db

app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
BASE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Access-Control-Allow-Origin": "null",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Origin-Agent-Cluster": "?1",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "X-Robots-Tag": "noindex, nofollow",
}


def canonical_asset(raw, handle, path):
    prefix = ("/l/" + handle + "/").encode("ascii", "backslashreplace")
    if not raw.startswith(prefix):
        raise HTTPException(404)
    parts = raw[len(prefix) :].split(b"/")
    decoded = []
    for segment in parts:
        if re.search(rb"%(?![a-fA-F0-9]{2})", segment):
            raise HTTPException(404)
        try:
            name = unquote_to_bytes(segment).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise HTTPException(404) from exc
        if (
            not name
            or name.startswith(".")
            or any(char in name for char in ("/", "\\", "%"))
            or any(ord(c) < 32 for c in name)
        ):
            raise HTTPException(404)
        decoded.append(name)
    if "/".join(decoded) != path:
        raise HTTPException(404)
    return unicodedata.normalize("NFC", path)


@app.api_route("/l/{handle}/{path:path}", methods=["GET", "HEAD"])
async def serve(request: Request, handle: str, path: str):
    try:
        main, assets = content_origins()
        try:
            parents = " ".join(platform_origins(main))
        except ValueError as exc:
            raise HTTPException(503) from exc
        if (
            request.headers.get("host", "").lower() != urlsplit(assets).netloc
            or request.url.query
        ):
            raise HTTPException(404)
        path = canonical_asset(request.scope.get("raw_path", b""), handle, path)
        filename, mime, game_id, entry = await run_db(resolve_asset, handle, path)
    except HTTPException as exc:
        return Response(
            status_code=503 if exc.status_code == 503 else 404, headers=BASE_HEADERS
        )
    policy = (
        f"sandbox allow-scripts; default-src 'none'; script-src {assets}; style-src {assets} 'unsafe-inline'; "
        f"img-src {assets} data: blob:; media-src {assets} blob:; font-src {assets} data:; connect-src {assets}; "
        f"frame-ancestors {parents}; base-uri 'none'; form-action 'none'; object-src 'none'; worker-src 'none'"
    )
    return GameFileResponse(
        game_id,
        filename,
        entry=entry
        if request.method == "GET" and "range" not in request.headers
        else None,
        media_type=mime,
        headers={**BASE_HEADERS, "Content-Security-Policy": policy},
    )


class GameFileResponse(FileResponse):
    def __init__(self, game_id, *args, entry=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.game_id = game_id
        self.entry = entry

    async def __call__(self, scope, receive, send):
        try:
            with asset_usage(self.game_id):
                if not self.path.is_file():
                    return await Response(status_code=404, headers=BASE_HEADERS)(
                        scope, receive, send
                    )
                # Keep the descriptor lease until ASGI has sent the last bytes.
                scope = {
                    **scope,
                    "extensions": {
                        k: v
                        for k, v in scope.get("extensions", {}).items()
                        if k != "http.response.pathsend"
                    },
                }
                if not self.entry:
                    return await super().__call__(scope, receive, send)
                # Uvicorn may silently ignore sends after transport loss. Observe peer
                # disconnects until the final body send, before normal ASGI completion
                # itself makes receive() return http.disconnect.
                disconnected = False
                delivered = None

                async def watch_connection():
                    nonlocal disconnected
                    while True:
                        if (await receive())["type"] == "http.disconnect":
                            disconnected = True
                            return

                async def send_entry(message):
                    nonlocal delivered
                    await send(message)
                    if message["type"] == "http.response.body" and not message.get("more_body") and not disconnected:
                        delivered = minigame_platform.now_ms()

                watcher = asyncio.create_task(watch_connection())
                try:
                    await super().__call__(scope, receive, send_entry)
                finally:
                    watcher.cancel()
                    with suppress(asyncio.CancelledError):
                        await watcher
        except HTTPException as exc:
            return await Response(status_code=exc.status_code, headers=BASE_HEADERS)(
                scope, receive, send
            )
        if delivered is not None:
            # Recheck the live lease before accepting this completed transfer.
            for attempt in range(3):
                try:
                    await run_db(mark_entry_loaded, self.entry, delivered)
                    break
                except HTTPException as exc:
                    if exc.status_code in (401, 403, 404, 410):
                        break
                    if exc.status_code == 503 and attempt < 2:
                        await asyncio.sleep(1.5 + random.random() + attempt)
                        continue
                    logging.getLogger(__name__).exception("Could not record the completed game entry")
                    break
                except Exception:
                    logging.getLogger(__name__).exception("Could not record the completed game entry")
                    break
